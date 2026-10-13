from pymol import cmd
from pymol.Qt import QtCore, QtWidgets
import copy
import datetime
import gzip
import hashlib
import html
import json
import math
import ntpath
import os
import posixpath
import re
import shutil
import tempfile
import threading
import uuid
import weakref
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field, fields, replace
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple
import traceback
import sys
from pymolplugins.annotation.filtering import compile_filter
from pymolplugins.annotation.persistence import pending_errors, submit_write
from pymolplugins.annotation.sources.csv_source import CsvReadCancelled, read_csv_header
from pymolplugins.annotation.review_config import default_fields, decode_value, field_key, missing_required, validate_fields, validate_score_config
from pymolplugins.annotation.review_actions import save_annotations, undo_annotations
from pymolplugins.annotation.score_summary import prepare_score_summary
from pymolplugins.annotation.command_templates import format_commands
from pymolplugins.annotation.review_file import (
    REVIEW_KEYS, check_review_unchanged, read_review_file, review_paths,
    validate_review_columns, write_review_override,
)
from pymolplugins.annotation.path_formats import rewrite_path
from pymolplugins.targets.commands import _execute_checked
from pymolplugins.remote.ssh import (
    SshCancelled, SshClient, SshLocation, is_ssh_path, normalize_source_path,
    parse_ssh_path, ssh_progress,
)

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
        quarantine_invalid_dataset_state,
        read_dataset_state,
        write_dataset_state,
    )
    from pymolplugins.annotation.annotation_store import (
        RowAnnotationStore,
        annotation_sidecar_path,
        directory_annotation_sidecar_path,
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
        SourceChangedError,
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
        CsvSourceError,
        directory_entry_id,
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
        quarantine_invalid_dataset_state,
        read_dataset_state,
        write_dataset_state,
    )
    from pymolplugins.annotation.annotation_store import (  # type: ignore
        RowAnnotationStore,
        annotation_sidecar_path,
        directory_annotation_sidecar_path,
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
        SourceChangedError,
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
        CsvSourceError,
        directory_entry_id,
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
REVIEW_STATUS_LABEL = "__pymol_review_status__"
REVIEW_STATUS_LATER = "later"


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


def _iter_text_file_lines(path: str) -> Iterator[str]:
    """Yield decoded structure lines without retaining the complete file."""

    resolved = _long_path(path)
    opener = gzip.open if resolved.lower().endswith(".gz") else open
    with opener(
        resolved,
        "rt",
        encoding="utf-8",
        errors="replace",
        newline="",
    ) as handle:
        yield from handle


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
    pattern = re.compile(rf"^{re.escape(REMARK_PREFIX)}\s+([^=\s]+)=(.*?)" rf"(?:\s+ts=\S+)?\s*$")
    for line in _iter_text_file_lines(path):
        match = pattern.match(line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def read_cif_annotations(path: str) -> Dict[str, str]:
    """Read all PYMOL_ANNOTATOR comments from a CIF file."""
    result = {}
    pattern = re.compile(rf"^#\s*{re.escape(REMARK_PREFIX.split()[-1])}\s+([^=\s]+)=(.*?)" rf"(?:\s+ts=\S+)?\s*$")
    for line in _iter_text_file_lines(path):
        match = pattern.match(line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def read_structure_annotations(path: str, *, resolved=False) -> Dict[str, str]:
    """Read existing annotations from a structure file."""
    if not resolved:
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
    label_column: str = ""
    reference_column: str = ""
    filter_presets: List[Dict[str, object]] = field(default_factory=list)
    sampling: Dict[str, object] = field(default_factory=dict)
    filtered_count: int = 0
    display_columns: List[str] = field(default_factory=list)
    csv_encoding: str = DEFAULT_ENCODING
    csv_delimiter: str = AUTO_DELIMITER
    source_base: str = ""
    mapping_profile: str = DEFAULT_PROFILE_NAME
    mapping_target_format: str = ""
    mapping_wsl_distribution: str = ""
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
    step_cmd: str = "zoom {object}"
    auto_advance: bool = True
    save_on_label_click: bool = False
    show_source_row: bool = False
    show_file_status: bool = False
    keep_previous: bool = False
    prefer_gz: bool = True
    skip_annotated: bool = False  # Skip files that already have the current label
    preload_enabled: bool = True
    preload_ahead: int = 2
    preload_interval_ms: int = 250
    display_columns_selected: bool = False
    annotation_fields: List[dict] = field(default_factory=list)
    score_guidance: dict = field(default_factory=dict)
    background_decompress_gz: bool = True
    cache_enabled: bool = True
    restore_last_dataset: bool = False
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
    state_path: str = ""
    review_reference: dict = field(default_factory=dict)

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
    "save_on_label_click",
    "show_source_row",
    "show_file_status",
    "keep_previous",
    "prefer_gz",
    "skip_annotated",
    "preload_enabled",
    "background_decompress_gz",
    "cache_enabled",
    "restore_last_dataset",
    "display_columns_selected",
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
    "label_column",
    "reference_column",
    "csv_encoding",
    "csv_delimiter",
    "source_base",
    "mapping_profile",
    "mapping_target_format",
    "mapping_wsl_distribution",
    "stage_root",
    "stage_alias",
)
_PENDING_INVALID_STATE_PATHS = set()
_PENDING_INVALID_STATE_LOCK = threading.Lock()


def _path_identity(path: str) -> str:
    text = str(path)
    if is_ssh_path(text):
        return parse_ssh_path(text).uri
    if ntpath.splitdrive(text)[0] or text.startswith(("\\\\", "\\\\?\\")):
        return ntpath.normcase(ntpath.normpath(text))
    return os.path.normcase(os.path.realpath(os.path.abspath(text)))


def _active_source_reference() -> str:
    if STATE.source_mode == "csv" and STATE.dataset is not None:
        return str(STATE.csv_path or "")
    if not STATE.files:
        return ""
    if STATE.source_mode == "csv":
        return str(STATE.csv_path or "")
    return _active_dataset_directory()


def _source_parent(path: str) -> str:
    if is_ssh_path(path):
        remote = parse_ssh_path(path)
        return remote.at(posixpath.dirname(remote.path)).uri
    if ntpath.splitdrive(path)[0] or path.startswith("\\\\"):
        return ntpath.dirname(path)
    return os.path.dirname(path)


def _saved_csv_source_base(persisted: Optional[Mapping[str, object]]) -> str:
    """Return the explicit base while migrating the old absolute default.

    Older state documents wrote the CSV parent even when Relative base was
    left empty. Treat that exact parent as the movable implicit default; a
    genuinely different configured base remains fixed.
    """

    if not persisted:
        return ""
    saved_csv = persisted.get("csv")
    source = persisted.get("source")
    if not isinstance(saved_csv, Mapping):
        return ""
    configured = str(saved_csv.get("source_base") or "")
    if not configured or not isinstance(source, Mapping):
        return configured
    previous_csv = str(source.get("csv_path") or "")
    previous_parent = _source_parent(previous_csv) if previous_csv else ""
    if (
        previous_parent
        and _path_identity(configured) == _path_identity(previous_parent)
    ):
        return ""
    return configured


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
        relative = os.path.relpath(absolute, dataset_dir)
        result.append(
            AnnotationEntry(
                id=directory_entry_id(relative),
                ordinal=index,
                display_name=os.path.basename(path),
                source_path=absolute,
                source_kind="directory",
                source_base=dataset_dir,
                metadata={
                    "structure_path": absolute,
                    "name": os.path.basename(path),
                    "relative_path": relative,
                },
                raw_values=(absolute, os.path.basename(path), relative),
                resolved_path=absolute,
                path_resolution="directory",
                availability=AVAILABILITY_AVAILABLE,
            )
        )
    return result


def _directory_annotation_baseline(
    entries: Sequence[AnnotationEntry],
    *, cancel_event=None, progress=None,
) -> Dict[str, Dict[str, str]]:
    """Import legacy inline remarks/comments as a read-only source baseline."""

    baseline: Dict[str, Dict[str, str]] = {}
    for index, entry in enumerate(entries):
        _check_csv_preparation_cancelled(cancel_event)
        if progress and index % 50 == 0:
            progress(f"Reading existing annotations: {index:,} / {len(entries):,}")
        annotations = read_structure_annotations(entry.source_path, resolved=True)
        if annotations:
            baseline[entry.id] = dict(annotations)
    return baseline


def _csv_annotation_baseline(
    dataset: AnnotationDataset,
) -> Dict[str, Dict[str, str]]:
    """Import ``pymol_annotation_<label>`` columns without mutating the CSV."""

    prefix = "pymol_annotation_"
    columns: Dict[str, str] = {}
    for column in dataset.columns:
        if not column.startswith(prefix) or not column[len(prefix) :]:
            continue
        label = field_key(column[len(prefix) :])
        previous = columns.get(label)
        if previous is not None and previous != column:
            raise ValueError(
                "CSV annotation columns normalize to the same label: "
                f"{previous!r}, {column!r}"
            )
        columns[label] = column
    baseline: Dict[str, Dict[str, str]] = {}
    for entry in dataset.entries:
        values = {
            label: entry.metadata.get(column, "")
            for label, column in columns.items()
            if entry.metadata.get(column, "") != ""
        }
        if values:
            baseline[entry.id] = values
    return baseline


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
    predicate = _filter_predicate(entries, active_filter)
    visible = [
        entry
        for entry in entries
        if entry.is_navigable
        and not entry.error
        and bool(_entry_source_path(entry))
        and predicate(entry)
    ]
    return _sort_entries(
        visible,
        column=active_sort,
        descending=active_descending,
    )


def _sort_entries(
    entries: Sequence[AnnotationEntry], *, column: str, descending: bool
) -> List[AnnotationEntry]:
    """Sort populated values while keeping missing values last."""

    values = list(entries)
    if not column:
        return values
    populated: List[Tuple[object, AnnotationEntry]] = []
    missing: List[AnnotationEntry] = []
    for entry in values:
        value = entry.display_metadata.get(column)
        if value is None:
            value = entry.metadata.get(column, "")
        if value is None or value == "":
            missing.append(entry)
        else:
            populated.append((value, entry))
    try:
        numeric = [(float(value), entry) for value, entry in populated]
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        missing.extend(entry for value, entry in numeric if not math.isfinite(value))
        populated = [(value, entry) for value, entry in numeric if math.isfinite(value)]
    populated.sort(key=lambda item: item[0], reverse=bool(descending))
    return [entry for _value, entry in populated] + missing


def _table_view_entries(
    entries: Sequence[AnnotationEntry],
) -> List[AnnotationEntry]:
    """Filter/sort Scores rows while retaining matching diagnostic rows."""

    predicate = _filter_predicate(entries, STATE.filter_text)
    filtered = [
        entry
        for entry in entries
        if predicate(entry)
    ]
    if STATE.sampling.get("enabled"):
        selected_ids = {entry.id for entry in STATE.entries}
        filtered = [entry for entry in filtered if entry.id in selected_ids]
    return _sort_entries(
        filtered,
        column=str(STATE.sort_column or ""),
        descending=bool(STATE.sort_descending),
    )


def _filter_predicate(entries, query):
    text = str(query or "")
    if text.startswith("where:"):
        columns = tuple(entries[0].metadata) if entries else ()
        predicate = compile_filter(text[6:], columns)
        return lambda entry: bool(predicate(entry.metadata))
    return lambda entry: _entry_filter_match(entry, text)


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
    navigation_session: Optional[NavigationSession] = None,
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
    STATE.navigation_session = navigation_session or _new_navigation_session(STATE.entries, resume)
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
        "navigation_generation", "current_object", "state_path", "review_reference",
        "sampling", "filtered_count",
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

    if not STATE.files:
        return ""
    if STATE.source_mode == "csv" and STATE.csv_path:
        return _source_parent(STATE.csv_path)
    if STATE.dataset is not None and STATE.dataset.source_kind == "directory":
        return STATE.dataset.source_path
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
    config["annotation_fields"] = copy.deepcopy(STATE.annotation_fields)
    config["score_guidance"] = copy.deepcopy(STATE.score_guidance)
    config["filter_presets"] = copy.deepcopy(STATE.filter_presets)
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
    try:
        if "annotation_fields" in raw:
            config["annotation_fields"] = validate_fields(raw["annotation_fields"])
        if "score_guidance" in raw:
            config["score_guidance"] = validate_score_config(raw["score_guidance"])
        if "filter_presets" in raw:
            from .filtering import validate_filter_presets
            config["filter_presets"] = validate_filter_presets(raw["filter_presets"])
    except ValueError as exc:
        raise StateStoreError(str(exc)) from exc
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
    for name in ("annotation_fields", "score_guidance", "filter_presets"):
        if name in config:
            setattr(STATE, name, copy.deepcopy(config[name]))


def _annotation_fields():
    return STATE.annotation_fields or default_fields(normalize_label_key(STATE.label_name), STATE.values())


def _active_annotation_key():
    return _annotation_fields()[0]["name"]


def _reviewed(fields, values):
    return not missing_required(fields, values) and any(values.get(field["name"]) for field in fields)


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
        config = _validate_persisted_config(payload.get("config"))
        config.setdefault("annotation_fields", [])
        config.setdefault("score_guidance", {})
        return {
            "config": config,
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
        invalid_path = dataset_state_path(source_reference, **kwargs)
        with _PENDING_INVALID_STATE_LOCK:
            _PENDING_INVALID_STATE_PATHS.add(_path_identity(invalid_path))
        print(f"  State path: {invalid_path}")
        return None


def _relative_current_file(dataset_dir: str) -> str:
    current = STATE.current_file()
    if not current:
        return ""
    if is_ssh_path(current) and is_ssh_path(dataset_dir):
        return posixpath.relpath(parse_ssh_path(current).path, parse_ssh_path(dataset_dir).path)
    try:
        relative = os.path.relpath(os.path.abspath(current), os.path.abspath(dataset_dir))
    except ValueError:
        return os.path.basename(current)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return os.path.basename(current)
    return relative.replace("\\", "/")


def _persist_dataset_document(source_reference, payload, options, review_override=None):
    state_path = dataset_state_path(source_reference, **options)
    state_identity = _path_identity(state_path)
    with _PENDING_INVALID_STATE_LOCK:
        pending_invalid = state_identity in _PENDING_INVALID_STATE_PATHS
    if pending_invalid:
        quarantined = quarantine_invalid_dataset_state(source_reference, **options)
        with _PENDING_INVALID_STATE_LOCK:
            _PENDING_INVALID_STATE_PATHS.discard(state_identity)
        if quarantined:
            print(f"Structure Annotator: preserved invalid state as {quarantined}")
    if review_override is not None:
        write_review_override(source_reference, review_override)
    return write_dataset_state(source_reference, payload, **options)


def _write_persisted_dataset_state(source_reference: str, *, background=False) -> Optional[str]:
    if not source_reference or (not STATE.files and STATE.dataset is None):
        return None
    try:
        state_path = STATE.state_path or dataset_state_path(source_reference, **_state_kwargs())
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
        options = dict(_state_kwargs())
        review_override = None
        if STATE.source_mode == "csv":
            review = {key: copy.deepcopy(getattr(STATE, key)) for key in REVIEW_KEYS}
            if review != STATE.review_reference:
                review_override = review
        if background:
            submit_write(("state", state_path), _persist_dataset_document,
                         source_reference, copy.deepcopy(payload), options, review_override)
            return state_path
        return _persist_dataset_document(source_reference, payload, options, review_override)
    except Exception as exc:
        print(f"Structure Annotator: state save failed: {exc}")
        print(f"  State path: {dataset_state_path(source_reference, **_state_kwargs())}")
        traceback.print_exc()
        return None


def _save_current_dataset_state(*, background=False) -> Optional[str]:
    active = _active_source_reference()
    if not active:
        return None
    if STATE.source_mode == "directory" and (
        not STATE.dataset_dir
        or _path_identity(active) != _path_identity(STATE.dataset_dir)
    ):
        return None
    return _write_persisted_dataset_state(active, background=background)


def _record_current_config_history(*, background=False) -> Optional[str]:
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
        config = copy.deepcopy(_persisted_config())
        config.update(filter_text=STATE.filter_text, sort_column=STATE.sort_column,
                      sort_descending=bool(STATE.sort_descending))
        options = dict(source_mode=STATE.source_mode,
                       csv_path=STATE.csv_path if STATE.source_mode == "csv" else "",
                       path_column=STATE.path_column, id_column=STATE.id_column,
                       display_columns=list(STATE.display_columns),
                       mapping_profile=STATE.mapping_profile)
        if background:
            submit_write(("history", active), record_config_history, dataset_dir, config, **options)
            return None
        return record_config_history(dataset_dir, config, **options)
    except Exception as exc:
        print(f"Structure Annotator: global history save failed: {exc}")
        traceback.print_exc()
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
    skip_annotated: bool = False, label_key: str = None,
    *, cancel_event=None, progress=None,
) -> List[str]:
    compiled = re.compile(pattern, re.IGNORECASE)
    selected: Dict[str, str] = {}
    dataset = os.path.abspath(os.path.expanduser(path))
    with os.scandir(_long_path(dataset)) as scan:
        candidates = []
        for index, item in enumerate(scan):
            _check_csv_preparation_cancelled(cancel_event)
            if progress and index % 256 == 0:
                progress(f"Scanning folder: {index:,} entries")
            if compiled.match(item.name) and item.is_file():
                candidates.append(item.name)
    for entry in sorted(candidates, key=lambda value: (value.casefold(), value)):
        if compiled.match(entry):
            full_path = os.path.join(dataset, entry)
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

    files = sorted(selected.values(), key=lambda value: (value.casefold(), value))

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
    # Row IDs are intentionally dataset-movable for sidecar persistence. PyMOL
    # object names instead include the concrete source path so two simultaneously
    # visited datasets with the same relative filename can never reuse a stale
    # loaded object.
    entry_id = (
        STATE.entries[index].id
        if 0 <= index < len(STATE.entries)
        else ""
    )
    identity = entry_id + "\0source:" + os.path.abspath(path)
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
_materialization_cancel_events = globals().get("_materialization_cancel_events", {})
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
    source = posixpath.normpath(entry.resolved_path or entry.source_path)
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
    if is_ssh_path(value):
        return parse_ssh_path(value).uri
    if ntpath.splitdrive(value)[0] or value.startswith("\\\\"):
        return ntpath.normcase(ntpath.normpath(value))
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def _materialize_ssh_entry(entry, *, cancel_event=None):
    remote = parse_ssh_path(entry.resolved_path)
    client = SshClient(remote)
    observed = client.stat(cancel_event=cancel_event)

    def identity(value):
        return SourceIdentity(provider="ssh", canonical_source=remote.at(value["path"]).uri,
                              size=value["size"], mtime_ns=value["mtime_ns"],
                              version_token=f"{value['device']}:{value['inode']}:{value['ctime_ns']}")

    source_identity = identity(observed)
    hit = _get_structure_cache().lookup(source_identity)
    if hit is not None:
        return MaterializedStructure(source_path=entry.source_path, path=hit.path, identity=source_identity,
                                     mode="cache", cache_key=hit.key, cache_hit=True)
    suffix = ".pdb.gz" if remote.path.lower().endswith(".pdb.gz") else ".cif.gz" if remote.path.lower().endswith(".cif.gz") else posixpath.splitext(remote.path)[1]
    if not remote.path.lower().endswith(".gz"):
        def download_to_cache(temporary):
            downloaded = client.download(temporary, cancel_event=cancel_event)
            if identity(downloaded) != source_identity:
                raise SourceChangedError(
                    f"remote source changed while downloading: {remote.path}"
                )

        cached = _get_structure_cache().get_or_create(
            source_identity, suffix=suffix, writer=download_to_cache
        )
        return MaterializedStructure(
            source_path=entry.source_path, path=cached.path,
            identity=source_identity, mode="copy", cache_key=cached.key,
            cache_hit=cached.cache_hit,
        )
    with tempfile.TemporaryDirectory(prefix="pymol_ssh_structure_") as directory:
        destination = os.path.join(directory, "structure" + suffix)
        downloaded = client.download(destination, cancel_event=cancel_event)
        if identity(downloaded) != source_identity:
            raise SourceChangedError(
                f"remote source changed while downloading: {remote.path}"
            )
        result = _get_copy_materializer().materialize(destination, provider="ssh",
                   canonical_source=source_identity.canonical_source, source_identity=source_identity,
                   logical_path=remote.path)
        return replace(result, source_path=entry.source_path)


def _materialize_entry(
    entry: AnnotationEntry,
    context: _MaterializationContext,
    *,
    staged_source_identity: Optional[object] = None,
    cancel_event: Optional[object] = None,
) -> MaterializedStructure:
    provider = _entry_provider(entry)
    if provider == "ssh":
        return _materialize_ssh_entry(entry, cancel_event=cancel_event)
    if provider.startswith("wsl-stage:"):
        return _materialize_wsl_entry(
            entry, context, observed=staged_source_identity
        )
    source = _entry_source_path(entry)
    use_cache = bool(context.cache_enabled) and (
        _is_remote_host_path(source, provider) or _is_gz_path(source)
    )
    if use_cache:
        try:
            return _get_copy_materializer().materialize(
                _long_path(source),
                provider=provider,
                canonical_source=_cache_canonical_source(source),
                logical_path=entry.source_path,
            )
        except Exception as cache_error:
            # Cache is an optimization for Host-readable sources. If its root
            # is unavailable or unwritable, retain the user's ability to view
            # the structure directly. WSL staging remains cache-backed above
            # because its downloaded temporary file cannot be returned safely.
            try:
                direct = _get_direct_materializer().materialize(
                    _long_path(source),
                    provider=provider,
                    canonical_source=_cache_canonical_source(source),
                )
            except Exception as direct_error:
                raise direct_error from cache_error
            print(
                "Structure Annotator: cache unavailable; using the source "
                f"directly: {cache_error}"
            )
            return direct
    return _get_direct_materializer().materialize(
        _long_path(source),
        provider=provider,
        canonical_source=_cache_canonical_source(source),
    )


def _materialize_entry_in_background(entry, context, cancel_event, bridge):
    callback = None
    if bridge is not None:
        callback = lambda message: bridge.materialization_progress.emit(
            context.generation, entry.id, message
        )
    with ssh_progress(callback):
        return _materialize_entry(entry, context, cancel_event=cancel_event)


def _cancel_materialization_key(key):
    event = _materialization_cancel_events.get(key)
    if event is not None:
        event.set()
    future = _materialization_futures.get(key)
    return bool(future is not None and (future.cancel() or future.done()))


def _cancel_materialization_record(record):
    if record is None:
        return False
    return _cancel_materialization_key((record[0], record[1]))


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
        cancel_event = _materialization_cancel_events.get(future_key)
        try:
            result = future.result()
        except Exception as exc:
            cancelled = bool(cancel_event is not None and cancel_event.is_set())
            if (not cancelled and not isinstance(exc, (CancelledError, SshCancelled))
                    and context.generation == _materialization_generation):
                STATE.materialization_errors[entry.id] = str(exc)
            _materialization_futures.pop(future_key, None)
            _materialization_cancel_events.pop(future_key, None)
            raise
        _materialization_futures.pop(future_key, None)
        _materialization_cancel_events.pop(future_key, None)
        if (
            context.generation == _materialization_generation
            and index < len(STATE.entries) and STATE.entries[index].id == entry.id
        ):
            STATE.materialized_paths[entry.id] = result
            STATE.materialization_errors.pop(entry.id, None)
        return result
    if allow_background:
        cancel_event = threading.Event()
        bridge = _CSV_ASYNC_BRIDGE
        app = QtCore.QCoreApplication.instance() if _HAS_QT_ASYNC else None
        if (bridge is None and app is not None
                and QtCore.QThread.currentThread() == app.thread()):
            bridge = _csv_async_bridge()
        future = _get_bg_executor().submit(
            _materialize_entry_in_background, entry, context, cancel_event, bridge
        )
        _materialization_cancel_events[future_key] = cancel_event
        _materialization_futures[future_key] = future

        def forget_cancelled(done, key=future_key, event=cancel_event):
            if event.is_set() and _materialization_futures.get(key) is done:
                _materialization_futures.pop(key, None)
                _materialization_cancel_events.pop(key, None)

        future.add_done_callback(forget_cancelled)
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
    global _materialization_futures, _materialization_cancel_events
    global _materialization_generation
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
    for event in list(_materialization_cancel_events.values()):
        event.set()
    for future in list(_materialization_futures.values()):
        try:
            future.cancel()
        except Exception:
            pass
    _materialization_futures = {}
    _materialization_cancel_events = {}
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
    for number, formatted in format_commands(template, context):
        try:
            _execute_checked(cmd, formatted)
        except Exception as exc:
            raise RuntimeError("Command line %d failed: %s\n%s" % (number, formatted, exc)) from exc


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
    if _panel is not None and _panel._pending_navigation is not None:
        desired.add(_panel._pending_navigation[2])

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
        if not trust_materialized and not os.path.exists(load_path_resolved):
            raise FileNotFoundError(f"Structure Annotator: file not found: {load_path}")
        if obj_name not in STATE.managed_objects:
            STATE.managed_objects.append(obj_name)
        try:
            cmd.load(load_path_resolved, obj_name, quiet=1, zoom=-1 if focus else 0)
            if _panel is not None and not _panel._closed:
                _panel._invalidate_reference_candidate(obj_name)
        except Exception:
            # Some Host loaders can leave a partial object before raising. Keep
            # that name owned so a transaction rollback can delete it exactly.
            if not _object_exists(obj_name) and obj_name in STATE.managed_objects:
                STATE.managed_objects.remove(obj_name)
            raise
        # ``cmd.load`` is synchronous. Calling ``cmd.sync`` from an sa_* parser
        # callback waits on the command queue which currently owns that callback
        # and produces a real Host ``wait_queue`` timeout.
        # A preload stays disabled before Qt can draw it. Forcing refresh here
        # briefly rendered the next object and stalled the active structure.
    if obj_name not in STATE.managed_objects:
        STATE.managed_objects.append(obj_name)

    if not focus:
        if obj_name != STATE.current_object:
            cmd.disable(obj_name)
        return obj_name

    for other in list(STATE.managed_objects):
        if other != obj_name and _object_exists(other):
            cmd.disable(other)
    cmd.enable(obj_name)

    STATE.current_object = obj_name
    if _panel is None:
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
            removable = _cancel_materialization_key(key)
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
    if STATE.background_decompress_gz and not STATE.entries:
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
        if _panel is not None:
            _panel._finish_navigation((generation, entry_id, idx))
        _evict_unneeded_objects()
    except Exception as e:
        print(f"Structure Annotator preload error: {e}")
        traceback.print_exc()
        _preload_queue = []
        if _panel is not None:
            _panel._pending_navigation = None
            _panel._refresh_status()
            _panel._show_error(f"Could not prepare structure: {e}")
        return
    if _preload_queue:
        _schedule_preload_tick()


def _load_current_structure(*, trust_materialized: bool = False) -> str:
    if not STATE.current_file():
        raise RuntimeError("Structure Annotator: no dataset loaded")
    loaded = _ensure_loaded(
        STATE.index,
        focus=True,
        allow_gz_fallback=True,
        trust_materialized=trust_materialized,
    )
    if not loaded:
        raise RuntimeError(f"failed to load structure at index {STATE.index}")
    if _panel is not None and not _panel._closed:
        _panel._queue_preload_maintenance()
    else:
        _evict_unneeded_objects()
        _kick_preload()
    return loaded


def _navigate_annotation(method: str, *args) -> str:
    """Transition the shared cursor and roll it back if display loading fails."""
    session = _ensure_navigation_session()
    previous = session.cursor
    previous_object = STATE.current_object
    previous_view = cmd.get_view()
    previous_enabled = set(cmd.get_names("objects", enabled_only=1)).intersection(STATE.managed_objects)
    getattr(session, method)(*args)
    STATE.index = session.cursor
    try:
        loaded = _load_current_structure()
        if _panel is not None and not _panel._closed:
            _panel._schedule_state_save()
        else:
            _save_current_dataset_state()
        return loaded
    except Exception:
        session.goto(previous)
        STATE.index = session.cursor
        STATE.current_object = previous_object
        for name in STATE.managed_objects:
            if _object_exists(name):
                (cmd.enable if name in previous_enabled else cmd.disable)(name)
        cmd.set_view(previous_view)
        raise


def _annotations_for_index(index: int, *, refresh: bool = True) -> Dict[str, str]:
    if not STATE.files or index < 0 or index >= len(STATE.files):
        return {}
    if STATE.row_annotation_store is not None and index < len(STATE.entries):
        return STATE.row_annotation_store.annotations_for(STATE.entries[index].id, refresh=refresh)
    return read_structure_annotations(_resolve_annotation_path(STATE.files[index]))


def _review_status_for_entry(entry_id: str, *, refresh: bool = True) -> str:
    """Return workflow status without making it part of user annotation fields."""
    if not entry_id or STATE.row_annotation_store is None:
        return ""
    return str(
        STATE.row_annotation_store.get(entry_id, REVIEW_STATUS_LABEL, "")
        or ""
    )


def _review_status_ids(status: str = REVIEW_STATUS_LATER) -> set:
    if STATE.row_annotation_store is None:
        return set()
    return {
        entry_id
        for entry_id, values in STATE.row_annotation_store.snapshot().items()
        if values.get(REVIEW_STATUS_LABEL) == status
    }


def _set_review_status(entry_ids: Sequence[str], status: str = "") -> None:
    """Persist triage state separately from the configured quality labels."""
    if STATE.row_annotation_store is None:
        return
    changes = [
        {"entry_id": entry_id, "label": REVIEW_STATUS_LABEL, "value": status or None}
        for entry_id in dict.fromkeys(str(value) for value in entry_ids if value)
    ]
    if changes:
        STATE.row_annotation_store.set_many(changes)


def _clear_review_status_entries(entry_ids: Sequence[str]) -> None:
    _set_review_status(entry_ids, "")


def _set_entry_annotation(index: int, label: str, value: str) -> Dict[str, object]:
    if STATE.row_annotation_store is not None:
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
    if STATE.row_annotation_store is not None:
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


def _rescan_annotated_indices(*, refresh=True) -> None:
    """Populate STATE.annotated_indices for progress tracking under the current label."""
    STATE.annotated_indices = set()
    STATE.annotated_entry_ids = set()
    if not STATE.files:
        return
    label_key = normalize_label_key(STATE.label_name)
    if STATE.row_annotation_store is not None:
        fields = _annotation_fields()
        required = [field for field in fields if field["required"]]
        if len(required) == 1:
            key = required[0]["name"]
            values = (STATE.row_annotation_store.snapshot_for_label(key) if refresh else
                      STATE.row_annotation_store.snapshot_for_label(key, refresh=False))
            complete = {entry_id for entry_id, value in values.items() if _reviewed(required, {key: value})}
        else:
            values = STATE.row_annotation_store.snapshot(refresh=refresh)
            complete = {entry_id for entry_id, value in values.items() if _reviewed(fields, value)}
        for idx, entry in enumerate(STATE.entries):
            if entry.id in complete:
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
    profile = _mapping_profile(profile_name, read_only=read_only)
    return profile["rules"]


def _mapping_profile(profile_name, *, read_only=True):
    profile = (
        get_mapping_profile_readonly(profile_name)
        if read_only
        else get_mapping_profile(profile_name)
    )
    if profile is None:
        if profile_name.casefold() == "custom":
            return dict(name="custom", description="Use paths as written", rules=[],
                        source_format="custom", target_format="custom", rewrites=[], wsl_distribution="")
        raise MappingStoreError(f"unknown path mapping profile: {profile_name}")
    rules = profile.get("rules", [])
    if not isinstance(rules, list):
        raise MappingStoreError("mapping profile rules must be a list")
    return profile


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
    target_format: Optional[str] = None,
    wsl_distribution: Optional[str] = None,
) -> AnnotationDataset:
    _check_csv_preparation_cancelled(cancel_event)
    if is_ssh_path(dataset.source_path):
        from .sources.ssh_source import resolve_ssh_dataset
        return resolve_ssh_dataset(dataset, require_exists=require_exists, cancel_event=cancel_event)
    profile = _mapping_profile(mapping_profile, read_only=read_only_mappings)
    options = {}
    if target_format is None and "target_format" not in profile and os.name != "nt":
        options["target_format"] = "custom"
    if target_format is not None:
        options["target_format"] = target_format
    if wsl_distribution is not None:
        options["wsl_distribution"] = wsl_distribution
    resolver = PathResolver.from_profile(
        profile, **options,
        exists=lambda path: os.path.exists(_long_path(path)),
        readable=lambda path: os.access(_long_path(path), os.R_OK),
        is_file=lambda path: os.path.isfile(_long_path(path)),
    )
    stage_entries = dataset.entries
    if stage_root and stage_alias and resolver.rewrites:
        stage_entries = tuple(replace(entry, source_path=rewrite_path(entry.source_path, resolver.rewrites))
                              for entry in dataset.entries)
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
        stage_entries,
        stage_root,
        stage_alias,
        require_exists=require_exists,
        cancel_event=cancel_event,
    )
    for entry, stage_entry in zip(dataset.entries, stage_entries):
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
            coordinates = _stage_coordinates(stage_entry, stage_root, stage_alias)
            outcome = (
                stage_outcomes.get(coordinates.relative)
                if coordinates is not None
                else None
            )
            if outcome is not None and coordinates is not None:
                resolution = _stage_resolution_from_outcome(
                    stage_entry,
                    coordinates,
                    require_exists=require_exists,
                    observed=outcome.observed,
                    error=outcome.error,
                    method=outcome.method,
                )
            else:
                resolution = _stage_resolution(
                    stage_entry,
                    stage_root,
                    stage_alias,
                    require_exists=require_exists,
                )
            if resolution is not None and stage_entry is not entry:
                resolution = replace(resolution, raw_path=entry.source_path, rewritten_path=stage_entry.source_path)
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
        "label_column": dataset.label_column,
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


