from pymol import cmd
from pymol.Qt import QtCore, QtWidgets
import copy
import datetime
import gzip
import hashlib
import os
import re
import shutil
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from typing import Dict, List, Optional
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
        dataset_state_path,
        read_dataset_state,
        write_dataset_state,
    )
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
        dataset_state_path,
        read_dataset_state,
        write_dataset_state,
    )
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
    dataset_dir: str = "."
    pattern: str = STRUCTURE_PATTERN
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
    restore_last_dataset: bool = True
    managed_objects: List[str] = field(default_factory=list)
    current_object: Optional[str] = None
    undo_history: List[dict] = field(default_factory=list)
    annotated_indices: set = field(default_factory=set)
    navigation_session: Optional[NavigationSession] = None

    def values(self) -> List[str]:
        return _split_values(self.values_text) or ["Good", "Bad"]

    def current_file(self) -> Optional[str]:
        if not self.files:
            return None
        if self.navigation_session is not None:
            self.index = self.navigation_session.cursor
        return self.files[self.index % len(self.files)]


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
    "restore_last_dataset",
)
_PERSISTED_INT_BOUNDS = {
    "preload_ahead": (0, 20),
    "preload_interval_ms": (50, 5000),
}


def _path_identity(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _active_dataset_directory() -> str:
    """Return the directory proved by the currently loaded file list."""

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
    if type(index) is not int or index < 0:
        raise StateStoreError("position.index must be a non-negative integer")
    if not isinstance(filename, str) or len(filename) > 4096:
        raise StateStoreError("position.file must be a string")
    return {"index": index, "file": filename.replace("\\", "/")}


def _apply_persisted_config(config: Dict[str, object]) -> None:
    for name in _PERSISTED_STRING_FIELDS + _PERSISTED_BOOL_FIELDS:
        if name in config:
            setattr(STATE, name, config[name])
    for name in _PERSISTED_INT_BOUNDS:
        if name in config:
            setattr(STATE, name, config[name])
    if "bucket_cmds" in config:
        STATE.bucket_cmds = dict(config["bucket_cmds"])


def _read_persisted_dataset_state(dataset_dir: str) -> Optional[Dict[str, object]]:
    try:
        payload = read_dataset_state(dataset_dir)
        if payload is None:
            return None
        return {
            "config": _validate_persisted_config(payload.get("config")),
            "position": _validate_persisted_position(payload.get("position")),
        }
    except StateStoreError as exc:
        print(f"Structure Annotator: ignoring invalid state file: {exc}")
        print(f"  State path: {dataset_state_path(dataset_dir)}")
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


def _write_persisted_dataset_state(dataset_dir: str) -> Optional[str]:
    if not dataset_dir or not STATE.files:
        return None
    try:
        payload = {
            "updated_at_utc": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "config": _persisted_config(),
            "position": {
                "index": int(STATE.index),
                "file": _relative_current_file(dataset_dir),
            },
        }
        return write_dataset_state(dataset_dir, payload)
    except Exception as exc:
        print(f"Structure Annotator: state save failed: {exc}")
        print(f"  State path: {dataset_state_path(dataset_dir)}")
        return None


def _save_current_dataset_state() -> Optional[str]:
    active = _active_dataset_directory()
    if not active or not STATE.dataset_dir:
        return None
    if _path_identity(active) != _path_identity(STATE.dataset_dir):
        return None
    return _write_persisted_dataset_state(active)


def _record_current_config_history() -> Optional[str]:
    """Record one finalized config without making history a runtime dependency."""

    active = _active_dataset_directory()
    if not active:
        return None
    try:
        return record_config_history(active, _persisted_config())
    except Exception as exc:
        print(f"Structure Annotator: global history save failed: {exc}")
        return None


def _restore_latest_config_from_history() -> bool:
    """Restore the last finalized config and, when enabled, its dataset."""

    if STATE.files:
        return False
    try:
        snapshot = latest_config_snapshot()
        if not snapshot:
            return False
        config = _validate_persisted_config(snapshot.get("config"))
        _apply_persisted_config(config)
        dataset_dir = str(snapshot.get("dataset_dir", "")).strip()
        if dataset_dir:
            STATE.dataset_dir = dataset_dir
        if not STATE.restore_last_dataset or not dataset_dir:
            return False
        if not os.path.isdir(_long_path(dataset_dir)):
            print(
                "Structure Annotator: last dataset is unavailable; "
                f"config restored without loading: {dataset_dir}"
            )
            return False
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
    dataset_dir: str, files: List[str], position: Optional[Dict[str, object]]
) -> int:
    if not files or not position:
        return 0
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


