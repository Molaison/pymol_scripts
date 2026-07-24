"""Global, versioned history for Structure Annotator configuration inputs."""

from __future__ import annotations

import datetime
import difflib
import json
import math
import ntpath
import os
import re
import tempfile
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from pymolplugins.annotation.state_store import mark_hidden_on_windows
from pymolplugins.core.windows_paths import windows_long_path


HISTORY_SCHEMA_V1 = "pymolplugins.structure-annotator-history/1.0"
HISTORY_SCHEMA = "pymolplugins.structure-annotator-history/2.0"
HISTORY_ENVIRONMENT_VARIABLE = "PYMOLPLUGINS_ANNOTATOR_HISTORY"
HISTORY_DIRECTORY = ".pymolplugins"
HISTORY_FILENAME = "structure_annotator_history.json"
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOTS = 100
MAX_FIELD_ENTRIES = 200
MAX_VALUE_LENGTH = 256 * 1024

HISTORY_FIELDS = (
    "dataset_dir",
    "csv_path",
    "source_mode",
    "path_column",
    "id_column",
    "display_columns",
    "mapping_profile",
    "pattern",
    "label_name",
    "values_text",
    "step_cmd",
    "bucket_cmd",
)

_CONFIG_FIELDS = (
    "pattern",
    "label_name",
    "values_text",
    "step_cmd",
    "auto_advance",
    "keep_previous",
    "prefer_gz",
    "skip_annotated",
    "preload_enabled",
    "preload_ahead",
    "preload_interval_ms",
    "background_decompress_gz",
    "cache_enabled",
    "restore_last_dataset",
    "bucket_cmds",
    "source_mode",
    "csv_path",
    "path_column",
    "id_column",
    "display_columns",
    "mapping_profile",
)

_TEXT_CONFIG_FIELDS = {
    "pattern",
    "label_name",
    "values_text",
    "step_cmd",
    "csv_path",
    "path_column",
    "id_column",
}
_SOURCE_MODES = {"directory", "csv"}


class HistoryStoreError(ValueError):
    """Raised when the global history document is invalid or unsupported."""


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def global_history_path() -> str:
    """Return the history path, honoring the test/deployment override."""

    override = os.environ.get(HISTORY_ENVIRONMENT_VARIABLE, "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(
        os.path.abspath(os.path.expanduser("~")),
        HISTORY_DIRECTORY,
        HISTORY_FILENAME,
    )


def _empty_history() -> Dict[str, object]:
    return {
        "schema": HISTORY_SCHEMA,
        "updated_at_utc": "",
        "snapshots": [],
        "fields": {name: [] for name in HISTORY_FIELDS},
    }