def _csv_file_fingerprint(path: str, *, cancel_event=None) -> _CsvFileFingerprint:
    if is_ssh_path(path):
        value = SshClient(path).stat(cancel_event=cancel_event)
        if not value["file"]:
            raise ValueError("Remote table path is not a regular file.")
        return _CsvFileFingerprint(value["device"], value["inode"], value["size"], value["mtime_ns"])
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
    annotation_fields: tuple = ()
    score_guidance: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    index: int = 0
    entry_id: str = ""
    dataset_dir: str = ""


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
        annotation_fields=tuple(copy.deepcopy(STATE.annotation_fields)),
        score_guidance=copy.deepcopy(STATE.score_guidance),
        config=copy.deepcopy(_persisted_config()),
        index=int(STATE.index),
        entry_id=STATE.current_entry_id(),
        dataset_dir=str(STATE.dataset_dir),
    )


@dataclass(frozen=True)
class _PreparedCsvPreview:
    payload: Dict[str, object]
    source_fingerprint: _CsvFileFingerprint
    dataset: Optional[AnnotationDataset] = None
    review_signatures: tuple = ()


@dataclass(frozen=True)
class _PreparedCsvLoad:
    dataset: AnnotationDataset
    active_entries: Tuple[AnnotationEntry, ...]
    payload: Dict[str, object]
    persisted_config: Optional[Dict[str, object]]
    mapping_profile: str
    source_base: str
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
    navigation_session: Optional[NavigationSession] = None
    target_format: str = ""
    wsl_distribution: str = ""
    state_path: str = ""
    review_config: dict = field(default_factory=dict)


def _prepare_csv_preview(
    csv_path: str,
    path_column: Optional[str] = None,
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
    progress=None,
    target_format: Optional[str] = None,
    wsl_distribution: Optional[str] = None,
    label_column: Optional[str] = None,
) -> _PreparedCsvPreview:
    """Perform preview I/O without reading live STATE or touching PyMOL/Qt."""

    canonical_csv = normalize_source_path(csv_path)
    _check_csv_preparation_cancelled(cancel_event)
    before = _csv_file_fingerprint(canonical_csv, cancel_event=cancel_event)
    review = read_review_file(canonical_csv, cancel_event=cancel_event)
    configured = review.config
    source = CsvDatasetSource(
        canonical_csv,
        str(path_column if path_column is not None else configured.get("path_column", "structure_path")),
        id_column=str(id_column or ""),
        display_columns=_display_column_names(configured.get("display_columns") if display_columns in (None, "") else display_columns),
        encoding=str(encoding or DEFAULT_ENCODING),
        delimiter=str(delimiter or AUTO_DELIMITER),
        source_base=str(source_base or ""),
        label_column=label_column if label_column is not None else configured.get("label_column", ""),
    )
    dataset = source.load(cancel_event=cancel_event, progress=(
        (lambda count: progress(f"Reading CSV: {count:,} rows")) if progress else None
    ))
    validate_review_columns(configured, dataset.columns)
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
        target_format=target_format, wsl_distribution=wsl_distribution,
    )
    after = _csv_file_fingerprint(canonical_csv, cancel_event=cancel_event)
    if after != before:
        raise RuntimeError("CSV changed while preview was being prepared; retry")
    check_review_unchanged(canonical_csv, review, cancel_event=cancel_event)
    return _PreparedCsvPreview(
        payload=_csv_preview_payload(dataset, profile),
        source_fingerprint=after,
        dataset=dataset,
        review_signatures=review.signatures,
    )