def _new_navigation_session(files: List[str], index: int = 0) -> NavigationSession:
    entries = [
        NavigationEntry(
            id=f"file:{os.path.abspath(path)}",
            kind="file",
            display_name=os.path.basename(path),
            source_paths=(os.path.abspath(path),),
        )
        for path in files
    ]
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
    expected_ids = [f"file:{os.path.abspath(path)}" for path in STATE.files]
    if session is None or [entry.id for entry in session.entries] != expected_ids:
        if not STATE.files:
            raise RuntimeError("Structure Annotator: no dataset loaded")
        session = _new_navigation_session(STATE.files, STATE.index)
        STATE.navigation_session = session
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
    digest = hashlib.sha1(
        os.path.abspath(path).encode("utf-8", errors="surrogatepass")
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


def _get_bg_executor() -> ThreadPoolExecutor:
    global _bg_executor
    if _bg_executor is None:
        workers = max(1, min(4, (os.cpu_count() or 2)))
        _bg_executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="pymol_sa_bg"
        )
    return _bg_executor


def _ensure_bg_cache_dir() -> str:
    global _bg_cache_dir
    if _bg_cache_dir is None:
        _bg_cache_dir = tempfile.mkdtemp(prefix="pymol_structure_annotator_")
    os.makedirs(_bg_cache_dir, exist_ok=True)
    return _bg_cache_dir


def _reset_bg_cache() -> None:
    global _bg_cache_dir, _bg_decompress_futures, _bg_decompressed_paths
    global _preload_generation, _preload_queue
    timer = globals().get("_preload_panel_timer")
    if timer is not None:
        try:
            timer.stop()
        except Exception:
            pass
    _preload_generation = int(globals().get("_preload_generation", 0)) + 1
    _preload_queue = []
    _bg_decompress_futures = {}
    _bg_decompressed_paths = {}
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


def _ensure_loaded(index: int, focus: bool, allow_gz_fallback: bool = True) -> Optional[str]:
    if not STATE.files:
        return None
    index = index % len(STATE.files)
    path = STATE.files[index]
    obj_name = _object_name_for_index(path, index)

    if not _object_exists(obj_name):
        load_path = path
        if _is_gz_path(path) and STATE.background_decompress_gz:
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
        cmd.load(load_path_resolved, obj_name, quiet=1)
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
    for idx in desired:
        obj = _object_name_for_index(STATE.files[idx], idx)
        if not _object_exists(obj):
            pending.append(idx)

    _preload_generation += 1
    _preload_queue = pending
    if STATE.background_decompress_gz:
        for idx in list(_preload_queue):
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
    _collect_ready_decompressed(list(_preload_queue))

    idx = _preload_queue[0]
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


def _load_current_structure() -> str:
    if not STATE.current_file():
        raise RuntimeError("Structure Annotator: no dataset loaded")
    if not STATE.keep_previous and not STATE.preload_enabled:
        _delete_managed_objects(False)

    loaded = _ensure_loaded(STATE.index, focus=True, allow_gz_fallback=True)
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


def _rescan_annotated_indices() -> None:
    """Populate STATE.annotated_indices for progress tracking under the current label."""
    STATE.annotated_indices = set()
    if not STATE.files:
        return
    label_key = normalize_label_key(STATE.label_name)
    for idx, path in enumerate(STATE.files):
        try:
            if has_annotation_fast(_resolve_annotation_path(path), label_key):
                STATE.annotated_indices.add(idx)
        except Exception:
            pass


