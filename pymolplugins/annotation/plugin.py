from pymol import cmd
from pymol.Qt import QtCore, QtWidgets
import copy
import datetime
import gzip
import hashlib
import json
import ntpath
import os
import posixpath
import re
import shutil
import tempfile
import threading
import uuid
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, fields, replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
import traceback
import sys

try:
    from pymolplugins.core.key_bindings import KeyBindingLease
    from pymolplugins.core.parameters import extend_command
    from pymolplugins.core.windows_paths import windows_long_path
    from pymolplugins.annotation.history_store import (
        HISTORY_FIELDS,
        HistoryStoreError,
        history_summary,
        latest_config_snapshot,
        record_config_history,
        search_config_history,
    )
    from pymolplugins.annotation.state_store import (
        StateStoreError,
        dataset_state_directory,
        dataset_state_path,
        read_dataset_state,
        write_dataset_state,
    )
    from pymolplugins.annotation.annotation_store import (
        RowAnnotationStore,
        annotation_sidecar_path,
    )
    from pymolplugins.annotation.cache import (
        PersistentStructureCache,
        SourceIdentity,
    )
    from pymolplugins.annotation.mapping_store import (
        DEFAULT_PROFILE_NAME,
        MappingStoreError,
        get_mapping_profile,
        get_mapping_profile_readonly,
        list_mapping_profiles,
        mapping_store_status,
        remove_mapping_profile,
        upsert_mapping_profile,
    )
    from pymolplugins.annotation.materializer import (
        CopyMaterializer,
        DirectMaterializer,
        MaterializedStructure,
    )
    from pymolplugins.annotation.model import (
        AVAILABILITY_AVAILABLE,
        AVAILABILITY_ERROR,
        AVAILABILITY_INVALID,
        AVAILABILITY_MISSING,
        AVAILABILITY_UNCHECKED,
        AVAILABILITY_UNREADABLE,
        AnnotationDataset,
        AnnotationEntry,
    )
    from pymolplugins.annotation.path_resolver import (
        PathResolution,
        PathResolver,
    )
    from pymolplugins.annotation.sources import (
        AUTO_DELIMITER,
        DEFAULT_ENCODING,
        CsvDatasetSource,
    )
    from pymolplugins.remote.staging import (
        download_staged_file,
        stat_staged_file,
        stat_staged_files,
    )
    from pymolplugins.remote.client import WorkerRequestError
    from pymolplugins.navigation.model import (
        NavigationEntry,
        NavigationSession,
    )
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.key_bindings import KeyBindingLease  # type: ignore
    from pymolplugins.core.parameters import extend_command  # type: ignore
    from pymolplugins.core.windows_paths import windows_long_path  # type: ignore
    from pymolplugins.annotation.history_store import (  # type: ignore
        HISTORY_FIELDS,
        HistoryStoreError,
        history_summary,
        latest_config_snapshot,
        record_config_history,
        search_config_history,
    )
    from pymolplugins.annotation.state_store import (  # type: ignore
        StateStoreError,
        dataset_state_directory,
        dataset_state_path,
        read_dataset_state,
        write_dataset_state,
    )
    from pymolplugins.annotation.annotation_store import (  # type: ignore
        RowAnnotationStore,
        annotation_sidecar_path,
    )
    from pymolplugins.annotation.cache import (  # type: ignore
        PersistentStructureCache,
        SourceIdentity,
    )
    from pymolplugins.annotation.mapping_store import (  # type: ignore
        DEFAULT_PROFILE_NAME,
        MappingStoreError,
        get_mapping_profile,
        get_mapping_profile_readonly,
        list_mapping_profiles,
        mapping_store_status,
        remove_mapping_profile,
        upsert_mapping_profile,
    )
    from pymolplugins.annotation.materializer import (  # type: ignore
        CopyMaterializer,
        DirectMaterializer,
        MaterializedStructure,
    )
    from pymolplugins.annotation.model import (  # type: ignore
        AVAILABILITY_AVAILABLE,
        AVAILABILITY_ERROR,
        AVAILABILITY_INVALID,
        AVAILABILITY_MISSING,
        AVAILABILITY_UNCHECKED,
        AVAILABILITY_UNREADABLE,
        AnnotationDataset,
        AnnotationEntry,
    )
    from pymolplugins.annotation.path_resolver import (  # type: ignore
        PathResolution,
        PathResolver,
    )
    from pymolplugins.annotation.sources import (  # type: ignore
        AUTO_DELIMITER,
        DEFAULT_ENCODING,
        CsvDatasetSource,
    )
    from pymolplugins.remote.staging import (  # type: ignore
        download_staged_file,
        stat_staged_file,
        stat_staged_files,
    )
    from pymolplugins.remote.client import WorkerRequestError  # type: ignore
    from pymolplugins.navigation.model import (  # type: ignore
        NavigationEntry,
        NavigationSession,
    )

STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
OBJECT_PREFIX = "sa"
REMARK_PREFIX = "REMARK 950 PYMOL_ANNOTATOR"


def _long_path(path: str) -> str:
    """Convert long drive and UNC paths to valid Windows extended paths."""
    return windows_long_path(path)