def _prepare_csv_header(csv_path, encoding=DEFAULT_ENCODING, delimiter=AUTO_DELIMITER,
                        *, cancel_event=None, progress=None, prefer_saved=False):
    _check_csv_preparation_cancelled(cancel_event)
    saved = _read_persisted_dataset_state(csv_path, "csv") if prefer_saved else None
    if prefer_saved and saved:
        encoding = (saved.get("csv") or {}).get("encoding") or encoding
        delimiter = (saved.get("csv") or {}).get("delimiter") or delimiter
    header = read_csv_header(csv_path if is_ssh_path(csv_path) else _long_path(csv_path),
                             encoding=encoding, delimiter=delimiter, cancel_event=cancel_event)
    review = read_review_file(csv_path, cancel_event=cancel_event)
    validate_review_columns(review.config, header["columns"])
    header["review_config"] = review.config
    header["saved_state"] = saved
    header["input_delimiter"] = delimiter
    _check_csv_preparation_cancelled(cancel_event)
    return header


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
    progress=None,
    validate_paths: bool = True,
    reuse_preview: Optional[_PreparedCsvPreview] = None,
    restore_saved: bool = True,
    config_override: Optional[Mapping[str, object]] = None,
    review_options: Optional[Mapping[str, object]] = None,
    target_format: Optional[str] = None,
    wsl_distribution: Optional[str] = None,
    label_column: Optional[str] = None,
) -> _PreparedCsvLoad:
    """Prepare one immutable CSV load plan without consulting live STATE."""

    canonical_csv = normalize_source_path(csv_path)
    _check_csv_preparation_cancelled(cancel_event)
    before = _csv_file_fingerprint(canonical_csv, cancel_event=cancel_event)
    review = read_review_file(canonical_csv, cancel_event=cancel_event)
    same_active_csv = bool(
        runtime.source_mode == "csv"
        and runtime.csv_path
        and _path_identity(runtime.csv_path) == _path_identity(canonical_csv)
    )
    persisted = _read_persisted_dataset_state(canonical_csv, "csv") if restore_saved else None
    saved_csv = dict(persisted.get("csv") or {}) if persisted else {}
    if persisted is not None:
        saved_csv["source_base"] = _saved_csv_source_base(persisted)
    try:
        saved_display_columns = _display_column_names(
            saved_csv.get("display_columns")
        )
    except (TypeError, ValueError):
        saved_display_columns = None
    saved_view = dict(persisted.get("view") or {}) if persisted else {}

    def _source_value(
        supplied: Optional[object],
        saved_name: str,
        active_value: object,
        default: object,
    ) -> object:
        if supplied is not None:
            return supplied
        if saved_name in review.config:
            return review.config[saved_name]
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
    effective_label_column = str(_source_value(
        label_column, "label_column", runtime.config.get("label_column", ""),
        (persisted or {}).get("config", {}).get("label_column", ""),
    ) or "")
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
    mapping_config = (persisted or {}).get("config") or (runtime.config if same_active_csv else {})
    if target_format is None:
        target_format = mapping_config.get("mapping_target_format") or None
    if wsl_distribution is None:
        wsl_distribution = mapping_config.get("mapping_wsl_distribution") or None
    if is_ssh_path(canonical_csv):
        effective_profile, target_format, wsl_distribution = "custom", "custom", ""
        profile = {"target_format": "custom"}
    else:
        profile = _mapping_profile(effective_profile, read_only=True)
    if target_format is None:
        target_format = profile.get("target_format", "windows" if os.name == "nt" else "custom")
    if wsl_distribution is None:
        wsl_distribution = profile.get("wsl_distribution", "")
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
    if config_override is not None:
        next_filter = str(config_override.get("filter_text", next_filter))
        next_sort_column = str(config_override.get("sort_column", next_sort_column))
        next_sort_descending = bool(config_override.get("sort_descending", next_sort_descending))

    _check_csv_preparation_cancelled(cancel_event)
    source = CsvDatasetSource(
        canonical_csv,
        effective_path_column,
        id_column=effective_id_column,
        display_columns=effective_display_columns,
        encoding=effective_encoding,
        delimiter=effective_delimiter,
        source_base=effective_source_base,
        label_column=effective_label_column,
    )
    try:
        reusable = (reuse_preview is not None and reuse_preview.dataset is not None
                    and reuse_preview.source_fingerprint == before
                    and reuse_preview.dataset.path_column == effective_path_column
                    and reuse_preview.dataset.id_column == effective_id_column
                    and reuse_preview.dataset.label_column == effective_label_column
                    and reuse_preview.review_signatures == review.signatures
                    and tuple(reuse_preview.dataset.display_columns) == tuple(effective_display_columns or ()))
        dataset = reuse_preview.dataset if reusable else source.load(
            cancel_event=cancel_event,
            progress=(lambda count: progress(f"Reading CSV: {count:,} rows")) if progress else None,
        )
    except CsvSourceError as exc:
        stale_saved_columns = (
            effective_display_columns is not None
            and persisted is not None
            and "display_columns" not in review.config
            and (
                display_columns is None
                or effective_display_columns == saved_display_columns
            )
            and (
                "unknown display columns" in str(exc)
                or "display_columns" in str(exc)
            )
        )
        if not stale_saved_columns:
            raise
        # Display columns are a recoverable view preference. Path/ID columns
        # remain strict dataset contracts and still fail with their diagnostics.
        effective_display_columns = None
        source = CsvDatasetSource(
            canonical_csv,
            effective_path_column,
            id_column=effective_id_column,
            display_columns=None,
            encoding=effective_encoding,
            delimiter=effective_delimiter,
            source_base=effective_source_base,
            label_column=effective_label_column,
        )
        dataset = source.load(cancel_event=cancel_event)
    validate_review_columns(review.config, dataset.columns)
    _check_csv_preparation_cancelled(cancel_event)
    if progress:
        progress(f"Mapping {dataset.count:,} rows")
    dataset = _resolve_csv_dataset(
        dataset,
        effective_profile,
        stage_root=effective_stage_root,
        stage_alias=effective_stage_alias,
        require_exists=validate_paths,
        target_format=target_format, wsl_distribution=wsl_distribution,
        read_only_mappings=True,
        cancel_event=cancel_event,
    )
    position = persisted.get("position") if persisted else None
    requested_index = int((position or {}).get("index", 0))
    requested_entry = str((position or {}).get("entry_id", ""))
    if not restore_saved and same_active_csv:
        requested_index, requested_entry = runtime.index, runtime.entry_id
    if next_sort_column not in set(dataset.columns):
        next_sort_column = ""
        next_sort_descending = False
    active_entries = _view_entries(
        dataset.entries,
        filter_text=next_filter,
        sort_column=next_sort_column,
        sort_descending=next_sort_descending,
    )
    if not active_entries and next_filter:
        unfiltered = _view_entries(
            dataset.entries,
            filter_text="",
            sort_column=next_sort_column,
            sort_descending=next_sort_descending,
        )
        if unfiltered:
            next_filter = ""
            active_entries = unfiltered
    if not active_entries:
        raise RuntimeError("CSV contains no resolvable, valid structure rows")
    selected_entry = active_entries[
        _resume_entry_index(active_entries, requested_entry, requested_index)
    ]
    if not validate_paths:
        if progress:
            progress("Checking the first structure; other paths will be checked as needed")
        replacements = {}
        start = active_entries.index(selected_entry)
        for candidate in active_entries[start:] + active_entries[:start]:
            checked = _resolve_csv_dataset(
                replace(dataset, entries=(candidate,)), effective_profile,
                stage_root=effective_stage_root, stage_alias=effective_stage_alias,
                target_format=target_format, wsl_distribution=wsl_distribution,
                read_only_mappings=True, cancel_event=cancel_event,
            ).entries[0]
            replacements[candidate.id] = checked
            if checked.is_navigable and not checked.error:
                selected_entry = checked
                break
        else:
            raise RuntimeError("CSV contains no readable structure paths; check the path column and mapping")
        dataset = replace(dataset, entries=tuple(replacements.get(entry.id, entry) for entry in dataset.entries))
        active_entries = _view_entries(dataset.entries, filter_text=next_filter,
                                      sort_column=next_sort_column, sort_descending=next_sort_descending)
        requested_entry = selected_entry.id
    _check_csv_preparation_cancelled(cancel_event)
    if progress:
        progress("Preparing the first structure")
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
        cancel_event=cancel_event,
    )
    _check_csv_preparation_cancelled(cancel_event)
    # Read annotations last so a slow remote materialization does not leave the
    # prepared progress snapshot stale for the duration of the download/copy.
    annotation_baseline = _csv_annotation_baseline(dataset)
    row_annotation_store = RowAnnotationStore(
        annotation_sidecar_path(dataset.source_path),
        baseline=annotation_baseline,
    )
    persisted_config = (
        dict(persisted.get("config") or {}) if persisted is not None else None
    )
    if config_override is not None:
        persisted_config = _validate_persisted_config(dict(config_override))
    if review.config:
        persisted_config = dict(persisted_config or {})
        if not same_active_csv and not persisted:
            persisted_config.setdefault("score_guidance", {})
        for name in ("step_cmd", "bucket_cmds"):
            if name in review.config:
                persisted_config[name] = copy.deepcopy(review.config[name])
        if "display_columns" in review.config:
            persisted_config["display_columns_selected"] = True
    if review_options:
        persisted_config = dict(persisted_config or {})
        for name in ("step_cmd", "bucket_cmds"):
            if name in review_options:
                persisted_config[name] = copy.deepcopy(review_options[name])
    reference_column = review.config.get("reference_column", (persisted_config or {}).get("reference_column", ""))
    if reference_column and reference_column not in dataset.columns:
        raise ValueError(f"Unknown reference column: {reference_column}")
    persisted_config = dict(persisted_config or {})
    persisted_config["reference_column"] = reference_column
    from .filtering import validate_filter_presets
    persisted_config["filter_presets"] = validate_filter_presets(
        review.config.get("filter_presets", persisted_config.get("filter_presets", []) if same_active_csv or persisted else []),
        dataset.columns)
    label_name = str(
        (persisted_config or {}).get("label_name") or runtime.label_name or "quality"
    )
    annotated_values = row_annotation_store.snapshot()
    review_fields = (persisted_config or {}).get("annotation_fields") or list(runtime.annotation_fields)
    if not review_fields:
        review_fields = default_fields(normalize_label_key(label_name), ["Good", "Bad"])
    annotated_entry_ids = tuple(
        entry.id for entry in active_entries if _reviewed(review_fields, annotated_values.get(entry.id, {}))
    )
    _check_csv_preparation_cancelled(cancel_event)
    after = _csv_file_fingerprint(canonical_csv, cancel_event=cancel_event)
    if after != before:
        raise RuntimeError("CSV changed while load was being prepared; retry")
    check_review_unchanged(canonical_csv, review, cancel_event=cancel_event)
    return _PreparedCsvLoad(
        dataset=dataset,
        active_entries=tuple(active_entries),
        payload=_csv_preview_payload(
            dataset, effective_profile, row_limit=20
        ),
        persisted_config=persisted_config,
        mapping_profile=effective_profile,
        target_format=target_format, wsl_distribution=wsl_distribution,
        source_base=effective_source_base,
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
        state_path=dataset_state_path(canonical_csv, source_mode="csv", csv_path=canonical_csv),
        review_config=review.config,
        navigation_session=_new_navigation_session(active_entries, _resume_entry_index(active_entries, requested_entry, requested_index)),
    )


def _commit_prepared_csv_load(plan: _PreparedCsvLoad) -> Dict[str, object]:
    """Commit a prepared plan transactionally; call only on PyMOL main thread."""

    previous = _runtime_snapshot()
    parked: List[_ParkedManagedObject] = []
    scene_swap_started = False
    try:
        if plan.persisted_config is None and (STATE.source_mode != "csv" or _path_identity(STATE.csv_path) != _path_identity(plan.dataset.source_path)):
            STATE.score_guidance = {}
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
        STATE.label_column = dataset.label_column
        STATE.display_columns = list(dataset.display_columns)
        STATE.csv_encoding = dataset.encoding
        STATE.csv_delimiter = dataset.delimiter
        # Keep the user's configured value separate from CsvDatasetSource's
        # effective parent-directory fallback so moved datasets remain portable.
        STATE.source_base = plan.source_base
        STATE.mapping_profile = plan.mapping_profile
        STATE.mapping_target_format = plan.target_format
        STATE.mapping_wsl_distribution = plan.wsl_distribution
        STATE.stage_root = plan.stage_root
        STATE.stage_alias = plan.stage_alias
        STATE.filter_text = plan.filter_text
        STATE.sort_column = plan.sort_column
        STATE.sort_descending = plan.sort_descending
        STATE.dataset = dataset
        STATE.sampling = {}
        STATE.filtered_count = len(plan.active_entries)
        STATE.state_path = plan.state_path
        STATE.review_reference = {key: copy.deepcopy(getattr(STATE, key)) for key in REVIEW_KEYS}
        STATE.review_reference.update(copy.deepcopy(plan.review_config))
        STATE.all_entries = list(dataset.entries)
        STATE.row_annotation_store = plan.row_annotation_store
        STATE.undo_history.clear()
        _install_entries(
            dataset.entries,
            resume_entry_id=plan.requested_entry,
            index=plan.requested_index,
            active_entries=plan.active_entries,
            navigation_session=plan.navigation_session,
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
        background = _panel is not None and not _panel._closed
        state_path = _write_persisted_dataset_state(dataset.source_path, background=background)
        _record_current_config_history(background=background)
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
    path_column: Optional[str] = None,
    id_column: str = "",
    display_columns: object = "",
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = AUTO_DELIMITER,
    mapping_profile: str = DEFAULT_PROFILE_NAME,
    source_base: str = "",
    stage_root: str = "",
    stage_alias: str = "",
    label_column: Optional[str] = None,
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
        label_column=label_column,
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
    label_column: Optional[str] = None,
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
            label_column=label_column,
        )
        return _commit_prepared_csv_load(plan)
    except Exception as exc:
        print(f"Structure Annotator CSV load error: {exc}")
        traceback.print_exc()
        return None


@dataclass(frozen=True)
class _PreparedDirectoryLoad:
    dataset: AnnotationDataset
    entries: tuple
    config: dict
    index: int
    materialized: MaterializedStructure
    annotation_store: RowAnnotationStore
    annotated_ids: frozenset
    navigation_session: NavigationSession
    state_path: str


def _prepare_directory_load(path, pattern, runtime, *, restore_saved=True,
                            cancel_event=None, progress=None):
    """Discover files and read legacy annotations without accessing live STATE."""
    directory = normalize_source_path(path)
    _check_csv_preparation_cancelled(cancel_event)
    saved = _read_persisted_dataset_state(directory, "directory") if restore_saved else None
    config = dict(runtime.config)
    if saved:
        config.update(saved["config"])
    config["pattern"] = str(pattern or (saved or {}).get("config", {}).get("pattern") or STRUCTURE_PATTERN)
    if is_ssh_path(directory):
        from .sources.ssh_source import load_ssh_directory
        remote_dataset = load_ssh_directory(directory, config["pattern"], prefer_gz=config["prefer_gz"], cancel_event=cancel_event)
        all_entries = list(remote_dataset.entries)
        baseline = {}
    else:
        files = _list_structure_files(directory, config["pattern"], config["prefer_gz"],
                                      cancel_event=cancel_event, progress=progress)
        all_entries = _directory_entries(directory, files)
        baseline = _directory_annotation_baseline(all_entries, cancel_event=cancel_event, progress=progress)
    if not all_entries:
        raise ValueError(f"No structure files matched the filename pattern in {directory}")
    store = RowAnnotationStore(directory_annotation_sidecar_path(directory), baseline=baseline)
    values = store.snapshot()
    review_fields = config.get("annotation_fields") or default_fields(normalize_label_key(config["label_name"]), _split_values(config["values_text"]))
    annotated = frozenset(entry.id for entry in all_entries if _reviewed(review_fields, values.get(entry.id, {})))
    entries = tuple(entry for entry in all_entries if not config["skip_annotated"] or entry.id not in annotated)
    if not entries:
        raise ValueError("All matching structures are already annotated. Disable 'Skip annotated structures' to reopen them.")
    position = (saved or {}).get("position")
    if not restore_saved and runtime.source_mode == "directory" and _path_identity(directory) == _path_identity(runtime.dataset_dir):
        position = {"index": runtime.index, "entry_id": runtime.entry_id}
    index = _resume_index(directory, [entry.source_path for entry in entries], position, entries)
    _check_csv_preparation_cancelled(cancel_event)
    if progress:
        progress(f"Preparing the first structure from {len(entries):,} files")
    materialized = _materialize_entry(entries[index], _MaterializationContext(
        generation=runtime.materialization_generation, cache_enabled=config["cache_enabled"], stage_root="", stage_alias=""),
        cancel_event=cancel_event)
    _check_csv_preparation_cancelled(cancel_event)
    dataset = AnnotationDataset(source_kind="directory", source_path=directory, source_base=directory,
                                entries=tuple(all_entries), columns=("structure_path", "name", "relative_path"), path_column="structure_path")
    return _PreparedDirectoryLoad(dataset, entries, config, index, materialized, store, annotated,
                                  _new_navigation_session(entries, index), dataset_state_path(directory))


def _commit_prepared_directory_load(plan):
    """Commit a fully prepared folder on the PyMOL thread, with scene rollback."""
    previous = _runtime_snapshot()
    parked: List[_ParkedManagedObject] = []
    scene_swap_started = False
    try:
        _apply_persisted_config(plan.config)
        parked = _begin_scene_swap()
        scene_swap_started = True
        STATE.source_mode = "directory"
        STATE.dataset_dir = plan.dataset.source_path
        STATE.csv_path = ""
        STATE.path_column = "structure_path"
        STATE.id_column = ""
        STATE.label_column = ""
        STATE.reference_column = ""
        STATE.filter_presets = []
        STATE.sampling = {}
        STATE.filtered_count = len(plan.entries)
        STATE.display_columns = []
        STATE.csv_encoding = DEFAULT_ENCODING
        STATE.csv_delimiter = AUTO_DELIMITER
        STATE.source_base = ""
        STATE.filter_text = ""
        STATE.sort_column = ""
        STATE.sort_descending = False
        STATE.stage_root = ""
        STATE.stage_alias = ""
        STATE.dataset = plan.dataset
        STATE.state_path = plan.state_path
        STATE.review_reference = {}
        STATE.all_entries = list(plan.entries)
        STATE.row_annotation_store = plan.annotation_store
        _install_entries(plan.entries, index=plan.index, active_entries=plan.entries, navigation_session=plan.navigation_session)
        STATE.materialized_paths[plan.entries[plan.index].id] = plan.materialized
        STATE.current_object = None
        STATE.undo_history.clear()
        STATE.annotated_entry_ids = {entry.id for entry in plan.entries if entry.id in plan.annotated_ids}
        STATE.annotated_indices = {index for index, entry in enumerate(plan.entries) if entry.id in plan.annotated_ids}
        _load_current_structure(trust_materialized=True)
        _discard_parked_objects(parked)
        parked = []
        scene_swap_started = False
        background = _panel is not None and not _panel._closed
        state_path = _write_persisted_dataset_state(STATE.dataset_dir, background=background)
        annotated = len(STATE.annotated_indices)
        print(
            f"Structure Annotator: loaded {len(STATE.files)} files from {STATE.dataset_dir} "
            f"({annotated} already annotated for '{normalize_label_key(STATE.label_name)}')"
        )
        if state_path:
            print(f"Structure Annotator: dataset state: {state_path}")
        _record_current_config_history(background=background)
    except Exception:
        try:
            if scene_swap_started:
                _rollback_scene_swap(previous, parked)
            else:
                _restore_runtime_snapshot(previous)
        except Exception:
            traceback.print_exc()
        raise


def sa_load_dir(path: str = ".", pattern: str = "", *, restore_saved: bool = True,
                raise_errors: bool = False) -> None:
    """Synchronously load a folder for console/script callers."""
    try:
        plan = _prepare_directory_load(path, pattern, _capture_csv_runtime_context(), restore_saved=restore_saved)
        _commit_prepared_directory_load(plan)
    except Exception as exc:
        if raise_errors:
            raise
        print(f"Structure Annotator load error: {exc}")
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
        value = str(value).strip()
        if not value:
            print("Structure Annotator: annotation value must not be empty")
            return
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
        complete = _reviewed(_annotation_fields(), STATE.row_annotation_store.annotations_for(entry_id, refresh=False)) if STATE.row_annotation_store is not None else True
        if complete:
            STATE.annotated_indices.add(STATE.index)
            if entry_id:
                STATE.annotated_entry_ids.add(entry_id)
        else:
            STATE.annotated_indices.discard(STATE.index)
            STATE.annotated_entry_ids.discard(entry_id)

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
        if _panel is not None:
            _panel._schedule_state_save()
        else:
            _save_current_dataset_state()

        display_name = entry.display_name if entry is not None else os.path.basename(path)
        print(f"Annotated {display_name}: {label_key}={value}")
        if STATE.auto_advance:
            if _panel is not None:
                _panel._next()
            else:
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
        entry = STATE.undo_history[-1]
        if entry.get("mode") == "batch":
            STATE.row_annotation_store.undo_batch(entry["batch_seq"])
            STATE.undo_history.pop()
            STATE.annotation_generation += 1
            _rescan_annotated_indices(refresh=False)
            if _panel is not None:
                _panel._refresh_status()
            return
        path = entry["path"]
        entry_id = str(entry.get("entry_id", ""))
        label_key = entry["label_key"]
        prev_value = entry["prev_value"]
        index = entry["index"]

        if entry.get("mode") in {"csv", "directory"}:
            if STATE.row_annotation_store is None:
                raise RuntimeError("annotation sidecar store is unavailable")
            change = STATE.row_annotation_store.undo(entry_id=entry_id, label=label_key)
            if change is None:
                raise RuntimeError(
                    "annotation sidecar history no longer contains this change"
                )
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

        # The durable mutation is now committed. Keep the entry available for
        # retry if the store or source-file write above failed.
        STATE.undo_history.pop()

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