def sa_load_dir(path: str = ".", pattern: str = "") -> None:
    """
    Load a directory of structures for annotation.

    Args:
        path: Directory containing .pdb/.cif (optionally .gz).
        pattern: Optional explicit regex override. Empty restores the dataset's
            saved pattern, or uses the default for a first-time dataset.
    """
    previous_dataset_dir = _active_dataset_directory() or STATE.dataset_dir
    previous = {
        "dataset_dir": previous_dataset_dir,
        "pattern": STATE.pattern,
        "config": copy.deepcopy(_persisted_config()),
        "files": list(STATE.files),
        "index": STATE.index,
        "undo_history": copy.deepcopy(STATE.undo_history),
        "annotated_indices": set(STATE.annotated_indices),
        "navigation_session": STATE.navigation_session,
    }
    try:
        dataset_dir = os.path.abspath(os.path.expanduser(path))
        persisted = _read_persisted_dataset_state(dataset_dir)
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
            _apply_persisted_config(previous["config"])
            STATE.dataset_dir = previous["dataset_dir"]
            STATE.pattern = previous["pattern"]
            print(f"Structure Annotator: no files matched in {path}; current dataset kept")
            return

        # Commit only after discovery succeeds.  Old owned objects are removed
        # exactly so a same-basename file in another dataset cannot be reused.
        _delete_managed_objects(False)
        _reset_bg_cache()
        STATE.dataset_dir = dataset_dir
        STATE.pattern = effective_pattern
        STATE.files = discovered
        resume_at = _resume_index(
            dataset_dir,
            discovered,
            persisted.get("position") if persisted is not None else None,
        )
        STATE.navigation_session = _new_navigation_session(discovered, resume_at)
        STATE.index = STATE.navigation_session.cursor
        STATE.current_object = None
        STATE.undo_history.clear()
        _rescan_annotated_indices()
        _load_current_structure()
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
            _delete_managed_objects(False)
            _reset_bg_cache()
            _apply_persisted_config(previous["config"])
            STATE.dataset_dir = previous["dataset_dir"]
            STATE.pattern = previous["pattern"]
            STATE.files = previous["files"]
            STATE.index = previous["index"]
            STATE.undo_history = previous["undo_history"]
            STATE.annotated_indices = previous["annotated_indices"]
            STATE.navigation_session = previous["navigation_session"]
            STATE.current_object = None
            if STATE.files:
                _set_annotation_index(STATE.index)
                _load_current_structure()
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
        anno_path = _resolve_annotation_path(path)
        prev_value = read_structure_annotations(anno_path).get(label_key)
        annotate_structure_file(anno_path, label_key, value)

        # Record for undo (bounded stack).
        STATE.undo_history.append({
            "index": STATE.index,
            "path": anno_path,
            "label_key": label_key,
            "prev_value": prev_value,
            "value": value,
        })
        if len(STATE.undo_history) > 100:
            STATE.undo_history.pop(0)
        STATE.annotated_indices.add(STATE.index)

        context = {
            "object": obj_name,
            "file": path,
            "index": str(STATE.index),
            "count": str(len(STATE.files)),
            "label": label_key,
            "value": value,
        }
        _run_pymol_commands(STATE.bucket_cmds.get(value, ""), context)
        _save_current_dataset_state()
        _record_current_config_history()

        print(f"Annotated {os.path.basename(path)}: {label_key}={value}")
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
        label_key = entry["label_key"]
        prev_value = entry["prev_value"]
        index = entry["index"]

        if prev_value is None:
            remove_structure_annotation(path, label_key)
            STATE.annotated_indices.discard(index)
            restored = "(none)"
        else:
            annotate_structure_file(path, label_key, prev_value)
            STATE.annotated_indices.add(index)
            restored = prev_value

        print(f"Undo: {os.path.basename(path)} {label_key} -> {restored}")
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

    active = _active_dataset_directory()
    target_dir = active or STATE.dataset_dir or "."
    path = dataset_state_path(target_dir)
    result: Dict[str, object] = {
        "status": "inactive" if not active else "active",
        "dataset_dir": active,
        "state_path": path,
        "exists": os.path.isfile(_long_path(path)),
    }
    if result["exists"]:
        try:
            result["state"] = read_dataset_state(target_dir)
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