def _validate_text(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise HistoryStoreError(f"{context} must be a string")
    if len(value) > MAX_VALUE_LENGTH:
        raise HistoryStoreError(f"{context} is too large")
    return value


def _validate_config(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise HistoryStoreError("snapshot config must be a JSON object")
    expanded = dict(raw)
    source = expanded.get("source")
    if source is not None:
        if not isinstance(source, Mapping):
            raise HistoryStoreError("config.source must be a JSON object")
        if "source_mode" not in expanded and "mode" in source:
            expanded["source_mode"] = source["mode"]
        if "csv_path" not in expanded and "csv_path" in source:
            expanded["csv_path"] = source["csv_path"]
    csv_config = expanded.get("csv")
    if csv_config is not None:
        if not isinstance(csv_config, Mapping):
            raise HistoryStoreError("config.csv must be a JSON object")
        for name in ("path_column", "id_column", "display_columns"):
            if name not in expanded and name in csv_config:
                expanded[name] = csv_config[name]
    view = expanded.get("view")
    if view is not None:
        if not isinstance(view, Mapping):
            raise HistoryStoreError("config.view must be a JSON object")
        if "display_columns" not in expanded and "display_columns" in view:
            expanded["display_columns"] = view["display_columns"]

    clean: Dict[str, object] = {}
    for name in _CONFIG_FIELDS:
        if name not in expanded:
            continue
        value = expanded[name]
        if name in _TEXT_CONFIG_FIELDS:
            clean[name] = _validate_text(value, f"config.{name}")
        elif name == "source_mode":
            mode = _validate_text(value, "config.source_mode").strip().lower()
            if mode not in _SOURCE_MODES:
                raise HistoryStoreError(
                    "config.source_mode must be directory or csv"
                )
            clean[name] = mode
        elif name == "display_columns":
            if isinstance(value, str):
                columns = [value] if value else []
            elif isinstance(value, Sequence):
                columns = list(value)
            else:
                raise HistoryStoreError(
                    "config.display_columns must be an array of strings"
                )
            if len(columns) > 512:
                raise HistoryStoreError(
                    "config.display_columns contains too many entries"
                )
            clean[name] = [
                _validate_text(column, "config.display_columns entry")
                for column in columns
            ]
        elif name == "mapping_profile":
            if isinstance(value, str):
                clean[name] = _validate_text(value, "config.mapping_profile")
            elif isinstance(value, Mapping):
                try:
                    clean[name] = json.loads(
                        json.dumps(dict(value), ensure_ascii=False)
                    )
                except (TypeError, ValueError) as exc:
                    raise HistoryStoreError(
                        f"config.mapping_profile is not JSON serializable: {exc}"
                    ) from exc
            else:
                raise HistoryStoreError(
                    "config.mapping_profile must be a string or JSON object"
                )
        elif name == "bucket_cmds":
            if not isinstance(value, dict):
                raise HistoryStoreError("config.bucket_cmds must be a JSON object")
            if len(value) > 100:
                raise HistoryStoreError("config.bucket_cmds contains too many entries")
            commands: Dict[str, str] = {}
            for key, command in value.items():
                clean_key = _validate_text(key, "bucket command key")
                clean_command = _validate_text(command, "bucket command")
                commands[clean_key] = clean_command
            clean[name] = commands
        elif name in {"preload_ahead", "preload_interval_ms"}:
            if type(value) is not int:
                raise HistoryStoreError(f"config.{name} must be an integer")
            minimum, maximum = (
                (0, 20) if name == "preload_ahead" else (50, 5000)
            )
            if not minimum <= value <= maximum:
                raise HistoryStoreError(
                    f"config.{name} must be in {minimum}..{maximum}"
                )
            clean[name] = value
        else:
            if type(value) is not bool:
                raise HistoryStoreError(f"config.{name} must be a boolean")
            clean[name] = value
    clean.setdefault("source_mode", "directory")
    return clean


def _validate_field_entry(raw: object, field: str) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise HistoryStoreError(f"fields.{field} entry must be a JSON object")
    value = _validate_text(raw.get("value"), f"fields.{field}.value")
    count = raw.get("count", 1)
    if type(count) is not int or count < 1:
        raise HistoryStoreError(f"fields.{field}.count must be a positive integer")
    last_used = _validate_text(
        raw.get("last_used_utc", ""), f"fields.{field}.last_used_utc"
    )
    return {"value": value, "count": count, "last_used_utc": last_used}


def _validate_snapshot(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise HistoryStoreError("history snapshot must be a JSON object")
    count = raw.get("count", 1)
    if type(count) is not int or count < 1:
        raise HistoryStoreError("snapshot.count must be a positive integer")
    config = _validate_config(raw.get("config"))
    source_mode = raw.get("source_mode", config.get("source_mode", "directory"))
    if not isinstance(source_mode, str) or source_mode not in _SOURCE_MODES:
        raise HistoryStoreError("snapshot.source_mode must be directory or csv")
    csv_path = raw.get("csv_path", config.get("csv_path", ""))
    clean_csv_path = _validate_text(csv_path, "snapshot.csv_path")
    config["source_mode"] = source_mode
    if clean_csv_path:
        config["csv_path"] = clean_csv_path
    return {
        "dataset_dir": _validate_text(raw.get("dataset_dir", ""), "dataset_dir"),
        "source_mode": source_mode,
        "csv_path": clean_csv_path,
        "recorded_at_utc": _validate_text(
            raw.get("recorded_at_utc", ""), "recorded_at_utc"
        ),
        "count": count,
        "config": config,
    }


def _validate_history(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise HistoryStoreError("history document must be a JSON object")
    schema = raw.get("schema")
    if schema not in {HISTORY_SCHEMA_V1, HISTORY_SCHEMA}:
        raise HistoryStoreError(
            "unsupported history schema: {!r}".format(schema)
        )
    snapshots = raw.get("snapshots")
    fields = raw.get("fields")
    if not isinstance(snapshots, list):
        raise HistoryStoreError("history snapshots must be a JSON array")
    if not isinstance(fields, dict):
        raise HistoryStoreError("history fields must be a JSON object")

    clean = _empty_history()
    clean["updated_at_utc"] = _validate_text(
        raw.get("updated_at_utc", ""), "updated_at_utc"
    )
    clean["snapshots"] = [
        _validate_snapshot(entry) for entry in snapshots[:MAX_SNAPSHOTS]
    ]
    clean_fields = clean["fields"]
    assert isinstance(clean_fields, dict)
    for field in HISTORY_FIELDS:
        entries = fields.get(field, [])
        if not isinstance(entries, list):
            raise HistoryStoreError(f"fields.{field} must be a JSON array")
        clean_fields[field] = [
            _validate_field_entry(entry, field)
            for entry in entries[:MAX_FIELD_ENTRIES]
        ]
    return clean


def read_global_history() -> Dict[str, object]:
    """Read the global history or return an empty document when absent."""

    path = global_history_path()
    resolved = windows_long_path(path)
    if not os.path.isfile(resolved):
        return _empty_history()
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        raise HistoryStoreError(f"cannot inspect history file: {exc}") from exc
    if size > MAX_HISTORY_BYTES:
        raise HistoryStoreError(
            f"history file exceeds {MAX_HISTORY_BYTES} bytes: {path}"
        )
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryStoreError(f"cannot read history file: {exc}") from exc
    return _validate_history(payload)


def _write_global_history(document: Mapping[str, object]) -> str:
    path = global_history_path()
    directory = os.path.dirname(path)
    resolved_directory = windows_long_path(directory)
    os.makedirs(resolved_directory, exist_ok=True)
    mark_hidden_on_windows(directory)

    # Work on a JSON round-trip copy so bounded eviction never mutates the
    # caller's document. Preserve the newest complete snapshot; old field
    # entries are expendable because subsequent finalized configs rebuild them.
    payload = json.loads(json.dumps(dict(document), ensure_ascii=False))
    payload["schema"] = HISTORY_SCHEMA
    while True:
        serialized = json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"
        encoded_size = len(serialized.encode("utf-8"))
        if encoded_size <= MAX_HISTORY_BYTES:
            break
        snapshots = payload.get("snapshots", [])
        fields = payload.get("fields", {})
        candidate_lists = []
        if isinstance(snapshots, list) and len(snapshots) > 1:
            candidate_lists.append(snapshots)
        if isinstance(fields, dict):
            candidate_lists.extend(
                entries
                for entries in fields.values()
                if isinstance(entries, list) and len(entries) > 1
            )
        if not candidate_lists and isinstance(fields, dict):
            candidate_lists.extend(
                entries
                for entries in fields.values()
                if isinstance(entries, list) and entries
            )
        if not candidate_lists:
            raise HistoryStoreError(
                f"latest history snapshot exceeds {MAX_HISTORY_BYTES} bytes"
            )
        max(candidate_lists, key=len).pop()

    resolved_path = windows_long_path(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".structure_annotator_history.", suffix=".tmp", dir=resolved_directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def _quarantine_invalid_history() -> Optional[str]:
    path = global_history_path()
    resolved = windows_long_path(path)
    if not os.path.exists(resolved):
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    for index in range(100):
        suffix = f".corrupt-{stamp}" if index == 0 else f".corrupt-{stamp}-{index}"
        candidate = path + suffix
        resolved_candidate = windows_long_path(candidate)
        if os.path.exists(resolved_candidate):
            continue
        os.replace(resolved, resolved_candidate)
        return candidate
    raise HistoryStoreError("cannot choose a quarantine path for invalid history")


def _normalized_dataset_dir(dataset_dir: str) -> str:
    if not str(dataset_dir or "").strip():
        return ""
    text = str(dataset_dir)
    if ntpath.splitdrive(text)[0] or text.startswith(("\\\\", "\\\\?\\")):
        return ntpath.normpath(text)
    return os.path.abspath(os.path.expanduser(text))


def _normalized_csv_path(csv_path: str) -> str:
    if not str(csv_path or "").strip():
        return ""
    text = str(csv_path)
    if ntpath.splitdrive(text)[0] or text.startswith(("\\\\", "\\\\?\\")):
        return ntpath.normpath(text)
    return os.path.abspath(os.path.expanduser(text))


def _path_identity(path: str) -> str:
    if ntpath.splitdrive(path)[0] or path.startswith(("\\\\", "\\\\?\\")):
        return ntpath.normcase(ntpath.normpath(path))
    return os.path.normcase(os.path.normpath(path))


def _field_values(dataset_dir: str, config: Mapping[str, object]) -> Dict[str, List[str]]:
    values = {field: [] for field in HISTORY_FIELDS}
    normalized_dir = _normalized_dataset_dir(dataset_dir)
    if normalized_dir:
        values["dataset_dir"].append(normalized_dir)
    for field in (
        "csv_path",
        "source_mode",
        "path_column",
        "id_column",
        "pattern",
        "label_name",
        "values_text",
        "step_cmd",
    ):
        value = config.get(field)
        if isinstance(value, str) and value.strip():
            values[field].append(value)
    display_columns = config.get("display_columns", [])
    if isinstance(display_columns, Sequence) and not isinstance(
        display_columns, (str, bytes)
    ):
        seen_columns = set()
        for column in display_columns:
            if (
                isinstance(column, str)
                and column.strip()
                and column not in seen_columns
            ):
                seen_columns.add(column)
                values["display_columns"].append(column)
    mapping_profile = config.get("mapping_profile", "")
    if isinstance(mapping_profile, str) and mapping_profile.strip():
        values["mapping_profile"].append(mapping_profile)
    elif isinstance(mapping_profile, Mapping):
        for key in ("name", "id", "profile"):
            candidate = mapping_profile.get(key)
            if isinstance(candidate, str) and candidate.strip():
                values["mapping_profile"].append(candidate)
                break
    commands = config.get("bucket_cmds", {})
    if isinstance(commands, Mapping):
        seen = set()
        for command in commands.values():
            if not isinstance(command, str) or not command.strip() or command in seen:
                continue
            seen.add(command)
            values["bucket_cmd"].append(command)
    return values


def _touch_field_entry(
    entries: Sequence[Mapping[str, object]], value: str, timestamp: str
) -> List[Dict[str, object]]:
    updated: List[Dict[str, object]] = []
    count = 1
    for entry in entries:
        if entry.get("value") == value:
            count = int(entry.get("count", 1)) + 1
            continue
        updated.append(dict(entry))
    updated.insert(
        0,
        {"value": value, "count": count, "last_used_utc": timestamp},
    )
    return updated[:MAX_FIELD_ENTRIES]


def _touch_snapshot(
    snapshots: Sequence[Mapping[str, object]],
    dataset_dir: str,
    config: Mapping[str, object],
    timestamp: str,
) -> List[Dict[str, object]]:
    updated: List[Dict[str, object]] = []
    count = 1
    source_mode = str(config.get("source_mode", "directory"))
    csv_path = str(config.get("csv_path", ""))
    for snapshot in snapshots:
        if (
            snapshot.get("dataset_dir") == dataset_dir
            and snapshot.get("source_mode", "directory") == source_mode
            and snapshot.get("csv_path", "") == csv_path
            and snapshot.get("config") == config
        ):
            count = int(snapshot.get("count", 1)) + 1
            continue
        updated.append(dict(snapshot))
    updated.insert(
        0,
        {
            "dataset_dir": dataset_dir,
            "source_mode": source_mode,
            "csv_path": csv_path,
            "recorded_at_utc": timestamp,
            "count": count,
            "config": dict(config),
        },
    )
    return updated[:MAX_SNAPSHOTS]


def record_config_history(
    dataset_dir: str,
    config: Mapping[str, object],
    *,
    source_mode: Optional[str] = None,
    csv_path: Optional[str] = None,
    path_column: Optional[str] = None,
    id_column: Optional[str] = None,
    display_columns: Optional[Sequence[str]] = None,
    mapping_profile: Optional[object] = None,
) -> str:
    """Record a complete config snapshot and de-duplicated field histories.

    A malformed prior file is preserved with a ``.corrupt-*`` suffix before a
    new history is created, so one broken JSON document cannot permanently
    disable future completion.
    """

    raw_config = dict(config)
    source = raw_config.get("source")
    csv_config = raw_config.get("csv")
    hinted_csv_path = raw_config.get("csv_path")
    if not hinted_csv_path and isinstance(source, Mapping):
        hinted_csv_path = source.get("csv_path")
    if not hinted_csv_path and isinstance(csv_config, Mapping):
        hinted_csv_path = csv_config.get("csv_path") or csv_config.get("path")
    if hinted_csv_path and "csv_path" not in raw_config:
        raw_config["csv_path"] = hinted_csv_path
    if source_mode is not None:
        raw_config["source_mode"] = source_mode
    elif "source_mode" not in raw_config:
        if isinstance(source, Mapping) and source.get("mode"):
            raw_config["source_mode"] = source.get("mode")
        else:
            raw_config["source_mode"] = (
                "csv" if csv_path or hinted_csv_path else "directory"
            )
    overrides = {
        "csv_path": csv_path,
        "path_column": path_column,
        "id_column": id_column,
        "display_columns": display_columns,
        "mapping_profile": mapping_profile,
    }
    for name, value in overrides.items():
        if value is not None:
            raw_config[name] = value

    clean_config = _validate_config(raw_config)
    mode = str(clean_config.get("source_mode", "directory"))
    if mode == "csv":
        normalized_csv = _normalized_csv_path(str(clean_config.get("csv_path", "")))
        if normalized_csv:
            clean_config["csv_path"] = normalized_csv
        normalized_dir = _normalized_dataset_dir(dataset_dir)
        if (
            normalized_csv
            and normalized_dir
            and _path_identity(normalized_dir) == _path_identity(normalized_csv)
        ):
            path_module = (
                ntpath
                if ntpath.splitdrive(normalized_csv)[0]
                or normalized_csv.startswith(("\\\\", "\\\\?\\"))
                else os.path
            )
            normalized_dir = path_module.dirname(normalized_csv)
        if not normalized_dir and normalized_csv:
            path_module = (
                ntpath
                if ntpath.splitdrive(normalized_csv)[0]
                or normalized_csv.startswith(("\\\\", "\\\\?\\"))
                else os.path
            )
            normalized_dir = path_module.dirname(normalized_csv)
    else:
        normalized_dir = _normalized_dataset_dir(dataset_dir)
    try:
        history = read_global_history()
    except HistoryStoreError:
        _quarantine_invalid_history()
        history = _empty_history()

    timestamp = utc_now()
    snapshots = history["snapshots"]
    fields = history["fields"]
    assert isinstance(snapshots, list)
    assert isinstance(fields, dict)
    history["snapshots"] = _touch_snapshot(
        snapshots, normalized_dir, clean_config, timestamp
    )
    for field, values in _field_values(normalized_dir, clean_config).items():
        entries = fields.get(field, [])
        assert isinstance(entries, list)
        for value in values:
            entries = _touch_field_entry(entries, value, timestamp)
        fields[field] = entries
    history["updated_at_utc"] = timestamp
    return _write_global_history(history)


def latest_config_snapshot() -> Optional[Dict[str, object]]:
    """Return a defensive copy of the most recently used full config."""

    history = read_global_history()
    snapshots = history.get("snapshots", [])
    if not isinstance(snapshots, list) or not snapshots:
        return None
    return dict(snapshots[0])


def _search_variants(value: str) -> List[str]:
    normalized = value.casefold().strip()
    variants = [normalized]
    path_tail = re.split(r"[\\/]", normalized)[-1]
    if path_tail and path_tail not in variants:
        variants.append(path_tail)
    for token in re.findall(r"[\w.+-]+", normalized):
        if token and token not in variants:
            variants.append(token)
    return variants


def _is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(any(char == candidate for candidate in iterator) for char in needle)


def _fuzzy_score(
    value: str, query: str, count: int, recency_index: int
) -> Optional[Tuple[float, float, float, float, float, float]]:
    needle = query.casefold().strip()
    if not needle:
        return (
            0.0,
            0.0,
            0.0,
            0.0,
            min(math.log1p(max(1, count)), 10.0),
            1.0 / (recency_index + 1.0),
        )

    variants = _search_variants(value)
    prefix = any(candidate.startswith(needle) for candidate in variants)
    substring = any(needle in candidate for candidate in variants)
    subsequence = any(_is_subsequence(needle, candidate) for candidate in variants)
    ratio = max(
        difflib.SequenceMatcher(None, needle, candidate).ratio()
        for candidate in variants
    )
    if not (prefix or substring or subsequence or ratio >= 0.38):
        return None
    return (
        1.0 if prefix else 0.0,
        1.0 if substring else 0.0,
        1.0 if subsequence else 0.0,
        ratio,
        min(math.log1p(max(1, count)), 10.0) / 10.0,
        1.0 / (recency_index + 1.0),
    )


def search_config_history(field: str, query: str = "", limit: int = 12) -> List[str]:
    """Fuzzy-search one configured input field, best match first."""

    canonical_field = str(field or "").strip().lower()
    if canonical_field not in HISTORY_FIELDS:
        raise HistoryStoreError(
            "unknown history field {!r}; expected one of {}".format(
                field, ", ".join(HISTORY_FIELDS)
            )
        )
    try:
        bounded_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError) as exc:
        raise HistoryStoreError("history search limit must be an integer") from exc

    history = read_global_history()
    fields = history.get("fields", {})
    assert isinstance(fields, dict)
    entries = fields.get(canonical_field, [])
    assert isinstance(entries, list)
    ranked = []
    for index, entry in enumerate(entries):
        assert isinstance(entry, dict)
        value = str(entry.get("value", ""))
        score = _fuzzy_score(value, str(query or ""), int(entry.get("count", 1)), index)
        if score is not None:
            ranked.append((score, value.casefold(), value))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [value for _, _, value in ranked[:bounded_limit]]


def history_summary() -> Dict[str, object]:
    """Return JSON-safe global-history counts for diagnostics."""

    path = global_history_path()
    history = read_global_history()
    snapshots = history["snapshots"]
    fields = history["fields"]
    assert isinstance(snapshots, list)
    assert isinstance(fields, dict)
    latest_dataset = snapshots[0].get("dataset_dir", "") if snapshots else ""
    latest_source_mode = (
        snapshots[0].get("source_mode", "directory") if snapshots else ""
    )
    latest_csv_path = snapshots[0].get("csv_path", "") if snapshots else ""
    return {
        "schema": HISTORY_SCHEMA,
        "path": path,
        "exists": os.path.isfile(windows_long_path(path)),
        "snapshot_count": len(snapshots),
        "field_counts": {
            field: len(fields.get(field, [])) for field in HISTORY_FIELDS
        },
        "latest_dataset": latest_dataset,
        "latest_source_mode": latest_source_mode,
        "latest_csv_path": latest_csv_path,
        "updated_at_utc": history.get("updated_at_utc", ""),
    }


__all__ = [
    "HISTORY_ENVIRONMENT_VARIABLE",
    "HISTORY_FIELDS",
    "HISTORY_FILENAME",
    "HISTORY_SCHEMA",
    "HISTORY_SCHEMA_V1",
    "HistoryStoreError",
    "global_history_path",
    "history_summary",
    "latest_config_snapshot",
    "read_global_history",
    "record_config_history",
    "search_config_history",
]