def _protected_export_paths() -> Dict[str, str]:
    """Return active source/state/annotation files that export must not replace."""

    protected: Dict[str, str] = {}
    active = _active_source_reference()
    if STATE.source_mode == "csv" and STATE.csv_path:
        protected[STATE.csv_path] = "source CSV"
    if active:
        try:
            state_path = dataset_state_path(active, **_state_kwargs())
            protected[state_path] = "dataset state"
        except Exception:
            pass
    if STATE.row_annotation_store is not None:
        protected[STATE.row_annotation_store.path] = "annotation sidecar"
    return protected


def _validate_export_destination(out_path: str) -> str:
    destination = os.path.abspath(os.path.expanduser(str(out_path)))
    for protected, protected_kind in _protected_export_paths().items():
        same = _path_identity(destination) == _path_identity(protected)
        if not same:
            try:
                same = os.path.samefile(
                    _long_path(destination), _long_path(protected)
                )
            except OSError:
                same = False
        if same:
            raise ValueError(
                f"CSV export refuses to overwrite the active {protected_kind}"
            )
    return destination


def _write_delimited_rows_atomic(
    destination: str,
    header: Sequence[object],
    rows: Sequence[Sequence[object]],
    *,
    encoding: str,
    delimiter: str = ",",
) -> str:
    """Atomically publish one delimited export and preserve an old destination."""

    import csv

    directory = os.path.dirname(destination) or os.curdir
    os.makedirs(_long_path(directory), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".structure_annotations.",
        suffix=".tmp",
        dir=_long_path(directory),
    )
    try:
        with os.fdopen(
            descriptor, "w", newline="", encoding=encoding
        ) as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(header)
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _long_path(destination))
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return destination


def sa_export_csv(out_path: str = "") -> Optional[str]:
    """
    Export all annotations in the current dataset to a CSV file.

    Args:
        out_path: Destination CSV. Defaults to 'annotations_<label>.csv' in the dataset dir.

    Returns the written path, or None on failure.
    """
    try:
        if not STATE.files and not STATE.all_entries:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return None
        label_key = _active_annotation_key()
        if STATE.source_mode == "csv":
            if STATE.dataset is None or STATE.row_annotation_store is None:
                raise RuntimeError("CSV dataset or annotation store is unavailable")
            if not out_path:
                stem = os.path.splitext(os.path.basename(STATE.csv_path))[0]
                out_path = os.path.join(
                    STATE.dataset_dir,
                    f"{stem}_annotated_{label_key}.csv",
                )
            destination = _validate_export_destination(out_path)
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
            labels = [label_key] + sorted({name for values in annotations.values() for name in values}
                                          - {label_key, REVIEW_STATUS_LABEL})
            annotation_indices = {}
            for label in labels:
                name = f"pymol_annotation_{label}"
                if name not in header:
                    header.append(_unique_csv_column(name, used_headers))
                annotation_indices[label] = header.index(name)
            rows = []
            for entry in STATE.dataset.entries:
                values = list(entry.raw_values)
                values.extend([""] * (len(header) - len(values)))
                for label, index in annotation_indices.items():
                    values[index] = annotations.get(entry.id, {}).get(label, "")
                rows.append(values)
            _write_delimited_rows_atomic(
                destination,
                header,
                rows,
                encoding="utf-8-sig",
                delimiter=STATE.dataset.delimiter or ",",
            )
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
        destination = _validate_export_destination(out_path)
        rows = []
        complete_entries = (
            list(STATE.dataset.entries)
            if STATE.dataset is not None
            and STATE.dataset.source_kind == "directory"
            else list(STATE.entries)
        )
        for index, entry in enumerate(complete_entries):
            path = _entry_source_path(entry)
            if STATE.row_annotation_store is not None:
                annotations = STATE.row_annotation_store.annotations_for(entry.id)
            else:
                annotations = (
                    _annotations_for_index(index)
                    if index < len(STATE.files)
                    else read_structure_annotations(path)
                )
            rows.append((os.path.basename(path), annotations, path))

        labels = [label_key] + sorted({name for _filename, values, _path in rows for name in values}
                                      - {label_key, REVIEW_STATUS_LABEL})
        output_rows = [[filename] + [values.get(name, "") for name in labels] + [path]
                       for filename, values, path in rows]

        _write_delimited_rows_atomic(
            destination,
            ["filename"] + labels + ["path"],
            output_rows,
            encoding="utf-8",
        )

        annotated = sum(1 for r in rows if r[1])
        print(
            f"Structure Annotator: exported {len(rows)} rows "
            f"({annotated} annotated) to {destination}"
        )
        return destination
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
    kwargs = (
        _state_kwargs()
        if STATE.source_mode == "csv" and STATE.csv_path
        else {"source_mode": "directory"}
    )
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
        sortable_columns = STATE.dataset.columns if STATE.dataset is not None else tuple(STATE.all_entries[0].metadata)
        if next_sort and next_sort not in set(sortable_columns):
            available = ", ".join(repr(value) for value in sortable_columns)
            raise RuntimeError(
                f"unknown sort column {next_sort!r}; available: {available or '(none)'}"
            )
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
        STATE.sampling = {}
        STATE.filtered_count = len(active_entries)
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


def _prepare_view(entries, query, sort_column, descending, current_id,
                  current_index, materialized, context, *, sampling=None, cancel_event=None):
    """Filter immutable rows and prepare the selected structure off the GUI thread."""
    _check_csv_preparation_cancelled(cancel_event)
    columns = tuple(entries[0].metadata) if entries else ()
    if sort_column and sort_column not in columns:
        raise ValueError(f"Unknown sort column {sort_column!r}; choose a CSV column")
    active = _view_entries(entries, filter_text=query, sort_column=sort_column,
                           sort_descending=bool(descending))
    filtered_count = len(active)
    sampling = dict(sampling or {})
    if sampling.get("enabled"):
        from .sampling import stratified_sample
        active, _ = stratified_sample(active, **{key: sampling[key] for key in
            ("group_column", "metric_column", "per_stratum") if key in sampling})
    selected = None
    prepared = None
    if active:
        selected = active[_resume_entry_index(active, current_id, current_index)]
        prepared = materialized.get(selected.id)
        if prepared is None:
            prepared = _materialize_entry(selected, context)
    _check_csv_preparation_cancelled(cancel_event)
    return {"entries": tuple(active), "selected": selected, "materialized": prepared,
            "sampling": sampling, "filtered_count": filtered_count,
            "query": query, "sort_column": sort_column, "descending": bool(descending),
            "navigation_session": _new_navigation_session(active, _resume_entry_index(active, selected.id, 0)) if selected is not None else None}


def _commit_prepared_view(plan):
    """Apply one filtered view through the existing transactional scene owner."""
    previous = _runtime_snapshot()
    parked = _begin_scene_swap()
    try:
        STATE.filter_text = plan["query"]
        STATE.sort_column = plan["sort_column"]
        STATE.sort_descending = plan["descending"]
        STATE.sampling = plan["sampling"]
        STATE.filtered_count = plan["filtered_count"]
        selected = plan["selected"]
        if selected is not None:
            _install_entries(STATE.all_entries, active_entries=plan["entries"],
                             resume_entry_id=selected.id, navigation_session=plan["navigation_session"])
            STATE.materialized_paths[selected.id] = plan["materialized"]
            _rescan_annotated_indices(refresh=False)
            _load_current_structure(trust_materialized=True)
        else:
            STATE.entries = []
            STATE.files = []
            STATE.dataset_generation += 1
            STATE.navigation_session = None
            STATE.navigation_generation = -1
            STATE.index = 0
            STATE.annotated_indices = set()
            STATE.annotated_entry_ids = set()
    except Exception:
        _rollback_scene_swap(previous, parked)
        raise
    _discard_parked_objects(parked)
    _save_current_dataset_state(background=True)


def _prepare_reference(dataset, entry, column, mapping, context, *, cancel_event=None):
    """Use the candidate's source base and normal resolver/cache for its reference."""
    path = str(entry.metadata.get(column, "") or "").strip()
    if not path:
        raise ValueError(f"No reference path in {column!r} for {entry.display_name}")
    reference = AnnotationEntry(id=entry.id + ":reference", ordinal=0,
        display_name=path, source_path=path, source_kind=entry.source_kind,
        source_base=entry.source_base)
    resolved = _resolve_csv_dataset(replace(dataset, entries=(reference,)),
        cancel_event=cancel_event, read_only_mappings=True, **mapping).entries[0]
    if resolved.error or not resolved.is_navigable:
        raise ValueError(resolved.error or f"Reference unavailable: {path}")
    materialized = _materialize_entry(resolved, context, cancel_event=cancel_event)
    _check_csv_preparation_cancelled(cancel_event)
    return materialized


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
        if _panel is not None and not _panel._closed:
            if _key_lease is not None and _key_lease.active:
                _key_lease.release()
            _panel._enable_qt_shortcuts()
            return {"status": "bound", "mode": "qt", "keys": ["Alt+1..9", "Alt+Left", "Alt+Right", "Alt+Z"]}
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
        if _panel is not None and _panel._qt_shortcuts is not None:
            _panel._qt_shortcuts.set_enabled(False)
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
        progress = QtCore.Signal(str, str)
        materialization_progress = QtCore.Signal(int, str, str)

        def __init__(self):
            super().__init__()
            queued = (
                QtCore.Qt.ConnectionType.QueuedConnection
                if hasattr(QtCore.Qt, "ConnectionType")
                else QtCore.Qt.QueuedConnection
            )
            self.completed.connect(self._dispatch, queued)
            self.progress.connect(self._progress, queued)
            self.materialization_progress.connect(self._materialization_progress, queued)

        @staticmethod
        def _on_main_thread():
            app = QtCore.QCoreApplication.instance()
            return bool(app is not None and QtCore.QThread.currentThread() == app.thread())

        @QtCore.Slot(str, object, object)
        def _dispatch(self, job_id, result, error):
            _complete_csv_async_job(job_id, result, error)

        @QtCore.Slot(str, str)
        def _progress(self, job_id, message):
            job = _CSV_ASYNC_JOBS.get(job_id)
            panel = job["panel"]() if job is not None else None
            if panel is not None and not panel._closed and panel._csv_task_id == job_id:
                panel._last_ssh_progress_message = message
                panel._last_ssh_progress_on_main_thread = self._on_main_thread()
                panel.status_label.setText(message)

        @QtCore.Slot(int, str, str)
        def _materialization_progress(self, generation, entry_id, message):
            panel = _panel
            if panel is None or panel._closed:
                return
            pending = panel._pending_navigation
            if pending is None or pending[:2] != (generation, entry_id):
                return
            panel._last_ssh_progress_message = message
            panel._last_ssh_progress_on_main_thread = self._on_main_thread()
            panel.status_label.setText(message)


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
                with ssh_progress(lambda message: self.bridge.progress.emit(self.job_id, message)):
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
    if worker in (_prepare_csv_load, _prepare_csv_preview, _prepare_csv_header, _prepare_directory_load):
        worker_kwargs["progress"] = lambda message: bridge.progress.emit(job_id, message)
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
        "runnable": runnable,
    }
    _csv_async_pool().start(runnable)
    return job_id


def _cancel_csv_async_job(job_id: str) -> bool:
    job = _CSV_ASYNC_JOBS.pop(str(job_id), None)
    if job is None:
        return False
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
    try:
        panel._complete_csv_task(str(job_id), job, result, error)
    except Exception as exc:
        traceback.print_exc()
        panel._clear_csv_task()
        panel._show_error(f"Could not finish import: {exc}")


def _completion_query(text: str, separator: str = "") -> str:
    """Return the active token used for fuzzy completion."""

    value = str(text)
    if separator:
        value = value.rsplit(separator, 1)[-1]
    return value.strip()


def _replace_completion_token(text: str, completion: str, separator: str = "") -> str:
    """Replace only the active token in a delimited input field."""

    value = str(completion)
    current = str(text)
    if not separator or separator not in current:
        return value
    prefix = current.rsplit(separator, 1)[0].rstrip()
    return prefix + separator + value


class _HistoryCompletionFilter(QtCore.QObject if _HAS_QT_ASYNC else object):
    def __init__(self, binding):
        super().__init__(binding.widget)
        self.binding = binding

    def eventFilter(self, obj, event):
        if event.type() != QtCore.QEvent.KeyPress:
            return super().eventFilter(obj, event)
        popup = self.binding.completer.popup()
        if popup.isVisible():
            if event.key() in (_qt_enum("Key", "Key_Tab"), _qt_enum("Key", "Key_Return"), _qt_enum("Key", "Key_Enter")):
                index = popup.currentIndex()
                if not index.isValid():
                    index = self.binding.completer.completionModel().index(0, 0)
                if index.isValid():
                    self.binding._accept(index.data())
                    return True
        return super().eventFilter(obj, event)