class StructureAnnotatorPanel(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Structure Annotator")
        self.setMinimumWidth(520)
        self._bucket_inputs: Dict[str, QtWidgets.QLineEdit] = {}
        self._value_buttons: List[QtWidgets.QPushButton] = []
        self._history_bindings: List[_HistoryCompletionBinding] = []
        self._bucket_history_bindings: List[_HistoryCompletionBinding] = []
        self._state_save_timer = QtCore.QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.timeout.connect(self._save_ui_state)
        _restore_latest_config_from_history()
        self._build_ui()
        self._install_history_completers()
        self._sync_from_state()
        self._rebuild_value_controls()
        self._refresh_status()

    def closeEvent(self, event) -> None:
        global _panel
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

        dataset_box = QtWidgets.QGroupBox("Dataset")
        dataset_layout = QtWidgets.QGridLayout(dataset_box)

        self.dir_edit = QtWidgets.QLineEdit()
        self.dir_browse = QtWidgets.QPushButton("Browse…")
        self.dir_browse.clicked.connect(self._browse_dir)

        self.pattern_edit = QtWidgets.QLineEdit()
        self.pattern_edit.editingFinished.connect(self._save_ui_state)

        self.load_btn = QtWidgets.QPushButton("Load")
        self.load_btn.clicked.connect(self._load_dataset)

        dataset_layout.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        dataset_layout.addWidget(self.dir_edit, 0, 1)
        dataset_layout.addWidget(self.dir_browse, 0, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Pattern:"), 1, 0)
        dataset_layout.addWidget(self.pattern_edit, 1, 1, 1, 2)
        dataset_layout.addWidget(self.load_btn, 2, 2)

        layout.addWidget(dataset_box)

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
        config_layout.addWidget(self.restore_last_cb, 9, 1)

        config_layout.addWidget(QtWidgets.QLabel("On step (after load):"), 10, 0)
        config_layout.addWidget(self.step_cmd_edit, 10, 1)

        layout.addWidget(config_box)

        self.values_box = QtWidgets.QGroupBox("Buckets")
        self.values_layout = QtWidgets.QVBoxLayout(self.values_box)
        layout.addWidget(self.values_box)

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
        STATE.restore_last_dataset = bool(self.restore_last_cb.isChecked())
        STATE.step_cmd = self.step_cmd_edit.toPlainText().strip() or ""
        for value, edit in self._bucket_inputs.items():
            STATE.bucket_cmds[value] = edit.text()
        if persist:
            _save_current_dataset_state()

    def _sync_from_state(self) -> None:
        self.dir_edit.setText(STATE.dataset_dir or ".")
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
        self.restore_last_cb.setChecked(bool(STATE.restore_last_dataset))
        self.step_cmd_edit.setPlainText(STATE.step_cmd or "")

    def _load_dataset(self) -> None:
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
        default = os.path.join(STATE.dataset_dir or ".", f"annotations_{label_key}.csv")
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

    def _refresh_status(self) -> None:
        if not STATE.files:
            self.status_label.setText("No dataset loaded.")
            self.goto_spin.setRange(1, 1)
            self.undo_btn.setEnabled(False)
            return

        _ensure_navigation_session()

        # Keep the go-to control in range with the loaded dataset.
        self.goto_spin.setRange(1, len(STATE.files))
        self.goto_spin.blockSignals(True)
        self.goto_spin.setValue(STATE.index + 1)
        self.goto_spin.blockSignals(False)
        self.undo_btn.setEnabled(bool(STATE.undo_history))

        path = STATE.current_file() or ""
        basename = os.path.basename(path)
        annotated = len(STATE.annotated_indices)
        total = len(STATE.files)
        remaining = total - annotated
        status_text = (
            f"{STATE.index + 1}/{total}: {basename}\n"
            f"Progress: {annotated} annotated, {remaining} remaining "
            f"(label '{normalize_label_key(STATE.label_name)}')"
        )

        # Show existing annotations for current file, emphasizing the active label.
        label_key = normalize_label_key(STATE.label_name)
        current_value = None
        try:
            annotations = read_structure_annotations(path)
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
extend_command(cmd, "sa_bind_keys", sa_bind_keys)
extend_command(cmd, "sa_unbind_keys", sa_unbind_keys)
extend_command(cmd, "show_structure_annotator_panel", show_structure_annotator_panel)
