"""Versioned, atomic per-dataset state storage for Structure Annotator."""

from __future__ import annotations

import ctypes
import hashlib
import json
import ntpath
import os
import re
import tempfile
from typing import Dict, Mapping, Optional, Sequence, Tuple

from pymolplugins.core.windows_paths import windows_long_path


STATE_SCHEMA_V1 = "pymolplugins.structure-annotator-state/1.0"
STATE_SCHEMA = "pymolplugins.structure-annotator-state/2.0"
STATE_DIRECTORY = ".pymolplugins"
STATE_FILENAME = "structure_annotator.json"
MAX_STATE_BYTES = 1024 * 1024

_SOURCE_MODES = {"directory", "csv"}
_WINDOWS_RESERVED_STEMS = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


class StateStoreError(ValueError):
    """Raised when a persisted state document is invalid or unsupported."""


def _looks_like_windows_path(path: str) -> bool:
    return bool(ntpath.splitdrive(path)[0]) or path.startswith(("\\\\", "\\\\?\\"))


def _absolute_path(path: str) -> str:
    """Normalize a local path without corrupting Host paths in WSL-side tests."""

    text = os.fspath(path) if path is not None else ""
    if not text or "\x00" in text:
        raise StateStoreError("dataset path must be a non-empty path")
    if _looks_like_windows_path(text):
        return ntpath.normpath(text)
    return os.path.abspath(os.path.expanduser(text))


def _path_parts(path: str) -> Tuple[str, str, str]:
    """Return ``(absolute, parent, basename)`` using the path's own syntax."""

    absolute = _absolute_path(path)
    path_module = ntpath if _looks_like_windows_path(absolute) else os.path
    return absolute, path_module.dirname(absolute), path_module.basename(absolute)


def _canonical_source_mode(value: object) -> str:
    mode = str(value or "directory").strip().lower()
    if mode not in _SOURCE_MODES:
        raise StateStoreError(
            "unsupported source mode {!r}; expected directory or csv".format(value)
        )
    return mode


def csv_dataset_id(csv_path: str) -> str:
    """Return the portable sidecar identity for one CSV dataset.

    Only the case-folded basename participates in the digest.  Consequently,
    the same CSV reached through ``K:``, UNC, or WSL aliases (or moved together
    with its ``.pymolplugins`` directory) retains its state and annotation
    sidecars.  The readable stem is Windows-safe and the basename digest still
    distinguishes files such as ``scores.csv`` and ``scores.tsv``.
    """

    _, _, basename = _path_parts(csv_path)
    stem = os.path.splitext(basename)[0]
    safe_stem = re.sub(r"[^\w.-]+", "_", stem.casefold(), flags=re.UNICODE)
    safe_stem = re.sub(r"_+", "_", safe_stem).strip(" ._")[:80]
    if not safe_stem:
        safe_stem = "dataset"
    if safe_stem.casefold() in _WINDOWS_RESERVED_STEMS:
        safe_stem = "_" + safe_stem
    digest = hashlib.sha256(
        basename.casefold().encode("utf-8", "surrogatepass")
    ).hexdigest()[:8]
    return f"{safe_stem}.{digest}"


def _csv_state_filename(csv_path: str) -> str:
    return f"structure_annotator.{csv_dataset_id(csv_path)}.json"


def _resolve_source(
    dataset_dir: str,
    *,
    source_mode: Optional[str] = None,
    csv_path: Optional[str] = None,
    source: Optional[Mapping[str, object]] = None,
) -> Tuple[str, str, str, Dict[str, object]]:
    """Return mode, source path, state root, and canonical descriptor."""

    raw_source = dict(source or {})
    inferred_mode = source_mode or raw_source.get("mode")
    if inferred_mode is None and csv_path:
        inferred_mode = "csv"
    mode = _canonical_source_mode(inferred_mode)

    if mode == "csv":
        candidate = csv_path or raw_source.get("csv_path") or raw_source.get("path")
        if not candidate:
            candidate = dataset_dir
        absolute, parent, _ = _path_parts(os.fspath(candidate))
        if not parent:
            raise StateStoreError(f"CSV path has no parent directory: {absolute}")
        descriptor = raw_source
        descriptor["mode"] = "csv"
        descriptor["csv_path"] = absolute
        descriptor.pop("path", None)
        return mode, absolute, parent, descriptor

    absolute = _absolute_path(dataset_dir)
    descriptor = raw_source
    descriptor["mode"] = "directory"
    descriptor["dataset_dir"] = absolute
    return mode, absolute, absolute, descriptor


def dataset_state_directory(
    dataset_dir: str,
    *,
    source_mode: Optional[str] = None,
    csv_path: Optional[str] = None,
) -> str:
    """Return the hidden state directory for a directory or CSV dataset."""

    _, _, root, _ = _resolve_source(
        dataset_dir, source_mode=source_mode, csv_path=csv_path
    )
    path_module = ntpath if _looks_like_windows_path(root) else os.path
    return path_module.join(root, STATE_DIRECTORY)