def _split_values(values_text: str) -> List[str]:
    tokens = re.split(r"[,;\n\r]+", values_text or "")
    values = [token.strip() for token in tokens if token.strip()]
    deduped = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _sanitize_token(text: str, fallback: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", (text or "").strip()).strip("_")
    return sanitized or fallback


def normalize_label_key(label_name: str) -> str:
    return _sanitize_token(label_name, "label").lower()


def derive_object_name(filename: str) -> str:
    """
    Build a PyMOL-friendly object name from a file path.
    PyMOL hides names that start with '_' in the object panel, so we normalize
    everything to an alphanumeric/underscore token that never begins with '_'.
    """
    base_name = os.path.basename(filename)
    lower_name = base_name.lower()
    for ext in (".pdb.gz", ".cif.gz", ".pdb", ".cif"):
        if lower_name.endswith(ext):
            base_name = base_name[: -len(ext)]
            lower_name = lower_name[: -len(ext)]
            break

    starts_with_underscore = base_name.startswith("_")
    sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", base_name).strip("_")

    if not sanitized:
        sanitized = OBJECT_PREFIX

    if sanitized[0].isdigit():
        sanitized = f"{OBJECT_PREFIX}_{sanitized}"

    if starts_with_underscore:
        sanitized = f"{OBJECT_PREFIX}_{sanitized}"

    return f"{OBJECT_PREFIX}_{sanitized}"


def _read_text_file(path: str) -> str:
    path = _long_path(path)
    if path.lower().endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    with open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write_text_file(path: str, text: str) -> None:
    path = _long_path(path)
    if path.lower().endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return
    with open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _strip_existing_pdb_remark(lines: List[str], label_key: str) -> List[str]:
    prefix = f"{REMARK_PREFIX} {label_key}="
    filtered = []
    for line in lines:
        if line.startswith(prefix):
            continue
        filtered.append(line)
    return filtered


def read_pdb_annotations(path: str) -> Dict[str, str]:
    """Read all PYMOL_ANNOTATOR remarks from a PDB file."""
    result = {}
    try:
        text = _read_text_file(path)
        pattern = re.compile(rf"^{re.escape(REMARK_PREFIX)}\s+(\S+)=(\S+)")
        for line in text.splitlines():
            m = pattern.match(line)
            if m:
                key, value = m.group(1), m.group(2)
                result[key] = value
    except Exception:
        pass
    return result


def read_cif_annotations(path: str) -> Dict[str, str]:
    """Read all PYMOL_ANNOTATOR comments from a CIF file."""
    result = {}
    try:
        text = _read_text_file(path)
        # Pattern: # PYMOL_ANNOTATOR key=value ts=...
        pattern = re.compile(rf"^#\s*{re.escape(REMARK_PREFIX.split()[-1])}\s+(\S+)=(\S+)")
        for line in text.splitlines():
            m = pattern.match(line)
            if m:
                key, value = m.group(1), m.group(2)
                result[key] = value
    except Exception:
        pass
    return result


def read_structure_annotations(path: str) -> Dict[str, str]:
    """Read existing annotations from a structure file."""
    path = _resolve_annotation_path(path)
    lower = path.lower()
    if lower.endswith(".pdb") or lower.endswith(".pdb.gz"):
        return read_pdb_annotations(path)
    if lower.endswith(".cif") or lower.endswith(".cif.gz"):
        return read_cif_annotations(path)
    return {}


def has_annotation_fast(path: str, label_key: str) -> bool:
    """
    Fast check if a structure file has a specific annotation.
    Uses simple string matching instead of full parsing.
    For PDB files, only reads the header portion since REMARK lines are at the top.
    """
    try:
        path = _long_path(path)
        lower = path.lower()
        pdb_marker = f"{REMARK_PREFIX} {label_key}="
        # CIF uses comment format: # PYMOL_ANNOTATOR key=value
        cif_marker = f"# PYMOL_ANNOTATOR {label_key}="

        if lower.endswith(".gz"):
            # For gzip, read in chunks to avoid loading entire file
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                # Read up to 512KB for header check
                content = fh.read(512 * 1024)
                if pdb_marker in content:
                    return True
                if lower.endswith(".cif.gz") and cif_marker in content:
                    return True
                # CIF annotations may be at end, need to read more
                if lower.endswith(".cif.gz"):
                    rest = fh.read()
                    if cif_marker in rest:
                        return True
        else:
            # For non-gzip PDB, REMARK is at top, read limited portion
            if lower.endswith(".pdb"):
                with open(path, "rt", encoding="utf-8", errors="replace") as fh:
                    # REMARKs are at file start, 256KB should be enough
                    content = fh.read(256 * 1024)
                    return pdb_marker in content
            else:
                # CIF: annotations may be at end, read full file
                with open(path, "rt", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                    return cif_marker in content

        return False
    except Exception:
        return False


def write_pdb_annotation(path: str, label_key: str, label_value: str) -> None:
    text = _read_text_file(path)
    has_trailing_newline = text.endswith("\n")
    lines = text.splitlines(keepends=True)

    lines = _strip_existing_pdb_remark(lines, label_key)

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    remark = f"{REMARK_PREFIX} {label_key}={label_value} ts={timestamp}\n"

    insert_at = len(lines)
    for idx in range(len(lines) - 1, -1, -1):
        stripped = lines[idx].strip()
        if stripped == "END" or stripped == "ENDMDL":
            insert_at = idx
            break
    lines.insert(insert_at, remark)

    out = "".join(lines)
    if has_trailing_newline and not out.endswith("\n"):
        out += "\n"
    _write_text_file(path, out)


def _strip_existing_cif_comment(lines: List[str], label_key: str) -> List[str]:
    """Remove existing PYMOL_ANNOTATOR comment for the given label_key."""
    prefix = f"# PYMOL_ANNOTATOR {label_key}="
    return [line for line in lines if not line.startswith(prefix)]


def write_cif_annotation(path: str, label_key: str, label_value: str) -> None:
    """Write annotation as a CIF comment to avoid breaking strict parsers like Rosetta."""
    text = _read_text_file(path)
    has_trailing_newline = text.endswith("\n")
    lines = text.splitlines(keepends=True)

    lines = _strip_existing_cif_comment(lines, label_key)

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    # Use comment format similar to PDB REMARK
    comment = f"# PYMOL_ANNOTATOR {label_key}={label_value} ts={timestamp}\n"
    lines.append(comment)

    out = "".join(lines)
    if has_trailing_newline and not out.endswith("\n"):
        out += "\n"
    _write_text_file(path, out)


def annotate_structure_file(path: str, label_key: str, label_value: str) -> None:
    lower = path.lower()
    if lower.endswith(".pdb") or lower.endswith(".pdb.gz"):
        write_pdb_annotation(path, label_key, label_value)
        return
    if lower.endswith(".cif") or lower.endswith(".cif.gz"):
        write_cif_annotation(path, label_key, label_value)
        return
    raise ValueError(f"Unsupported file type: {path}")


def remove_structure_annotation(path: str, label_key: str) -> None:
    """Delete a single PYMOL_ANNOTATOR entry from a structure file (used for undo)."""
    text = _read_text_file(path)
    has_trailing_newline = text.endswith("\n")
    lines = text.splitlines(keepends=True)
    lower = path.lower()
    if lower.endswith(".pdb") or lower.endswith(".pdb.gz"):
        lines = _strip_existing_pdb_remark(lines, label_key)
    elif lower.endswith(".cif") or lower.endswith(".cif.gz"):
        lines = _strip_existing_cif_comment(lines, label_key)
    else:
        raise ValueError(f"Unsupported file type: {path}")
    out = "".join(lines)
    if has_trailing_newline and not out.endswith("\n"):
        out += "\n"
    _write_text_file(path, out)


@dataclass
class AnnotatorState:
    source_mode: str = "directory"
    dataset_dir: str = "."
    csv_path: str = ""
    path_column: str = "structure_path"
    id_column: str = ""
    display_columns: List[str] = field(default_factory=list)
    csv_encoding: str = DEFAULT_ENCODING
    csv_delimiter: str = AUTO_DELIMITER
    source_base: str = ""
    mapping_profile: str = DEFAULT_PROFILE_NAME
    filter_text: str = ""
    sort_column: str = ""
    sort_descending: bool = False
    stage_root: str = ""
    stage_alias: str = ""
    pattern: str = STRUCTURE_PATTERN
    dataset: Optional[AnnotationDataset] = None
    dataset_generation: int = 0
    all_entries: List[AnnotationEntry] = field(default_factory=list)
    entries: List[AnnotationEntry] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    index: int = 0
    label_name: str = "quality"
    values_text: str = "Good,Bad"
    bucket_cmds: Dict[str, str] = field(default_factory=dict)
    step_cmd: str = "zoom"
    auto_advance: bool = True
    keep_previous: bool = False
    prefer_gz: bool = True
    skip_annotated: bool = False  # Skip files that already have the current label
    preload_enabled: bool = True
    preload_ahead: int = 2
    preload_interval_ms: int = 250
    background_decompress_gz: bool = True
    cache_enabled: bool = True
    restore_last_dataset: bool = True
    managed_objects: List[str] = field(default_factory=list)
    current_object: Optional[str] = None
    undo_history: List[dict] = field(default_factory=list)
    annotated_indices: set = field(default_factory=set)
    annotated_entry_ids: set = field(default_factory=set)
    annotation_generation: int = 0
    row_annotation_store: Optional[RowAnnotationStore] = None
    materialized_paths: Dict[str, MaterializedStructure] = field(default_factory=dict)
    materialization_errors: Dict[str, str] = field(default_factory=dict)
    navigation_session: Optional[NavigationSession] = None
    navigation_generation: int = -1

    def values(self) -> List[str]:
        return _split_values(self.values_text) or ["Good", "Bad"]

    def current_file(self) -> Optional[str]:
        if not self.files:
            return None
        if self.navigation_session is not None:
            self.index = self.navigation_session.cursor
        return self.files[self.index % len(self.files)]

    def current_entry(self) -> Optional[AnnotationEntry]:
        if not self.entries:
            return None
        if self.navigation_session is not None:
            self.index = self.navigation_session.cursor
        return self.entries[self.index % len(self.entries)]

    def current_entry_id(self) -> str:
        entry = self.current_entry()
        return entry.id if entry is not None else ""


STATE = globals().get("STATE", AnnotatorState())

# Back-fill every field added by newer versions when reusing STATE on reload.
_DEFAULT_STATE = AnnotatorState()
for _state_field in fields(AnnotatorState):
    if not hasattr(STATE, _state_field.name):
        setattr(
            STATE,
            _state_field.name,
            copy.deepcopy(getattr(_DEFAULT_STATE, _state_field.name)),
        )


_PERSISTED_STRING_FIELDS = (
    "pattern",
    "label_name",
    "values_text",
    "step_cmd",
)
_PERSISTED_BOOL_FIELDS = (
    "auto_advance",
    "keep_previous",
    "prefer_gz",
    "skip_annotated",
    "preload_enabled",
    "background_decompress_gz",
    "cache_enabled",
    "restore_last_dataset",
)
_PERSISTED_INT_BOUNDS = {
    "preload_ahead": (0, 20),
    "preload_interval_ms": (50, 5000),
}
_PERSISTED_SOURCE_STRING_FIELDS = (
    "source_mode",
    "csv_path",
    "path_column",
    "id_column",
    "mapping_profile",
)


def _path_identity(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _active_source_reference() -> str:
    if STATE.source_mode == "csv":
        return str(STATE.csv_path or "")
    return _active_dataset_directory()


def _source_parent(path: str) -> str:
    if ntpath.splitdrive(path)[0] or path.startswith("\\\\"):
        return ntpath.dirname(path)
    return os.path.dirname(path)


def _state_kwargs() -> Dict[str, str]:
    if STATE.source_mode == "csv":
        return {"source_mode": "csv", "csv_path": STATE.csv_path}
    return {"source_mode": "directory"}


def _entry_source_path(entry: AnnotationEntry) -> str:
    return str(entry.resolved_path or entry.source_path)


def _directory_entries(dataset_dir: str, files: Sequence[str]) -> List[AnnotationEntry]:
    result = []
    for index, path in enumerate(files):
        absolute = os.path.abspath(path)
        result.append(
            AnnotationEntry(
                id=f"file:{absolute}",
                ordinal=index,
                display_name=os.path.basename(path),
                source_path=absolute,
                source_kind="directory",
                source_base=dataset_dir,
                metadata={
                    "structure_path": absolute,
                    "name": os.path.basename(path),
                },
                raw_values=(absolute, os.path.basename(path)),
                resolved_path=absolute,
                path_resolution="directory",
                availability=AVAILABILITY_AVAILABLE,
            )
        )
    return result


def _entry_filter_match(entry: AnnotationEntry, query: str) -> bool:
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    values = [entry.display_name, entry.source_path, entry.resolved_path]
    values.extend(entry.metadata.values())
    return any(needle in str(value).casefold() for value in values)


def _view_entries(
    entries: Sequence[AnnotationEntry],
    *,
    filter_text: Optional[str] = None,
    sort_column: Optional[str] = None,
    sort_descending: Optional[bool] = None,
) -> List[AnnotationEntry]:
    active_filter = STATE.filter_text if filter_text is None else str(filter_text)
    active_sort = STATE.sort_column if sort_column is None else str(sort_column)
    active_descending = (
        bool(STATE.sort_descending)
        if sort_descending is None
        else bool(sort_descending)
    )
    visible = [
        entry
        for entry in entries
        if entry.is_navigable
        and not entry.error
        and bool(_entry_source_path(entry))
        and _entry_filter_match(entry, active_filter)
    ]
    if active_sort:
        column = active_sort

        def _key(entry: AnnotationEntry):
            value = entry.display_metadata.get(column)
            if value is None:
                value = entry.metadata.get(column, "")
            return (value is None or value == "", value if value is not None else "")

        visible.sort(key=_key, reverse=active_descending)
    return visible


def _resume_entry_index(
    entries: Sequence[AnnotationEntry], entry_id: str, index: int
) -> int:
    resume = max(0, min(int(index), len(entries) - 1))
    if entry_id:
        for candidate, entry in enumerate(entries):
            if entry.id == entry_id:
                return candidate
    return resume


def _install_entries(
    entries: Sequence[AnnotationEntry],
    *,
    resume_entry_id: str = "",
    index: int = 0,
    active_entries: Optional[Sequence[AnnotationEntry]] = None,
) -> None:
    active = (
        list(active_entries)
        if active_entries is not None
        else _view_entries(entries)
    )
    if not active:
        raise RuntimeError("Structure Annotator: no navigable structure rows")
    STATE.dataset_generation = int(STATE.dataset_generation) + 1
    STATE.entries = list(active)
    STATE.files = [_entry_source_path(entry) for entry in active]
    resume = _resume_entry_index(active, resume_entry_id, index)
    STATE.navigation_session = _new_navigation_session(STATE.entries, resume)
    STATE.navigation_generation = STATE.dataset_generation
    STATE.index = STATE.navigation_session.cursor


def _runtime_snapshot() -> Dict[str, object]:
    names = (
        "source_mode", "dataset_dir", "csv_path", "path_column", "id_column",
        "display_columns", "csv_encoding", "csv_delimiter", "source_base",
        "mapping_profile", "filter_text", "sort_column", "sort_descending",
        "stage_root", "stage_alias", "pattern", "dataset", "all_entries",
        "dataset_generation", "entries", "files", "index", "undo_history", "annotated_indices",
        "annotated_entry_ids", "row_annotation_store", "materialized_paths",
        "materialization_errors", "navigation_session", "managed_objects",
        "navigation_generation", "current_object",
    )
    snapshot: Dict[str, object] = {}
    for name in names:
        value = getattr(STATE, name)
        if isinstance(value, list):
            value = list(value)
        elif isinstance(value, dict):
            value = dict(value)
        elif isinstance(value, set):
            value = set(value)
        snapshot[name] = value
    snapshot["config"] = copy.deepcopy(_persisted_config())
    return snapshot


def _restore_runtime_snapshot(snapshot: Mapping[str, object]) -> None:
    _apply_persisted_config(dict(snapshot["config"]))
    for name, value in snapshot.items():
        if name != "config":
            setattr(STATE, name, value)


def _active_dataset_directory() -> str:
    """Return the directory proved by the currently loaded file list."""

    if STATE.source_mode == "csv" and STATE.csv_path:
        return _source_parent(STATE.csv_path)
    if not STATE.files:
        return ""
    directories = {
        _path_identity(os.path.dirname(os.path.abspath(path)))
        for path in STATE.files
    }
    if len(directories) != 1:
        return ""
    return os.path.dirname(os.path.abspath(STATE.files[0]))


def _persisted_config() -> Dict[str, object]:
    config: Dict[str, object] = {
        name: getattr(STATE, name) for name in _PERSISTED_STRING_FIELDS
    }
    config.update(
        {name: str(getattr(STATE, name) or "") for name in _PERSISTED_SOURCE_STRING_FIELDS}
    )
    config["display_columns"] = [str(value) for value in STATE.display_columns]
    config.update({name: bool(getattr(STATE, name)) for name in _PERSISTED_BOOL_FIELDS})
    config.update({name: int(getattr(STATE, name)) for name in _PERSISTED_INT_BOUNDS})
    config["bucket_cmds"] = {
        str(key): str(value)
        for key, value in STATE.bucket_cmds.items()
        if str(key)
    }
    return config


def _validate_persisted_config(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise StateStoreError("state config must be a JSON object")
    config: Dict[str, object] = {}
    for name in _PERSISTED_STRING_FIELDS:
        if name not in raw:
            continue
        value = raw[name]
        if not isinstance(value, str):
            raise StateStoreError(f"config.{name} must be a string")
        if len(value) > 256 * 1024:
            raise StateStoreError(f"config.{name} is too large")
        config[name] = value
    for name in _PERSISTED_SOURCE_STRING_FIELDS:
        if name not in raw:
            continue
        value = raw[name]
        if not isinstance(value, str) or len(value) > 4096:
            raise StateStoreError(f"config.{name} must be a bounded string")
        config[name] = value
    if "source_mode" in config and config["source_mode"] not in {"directory", "csv"}:
        raise StateStoreError("config.source_mode must be directory or csv")
    if "display_columns" in raw:
        columns = raw["display_columns"]
        if (
            not isinstance(columns, list)
            or len(columns) > 1000
            or any(not isinstance(value, str) or len(value) > 4096 for value in columns)
        ):
            raise StateStoreError("config.display_columns must be bounded strings")
        config["display_columns"] = list(columns)
    for name in _PERSISTED_BOOL_FIELDS:
        if name not in raw:
            continue
        value = raw[name]
        if type(value) is not bool:
            raise StateStoreError(f"config.{name} must be a boolean")
        config[name] = value
    for name, (minimum, maximum) in _PERSISTED_INT_BOUNDS.items():
        if name not in raw:
            continue
        value = raw[name]
        if type(value) is not int or not minimum <= value <= maximum:
            raise StateStoreError(
                f"config.{name} must be an integer in {minimum}..{maximum}"
            )
        config[name] = value
    if "bucket_cmds" in raw:
        commands = raw["bucket_cmds"]
        if not isinstance(commands, dict):
            raise StateStoreError("config.bucket_cmds must be a JSON object")
        if len(commands) > 100:
            raise StateStoreError("config.bucket_cmds contains too many entries")
        clean_commands = {}
        for key, value in commands.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise StateStoreError(
                    "config.bucket_cmds keys and values must be strings"
                )
            if len(key) > 1024 or len(value) > 256 * 1024:
                raise StateStoreError("config.bucket_cmds entry is too large")
            clean_commands[key] = value
        config["bucket_cmds"] = clean_commands
    return config


def _validate_persisted_position(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise StateStoreError("state position must be a JSON object")
    index = raw.get("index", 0)
    filename = raw.get("file", "")
    entry_id = raw.get("entry_id", "")
    if type(index) is not int or index < 0:
        raise StateStoreError("position.index must be a non-negative integer")
    if not isinstance(filename, str) or len(filename) > 4096:
        raise StateStoreError("position.file must be a string")
    if not isinstance(entry_id, str) or len(entry_id) > 4096:
        raise StateStoreError("position.entry_id must be a string")
    return {
        "index": index,
        "file": filename.replace("\\", "/"),
        "entry_id": entry_id,
    }


def _apply_persisted_config(config: Dict[str, object]) -> None:
    for name in (
        _PERSISTED_STRING_FIELDS
        + _PERSISTED_SOURCE_STRING_FIELDS
        + _PERSISTED_BOOL_FIELDS
    ):
        if name in config:
            setattr(STATE, name, config[name])
    for name in _PERSISTED_INT_BOUNDS:
        if name in config:
            setattr(STATE, name, config[name])
    if "bucket_cmds" in config:
        STATE.bucket_cmds = dict(config["bucket_cmds"])
    if "display_columns" in config:
        STATE.display_columns = list(config["display_columns"])


def _read_persisted_dataset_state(
    source_reference: str, source_mode: str = "directory"
) -> Optional[Dict[str, object]]:
    try:
        kwargs = (
            {"source_mode": "csv", "csv_path": source_reference}
            if source_mode == "csv"
            else {"source_mode": "directory"}
        )
        payload = read_dataset_state(source_reference, **kwargs)
        if payload is None:
            return None
        return {
            "config": _validate_persisted_config(payload.get("config")),
            "position": _validate_persisted_position(payload.get("position")),
            "source": dict(payload.get("source") or {}),
            "csv": dict(payload.get("csv") or {}),
            "view": dict(payload.get("view") or {}),
            "mapping_profile": payload.get("mapping_profile", ""),
        }
    except StateStoreError as exc:
        print(f"Structure Annotator: ignoring invalid state file: {exc}")
        kwargs = (
            {"source_mode": "csv", "csv_path": source_reference}
            if source_mode == "csv"
            else {"source_mode": "directory"}
        )
        print(f"  State path: {dataset_state_path(source_reference, **kwargs)}")
        return None


def _relative_current_file(dataset_dir: str) -> str:
    current = STATE.current_file()
    if not current:
        return ""
    try:
        relative = os.path.relpath(os.path.abspath(current), os.path.abspath(dataset_dir))
    except ValueError:
        return os.path.basename(current)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return os.path.basename(current)
    return relative.replace("\\", "/")


def _write_persisted_dataset_state(source_reference: str) -> Optional[str]:
    if not source_reference or not STATE.files:
        return None
    try:
        position = {
            "index": int(STATE.index),
            "file": (
                ""
                if STATE.source_mode == "csv"
                else _relative_current_file(source_reference)
            ),
        }
        if STATE.source_mode == "csv":
            position["entry_id"] = STATE.current_entry_id()
        payload = {
            "updated_at_utc": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "config": _persisted_config(),
            "position": position,
            "source": (
                {"mode": "csv", "csv_path": STATE.csv_path}
                if STATE.source_mode == "csv"
                else {"mode": "directory", "dataset_dir": STATE.dataset_dir}
            ),
            "csv": {
                "csv_path": STATE.csv_path,
                "path_column": STATE.path_column,
                "id_column": STATE.id_column,
                "display_columns": list(STATE.display_columns),
                "encoding": STATE.csv_encoding,
                "delimiter": STATE.csv_delimiter,
                "source_base": STATE.source_base,
                "stage_root": STATE.stage_root,
                "stage_alias": STATE.stage_alias,
            },
            "view": {
                "display_columns": list(STATE.display_columns),
                "filters": {"text": STATE.filter_text},
                "sort": {
                    "column": STATE.sort_column,
                    "descending": bool(STATE.sort_descending),
                },
            },
            "mapping_profile": STATE.mapping_profile,
        }
        return write_dataset_state(source_reference, payload, **_state_kwargs())
    except Exception as exc:
        print(f"Structure Annotator: state save failed: {exc}")
        print(f"  State path: {dataset_state_path(source_reference, **_state_kwargs())}")
        return None


def _save_current_dataset_state() -> Optional[str]:
    active = _active_source_reference()
    if not active:
        return None
    if STATE.source_mode == "directory" and (
        not STATE.dataset_dir
        or _path_identity(active) != _path_identity(STATE.dataset_dir)
    ):
        return None
    return _write_persisted_dataset_state(active)


def _record_current_config_history() -> Optional[str]:
    """Record one finalized config without making history a runtime dependency."""

    active = _active_source_reference()
    if not active:
        return None
    try:
        dataset_dir = (
            _source_parent(STATE.csv_path)
            if STATE.source_mode == "csv"
            else active
        )
        return record_config_history(
            dataset_dir,
            _persisted_config(),
            source_mode=STATE.source_mode,
            csv_path=STATE.csv_path if STATE.source_mode == "csv" else "",
            path_column=STATE.path_column,
            id_column=STATE.id_column,
            display_columns=STATE.display_columns,
            mapping_profile=STATE.mapping_profile,
        )
    except Exception as exc:
        print(f"Structure Annotator: global history save failed: {exc}")
        return None


def _restore_latest_config_from_history(*, defer_dataset: bool = False) -> bool:
    """Restore the last config and optionally defer its dataset until UI show.

    With ``defer_dataset=True`` a true return means that a restore intent was
    recovered, not that its dataset has already been committed.
    """

    if STATE.files:
        return False
    try:
        snapshot = latest_config_snapshot()
        if not snapshot:
            return False
        config = _validate_persisted_config(snapshot.get("config"))
        _apply_persisted_config(config)
        source_mode = str(
            snapshot.get("source_mode")
            or config.get("source_mode")
            or "directory"
        )
        csv_path = str(
            snapshot.get("csv_path") or config.get("csv_path") or ""
        ).strip()
        dataset_dir = str(snapshot.get("dataset_dir", "")).strip()
        STATE.source_mode = source_mode
        if csv_path:
            STATE.csv_path = csv_path
        if dataset_dir:
            STATE.dataset_dir = dataset_dir
        source_reference = csv_path if source_mode == "csv" else dataset_dir
        if not STATE.restore_last_dataset or not source_reference:
            return False
        if defer_dataset:
            return True
        source_available = (
            os.path.isfile(_long_path(source_reference))
            if source_mode == "csv"
            else os.path.isdir(_long_path(source_reference))
        )
        if not source_available:
            print(
                "Structure Annotator: last dataset is unavailable; "
                f"config restored without loading: {source_reference}"
            )
            return False
        if source_mode == "csv":
            sa_load_csv(
                csv_path=csv_path,
                path_column=str(
                    config.get("path_column") or STATE.path_column
                ),
                id_column=str(config.get("id_column") or ""),
                display_columns=config.get("display_columns"),
                mapping_profile=str(
                    config.get("mapping_profile")
                    or STATE.mapping_profile
                    or DEFAULT_PROFILE_NAME
                ),
            )
            restored = bool(
                STATE.source_mode == "csv"
                and STATE.csv_path
                and _path_identity(STATE.csv_path) == _path_identity(csv_path)
            )
            if restored:
                print(f"Structure Annotator: restored last CSV dataset: {csv_path}")
            return restored
        saved_state_exists = os.path.isfile(
            _long_path(dataset_state_path(dataset_dir))
        )
        pattern_override = "" if saved_state_exists else str(
            config.get("pattern", STRUCTURE_PATTERN)
        )
        sa_load_dir(dataset_dir, pattern_override)
        restored = bool(
            _active_dataset_directory()
            and _path_identity(_active_dataset_directory())
            == _path_identity(dataset_dir)
        )
        if restored:
            print(f"Structure Annotator: restored last dataset: {dataset_dir}")
        return restored
    except (HistoryStoreError, StateStoreError) as exc:
        print(f"Structure Annotator: ignoring invalid global history: {exc}")
        return False
    except Exception as exc:
        print(f"Structure Annotator: last-session restore failed: {exc}")
        traceback.print_exc()
        return False


def _history_suggestions(field: str, query: str, limit: int = 12) -> List[str]:
    try:
        return search_config_history(field, query, limit)
    except HistoryStoreError as exc:
        print(f"Structure Annotator: global history search unavailable: {exc}")
        return []


def _resume_index(
    dataset_dir: str,
    files: List[str],
    position: Optional[Dict[str, object]],
    entries: Optional[Sequence[AnnotationEntry]] = None,
) -> int:
    if not files or not position:
        return 0
    requested_entry = str(position.get("entry_id", ""))
    if requested_entry and entries:
        for index, entry in enumerate(entries):
            if entry.id == requested_entry:
                return index
    requested_file = str(position.get("file", "")).replace("\\", "/")
    if requested_file:
        for index, path in enumerate(files):
            relative = os.path.relpath(
                os.path.abspath(path), os.path.abspath(dataset_dir)
            ).replace("\\", "/")
            if relative == requested_file:
                return index
    requested_index = int(position.get("index", 0))
    return max(0, min(requested_index, len(files) - 1))


def _new_navigation_session(
    source_entries: Sequence[object], index: int = 0
) -> NavigationSession:
    entries = []
    for value in source_entries:
        if isinstance(value, AnnotationEntry):
            path = _entry_source_path(value)
            entries.append(
                NavigationEntry(
                    id=value.id,
                    kind="file",
                    display_name=value.display_name,
                    source_paths=(path,),
                    metadata={
                        "entry_id": value.id,
                        "row_number": value.row_number,
                    },
                )
            )
        else:
            path = str(value)
            entries.append(
                NavigationEntry(
                    id=f"file:{os.path.abspath(path)}",
                    kind="file",
                    display_name=os.path.basename(path),
                    source_paths=(os.path.abspath(path),),
                )
            )
    session = NavigationSession(
        entries=entries,
        batch_size=max(1, len(entries)),
        window_size=1,
        stride=1,
        wrap=True,
    )
    session.goto(max(0, min(int(index), len(entries) - 1)))
    return session


def _ensure_navigation_session() -> NavigationSession:
    session = STATE.navigation_session
    stale = (
        session is None
        or STATE.navigation_generation != STATE.dataset_generation
        or len(session.entries) != len(STATE.files)
    )
    if stale:
        if not STATE.files:
            raise RuntimeError("Structure Annotator: no dataset loaded")
        session = _new_navigation_session(STATE.entries or STATE.files, STATE.index)
        STATE.navigation_session = session
        STATE.navigation_generation = STATE.dataset_generation
    STATE.index = session.cursor
    return session


def _set_annotation_index(index: int) -> int:
    session = _ensure_navigation_session()
    session.goto(int(index))
    STATE.index = session.cursor
    return STATE.index


def _structure_dedupe_key(file_path: str) -> str:
    name = os.path.basename(file_path).lower()
    if name.endswith(".gz"):
        name = name[: -len(".gz")]
    for ext in (".pdb", ".cif"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return name


def _list_structure_files(
    path: str, pattern: str, prefer_gz: bool,
    skip_annotated: bool = False, label_key: str = None
) -> List[str]:
    compiled = re.compile(pattern)
    selected: Dict[str, str] = {}
    dataset = os.path.abspath(os.path.expanduser(path))
    for entry in os.listdir(_long_path(dataset)):
        if compiled.match(entry):
            full_path = os.path.join(dataset, entry)
            if not os.path.isfile(_long_path(full_path)):
                continue
            key = _structure_dedupe_key(full_path)
            is_gz = full_path.lower().endswith(".gz")
            existing = selected.get(key)
            if existing is None:
                selected[key] = full_path
                continue

            existing_is_gz = existing.lower().endswith(".gz")
            if prefer_gz:
                if is_gz and not existing_is_gz:
                    selected[key] = full_path
            else:
                if (not is_gz) and existing_is_gz:
                    selected[key] = full_path

    files = sorted(selected.values())

    # Filter out files that already have the specified label
    if skip_annotated and label_key:
        filtered = []
        skipped = 0
        for f in files:
            if has_annotation_fast(f, label_key):
                skipped += 1
            else:
                filtered.append(f)
        if skipped > 0:
            print(f"Structure Annotator: skipped {skipped} already-annotated files")
        files = filtered

    return files


def _resolve_annotation_path(path: str) -> str:
    if not path:
        return path
    lower = path.lower()
    if lower.endswith(".gz"):
        return path
    if STATE.prefer_gz and os.path.exists(path + ".gz"):
        return path + ".gz"
    return path


def _object_name_for_index(path: str, index: int) -> str:
    base = os.path.basename(path)
    base_lower = base.lower()
    for ext in (".pdb.gz", ".cif.gz", ".pdb", ".cif"):
        if base_lower.endswith(ext):
            base = base[: -len(ext)]
            base_lower = base_lower[: -len(ext)]
            break
    base_token = _sanitize_token(base, "structure")
    identity = (
        STATE.entries[index].id
        if 0 <= index < len(STATE.entries)
        else os.path.abspath(path)
    )
    digest = hashlib.sha1(
        identity.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:8]
    return f"{OBJECT_PREFIX}_{index + 1:05d}_{base_token}_{digest}"


def _object_exists(object_name: str) -> bool:
    try:
        return object_name in set(cmd.get_object_list())
    except Exception:
        return False


def _get_enabled_managed_object() -> Optional[str]:
    """Get the currently enabled managed object in PyMOL, if any."""
    try:
        enabled = cmd.get_names("objects", enabled_only=1)
        for obj in enabled:
            if obj in STATE.managed_objects:
                return obj
        return None
    except Exception:
        return None


def _verify_current_object_enabled() -> bool:
    """
    Verify that STATE.current_object is actually enabled in PyMOL.
    Returns True if matched, False if mismatch detected.
    """
    if not STATE.current_object:
        return False
    try:
        enabled = set(cmd.get_names("objects", enabled_only=1))
        enabled_managed = enabled.intersection(STATE.managed_objects)
        return enabled_managed == {STATE.current_object}
    except Exception:
        return False


def _get_index_for_object(obj_name: str) -> Optional[int]:
    """Extract the index from an object name like 'sa_00001_filename'."""
    m = re.match(rf"^{re.escape(OBJECT_PREFIX)}_(\d+)_", obj_name)
    if m:
        return int(m.group(1)) - 1
    return None


_bg_executor = globals().get("_bg_executor", None)
_bg_cache_dir = globals().get("_bg_cache_dir", None)
_bg_decompress_futures = globals().get("_bg_decompress_futures", {})
_bg_decompressed_paths = globals().get("_bg_decompressed_paths", {})
_materialization_futures = globals().get("_materialization_futures", {})
_materialization_generation = globals().get("_materialization_generation", 0)
_structure_cache = globals().get("_structure_cache", None)
_copy_materializer = globals().get("_copy_materializer", None)
_direct_materializer = globals().get("_direct_materializer", None)
_materializer_lock = globals().get("_materializer_lock", None)
if _materializer_lock is None:
    _materializer_lock = threading.RLock()


@dataclass(frozen=True)
class _MaterializationContext:
    """Immutable I/O settings captured before a background job is submitted."""

    generation: int
    cache_enabled: bool
    stage_root: str
    stage_alias: str


def _materialization_context() -> _MaterializationContext:
    return _MaterializationContext(
        generation=int(_materialization_generation),
        cache_enabled=bool(STATE.cache_enabled),
        stage_root=str(STATE.stage_root or ""),
        stage_alias=str(STATE.stage_alias or ""),
    )


def _get_bg_executor() -> ThreadPoolExecutor:
    global _bg_executor
    if _bg_executor is None:
        workers = max(1, min(4, (os.cpu_count() or 2)))
        _bg_executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="pymol_sa_bg"
        )
    return _bg_executor


def _get_structure_cache() -> PersistentStructureCache:
    global _structure_cache
    if _structure_cache is None:
        with _materializer_lock:
            if _structure_cache is None:
                _structure_cache = PersistentStructureCache()
    return _structure_cache


def _get_copy_materializer() -> CopyMaterializer:
    global _copy_materializer
    if _copy_materializer is None:
        with _materializer_lock:
            if _copy_materializer is None:
                _copy_materializer = CopyMaterializer(_get_structure_cache())
    return _copy_materializer


def _get_direct_materializer() -> DirectMaterializer:
    global _direct_materializer
    if _direct_materializer is None:
        with _materializer_lock:
            if _direct_materializer is None:
                _direct_materializer = DirectMaterializer()
    return _direct_materializer


def _entry_provider(entry: AnnotationEntry) -> str:
    resolution = entry.path_resolution
    if isinstance(resolution, Mapping):
        return str(resolution.get("provider") or "direct")
    return str(getattr(resolution, "provider", "") or "direct")


def _is_remote_host_path(path: str, provider: str) -> bool:
    value = str(path)
    configured_drives = {
        token.strip().rstrip(":").upper() + ":"
        for token in re.split(
            r"[,;\s]+",
            os.environ.get("PYMOLPLUGINS_REMOTE_DRIVES", "K"),
        )
        if token.strip()
    }
    drive = ntpath.splitdrive(value)[0].upper()
    return (
        provider not in {"direct", "directory", "windows-host", "local"}
        or value.startswith("\\\\")
        or provider.startswith("wsl-stage:")
        or drive in configured_drives
    )


def _stage_relative_path(
    entry: AnnotationEntry, context: _MaterializationContext
) -> Tuple[str, str]:
    raw_root = str(context.stage_root or "").strip()
    source = posixpath.normpath(entry.source_path)
    alias = str(context.stage_alias or "").strip()
    if not raw_root or not alias:
        raise RuntimeError("WSL staging requires stage_root and stage_alias")
    root = posixpath.normpath(raw_root)
    if source == root:
        raise RuntimeError("WSL staging source must be a file below stage_root")
    if not source.startswith(root.rstrip("/") + "/"):
        raise RuntimeError("WSL staging source is outside the configured stage_root")
    relative = posixpath.relpath(source, root)
    if relative in {"", "."} or relative.startswith("../"):
        raise RuntimeError("invalid WSL staging relative path")
    return alias, relative


def _materialize_wsl_entry(
    entry: AnnotationEntry,
    context: _MaterializationContext,
    observed: Optional[object] = None,
) -> MaterializedStructure:
    alias, relative = _stage_relative_path(entry, context)
    if observed is None:
        observed = stat_staged_file(alias, relative)
    observed_identity = SourceIdentity(
        provider=f"wsl-stage:{alias}",
        canonical_source=f"{alias}/{relative}",
        size=int(observed.size),
        mtime_ns=int(observed.mtime_ns),
        version_token=str(observed.token),
    )
    hit = _get_structure_cache().lookup(observed_identity)
    if hit is not None:
        return MaterializedStructure(
            source_path=entry.source_path,
            path=hit.path,
            identity=observed_identity,
            mode="cache",
            cache_key=hit.key,
            cache_hit=True,
        )
    suffix = "".join(
        extension
        for extension in (".pdb", ".cif", ".gz")
        if entry.source_path.lower().endswith(extension)
    )
    with tempfile.TemporaryDirectory(prefix="pymol_sa_stage_") as stage_dir:
        destination = os.path.join(stage_dir, "download" + suffix)
        downloaded = download_staged_file(alias, relative, destination)
        identity = SourceIdentity(
            provider=f"wsl-stage:{alias}",
            canonical_source=f"{alias}/{relative}",
            size=int(downloaded.source.size),
            mtime_ns=int(downloaded.source.mtime_ns),
            version_token=str(downloaded.source.token),
        )
        materialized = _get_copy_materializer().materialize(
            downloaded.destination,
            provider=identity.provider,
            canonical_source=identity.canonical_source,
            source_identity=identity,
            logical_path=entry.source_path,
        )
        return replace(materialized, source_path=entry.source_path)


def _cache_canonical_source(path: str) -> str:
    value = str(path)
    if ntpath.splitdrive(value)[0] or value.startswith("\\\\"):
        return ntpath.normcase(ntpath.normpath(value))
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def _materialize_entry(
    entry: AnnotationEntry,
    context: _MaterializationContext,
    *,
    staged_source_identity: Optional[object] = None,
) -> MaterializedStructure:
    provider = _entry_provider(entry)
    if provider.startswith("wsl-stage:"):
        return _materialize_wsl_entry(
            entry, context, observed=staged_source_identity
        )
    source = _entry_source_path(entry)
    use_cache = bool(context.cache_enabled) and (
        _is_remote_host_path(source, provider) or _is_gz_path(source)
    )
    if use_cache:
        return _get_copy_materializer().materialize(
            _long_path(source),
            provider=provider,
            canonical_source=_cache_canonical_source(source),
            logical_path=entry.source_path,
        )
    return _get_direct_materializer().materialize(
        _long_path(source),
        provider=provider,
        canonical_source=_cache_canonical_source(source),
    )


def _materialize_index(
    index: int, *, allow_background: bool, trust_cached: bool = False
) -> Optional[MaterializedStructure]:
    global _materialization_futures
    if not STATE.entries:
        return None
    index %= len(STATE.entries)
    entry = STATE.entries[index]
    context = _materialization_context()
    future_key = (context.generation, entry.id)
    cached = STATE.materialized_paths.get(entry.id)
    if cached and (trust_cached or os.path.isfile(_long_path(cached.path))):
        return cached
    future = _materialization_futures.get(future_key)
    if future is not None:
        if not future.done():
            if allow_background:
                return None
        try:
            result = future.result()
        except Exception as exc:
            if context.generation == _materialization_generation:
                STATE.materialization_errors[entry.id] = str(exc)
            _materialization_futures.pop(future_key, None)
            raise
        _materialization_futures.pop(future_key, None)
        if (
            context.generation == _materialization_generation
            and entry.id in {candidate.id for candidate in STATE.entries}
        ):
            STATE.materialized_paths[entry.id] = result
            STATE.materialization_errors.pop(entry.id, None)
        return result
    if allow_background:
        _materialization_futures[future_key] = _get_bg_executor().submit(
            _materialize_entry, entry, context
        )
        return None
    result = _materialize_entry(entry, context)
    if context.generation == _materialization_generation:
        STATE.materialized_paths[entry.id] = result
        STATE.materialization_errors.pop(entry.id, None)
    return result


def _update_cache_navigation_pins() -> None:
    if _structure_cache is None or not STATE.entries:
        return
    current = STATE.materialized_paths.get(STATE.current_entry_id())
    preload = []
    for index in _desired_indices_ahead():
        materialized = STATE.materialized_paths.get(STATE.entries[index].id)
        if materialized is not None and materialized.cache_key:
            preload.append(materialized)
    _structure_cache.set_navigation_pins(
        current=(current if current is not None and current.cache_key else None),
        preload=preload,
    )


def _ensure_bg_cache_dir() -> str:
    global _bg_cache_dir
    if _bg_cache_dir is None:
        _bg_cache_dir = tempfile.mkdtemp(prefix="pymol_structure_annotator_")
    os.makedirs(_bg_cache_dir, exist_ok=True)
    return _bg_cache_dir


def _reset_bg_cache() -> None:
    global _bg_cache_dir, _bg_decompress_futures, _bg_decompressed_paths
    global _materialization_futures, _materialization_generation
    global _preload_generation, _preload_queue
    timer = globals().get("_preload_panel_timer")
    if timer is not None:
        try:
            timer.stop()
        except Exception:
            pass
    _preload_generation = int(globals().get("_preload_generation", 0)) + 1
    _materialization_generation = int(_materialization_generation) + 1
    _preload_queue = []
    _bg_decompress_futures = {}
    _bg_decompressed_paths = {}
    for future in list(_materialization_futures.values()):
        try:
            future.cancel()
        except Exception:
            pass
    _materialization_futures = {}
    STATE.materialized_paths = {}
    STATE.materialization_errors = {}
    if _structure_cache is not None:
        try:
            _structure_cache.set_navigation_pins(current=None, preload=())
        except Exception:
            pass
    if _bg_cache_dir and os.path.isdir(_bg_cache_dir):
        try:
            shutil.rmtree(_bg_cache_dir, ignore_errors=True)
        except Exception:
            pass
    _bg_cache_dir = None


def _is_gz_path(path: str) -> bool:
    return bool(path) and path.lower().endswith(".gz")


def _decompress_gz_to_cache(src_path: str, dst_path: str) -> str:
    src_path = _long_path(src_path)
    dst_path = _long_path(dst_path)
    tmp_path = dst_path + ".tmp"
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with gzip.open(src_path, "rb") as fin, open(tmp_path, "wb") as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)
    os.replace(tmp_path, dst_path)
    return dst_path


def _cached_unzipped_path(src_path: str) -> str:
    base = os.path.basename(src_path)
    if base.lower().endswith(".gz"):
        base = base[: -len(".gz")]
    try:
        st = os.stat(src_path)
        stamp = f"{int(st.st_mtime)}_{st.st_size}"
    except Exception:
        stamp = "unknown"
    cache_dir = _ensure_bg_cache_dir()
    return os.path.join(cache_dir, f"{stamp}_{base}")


def _request_decompressed_path(index: int) -> Optional[str]:
    if not STATE.files:
        return None
    index = index % len(STATE.files)
    src_path = STATE.files[index]
    if not _is_gz_path(src_path):
        return src_path
    if not STATE.background_decompress_gz:
        return src_path

    existing = _bg_decompressed_paths.get(index)
    if existing and os.path.exists(existing):
        return existing

    fut = _bg_decompress_futures.get(index)
    if fut is None or fut.done():
        dst_path = _cached_unzipped_path(src_path)

        def _job() -> str:
            return _decompress_gz_to_cache(src_path, dst_path)

        _bg_decompress_futures[index] = _get_bg_executor().submit(_job)
    return None


def _collect_ready_decompressed(indexes: List[int]) -> None:
    for idx in indexes:
        fut = _bg_decompress_futures.get(idx)
        if not fut or not fut.done():
            continue
        try:
            out = fut.result()
        except Exception:
            out = None
        if out and os.path.exists(out):
            _bg_decompressed_paths[idx] = out


def _evict_decompressed_except(keep_indices: set) -> None:
    for idx, path in list(_bg_decompressed_paths.items()):
        if idx in keep_indices:
            continue
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        _bg_decompressed_paths.pop(idx, None)
        _bg_decompress_futures.pop(idx, None)


def _run_pymol_commands(template: str, context: Dict[str, str]) -> None:
    if not template:
        return
    for raw_line in (template or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            formatted = line.format(**context)
            cmd.do(formatted)
        except Exception as e:
            print(f"Structure Annotator: command failed: {e}")
            print(f"  Template: {line}")
            traceback.print_exc()


@dataclass(frozen=True)
class _ParkedManagedObject:
    original: str
    parked: str
    enabled: bool


def _park_managed_objects() -> List[_ParkedManagedObject]:
    """Rename owned objects so a new dataset can be loaded transactionally."""

    if not STATE.managed_objects:
        return []
    if not hasattr(cmd, "set_name"):
        raise RuntimeError(
            "Host PyMOL lacks cmd.set_name required for transactional dataset swap"
        )
    try:
        existing = set(cmd.get_names("objects"))
        enabled = set(cmd.get_names("objects", enabled_only=1))
    except Exception:
        existing = set(cmd.get_names("all"))
        enabled = set(cmd.get_names("all", enabled_only=1))
    parked: List[_ParkedManagedObject] = []
    token = uuid.uuid4().hex[:12]
    try:
        for index, original in enumerate(list(STATE.managed_objects)):
            if original not in existing:
                continue
            candidate = f"sa_tx_{token}_{index + 1}"
            while candidate in existing:
                candidate += "x"
            cmd.set_name(original, candidate)
            existing.discard(original)
            existing.add(candidate)
            parked.append(
                _ParkedManagedObject(
                    original=original,
                    parked=candidate,
                    enabled=original in enabled,
                )
            )
    except Exception:
        for record in reversed(parked):
            try:
                cmd.set_name(record.parked, record.original)
            except Exception:
                traceback.print_exc()
        raise
    return parked


def _restore_parked_objects(records: Sequence[_ParkedManagedObject]) -> None:
    for record in reversed(list(records)):
        cmd.set_name(record.parked, record.original)
        if record.enabled:
            cmd.enable(record.original)
        else:
            cmd.disable(record.original)


def _discard_parked_objects(records: Sequence[_ParkedManagedObject]) -> None:
    for record in records:
        try:
            cmd.delete(record.parked)
        except Exception:
            traceback.print_exc()


def _begin_scene_swap() -> List[_ParkedManagedObject]:
    parked = _park_managed_objects()
    previous_managed = list(STATE.managed_objects)
    previous_current = STATE.current_object
    try:
        STATE.managed_objects = []
        STATE.current_object = None
        _reset_bg_cache()
    except Exception:
        STATE.managed_objects = previous_managed
        STATE.current_object = previous_current
        _restore_parked_objects(parked)
        raise
    return parked


def _rollback_scene_swap(
    previous: Mapping[str, object], records: Sequence[_ParkedManagedObject]
) -> None:
    _delete_managed_objects(False)
    _reset_bg_cache()
    _restore_runtime_snapshot(previous)
    _restore_parked_objects(records)


def _delete_managed_objects(keep: bool) -> None:
    if keep:
        return
    for obj in list(STATE.managed_objects):
        try:
            cmd.delete(obj)
        except Exception:
            pass
    STATE.managed_objects.clear()
    STATE.current_object = None


def _desired_indices_ahead() -> List[int]:
    if not STATE.files:
        return []
    ahead = max(0, int(STATE.preload_ahead or 0))
    return [(STATE.index + offset) % len(STATE.files) for offset in range(ahead + 1)]


def _evict_unneeded_objects() -> None:
    if STATE.keep_previous:
        return
    if not STATE.files:
        _delete_managed_objects(False)
        return

    if not STATE.preload_enabled or int(STATE.preload_ahead or 0) <= 0:
        desired = {STATE.index}
    else:
        desired = set(_desired_indices_ahead())

    keep_objects = []
    for obj in list(STATE.managed_objects):
        if not _object_exists(obj):
            continue
        keep_objects.append(obj)
    STATE.managed_objects = keep_objects

    loaded_by_index = {}
    for obj in STATE.managed_objects:
        m = re.match(rf"^{re.escape(OBJECT_PREFIX)}_(\d+)_", obj)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        loaded_by_index[idx] = obj

    for idx, obj in list(loaded_by_index.items()):
        if idx not in desired:
            try:
                cmd.delete(obj)
            except Exception:
                pass
            if obj in STATE.managed_objects:
                STATE.managed_objects.remove(obj)

    _evict_decompressed_except(desired)
    _update_cache_navigation_pins()


def _ensure_loaded(
    index: int,
    focus: bool,
    allow_gz_fallback: bool = True,
    *,
    trust_materialized: bool = False,
) -> Optional[str]:
    if not STATE.files:
        return None
    index = index % len(STATE.files)
    path = STATE.files[index]
    obj_name = _object_name_for_index(path, index)

    if not _object_exists(obj_name):
        load_path = path
        if STATE.entries:
            materialized = _materialize_index(
                index,
                allow_background=(not focus and not allow_gz_fallback),
                trust_cached=trust_materialized,
            )
            if materialized is None:
                return None
            load_path = materialized.path
        elif _is_gz_path(path) and STATE.background_decompress_gz:
            requested = _request_decompressed_path(index)
            if requested and os.path.exists(_long_path(requested)):
                load_path = requested
            elif not allow_gz_fallback:
                return None
        # Use long path for Windows compatibility
        load_path_resolved = _long_path(load_path)
        if not os.path.exists(load_path_resolved):
            print(f"Structure Annotator: file not found: {load_path}")
            return None
        if obj_name not in STATE.managed_objects:
            STATE.managed_objects.append(obj_name)
        try:
            cmd.load(load_path_resolved, obj_name, quiet=1)
        except Exception:
            # Some Host loaders can leave a partial object before raising. Keep
            # that name owned so a transaction rollback can delete it exactly.
            if not _object_exists(obj_name) and obj_name in STATE.managed_objects:
                STATE.managed_objects.remove(obj_name)
            raise
        # ``cmd.load`` is synchronous. Calling ``cmd.sync`` from an sa_* parser
        # callback waits on the command queue which currently owns that callback
        # and produces a real Host ``wait_queue`` timeout.
        cmd.refresh()
    if obj_name not in STATE.managed_objects:
        STATE.managed_objects.append(obj_name)

    if not focus:
        try:
            cmd.disable(obj_name)
        except Exception:
            pass
        return obj_name

    for other in list(STATE.managed_objects):
        if other != obj_name and _object_exists(other):
            try:
                cmd.disable(other)
            except Exception:
                pass
    try:
        cmd.enable(obj_name)
    except Exception:
        pass

    STATE.current_object = obj_name
    _update_cache_navigation_pins()

    # Ensure the synchronous load produced an addressable object before a
    # restored command template can run against it.
    if not _object_exists(obj_name):
        cmd.refresh()
        if not _object_exists(obj_name):
            raise RuntimeError(f"loaded object is unavailable: {obj_name}")

    context = {
        "object": obj_name,
        "file": path,
        "index": str(index),
        "count": str(len(STATE.files)),
        "entry_id": STATE.current_entry_id(),
        "row_number": str(
            STATE.current_entry().row_number
            if STATE.current_entry() is not None
            and STATE.current_entry().row_number is not None
            else ""
        ),
    }
    _run_pymol_commands(STATE.step_cmd, context)
    return obj_name


_preload_panel_timer = globals().get("_preload_panel_timer", None)
_preload_generation = globals().get("_preload_generation", 0)
_preload_queue = globals().get("_preload_queue", [])


def _schedule_preload_tick() -> None:
    global _preload_panel_timer
    if QtWidgets is None or QtCore is None:
        return
    if QtWidgets.QApplication.instance() is None:
        return
    if _preload_panel_timer is None:
        _preload_panel_timer = QtCore.QTimer()
        _preload_panel_timer.setSingleShot(True)
        _preload_panel_timer.timeout.connect(_process_preload_tick)
    _preload_panel_timer.start(max(1, int(STATE.preload_interval_ms)))


def _kick_preload() -> None:
    global _preload_generation, _preload_queue
    if not STATE.preload_enabled:
        return
    if not STATE.files:
        return
    if int(STATE.preload_ahead or 0) <= 0:
        return

    desired = _desired_indices_ahead()
    pending = []
    generation = int(_materialization_generation)
    desired_entry_ids = {
        STATE.entries[idx].id for idx in desired if idx < len(STATE.entries)
    }
    for key, future in list(_materialization_futures.items()):
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or key[0] != generation
            or key[1] in desired_entry_ids
        ):
            continue
        try:
            removable = future.cancel() or future.done()
        except Exception:
            removable = True
        if removable:
            _materialization_futures.pop(key, None)
    for idx in desired:
        obj = _object_name_for_index(STATE.files[idx], idx)
        if not _object_exists(obj):
            entry_id = STATE.entries[idx].id if idx < len(STATE.entries) else ""
            pending.append((generation, entry_id, idx))

    _preload_generation += 1
    _preload_queue = pending
    if STATE.entries:
        for _generation, _entry_id, idx in list(_preload_queue):
            try:
                _materialize_index(idx, allow_background=True)
            except Exception:
                pass
    if STATE.background_decompress_gz:
        for _generation, _entry_id, idx in list(_preload_queue):
            try:
                _request_decompressed_path(idx)
            except Exception:
                pass
    if _preload_queue:
        _schedule_preload_tick()


def _process_preload_tick() -> None:
    global _preload_queue
    if not _preload_queue:
        return
    generation, entry_id, idx = _preload_queue[0]
    if (
        generation != _materialization_generation
        or idx < 0
        or idx >= len(STATE.files)
        or (
            entry_id
            and (idx >= len(STATE.entries) or STATE.entries[idx].id != entry_id)
        )
    ):
        _preload_queue.pop(0)
        if _preload_queue:
            _schedule_preload_tick()
        return
    _collect_ready_decompressed([idx])

    try:
        loaded = _ensure_loaded(idx, focus=False, allow_gz_fallback=False)
        if loaded is None:
            _schedule_preload_tick()
            return
        _preload_queue.pop(0)
        _evict_unneeded_objects()
    except Exception as e:
        print(f"Structure Annotator preload error: {e}")
        traceback.print_exc()
        _preload_queue = []
        return
    if _preload_queue:
        _schedule_preload_tick()


def _load_current_structure(*, trust_materialized: bool = False) -> str:
    if not STATE.current_file():
        raise RuntimeError("Structure Annotator: no dataset loaded")
    if not STATE.keep_previous and not STATE.preload_enabled:
        _delete_managed_objects(False)

    loaded = _ensure_loaded(
        STATE.index,
        focus=True,
        allow_gz_fallback=True,
        trust_materialized=trust_materialized,
    )
    if not loaded:
        raise RuntimeError(f"failed to load structure at index {STATE.index}")
    _evict_unneeded_objects()
    _kick_preload()
    return loaded


def _navigate_annotation(method: str, *args) -> str:
    """Transition the shared cursor and roll it back if display loading fails."""
    session = _ensure_navigation_session()
    previous = session.cursor
    getattr(session, method)(*args)
    STATE.index = session.cursor
    try:
        loaded = _load_current_structure()
        _save_current_dataset_state()
        return loaded
    except Exception:
        session.goto(previous)
        STATE.index = session.cursor
        try:
            _load_current_structure()
        except Exception:
            traceback.print_exc()
        raise


def _annotations_for_index(index: int) -> Dict[str, str]:
    if not STATE.files or index < 0 or index >= len(STATE.files):
        return {}
    if STATE.source_mode == "csv":
        if STATE.row_annotation_store is None or index >= len(STATE.entries):
            return {}
        return STATE.row_annotation_store.annotations_for(STATE.entries[index].id)
    return read_structure_annotations(_resolve_annotation_path(STATE.files[index]))


def _set_entry_annotation(index: int, label: str, value: str) -> Dict[str, object]:
    if STATE.source_mode == "csv":
        if STATE.row_annotation_store is None:
            raise RuntimeError("CSV annotation store is unavailable")
        return STATE.row_annotation_store.set(STATE.entries[index].id, label, value)
    path = _resolve_annotation_path(STATE.files[index])
    previous = read_structure_annotations(path).get(label)
    annotate_structure_file(path, label, value)
    return {
        "entry_id": STATE.entries[index].id if STATE.entries else "",
        "path": path,
        "previous": previous,
        "current": value,
        "had_previous": previous is not None,
    }


def _remove_entry_annotation(index: int, label: str) -> Dict[str, object]:
    if STATE.source_mode == "csv":
        if STATE.row_annotation_store is None:
            raise RuntimeError("CSV annotation store is unavailable")
        return STATE.row_annotation_store.remove(STATE.entries[index].id, label)
    path = _resolve_annotation_path(STATE.files[index])
    previous = read_structure_annotations(path).get(label)
    remove_structure_annotation(path, label)
    return {
        "entry_id": STATE.entries[index].id if STATE.entries else "",
        "path": path,
        "previous": previous,
        "current": None,
        "had_previous": previous is not None,
    }


def _rescan_annotated_indices() -> None:
    """Populate STATE.annotated_indices for progress tracking under the current label."""
    STATE.annotated_indices = set()
    STATE.annotated_entry_ids = set()
    if not STATE.files:
        return
    label_key = normalize_label_key(STATE.label_name)
    if STATE.source_mode == "csv" and STATE.row_annotation_store is not None:
        try:
            values = STATE.row_annotation_store.snapshot_for_label(label_key)
        except Exception:
            return
        for idx, entry in enumerate(STATE.entries):
            if entry.id in values:
                STATE.annotated_indices.add(idx)
                STATE.annotated_entry_ids.add(entry.id)
        return
    for idx, _path in enumerate(STATE.files):
        try:
            if _annotations_for_index(idx).get(label_key) is not None:
                STATE.annotated_indices.add(idx)
                if idx < len(STATE.entries):
                    STATE.annotated_entry_ids.add(STATE.entries[idx].id)
        except Exception:
            pass


def _display_column_names(value: object) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
            raise ValueError("display_columns JSON must be an array of strings")
        return list(parsed)
    return _split_values(text.replace("|", ";"))


def _mapping_rules(
    profile_name: str, *, read_only: bool = False
) -> Sequence[Mapping[str, object]]:
    profile = (
        get_mapping_profile_readonly(profile_name)
        if read_only
        else get_mapping_profile(profile_name)
    )
    if profile is None:
        raise MappingStoreError(f"unknown path mapping profile: {profile_name}")
    rules = profile.get("rules", [])
    if not isinstance(rules, list):
        raise MappingStoreError("mapping profile rules must be a list")
    return rules


@dataclass(frozen=True)
class _StagedPathResolution(PathResolution):
    """Path result carrying one already-validated worker stat observation."""

    staged_source_identity: Optional[object] = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True)
class _StageCoordinates:
    root: str
    source: str
    alias: str
    relative: str


@dataclass(frozen=True)
class _StageOutcome:
    observed: Optional[object]
    error: Optional[BaseException]
    method: str


class _CsvPreparationCancelled(RuntimeError):
    """Raised at safe phase boundaries after a Qt cancellation request."""


def _check_csv_preparation_cancelled(cancel_event: Optional[object]) -> None:
    if cancel_event is not None and bool(cancel_event.is_set()):
        raise _CsvPreparationCancelled("CSV preparation was cancelled")


def _stage_coordinates(
    entry: AnnotationEntry, stage_root: str, stage_alias: str
) -> Optional[_StageCoordinates]:
    root = str(stage_root or "").strip()
    alias = str(stage_alias or "").strip()
    source = str(entry.source_path or "").strip()
    if not root or not alias or not source.startswith("/"):
        return None
    normalized_root = posixpath.normpath(root)
    normalized_source = posixpath.normpath(source)
    if not (
        normalized_source != normalized_root
        and normalized_source.startswith(normalized_root.rstrip("/") + "/")
    ):
        return None
    return _StageCoordinates(
        root=normalized_root,
        source=normalized_source,
        alias=alias,
        relative=posixpath.relpath(normalized_source, normalized_root),
    )


def _stage_error_status(code: str) -> str:
    if code == "stage_file_not_found":
        return AVAILABILITY_MISSING
    if code in {"stage_file_unreadable", "stage_path_forbidden"}:
        return AVAILABILITY_UNREADABLE
    if code in {"invalid_stage_path", "stage_not_regular_file"}:
        return AVAILABILITY_INVALID
    return AVAILABILITY_ERROR


def _stage_resolution_from_outcome(
    entry: AnnotationEntry,
    coordinates: _StageCoordinates,
    *,
    require_exists: bool,
    observed: Optional[object] = None,
    error: Optional[BaseException] = None,
    method: str = "wsl-stage-stat",
) -> PathResolution:
    provider = f"wsl-stage:{coordinates.alias}"
    if not require_exists:
        return _StagedPathResolution(
            raw_path=entry.source_path,
            resolved_path=coordinates.source,
            status=AVAILABILITY_UNCHECKED,
            provider=provider,
            method="wsl-stage-unchecked",
            matched_rule=coordinates.root,
        )
    if error is None:
        status = AVAILABILITY_AVAILABLE
        diagnostics: Tuple[str, ...] = ()
    elif isinstance(error, WorkerRequestError):
        code = str(getattr(error, "code", ""))
        status = _stage_error_status(code)
        diagnostics = (f"WSL staging {code or 'error'}: {error}",)
    else:
        status = AVAILABILITY_ERROR
        diagnostics = (f"WSL staging error: {error}",)
    return _StagedPathResolution(
        raw_path=entry.source_path,
        resolved_path=coordinates.source,
        status=status,
        provider=provider,
        method=method,
        matched_rule=coordinates.root,
        diagnostics=diagnostics,
        staged_source_identity=observed,
    )


def _stage_resolution(
    entry: AnnotationEntry,
    stage_root: str,
    stage_alias: str,
    *,
    require_exists: bool,
) -> Optional[PathResolution]:
    coordinates = _stage_coordinates(entry, stage_root, stage_alias)
    if coordinates is None:
        return None
    if not require_exists:
        return _stage_resolution_from_outcome(
            entry, coordinates, require_exists=False
        )
    observed = None
    error = None
    try:
        observed = stat_staged_file(coordinates.alias, coordinates.relative)
    except Exception as exc:
        error = exc
    return _stage_resolution_from_outcome(
        entry,
        coordinates,
        require_exists=True,
        observed=observed,
        error=error,
    )


def _collect_stage_outcomes(
    entries: Sequence[AnnotationEntry],
    stage_root: str,
    stage_alias: str,
    *,
    require_exists: bool,
    cancel_event: Optional[object] = None,
) -> Dict[str, _StageOutcome]:
    _check_csv_preparation_cancelled(cancel_event)
    if not require_exists:
        return {}
    coordinates_by_relative: Dict[str, _StageCoordinates] = {}
    for entry in entries:
        _check_csv_preparation_cancelled(cancel_event)
        coordinates = _stage_coordinates(entry, stage_root, stage_alias)
        if coordinates is not None:
            coordinates_by_relative.setdefault(coordinates.relative, coordinates)
    if not coordinates_by_relative:
        return {}
    relatives = list(coordinates_by_relative)
    if len(relatives) == 1:
        relative = relatives[0]
        try:
            observed = stat_staged_file(str(stage_alias).strip(), relative)
            error = None
        except Exception as exc:
            observed = None
            error = exc
        _check_csv_preparation_cancelled(cancel_event)
        return {
            relative: _StageOutcome(observed, error, "wsl-stage-stat")
        }
    try:
        results = stat_staged_files(str(stage_alias).strip(), relatives)
    except Exception as exc:
        _check_csv_preparation_cancelled(cancel_event)
        return {
            relative: _StageOutcome(None, exc, "wsl-stage-stat-batch")
            for relative in relatives
        }
    _check_csv_preparation_cancelled(cancel_event)
    return {
        result.relative_path: _StageOutcome(
            result.source, result.error, "wsl-stage-stat-batch"
        )
        for result in results
    }


def _resolve_csv_dataset(
    dataset: AnnotationDataset,
    mapping_profile: str,
    *,
    stage_root: str = "",
    stage_alias: str = "",
    require_exists: bool = True,
    read_only_mappings: bool = False,
    cancel_event: Optional[object] = None,
) -> AnnotationDataset:
    _check_csv_preparation_cancelled(cancel_event)
    resolver = PathResolver(
        rules=_mapping_rules(mapping_profile, read_only=read_only_mappings),
        exists=lambda path: os.path.exists(_long_path(path)),
        readable=lambda path: os.access(_long_path(path), os.R_OK),
        is_file=lambda path: os.path.isfile(_long_path(path)),
    )
    resolved_entries = []
    # Only allocate cached resolutions for raw keys which are actually
    # duplicated.  Populating a 50k-entry cache for an all-unique table costs
    # more CPU and memory than it saves.  PathResolver separately deduplicates
    # distinct aliases which map to the same physical Windows candidate.
    first_source_base = (
        str(dataset.entries[0].source_base) if dataset.entries else ""
    )
    homogeneous_source_base = all(
        str(entry.source_base) == first_source_base for entry in dataset.entries
    )

    def _resolution_key(entry: AnnotationEntry):
        raw_path = str(entry.source_path)
        base_is_irrelevant = bool(
            raw_path.startswith(("/", "\\"))
            or raw_path == "~"
            or raw_path.startswith("~/")
            or ntpath.splitdrive(raw_path)[0]
        )
        if homogeneous_source_base or base_is_irrelevant:
            return raw_path
        return (raw_path, str(entry.source_base))

    seen_resolution_keys = set()
    duplicate_resolution_keys = set()
    for candidate_entry in dataset.entries:
        candidate_key = _resolution_key(candidate_entry)
        if candidate_key in seen_resolution_keys:
            duplicate_resolution_keys.add(candidate_key)
        else:
            seen_resolution_keys.add(candidate_key)
    del seen_resolution_keys
    has_duplicate_resolutions = bool(duplicate_resolution_keys)
    resolution_cache: Dict[object, PathResolution] = {}
    stage_outcomes = _collect_stage_outcomes(
        dataset.entries,
        stage_root,
        stage_alias,
        require_exists=require_exists,
        cancel_event=cancel_event,
    )
    for entry in dataset.entries:
        _check_csv_preparation_cancelled(cancel_event)
        resolution_key = (
            _resolution_key(entry) if has_duplicate_resolutions else None
        )
        cache_resolution = (
            has_duplicate_resolutions
            and resolution_key in duplicate_resolution_keys
        )
        resolution = (
            resolution_cache.get(resolution_key) if cache_resolution else None
        )
        if resolution is None:
            coordinates = _stage_coordinates(entry, stage_root, stage_alias)
            outcome = (
                stage_outcomes.get(coordinates.relative)
                if coordinates is not None
                else None
            )
            if outcome is not None and coordinates is not None:
                resolution = _stage_resolution_from_outcome(
                    entry,
                    coordinates,
                    require_exists=require_exists,
                    observed=outcome.observed,
                    error=outcome.error,
                    method=outcome.method,
                )
            else:
                resolution = _stage_resolution(
                    entry,
                    stage_root,
                    stage_alias,
                    require_exists=require_exists,
                )
            if (
                resolution is None
                and os.name != "nt"
                and not entry.source_path.startswith("~")
            ):
                candidate = entry.source_path
                if not os.path.isabs(candidate):
                    candidate = os.path.join(entry.source_base, candidate)
                candidate = os.path.abspath(candidate)
                if os.path.isfile(candidate) and os.access(candidate, os.R_OK):
                    resolution = PathResolution(
                        raw_path=entry.source_path,
                        resolved_path=candidate,
                        status=AVAILABILITY_AVAILABLE,
                        provider="local",
                        method="local-direct",
                    )
            if resolution is None:
                resolution = resolver.resolve(
                    entry.source_path,
                    source_base=entry.source_base,
                    require_exists=require_exists,
                )
            if cache_resolution:
                resolution_cache[resolution_key] = resolution
        resolved_entries.append(entry.with_resolution(resolution))
    _check_csv_preparation_cancelled(cancel_event)
    return replace(dataset, entries=tuple(resolved_entries))


def _csv_preview_payload(
    dataset: AnnotationDataset,
    mapping_profile: str,
    *,
    row_limit: int = 50,
) -> Dict[str, object]:
    counts: Dict[str, int] = {}
    rule_counts: Dict[str, int] = {}
    for entry in dataset.entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1
        rule = getattr(entry.path_resolution, "matched_rule", "")
        if rule:
            rule_counts[str(rule)] = rule_counts.get(str(rule), 0) + 1
    rows = []
    for entry in dataset.entries[: max(0, int(row_limit))]:
        resolution = entry.path_resolution
        rows.append(
            {
                "entry_id": entry.id,
                "row_number": entry.row_number,
                "display_name": entry.display_name,
                "raw_path": entry.source_path,
                "resolved_path": entry.resolved_path or None,
                "status": entry.status,
                "provider": getattr(resolution, "provider", None),
                "matched_rule": getattr(resolution, "matched_rule", None),
                "error": entry.error or None,
                "metadata": dict(entry.metadata),
            }
        )
    return {
        "schema": "pymolplugins.structure-annotator-csv-preview/1.0",
        "status": "ok",
        "csv_path": dataset.source_path,
        "path_column": dataset.path_column,
        "id_column": dataset.id_column,
        "delimiter": dataset.delimiter,
        "encoding": dataset.encoding,
        "columns": list(dataset.columns),
        "display_columns": list(dataset.display_columns),
        "mapping_profile": mapping_profile,
        "row_count": dataset.count,
        "status_counts": counts,
        "rule_counts": rule_counts,
        "rows": rows,
        "preview_truncated": dataset.count > len(rows),
    }


@dataclass(frozen=True)
class _CsvFileFingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int


def _csv_file_fingerprint(path: str) -> _CsvFileFingerprint:
    try:
        metadata = os.stat(_long_path(path))
    except OSError as exc:
        raise RuntimeError(f"cannot inspect CSV {path}: {exc}") from exc
    return _CsvFileFingerprint(
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        size=int(metadata.st_size),
        mtime_ns=int(
            getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1_000_000_000))
        ),
    )


@dataclass(frozen=True)
class _CsvRuntimeContext:
    source_mode: str
    csv_path: str
    path_column: str
    id_column: str
    display_columns: Tuple[str, ...]
    csv_encoding: str
    csv_delimiter: str
    source_base: str
    mapping_profile: str
    stage_root: str
    stage_alias: str
    filter_text: str
    sort_column: str
    sort_descending: bool
    cache_enabled: bool
    label_name: str
    materialization_generation: int


def _capture_csv_runtime_context() -> _CsvRuntimeContext:
    """Capture every live value a worker may need before leaving main thread."""

    return _CsvRuntimeContext(
        source_mode=str(STATE.source_mode),
        csv_path=str(STATE.csv_path or ""),
        path_column=str(STATE.path_column or ""),
        id_column=str(STATE.id_column or ""),
        display_columns=tuple(str(value) for value in STATE.display_columns),
        csv_encoding=str(STATE.csv_encoding or DEFAULT_ENCODING),
        csv_delimiter=str(STATE.csv_delimiter or AUTO_DELIMITER),
        source_base=str(STATE.source_base or ""),
        mapping_profile=str(STATE.mapping_profile or DEFAULT_PROFILE_NAME),
        stage_root=str(STATE.stage_root or ""),
        stage_alias=str(STATE.stage_alias or ""),
        filter_text=str(STATE.filter_text or ""),
        sort_column=str(STATE.sort_column or ""),
        sort_descending=bool(STATE.sort_descending),
        cache_enabled=bool(STATE.cache_enabled),
        label_name=str(STATE.label_name or "quality"),
        materialization_generation=int(_materialization_generation),
    )


@dataclass(frozen=True)
class _PreparedCsvPreview:
    payload: Dict[str, object]
    source_fingerprint: _CsvFileFingerprint


@dataclass(frozen=True)
class _PreparedCsvLoad:
    dataset: AnnotationDataset
    active_entries: Tuple[AnnotationEntry, ...]
    payload: Dict[str, object]
    persisted_config: Optional[Dict[str, object]]
    mapping_profile: str
    stage_root: str
    stage_alias: str
    filter_text: str
    sort_column: str
    sort_descending: bool
    requested_index: int
    requested_entry: str
    selected_entry: AnnotationEntry
    preflight_materialized: MaterializedStructure
    row_annotation_store: RowAnnotationStore
    annotated_entry_ids: Tuple[str, ...]
    source_fingerprint: _CsvFileFingerprint


def _prepare_csv_preview(
    csv_path: str,
    path_column: str,
    id_column: str = "",
    display_columns: object = "",
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = AUTO_DELIMITER,
    mapping_profile: str = DEFAULT_PROFILE_NAME,
    source_base: str = "",
    stage_root: str = "",
    stage_alias: str = "",
    *,
    cancel_event: Optional[object] = None,
) -> _PreparedCsvPreview:
    """Perform preview I/O without reading live STATE or touching PyMOL/Qt."""

    canonical_csv = os.path.abspath(os.path.expanduser(str(csv_path)))
    _check_csv_preparation_cancelled(cancel_event)
    before = _csv_file_fingerprint(canonical_csv)
    source = CsvDatasetSource(
        canonical_csv,
        str(path_column),
        id_column=str(id_column or ""),
        display_columns=_display_column_names(display_columns),
        encoding=str(encoding or DEFAULT_ENCODING),
        delimiter=str(delimiter or AUTO_DELIMITER),
        source_base=str(source_base or ""),
    )
    dataset = source.load()
    _check_csv_preparation_cancelled(cancel_event)
    profile = str(mapping_profile or DEFAULT_PROFILE_NAME)
    dataset = _resolve_csv_dataset(
        dataset,
        profile,
        stage_root=str(stage_root or ""),
        stage_alias=str(stage_alias or ""),
        require_exists=True,
        read_only_mappings=True,
        cancel_event=cancel_event,
    )
    after = _csv_file_fingerprint(canonical_csv)
    if after != before:
        raise RuntimeError("CSV changed while preview was being prepared; retry")
    return _PreparedCsvPreview(
        payload=_csv_preview_payload(dataset, profile),
        source_fingerprint=after,
    )


def _prepare_csv_load(
    csv_path: str,
    path_column: Optional[str],
    id_column: Optional[str],
    display_columns: Optional[object],
    encoding: Optional[str],
    delimiter: Optional[str],
    mapping_profile: Optional[str],
    source_base: Optional[str],
    stage_root: Optional[str],
    stage_alias: Optional[str],
    runtime: _CsvRuntimeContext,
    *,
    cancel_event: Optional[object] = None,
) -> _PreparedCsvLoad:
    """Prepare one immutable CSV load plan without consulting live STATE."""

    canonical_csv = os.path.abspath(os.path.expanduser(str(csv_path)))
    _check_csv_preparation_cancelled(cancel_event)
    before = _csv_file_fingerprint(canonical_csv)
    same_active_csv = bool(
        runtime.source_mode == "csv"
        and runtime.csv_path
        and _path_identity(runtime.csv_path) == _path_identity(canonical_csv)
    )
    persisted = _read_persisted_dataset_state(canonical_csv, "csv")
    saved_csv = dict(persisted.get("csv") or {}) if persisted else {}
    saved_view = dict(persisted.get("view") or {}) if persisted else {}

    def _source_value(
        supplied: Optional[object],
        saved_name: str,
        active_value: object,
        default: object,
    ) -> object:
        if supplied is not None:
            return supplied
        if saved_name in saved_csv:
            return saved_csv[saved_name]
        if same_active_csv:
            return active_value
        return default

    effective_path_column = str(
        _source_value(path_column, "path_column", runtime.path_column, "structure_path")
    )
    if not effective_path_column:
        raise ValueError("path_column is required for CSV mode")
    effective_id_column = str(
        _source_value(id_column, "id_column", runtime.id_column, "") or ""
    )
    effective_display_columns = _display_column_names(
        _source_value(
            display_columns,
            "display_columns",
            runtime.display_columns,
            None,
        )
    )
    effective_encoding = str(
        _source_value(
            encoding, "encoding", runtime.csv_encoding, DEFAULT_ENCODING
        )
        or DEFAULT_ENCODING
    )
    effective_delimiter = str(
        _source_value(
            delimiter, "delimiter", runtime.csv_delimiter, AUTO_DELIMITER
        )
        or AUTO_DELIMITER
    )
    saved_profile = persisted.get("mapping_profile") if persisted else None
    if mapping_profile is not None:
        effective_profile = str(mapping_profile or DEFAULT_PROFILE_NAME)
    elif saved_profile:
        effective_profile = str(saved_profile)
    else:
        effective_profile = str(runtime.mapping_profile or DEFAULT_PROFILE_NAME)
    effective_source_base = str(
        _source_value(source_base, "source_base", runtime.source_base, "") or ""
    )
    effective_stage_root = str(
        _source_value(stage_root, "stage_root", runtime.stage_root, "") or ""
    )
    effective_stage_alias = str(
        _source_value(stage_alias, "stage_alias", runtime.stage_alias, "") or ""
    )
    filters = saved_view.get("filters") if persisted else {}
    sorting = saved_view.get("sort") if persisted else {}
    next_filter = str(filters.get("text", "")) if isinstance(filters, dict) else ""
    next_sort_column = (
        str(sorting.get("column", "")) if isinstance(sorting, dict) else ""
    )
    next_sort_descending = (
        bool(sorting.get("descending", False))
        if isinstance(sorting, dict)
        else False
    )
    if persisted is None and same_active_csv:
        next_filter = runtime.filter_text
        next_sort_column = runtime.sort_column
        next_sort_descending = runtime.sort_descending

    _check_csv_preparation_cancelled(cancel_event)
    source = CsvDatasetSource(
        canonical_csv,
        effective_path_column,
        id_column=effective_id_column,
        display_columns=effective_display_columns,
        encoding=effective_encoding,
        delimiter=effective_delimiter,
        source_base=effective_source_base,
    )
    dataset = source.load()
    _check_csv_preparation_cancelled(cancel_event)
    dataset = _resolve_csv_dataset(
        dataset,
        effective_profile,
        stage_root=effective_stage_root,
        stage_alias=effective_stage_alias,
        require_exists=True,
        read_only_mappings=True,
        cancel_event=cancel_event,
    )
    position = persisted.get("position") if persisted else None
    requested_index = int((position or {}).get("index", 0))
    requested_entry = str((position or {}).get("entry_id", ""))
    active_entries = _view_entries(
        dataset.entries,
        filter_text=next_filter,
        sort_column=next_sort_column,
        sort_descending=next_sort_descending,
    )
    if not active_entries:
        raise RuntimeError("CSV contains no resolvable, valid structure rows")
    selected_entry = active_entries[
        _resume_entry_index(active_entries, requested_entry, requested_index)
    ]
    _check_csv_preparation_cancelled(cancel_event)
    preflight_materialized = _materialize_entry(
        selected_entry,
        _MaterializationContext(
            generation=runtime.materialization_generation,
            cache_enabled=runtime.cache_enabled,
            stage_root=effective_stage_root,
            stage_alias=effective_stage_alias,
        ),
        staged_source_identity=getattr(
            selected_entry.path_resolution,
            "staged_source_identity",
            None,
        ),
    )
    _check_csv_preparation_cancelled(cancel_event)
    # Read annotations last so a slow remote materialization does not leave the
    # prepared progress snapshot stale for the duration of the download/copy.
    row_annotation_store = RowAnnotationStore(
        annotation_sidecar_path(dataset.source_path)
    )
    persisted_config = (
        dict(persisted.get("config") or {}) if persisted is not None else None
    )
    label_name = str(
        (persisted_config or {}).get("label_name") or runtime.label_name or "quality"
    )
    annotated_values = row_annotation_store.snapshot_for_label(
        normalize_label_key(label_name)
    )
    annotated_entry_ids = tuple(
        entry.id for entry in active_entries if entry.id in annotated_values
    )
    _check_csv_preparation_cancelled(cancel_event)
    after = _csv_file_fingerprint(canonical_csv)
    if after != before:
        raise RuntimeError("CSV changed while load was being prepared; retry")
    return _PreparedCsvLoad(
        dataset=dataset,
        active_entries=tuple(active_entries),
        payload=_csv_preview_payload(
            dataset, effective_profile, row_limit=20
        ),
        persisted_config=persisted_config,
        mapping_profile=effective_profile,
        stage_root=effective_stage_root,
        stage_alias=effective_stage_alias,
        filter_text=next_filter,
        sort_column=next_sort_column,
        sort_descending=next_sort_descending,
        requested_index=requested_index,
        requested_entry=requested_entry,
        selected_entry=selected_entry,
        preflight_materialized=preflight_materialized,
        row_annotation_store=row_annotation_store,
        annotated_entry_ids=annotated_entry_ids,
        source_fingerprint=after,
    )


def _commit_prepared_csv_load(plan: _PreparedCsvLoad) -> Dict[str, object]:
    """Commit a prepared plan transactionally; call only on PyMOL main thread."""

    previous = _runtime_snapshot()
    parked: List[_ParkedManagedObject] = []
    scene_swap_started = False
    try:
        if plan.persisted_config is not None:
            _apply_persisted_config(plan.persisted_config)
        parked = _begin_scene_swap()
        scene_swap_started = True
        dataset = plan.dataset
        STATE.source_mode = "csv"
        STATE.csv_path = dataset.source_path
        STATE.dataset_dir = _source_parent(dataset.source_path)
        STATE.path_column = dataset.path_column
        STATE.id_column = dataset.id_column
        STATE.display_columns = list(dataset.display_columns)
        STATE.csv_encoding = dataset.encoding
        STATE.csv_delimiter = dataset.delimiter
        STATE.source_base = dataset.source_base
        STATE.mapping_profile = plan.mapping_profile
        STATE.stage_root = plan.stage_root
        STATE.stage_alias = plan.stage_alias
        STATE.filter_text = plan.filter_text
        STATE.sort_column = plan.sort_column
        STATE.sort_descending = plan.sort_descending
        STATE.dataset = dataset
        STATE.all_entries = list(dataset.entries)
        STATE.row_annotation_store = plan.row_annotation_store
        STATE.undo_history.clear()
        _install_entries(
            dataset.entries,
            resume_entry_id=plan.requested_entry,
            index=plan.requested_index,
            active_entries=plan.active_entries,
        )
        STATE.materialized_paths[plan.selected_entry.id] = (
            plan.preflight_materialized
        )
        annotated_ids = set(plan.annotated_entry_ids)
        STATE.annotated_entry_ids = {
            entry.id for entry in STATE.entries if entry.id in annotated_ids
        }
        STATE.annotated_indices = {
            index
            for index, entry in enumerate(STATE.entries)
            if entry.id in annotated_ids
        }
        _load_current_structure(trust_materialized=True)
        _discard_parked_objects(parked)
        parked = []
        scene_swap_started = False
        state_path = _write_persisted_dataset_state(dataset.source_path)
        _record_current_config_history()
        payload = dict(plan.payload)
        payload.update(
            {
                "status": "loaded",
                "active_count": len(STATE.entries),
                "state_path": state_path,
                "annotation_path": STATE.row_annotation_store.path,
            }
        )
        print(
            f"Structure Annotator: loaded {len(STATE.entries)} navigable rows "
            f"from {dataset.source_path} ({dataset.count} total rows)"
        )
        print(f"Structure Annotator: CSV state: {state_path}")
        return payload
    except Exception:
        try:
            if scene_swap_started:
                _rollback_scene_swap(previous, parked)
            else:
                _restore_runtime_snapshot(previous)
        except Exception:
            traceback.print_exc()
        raise


def sa_csv_preview(
    csv_path: str,
    path_column: str,
    id_column: str = "",
    display_columns: object = "",
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = AUTO_DELIMITER,
    mapping_profile: str = DEFAULT_PROFILE_NAME,
    source_base: str = "",
    stage_root: str = "",
    stage_alias: str = "",
) -> Dict[str, object]:
    """Synchronously preview a CSV for console/script compatibility."""

    prepared = _prepare_csv_preview(
        csv_path,
        path_column,
        id_column,
        display_columns,
        encoding,
        delimiter,
        mapping_profile,
        source_base,
        stage_root,
        stage_alias,
    )
    print(json.dumps(prepared.payload, indent=2, sort_keys=True, ensure_ascii=False))
    return prepared.payload


def sa_load_csv(
    csv_path: str,
    path_column: Optional[str] = None,
    id_column: Optional[str] = None,
    display_columns: Optional[object] = None,
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
    mapping_profile: Optional[str] = None,
    source_base: Optional[str] = None,
    stage_root: Optional[str] = None,
    stage_alias: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    """Synchronously prepare and commit one independently annotatable CSV row."""

    try:
        plan = _prepare_csv_load(
            csv_path,
            path_column,
            id_column,
            display_columns,
            encoding,
            delimiter,
            mapping_profile,
            source_base,
            stage_root,
            stage_alias,
            _capture_csv_runtime_context(),
        )
        return _commit_prepared_csv_load(plan)
    except Exception as exc:
        print(f"Structure Annotator CSV load error: {exc}")
        traceback.print_exc()
        return None


def sa_load_dir(path: str = ".", pattern: str = "") -> None:
    """
    Load a directory of structures for annotation.

    Args:
        path: Directory containing .pdb/.cif (optionally .gz).
        pattern: Optional explicit regex override. Empty restores the dataset's
            saved pattern, or uses the default for a first-time dataset.
    """
    previous = _runtime_snapshot()
    parked: List[_ParkedManagedObject] = []
    scene_swap_started = False
    try:
        dataset_dir = os.path.abspath(os.path.expanduser(path))
        persisted = _read_persisted_dataset_state(dataset_dir, "directory")
        if persisted is not None:
            _apply_persisted_config(persisted["config"])
        explicit_pattern = str(pattern or "").strip()
        if explicit_pattern:
            STATE.pattern = explicit_pattern
        elif persisted is None:
            STATE.pattern = STRUCTURE_PATTERN
        effective_pattern = STATE.pattern or STRUCTURE_PATTERN

        label_key = normalize_label_key(STATE.label_name) if STATE.skip_annotated else None
        discovered = _list_structure_files(
            dataset_dir, effective_pattern, STATE.prefer_gz,
            skip_annotated=STATE.skip_annotated, label_key=label_key
        )
        if not discovered:
            _restore_runtime_snapshot(previous)
            print(f"Structure Annotator: no files matched in {path}; current dataset kept")
            return

        entries = _directory_entries(dataset_dir, discovered)
        resume_at = _resume_index(
            dataset_dir,
            discovered,
            persisted.get("position") if persisted is not None else None,
            entries,
        )
        selected_entry = entries[resume_at]
        preflight_materialized = _materialize_entry(
            selected_entry,
            _MaterializationContext(
                generation=int(_materialization_generation),
                cache_enabled=bool(STATE.cache_enabled),
                stage_root="",
                stage_alias="",
            ),
        )
        # Keep old owned objects parked until the replacement has loaded.  This
        # preserves the complete keep_previous scene if the new load fails.
        parked = _begin_scene_swap()
        scene_swap_started = True
        STATE.source_mode = "directory"
        STATE.dataset_dir = dataset_dir
        STATE.csv_path = ""
        STATE.path_column = "structure_path"
        STATE.id_column = ""
        STATE.display_columns = []
        STATE.source_base = dataset_dir
        STATE.filter_text = ""
        STATE.sort_column = ""
        STATE.sort_descending = False
        STATE.stage_root = ""
        STATE.stage_alias = ""
        STATE.pattern = effective_pattern
        STATE.dataset = AnnotationDataset(
            source_kind="directory",
            source_path=dataset_dir,
            source_base=dataset_dir,
            entries=tuple(entries),
            columns=("structure_path", "name"),
            path_column="structure_path",
        )
        STATE.all_entries = list(entries)
        STATE.row_annotation_store = None
        _install_entries(entries, index=resume_at)
        STATE.materialized_paths[selected_entry.id] = preflight_materialized
        STATE.current_object = None
        STATE.undo_history.clear()
        STATE.annotated_entry_ids.clear()
        _rescan_annotated_indices()
        _load_current_structure()
        _discard_parked_objects(parked)
        parked = []
        scene_swap_started = False
        state_path = _write_persisted_dataset_state(dataset_dir)
        annotated = len(STATE.annotated_indices)
        print(
            f"Structure Annotator: loaded {len(STATE.files)} files from {path} "
            f"({annotated} already annotated for '{normalize_label_key(STATE.label_name)}')"
        )
        if state_path:
            print(f"Structure Annotator: dataset state: {state_path}")
        _record_current_config_history()
    except Exception as e:
        # A failed new load must not leave a writable context pointing at the
        # wrong dataset.  Restore the old cursor/domain state and reload it.
        try:
            if scene_swap_started:
                _rollback_scene_swap(previous, parked)
            else:
                _restore_runtime_snapshot(previous)
        except Exception:
            traceback.print_exc()
        print(f"Structure Annotator load error: {e}")
        traceback.print_exc()


def sa_next() -> None:
    try:
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return
        _navigate_annotation("next")
    except Exception as e:
        print(f"Structure Annotator next error: {e}")
        traceback.print_exc()


def sa_prev() -> None:
    try:
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return
        _navigate_annotation("previous")
    except Exception as e:
        print(f"Structure Annotator prev error: {e}")
        traceback.print_exc()


def sa_annotate(value: str) -> None:
    """
    Annotate current structure and optionally advance.

    Args:
        value: One of the configured bucket values (e.g. Good/Bad).
    """
    try:
        path = STATE.current_file()
        obj_name = STATE.current_object
        if not path or not obj_name:
            print("Structure Annotator: nothing to annotate")
            return

        # Verify the displayed object matches STATE before annotating
        if not _verify_current_object_enabled():
            enabled = set(cmd.get_names("objects", enabled_only=1))
            enabled_managed = sorted(enabled.intersection(STATE.managed_objects))
            print("Structure Annotator: scene/context mismatch; annotation aborted")
            print(f"  Expected exactly: {obj_name} (index {STATE.index})")
            print(f"  Enabled managed objects: {enabled_managed or '(none)'}")
            print("  Click Reload to explicitly sync or restore the expected view.")
            return

        label_key = normalize_label_key(STATE.label_name)
        entry = STATE.current_entry()
        entry_id = entry.id if entry is not None else ""
        prev_value = _annotations_for_index(STATE.index).get(label_key)
        change = _set_entry_annotation(STATE.index, label_key, value)
        STATE.annotation_generation = int(STATE.annotation_generation) + 1

        # Record for undo (bounded stack).
        STATE.undo_history.append({
            "index": STATE.index,
            "entry_id": entry_id,
            "path": change.get("path", path),
            "mode": STATE.source_mode,
            "label_key": label_key,
            "prev_value": prev_value,
            "value": value,
        })
        if len(STATE.undo_history) > 100:
            STATE.undo_history.pop(0)
        STATE.annotated_indices.add(STATE.index)
        if entry_id:
            STATE.annotated_entry_ids.add(entry_id)

        context = {
            "object": obj_name,
            "file": path,
            "index": str(STATE.index),
            "count": str(len(STATE.files)),
            "label": label_key,
            "value": value,
            "entry_id": entry_id,
            "row_number": str(entry.row_number if entry and entry.row_number else ""),
        }
        _run_pymol_commands(STATE.bucket_cmds.get(value, ""), context)
        _save_current_dataset_state()
        _record_current_config_history()

        display_name = entry.display_name if entry is not None else os.path.basename(path)
        print(f"Annotated {display_name}: {label_key}={value}")
        if STATE.auto_advance:
            sa_next()
    except Exception as e:
        print(f"Structure Annotator annotate error: {e}")
        traceback.print_exc()


def sa_undo() -> None:
    """Undo the most recent annotation, restoring the file's prior state."""
    try:
        if not STATE.undo_history:
            print("Structure Annotator: nothing to undo")
            return
        entry = STATE.undo_history.pop()
        path = entry["path"]
        entry_id = str(entry.get("entry_id", ""))
        label_key = entry["label_key"]
        prev_value = entry["prev_value"]
        index = entry["index"]

        if entry.get("mode") == "csv":
            if STATE.row_annotation_store is None:
                raise RuntimeError("CSV annotation store is unavailable")
            change = STATE.row_annotation_store.undo(entry_id=entry_id, label=label_key)
            if change is None:
                raise RuntimeError("CSV annotation history no longer contains this change")
            current = change.get("current")
            restored = "(none)" if current is None else str(current)
            for candidate, source_entry in enumerate(STATE.entries):
                if source_entry.id == entry_id:
                    index = candidate
                    break
        else:
            if prev_value is None:
                remove_structure_annotation(path, label_key)
                restored = "(none)"
            else:
                annotate_structure_file(path, label_key, prev_value)
                restored = prev_value

        STATE.annotation_generation = int(STATE.annotation_generation) + 1
        _rescan_annotated_indices()

        display = (
            STATE.entries[index].display_name
            if 0 <= index < len(STATE.entries)
            else os.path.basename(path)
        )
        print(f"Undo: {display} {label_key} -> {restored}")
        # Return to the file we just changed so the user can see the result.
        if 0 <= index < len(STATE.files):
            _navigate_annotation("goto", index)
        else:
            _save_current_dataset_state()
    except Exception as e:
        print(f"Structure Annotator undo error: {e}")
        traceback.print_exc()


def sa_goto(position: int) -> None:
    """
    Jump to a 1-based position in the dataset.

    Args:
        position: 1-based index of the structure to display.
    """
    try:
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return
        target = int(position) - 1
        if target < 0 or target >= len(STATE.files):
            print(f"Structure Annotator: position must be 1..{len(STATE.files)}")
            return
        _navigate_annotation("goto", target)
    except Exception as e:
        print(f"Structure Annotator goto error: {e}")
        traceback.print_exc()


def sa_next_unannotated() -> None:
    """Advance to the next structure that has no annotation for the current label."""
    try:
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return
        total = len(STATE.files)
        for offset in range(1, total + 1):
            candidate = (STATE.index + offset) % total
            if candidate not in STATE.annotated_indices:
                _navigate_annotation("goto", candidate)
                return
        print("Structure Annotator: all files are annotated for this label")
    except Exception as e:
        print(f"Structure Annotator next-unannotated error: {e}")
        traceback.print_exc()


def _unique_csv_column(base: str, used: set) -> str:
    candidate = str(base)
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def sa_export_csv(out_path: str = "") -> Optional[str]:
    """
    Export all annotations in the current dataset to a CSV file.

    Args:
        out_path: Destination CSV. Defaults to 'annotations_<label>.csv' in the dataset dir.

    Returns the written path, or None on failure.
    """
    try:
        import csv
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return None
        label_key = normalize_label_key(STATE.label_name)
        if STATE.source_mode == "csv":
            if STATE.dataset is None or STATE.row_annotation_store is None:
                raise RuntimeError("CSV dataset or annotation store is unavailable")
            if not out_path:
                stem = os.path.splitext(os.path.basename(STATE.csv_path))[0]
                out_path = os.path.join(
                    STATE.dataset_dir,
                    f"{stem}_annotated_{label_key}.csv",
                )
            destination = os.path.abspath(os.path.expanduser(out_path))
            if _path_identity(destination) == _path_identity(STATE.csv_path):
                raise ValueError("CSV export refuses to overwrite the source document")
            annotations = STATE.row_annotation_store.export_mapping()
            source_width = len(STATE.dataset.columns)
            extra_width = max(
                [max(0, len(entry.raw_values) - source_width) for entry in STATE.dataset.entries]
                or [0]
            )
            header = list(STATE.dataset.columns)
            used_headers = set(header)
            extra_headers = [
                _unique_csv_column(f"__extra_{index + 1}", used_headers)
                for index in range(extra_width)
            ]
            header.extend(extra_headers)
            annotation_header = f"pymol_annotation_{label_key}"
            existing_annotation_index = (
                header.index(annotation_header)
                if annotation_header in header
                else None
            )
            if existing_annotation_index is None:
                annotation_header = _unique_csv_column(
                    annotation_header, used_headers
                )
                header.append(annotation_header)
            rows = []
            for entry in STATE.dataset.entries:
                values = list(entry.raw_values)
                values.extend([""] * (source_width + extra_width - len(values)))
                annotation_value = annotations.get(entry.id, {}).get(label_key, "")
                if existing_annotation_index is None:
                    values.append(annotation_value)
                else:
                    values[existing_annotation_index] = annotation_value
                rows.append(values)
            os.makedirs(os.path.dirname(destination) or os.curdir, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".structure_annotations.", suffix=".tmp",
                dir=os.path.dirname(destination) or os.curdir,
            )
            try:
                with os.fdopen(
                    descriptor, "w", newline="", encoding="utf-8-sig"
                ) as fh:
                    writer = csv.writer(fh, delimiter=STATE.dataset.delimiter or ",")
                    writer.writerow(header)
                    writer.writerows(rows)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temporary, destination)
                temporary = ""
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
            annotated = sum(
                1
                for entry in STATE.dataset.entries
                if annotations.get(entry.id, {}).get(label_key, "")
            )
            print(
                f"Structure Annotator: exported {len(rows)} CSV rows "
                f"({annotated} annotated) to {destination}"
            )
            return destination
        if not out_path:
            out_path = os.path.join(
                STATE.dataset_dir or ".", f"annotations_{label_key}.csv"
            )
        rows = []
        for path in STATE.files:
            annotations = read_structure_annotations(_resolve_annotation_path(path))
            rows.append((os.path.basename(path), annotations.get(label_key, ""), path))

        with open(_long_path(out_path), "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["filename", label_key, "path"])
            writer.writerows(rows)

        annotated = sum(1 for r in rows if r[1])
        print(
            f"Structure Annotator: exported {len(rows)} rows "
            f"({annotated} annotated) to {out_path}"
        )
        return out_path
    except Exception as e:
        print(f"Structure Annotator export error: {e}")
        traceback.print_exc()
        return None


def sa_save_state() -> Optional[str]:
    """Persist the dataset state and finalized global config history."""

    path = _save_current_dataset_state()
    if path:
        _record_current_config_history()
        print(f"Structure Annotator: saved dataset state: {path}")
    else:
        print("Structure Annotator: no active dataset state to save")
    return path


def sa_state_status() -> Dict[str, object]:
    """Return the current dataset's persistent-state location and payload."""

    active = _active_source_reference()
    target = active or STATE.csv_path or STATE.dataset_dir or "."
    kwargs = _state_kwargs() if active else {"source_mode": "directory"}
    path = dataset_state_path(target, **kwargs)
    result: Dict[str, object] = {
        "status": "inactive" if not active else "active",
        "source_mode": STATE.source_mode,
        "source": active,
        "dataset_dir": _active_dataset_directory(),
        "csv_path": STATE.csv_path if STATE.source_mode == "csv" else "",
        "state_path": path,
        "exists": os.path.isfile(_long_path(path)),
    }
    if result["exists"]:
        try:
            result["state"] = read_dataset_state(target, **kwargs)
        except StateStoreError as exc:
            result.update({"status": "invalid", "error": str(exc)})
    print(result)
    return result


def sa_history_search(
    field: str = "step_cmd", query: str = "", limit: int = 12
) -> Dict[str, object]:
    """Fuzzy-search global input history for console use and diagnostics."""

    try:
        suggestions = search_config_history(field, query, int(limit))
        result: Dict[str, object] = {
            "status": "ok",
            "field": str(field),
            "query": str(query),
            "suggestions": suggestions,
        }
    except Exception as exc:
        result = {
            "status": "error",
            "field": str(field),
            "query": str(query),
            "error": str(exc),
            "allowed_fields": list(HISTORY_FIELDS),
        }
    print(result)
    return result


def sa_history_status() -> Dict[str, object]:
    """Return the global history path, schema, and bounded entry counts."""

    try:
        result = {"status": "ok", **history_summary()}
    except Exception as exc:
        result = {"status": "invalid", "error": str(exc)}
    print(result)
    return result


def _flag(value: object) -> bool:
    if type(value) is bool:
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"expected a boolean flag, got {value!r}")


def sa_cache_status(include_entries: int = 0) -> Dict[str, object]:
    result = _get_structure_cache().status(include_entries=_flag(include_entries))
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return result


def sa_cache_clear(force: int = 0) -> Dict[str, object]:
    result = _get_structure_cache().clear(force=_flag(force))
    print(result)
    return result


def sa_mapping_list() -> Dict[str, object]:
    result = {
        "status": "ok",
        **mapping_store_status(),
        "profiles": list_mapping_profiles(),
    }
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return result


def sa_mapping_add(
    profile: str,
    source_prefix: str,
    target_prefix: str,
    provider: str = "mapped",
    priority: int = 0,
    require_exists: int = 1,
    case_sensitive: int = 1,
) -> Dict[str, object]:
    current = get_mapping_profile(str(profile))
    rules = list(current.get("rules", [])) if current else []
    replacement = {
        "source_prefix": str(source_prefix),
        "target_prefix": str(target_prefix),
        "priority": int(priority),
        "enabled": True,
        "case_sensitive": _flag(case_sensitive),
        "require_exists": _flag(require_exists),
        "provider": str(provider or "mapped"),
    }
    rules = [
        rule
        for rule in rules
        if str(rule.get("source_prefix", "")) != str(source_prefix)
    ]
    rules.append(replacement)
    saved = upsert_mapping_profile(
        str(profile),
        rules,
        description=str((current or {}).get("description", "")),
    )
    print(json.dumps(saved, indent=2, sort_keys=True, ensure_ascii=False))
    return saved


def sa_mapping_remove(profile: str, source_prefix: str = "") -> Dict[str, object]:
    name = str(profile)
    source = str(source_prefix or "")
    if not source:
        removed = remove_mapping_profile(name)
        result = {"status": "removed" if removed else "missing", "profile": name}
        print(result)
        return result
    current = get_mapping_profile(name)
    if current is None:
        result = {"status": "missing", "profile": name, "source_prefix": source}
        print(result)
        return result
    rules = [
        rule for rule in current.get("rules", [])
        if str(rule.get("source_prefix", "")) != source
    ]
    saved = upsert_mapping_profile(
        name, rules, description=str(current.get("description", ""))
    )
    result = {"status": "ok", "profile": saved}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return result


def sa_filter(
    query: str = "", sort_column: str = "", descending: int = 0
) -> Dict[str, object]:
    if not STATE.all_entries:
        result = {"status": "inactive", "error": "no dataset loaded"}
        print(result)
        return result
    previous = _runtime_snapshot()
    current_id = STATE.current_entry_id()
    parked: List[_ParkedManagedObject] = []
    scene_swap_started = False
    try:
        next_filter = str(query or "")
        next_sort = str(sort_column or "")
        next_descending = _flag(descending)
        active_entries = _view_entries(
            STATE.all_entries,
            filter_text=next_filter,
            sort_column=next_sort,
            sort_descending=next_descending,
        )
        if not active_entries:
            raise RuntimeError("Structure Annotator: no navigable structure rows")
        selected_index = _resume_entry_index(active_entries, current_id, STATE.index)
        selected_entry = active_entries[selected_index]
        preflight_materialized = _materialize_entry(
            selected_entry, _materialization_context()
        )
        parked = _begin_scene_swap()
        scene_swap_started = True
        STATE.filter_text = next_filter
        STATE.sort_column = next_sort
        STATE.sort_descending = next_descending
        _install_entries(STATE.all_entries, resume_entry_id=current_id)
        STATE.materialized_paths[selected_entry.id] = preflight_materialized
        STATE.undo_history.clear()
        _rescan_annotated_indices()
        _load_current_structure()
        _discard_parked_objects(parked)
        parked = []
        scene_swap_started = False
        _save_current_dataset_state()
        result = {
            "status": "ok",
            "query": STATE.filter_text,
            "sort_column": STATE.sort_column,
            "descending": STATE.sort_descending,
            "active_count": len(STATE.entries),
            "total_count": len(STATE.all_entries),
        }
    except Exception as exc:
        try:
            if scene_swap_started:
                _rollback_scene_swap(previous, parked)
            else:
                _restore_runtime_snapshot(previous)
        except Exception:
            traceback.print_exc()
        result = {"status": "error", "error": str(exc)}
    print(result)
    return result


def _sa_key_next():
    sa_next()
    if _panel is not None:
        _panel._refresh_status()


def _sa_key_prev():
    sa_prev()
    if _panel is not None:
        _panel._refresh_status()


def _sa_annotate_nth(n: int) -> None:
    """Annotate with the n-th (1-based) configured bucket value."""
    values = STATE.values()
    if 1 <= n <= len(values):
        sa_annotate(values[n - 1])
        if _panel is not None:
            try:
                _panel._refresh_status()
            except Exception:
                pass


def _sa_key_undo():
    sa_undo()
    if _panel is not None:
        _panel._refresh_status()


_panel = globals().get("_panel", None)
_key_lease = globals().get("_key_lease", None)


def sa_bind_keys() -> Dict[str, object]:
    """Bind annotator shortcuts until ``sa_unbind_keys`` or panel close."""
    global _key_lease
    try:
        if _key_lease is not None and _key_lease.active:
            return {"status": "already_bound", "keys": list(_key_lease.callbacks)}
        callbacks = {
            "ALT-right": _sa_key_next,
            "ALT-left": _sa_key_prev,
            "ALT-z": _sa_key_undo,
        }
        for digit in range(1, 10):
            callbacks[f"ALT-{digit}"] = (
                lambda selected=digit: _sa_annotate_nth(selected)
            )
        _key_lease = KeyBindingLease(cmd, callbacks, "Structure Annotator")
        _key_lease.acquire()
        return {"status": "bound", "keys": list(callbacks)}
    except Exception as exc:
        print(f"Structure Annotator key binding error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def sa_unbind_keys() -> Dict[str, object]:
    """Restore every shortcut that was active before the panel lease."""
    global _key_lease
    try:
        if _key_lease is None or not _key_lease.active:
            return {"status": "inactive"}
        keys = list(_key_lease.callbacks)
        _key_lease.release()
        return {"status": "restored", "keys": keys}
    except Exception as exc:
        print(f"Structure Annotator key restore error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


_CSV_ASYNC_JOBS: Dict[str, Dict[str, object]] = globals().get(
    "_CSV_ASYNC_JOBS", {}
)
_CSV_ASYNC_BRIDGE = globals().get("_CSV_ASYNC_BRIDGE", None)
_CSV_ASYNC_POOL = globals().get("_CSV_ASYNC_POOL", None)


_HAS_QT_ASYNC = all(
    hasattr(QtCore, name)
    for name in ("QObject", "QRunnable", "QThreadPool", "QCoreApplication", "Signal", "Slot")
)


if _HAS_QT_ASYNC:
    class _CsvIoBridge(QtCore.QObject):
        completed = QtCore.Signal(str, object, object)

        def __init__(self):
            super().__init__()
            queued = (
                QtCore.Qt.ConnectionType.QueuedConnection
                if hasattr(QtCore.Qt, "ConnectionType")
                else QtCore.Qt.QueuedConnection
            )
            self.completed.connect(self._dispatch, queued)

        @QtCore.Slot(str, object, object)
        def _dispatch(self, job_id, result, error):
            _complete_csv_async_job(job_id, result, error)


    class _CsvIoRunnable(QtCore.QRunnable):
        """Run only an immutable prepare callable; never retain a panel/cmd."""

        def __init__(self, bridge, job_id, worker, args, kwargs):
            super().__init__()
            self.bridge = bridge
            self.job_id = job_id
            self.worker = worker
            self.args = tuple(args)
            self.kwargs = dict(kwargs)

        @QtCore.Slot()
        def run(self):
            try:
                result = self.worker(*self.args, **self.kwargs)
                self.bridge.completed.emit(self.job_id, result, None)
            except Exception as exc:
                self.bridge.completed.emit(self.job_id, None, exc)


def _csv_async_bridge():
    global _CSV_ASYNC_BRIDGE
    if not _HAS_QT_ASYNC:
        raise RuntimeError("PyMOL Qt asynchronous primitives are unavailable")
    if QtCore.QCoreApplication.instance() is None:
        raise RuntimeError("no Qt application/event loop is active")
    if _CSV_ASYNC_BRIDGE is None:
        _CSV_ASYNC_BRIDGE = _CsvIoBridge()
    return _CSV_ASYNC_BRIDGE


def _csv_async_pool():
    global _CSV_ASYNC_POOL
    if not _HAS_QT_ASYNC:
        raise RuntimeError("PyMOL Qt asynchronous primitives are unavailable")
    if _CSV_ASYNC_POOL is None:
        _CSV_ASYNC_POOL = QtCore.QThreadPool()
        _CSV_ASYNC_POOL.setMaxThreadCount(2)
    return _CSV_ASYNC_POOL


def _submit_csv_async_job(
    panel,
    kind: str,
    worker,
    args: Sequence[object],
    kwargs: Mapping[str, object],
    *,
    epoch: int,
    arguments_fingerprint: Tuple[Tuple[str, str], ...],
    config_fingerprint: Tuple[object, ...],
    dataset_generation: int,
    annotation_generation: int,
) -> str:
    bridge = _csv_async_bridge()
    cancel_event = threading.Event()
    job_id = uuid.uuid4().hex[:12]
    worker_kwargs = dict(kwargs)
    worker_kwargs["cancel_event"] = cancel_event
    runnable = _CsvIoRunnable(
        bridge, job_id, worker, tuple(args), worker_kwargs
    )
    _CSV_ASYNC_JOBS[job_id] = {
        "kind": str(kind),
        "panel": weakref.ref(panel),
        "epoch": int(epoch),
        "arguments_fingerprint": tuple(arguments_fingerprint),
        "config_fingerprint": tuple(config_fingerprint),
        "dataset_generation": int(dataset_generation),
        "annotation_generation": int(annotation_generation),
        "cancel_event": cancel_event,
        "cancelled": False,
        "runnable": runnable,
    }
    _csv_async_pool().start(runnable)
    return job_id


def _cancel_csv_async_job(job_id: str) -> bool:
    job = _CSV_ASYNC_JOBS.get(str(job_id))
    if job is None:
        return False
    job["cancelled"] = True
    event = job.get("cancel_event")
    if event is not None:
        event.set()
    return True


def _complete_csv_async_job(job_id: str, result, error) -> None:
    """Queued Qt-main-thread dispatcher for a completed prepare job."""

    job = _CSV_ASYNC_JOBS.pop(str(job_id), None)
    if job is None or bool(job.get("cancelled")):
        return
    panel_ref = job.get("panel")
    panel = panel_ref() if callable(panel_ref) else None
    if panel is None:
        return
    panel._complete_csv_task(str(job_id), job, result, error)


class _HistoryCompletionBinding:
    """Attach dynamically fuzzy-ranked global history to one Qt text widget."""

    def __init__(self, widget, field: str, multiline: bool = False):
        self.widget = widget
        self.field = field
        self.multiline = bool(multiline)
        self._accepting = False
        self.model = QtCore.QStringListModel([], widget)
        self.completer = QtWidgets.QCompleter(widget)
        self.completer.setModel(self.model)

        popup_mode = getattr(QtWidgets.QCompleter, "PopupCompletion", None)
        if popup_mode is None:
            completion_mode = getattr(QtWidgets.QCompleter, "CompletionMode", None)
            popup_mode = getattr(completion_mode, "PopupCompletion", None)
        if popup_mode is not None:
            self.completer.setCompletionMode(popup_mode)
        self.completer.setMaxVisibleItems(12)

        case_insensitive = getattr(QtCore.Qt, "CaseInsensitive", None)
        if case_insensitive is None:
            case_enum = getattr(QtCore.Qt, "CaseSensitivity", None)
            case_insensitive = getattr(case_enum, "CaseInsensitive", None)
        if case_insensitive is not None:
            self.completer.setCaseSensitivity(case_insensitive)

        if self.multiline:
            self.completer.setWidget(widget)
            widget.textChanged.connect(self.refresh)
        else:
            widget.setCompleter(self.completer)
            widget.textEdited.connect(self.refresh)
        try:
            self.completer.activated[str].connect(self._accept)
        except Exception:
            self.completer.activated.connect(self._accept)

    def _current_text(self) -> str:
        if self.multiline:
            return str(self.widget.toPlainText())
        return str(self.widget.text())

    def refresh(self, *args) -> None:
        if self._accepting:
            return
        try:
            if not self.widget.hasFocus():
                return
            suggestions = _history_suggestions(
                self.field, self._current_text(), limit=12
            )
            self.model.setStringList(suggestions)
            if not suggestions:
                self.completer.popup().hide()
                return
            # The model is already fuzzy-filtered. An empty completion prefix
            # prevents QCompleter from applying a second prefix-only filter.
            self.completer.setCompletionPrefix("")
            self.completer.popup().setCurrentIndex(self.model.index(0, 0))
            if self.multiline:
                rectangle = self.widget.cursorRect()
                width = self.completer.popup().sizeHintForColumn(0)
                width += self.completer.popup().verticalScrollBar().sizeHint().width()
                rectangle.setWidth(max(rectangle.width(), width))
                self.completer.complete(rectangle)
            else:
                self.completer.complete()
        except Exception:
            # Completion is an affordance; it must never interrupt annotation.
            try:
                self.completer.popup().hide()
            except Exception:
                pass

    def _accept(self, completion) -> None:
        value = completion
        if not isinstance(value, str) and hasattr(value, "data"):
            value = value.data()
        value = str(value)
        self._accepting = True
        try:
            if self.multiline:
                self.widget.setPlainText(value)
            else:
                self.widget.setText(value)
            self.completer.popup().hide()
        finally:
            self._accepting = False

    def detach(self) -> None:
        try:
            self.completer.popup().hide()
        except Exception:
            pass


def _qt_enum(group: str, name: str):
    """Return one Qt5 flat or Qt6 scoped enum value."""

    value = getattr(QtCore.Qt, name, None)
    if value is not None:
        return value
    return getattr(getattr(QtCore.Qt, group), name)


def _qt_class_enum(owner, group: str, name: str):
    """Return one Qt5 flat or Qt6 class-scoped enum value."""

    value = getattr(owner, name, None)
    if value is not None:
        return value
    return getattr(getattr(owner, group), name)


class _DatasetTableModel(QtCore.QAbstractTableModel):
    """Lazy Scores table; cells are rendered from row data on demand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "active"
        self._rows: List[object] = []
        self._headers = ["Row", "Status", "Structure"]
        self._display_columns: List[str] = []
        self._source_mode = "directory"
        self._annotation_values: Dict[str, str] = {}
        self._entry_id_to_row: Dict[str, int] = {}

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._headers)

    def _entry_id(self, row: int) -> str:
        if not 0 <= row < len(self._rows):
            return ""
        value = self._rows[row]
        if self._mode == "preview":
            return str(dict(value).get("entry_id", ""))
        return str(value.id)

    def entry_id_at(self, row: int) -> str:
        return self._entry_id(int(row))

    def row_for_entry(self, entry_id: str) -> int:
        return self._entry_id_to_row.get(str(entry_id), -1)

    def _active_value(self, entry: AnnotationEntry, column: int) -> object:
        if column == 0:
            return entry.row_number if entry.row_number is not None else entry.ordinal + 1
        if column == 1:
            return entry.status
        if column == 2:
            return entry.display_name
        offset = 3
        if self._source_mode == "csv":
            if column == 3:
                return self._annotation_values.get(entry.id, "")
            offset = 4
        metadata_index = column - offset
        if 0 <= metadata_index < len(self._display_columns):
            return entry.metadata.get(self._display_columns[metadata_index], "")
        return ""

    def _preview_value(self, raw: Mapping[str, object], column: int) -> object:
        if column == 0:
            return raw.get("row_number", "")
        if column == 1:
            return raw.get("status", "")
        if column == 2:
            return raw.get("display_name", "")
        metadata_index = column - 3
        if 0 <= metadata_index < len(self._display_columns):
            return dict(raw.get("metadata") or {}).get(
                self._display_columns[metadata_index], ""
            )
        return ""

    def data(self, index, role=None):
        if index is None or not index.isValid():
            return None
        display_role = _qt_enum("ItemDataRole", "DisplayRole")
        user_role = _qt_enum("ItemDataRole", "UserRole")
        if role == user_role:
            return self._entry_id(index.row())
        if role != display_role:
            return None
        raw = self._rows[index.row()]
        value = (
            self._preview_value(dict(raw), index.column())
            if self._mode == "preview"
            else self._active_value(raw, index.column())
        )
        return str(value if value is not None else "")

    def headerData(self, section: int, orientation, role=None):
        if (
            role == _qt_enum("ItemDataRole", "DisplayRole")
            and orientation == _qt_enum("Orientation", "Horizontal")
            and 0 <= section < len(self._headers)
        ):
            return self._headers[section]
        return None

    def _reset(
        self,
        *,
        mode: str,
        rows: Sequence[object],
        headers: Sequence[str],
        display_columns: Sequence[str],
        source_mode: str,
        annotation_values: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.beginResetModel()
        self._mode = str(mode)
        self._rows = list(rows)
        self._headers = [str(value) for value in headers]
        self._display_columns = [str(value) for value in display_columns]
        self._source_mode = str(source_mode)
        self._annotation_values = {
            str(key): str(value)
            for key, value in dict(annotation_values or {}).items()
        }
        self._entry_id_to_row = {
            self._entry_id(row): row for row in range(len(self._rows))
        }
        self.endResetModel()

    def set_preview(
        self, rows: Sequence[Mapping[str, object]], display_columns: Sequence[str]
    ) -> None:
        self._reset(
            mode="preview",
            rows=rows,
            headers=["Row", "Status", "Structure"] + list(display_columns),
            display_columns=display_columns,
            source_mode="preview",
        )

    def set_active(
        self,
        entries: Sequence[AnnotationEntry],
        display_columns: Sequence[str],
        source_mode: str,
        label_key: str,
        annotation_values: Mapping[str, str],
    ) -> None:
        headers = ["Row", "Status", "Structure"]
        if source_mode == "csv":
            headers.append(f"Annotation ({label_key})")
        headers.extend(display_columns)
        self._reset(
            mode="active",
            rows=entries,
            headers=headers,
            display_columns=display_columns,
            source_mode=source_mode,
            annotation_values=annotation_values,
        )

    def clear_rows(self) -> None:
        self._reset(
            mode="active",
            rows=(),
            headers=("Row", "Status", "Structure"),
            display_columns=(),
            source_mode="directory",
        )

    def update_annotations(self, values: Mapping[str, str]) -> Tuple[str, ...]:
        """Replace annotations and emit only cells whose display changed."""

        next_values = {str(key): str(value) for key, value in dict(values).items()}
        changed = tuple(
            entry_id
            for entry_id in sorted(set(self._annotation_values).union(next_values))
            if self._annotation_values.get(entry_id, "")
            != next_values.get(entry_id, "")
        )
        self._annotation_values = next_values
        if self._mode != "active" or self._source_mode != "csv":
            return changed
        display_role = _qt_enum("ItemDataRole", "DisplayRole")
        for entry_id in changed:
            row = self._entry_id_to_row.get(entry_id)
            if row is None:
                continue
            model_index = self.index(row, 3)
            try:
                self.dataChanged.emit(model_index, model_index, [display_role])
            except TypeError:
                self.dataChanged.emit(model_index, model_index)
        return changed


class StructureAnnotatorPanel(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Structure Annotator")
        self.setMinimumWidth(900)
        self.setMinimumHeight(720)
        self._bucket_inputs: Dict[str, QtWidgets.QLineEdit] = {}
        self._value_buttons: List[QtWidgets.QPushButton] = []
        self._history_bindings: List[_HistoryCompletionBinding] = []
        self._bucket_history_bindings: List[_HistoryCompletionBinding] = []
        self._dataset_table_signature = None
        self._table_annotation_revision = None
        self._table_mode = "active"
        self._table_source_token = ""
        self._preview_rows_by_id: Dict[str, Dict[str, object]] = {}
        self._entry_id_to_active_index: Dict[str, int] = {}
        self._entries_by_id: Dict[str, AnnotationEntry] = {}
        self._csv_task_id = ""
        self._csv_task_kind = ""
        self._csv_task_epoch = 0
        self._csv_task_arguments_fingerprint: Tuple[Tuple[str, str], ...] = ()
        self._csv_task_dataset_generation = -1
        self._closed = False
        self._last_csv_completion_on_main_thread = None
        self._state_save_timer = QtCore.QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.timeout.connect(self._save_ui_state)
        deferred_restore = _restore_latest_config_from_history(
            defer_dataset=True
        )
        self._build_ui()
        self._install_history_completers()
        self._sync_from_state()
        self._rebuild_value_controls()
        self._refresh_status()
        if deferred_restore:
            QtCore.QTimer.singleShot(0, self._restore_deferred_dataset)

    def closeEvent(self, event) -> None:
        global _panel
        self._closed = True
        self._cancel_csv_task(announce=False)
        try:
            self._state_save_timer.stop()
            self._sync_to_state()
            _record_current_config_history()
        except Exception:
            traceback.print_exc()
        sa_unbind_keys()
        _panel = None
        super().closeEvent(event)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        self.main_tabs = QtWidgets.QTabWidget()
        source_tab = QtWidgets.QWidget()
        source_tab_layout = QtWidgets.QVBoxLayout(source_tab)
        annotation_tab = QtWidgets.QWidget()
        annotation_tab_layout = QtWidgets.QVBoxLayout(annotation_tab)
        scores_tab = QtWidgets.QWidget()
        scores_tab_layout = QtWidgets.QVBoxLayout(scores_tab)
        self.main_tabs.addTab(source_tab, "Source")
        self.main_tabs.addTab(annotation_tab, "Annotate")
        self.main_tabs.addTab(scores_tab, "Scores")
        layout.addWidget(self.main_tabs, 1)

        dataset_box = QtWidgets.QGroupBox("Dataset")
        dataset_layout = QtWidgets.QGridLayout(dataset_box)

        self.source_mode_combo = QtWidgets.QComboBox()
        self.source_mode_combo.addItem("Directory", "directory")
        self.source_mode_combo.addItem("CSV + scores", "csv")
        self.source_mode_combo.currentIndexChanged.connect(self._source_mode_changed)

        self.dir_edit = QtWidgets.QLineEdit()
        self.dir_browse = QtWidgets.QPushButton("Browse…")
        self.dir_browse.clicked.connect(self._browse_dir)

        self.pattern_edit = QtWidgets.QLineEdit()
        self.pattern_edit.editingFinished.connect(self._save_ui_state)

        self.load_btn = QtWidgets.QPushButton("Load")
        self.load_btn.clicked.connect(self._load_dataset)

        self.csv_edit = QtWidgets.QLineEdit()
        self.csv_browse = QtWidgets.QPushButton("Browse CSV…")
        self.csv_browse.clicked.connect(self._browse_csv)
        self.path_column_edit = QtWidgets.QLineEdit()
        self.path_column_edit.setPlaceholderText("required exact header, e.g. structure_path")
        self.id_column_edit = QtWidgets.QLineEdit()
        self.id_column_edit.setPlaceholderText("optional unique row ID column")
        self.display_columns_edit = QtWidgets.QLineEdit()
        self.display_columns_edit.setPlaceholderText("optional; semicolon-separated; default all scores")
        self.encoding_combo = QtWidgets.QComboBox()
        self.encoding_combo.setEditable(True)
        self.encoding_combo.addItems(["utf-8-sig", "utf-8", "gb18030"])
        self.delimiter_combo = QtWidgets.QComboBox()
        self.delimiter_combo.setEditable(True)
        self.delimiter_combo.addItems(["auto", "comma", "tab", "semicolon"])
        self.mapping_profile_combo = QtWidgets.QComboBox()
        self.mapping_profile_combo.setEditable(True)
        try:
            self.mapping_profile_combo.addItems(
                [str(profile["name"]) for profile in list_mapping_profiles()]
            )
        except Exception:
            self.mapping_profile_combo.addItem(DEFAULT_PROFILE_NAME)
        self.source_base_edit = QtWidgets.QLineEdit()
        self.source_base_edit.setPlaceholderText("optional base for relative structure paths")
        self.stage_root_edit = QtWidgets.QLineEdit()
        self.stage_root_edit.setPlaceholderText("optional WSL allowed root, e.g. /home/admin/data")
        self.stage_alias_edit = QtWidgets.QLineEdit()
        self.stage_alias_edit.setPlaceholderText("worker --stage-root alias")
        self.csv_preview_btn = QtWidgets.QPushButton("Preview mapping")
        self.csv_preview_btn.clicked.connect(self._preview_csv)
        self.csv_load_btn = QtWidgets.QPushButton("Load CSV")
        self.csv_load_btn.clicked.connect(self._load_csv_dataset)

        dataset_layout.addWidget(QtWidgets.QLabel("Source mode:"), 0, 0)
        dataset_layout.addWidget(self.source_mode_combo, 0, 1, 1, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Directory:"), 1, 0)
        dataset_layout.addWidget(self.dir_edit, 1, 1)
        dataset_layout.addWidget(self.dir_browse, 1, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Pattern:"), 2, 0)
        dataset_layout.addWidget(self.pattern_edit, 2, 1)
        dataset_layout.addWidget(self.load_btn, 2, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("CSV file:"), 3, 0)
        dataset_layout.addWidget(self.csv_edit, 3, 1)
        dataset_layout.addWidget(self.csv_browse, 3, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Path / ID columns:"), 4, 0)
        csv_columns_row = QtWidgets.QHBoxLayout()
        csv_columns_row.addWidget(self.path_column_edit)
        csv_columns_row.addWidget(self.id_column_edit)
        csv_columns_widget = QtWidgets.QWidget()
        csv_columns_widget.setLayout(csv_columns_row)
        dataset_layout.addWidget(csv_columns_widget, 4, 1, 1, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Display columns:"), 5, 0)
        dataset_layout.addWidget(self.display_columns_edit, 5, 1, 1, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("CSV parsing:"), 6, 0)
        parsing_row = QtWidgets.QHBoxLayout()
        parsing_row.addWidget(QtWidgets.QLabel("Encoding"))
        parsing_row.addWidget(self.encoding_combo)
        parsing_row.addWidget(QtWidgets.QLabel("Delimiter"))
        parsing_row.addWidget(self.delimiter_combo)
        parsing_row.addWidget(QtWidgets.QLabel("Mapping"))
        parsing_row.addWidget(self.mapping_profile_combo)
        parsing_widget = QtWidgets.QWidget()
        parsing_widget.setLayout(parsing_row)
        dataset_layout.addWidget(parsing_widget, 6, 1, 1, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Relative base:"), 7, 0)
        dataset_layout.addWidget(self.source_base_edit, 7, 1, 1, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("WSL staging:"), 8, 0)
        stage_row = QtWidgets.QHBoxLayout()
        stage_row.addWidget(self.stage_root_edit)
        stage_row.addWidget(self.stage_alias_edit)
        stage_widget = QtWidgets.QWidget()
        stage_widget.setLayout(stage_row)
        dataset_layout.addWidget(stage_widget, 8, 1, 1, 2)
        csv_buttons = QtWidgets.QHBoxLayout()
        csv_buttons.addStretch()
        csv_buttons.addWidget(self.csv_preview_btn)
        csv_buttons.addWidget(self.csv_load_btn)
        csv_buttons_widget = QtWidgets.QWidget()
        csv_buttons_widget.setLayout(csv_buttons)
        dataset_layout.addWidget(csv_buttons_widget, 9, 1, 1, 2)

        source_tab_layout.addWidget(dataset_box)
        source_tab_layout.addStretch()

        config_box = QtWidgets.QGroupBox("Annotation")
        config_layout = QtWidgets.QGridLayout(config_box)

        self.label_edit = QtWidgets.QLineEdit()
        self.label_edit.editingFinished.connect(self._on_label_changed)
        self.values_edit = QtWidgets.QLineEdit()
        self.values_edit.editingFinished.connect(self._rebuild_value_controls)

        self.auto_advance_cb = QtWidgets.QCheckBox("Auto-advance after annotate")
        self.keep_previous_cb = QtWidgets.QCheckBox("Keep previous loaded objects")
        self.prefer_gz_cb = QtWidgets.QCheckBox("Prefer .gz when both exist")
        self.skip_annotated_cb = QtWidgets.QCheckBox("Skip already-annotated files on load")
        self.preload_enabled_cb = QtWidgets.QCheckBox("Preload next structures silently")
        self.preload_ahead_spin = QtWidgets.QSpinBox()
        self.preload_ahead_spin.setRange(0, 20)
        self.preload_ahead_spin.setSingleStep(1)
        self.preload_interval_spin = QtWidgets.QSpinBox()
        self.preload_interval_spin.setRange(50, 5000)
        self.preload_interval_spin.setSingleStep(50)
        self.preload_interval_spin.setSuffix(" ms")
        self.bg_decompress_cb = QtWidgets.QCheckBox("Background decompress .gz (IO-only)")
        self.cache_enabled_cb = QtWidgets.QCheckBox(
            "Use persistent Windows-local cache for remote/.gz structures"
        )
        self.restore_last_cb = QtWidgets.QCheckBox(
            "Restore last dataset when this panel opens"
        )

        for checkbox in (
            self.auto_advance_cb,
            self.keep_previous_cb,
            self.prefer_gz_cb,
            self.skip_annotated_cb,
            self.preload_enabled_cb,
            self.bg_decompress_cb,
            self.cache_enabled_cb,
            self.restore_last_cb,
        ):
            checkbox.stateChanged.connect(self._schedule_state_save)
        self.preload_ahead_spin.valueChanged.connect(self._schedule_state_save)
        self.preload_interval_spin.valueChanged.connect(self._schedule_state_save)

        self.step_cmd_edit = QtWidgets.QPlainTextEdit()
        self.step_cmd_edit.setPlaceholderText("PyMOL commands; use {object}, {file}, {index}, {count}")
        self.step_cmd_edit.setMaximumBlockCount(200)
        self.step_cmd_edit.setFixedHeight(80)
        self.step_cmd_edit.textChanged.connect(self._schedule_state_save)

        config_layout.addWidget(QtWidgets.QLabel("Label name:"), 0, 0)
        config_layout.addWidget(self.label_edit, 0, 1)
        config_layout.addWidget(QtWidgets.QLabel("Values:"), 1, 0)
        config_layout.addWidget(self.values_edit, 1, 1)
        config_layout.addWidget(self.auto_advance_cb, 2, 1)
        config_layout.addWidget(self.keep_previous_cb, 3, 1)
        config_layout.addWidget(self.prefer_gz_cb, 4, 1)
        config_layout.addWidget(self.skip_annotated_cb, 5, 1)
        config_layout.addWidget(self.preload_enabled_cb, 6, 1)
        preload_row = QtWidgets.QHBoxLayout()
        preload_row.addWidget(QtWidgets.QLabel("Preload ahead:"))
        preload_row.addWidget(self.preload_ahead_spin)
        preload_row.addSpacing(12)
        preload_row.addWidget(QtWidgets.QLabel("Interval:"))
        preload_row.addWidget(self.preload_interval_spin)
        preload_row.addStretch()
        preload_row_widget = QtWidgets.QWidget()
        preload_row_widget.setLayout(preload_row)
        config_layout.addWidget(preload_row_widget, 7, 1)
        config_layout.addWidget(self.bg_decompress_cb, 8, 1)
        config_layout.addWidget(self.cache_enabled_cb, 9, 1)
        config_layout.addWidget(self.restore_last_cb, 10, 1)

        config_layout.addWidget(QtWidgets.QLabel("On step (after load):"), 11, 0)
        config_layout.addWidget(self.step_cmd_edit, 11, 1)

        annotation_tab_layout.addWidget(config_box)

        self.values_box = QtWidgets.QGroupBox("Buckets")
        self.values_layout = QtWidgets.QVBoxLayout(self.values_box)
        annotation_tab_layout.addWidget(self.values_box)
        annotation_tab_layout.addStretch()

        scores_box = QtWidgets.QGroupBox("CSV scores / metadata")
        scores_layout = QtWidgets.QVBoxLayout(scores_box)
        view_row = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter rows by any displayed/raw value")
        self.sort_column_combo = QtWidgets.QComboBox()
        self.sort_column_combo.setEditable(True)
        self.sort_desc_cb = QtWidgets.QCheckBox("Descending")
        self.apply_view_btn = QtWidgets.QPushButton("Apply view")
        self.apply_view_btn.clicked.connect(self._apply_view)
        view_row.addWidget(QtWidgets.QLabel("Filter:"))
        view_row.addWidget(self.filter_edit, 2)
        view_row.addWidget(QtWidgets.QLabel("Sort:"))
        view_row.addWidget(self.sort_column_combo, 1)
        view_row.addWidget(self.sort_desc_cb)
        view_row.addWidget(self.apply_view_btn)
        scores_layout.addLayout(view_row)

        tables_row = QtWidgets.QHBoxLayout()
        self.dataset_table = QtWidgets.QTableView()
        self._dataset_model = _DatasetTableModel(self.dataset_table)
        self.dataset_table.setModel(self._dataset_model)
        self.dataset_table.setEditTriggers(
            _qt_class_enum(
                QtWidgets.QAbstractItemView, "EditTrigger", "NoEditTriggers"
            )
        )
        try:
            select_rows = _qt_class_enum(
                QtWidgets.QAbstractItemView, "SelectionBehavior", "SelectRows"
            )
            single_selection = _qt_class_enum(
                QtWidgets.QAbstractItemView,
                "SelectionMode",
                "SingleSelection",
            )
            self.dataset_table.setSelectionBehavior(select_rows)
            self.dataset_table.setSelectionMode(single_selection)
        except Exception:
            pass
        self.dataset_table.clicked.connect(self._dataset_index_clicked)
        self.metadata_table = QtWidgets.QTableWidget(0, 2)
        self.metadata_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.metadata_table.setEditTriggers(
            _qt_class_enum(
                QtWidgets.QAbstractItemView, "EditTrigger", "NoEditTriggers"
            )
        )
        self.metadata_table.horizontalHeader().setStretchLastSection(True)
        tables_row.addWidget(self.dataset_table, 3)
        tables_row.addWidget(self.metadata_table, 2)
        scores_layout.addLayout(tables_row)
        scores_tab_layout.addWidget(scores_box, 1)

        nav_row = QtWidgets.QHBoxLayout()
        self.prev_btn = QtWidgets.QPushButton("Prev (Alt+Left)")
        self.next_btn = QtWidgets.QPushButton("Next (Alt+Right)")
        self.next_unann_btn = QtWidgets.QPushButton("Next Unannotated")
        self.undo_btn = QtWidgets.QPushButton("Undo (Alt+Z)")
        self.reload_btn = QtWidgets.QPushButton("Reload")

        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.next_unann_btn.clicked.connect(self._next_unannotated)
        self.undo_btn.clicked.connect(self._undo)
        self.reload_btn.clicked.connect(self._reload)

        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        nav_row.addWidget(self.next_unann_btn)
        nav_row.addWidget(self.undo_btn)
        nav_row.addWidget(self.reload_btn)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        goto_row = QtWidgets.QHBoxLayout()
        goto_row.addWidget(QtWidgets.QLabel("Go to #:"))
        self.goto_spin = QtWidgets.QSpinBox()
        self.goto_spin.setRange(1, 1)
        self.goto_btn = QtWidgets.QPushButton("Go")
        self.goto_btn.clicked.connect(self._goto)
        self.export_btn = QtWidgets.QPushButton("Export CSV…")
        self.export_btn.clicked.connect(self._export_csv)
        goto_row.addWidget(self.goto_spin)
        goto_row.addWidget(self.goto_btn)
        goto_row.addSpacing(16)
        goto_row.addWidget(self.export_btn)
        goto_row.addStretch()
        layout.addLayout(goto_row)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        footer_row = QtWidgets.QHBoxLayout()
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply_selected_value)
        self.value_combo = QtWidgets.QComboBox()
        footer_row.addWidget(QtWidgets.QLabel("Value:"))
        footer_row.addWidget(self.value_combo)
        footer_row.addWidget(self.apply_btn)
        footer_row.addStretch()
        layout.addLayout(footer_row)

    def _install_history_completers(self) -> None:
        self._history_bindings = [
            _HistoryCompletionBinding(self.dir_edit, "dataset_dir"),
            _HistoryCompletionBinding(self.csv_edit, "csv_path"),
            _HistoryCompletionBinding(self.path_column_edit, "path_column"),
            _HistoryCompletionBinding(self.id_column_edit, "id_column"),
            _HistoryCompletionBinding(
                self.mapping_profile_combo.lineEdit(), "mapping_profile"
            ),
            _HistoryCompletionBinding(self.pattern_edit, "pattern"),
            _HistoryCompletionBinding(self.label_edit, "label_name"),
            _HistoryCompletionBinding(self.values_edit, "values_text"),
            _HistoryCompletionBinding(self.step_cmd_edit, "step_cmd", multiline=True),
        ]

    def _browse_dir(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Dataset Directory", self.dir_edit.text() or "."
        )
        if directory:
            self.dir_edit.setText(directory)

    def _browse_csv(self) -> None:
        filename, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Structure Score CSV",
            self.csv_edit.text() or STATE.dataset_dir or ".",
            "Delimited tables (*.csv *.tsv *.txt);;All files (*)",
        )
        if filename:
            self.csv_edit.setText(filename)
            self._set_csv_source_widgets(filename)

    def _set_csv_source_widgets(self, csv_path: str) -> None:
        """Populate source-specific controls from this CSV, never the old CSV."""

        persisted = _read_persisted_dataset_state(str(csv_path), "csv")
        saved_csv = dict(persisted.get("csv") or {}) if persisted else {}
        saved_view = dict(persisted.get("view") or {}) if persisted else {}
        view_columns = saved_view.get("display_columns")
        columns = saved_csv.get("display_columns", view_columns)
        if not isinstance(columns, (list, tuple)):
            columns = []
        self.path_column_edit.setText(
            str(saved_csv.get("path_column") or "structure_path")
        )
        self.id_column_edit.setText(str(saved_csv.get("id_column") or ""))
        self.display_columns_edit.setText(";".join(str(value) for value in columns))
        self.encoding_combo.setCurrentText(
            str(saved_csv.get("encoding") or DEFAULT_ENCODING)
        )
        self.delimiter_combo.setCurrentText(
            str(saved_csv.get("delimiter") or AUTO_DELIMITER)
        )
        self.mapping_profile_combo.setCurrentText(
            str(
                (persisted or {}).get("mapping_profile")
                or STATE.mapping_profile
                or DEFAULT_PROFILE_NAME
            )
        )
        self.source_base_edit.setText(str(saved_csv.get("source_base") or ""))
        self.stage_root_edit.setText(str(saved_csv.get("stage_root") or ""))
        self.stage_alias_edit.setText(str(saved_csv.get("stage_alias") or ""))

    def _source_mode(self) -> str:
        return str(self.source_mode_combo.currentData() or "directory")

    def _source_mode_changed(self, *args) -> None:
        csv_mode = self._source_mode() == "csv"
        busy = bool(self._csv_task_id)
        self.source_mode_combo.setEnabled(not busy)
        for widget in (
            self.csv_edit, self.csv_browse, self.path_column_edit,
            self.id_column_edit, self.display_columns_edit, self.encoding_combo,
            self.delimiter_combo, self.mapping_profile_combo, self.source_base_edit,
            self.stage_root_edit, self.stage_alias_edit,
        ):
            widget.setEnabled(csv_mode and not busy)
        for widget in (self.dir_edit, self.dir_browse, self.pattern_edit, self.load_btn):
            widget.setEnabled(not csv_mode and not busy)
        self.csv_preview_btn.setText("Preview mapping")
        self.csv_load_btn.setText("Load CSV")
        self.csv_preview_btn.setEnabled(csv_mode and not busy)
        self.csv_load_btn.setEnabled(csv_mode and not busy)
        if busy and self._csv_task_kind == "preview":
            self.csv_preview_btn.setText("Cancel preview")
            self.csv_preview_btn.setEnabled(True)
        elif busy and self._csv_task_kind == "load":
            self.csv_load_btn.setText("Cancel load")
            self.csv_load_btn.setEnabled(True)
        elif busy and self._csv_task_kind == "commit":
            self.csv_load_btn.setText("Committing…")

    def _csv_arguments(self) -> Dict[str, str]:
        return {
            "csv_path": self.csv_edit.text().strip(),
            "path_column": self.path_column_edit.text(),
            "id_column": self.id_column_edit.text(),
            "display_columns": self.display_columns_edit.text(),
            "encoding": self.encoding_combo.currentText().strip(),
            "delimiter": self.delimiter_combo.currentText().strip(),
            "mapping_profile": self.mapping_profile_combo.currentText().strip(),
            "source_base": self.source_base_edit.text().strip(),
            "stage_root": self.stage_root_edit.text().strip(),
            "stage_alias": self.stage_alias_edit.text().strip(),
        }

    @staticmethod
    def _csv_arguments_fingerprint(
        arguments: Mapping[str, object]
    ) -> Tuple[Tuple[str, str], ...]:
        return tuple(
            sorted((str(key), str(value)) for key, value in arguments.items())
        )

    def _annotation_ui_fingerprint(self) -> Tuple[object, ...]:
        """Capture editable config which a prepared load must not overwrite."""

        return (
            self.label_edit.text(),
            self.values_edit.text(),
            bool(self.auto_advance_cb.isChecked()),
            bool(self.keep_previous_cb.isChecked()),
            bool(self.prefer_gz_cb.isChecked()),
            bool(self.skip_annotated_cb.isChecked()),
            bool(self.preload_enabled_cb.isChecked()),
            int(self.preload_ahead_spin.value()),
            int(self.preload_interval_spin.value()),
            bool(self.bg_decompress_cb.isChecked()),
            bool(self.cache_enabled_cb.isChecked()),
            bool(self.restore_last_cb.isChecked()),
            self.step_cmd_edit.toPlainText(),
            tuple(
                sorted(
                    (str(value), edit.text())
                    for value, edit in self._bucket_inputs.items()
                )
            ),
        )

    def _restore_deferred_dataset(self) -> None:
        if self._closed or STATE.files or not STATE.restore_last_dataset:
            return
        if self._source_mode() == "csv":
            # Match the historical synchronous restore contract: parser fields
            # present in global history stay explicit, while fields stored only
            # in the per-CSV state remain tri-state None and are resolved by the
            # worker.  Reading that sidecar on the GUI thread would reintroduce
            # the pre-show remote-filesystem stall this path is designed to fix.
            ui_arguments = self._csv_arguments()
            worker_arguments: Dict[str, object] = dict(ui_arguments)
            worker_arguments["display_columns"] = list(STATE.display_columns)
            for name in (
                "encoding", "delimiter", "source_base", "stage_root", "stage_alias"
            ):
                worker_arguments[name] = None
            self._start_csv_task(
                "load",
                _prepare_csv_load,
                ui_arguments,
                runtime=_capture_csv_runtime_context(),
                worker_arguments=worker_arguments,
            )
        else:
            self._load_dataset()

    def _start_csv_task(
        self,
        kind: str,
        worker,
        arguments: Mapping[str, str],
        *,
        runtime: Optional[_CsvRuntimeContext] = None,
        worker_arguments: Optional[Mapping[str, object]] = None,
    ) -> None:
        if self._csv_task_id:
            return
        if not str(arguments.get("csv_path", "")).strip():
            self.status_label.setText("CSV path is required.")
            return
        self._csv_task_epoch += 1
        fingerprint = self._csv_arguments_fingerprint(arguments)
        config_fingerprint = (
            self._annotation_ui_fingerprint() if kind == "load" else ()
        )
        generation = int(STATE.dataset_generation)
        kwargs: Dict[str, object] = dict(worker_arguments or arguments)
        if runtime is not None:
            kwargs["runtime"] = runtime
        try:
            job_id = _submit_csv_async_job(
                self,
                kind,
                worker,
                (),
                kwargs,
                epoch=self._csv_task_epoch,
                arguments_fingerprint=fingerprint,
                config_fingerprint=config_fingerprint,
                dataset_generation=generation,
                annotation_generation=int(STATE.annotation_generation),
            )
        except Exception as exc:
            self.status_label.setText(f"CSV {kind} could not start: {exc}")
            traceback.print_exc()
            return
        self._csv_task_id = job_id
        self._csv_task_kind = str(kind)
        self._csv_task_arguments_fingerprint = fingerprint
        self._csv_task_dataset_generation = generation
        self._source_mode_changed()
        self.status_label.setText(
            f"CSV {kind} preparation is running in the background…"
        )

    def _clear_csv_task(self) -> None:
        self._csv_task_id = ""
        self._csv_task_kind = ""
        self._csv_task_arguments_fingerprint = ()
        self._csv_task_dataset_generation = -1
        if not self._closed:
            self._source_mode_changed()

    def _cancel_csv_task(self, *, announce: bool = True) -> None:
        job_id = self._csv_task_id
        if not job_id or self._csv_task_kind == "commit":
            return
        _cancel_csv_async_job(job_id)
        self._csv_task_epoch += 1
        self._clear_csv_task()
        if announce and not self._closed:
            self.status_label.setText(
                "CSV cancellation requested; any in-progress filesystem/HTTP "
                "read may finish, but its result will be ignored."
            )

    def _complete_csv_task(self, job_id: str, job, result, error) -> None:
        app = QtCore.QCoreApplication.instance()
        try:
            self._last_csv_completion_on_main_thread = bool(
                app is not None
                and QtCore.QThread.currentThread() == app.thread()
            )
        except Exception:
            self._last_csv_completion_on_main_thread = None
        if (
            self._closed
            or job_id != self._csv_task_id
            or int(job.get("epoch", -1)) != self._csv_task_epoch
        ):
            return
        kind = self._csv_task_kind
        current_fingerprint = self._csv_arguments_fingerprint(
            self._csv_arguments()
        )
        stale_reason = ""
        if current_fingerprint != tuple(job.get("arguments_fingerprint") or ()):
            stale_reason = "source arguments changed"
        elif (
            kind == "load"
            and self._annotation_ui_fingerprint()
            != tuple(job.get("config_fingerprint") or ())
        ):
            stale_reason = "annotation configuration changed"
        elif int(STATE.dataset_generation) != int(
            job.get("dataset_generation", -1)
        ):
            stale_reason = "the active dataset changed"
        elif (
            kind == "load"
            and int(STATE.annotation_generation)
            != int(job.get("annotation_generation", -1))
        ):
            stale_reason = "annotations changed"
        if stale_reason:
            self._clear_csv_task()
            self.status_label.setText(
                f"Discarded stale CSV {kind} result because {stale_reason}; retry."
            )
            return
        if error is not None:
            self._clear_csv_task()
            if isinstance(error, _CsvPreparationCancelled):
                self.status_label.setText("CSV preparation cancelled.")
                return
            self.status_label.setText(f"CSV {kind} failed: {error}")
            traceback.print_exception(
                type(error), error, getattr(error, "__traceback__", None)
            )
            return
        if kind == "preview":
            if not isinstance(result, _PreparedCsvPreview):
                self._clear_csv_task()
                self.status_label.setText("CSV preview returned an invalid result.")
                return
            self._clear_csv_task()
            payload = result.payload
            self._populate_preview_table(payload)
            self.status_label.setText(
                f"CSV preview: {payload.get('row_count', 0)} rows; "
                f"{payload.get('status_counts', {})}"
            )
            return
        if kind != "load" or not isinstance(result, _PreparedCsvLoad):
            self._clear_csv_task()
            self.status_label.setText("CSV load returned an invalid result.")
            return
        self._csv_task_kind = "commit"
        self._source_mode_changed()
        self.status_label.setText("CSV prepared; committing to PyMOL…")
        try:
            _commit_prepared_csv_load(result)
        except Exception as exc:
            self._clear_csv_task()
            self.status_label.setText(f"CSV load commit failed: {exc}")
            traceback.print_exc()
            return
        self._clear_csv_task()
        self._sync_from_state()
        self._rebuild_value_controls(sync_to_state=False)
        self._refresh_status()

    def _preview_csv(self) -> None:
        if self._csv_task_id:
            if self._csv_task_kind == "preview":
                self._cancel_csv_task()
            return
        arguments = self._csv_arguments()
        self._start_csv_task("preview", _prepare_csv_preview, arguments)

    def _load_csv_dataset(self) -> None:
        if self._csv_task_id:
            if self._csv_task_kind == "load":
                self._cancel_csv_task()
            return
        self._sync_to_state(update_directory=False, persist=False)
        arguments = self._csv_arguments()
        self._start_csv_task(
            "load",
            _prepare_csv_load,
            arguments,
            runtime=_capture_csv_runtime_context(),
        )

    @staticmethod
    def _user_role():
        role = getattr(QtCore.Qt, "UserRole", None)
        if role is not None:
            return role
        return QtCore.Qt.ItemDataRole.UserRole

    @staticmethod
    def _current_table_identity() -> Tuple[str, Tuple[object, ...]]:
        source_reference = _active_source_reference()
        source_token = (
            _path_identity(source_reference) if source_reference else ""
        )
        label_key = normalize_label_key(STATE.label_name)
        signature: Tuple[object, ...] = (
            source_token,
            int(STATE.dataset_generation),
            STATE.source_mode,
            tuple(STATE.display_columns),
            label_key if STATE.source_mode == "csv" else "",
        )
        return source_token, signature

    def _populate_preview_table(self, payload: Mapping[str, object]) -> None:
        rows = list(payload.get("rows") or [])
        self._table_mode = "preview"
        self._table_source_token = _path_identity(str(payload.get("csv_path") or "."))
        self._dataset_table_signature = None
        self._table_annotation_revision = None
        self._entry_id_to_active_index = {}
        self._entries_by_id = {}
        self._preview_rows_by_id = {
            str(dict(row).get("entry_id", "")): dict(row) for row in rows
        }
        display_columns = list(payload.get("display_columns") or [])
        self._dataset_model.set_preview(rows, display_columns)

    def _apply_view(self) -> None:
        sa_filter(
            self.filter_edit.text(),
            self.sort_column_combo.currentText(),
            int(self.sort_desc_cb.isChecked()),
        )
        self._refresh_status()

    def _dataset_index_clicked(self, index) -> None:
        if index is not None and index.isValid():
            self._dataset_row_clicked(index.row(), index.column())

    def _dataset_row_clicked(self, row: int, column: int) -> None:
        entry_id = self._dataset_model.entry_id_at(row)
        if not entry_id:
            return
        if self._table_mode == "preview":
            preview = self._preview_rows_by_id.get(entry_id, {})
            metadata = dict(preview.get("metadata") or {})
            rows = [
                ("entry_id", entry_id),
                ("row_number", preview.get("row_number", "")),
                ("raw_path", preview.get("raw_path", "")),
                ("resolved_path", preview.get("resolved_path", "")),
                ("status", preview.get("status", "")),
                ("provider", preview.get("provider", "")),
                ("matched_rule", preview.get("matched_rule", "")),
                ("error", preview.get("error", "")),
            ]
            rows.extend(metadata.items())
            self.metadata_table.setRowCount(len(rows))
            for preview_row, (name, value) in enumerate(rows):
                self.metadata_table.setItem(
                    preview_row, 0, QtWidgets.QTableWidgetItem(str(name))
                )
                self.metadata_table.setItem(
                    preview_row,
                    1,
                    QtWidgets.QTableWidgetItem(
                        str(value if value is not None else "")
                    ),
                )
            self.status_label.setText(
                "CSV preview row; load this CSV before navigating it."
            )
            return
        active_token, active_signature = self._current_table_identity()
        if (
            self._table_source_token != active_token
            or self._dataset_table_signature != active_signature
        ):
            if not STATE.files:
                self.status_label.setText(
                    "The dataset is no longer active; load or refresh a source."
                )
                return
            try:
                self._refresh_dataset_table()
            except Exception as exc:
                self.status_label.setText(
                    f"Dataset table refresh failed: {exc}"
                )
                return
            self.status_label.setText(
                "Dataset changed after this row was drawn; the table was refreshed. "
                "Click the intended row again."
            )
            return
        index = self._entry_id_to_active_index.get(entry_id)
        if index is not None:
            self._sync_to_state()
            sa_goto(index + 1)
            self._refresh_status()
            return
        entry = self._entries_by_id.get(entry_id)
        if entry is not None:
            if entry.is_navigable and not entry.error:
                message = "excluded by the current filter; clear or change the filter"
            else:
                message = entry.error or entry.status
            self.status_label.setText(
                f"Row {entry.row_number or entry.ordinal + 1} is not active: "
                f"{message}"
            )

    def _schedule_state_save(self, *args) -> None:
        self._state_save_timer.start(300)

    def _save_ui_state(self, *args) -> None:
        self._sync_to_state()

    def _sync_to_state(
        self, update_directory: bool = True, persist: bool = True
    ) -> None:
        if update_directory:
            requested_dir = self.dir_edit.text().strip() or "."
            active_dir = _active_dataset_directory()
            if not active_dir or _path_identity(active_dir) == _path_identity(
                requested_dir
            ):
                STATE.dataset_dir = requested_dir
        requested_csv = self.csv_edit.text().strip()
        if STATE.source_mode == "csv" and requested_csv:
            try:
                same_csv = _path_identity(requested_csv) == _path_identity(STATE.csv_path)
            except Exception:
                same_csv = False
            if same_csv:
                STATE.path_column = self.path_column_edit.text()
                STATE.id_column = self.id_column_edit.text()
                try:
                    columns = _display_column_names(self.display_columns_edit.text())
                    if columns is not None:
                        STATE.display_columns = columns
                except Exception:
                    pass
                STATE.csv_encoding = self.encoding_combo.currentText().strip()
                STATE.csv_delimiter = self.delimiter_combo.currentText().strip()
                STATE.mapping_profile = self.mapping_profile_combo.currentText().strip()
                STATE.source_base = self.source_base_edit.text().strip()
                STATE.stage_root = self.stage_root_edit.text().strip()
                STATE.stage_alias = self.stage_alias_edit.text().strip()
        STATE.pattern = self.pattern_edit.text().strip() or STRUCTURE_PATTERN
        STATE.label_name = self.label_edit.text().strip() or "quality"
        STATE.values_text = self.values_edit.text().strip() or "Good,Bad"
        STATE.auto_advance = bool(self.auto_advance_cb.isChecked())
        STATE.keep_previous = bool(self.keep_previous_cb.isChecked())
        STATE.prefer_gz = bool(self.prefer_gz_cb.isChecked())
        STATE.skip_annotated = bool(self.skip_annotated_cb.isChecked())
        STATE.preload_enabled = bool(self.preload_enabled_cb.isChecked())
        STATE.preload_ahead = int(self.preload_ahead_spin.value())
        STATE.preload_interval_ms = int(self.preload_interval_spin.value())
        STATE.background_decompress_gz = bool(self.bg_decompress_cb.isChecked())
        STATE.cache_enabled = bool(self.cache_enabled_cb.isChecked())
        STATE.restore_last_dataset = bool(self.restore_last_cb.isChecked())
        STATE.step_cmd = self.step_cmd_edit.toPlainText().strip() or ""
        for value, edit in self._bucket_inputs.items():
            STATE.bucket_cmds[value] = edit.text()
        if persist:
            _save_current_dataset_state()

    def _sync_from_state(self) -> None:
        mode_index = self.source_mode_combo.findData(STATE.source_mode)
        self.source_mode_combo.setCurrentIndex(max(0, mode_index))
        self.dir_edit.setText(STATE.dataset_dir or ".")
        self.csv_edit.setText(STATE.csv_path or "")
        self.path_column_edit.setText(STATE.path_column or "structure_path")
        self.id_column_edit.setText(STATE.id_column or "")
        self.display_columns_edit.setText(";".join(STATE.display_columns))
        self.encoding_combo.setCurrentText(STATE.csv_encoding or DEFAULT_ENCODING)
        self.delimiter_combo.setCurrentText(STATE.csv_delimiter or AUTO_DELIMITER)
        self.mapping_profile_combo.setCurrentText(
            STATE.mapping_profile or DEFAULT_PROFILE_NAME
        )
        self.source_base_edit.setText(STATE.source_base or "")
        self.stage_root_edit.setText(STATE.stage_root or "")
        self.stage_alias_edit.setText(STATE.stage_alias or "")
        self.filter_edit.setText(STATE.filter_text or "")
        self.sort_column_combo.setCurrentText(STATE.sort_column or "")
        self.sort_desc_cb.setChecked(bool(STATE.sort_descending))
        self.pattern_edit.setText(STATE.pattern or STRUCTURE_PATTERN)
        self.label_edit.setText(STATE.label_name or "quality")
        self.values_edit.setText(STATE.values_text or "Good,Bad")
        self.auto_advance_cb.setChecked(bool(STATE.auto_advance))
        self.keep_previous_cb.setChecked(bool(STATE.keep_previous))
        self.prefer_gz_cb.setChecked(bool(STATE.prefer_gz))
        self.skip_annotated_cb.setChecked(bool(STATE.skip_annotated))
        self.preload_enabled_cb.setChecked(bool(STATE.preload_enabled))
        self.preload_ahead_spin.setValue(int(STATE.preload_ahead or 0))
        self.preload_interval_spin.setValue(int(STATE.preload_interval_ms or 250))
        self.bg_decompress_cb.setChecked(bool(STATE.background_decompress_gz))
        self.cache_enabled_cb.setChecked(bool(STATE.cache_enabled))
        self.restore_last_cb.setChecked(bool(STATE.restore_last_dataset))
        self.step_cmd_edit.setPlainText(STATE.step_cmd or "")
        self._source_mode_changed()

    def _load_dataset(self) -> None:
        if self._source_mode() == "csv":
            self._load_csv_dataset()
            return
        target_dir = self.dir_edit.text().strip() or "."
        requested_pattern = self.pattern_edit.text().strip() or STRUCTURE_PATTERN
        active_before = _active_dataset_directory()
        loading_different = not active_before or (
            _path_identity(active_before) != _path_identity(target_dir)
        )
        switching = bool(active_before) and loading_different
        self._sync_to_state(update_directory=False, persist=not switching)
        saved_path = dataset_state_path(target_dir)
        has_saved_state = os.path.isfile(_long_path(saved_path))
        pattern_override = (
            "" if has_saved_state and loading_different else requested_pattern
        )
        sa_load_dir(target_dir, pattern_override)
        active_after = _active_dataset_directory()
        if active_after and _path_identity(active_after) == _path_identity(target_dir):
            self._sync_from_state()
            self._rebuild_value_controls(sync_to_state=False)
        self._refresh_status()

    def _next(self) -> None:
        self._sync_to_state()
        sa_next()
        self._refresh_status()

    def _prev(self) -> None:
        self._sync_to_state()
        sa_prev()
        self._refresh_status()

    def _reload(self) -> None:
        self._sync_to_state()
        # If there's a mismatch, sync STATE to match PyMOL's actual enabled object
        if STATE.current_object and not _verify_current_object_enabled():
            actual_enabled = _get_enabled_managed_object()
            if actual_enabled and actual_enabled != STATE.current_object:
                actual_idx = _get_index_for_object(actual_enabled)
                if actual_idx is not None and 0 <= actual_idx < len(STATE.files):
                    print(f"Structure Annotator: syncing to PyMOL state (index {actual_idx})")
                    _set_annotation_index(actual_idx)
        _load_current_structure()
        self._refresh_status()

    def _apply_selected_value(self) -> None:
        self._sync_to_state()
        value = self.value_combo.currentText().strip()
        if not value:
            return
        sa_annotate(value)
        self._refresh_status()

    def _next_unannotated(self) -> None:
        self._sync_to_state()
        sa_next_unannotated()
        self._refresh_status()

    def _undo(self) -> None:
        self._sync_to_state()
        sa_undo()
        self._refresh_status()

    def _goto(self) -> None:
        self._sync_to_state()
        sa_goto(int(self.goto_spin.value()))
        self._refresh_status()

    def _export_csv(self) -> None:
        self._sync_to_state()
        label_key = normalize_label_key(STATE.label_name)
        if STATE.source_mode == "csv" and STATE.csv_path:
            stem = os.path.splitext(os.path.basename(STATE.csv_path))[0]
            default = os.path.join(
                STATE.dataset_dir or ".", f"{stem}_annotated_{label_key}.csv"
            )
        else:
            default = os.path.join(
                STATE.dataset_dir or ".", f"annotations_{label_key}.csv"
            )
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Annotations", default, "CSV Files (*.csv)"
        )
        if not out_path:
            return
        written = sa_export_csv(out_path)
        if written:
            self.status_label.setText(f"Exported annotations to {written}")

    def _on_label_changed(self) -> None:
        # Rescan progress against the new label so counts/highlighting stay accurate.
        self._sync_to_state()
        if STATE.files:
            _rescan_annotated_indices()
            STATE.undo_history.clear()
        self._refresh_status()

    def _rebuild_value_controls(self, sync_to_state: bool = True) -> None:
        if sync_to_state:
            self._sync_to_state()
        values = STATE.values()

        for binding in self._bucket_history_bindings:
            binding.detach()
        self._bucket_history_bindings = []

        while self.values_layout.count():
            item = self.values_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._bucket_inputs = {}
        self._value_buttons = []

        if len(values) < 5:
            btn_row = QtWidgets.QHBoxLayout()
            for i, value in enumerate(values):
                hint = f" (Alt+{i + 1})" if i < 9 else ""
                btn = QtWidgets.QPushButton(f"{value}{hint}")
                btn.clicked.connect(lambda checked=False, v=value: self._annotate(v))
                self._value_buttons.append(btn)
                btn_row.addWidget(btn)
            btn_row.addStretch()
            btn_widget = QtWidgets.QWidget()
            btn_widget.setLayout(btn_row)
            self.values_layout.addWidget(btn_widget)

        form = QtWidgets.QFormLayout()
        for value in values:
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText("On bucket cmd (optional); e.g. set_name {object}, good_{object}")
            edit.setText(STATE.bucket_cmds.get(value, ""))
            edit.editingFinished.connect(self._save_ui_state)
            self._bucket_history_bindings.append(
                _HistoryCompletionBinding(edit, "bucket_cmd")
            )
            self._bucket_inputs[value] = edit
            form.addRow(f"{value} cmd:", edit)

        form_widget = QtWidgets.QWidget()
        form_widget.setLayout(form)
        self.values_layout.addWidget(form_widget)

        self.value_combo.clear()
        self.value_combo.addItems(values)

    def _annotate(self, value: str) -> None:
        self._sync_to_state()
        sa_annotate(value)
        self._refresh_status()

    def _refresh_dataset_table(self) -> None:
        entries = STATE.all_entries or STATE.entries
        display_columns = list(STATE.display_columns)
        label_key = normalize_label_key(STATE.label_name)
        source_token, signature = self._current_table_identity()
        store = (
            STATE.row_annotation_store
            if STATE.source_mode == "csv"
            else None
        )
        annotation_revision = store.revision() if store is not None else None
        reset_model = not (
            self._table_mode == "active"
            and source_token == self._table_source_token
            and signature == self._dataset_table_signature
        )
        annotations_changed = annotation_revision != self._table_annotation_revision
        annotation_values: Dict[str, str] = {}
        if store is not None and (reset_model or annotations_changed):
            annotation_values = store.snapshot_for_label(label_key)

        if reset_model:
            self._table_mode = "active"
            self._table_source_token = source_token
            self._preview_rows_by_id = {}
            self._dataset_table_signature = signature
            self._entry_id_to_active_index = {
                entry.id: index for index, entry in enumerate(STATE.entries)
            }
            self._entries_by_id = {entry.id: entry for entry in entries}
            self._dataset_model.set_active(
                entries,
                display_columns,
                STATE.source_mode,
                label_key,
                annotation_values,
            )
            selected_sort = self.sort_column_combo.currentText()
            self.sort_column_combo.blockSignals(True)
            self.sort_column_combo.clear()
            self.sort_column_combo.addItem("")
            self.sort_column_combo.addItems(display_columns)
            self.sort_column_combo.setCurrentText(
                selected_sort or STATE.sort_column
            )
            self.sort_column_combo.blockSignals(False)
        elif annotations_changed:
            self._dataset_model.update_annotations(annotation_values)

        self._table_annotation_revision = annotation_revision
        current_row = self._dataset_model.row_for_entry(STATE.current_entry_id())
        if current_row >= 0:
            self.dataset_table.selectRow(current_row)

    def _refresh_metadata_table(self) -> None:
        entry = STATE.current_entry()
        rows: List[Tuple[str, object]] = []
        if entry is not None:
            resolution = entry.path_resolution
            materialized = STATE.materialized_paths.get(entry.id)
            rows.extend(
                [
                    ("row_number", entry.row_number or entry.ordinal + 1),
                    ("entry_id", entry.id),
                    ("raw_path", entry.source_path),
                    ("resolved_path", entry.resolved_path),
                    ("status", entry.status),
                    ("provider", _entry_provider(entry)),
                    ("matched_rule", getattr(resolution, "matched_rule", "")),
                    ("materialized_path", materialized.path if materialized else ""),
                    ("cache_hit", materialized.cache_hit if materialized else ""),
                    ("error", entry.error or STATE.materialization_errors.get(entry.id, "")),
                ]
            )
            rows.extend((name, value) for name, value in entry.metadata.items())
        self.metadata_table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self.metadata_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(name)))
            self.metadata_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(str(value if value is not None else ""))
            )

    def _refresh_status(self) -> None:
        if not STATE.files:
            self.status_label.setText("No dataset loaded.")
            self.goto_spin.setRange(1, 1)
            self.undo_btn.setEnabled(False)
            self._dataset_model.clear_rows()
            self.metadata_table.setRowCount(0)
            self._dataset_table_signature = None
            self._table_annotation_revision = None
            self._table_mode = "active"
            self._table_source_token = ""
            self._preview_rows_by_id = {}
            self._entry_id_to_active_index = {}
            self._entries_by_id = {}
            return

        _ensure_navigation_session()

        # Keep the go-to control in range with the loaded dataset.
        self.goto_spin.setRange(1, len(STATE.files))
        self.goto_spin.blockSignals(True)
        self.goto_spin.setValue(STATE.index + 1)
        self.goto_spin.blockSignals(False)
        self.undo_btn.setEnabled(bool(STATE.undo_history))

        path = STATE.current_file() or ""
        entry = STATE.current_entry()
        basename = entry.display_name if entry is not None else os.path.basename(path)
        annotated = len(STATE.annotated_indices)
        total = len(STATE.files)
        remaining = total - annotated
        status_text = (
            f"{STATE.index + 1}/{total}: {basename} [{STATE.source_mode}]\n"
            f"Progress: {annotated} annotated, {remaining} remaining "
            f"(label '{normalize_label_key(STATE.label_name)}')"
        )

        # Show existing annotations for current file, emphasizing the active label.
        label_key = normalize_label_key(STATE.label_name)
        current_value = None
        try:
            annotations = _annotations_for_index(STATE.index)
            current_value = annotations.get(label_key)
            if annotations:
                anno_str = ", ".join(f"{k}={v}" for k, v in annotations.items())
                status_text += f"\nAnnotations: {anno_str}"
            else:
                status_text += "\nAnnotations: (none)"
        except Exception:
            pass

        if current_value:
            status_text += f"\nThis file: {label_key} = {current_value}"

        status_text += f"\n{path}"
        if entry is not None and entry.source_path != path:
            status_text += f"\nCSV source: {entry.source_path}"

        # Highlight the bucket button matching the current file's annotation.
        for btn in self._value_buttons:
            label = btn.text().split(" (")[0]
            if current_value is not None and label == current_value:
                btn.setStyleSheet("font-weight: bold; background-color: #cdebc5;")
            else:
                btn.setStyleSheet("")

        # Check for mismatch between widget state and PyMOL state
        if STATE.current_object:
            if not _verify_current_object_enabled():
                actual_enabled = _get_enabled_managed_object()
                if actual_enabled and actual_enabled != STATE.current_object:
                    actual_idx = _get_index_for_object(actual_enabled)
                    status_text += f"\n\nWARNING: PyMOL shows different object!"
                    status_text += f"\nWidget: {STATE.current_object}"
                    status_text += f"\nPyMOL:  {actual_enabled} (idx {actual_idx})"
                    status_text += f"\nClick Reload to sync."

        self.status_label.setText(status_text)
        self._refresh_dataset_table()
        self._refresh_metadata_table()


def show_structure_annotator_panel() -> QtWidgets.QDialog:
    global _panel
    if _panel is not None:
        _panel.raise_()
        _panel.activateWindow()
        return _panel
    _panel = StructureAnnotatorPanel(parent=QtWidgets.QApplication.activeWindow())
    sa_bind_keys()
    _panel.show()
    return _panel


def __init_plugin__(app=None) -> None:
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("Structure Annotator", show_structure_annotator_panel)


extend_command(cmd, "sa_load_dir", sa_load_dir)
extend_command(cmd, "sa_csv_preview", sa_csv_preview)
extend_command(cmd, "sa_load_csv", sa_load_csv)
extend_command(cmd, "sa_next", sa_next)
extend_command(cmd, "sa_prev", sa_prev)
extend_command(cmd, "sa_annotate", sa_annotate)
extend_command(cmd, "sa_undo", sa_undo)
extend_command(cmd, "sa_goto", sa_goto)
extend_command(cmd, "sa_next_unannotated", sa_next_unannotated)
extend_command(cmd, "sa_export_csv", sa_export_csv)
extend_command(cmd, "sa_save_state", sa_save_state)
extend_command(cmd, "sa_state_status", sa_state_status)
extend_command(cmd, "sa_history_search", sa_history_search)
extend_command(cmd, "sa_history_status", sa_history_status)
extend_command(cmd, "sa_cache_status", sa_cache_status)
extend_command(cmd, "sa_cache_clear", sa_cache_clear)
extend_command(cmd, "sa_mapping_list", sa_mapping_list)
extend_command(cmd, "sa_mapping_add", sa_mapping_add)
extend_command(cmd, "sa_mapping_remove", sa_mapping_remove)
extend_command(cmd, "sa_filter", sa_filter)
extend_command(cmd, "sa_bind_keys", sa_bind_keys)
extend_command(cmd, "sa_unbind_keys", sa_unbind_keys)
extend_command(cmd, "show_structure_annotator_panel", show_structure_annotator_panel)
