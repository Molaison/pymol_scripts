"""Global, versioned history for Structure Annotator configuration inputs."""

from __future__ import annotations

import datetime
import difflib
import json
import math
import os
import re
import tempfile
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from pymolplugins.annotation.state_store import mark_hidden_on_windows
from pymolplugins.core.windows_paths import windows_long_path


HISTORY_SCHEMA = "pymolplugins.structure-annotator-history/1.0"
HISTORY_ENVIRONMENT_VARIABLE = "PYMOLPLUGINS_ANNOTATOR_HISTORY"
HISTORY_DIRECTORY = ".pymolplugins"
HISTORY_FILENAME = "structure_annotator_history.json"
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOTS = 100
MAX_FIELD_ENTRIES = 200
MAX_VALUE_LENGTH = 256 * 1024

HISTORY_FIELDS = (
    "dataset_dir",
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
    "restore_last_dataset",
    "bucket_cmds",
)


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
    clean: Dict[str, object] = {}
    for name in _CONFIG_FIELDS:
        if name not in raw:
            continue
        value = raw[name]
        if name in {"pattern", "label_name", "values_text", "step_cmd"}:
            clean[name] = _validate_text(value, f"config.{name}")
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
    return {
        "dataset_dir": _validate_text(raw.get("dataset_dir", ""), "dataset_dir"),
        "recorded_at_utc": _validate_text(
            raw.get("recorded_at_utc", ""), "recorded_at_utc"
        ),
        "count": count,
        "config": _validate_config(raw.get("config")),
    }


def _validate_history(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise HistoryStoreError("history document must be a JSON object")
    if raw.get("schema") != HISTORY_SCHEMA:
        raise HistoryStoreError(
            "unsupported history schema: {!r}".format(raw.get("schema"))
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
    return os.path.abspath(os.path.expanduser(str(dataset_dir)))


def _field_values(dataset_dir: str, config: Mapping[str, object]) -> Dict[str, List[str]]:
    values = {field: [] for field in HISTORY_FIELDS}
    normalized_dir = _normalized_dataset_dir(dataset_dir)
    if normalized_dir:
        values["dataset_dir"].append(normalized_dir)
    for field in ("pattern", "label_name", "values_text", "step_cmd"):
        value = config.get(field)
        if isinstance(value, str) and value.strip():
            values[field].append(value)
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
    for snapshot in snapshots:
        if (
            snapshot.get("dataset_dir") == dataset_dir
            and snapshot.get("config") == config
        ):
            count = int(snapshot.get("count", 1)) + 1
            continue
        updated.append(dict(snapshot))
    updated.insert(
        0,
        {
            "dataset_dir": dataset_dir,
            "recorded_at_utc": timestamp,
            "count": count,
            "config": dict(config),
        },
    )
    return updated[:MAX_SNAPSHOTS]


def record_config_history(dataset_dir: str, config: Mapping[str, object]) -> str:
    """Record a complete config snapshot and de-duplicated field histories.

    A malformed prior file is preserved with a ``.corrupt-*`` suffix before a
    new history is created, so one broken JSON document cannot permanently
    disable future completion.
    """

    clean_config = _validate_config(dict(config))
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
    return {
        "schema": HISTORY_SCHEMA,
        "path": path,
        "exists": os.path.isfile(windows_long_path(path)),
        "snapshot_count": len(snapshots),
        "field_counts": {
            field: len(fields.get(field, [])) for field in HISTORY_FIELDS
        },
        "latest_dataset": latest_dataset,
        "updated_at_utc": history.get("updated_at_utc", ""),
    }


__all__ = [
    "HISTORY_ENVIRONMENT_VARIABLE",
    "HISTORY_FIELDS",
    "HISTORY_FILENAME",
    "HISTORY_SCHEMA",
    "HistoryStoreError",
    "global_history_path",
    "history_summary",
    "latest_config_snapshot",
    "read_global_history",
    "record_config_history",
    "search_config_history",
]