def dataset_state_path(
    dataset_dir: str,
    *,
    source_mode: Optional[str] = None,
    csv_path: Optional[str] = None,
) -> str:
    """Return the state path for a directory dataset or one CSV source.

    Existing callers keep using ``dataset_state_path(dataset_dir)``.  CSV
    callers may use either ``dataset_state_path(csv_path, source_mode="csv")``
    or pass the path through the ``csv_path`` keyword.  The hash is derived
    from the case-folded CSV basename, so aliasing or moving the dataset and
    its hidden directory does not change the state identity.
    """

    mode, source_path, root, _ = _resolve_source(
        dataset_dir, source_mode=source_mode, csv_path=csv_path
    )
    path_module = ntpath if _looks_like_windows_path(root) else os.path
    filename = STATE_FILENAME if mode == "directory" else _csv_state_filename(source_path)
    return path_module.join(root, STATE_DIRECTORY, filename)


def mark_hidden_on_windows(path: str) -> None:
    """Best-effort Windows hidden attribute in addition to the dot prefix."""

    if os.name != "nt":
        return
    try:
        get_attributes = ctypes.windll.kernel32.GetFileAttributesW
        set_attributes = ctypes.windll.kernel32.SetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int
        attributes = int(get_attributes(windows_long_path(path)))
        if attributes != 0xFFFFFFFF:
            set_attributes(windows_long_path(path), attributes | 0x2)
    except Exception:
        pass