class _HistoryCompletionBinding:
    """Attach dynamically fuzzy-ranked global history to one Qt text widget."""

    def __init__(
        self,
        widget,
        field: str,
        multiline: bool = False,
        separator: str = "",
        suggestions=None,
    ):
        self.widget = widget
        self.field = field
        self.multiline = bool(multiline)
        self.separator = str(separator)
        self.suggestions = suggestions
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
            # Native QLineEdit highlighted() inserts the first history match
            # before the user accepts it, replacing freshly pasted source paths.
            widget.setCompleter(None)
            self.completer.setWidget(widget)
            self._key_filter = _HistoryCompletionFilter(self)
            widget.installEventFilter(self._key_filter)
            self.completer.popup().installEventFilter(self._key_filter)
            widget.textEdited.connect(self.refresh)
        try:
            self.completer.activated[str].connect(self._accept)
        except Exception:
            self.completer.activated.connect(self._accept)

    def _current_text(self) -> str:
        if self.multiline:
            value = str(self.widget.toPlainText())
        else:
            value = str(self.widget.text())
        return _completion_query(value, self.separator)

    def refresh(self, *args) -> None:
        if self._accepting:
            return
        try:
            if not self.widget.hasFocus():
                return
            suggestions = self.suggestions(self._current_text()) if self.suggestions else _history_suggestions(
                self.field, self._current_text(), limit=12)
            self.model.setStringList(suggestions)
            if not suggestions:
                self.completer.popup().hide()
                return
            # The model is already fuzzy-filtered. An empty completion prefix
            # prevents QCompleter from applying a second prefix-only filter.
            self.completer.setCompletionPrefix("")
            if self.multiline:
                rectangle = self.widget.cursorRect()
                width = self.completer.popup().sizeHintForColumn(0)
                width += self.completer.popup().verticalScrollBar().sizeHint().width()
                rectangle.setWidth(max(rectangle.width(), width))
                self.completer.complete(rectangle)
            else:
                self.completer.complete()
            self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))
        except Exception:
            traceback.print_exc()
            self.completer.popup().hide()

    def _accept(self, completion) -> None:
        value = completion
        if not isinstance(value, str) and hasattr(value, "data"):
            value = value.data()
        value = str(value)
        self._accepting = True
        try:
            if self.multiline:
                current = str(self.widget.toPlainText())
                self.widget.setPlainText(
                    _replace_completion_token(current, value, self.separator)
                )
            else:
                current = str(self.widget.text())
                self.widget.setText(
                    _replace_completion_token(current, value, self.separator)
                )
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
        self._headers = ["Source row", "Status", "Structure"]
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
            if entry.id in STATE.materialization_errors:
                return "error"
            if entry.id in STATE.materialized_paths:
                return "available"
            return entry.status
        if column == 2:
            return entry.display_name
        if column == 3:
            return self._annotation_values.get(entry.id, "")
        offset = 4
        if STATE.reference_column:
            if column == offset:
                path = str(entry.metadata.get(STATE.reference_column, "") or "")
                return ntpath.basename(path) if path else "— No reference"
            offset += 1
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
        if self._mode == "active" and role == _qt_enum("ItemDataRole", "ForegroundRole"):
            from pymol.Qt import QtGui
            entry = self._rows[index.row()]
            if index.column() == 4 and STATE.reference_column:
                return QtGui.QColor("#9b5414")
            if index.column() == 3 and self._annotation_values.get(entry.id):
                return QtGui.QColor("#2854ab")
            if index.column() == 1 and (entry.error or entry.id in STATE.materialization_errors):
                return QtGui.QColor("#982e36")
        if role not in (display_role, _qt_enum("ItemDataRole", "ToolTipRole")):
            return None
        raw = self._rows[index.row()]
        if (role == _qt_enum("ItemDataRole", "ToolTipRole") and self._mode == "active"
                and index.column() == 4 and STATE.reference_column):
            return str(raw.metadata.get(STATE.reference_column, "") or "No reference supplied")
        value = (
            self._preview_value(dict(raw), index.column())
            if self._mode == "preview"
            else self._active_value(raw, index.column())
        )
        return str(value if value is not None else "")

    def headerData(self, section: int, orientation, role=None):
        if (
            role in (_qt_enum("ItemDataRole", "DisplayRole"), _qt_enum("ItemDataRole", "ToolTipRole"))
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
            headers=["Source row", "Status", "Structure"] + list(display_columns),
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
        headers = ["Source row", "Status", "Structure"]
        headers.append(label_key.replace("_", " ").capitalize())
        if STATE.reference_column:
            headers.append("Reference structure")
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
            headers=("Source row", "Status", "Structure"),
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
        if self._mode != "active":
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

    def update_annotation_entries(self, values):
        """Update a saved batch without copying the complete annotation mapping."""
        role = _qt_enum("ItemDataRole", "DisplayRole")
        for entry_id, value in values.items():
            previous = self._annotation_values.get(entry_id, "")
            if value:
                self._annotation_values[entry_id] = value
            else:
                self._annotation_values.pop(entry_id, None)
            row = self._entry_id_to_row.get(entry_id)
            if row is not None and previous != (value or ""):
                index = self.index(row, 3)
                self.dataChanged.emit(index, index, [role])


class StructureAnnotatorPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Structure Annotator")
        self.setMinimumWidth(760)
        self.setMinimumHeight(560)
        self.resize(1000, 720)
        self._bucket_inputs: Dict[str, QtWidgets.QPlainTextEdit] = {}
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
        self._source_widgets_csv_path = ""
        self._csv_task_id = ""
        self._csv_task_kind = ""
        self._csv_task_epoch = 0
        self._closed = False
        self._last_csv_completion_on_main_thread = None
        self._last_ssh_progress_message = ""
        self._last_ssh_progress_on_main_thread = None
        self._column_headers = ()
        self._column_bindings = {}
        self._prepared_preview = None
        self._preview_arguments_fingerprint = None
        self._header_csv_path = ""
        self._header_options = None
        self._pending_csv_action = ""
        self._source_columns_explicit = False
        self._source_review_config = {}
        self._pending_navigation = None
        self._filter_empty = False
        self._qt_shortcuts = None
        self._history_restore = None
        self._syncing_widgets = True
        self._reference_object = ""
        self._reference_owner = ""
        self._reference_alignment = None
        self._reference_cache = {}
        self._reference_pairs = {}
        self._reference_source = None
        self._reference_step = None
        self._reference_attempted = False
        self._reference_timer = QtCore.QTimer(self)
        self._reference_timer.setSingleShot(True)
        self._reference_timer.timeout.connect(self._auto_reference)
        self.review = None
        self._state_save_timer = QtCore.QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.timeout.connect(self._flush_state_save)
        self._maintenance_timer = QtCore.QTimer(self)
        self._maintenance_timer.setSingleShot(True)
        self._maintenance_timer.timeout.connect(self._preload_maintenance)
        self._error_poll_timer = QtCore.QTimer(self)
        self._error_poll_timer.timeout.connect(self._poll_background_errors)
        self._build_ui()
        from .review_controller import ReviewController
        self.review = ReviewController(self, sys.modules[__name__])
        self._install_history_completers()
        self._sync_from_state()
        self._rebuild_value_controls(sync_to_state=False)
        self._refresh_status()
        if not STATE.files:
            self.main_tabs.setCurrentIndex(1)
        self._syncing_widgets = False
        self._state_save_timer.stop()
        self._error_poll_timer.start(250)

    def closeEvent(self, event) -> None:
        global _panel
        if self._closed:
            super().closeEvent(event)
            return
        if self._csv_task_kind in {"annotations", "annotation_undo"}:
            self.status_label.setText("Saving annotations; close the panel after saving finishes.")
            event.ignore()
            return
        self._closed = True
        self._reference_timer.stop()
        self._clear_reference_cache()
        self.review.close()
        self._cancel_pending_task(announce=False)
        try:
            self._state_save_timer.stop()
            self._maintenance_timer.stop()
            self._error_poll_timer.stop()
            self._sync_to_state(persist=False)
            self._flush_state_save()
            _record_current_config_history(background=True)
        except Exception:
            traceback.print_exc()
        sa_unbind_keys()
        if self._qt_shortcuts is not None:
            self._qt_shortcuts.set_enabled(False)
        if _panel is self:
            _panel = None
        super().closeEvent(event)
        from pymolplugins.host.workspace import workspace_closed
        workspace_closed(self)

    def _build_ui(self) -> None:
        from .panel_layout import build_panel
        build_panel(self)

    def _show_overview(self):
        if not STATE.all_entries or self._csv_task_id:
            return
        from .sampling_widgets import DatasetOverviewDialog
        filtered = _view_entries(STATE.all_entries)
        dialog = DatasetOverviewDialog(STATE.all_entries, filtered,
            STATE.dataset.columns, STATE.sampling, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._apply_view(sampling=dialog.result_config)

    def _choose_reference_column(self):
        if STATE.dataset is None or STATE.source_mode != "csv" or self._csv_task_id:
            return
        choices = ["(None)"] + list(STATE.dataset.columns)
        current = choices.index(STATE.reference_column) if STATE.reference_column in choices else 0
        column, accepted = QtWidgets.QInputDialog.getItem(self, "Reference structure",
            "Column containing reference paths (same path mapping as candidates):", choices, current, False)
        if accepted:
            self._clear_reference()
            STATE.reference_column = "" if column == "(None)" else column
            self._dataset_table_signature = None
            _save_current_dataset_state(background=True)
            self._refresh_status()

    def _reference_arguments(self):
        entry = STATE.current_entry()
        return {"entry_id": STATE.current_entry_id(), "object": STATE.current_object or "",
                "column": STATE.reference_column, "generation": STATE.dataset_generation,
                "source": STATE.csv_path,
                "path": str(entry.metadata.get(STATE.reference_column, "") or "").strip() if entry else ""}

    def _queue_reference_update(self):
        if self._closed:
            return
        source = (STATE.source_mode, STATE.csv_path, STATE.source_base, STATE.mapping_profile,
                  STATE.mapping_target_format, STATE.mapping_wsl_distribution, STATE.stage_root, STATE.stage_alias)
        if source != self._reference_source:
            self._clear_reference_cache()
            self._reference_source = source
        step = self._reference_arguments()
        if step != self._reference_step:
            self._reference_step = step
            self._reference_attempted = False
            self._clear_reference()
            if self._csv_task_kind == "reference":
                self._cancel_csv_task(announce=False)
        # Defer until the candidate transaction and its UI update have finished.
        self._reference_timer.start(0)

    def _auto_reference(self):
        if self._closed or self._csv_task_id or self._reference_attempted:
            return
        if (STATE.source_mode == "csv" and STATE.dataset is not None and STATE.reference_column
                and STATE.current_object and self._reference_arguments()["path"]):
            self._compare_reference()

    def _reference_key(self):
        entry = STATE.current_entry()
        return (entry.source_base if entry else "", self._reference_arguments()["path"])

    def _compare_reference(self):
        if self._reference_object:
            self._reference_attempted = True
            self._clear_reference()
            self._refresh_status()
            return
        if (self._csv_task_id or STATE.current_entry() is None
                or not STATE.current_object or not self._reference_arguments()["path"]):
            return
        self._reference_attempted = True
        name = self._reference_cache.get(self._reference_key())
        if name and _object_exists(name):
            try:
                self._show_reference(name)
            except Exception as exc:
                self._show_error(f"Reference comparison failed: {exc}")
                traceback.print_exc()
            return
        self._start_csv_task("reference", _prepare_reference, self._reference_arguments(),
            worker_arguments={"dataset": STATE.dataset, "entry": STATE.current_entry(),
                "column": STATE.reference_column, "context": _materialization_context(),
                "mapping": {"mapping_profile": STATE.mapping_profile,
                    "target_format": STATE.mapping_target_format,
                    "wsl_distribution": STATE.mapping_wsl_distribution,
                    "stage_root": STATE.stage_root, "stage_alias": STATE.stage_alias}})

    def _clear_reference(self):
        if self._reference_object:
            cmd.disable(self._reference_object)
        self._reference_object = ""
        self._reference_owner = ""
        self._reference_alignment = None

    def _clear_reference_cache(self):
        for name in self._reference_cache.values():
            cmd.delete(name)
        self._reference_cache.clear()
        self._reference_pairs.clear()
        self._reference_object = ""
        self._reference_owner = ""
        self._reference_alignment = None

    def _invalidate_reference_candidate(self, name):
        self._reference_pairs = {key: value for key, value in self._reference_pairs.items()
                                 if key[1] != name}
        if self._reference_owner == name:
            self._clear_reference()
            self._reference_attempted = False

    def _show_reference(self, name):
        candidate = STATE.current_object
        key = (STATE.dataset_generation, candidate)
        previous = self._reference_pairs.get(key)
        matrix = tuple(cmd.get_object_matrix(candidate))
        if previous is not None and previous[:2] == (name, matrix):
            alignment = previous[2]
        else:
            # Keep the reference in its input frame; transform only the display candidate.
            alignment = cmd.align(candidate, name, quiet=1)
            self._reference_pairs[key] = (name, tuple(cmd.get_object_matrix(candidate)), alignment)
        cmd.enable(name)
        cmd.zoom(f"({candidate}) or ({name})", buffer=2, animate=0)
        self._reference_object = name
        self._reference_owner = candidate
        self._reference_alignment = alignment
        self._refresh_status()

    def _accept_reference(self, materialized):
        basename = ntpath.basename(self._reference_arguments()["path"])
        label = _sanitize_token(basename, "reference")[:48]
        name = "sa_reference_" + label + "_" + uuid.uuid4().hex[:8]
        try:
            cmd.load(_long_path(materialized.path), name, quiet=1, zoom=0)
            if cmd.count_atoms(name) == 0:
                raise ValueError("Reference contains no atoms")
            cmd.color("orange", name)
            cmd.show("cartoon", name)
            cmd.disable(name)
        except Exception:
            cmd.delete(name)
            raise
        self._reference_cache[self._reference_key()] = name
        self._show_reference(name)

    def _page_changed(self, *args):
        index = self.main_tabs.currentIndex()
        source = STATE.csv_path if STATE.source_mode == "csv" else _active_dataset_directory()
        name_path = parse_ssh_path(source).path if is_ssh_path(source) else source
        title = os.path.basename(name_path or "") if index == 0 else "Open data" if index == 1 else "Settings"
        self.active_source_label.setText(title or "Structure Annotator")
        self.active_source_label.setToolTip(source or "")
        self.back_button.setVisible(index == 2 or (index == 1 and STATE.dataset is not None))
        self.import_button.setVisible(index != 1)
        self.settings_button.setVisible(index != 2)
        self.export_action.setEnabled(STATE.dataset is not None)
        self.handoff_action.setEnabled(STATE.dataset is not None)
        if hasattr(self, "resume_button"):
            try:
                has_history = latest_config_snapshot() is not None
            except Exception:
                has_history = False
            self.resume_button.setVisible(has_history)
            self.resume_button.setEnabled(has_history and not self._csv_task_id)
            self.history_button.setVisible(has_history)
        if self.review is not None and self.review.form is not None:
            self.review._refresh_actions()
        if self._qt_shortcuts is not None:
            self._qt_shortcuts.set_enabled(index == 0)

    def _back_to_review(self):
        self.main_tabs.setCurrentIndex(0 if STATE.dataset is not None else 1)

    def _show_filters(self):
        self._refresh_filter_presets()
        self.filter_error_label.hide()
        self.filter_dialog.show()
        self.filter_dialog.raise_()
        self.filter_edit.setFocus()

    def _apply_filters(self):
        try:
            if self.filter_mode_combo.currentIndex() == 1 and STATE.dataset is not None:
                from .filtering import compile_filter
                compile_filter(self.filter_edit.text(), STATE.dataset.columns)
        except ValueError as exc:
            self.filter_error_label.setText(str(exc))
            self.filter_error_label.show()
            return
        self.filter_dialog.accept()
        self._apply_view()

    def _refresh_filter_presets(self, preferred=None):
        table = STATE.source_mode == "csv" and STATE.dataset is not None
        self.filter_group_row.setVisible(table)
        self.filter_group_label.setVisible(table)
        query = self.filter_edit.text().strip()
        self.filter_preset_combo.blockSignals(True)
        self.filter_preset_combo.clear()
        self.filter_preset_combo.addItem("Custom", "")
        selected = 0
        for index, preset in enumerate(STATE.filter_presets, 1):
            self.filter_preset_combo.addItem(preset["name"], preset["name"])
            match = (self.filter_mode_combo.currentIndex() == 1 and query == preset["expression"]
                     and self.sort_column_combo.currentText() == preset["sort_column"]
                     and self.sort_desc_cb.isChecked() == preset["sort_descending"])
            if preset["name"] == preferred or (preferred is None and match):
                selected = index
        self.filter_preset_combo.setCurrentIndex(selected)
        self.filter_preset_combo.blockSignals(False)
        self.filter_delete_button.setEnabled(bool(selected))
        self.filter_save_button.setEnabled(table and self.filter_mode_combo.currentIndex() == 1)
        self._refresh_filter_button()

    def _refresh_filter_button(self):
        active = next((p for p in STATE.filter_presets
                       if STATE.filter_text == "where:" + p["expression"]
                       and STATE.sort_column == p["sort_column"]
                       and STATE.sort_descending == p["sort_descending"]), None)
        title = active["name"] if active else "Filter •" if STATE.filter_text or STATE.sort_column else "Filter"
        self.filter_button.setText(self.filter_button.fontMetrics().elidedText(title, QtCore.Qt.ElideRight, 100))
        self.filter_button.setAccessibleName("Filter: " + title)
        self.filter_button.setToolTip((active["name"] + "\n" + active["expression"]) if active else "Edit filters")

    def _select_filter_preset(self, *_args):
        name = self.filter_preset_combo.currentData()
        preset = next((value for value in STATE.filter_presets if value["name"] == name), None)
        self.filter_delete_button.setEnabled(preset is not None)
        if preset is not None:
            self.filter_mode_combo.setCurrentIndex(1)
            self.filter_edit.setText(preset["expression"])
            self.sort_column_combo.setCurrentText(preset["sort_column"])
            self.sort_desc_cb.setChecked(preset["sort_descending"])
        self.filter_error_label.hide()

    def _save_filter_preset(self):
        from .filtering import validate_filter_presets
        if STATE.dataset is None or STATE.source_mode != "csv":
            return
        name, accepted = QtWidgets.QInputDialog.getText(self.filter_dialog, "Save filter", "Name",
                                                       text=self.filter_preset_combo.currentData() or "")
        if not accepted:
            return
        candidate = {"name": name, "expression": self.filter_edit.text(),
                     "sort_column": self.sort_column_combo.currentText(),
                     "sort_descending": self.sort_desc_cb.isChecked()}
        try:
            validated = validate_filter_presets([candidate], STATE.dataset.columns)[0]
        except ValueError as exc:
            self.filter_error_label.setText(str(exc))
            self.filter_error_label.show()
            return
        updated = [validated if value["name"] == validated["name"] else value for value in STATE.filter_presets]
        if not any(value["name"] == validated["name"] for value in STATE.filter_presets):
            updated.append(validated)
        STATE.filter_presets = updated
        _save_current_dataset_state(background=True)
        self._refresh_filter_presets(preferred=validated["name"])
        self.filter_error_label.hide()

    def _delete_filter_preset(self):
        name = self.filter_preset_combo.currentData()
        if not name:
            return
        answer = QtWidgets.QMessageBox.question(self.filter_dialog, "Delete filter?", name,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer == QtWidgets.QMessageBox.Yes:
            STATE.filter_presets = [value for value in STATE.filter_presets if value["name"] != name]
            _save_current_dataset_state(background=True)
            self._refresh_filter_presets()

    def _filter_menu_about_to_show(self):
        self.filter_menu.clear()
        self.filter_menu.addAction("All candidates", self._clear_filters)
        self.filter_menu.addSeparator()
        for preset in STATE.filter_presets:
            action = self.filter_menu.addAction(preset["name"])
            action.setToolTip(preset["expression"])
            action.setCheckable(True)
            action.setChecked(STATE.filter_text == "where:" + preset["expression"]
                              and STATE.sort_column == preset["sort_column"]
                              and STATE.sort_descending == preset["sort_descending"])
            action.triggered.connect(lambda _checked=False, name=preset["name"]: self._apply_filter_preset(name))
        self.filter_menu.addSeparator()
        self.filter_menu.addAction("Edit filters…", self._show_filters)

    def _apply_filter_preset(self, name):
        self._refresh_filter_presets(preferred=name)
        self._select_filter_preset()
        self._apply_filters()

    def _clear_filters(self):
        self.filter_dialog.accept()
        self._clear_view()

    def _show_details(self, checked):
        if checked:
            self._refresh_metadata_table()
            self.details_dialog.show()
            self.details_dialog.raise_()
        else:
            self.details_dialog.hide()

    def _choose_position(self):
        if not STATE.entries:
            return
        position, accepted = QtWidgets.QInputDialog.getInt(self, "Go to candidate", "Candidate", STATE.index + 1, 1, len(STATE.entries))
        if accepted:
            self._request_navigation(position - 1)

    def _import_inputs_changed(self, *args):
        if self._csv_task_id:
            return
        self.source_open_button.setEnabled(bool(self.source_path_edit.text().strip()))

    @staticmethod
    def _detected_source_mode(path):
        if is_ssh_path(path):
            path = path.split("?", 1)[0]
        return "csv" if path.lower().endswith((".csv", ".tsv", ".tab", ".txt")) else "directory"

    def _set_source_type_for_path(self, path, mode):
        choice = "auto" if self._detected_source_mode(path) == mode else mode
        self.source_type_combo.setCurrentIndex(self.source_type_combo.findData(choice))

    def _update_source_mode(self):
        selected = self.source_type_combo.currentData()
        if selected == "auto":
            selected = self._detected_source_mode(self.source_path_edit.text().strip())
        self.source_mode_combo.setCurrentIndex(self.source_mode_combo.findData(selected))

    def _source_path_changed(self, *args):
        if self._syncing_widgets:
            return
        self._update_source_mode()
        self._header_csv_path = ""
        self._prepared_preview = None
        self.csv_columns_section.hide()
        self._import_inputs_changed()

    def _source_type_changed(self, *args):
        self._source_path_changed()

    def _source_path_edited(self):
        self._update_source_mode()
        if self._source_mode() == "csv":
            self._csv_path_edited()

    def _source_location_changed(self):
        if not hasattr(self, "csv_edit") or self._syncing_widgets:
            return
        if self._csv_task_id:
            self._cancel_csv_task(announce=False)
        self._header_csv_path = ""
        self._source_widgets_csv_path = ""
        self._prepared_preview = None
        self.local_mapping_group.setVisible(not self.source_location.remote)
        self.source_path_edit.setPlaceholderText("Remote folder or CSV / TSV path" if self.source_location.remote else "Folder or CSV / TSV file")

    def _source_access_ready(self, path=None):
        try:
            path = self.source_path_edit.text().strip() if path is None else path
            self.source_location.source(path or "~")
            if (os.name == "nt" and not self.source_location.remote
                    and path.startswith("/") and not path.startswith("//")):
                raise ValueError("This is a Linux path. Choose its SSH server in Open from, or enter a Windows drive path.")
        except ValueError as exc:
            self._show_error(str(exc))
            return False
        return True

    def _source_history_suggestions(self, field, query):
        from .history_store import fuzzy_matches
        fields = (field,) if field else ("csv_path", "dataset_dir")
        values = [value for name in fields for value in _history_suggestions(name, "", limit=100)]
        if self.source_location.remote:
            profile = self.source_location.profile.currentText().strip()
            config = self.source_location.config.text().strip()
            config = os.path.abspath(os.path.expanduser(config)) if config else ""
            program = self.source_location.program.text().strip()
            program = os.path.abspath(os.path.expanduser(program)) if program else ""
            values = [parse_ssh_path(value).path for value in values if is_ssh_path(value)
                      and (parse_ssh_path(value).profile, parse_ssh_path(value).config, parse_ssh_path(value).program) == (profile, config, program)]
        else:
            values = [value for value in values if not is_ssh_path(value)]
        return fuzzy_matches(values, query, limit=12)

    def _cancel_pending_task(self, *, announce=True):
        self._pending_csv_action = ""
        if self._csv_task_id:
            self._cancel_csv_task(announce=announce)
            return
        if self._pending_navigation is None:
            return
        global _preload_queue
        record = self._pending_navigation
        self._pending_navigation = None
        _cancel_materialization_record(record)
        _preload_queue = [value for value in _preload_queue if value != record]
        self._source_mode_changed()
        self.review.refresh()
        if announce and not self._closed:
            self.status_label.setText(
                "Structure preparation cancelled; current candidate unchanged."
            )

    def _refresh_mapping_profiles(self, selected=None):
        from .mapping_widgets import profiles
        selected = selected or self.mapping_profile_combo.currentText() or "custom"
        self.mapping_profile_combo.blockSignals(True)
        try:
            self.mapping_profile_combo.clear()
            self.mapping_profile_combo.addItems([value["name"] for value in profiles()])
            if self.mapping_profile_combo.findText(selected) < 0:
                self.mapping_profile_combo.addItem(selected)
            self.mapping_profile_combo.setCurrentText(selected)
        finally:
            self.mapping_profile_combo.blockSignals(False)
        self._mapping_profile_changed()

    def _mapping_profile_changed(self, *args):
        if not hasattr(self, "target_format_combo"):
            return
        from .mapping_widgets import set_format
        try:
            profile = _mapping_profile(self.mapping_profile_combo.currentText())
            set_format(self.target_format_combo, profile.get("target_format", "windows"))
            self.wsl_distribution_edit.setText(profile.get("wsl_distribution", ""))
            self._mapping_format_changed()
        except Exception as exc:
            traceback.print_exc()
            self._show_error(str(exc))

    def _mapping_format_changed(self, *args):
        if not hasattr(self, "wsl_distribution_row"):
            return
        try:
            profile = _mapping_profile(self.mapping_profile_combo.currentText())
            source = profile.get("source_format", "custom")
            target = self.target_format_combo.currentData()
            self.wsl_distribution_row.setVisible(source == "wsl" or target == "wsl")
            source = {"custom": "Custom", "windows": "Windows", "posix": "Linux / POSIX", "wsl": "WSL"}[source]
            self.mapping_summary_label.setText("%s server paths · %d text rewrites · %d root mappings. Applies when you load the CSV." %
                                               (source, len(profile.get("rewrites", [])), len(profile["rules"])))
            name = profile["name"]
            self.mapping_section.toggle.setText("File locations" if name == "custom" else "File locations · " + name)
            self._prepared_preview = None
        except Exception as exc:
            traceback.print_exc()
            self._show_error(str(exc))

    def _mapping_access_changed(self, *args):
        if hasattr(self, "worker_access_group"):
            self.worker_access_group.setVisible(self.access_method_combo.currentData() == "worker")

    def _csv_access_ready(self):
        if not self._source_access_ready():
            return False
        if self.source_location.remote:
            return True
        if self.access_method_combo.currentData() == "worker" and not (self.stage_root_edit.text().strip() and self.stage_alias_edit.text().strip()):
            self._show_error("Worker file transfer requires a server root and worker root alias.")
            return False
        return True

    def _edit_mapping_profiles(self):
        from .mapping_widgets import MappingProfileDialog
        try:
            entry = STATE.current_entry()
            dialog = MappingProfileDialog(self, selected=self.mapping_profile_combo.currentText(),
                                          sample=entry.source_path if entry else "", source_base=self.source_base_edit.text())
            if dialog.exec_():
                self._refresh_mapping_profiles(dialog.saved_name)
        except Exception as exc:
            traceback.print_exc()
            self._show_error(str(exc))

    def _show_import(self) -> None:
        self.main_tabs.setCurrentIndex(1)
        (self.csv_edit if self._source_mode() == "csv" else self.dir_edit).setFocus()

    def _show_configuration_history(self) -> None:
        from .history_browser import HistoryRestoreDialog
        try:
            dialog = HistoryRestoreDialog(self)
            run = getattr(dialog, "exec", None) or dialog.exec_
            if run() == QtWidgets.QDialog.Accepted:
                self._restore_history_snapshot(dialog.snapshot, load_dataset=dialog.load_dataset)
        except Exception as exc:
            traceback.print_exc()
            self._show_error(f"Configuration history: {exc}")

    def _continue_last_review(self) -> None:
        """Explicitly resume the latest source and saved cursor, without auto-loading."""
        try:
            snapshot = latest_config_snapshot()
        except Exception as exc:
            traceback.print_exc()
            self._show_error(f"Could not find the last review: {exc}")
            return
        if not snapshot:
            self.status_label.setText(
                "No saved review yet. Open a folder or CSV/TSV to start one."
            )
            return
        try:
            self._restore_history_snapshot(snapshot, load_dataset=True)
        except Exception as exc:
            traceback.print_exc()
            self._show_error(
                "Could not resume the last review. Check the source path or open "
                "Recent configurations… to choose another source.\n" + str(exc)
            )

    def _restore_history_snapshot(self, snapshot, *, load_dataset=False) -> None:
        """Apply an explicitly chosen workflow; source identity changes only on load."""
        raw = snapshot["config"]
        config = _validate_persisted_config(raw)
        workflow = {key: value for key, value in config.items()
                    if key not in _PERSISTED_SOURCE_STRING_FIELDS and key != "display_columns"}
        workflow["restore_last_dataset"] = False
        workflow.setdefault("annotation_fields", [])
        workflow.setdefault("score_guidance", {})
        self._syncing_widgets = True
        self._state_save_timer.stop()
        try:
            _apply_persisted_config(workflow)
            self._sync_from_state()
            self._rebuild_value_controls(sync_to_state=False)
            self._history_restore = copy.deepcopy(snapshot)
            self._set_history_source_inputs(snapshot, config)
            if STATE.files:
                _rescan_annotated_indices()
            self._refresh_status()
        finally:
            self._syncing_widgets = False
        self.error_label.hide()
        self._show_import()
        self.status_label.setText("Configuration restored. Use Load to open the selected source; the current structure is unchanged.")
        if load_dataset:
            self._load_dataset()

    def _set_history_source_inputs(self, snapshot, config):
        mode = snapshot["source_mode"]
        selected_source = snapshot.get("csv_path" if mode == "csv" else "dataset_dir", "")
        self.source_location.set_source(selected_source)
        self.source_mode_combo.setCurrentIndex(self.source_mode_combo.findData(mode))
        directory, table = snapshot.get("dataset_dir", ""), snapshot.get("csv_path", "")
        chosen = table if mode == "csv" else directory
        self._set_source_type_for_path(chosen, mode)
        self.source_path_edit.setText(parse_ssh_path(chosen).path if is_ssh_path(chosen) else chosen)
        self.local_mapping_group.setVisible(not self.source_location.remote)
        for name, key, default in (
            ("path_column_edit", "path_column", "structure_path"),
            ("id_column_edit", "id_column", ""),
            ("label_column_edit", "label_column", ""),
            ("source_base_edit", "source_base", ""),
            ("stage_root_edit", "stage_root", ""),
            ("stage_alias_edit", "stage_alias", ""),
        ):
            getattr(self, name).setText(str(config.get(key, default)))
        for name, key, default in (
            ("encoding_combo", "csv_encoding", DEFAULT_ENCODING),
            ("delimiter_combo", "csv_delimiter", AUTO_DELIMITER),
            ("mapping_profile_combo", "mapping_profile", DEFAULT_PROFILE_NAME),
        ):
            getattr(self, name).setCurrentText(str(config.get(key, default)))
        from .mapping_widgets import set_format
        if config.get("mapping_target_format"):
            set_format(self.target_format_combo, config["mapping_target_format"])
        self.wsl_distribution_edit.setText(config.get("mapping_wsl_distribution", ""))
        self.access_method_combo.setCurrentIndex(1 if config.get("stage_root") or config.get("stage_alias") else 0)
        self.display_columns_edit.setText(json.dumps(config.get("display_columns", []), ensure_ascii=False))
        self._source_columns_explicit = bool(config.get("display_columns_selected") or config.get("display_columns"))
        self._header_csv_path = ""
        self._prepared_preview = None
        self._source_widgets_csv_path = table
        self.header_status_label.setText("Saved columns are ready. Column names will be checked when you load.")

    def _install_history_completers(self) -> None:
        from .qt_widgets import ColumnCompletion, CommandCompletion
        self._refresh_mapping_profiles("custom" if not STATE.files else STATE.mapping_profile)
        self._history_bindings = [
            _HistoryCompletionBinding(self.source_path_edit, "csv_path", suggestions=lambda query: self._source_history_suggestions(None, query)),
            _HistoryCompletionBinding(
                self.encoding_combo.lineEdit(), "csv_encoding"
            ),
            _HistoryCompletionBinding(
                self.delimiter_combo.lineEdit(), "csv_delimiter"
            ),
            _HistoryCompletionBinding(self.source_base_edit, "source_base"),
            _HistoryCompletionBinding(self.stage_root_edit, "stage_root"),
            _HistoryCompletionBinding(self.stage_alias_edit, "stage_alias"),
            _HistoryCompletionBinding(self.pattern_edit, "pattern"),
            _HistoryCompletionBinding(self.label_edit, "label_name"),
            _HistoryCompletionBinding(self.values_edit, "values_text"),
        ]
        self._column_bindings = {
            "path": ColumnCompletion(self.path_column_edit),
            "id": ColumnCompletion(self.id_column_edit),
            "label": ColumnCompletion(self.label_column_edit),
            "sort": ColumnCompletion(self.sort_column_combo.lineEdit()),
            "filter": ColumnCompletion(self.filter_edit, expression=True),
        }
        self._column_bindings["filter"].enabled = False
        self._command_completion = CommandCompletion(self.step_cmd_edit, multiline=True)

    def _show_error(self, message):
        text = str(message)
        self._error_details = text
        summary = text
        actions = ""
        if "Permission denied" in text and "SSH" in text:
            summary = "SSH login failed. Check the login program in Connections."
            actions = ' <a href="connections">Open Connections…</a>'
        elif any(token in text.casefold() for token in ("no such file", "not found", "path")):
            actions = ' <a href="source">Open data</a>'
        elif len(text) > 350:
            summary = next((line for line in reversed(text.splitlines()) if line.strip()), text)
            if len(summary) > 350:
                summary = summary[:347] + "…"
        self.error_label.setText(html.escape(summary) + actions + ' <a href="details">Details…</a>')
        self.error_label.setToolTip(text)
        self.error_label.show()
        if self.main_tabs.currentIndex() == 1 and any(word in text.casefold() for word in ("mapping", "resolvable", "readable structure")):
            self.source_options_section.toggle.setChecked(True)
            self.mapping_section.toggle.setChecked(True)

    def _show_error_details(self, *args):
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Import or review error")
        dialog.setIcon(QtWidgets.QMessageBox.Critical)
        dialog.setText("The operation did not complete.")
        dialog.setDetailedText(self._error_details)
        dialog.exec_()

    def _error_link(self, link):
        if link == "connections":
            self.source_location._configure()
        elif link == "source":
            self._show_import()
        else:
            self._show_error_details(link)

    def _poll_background_errors(self):
        for message in pending_errors():
            self._show_error(f"Could not save settings: {message}")

    def _set_column_catalog(self, columns, selected=()):
        self._column_headers = tuple(columns)
        for name in ("path", "id", "label"):
            self._column_bindings[name].set_columns(columns)
        self.source_column_picker.set_columns(columns, selected)
        self.header_status_label.setText(f"{len(columns)} columns available. Type to search column names.")
        self.csv_columns_section.setVisible(bool(columns))
        if not STATE.files:
            self.column_picker.set_columns(columns, selected)

    def _source_columns_selected(self, columns):
        self._source_columns_explicit = True
        self.display_columns_edit.setText(json.dumps(list(columns), ensure_ascii=False))

    def _columns_selected(self, columns):
        if self._table_mode == "preview":
            self._source_columns_selected(columns)
            if self._prepared_preview is not None:
                payload = dict(self._prepared_preview.payload)
                payload["display_columns"] = list(columns)
                self._populate_preview_table(payload)
            return
        STATE.display_columns = list(columns)
        STATE.display_columns_selected = True
        if STATE.csv_path and _path_identity(self.source_location.source(self.csv_edit.text())) == _path_identity(STATE.csv_path):
            self._source_columns_selected(columns)
            self.source_column_picker.set_columns(self._column_headers, columns)
        self._refresh_dataset_table()
        self._refresh_metadata_table()
        self.review.refresh()
        self._schedule_state_save()

    def _filter_mode_changed(self, *args):
        conditions = self.filter_mode_combo.currentIndex() == 1
        if hasattr(self, "filter_save_button"):
            self.filter_save_button.setEnabled(conditions and STATE.source_mode == "csv" and STATE.dataset is not None)
        self.filter_edit.setPlaceholderText('For example: AC.iptm >= 0.8 and full_pass == True' if conditions else "Search row values")
        if "filter" in self._column_bindings:
            self._column_bindings["filter"].enabled = conditions

    def _clear_view(self):
        self.filter_edit.clear()
        self.sort_column_combo.setCurrentText("")
        self.sort_desc_cb.setChecked(False)
        self._apply_view()

    def _show_command_history(self):
        menu = QtWidgets.QMenu(self)
        for command in _history_suggestions("step_cmd", "", limit=20):
            menu.addAction(command, lambda value=command: self.step_cmd_edit.setPlainText(value))
        if not menu.actions():
            action = menu.addAction("No saved commands")
            action.setEnabled(False)
        menu.exec_(self.command_history_btn.mapToGlobal(QtCore.QPoint(0, self.command_history_btn.height())))

    def _annotate_digit(self, digit):
        self.review.quick_digit(digit)

    def _show_batch_processing(self):
        from pymolplugins.targets.batch_sources import BatchConfig
        from pymolplugins.targets.batch_widgets import show_batch_dialog
        initial = BatchConfig(
            a=STATE.csv_path if STATE.source_mode == "csv" else STATE.dataset_dir,
            b=STATE.reference_column if STATE.source_mode == "csv" else "",
            mode="paired" if STATE.source_mode == "csv" and STATE.reference_column else "reference",
            source="csv" if STATE.source_mode == "csv" else "files",
            a_column=STATE.path_column or "structure_file", id_column=STATE.id_column,
            source_base=STATE.source_base, mapping_profile=STATE.mapping_profile or "custom",
            target_format=STATE.mapping_target_format or "custom",
            wsl_distribution=STATE.mapping_wsl_distribution,
            encoding=STATE.csv_encoding, delimiter=STATE.csv_delimiter)
        dialog = show_batch_dialog(initial)
        dialog.source_scope_label.setText(f"Source scope: {len(STATE.all_entries):,} rows. "
            "Review sampling / filters are NOT transferred. Choose IDs and preview before running.")
        dialog.source_scope_label.show()
        return dialog

    def _enable_qt_shortcuts(self):
        from .qt_widgets import AnnotationShortcuts
        if self._qt_shortcuts is None:
            self._qt_shortcuts = AnnotationShortcuts(self)
        self._qt_shortcuts.set_enabled(self.main_tabs.currentIndex() == 0)

    def _flush_state_save(self):
        self._sync_to_state(update_directory=False, persist=False)
        _save_current_dataset_state(background=True)

    def _queue_preload_maintenance(self):
        if not self._closed:
            self._queue_reference_update()
            self._maintenance_timer.start(0)

    def _preload_maintenance(self):
        if self._closed:
            return
        _evict_unneeded_objects()
        _kick_preload()

    def _request_navigation(self, index):
        try:
            self._request_navigation_impl(index)
        except Exception as exc:
            self._pending_navigation = None
            self._source_mode_changed()
            traceback.print_exc()
            self._refresh_status()
            self._show_error(f"Could not switch structure: {exc}")

    def _request_navigation_impl(self, index):
        global _preload_queue
        if self._csv_task_kind in {"annotations", "annotation_undo"}:
            return
        if not STATE.entries or self._filter_empty or self._table_mode == "preview":
            self._show_error("Load a dataset with matching rows before navigating.")
            return
        self.error_label.hide()
        self.review.selection.clear()
        self._sync_to_state(update_directory=False, persist=False)
        index %= len(STATE.entries)
        if self._pending_navigation is not None:
            previous = self._pending_navigation
            _cancel_materialization_record(previous)
            _preload_queue = [value for value in _preload_queue if value != previous]
        target = _object_name_for_index(STATE.files[index], index)
        if _object_exists(target):
            self._pending_navigation = None
            self._source_mode_changed()
            _navigate_annotation("goto", index)
            self._refresh_status()
            return
        entry = STATE.entries[index]
        self._pending_navigation = (_materialization_generation, entry.id, index)
        _preload_queue = [self._pending_navigation] + [value for value in _preload_queue if value != self._pending_navigation]
        _materialize_index(index, allow_background=True, trust_cached=True)
        self._source_mode_changed()
        self.status_label.setText(f"Preparing structure {index + 1}: {entry.display_name}…")
        self.review.refresh()
        _schedule_preload_tick()

    def _finish_navigation(self, record):
        if self._pending_navigation != record:
            return
        self._pending_navigation = None
        self._source_mode_changed()
        _navigate_annotation("goto", record[2])
        self._refresh_status()

    def _browse_dir(self) -> None:
        if self.source_location.remote:
            try:
                directory = self.source_location.browse(self.dir_edit.text(), directory=True)
                if directory:
                    self._set_source_type_for_path(directory, "directory")
                    self.dir_edit.setText(directory)
            except Exception as exc:
                traceback.print_exc()
                self._show_error(str(exc))
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Dataset Directory", self.dir_edit.text() or "."
        )
        if directory:
            self._set_source_type_for_path(directory, "directory")
            self.dir_edit.setText(directory)

    def _browse_csv(self) -> None:
        if self.source_location.remote:
            try:
                filename = self.source_location.browse(self.csv_edit.text(), initial_directory=self._source_mode() == "directory")
                if filename:
                    self._set_source_type_for_path(filename, "csv")
                    self.csv_edit.setText(filename)
                    self._set_csv_source_widgets(filename)
            except Exception as exc:
                traceback.print_exc()
                self._show_error(str(exc))
            return
        filename, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Structure Score Table",
            self.csv_edit.text() or STATE.dataset_dir or ".",
            "Delimited tables (*.csv *.tsv *.tab *.txt);;CSV (*.csv);;TSV (*.tsv *.tab);;All files (*)",
        )
        if filename:
            self._set_source_type_for_path(filename, "csv")
            self.csv_edit.setText(filename)
            self._set_csv_source_widgets(filename)

    def _set_csv_source_widgets(self, csv_path: str, *, preserve_fields=False) -> None:
        """Read source headers/state in a worker; never probe a remote path here."""
        if not self._source_access_ready():
            self._pending_csv_action = ""
            return
        csv_path = self.source_location.source(csv_path)
        if self._csv_task_id:
            self._cancel_csv_task(announce=False)
        self._source_widgets_csv_path = str(csv_path)
        self._prepared_preview = None
        self._header_csv_path = ""
        self.csv_columns_section.hide()
        if not preserve_fields:
            self._source_columns_explicit = False
            self._source_review_config = {}
            self.path_column_edit.clear()
            self.id_column_edit.clear()
            self.label_column_edit.clear()
            self.display_columns_edit.setText("[]")
            self.encoding_combo.setCurrentText(DEFAULT_ENCODING)
            self.delimiter_combo.setCurrentText(AUTO_DELIMITER)
            self.source_base_edit.clear()
            self.stage_root_edit.clear()
            self.stage_alias_edit.clear()
            self.access_method_combo.setCurrentIndex(0)
        arguments = self._csv_arguments()
        self._start_csv_task("headers", _prepare_csv_header, arguments,
                             worker_arguments=dict({key: arguments[key] for key in ("csv_path", "encoding", "delimiter")}, prefer_saved=not preserve_fields))

    def _accept_csv_header(self, result):
        columns = tuple(result["columns"])
        saved = result.get("saved_state") or {}
        source = dict(saved.get("csv") or {})
        self._source_review_config = dict(result.get("review_config") or {})
        source.update(self._source_review_config)
        self.encoding_combo.setCurrentText(str(result["encoding"]))
        self.delimiter_combo.setCurrentText(str(result["input_delimiter"]))
        path_column = self.path_column_edit.text() or source.get("path_column", "")
        if path_column not in columns:
            path_column = next((name for name in ("structure_path", "structure_file", "pdb_path", "file_path") if name in columns), "")
        self.path_column_edit.setText(path_column)
        self.id_column_edit.setText(self.id_column_edit.text() or str(source.get("id_column") or ""))
        self.label_column_edit.setText(self.label_column_edit.text() or str(source.get("label_column") or (saved.get("config") or {}).get("label_column") or ""))
        chosen = _display_column_names(self.display_columns_edit.text()) or []
        saved_columns = list(source.get("display_columns") or [])
        explicit = bool((saved.get("config") or {}).get("display_columns_selected"))
        explicit = explicit or "display_columns" in self._source_review_config
        if not chosen and saved_columns and (explicit or len(saved_columns) < len(columns) - 1):
            chosen = [name for name in saved_columns if name in columns]
        self.display_columns_edit.setText(json.dumps(chosen, ensure_ascii=False))
        self._source_columns_explicit = self._source_columns_explicit or explicit or bool(chosen)
        self.source_base_edit.setText(self.source_base_edit.text() or _saved_csv_source_base(saved))
        self.stage_root_edit.setText(self.stage_root_edit.text() or str(source.get("stage_root") or ""))
        self.stage_alias_edit.setText(self.stage_alias_edit.text() or str(source.get("stage_alias") or ""))
        if saved.get("mapping_profile"):
            self.mapping_profile_combo.setCurrentText(str(saved["mapping_profile"]))
        self._set_column_catalog(columns, chosen)
        self._header_csv_path = self.source_location.source(self.csv_edit.text().strip())
        self._header_options = (self.encoding_combo.currentText(), self.delimiter_combo.currentText())
        self.status_label.setText("")
        pending = self._pending_csv_action
        self._pending_csv_action = ""
        if pending:
            (self._load_csv_dataset if pending == "load" else self._preview_csv)()

    def _ensure_csv_headers(self, action):
        current = self.source_location.source(self.csv_edit.text().strip())
        options = (self.encoding_combo.currentText(), self.delimiter_combo.currentText())
        if current and self._header_csv_path == current and self._header_options == options:
            return True
        self._pending_csv_action = action
        if self._csv_task_kind != "headers":
            same_source = bool(self._source_widgets_csv_path and _path_identity(current) == _path_identity(self._source_widgets_csv_path))
            self._set_csv_source_widgets(current, preserve_fields=same_source)
        return False

    def _csv_path_edited(self) -> None:
        """Read column names after a pasted/completed CSV path changes."""

        csv_path = self.csv_edit.text().strip()
        if not csv_path:
            self._source_widgets_csv_path = ""
            return
        if is_ssh_path(csv_path):
            self.csv_edit.setText(self.source_location.set_source(csv_path))
            self.local_mapping_group.setVisible(False)
        if not self._source_access_ready():
            return
        csv_path = self.source_location.source(csv_path)
        if self._source_widgets_csv_path:
            try:
                if _path_identity(csv_path) == _path_identity(
                    self._source_widgets_csv_path
                ):
                    return
            except Exception:
                if csv_path == self._source_widgets_csv_path:
                    return
        self._set_csv_source_widgets(csv_path)

    def _source_mode(self) -> str:
        return str(self.source_mode_combo.currentData() or "directory")

    def _source_mode_changed(self, *args) -> None:
        csv_mode = self._source_mode() == "csv"
        busy = bool(self._csv_task_id)
        navigating = self._pending_navigation is not None
        self.overview_button.setEnabled(bool(STATE.all_entries) and not busy)
        self.reference_button.setEnabled(bool(STATE.reference_column) and not busy)
        self.source_location.setEnabled(not busy)
        self.local_mapping_group.setVisible(not self.source_location.remote)
        self.source_path_edit.setEnabled(not busy)
        self.source_browse_button.setEnabled(not busy)
        self.source_type_combo.setEnabled(not busy)
        self.source_path_edit.setPlaceholderText("Remote folder or CSV / TSV path" if self.source_location.remote else "Folder or CSV / TSV file")
        self.history_button.setEnabled(not busy)
        self.history_action.setEnabled(not busy)
        annotation_busy = self._csv_task_kind in {"annotations", "annotation_undo"}
        self.dataset_table.setEnabled(not annotation_busy)
        self.more_button.setEnabled(not annotation_busy)
        if annotation_busy:
            for widget in (self.next_btn, self.prev_btn, self.next_unann_btn, self.goto_btn, self.undo_btn):
                widget.setEnabled(False)
        self.directory_group.setVisible(not csv_mode)
        self.csv_group.setVisible(csv_mode)
        self.csv_options_group.setVisible(csv_mode)
        self.progress_bar.setVisible(busy or navigating)
        self.cancel_task_button.setVisible(navigating or (busy and self._csv_task_kind not in {"commit", "annotations", "annotation_undo"}))
        self.apply_view_btn.setEnabled(not busy)
        self.source_mode_combo.setEnabled(not busy)
        self.source_open_button.setVisible(True)
        for widget in (
            self.path_column_edit,
            self.id_column_edit, self.label_column_edit, self.display_columns_edit, self.encoding_combo,
            self.delimiter_combo, self.mapping_profile_combo, self.source_base_edit,
            self.stage_root_edit, self.stage_alias_edit,
            self.mapping_edit_btn, self.target_format_combo, self.wsl_distribution_edit, self.access_method_combo,
        ):
            widget.setEnabled(csv_mode and not busy)
        self.pattern_edit.setEnabled(not csv_mode and not busy)
        self.csv_preview_btn.setText("Check file paths")
        self.source_open_button.setText("Open")
        self.csv_preview_btn.setEnabled(csv_mode and not busy)
        self.source_open_button.setEnabled(not busy and bool(self.source_path_edit.text().strip()))
        if busy and self._csv_task_kind == "preview":
            self.csv_preview_btn.setText("Cancel preview")
            self.csv_preview_btn.setEnabled(True)
        elif busy and self._csv_task_kind in {"load", "directory", "commit"}:
            self.source_open_button.setText("Opening…")
        elif busy and self._csv_task_kind == "headers":
            self.csv_preview_btn.setText("Cancel reading columns")
            self.csv_preview_btn.setEnabled(True)
            self.source_open_button.setEnabled(True)
        self.source_column_picker.setEnabled(csv_mode and not busy and bool(self._column_headers))
        if not busy:
            self._import_inputs_changed()

    def _csv_arguments(self) -> Dict[str, str]:
        return {
            "csv_path": self.source_location.source(self.csv_edit.text().strip()),
            "path_column": self.path_column_edit.text(),
            "id_column": self.id_column_edit.text(),
            "label_column": self.label_column_edit.text(),
            "display_columns": self.display_columns_edit.text() or "[]",
            "encoding": self.encoding_combo.currentText().strip(),
            "delimiter": self.delimiter_combo.currentText().strip(),
            "mapping_profile": self.mapping_profile_combo.currentText().strip(),
            "target_format": self.target_format_combo.currentData(),
            "wsl_distribution": self.wsl_distribution_edit.text().strip(),
            "source_base": self.source_base_edit.text().strip(),
            "stage_root": self.stage_root_edit.text().strip() if self.access_method_combo.currentData() == "worker" else "",
            "stage_alias": self.stage_alias_edit.text().strip() if self.access_method_combo.currentData() == "worker" else "",
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
            bool(self.save_on_label_click_cb.isChecked()),
            bool(self.show_source_row_action.isChecked()),
            bool(self.show_file_status_action.isChecked()),
            bool(self.keep_previous_cb.isChecked()),
            bool(self.prefer_gz_cb.isChecked()),
            bool(self.skip_annotated_cb.isChecked()),
            bool(self.preload_enabled_cb.isChecked()),
            int(self.preload_ahead_spin.value()),
            int(self.preload_interval_spin.value()),
            bool(self.bg_decompress_cb.isChecked()),
            bool(self.cache_enabled_cb.isChecked()),
            self.step_cmd_edit.toPlainText(),
            json.dumps(STATE.annotation_fields, sort_keys=True, ensure_ascii=False),
            json.dumps(STATE.score_guidance, sort_keys=True, ensure_ascii=False),
            tuple(
                sorted(
                    (str(value), edit.toPlainText())
                    for value, edit in self._bucket_inputs.items()
                )
            ),
        )

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
        self.error_label.hide()
        if kind not in {"view", "reference", "annotations", "annotation_undo"} and not str(arguments.get("path" if kind == "directory" else "csv_path", "")).strip():
            self._show_error("Enter a folder or table path to import.")
            return
        self._csv_task_epoch += 1
        fingerprint = self._csv_arguments_fingerprint(arguments)
        config_fingerprint = (
            self._annotation_ui_fingerprint() if kind in {"load", "directory"} else ()
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
            self._show_error(f"CSV {kind} could not start: {exc}")
            traceback.print_exc()
            return
        self._csv_task_id = job_id
        self._csv_task_kind = str(kind)
        self._source_mode_changed()
        self.status_label.setText(
            {"headers": "Reading column names…", "load": "Reading CSV and preparing the first structure…",
             "preview": "Checking CSV paths…", "view": "Applying filter and sort…",
             "reference": "Preparing reference; Cancel keeps the candidate unchanged…",
             "directory": "Scanning folder and reading existing annotations…",
             "annotations": "Saving selected annotations…", "annotation_undo": "Undoing annotations…"}[kind]
        )

    def _clear_csv_task(self) -> None:
        self._csv_task_id = ""
        self._csv_task_kind = ""
        if not self._closed:
            self._source_mode_changed()
            self._queue_reference_update()

    def _cancel_csv_task(self, *, announce: bool = True) -> None:
        job_id = self._csv_task_id
        if not job_id or self._csv_task_kind in {"commit", "annotations", "annotation_undo"}:
            return
        _cancel_csv_async_job(job_id)
        self._csv_task_epoch += 1
        self._clear_csv_task()
        if announce and not self._closed:
            self.status_label.setText("Operation cancelled; current candidate unchanged.")

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
        if kind in {"annotations", "annotation_undo"}:
            self.review.complete(result, error)
            return
        current_fingerprint = self._csv_arguments_fingerprint(
            self._reference_arguments() if kind == "reference" else self._view_arguments() if kind == "view" else self._directory_arguments() if kind == "directory" else self._csv_arguments()
        )
        stale_reason = ""
        if current_fingerprint != tuple(job.get("arguments_fingerprint") or ()):
            stale_reason = "source arguments changed"
        elif (
            kind in {"load", "directory"}
            and self._annotation_ui_fingerprint()
            != tuple(job.get("config_fingerprint") or ())
        ):
            stale_reason = "annotation configuration changed"
        elif int(STATE.dataset_generation) != int(
            job.get("dataset_generation", -1)
        ):
            stale_reason = "the active dataset changed"
        elif (
            kind in {"load", "directory"}
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
            self._pending_csv_action = ""
            if isinstance(error, (_CsvPreparationCancelled, CsvReadCancelled)):
                self.status_label.setText("CSV preparation cancelled.")
                return
            self.status_label.setText("")
            prefix = "Directory import" if kind == "directory" else f"Table {kind}"
            self._show_error(f"{prefix} failed: {error}")
            traceback.print_exception(
                type(error), error, getattr(error, "__traceback__", None)
            )
            return
        if kind == "headers":
            self._clear_csv_task()
            self._accept_csv_header(result)
            self._source_mode_changed()
            return
        if kind == "view":
            try:
                _commit_prepared_view(result)
            except Exception as exc:
                self._clear_csv_task()
                self._show_error(f"View change failed: {exc}")
                traceback.print_exc()
                return
            self._clear_csv_task()
            self._pending_navigation = None
            self._filter_empty = not bool(STATE.entries)
            self._table_mode = "active"
            self._dataset_table_signature = None
            self._refresh_status()
            return
        if kind == "reference":
            self._clear_csv_task()
            try:
                self._accept_reference(result)
            except Exception as exc:
                self._show_error(f"Reference comparison failed: {exc}")
                traceback.print_exc()
            return
        if kind == "preview":
            if not isinstance(result, _PreparedCsvPreview):
                self._clear_csv_task()
                self.status_label.setText("CSV preview returned an invalid result.")
                return
            self._clear_csv_task()
            self._prepared_preview = result
            self._preview_arguments_fingerprint = tuple(job["arguments_fingerprint"])
            payload = result.payload
            from .mapping_widgets import MappingPreviewDialog
            if getattr(self, "_mapping_preview_dialog", None) is not None:
                self._mapping_preview_dialog.close()
            self._mapping_preview_dialog = MappingPreviewDialog(payload, self)
            self._mapping_preview_dialog.show()
            self.status_label.setText(
                f"CSV preview: {payload.get('row_count', 0)} rows; "
                f"{payload.get('status_counts', {})}"
            )
            return
        if kind not in {"load", "directory"} or not isinstance(result, _PreparedCsvLoad if kind == "load" else _PreparedDirectoryLoad):
            self._clear_csv_task()
            self.status_label.setText("CSV load returned an invalid result.")
            return
        self._csv_task_kind = "commit"
        self._source_mode_changed()
        self.status_label.setText("Opening the prepared structure in PyMOL…")
        try:
            if kind == "directory":
                _commit_prepared_directory_load(result)
            else:
                _commit_prepared_csv_load(result)
        except Exception as exc:
            self._clear_csv_task()
            self.status_label.setText(f"CSV load commit failed: {exc}")
            self._show_error(f"CSV load commit failed: {exc}")
            traceback.print_exc()
            return
        self._clear_csv_task()
        self._pending_navigation = None
        self._filter_empty = False
        self._history_restore = None
        if kind == "load":
            STATE.display_columns_selected = self._source_columns_explicit
        self._schedule_state_save()
        self._sync_from_state()
        self._rebuild_value_controls(sync_to_state=False)
        self._refresh_status()
        self.main_tabs.setCurrentWidget(self.review_tab)

    def _preview_csv(self) -> None:
        if self._csv_task_id:
            if self._csv_task_kind in {"preview", "headers"}:
                self._pending_csv_action = ""
                self._cancel_csv_task()
            return
        if not self._csv_access_ready() or not self._ensure_csv_headers("preview"):
            return
        arguments = self._csv_arguments()
        self._start_csv_task("preview", _prepare_csv_preview, arguments)

    def _load_csv_dataset(self) -> None:
        if self._csv_task_id:
            if self._csv_task_kind == "headers":
                self._pending_csv_action = "load"
                if _path_identity(self.source_location.source(self.csv_edit.text())) != _path_identity(self._source_widgets_csv_path):
                    self._set_csv_source_widgets(self.csv_edit.text().strip())
                return
            if self._csv_task_kind == "load":
                self._cancel_csv_task()
            return
        if not self._csv_access_ready() or not self._ensure_csv_headers("load"):
            return
        self._sync_to_state(update_directory=False, persist=False)
        arguments = self._csv_arguments()
        runtime = _capture_csv_runtime_context()
        config = copy.deepcopy(runtime.config)
        if self._history_restore is not None and self._history_restore["source_mode"] == "csv" and _path_identity(self._history_restore["csv_path"]) == _path_identity(arguments["csv_path"]):
            restored = self._history_restore["config"]
            config.update({key: restored.get(key, default) for key, default in
                           (("filter_text", ""), ("sort_column", ""), ("sort_descending", False))})
        elif runtime.source_mode != "csv" or _path_identity(runtime.csv_path) != _path_identity(arguments["csv_path"]):
            config["score_guidance"] = {}
            config["reference_column"] = ""
            config["filter_presets"] = []
        worker_arguments = dict(arguments, validate_paths=False, restore_saved=False, config_override=config)
        if self._history_restore is not None and self._history_restore["source_mode"] == "csv" and _path_identity(self._history_restore["csv_path"]) == _path_identity(arguments["csv_path"]):
            worker_arguments["review_options"] = self._history_restore["config"]
        elif runtime.source_mode == "csv" and _path_identity(runtime.csv_path) == _path_identity(arguments["csv_path"]) and STATE.step_cmd != STATE.review_reference.get("step_cmd", STATE.step_cmd):
            worker_arguments["review_options"] = {"step_cmd": STATE.step_cmd}
        if self._preview_arguments_fingerprint == self._csv_arguments_fingerprint(arguments):
            worker_arguments["reuse_preview"] = self._prepared_preview
        self._start_csv_task(
            "load",
            _prepare_csv_load,
            arguments,
            runtime=runtime,
            worker_arguments=worker_arguments,
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
        label_key = _active_annotation_key()
        signature: Tuple[object, ...] = (
            source_token,
            int(STATE.dataset_generation),
            STATE.source_mode,
            tuple(STATE.display_columns),
            STATE.reference_column,
            label_key,
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
        self.column_picker.set_columns(payload.get("columns", ()), display_columns)
        self.view_summary_label.setText(f"Preview: {payload.get('row_count', 0):,} rows · load this CSV to annotate")
        self.review.selection.clear()
        self.review.refresh()

    def _apply_view(self, *, sampling=None) -> None:
        if not STATE.all_entries or self._table_mode == "preview":
            self._show_error("Load the CSV before applying a filter.")
            return
        if self._csv_task_id:
            if self._csv_task_kind != "view":
                return
            self._cancel_csv_task(announce=False)
        arguments = self._view_arguments()
        query = arguments["query"]
        if self.filter_mode_combo.currentIndex() == 1 and query.strip():
            query = "where:" + query
        self._start_csv_task("view", _prepare_view, arguments, worker_arguments={
            "entries": tuple(STATE.all_entries), "query": query,
            "sort_column": arguments["sort_column"], "descending": arguments["descending"],
            "current_id": STATE.current_entry_id(), "current_index": STATE.index,
            "materialized": dict(STATE.materialized_paths), "context": _materialization_context(),
            "sampling": dict(STATE.sampling if sampling is None else sampling),
        })

    def _view_arguments(self):
        return {"query": self.filter_edit.text(), "sort_column": self.sort_column_combo.currentText(),
                "descending": bool(self.sort_desc_cb.isChecked()), "mode": self.filter_mode_combo.currentIndex()}

    def _dataset_index_clicked(self, index) -> None:
        if index is not None and index.isValid():
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            if modifiers & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier):
                self.review.refresh()
                return
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
            rows.extend((name, metadata.get(name, "")) for name in self._dataset_model._display_columns)
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
            self._request_navigation(index)
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
        if not self._syncing_widgets:
            self._state_save_timer.start(300)

    def _save_ui_state(self, *args) -> None:
        self._sync_to_state(persist=False)
        _save_current_dataset_state(background=True)
        _record_current_config_history(background=True)

    def _commands_edited(self, *args):
        if not hasattr(self, "command_draft_label"):
            return
        changed = self.step_cmd_edit.toPlainText().strip() != STATE.step_cmd or any(
            edit.toPlainText() != STATE.bucket_cmds.get(value, "") for value, edit in self._bucket_inputs.items())
        self.command_draft_label.setText("Unapplied changes" if changed else "")
        self.commands_apply_btn.setEnabled(changed)

    def _apply_commands(self):
        from .command_templates import validate_commands
        step = self.step_cmd_edit.toPlainText().strip()
        buckets = {value: edit.toPlainText() for value, edit in self._bucket_inputs.items()}
        try:
            validate_commands(step, buckets)
            STATE.step_cmd, STATE.bucket_cmds = step, buckets
            self._save_ui_state()
            self.error_label.hide()
            self._commands_edited()
            return True
        except Exception as exc:
            traceback.print_exc()
            self._show_error(str(exc))
            return False

    def _sync_to_state(
        self, update_directory: bool = True, persist: bool = True,
        update_source: bool = False,
    ) -> None:
        if update_directory:
            requested_dir = self.source_location.source(self.dir_edit.text().strip()) or "."
            active_dir = _active_dataset_directory()
            if not active_dir or _path_identity(active_dir) == _path_identity(
                requested_dir
            ):
                STATE.dataset_dir = requested_dir
        requested_csv = self.source_location.source(self.csv_edit.text().strip()) if update_source else ""
        if update_source and STATE.source_mode == "csv" and requested_csv:
            try:
                same_csv = _path_identity(requested_csv) == _path_identity(STATE.csv_path)
            except Exception:
                same_csv = False
            if same_csv:
                STATE.path_column = self.path_column_edit.text()
                STATE.id_column = self.id_column_edit.text()
                STATE.label_column = self.label_column_edit.text()
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
        STATE.save_on_label_click = bool(self.save_on_label_click_cb.isChecked())
        STATE.show_source_row = bool(self.show_source_row_action.isChecked())
        STATE.show_file_status = bool(self.show_file_status_action.isChecked())
        STATE.keep_previous = bool(self.keep_previous_cb.isChecked())
        STATE.prefer_gz = bool(self.prefer_gz_cb.isChecked())
        STATE.skip_annotated = bool(self.skip_annotated_cb.isChecked())
        STATE.preload_enabled = bool(self.preload_enabled_cb.isChecked())
        STATE.preload_ahead = int(self.preload_ahead_spin.value())
        STATE.preload_interval_ms = int(self.preload_interval_spin.value())
        STATE.background_decompress_gz = bool(self.bg_decompress_cb.isChecked())
        STATE.cache_enabled = bool(self.cache_enabled_cb.isChecked())
        STATE.restore_last_dataset = False
        if persist:
            _save_current_dataset_state(background=True)

    def _sync_from_state(self) -> None:
        self._table_mode = "active"
        mode_index = self.source_mode_combo.findData(STATE.source_mode)
        self.source_mode_combo.setCurrentIndex(max(0, mode_index))
        self.source_location.set_source(STATE.csv_path if STATE.source_mode == "csv" else STATE.dataset_dir)
        directory = STATE.dataset_dir if STATE.dataset_dir != "." or STATE.dataset is not None else ""
        source = STATE.csv_path if STATE.source_mode == "csv" else directory
        self._set_source_type_for_path(source, STATE.source_mode)
        self.source_path_edit.setText(parse_ssh_path(source).path if is_ssh_path(source) else source or "")
        self._source_widgets_csv_path = STATE.csv_path or ""
        self.path_column_edit.setText(STATE.path_column or "structure_path")
        self.id_column_edit.setText(STATE.id_column or "")
        self.label_column_edit.setText(STATE.label_column or "")
        self.display_columns_edit.setText(json.dumps(STATE.display_columns, ensure_ascii=False))
        self.encoding_combo.setCurrentText(STATE.csv_encoding or DEFAULT_ENCODING)
        self.delimiter_combo.setCurrentText({"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(STATE.csv_delimiter, STATE.csv_delimiter) or AUTO_DELIMITER)
        self._refresh_mapping_profiles(STATE.mapping_profile if STATE.files else "custom")
        from .mapping_widgets import set_format
        if STATE.files and STATE.mapping_target_format:
            set_format(self.target_format_combo, STATE.mapping_target_format)
        if STATE.files:
            self.wsl_distribution_edit.setText(STATE.mapping_wsl_distribution)
        self.source_base_edit.setText(STATE.source_base or "")
        self.stage_root_edit.setText(STATE.stage_root or "")
        self.stage_alias_edit.setText(STATE.stage_alias or "")
        self.access_method_combo.setCurrentIndex(1 if STATE.stage_root or STATE.stage_alias else 0)
        condition = STATE.filter_text.startswith("where:")
        self.filter_mode_combo.setCurrentIndex(1 if condition else 0)
        self.filter_edit.setText(STATE.filter_text[6:] if condition else STATE.filter_text or "")
        self.sort_column_combo.setCurrentText(STATE.sort_column or "")
        self.sort_desc_cb.setChecked(bool(STATE.sort_descending))
        self.pattern_edit.setText(STATE.pattern or STRUCTURE_PATTERN)
        self.label_edit.setText(STATE.label_name or "quality")
        self.values_edit.setText(STATE.values_text or "Good,Bad")
        self.auto_advance_cb.setChecked(bool(STATE.auto_advance))
        self.save_on_label_click_cb.setChecked(bool(STATE.save_on_label_click))
        self.show_source_row_action.setChecked(bool(STATE.show_source_row))
        self.show_file_status_action.setChecked(bool(STATE.show_file_status))
        self.keep_previous_cb.setChecked(bool(STATE.keep_previous))
        self.prefer_gz_cb.setChecked(bool(STATE.prefer_gz))
        self.skip_annotated_cb.setChecked(bool(STATE.skip_annotated))
        self.preload_enabled_cb.setChecked(bool(STATE.preload_enabled))
        self.preload_ahead_spin.setValue(int(STATE.preload_ahead or 0))
        self.preload_interval_spin.setValue(int(STATE.preload_interval_ms or 250))
        self.bg_decompress_cb.setChecked(bool(STATE.background_decompress_gz))
        self.cache_enabled_cb.setChecked(bool(STATE.cache_enabled))
        self.step_cmd_edit.setPlainText(STATE.step_cmd or "")
        if STATE.dataset is not None:
            self._set_column_catalog(STATE.dataset.columns, STATE.display_columns)
            self._header_csv_path = STATE.csv_path
            self._header_options = (self.encoding_combo.currentText(), self.delimiter_combo.currentText())
        self._source_mode_changed()

    def _directory_arguments(self):
        return {"path": self.source_location.source(self.dir_edit.text().strip()), "pattern": self.pattern_edit.text().strip() or STRUCTURE_PATTERN}

    def _load_dataset(self) -> None:
        self._update_source_mode()
        if self._source_mode() == "csv":
            self._load_csv_dataset()
            return
        if self._csv_task_id:
            return
        if not self._source_access_ready():
            return
        arguments = self._directory_arguments()
        self._sync_to_state(update_directory=False, persist=False)
        self._start_csv_task("directory", _prepare_directory_load, arguments,
                             runtime=_capture_csv_runtime_context(),
                             worker_arguments=dict(arguments, restore_saved=False))

    def _next(self) -> None:
        self._request_navigation(STATE.index + 1)

    def _prev(self) -> None:
        self._request_navigation(STATE.index - 1)

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
        value = self.value_combo.currentText().strip()
        if not value:
            return
        self._annotate(value)

    def _next_unannotated(self) -> None:
        if STATE.entries:
            later = _review_status_ids()
            for offset in range(1, len(STATE.entries) + 1):
                candidate = (STATE.index + offset) % len(STATE.entries)
                if (candidate not in STATE.annotated_indices
                        and STATE.entries[candidate].id not in later):
                    self._request_navigation(candidate)
                    return
        later_count = len(_review_status_ids())
        self.status_label.setText(
            "No unreviewed candidates remain in this view"
            + (f"; {later_count:,} marked for later." if later_count else ".")
        )

    def _next_marked_for_later(self) -> None:
        later = _review_status_ids()
        if STATE.entries and later:
            for offset in range(1, len(STATE.entries) + 1):
                candidate = (STATE.index + offset) % len(STATE.entries)
                if STATE.entries[candidate].id in later:
                    self._request_navigation(candidate)
                    return
        self.status_label.setText("No candidates are marked for later in this view.")

    def _mark_current_for_later(self) -> None:
        entry = STATE.current_entry()
        if entry is None:
            return
        try:
            _set_review_status((entry.id,), REVIEW_STATUS_LATER)
            self._refresh_status()
        except Exception as exc:
            traceback.print_exc()
            self._show_error(f"Could not mark this candidate for later: {exc}")

    def _clear_current_review_status(self) -> None:
        entry = STATE.current_entry()
        if entry is None:
            return
        try:
            _set_review_status((entry.id,), "")
            self._refresh_status()
        except Exception as exc:
            traceback.print_exc()
            self._show_error(f"Could not clear the later mark: {exc}")

    def _undo(self) -> None:
        if self.review.undo():
            return
        self._sync_to_state()
        sa_undo()
        self._refresh_status()

    def _goto(self) -> None:
        self._request_navigation(int(self.goto_spin.value()) - 1)

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

    def _export_review_handoff(self) -> None:
        """Export a compact, source-aware handoff without copying structures."""
        if STATE.dataset is None:
            return
        source = _active_source_reference()
        stem = os.path.splitext(os.path.basename(parse_ssh_path(source).path if is_ssh_path(source) else source))[0]
        default = os.path.join(STATE.dataset_dir or ".", f"{stem or 'review'}_handoff.json")
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export review handoff", default, "JSON Files (*.json)"
        )
        if not out_path:
            return
        later = _review_status_ids()
        values = STATE.row_annotation_store.snapshot() if STATE.row_annotation_store is not None else {}
        entries = []
        for entry in STATE.all_entries:
            annotations = {key: value for key, value in values.get(entry.id, {}).items()
                           if key != REVIEW_STATUS_LABEL}
            entries.append({
                "entry_id": entry.id,
                "row_number": entry.row_number or entry.ordinal + 1,
                "name": entry.display_name,
                "source_path": entry.source_path,
                "status": entry.status,
                "review_status": "later" if entry.id in later else "",
                "annotations": annotations,
            })
        payload = {
            "schema": "pymolplugins.review-handoff/1",
            "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": source,
            "source_mode": STATE.source_mode,
            "filter": STATE.filter_text,
            "sampling": copy.deepcopy(STATE.sampling),
            "current_entry_id": STATE.current_entry_id(),
            "counts": {"all": len(STATE.all_entries), "view": len(STATE.entries),
                       "reviewed": len(STATE.annotated_entry_ids), "later": len(later)},
            "score_guidance": copy.deepcopy(STATE.score_guidance),
            "entries": entries,
            "note": "Structure files are not included. Review status is separate from quality labels.",
        }
        destination = _validate_export_destination(out_path)
        directory = os.path.dirname(destination) or os.curdir
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".review_handoff.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = ""
            self.status_label.setText(f"Exported review handoff to {destination}")
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _on_label_changed(self) -> None:
        # Rescan progress against the new label so counts/highlighting stay accurate.
        self._save_ui_state()
        if STATE.files:
            _rescan_annotated_indices()
            STATE.undo_history.clear()
        self._refresh_status()

    def _rebuild_value_controls(self, sync_to_state: bool = True) -> None:
        from .qt_widgets import CommandCompletion
        if sync_to_state:
            self._save_ui_state()
        for binding in self._bucket_history_bindings:
            binding.detach()
        self._bucket_history_bindings = []
        while self.values_layout.count():
            item = self.values_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._bucket_inputs = {}
        self.review.rebuild_form()
        values = _annotation_fields()[0]["values"]
        form = QtWidgets.QFormLayout()
        for value in values:
            edit = QtWidgets.QPlainTextEdit()
            edit.setObjectName("commandEditor")
            edit.setMinimumHeight(65)
            edit.setMaximumHeight(110)
            edit.setPlaceholderText("Optional command for a single displayed candidate")
            edit.setPlainText(STATE.bucket_cmds.get(value, ""))
            edit.textChanged.connect(self._commands_edited)
            self._bucket_history_bindings.append(CommandCompletion(edit, multiline=True))
            self._bucket_inputs[value] = edit
            form.addRow(f"{value} cmd:", edit)
        form_widget = QtWidgets.QWidget()
        form_widget.setLayout(form)
        self.values_layout.addWidget(form_widget)
        self.value_combo.clear()
        self.value_combo.addItems(values)
        self.manual_annotation_row.hide()

    def _annotate(self, value: str) -> None:
        if self._table_mode == "preview" or self._filter_empty:
            self._show_error("Load matching rows before annotating.")
            return
        self.review.quick_label(value)

    def _display_source_columns(self, *args) -> None:
        source_row = self.show_source_row_action.isChecked()
        file_status = self.show_file_status_action.isChecked()
        self.dataset_table.setColumnHidden(0, not source_row)
        self.dataset_table.setColumnHidden(1, not file_status)
        self.dataset_table.horizontalHeader().setVisible(bool(STATE.display_columns or STATE.reference_column) or source_row or file_status)

    def _refresh_dataset_table(self) -> None:
        display_columns = list(STATE.display_columns)
        label_key = _active_annotation_key()
        source_token, signature = self._current_table_identity()
        store = STATE.row_annotation_store
        annotation_revision = store.revision(refresh=False) if store is not None else None
        reset_model = not (
            self._table_mode == "active"
            and source_token == self._table_source_token
            and signature == self._dataset_table_signature
        )
        annotations_changed = annotation_revision != self._table_annotation_revision
        annotation_values: Dict[str, str] = {}
        if store is not None and (reset_model or annotations_changed):
            annotation_values = store.snapshot_for_label(label_key, refresh=False)

        if reset_model:
            entries = _table_view_entries(STATE.all_entries or STATE.entries)
            self._table_mode = "active"
            self._table_source_token = source_token
            self._preview_rows_by_id = {}
            self._dataset_table_signature = signature
            self._entry_id_to_active_index = {
                entry.id: index for index, entry in enumerate(STATE.entries)
            }
            self._entries_by_id = {entry.id: entry for entry in entries}
            self.review.selection.updating = True
            try:
                self._dataset_model.set_active(entries, display_columns, STATE.source_mode, label_key, annotation_values)
            finally:
                self.review.selection.updating = False
            self.review.selection.model_reset(source_token)
            for column, width in enumerate((130, 110, 220, 170)):
                self.dataset_table.setColumnWidth(column, width)
            self.dataset_table.setColumnWidth(2, 175)
            self.dataset_table.setColumnWidth(3, 80)
            self._display_source_columns()
            columns = STATE.dataset.columns if STATE.dataset is not None else tuple(entries[0].metadata) if entries else ()
            self.column_picker.set_columns(columns, display_columns)
            self._column_bindings["filter"].set_columns(columns)
            self._column_bindings["sort"].set_columns(columns)
            selected_sort = self.sort_column_combo.currentText()
            self.sort_column_combo.blockSignals(True)
            self.sort_column_combo.clear()
            self.sort_column_combo.addItem("")
            self.sort_column_combo.addItems(list(columns))
            self.sort_column_combo.setCurrentText(
                selected_sort or STATE.sort_column
            )
            self.sort_column_combo.blockSignals(False)
        elif annotations_changed:
            self._dataset_model.update_annotations(annotation_values)

        self._table_annotation_revision = annotation_revision
        current_row = self._dataset_model.row_for_entry(STATE.current_entry_id())
        self.review.selection.ensure_current(current_row)
        filtered = bool(STATE.filter_text or STATE.sort_column)
        sampled = bool(STATE.sampling.get("enabled"))
        self.view_summary_label.setText("Sample" if sampled else "Candidates")
        self.view_summary_label.setVisible(sampled)
        self.view_scope_label.setText(
            f"Sample {len(STATE.entries):,} · Filtered {STATE.filtered_count:,} · All {len(STATE.all_entries):,}"
            if sampled else f"Filtered {len(STATE.entries):,} / {len(STATE.all_entries):,}")
        self.view_scope_label.setVisible(sampled or len(STATE.entries) != len(STATE.all_entries))
        self.view_scope_label.setToolTip("Sampling is a coverage-review subset, not the whole dataset. "
            "Overview / sample shows the method and returns to the full filtered set.")
        self.overview_button.setText("Overview •" if sampled else "Overview")
        self._refresh_filter_button()

    def _refresh_metadata_table(self) -> None:
        if not self.details_cb.isChecked():
            return
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
            rows.extend((name, entry.metadata.get(name, "")) for name in STATE.display_columns)
            rows.extend(("Annotation: " + name, value)
                        for name, value in _annotations_for_index(STATE.index, refresh=False).items())
        self.metadata_table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            for column, text in enumerate((str(name), str(value if value is not None else ""))):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(text)
                self.metadata_table.setItem(row, column, item)

    def _refresh_reference_status(self):
        reference = self._reference_arguments()["path"]
        self.reference_label.setVisible(STATE.source_mode == "csv")
        self.reference_button.setVisible(bool(STATE.reference_column))
        self.reference_button.setEnabled(bool(reference) and not self._csv_task_id)
        if not STATE.reference_column:
            text = "Reference: no column configured (More → Reference column…)"
        elif not reference:
            text = f"Reference: no path in '{STATE.reference_column}' for this candidate"
        else:
            text = "Reference: " + ntpath.basename(reference)
        self.reference_label.setText(text)
        self.reference_label.setToolTip(reference)
        self.reference_button.setText("Hide reference" if self._reference_object else "Show reference")
        if self._reference_alignment is not None:
            self.reference_label.setText(f"Reference: {ntpath.basename(reference)} · fixed · "
                f"display alignment {self._reference_alignment[0]:.3f} Å · "
                f"{self._reference_alignment[1]} matched atoms")
            self.reference_label.setToolTip(reference + "\nCandidate aligned to fixed reference for display; "
                "source files and stored CSV scores are unchanged.")

    def _refresh_status(self) -> None:
        self._queue_reference_update()
        self._refresh_reference_status()
        self._page_changed()
        busy = self._csv_task_kind in {"annotations", "annotation_undo"}
        ready = bool(STATE.files) and not self._filter_empty and self._table_mode != "preview" and not busy
        for widget in (self.next_btn, self.prev_btn, self.next_unann_btn, self.goto_btn, self.apply_btn, *self._value_buttons):
            widget.setEnabled(ready)
        self.next_unann_action.setEnabled(ready)
        can_undo = bool(STATE.undo_history) and STATE.row_annotation_store is not None and not busy
        self.undo_btn.setEnabled(can_undo)
        self.undo_action.setEnabled(can_undo)
        self.more_button.setEnabled(STATE.dataset is not None and not busy)
        self.empty_view.setVisible(bool(STATE.all_entries) and not STATE.files)
        self.status_label.setText("")
        if not STATE.files:
            self.position_label.setText("")
            self.current_title_label.setText("No matching candidates" if STATE.all_entries else "")
            self.goto_spin.setRange(1, 1)
            self._dataset_model.clear_rows()
            self.review.selection.clear()
            self.metadata_table.setRowCount(0)
            self._dataset_table_signature = None
            self._table_annotation_revision = None
            self._table_mode = "active"
            self._table_source_token = ""
            self._preview_rows_by_id = {}
            self._entry_id_to_active_index = {}
            self._entries_by_id = {}
            if STATE.all_entries:
                self._dataset_model.set_active((), STATE.display_columns, STATE.source_mode,
                                               normalize_label_key(STATE.label_name), {})
                self.view_summary_label.setText("0 matches")
                self.view_scope_label.setText(f"Filtered 0 / {len(STATE.all_entries):,}")
                self.view_scope_label.show()
                self._refresh_filter_button()
            self.review.refresh()
            return

        _ensure_navigation_session()

        # Keep the go-to control in range with the loaded dataset.
        self.goto_spin.setRange(1, len(STATE.files))
        self.goto_spin.blockSignals(True)
        self.goto_spin.setValue(STATE.index + 1)
        self.goto_spin.blockSignals(False)
        path = STATE.current_file() or ""
        entry = STATE.current_entry()
        basename = entry.display_name if entry is not None else os.path.basename(path)
        annotated = len(STATE.annotated_indices)
        total = len(STATE.files)
        later_ids = _review_status_ids()
        later_count = sum(1 for candidate in STATE.entries if candidate.id in later_ids)
        self.position_label.setText(
            f"{STATE.index + 1:,} / {total:,} · {annotated:,} reviewed"
            + (f" · {later_count:,} later" if later_count else "")
        )
        self.current_title_label.setText(basename)
        self.current_title_label.setToolTip(basename + "\n" + path)
        later = bool(entry is not None and entry.id in later_ids)
        self.review_status_label.setVisible(later)
        self.review_status_label.setText("Later")
        self.mark_later_action.setEnabled(not later)
        self.clear_later_action.setEnabled(later)
        self.next_later_action.setEnabled(bool(later_ids))

        # Check for mismatch between widget state and PyMOL state
        if STATE.current_object:
            if not _verify_current_object_enabled():
                actual_enabled = _get_enabled_managed_object()
                if actual_enabled and actual_enabled != STATE.current_object:
                    self._show_error(f"PyMOL is displaying {actual_enabled} instead of {STATE.current_object}. Use Reload structure to synchronize.")
        self._refresh_dataset_table()
        self._refresh_metadata_table()
        self.review.refresh()


def show_structure_annotator_panel(mode=None) -> QtWidgets.QWidget:
    from pymolplugins.host.workspace import show_workspace

    global _panel
    if _panel is not None:
        return show_workspace(_panel, "structure_annotator", mode)
    _panel = StructureAnnotatorPanel()
    try:
        show_workspace(_panel, "structure_annotator", mode)
    except Exception:
        _panel.close()
        raise
    sa_bind_keys()
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