def _validate_csv_config(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise StateStoreError("state csv config must be a JSON object")
    for field in (
        "csv_path",
        "path_column",
        "id_column",
        "encoding",
        "delimiter",
        "relative_path_base",
        "source_base",
        "stage_root",
        "stage_alias",
    ):
        if field in raw:
            value = raw[field]
            if not isinstance(value, str) or len(value) > 4096:
                raise StateStoreError(
                    f"csv.{field} must be a bounded string"
                )
    clean = dict(raw)
    display_columns = raw.get("display_columns")
    if display_columns is not None and (
        not isinstance(display_columns, Sequence)
        or isinstance(display_columns, (str, bytes))
        or len(display_columns) > 1000
        or any(not isinstance(value, str) for value in display_columns)
        or any(len(value) > 4096 for value in display_columns)
    ):
        raise StateStoreError("csv.display_columns must be an array of strings")
    if display_columns is not None:
        clean["display_columns"] = list(display_columns)
    return clean


def _validate_position(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise StateStoreError("state position must be a JSON object")
    if "index" in raw and (type(raw["index"]) is not int or raw["index"] < 0):
        raise StateStoreError("position.index must be a non-negative integer")
    for field in ("file", "entry_id"):
        if field in raw and not isinstance(raw[field], str):
            raise StateStoreError(f"position.{field} must be a string")
    return dict(raw)


def _validate_v2_document(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise StateStoreError("state document must be a JSON object")
    if raw.get("schema") != STATE_SCHEMA:
        raise StateStoreError(
            "unsupported state schema: {!r}".format(raw.get("schema"))
        )
    if not isinstance(raw.get("config"), dict):
        raise StateStoreError("state config must be a JSON object")
    position = _validate_position(raw.get("position"))
    source = raw.get("source")
    if not isinstance(source, dict):
        raise StateStoreError("state source must be a JSON object")
    mode = _canonical_source_mode(source.get("mode"))
    required_path = "csv_path" if mode == "csv" else "dataset_dir"
    if not isinstance(source.get(required_path), str) or not source[required_path]:
        raise StateStoreError(f"state source.{required_path} must be a non-empty string")
    csv_config = _validate_csv_config(raw.get("csv", {}))
    view = raw.get("view", {})
    if not isinstance(view, dict):
        raise StateStoreError("state view must be a JSON object")
    clean_view = dict(view)
    if "display_columns" in view:
        display_columns = view["display_columns"]
        if (
            not isinstance(display_columns, Sequence)
            or isinstance(display_columns, (str, bytes))
            or any(not isinstance(value, str) for value in display_columns)
        ):
            raise StateStoreError("view.display_columns must be an array of strings")
        clean_view["display_columns"] = list(display_columns)
    if "filters" in view and not isinstance(view["filters"], (dict, list, tuple)):
        raise StateStoreError("view.filters must be a JSON object or array")
    if isinstance(view.get("filters"), tuple):
        clean_view["filters"] = list(view["filters"])
    if "sort" in view and not isinstance(view["sort"], (dict, list, tuple)):
        raise StateStoreError("view.sort must be a JSON object or array")
    if isinstance(view.get("sort"), tuple):
        clean_view["sort"] = list(view["sort"])
    mapping_profile = raw.get("mapping_profile", "")
    if not isinstance(mapping_profile, (str, dict)):
        raise StateStoreError("state mapping_profile must be a string or JSON object")

    clean = dict(raw)
    clean["position"] = position
    clean["source"] = dict(source)
    clean["csv"] = csv_config
    clean["view"] = clean_view
    clean["mapping_profile"] = mapping_profile
    return clean


def _migrate_v1(payload: Mapping[str, object], dataset_dir: str) -> Dict[str, object]:
    """Migrate v1 in memory; the next explicit write persists schema v2."""

    if not isinstance(payload.get("config"), dict):
        raise StateStoreError("state config must be a JSON object")
    if not isinstance(payload.get("position"), dict):
        raise StateStoreError("state position must be a JSON object")
    document = dict(payload)
    document["schema"] = STATE_SCHEMA
    document["source"] = {
        "mode": "directory",
        "dataset_dir": _absolute_path(dataset_dir),
    }
    document.setdefault("csv", {})
    document.setdefault("view", {})
    document.setdefault("mapping_profile", "")
    return _validate_v2_document(document)


def read_dataset_state(
    dataset_dir: str,
    *,
    source_mode: Optional[str] = None,
    csv_path: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    """Read and validate a dataset state document.

    ``None`` means no state file exists.  A schema-v1 directory document is
    returned as schema v2 in memory without modifying the file; the next save
    performs the durable migration.  Invalid JSON, an unsupported schema, or
    an oversized document raises :class:`StateStoreError`.
    """

    mode, source_path, _, _ = _resolve_source(
        dataset_dir, source_mode=source_mode, csv_path=csv_path
    )
    path = dataset_state_path(
        dataset_dir, source_mode=mode, csv_path=csv_path
    )
    resolved = windows_long_path(path)
    if not os.path.isfile(resolved):
        return None
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        raise StateStoreError(f"cannot inspect state file: {exc}") from exc
    if size > MAX_STATE_BYTES:
        raise StateStoreError(f"state file exceeds {MAX_STATE_BYTES} bytes: {path}")
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateStoreError(f"cannot read state file: {exc}") from exc
    if not isinstance(payload, dict):
        raise StateStoreError("state document must be a JSON object")
    schema = payload.get("schema")
    if schema == STATE_SCHEMA_V1:
        if mode != "directory":
            raise StateStoreError("schema-v1 state is only valid for directory sources")
        return _migrate_v1(payload, source_path)
    if schema != STATE_SCHEMA:
        raise StateStoreError(f"unsupported state schema: {schema!r}")
    document = _validate_v2_document(payload)
    persisted_source = document["source"]
    assert isinstance(persisted_source, dict)
    if persisted_source.get("mode") != mode:
        raise StateStoreError(
            "state source mode does not match requested source mode: "
            f"{persisted_source.get('mode')!r} != {mode!r}"
        )
    return document


def write_dataset_state(
    dataset_dir: str,
    payload: Dict[str, object],
    *,
    source_mode: Optional[str] = None,
    csv_path: Optional[str] = None,
) -> str:
    """Atomically write one schema-v2 directory or CSV state document.

    ``source_mode`` defaults to ``payload['source']['mode']`` when available,
    and otherwise retains the historical directory behavior.
    """

    if not isinstance(payload, dict):
        raise StateStoreError("state document must be a JSON object")
    raw_source = payload.get("source")
    if raw_source is not None and not isinstance(raw_source, Mapping):
        raise StateStoreError("state source must be a JSON object")
    mode, source_path, root, descriptor = _resolve_source(
        dataset_dir,
        source_mode=source_mode,
        csv_path=csv_path,
        source=raw_source if isinstance(raw_source, Mapping) else None,
    )
    if not os.path.isdir(windows_long_path(root)):
        kind = "CSV parent directory" if mode == "csv" else "dataset directory"
        raise StateStoreError(f"{kind} does not exist: {root}")

    state_dir = dataset_state_directory(
        dataset_dir,
        source_mode=mode,
        csv_path=source_path if mode == "csv" else None,
    )
    resolved_dir = windows_long_path(state_dir)
    os.makedirs(resolved_dir, exist_ok=True)
    mark_hidden_on_windows(state_dir)

    document = dict(payload)
    document["schema"] = STATE_SCHEMA
    document["source"] = descriptor
    document.setdefault("csv", {})
    document.setdefault("view", {})
    document.setdefault("mapping_profile", "")
    document = _validate_v2_document(document)
    try:
        serialized = json.dumps(
            document, indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise StateStoreError(f"state document is not JSON serializable: {exc}") from exc
    encoded_size = len(serialized.encode("utf-8"))
    if encoded_size > MAX_STATE_BYTES:
        raise StateStoreError(
            f"state document exceeds {MAX_STATE_BYTES} bytes: {encoded_size}"
        )
    destination = dataset_state_path(
        dataset_dir,
        source_mode=mode,
        csv_path=source_path if mode == "csv" else None,
    )
    resolved_destination = windows_long_path(destination)
    descriptor_fd, temporary = tempfile.mkstemp(
        prefix=".structure_annotator.", suffix=".tmp", dir=resolved_dir
    )
    try:
        with os.fdopen(
            descriptor_fd, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved_destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return destination


__all__ = [
    "MAX_STATE_BYTES",
    "STATE_DIRECTORY",
    "STATE_FILENAME",
    "STATE_SCHEMA",
    "STATE_SCHEMA_V1",
    "StateStoreError",
    "csv_dataset_id",
    "dataset_state_directory",
    "dataset_state_path",
    "mark_hidden_on_windows",
    "read_dataset_state",
    "write_dataset_state",
]
