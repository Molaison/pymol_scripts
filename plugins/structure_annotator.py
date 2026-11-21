from pymol import cmd
from pymol.Qt import QtCore, QtWidgets
import datetime
import gzip
import os
import re
import shutil
import stat
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple
import traceback
import sys
from uuid import uuid4

STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
OBJECT_PREFIX = "sa"
REMARK_PREFIX = "REMARK 950 PYMOL_ANNOTATOR"
PDB_ANNOTATION_MARKER = re.compile(rf"^{re.escape(REMARK_PREFIX)}(?=[ \t]+)")
CIF_ANNOTATION_MARKER = re.compile(r"^#[ \t]*PYMOL_ANNOTATOR(?=[ \t]+)")
WRITER_TIMESTAMP_PATTERN = re.compile(
    r"[ \t]+ts=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\Z"
)


def _long_path(path: str) -> str:
    """
    Convert path to Windows long path format if needed.
    Windows has a 260 character path limit unless using \\?\ prefix.
    """
    if sys.platform != "win32":
        return path
    if not path or path.startswith("\\\\?\\"):
        return path
    # Convert to absolute path and add long path prefix
    abs_path = os.path.abspath(path)
    if len(abs_path) >= 240:  # Add prefix for long paths
        return "\\\\?\\" + abs_path
    return path


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


def _copy_windows_source(source: str, destination: str) -> None:
    """Stage an NTFS copy with the original file's access and encryption."""
    from ctypes import POINTER, WinDLL, WinError, byref, c_void_p, get_last_error
    from ctypes import wintypes

    security = WinDLL("advapi32", use_last_error=True)
    kernel = WinDLL("kernel32", use_last_error=True)
    get_info = security.GetNamedSecurityInfoW
    get_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD] + [c_void_p] * 5
    get_info.restype = wintypes.DWORD
    set_info = security.SetNamedSecurityInfoW
    set_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD] + [c_void_p] * 4
    set_info.restype = wintypes.DWORD
    get_control = security.GetSecurityDescriptorControl
    get_control.argtypes = [c_void_p, POINTER(wintypes.WORD), POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    security.EqualSid.argtypes = [c_void_p, c_void_p]
    security.EqualSid.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [c_void_p]
    kernel.LocalFree.restype = c_void_p
    copy_file = kernel.CopyFileW
    copy_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.BOOL]
    copy_file.restype = wintypes.BOOL

    file_object, owner_info, dacl_info = 1, 1, 4
    owner, dacl, descriptor = c_void_p(), c_void_p(), c_void_p()
    staged_owner, staged_descriptor = c_void_p(), c_void_p()
    try:
        error = get_info(source, file_object, owner_info | dacl_info,
                         byref(owner), None, byref(dacl), None, byref(descriptor))
        if error:
            raise WinError(error)
        error = get_info(destination, file_object, owner_info,
                         byref(staged_owner), None, None, None, byref(staged_descriptor))
        if error:
            raise WinError(error)
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not get_control(descriptor, byref(control), byref(revision)):
            raise WinError(get_last_error())
        # SE_DACL_PROTECTED prevents the parent directory from contributing ACEs.
        inheritance = 0x80000000 if control.value & 0x1000 else 0x20000000
        set_owner = not security.EqualSid(owner, staged_owner)
        info = dacl_info | inheritance | (owner_info if set_owner else 0)
        error = set_info(destination, file_object, info, owner if set_owner else None, None, dacl, None)
        if error:
            raise WinError(error)
    finally:
        kernel.LocalFree(descriptor)
        kernel.LocalFree(staged_descriptor)

    # The system copy carries EFS and alternate streams into the protected staging file.
    if not copy_file(source, destination, False):
        raise WinError(get_last_error())


def _write_text_file(path: str, text: str) -> None:
    path = _long_path(path)
    target = os.path.realpath(path)
    with open(target, "r+b") as source:
        target_mode = stat.S_IMODE(os.fstat(source.fileno()).st_mode)
    fd, temp_path = tempfile.mkstemp(
        prefix=".sa-write-", suffix=".tmp", dir=os.path.dirname(target)
    )
    try:
        os.close(fd)
        if sys.platform == "win32":
            _copy_windows_source(target, temp_path)
        payload = text.encode("utf-8")
        # Update just the default stream of the copy without recreating its file.
        with open(temp_path, "r+b") as fh:
            if path.lower().endswith(".gz"):
                with gzip.GzipFile(filename=os.path.basename(path), fileobj=fh, mode="wb") as compressed:
                    compressed.write(payload)
            else:
                fh.write(payload)
            fh.truncate()
        os.chmod(temp_path, target_mode)
        os.replace(temp_path, target)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        except PermissionError:
            os.chmod(temp_path, stat.S_IWRITE | stat.S_IREAD)
            os.unlink(temp_path)


def _iter_text_lines(path: str) -> Iterator[str]:
    """Yield a structure file one line at a time, including compressed files."""
    path = _long_path(path)
    if path.lower().endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            yield from fh
        return
    with open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
        yield from fh


def _parse_annotation_record(line: str, marker) -> Optional[Tuple[str, str]]:
    """Parse one complete annotator line without imposing a value token shape."""
    marker_match = marker.match(line)
    if not marker_match:
        return None

    payload = line[marker_match.end():].rstrip("\r\n")
    match = re.match(r"[ \t]+([^ \t=]+)=(.*)\Z", payload)
    if not match:
        return None

    value = match.group(2)
    timestamp_match = WRITER_TIMESTAMP_PATTERN.search(value)
    if timestamp_match:
        value = value[: timestamp_match.start()]
    return match.group(1), value


def _iter_annotation_records(path: str, marker) -> Iterator[Tuple[str, str]]:
    """Stream annotation records from a structure file using one marker."""
    for line in _iter_text_lines(path):
        record = _parse_annotation_record(line, marker)
        if record is not None:
            yield record


def _read_annotation_records(path: str, marker) -> Dict[str, str]:
    result = {}
    try:
        for key, value in _iter_annotation_records(path, marker):
            result[key] = value
    except Exception:
        pass
    return result


def _strip_existing_annotations(lines: List[str], marker, label_key: str) -> List[str]:
    filtered = []
    for line in lines:
        record = _parse_annotation_record(line, marker)
        if record is None or record[0] != label_key:
            filtered.append(line)
    return filtered


def _insert_annotation_line(
    lines: List[str], index: int, annotation: str, has_trailing_newline: bool
) -> None:
    """Insert a standalone record while preserving the file's final newline state."""
    if index > 0 and not lines[index - 1].endswith(("\n", "\r")):
        lines[index - 1] += "\n"
    if index == len(lines) and not has_trailing_newline:
        annotation = annotation.rstrip("\r\n")
    lines.insert(index, annotation)


def _join_text_with_trailing_state(lines: List[str], has_trailing_newline: bool) -> str:
    text = "".join(lines)
    if has_trailing_newline:
        if not text.endswith(("\n", "\r")):
            text += "\n"
    elif text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith(("\n", "\r")):
        text = text[:-1]
    return text


def read_pdb_annotations(path: str) -> Dict[str, str]:
    """Read all PYMOL_ANNOTATOR remarks from a PDB file."""
    return _read_annotation_records(path, PDB_ANNOTATION_MARKER)


def read_cif_annotations(path: str) -> Dict[str, str]:
    """Read all PYMOL_ANNOTATOR comments from a CIF file."""
    return _read_annotation_records(path, CIF_ANNOTATION_MARKER)


def read_structure_annotations(path: str) -> Dict[str, str]:
    """Read existing annotations from a structure file."""
    lower = path.lower()
    if lower.endswith(".pdb") or lower.endswith(".pdb.gz"):
        return read_pdb_annotations(path)
    if lower.endswith(".cif") or lower.endswith(".cif.gz"):
        return read_cif_annotations(path)
    return {}


def has_annotation_fast(path: str, label_key: str) -> bool:
    """
    Fast check if a structure file has a specific annotation.
    Streams records in file order and stops as soon as the requested key matches.
    """
    lower = path.lower()
    if lower.endswith(".pdb") or lower.endswith(".pdb.gz"):
        marker = PDB_ANNOTATION_MARKER
    elif lower.endswith(".cif") or lower.endswith(".cif.gz"):
        marker = CIF_ANNOTATION_MARKER
    else:
        return False

    try:
        for key, _value in _iter_annotation_records(path, marker):
            if key == label_key:
                return True
    except Exception:
        return False
    return False


def write_pdb_annotation(path: str, label_key: str, label_value: str) -> None:
    text = _read_text_file(path)
    has_trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines(keepends=True)

    lines = _strip_existing_annotations(lines, PDB_ANNOTATION_MARKER, label_key)

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    remark = f"{REMARK_PREFIX} {label_key}={label_value} ts={timestamp}\n"

    insert_at = len(lines)
    for idx in range(len(lines) - 1, -1, -1):
        stripped = lines[idx].strip()
        if stripped == "END" or stripped == "ENDMDL":
            insert_at = idx
            break
    _insert_annotation_line(lines, insert_at, remark, has_trailing_newline)

    out = _join_text_with_trailing_state(lines, has_trailing_newline)
    _write_text_file(path, out)


def write_cif_annotation(path: str, label_key: str, label_value: str) -> None:
    """Write annotation as a CIF comment to avoid breaking strict parsers like Rosetta."""
    text = _read_text_file(path)
    has_trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines(keepends=True)

    lines = _strip_existing_annotations(lines, CIF_ANNOTATION_MARKER, label_key)

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    # Use comment format similar to PDB REMARK
    comment = f"# PYMOL_ANNOTATOR {label_key}={label_value} ts={timestamp}\n"
    _insert_annotation_line(lines, len(lines), comment, has_trailing_newline)

    out = _join_text_with_trailing_state(lines, has_trailing_newline)
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
    has_trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines(keepends=True)
    lower = path.lower()
    if lower.endswith(".pdb") or lower.endswith(".pdb.gz"):
        lines = _strip_existing_annotations(lines, PDB_ANNOTATION_MARKER, label_key)
    elif lower.endswith(".cif") or lower.endswith(".cif.gz"):
        lines = _strip_existing_annotations(lines, CIF_ANNOTATION_MARKER, label_key)
    else:
        raise ValueError(f"Unsupported file type: {path}")
    out = _join_text_with_trailing_state(lines, has_trailing_newline)
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
    object_marks: Dict[int, str] = field(default_factory=dict)
    current_object: Optional[str] = None
    undo_history: List[dict] = field(default_factory=list)
    annotated_indices: set = field(default_factory=set)

    def values(self) -> List[str]:
        return _split_values(self.values_text) or ["Good", "Bad"]

    def current_file(self) -> Optional[str]:
        if not self.files:
            return None
        return self.files[self.index % len(self.files)]


STATE = globals().get("STATE", AnnotatorState())

# Back-fill fields added in newer versions when reusing a STATE from a prior load.
if not hasattr(STATE, "undo_history"):
    STATE.undo_history = []
if not hasattr(STATE, "annotated_indices"):
    STATE.annotated_indices = set()
if not hasattr(STATE, "object_marks"):
    STATE.object_marks = {}


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
    for entry in os.listdir(path):
        if compiled.match(entry):
            full_path = os.path.join(path, entry)
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


def _object_name_for_index(path: str, index: int) -> str:
    base = os.path.basename(path)
    base_lower = base.lower()
    for ext in (".pdb.gz", ".cif.gz", ".pdb", ".cif"):
        if base_lower.endswith(ext):
            base = base[: -len(ext)]
            base_lower = base_lower[: -len(ext)]
            break
    base_token = _sanitize_token(base, "structure")
    return f"{OBJECT_PREFIX}_{index + 1:05d}_{base_token}"


def _tracked_objects(indices=None) -> Dict[int, str]:
    if not STATE.object_marks:
        return {}
    selections = set(cmd.get_names("selections"))
    tracked = {}
    requested = STATE.object_marks if indices is None else indices
    for index in requested:
        marker = STATE.object_marks.get(index)
        if marker in selections:
            names = cmd.get_object_list(marker)
            if len(names) == 1:
                tracked[index] = names[0]
    return tracked


def _enabled_managed_objects(indices=None) -> Dict[int, str]:
    """Find displayed models through their atom selections."""
    try:
        enabled = set(cmd.get_names("objects", enabled_only=1))
        return {index: obj for index, obj in _tracked_objects(indices).items() if obj in enabled}
    except Exception:
        return {}


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


def _delete_managed_objects(keep_indices=()) -> None:
    removed = STATE.object_marks.keys() - set(keep_indices)
    for obj in _tracked_objects(removed).values():
        cmd.delete(obj)
    for index in removed:
        cmd.delete(STATE.object_marks.pop(index))


def _desired_indices_ahead() -> List[int]:
    if not STATE.files:
        return []
    ahead = max(0, int(STATE.preload_ahead or 0))
    return [(STATE.index + offset) % len(STATE.files) for offset in range(ahead + 1)]


def _evict_unneeded_objects() -> None:
    if STATE.keep_previous:
        return
    if not STATE.files:
        _delete_managed_objects()
        STATE.current_object = None
        return

    if not STATE.preload_enabled or int(STATE.preload_ahead or 0) <= 0:
        desired = {STATE.index}
    else:
        desired = set(_desired_indices_ahead())

    _delete_managed_objects(desired)
    _evict_decompressed_except(desired)


def _ensure_loaded(index: int, focus: bool, allow_gz_fallback: bool = True) -> Optional[str]:
    if not STATE.files:
        return None
    index = index % len(STATE.files)
    path = STATE.files[index]
    loaded = _tracked_objects(None if focus else (index,))
    obj_name = loaded.get(index)
    if obj_name is None:
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
        obj_name = cmd.get_unused_name(_object_name_for_index(path, index), alwaysnumber=0)
        cmd.load(load_path_resolved, obj_name, quiet=1)
        cmd.sync()
        cmd.refresh()
        marker = f"_sa_owner_{uuid4().hex}"
        if not cmd.select(marker, f"model {obj_name}", enable=0, quiet=1):
            cmd.delete(marker)
            cmd.delete(obj_name)
            return None
        old_marker = STATE.object_marks.get(index)
        if old_marker:
            cmd.delete(old_marker)
        STATE.object_marks[index] = marker

    if not focus:
        try:
            cmd.disable(obj_name)
        except Exception:
            pass
        return obj_name

    for other_index, other in loaded.items():
        if other_index != index:
            try:
                cmd.disable(other)
            except Exception:
                pass
    try:
        cmd.enable(obj_name)
    except Exception:
        pass

    STATE.current_object = obj_name

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
    loaded = _tracked_objects(desired)
    pending = [idx for idx in desired if idx not in loaded]

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


def _load_current_structure() -> None:
    if not STATE.current_file():
        print("Structure Annotator: no dataset loaded")
        return
    if not STATE.keep_previous and not STATE.preload_enabled:
        _delete_managed_objects()

    STATE.current_object = None
    _ensure_loaded(STATE.index, focus=True, allow_gz_fallback=True)
    _evict_unneeded_objects()
    _kick_preload()


def _rescan_annotated_indices() -> None:
    """Populate STATE.annotated_indices for progress tracking under the current label."""
    STATE.annotated_indices = set()
    if not STATE.files:
        return
    label_key = normalize_label_key(STATE.label_name)
    for idx, path in enumerate(STATE.files):
        try:
            if has_annotation_fast(path, label_key):
                STATE.annotated_indices.add(idx)
        except Exception:
            pass


def sa_load_dir(path: str = ".", pattern: str = STRUCTURE_PATTERN) -> None:
    """
    Load a directory of structures for annotation.

    Args:
        path: Directory containing .pdb/.cif (optionally .gz).
        pattern: Regex to filter files.
    """
    try:
        label_key = normalize_label_key(STATE.label_name) if STATE.skip_annotated else None
        files = _list_structure_files(
            os.path.abspath(path), pattern, STATE.prefer_gz,
            skip_annotated=STATE.skip_annotated, label_key=label_key
        )
        if _preload_panel_timer is not None:
            _preload_panel_timer.stop()
        _preload_queue.clear()
        _reset_bg_cache()
        _delete_managed_objects()
        STATE.current_object = None
        STATE.dataset_dir, STATE.pattern, STATE.files = path, pattern, files
        STATE.index = 0
        STATE.undo_history.clear()
        STATE.annotated_indices.clear()
        if not STATE.files:
            print(f"Structure Annotator: no files matched in {path}")
            return
        if not STATE.skip_annotated:
            _rescan_annotated_indices()
        _load_current_structure()
        annotated = len(STATE.annotated_indices)
        print(
            f"Structure Annotator: loaded {len(STATE.files)} files from {path} "
            f"({annotated} already annotated for '{normalize_label_key(STATE.label_name)}')"
        )
    except Exception as e:
        print(f"Structure Annotator load error: {e}")
        traceback.print_exc()


def sa_next() -> None:
    try:
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return
        STATE.index = (STATE.index + 1) % len(STATE.files)
        _load_current_structure()
    except Exception as e:
        print(f"Structure Annotator next error: {e}")
        traceback.print_exc()


def sa_prev() -> None:
    try:
        if not STATE.files:
            print("Structure Annotator: no dataset loaded (use sa_load_dir)")
            return
        STATE.index = (STATE.index - 1) % len(STATE.files)
        _load_current_structure()
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
        if not path:
            print("Structure Annotator: nothing to annotate")
            return
        obj_name = _enabled_managed_objects((STATE.index,)).get(STATE.index)
        if not obj_name:
            print("Structure Annotator: the current structure is not displayed. Click Reload before annotating.")
            return
        STATE.current_object = obj_name

        label_key = normalize_label_key(STATE.label_name)
        anno_path = path
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
        entry = STATE.undo_history[-1]
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

        STATE.undo_history.pop()
        print(f"Undo: {os.path.basename(path)} {label_key} -> {restored}")
        # Return to the file we just changed so the user can see the result.
        if 0 <= index < len(STATE.files):
            STATE.index = index
            _load_current_structure()
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
        STATE.index = target
        _load_current_structure()
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
                STATE.index = candidate
                _load_current_structure()
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
            annotations = read_structure_annotations(path)
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


@cmd.set_key("ALT-right")
def _sa_key_next():
    sa_next()


@cmd.set_key("ALT-left")
def _sa_key_prev():
    sa_prev()


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


def _register_number_key_bindings() -> None:
    """Bind digit keys 1..9 to the corresponding bucket. Best-effort; ignore failures."""
    for digit in range(1, 10):
        def _handler(d=digit):
            _sa_annotate_nth(d)
        try:
            cmd.set_key(str(digit), _handler)
        except Exception:
            pass


try:
    @cmd.set_key("ALT-z")
    def _sa_key_undo():
        sa_undo()
        if _panel is not None:
            try:
                _panel._refresh_status()
            except Exception:
                pass
except Exception:
    pass

_register_number_key_bindings()


_panel = globals().get("_panel", None)


class StructureAnnotatorPanel(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Structure Annotator")
        self.setMinimumWidth(520)
        font = self.font()
        font.setPixelSize(14)
        self.setFont(font)
        if sys.platform == "darwin":
            self.setAttribute(QtCore.Qt.WA_MacSmallSize)
        self._bucket_inputs: Dict[str, QtWidgets.QLineEdit] = {}
        self._value_buttons: List[QtWidgets.QPushButton] = []
        self._build_ui()
        self._sync_from_state()
        self._rebuild_value_controls()
        self._refresh_status()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        groups = QtWidgets.QWidget()
        groups_layout = QtWidgets.QVBoxLayout(groups)
        groups_layout.setContentsMargins(0, 0, 0, 0)
        groups_layout.setSpacing(8)
        scroll.setWidget(groups)
        layout.addWidget(scroll, 1)

        dataset_box = QtWidgets.QGroupBox("Dataset")
        dataset_layout = QtWidgets.QGridLayout(dataset_box)
        dataset_layout.setContentsMargins(10, 10, 10, 10)
        dataset_layout.setHorizontalSpacing(10)
        dataset_layout.setVerticalSpacing(6)
        dataset_layout.setColumnStretch(1, 1)

        self.dir_edit = QtWidgets.QLineEdit()
        self.dir_browse = QtWidgets.QPushButton("Browse…")
        self.dir_browse.clicked.connect(self._browse_dir)

        self.pattern_edit = QtWidgets.QLineEdit()

        self.load_btn = QtWidgets.QPushButton("Load")
        self.load_btn.clicked.connect(self._load_dataset)

        dataset_layout.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        dataset_layout.addWidget(self.dir_edit, 0, 1)
        dataset_layout.addWidget(self.dir_browse, 0, 2)
        dataset_layout.addWidget(QtWidgets.QLabel("Pattern:"), 1, 0)
        dataset_layout.addWidget(self.pattern_edit, 1, 1, 1, 2)
        dataset_layout.addWidget(self.load_btn, 2, 2)

        groups_layout.addWidget(dataset_box)

        config_box = QtWidgets.QGroupBox("Annotation")
        config_layout = QtWidgets.QGridLayout(config_box)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setHorizontalSpacing(10)
        config_layout.setVerticalSpacing(6)
        config_layout.setColumnStretch(1, 1)

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

        self.step_cmd_edit = QtWidgets.QPlainTextEdit()
        self.step_cmd_edit.setPlaceholderText("PyMOL commands; use {object}, {file}, {index}, {count}")
        self.step_cmd_edit.setMaximumBlockCount(200)
        self.step_cmd_edit.setFixedHeight(64)

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
        preload_row.setContentsMargins(0, 0, 0, 0)
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

        config_layout.addWidget(QtWidgets.QLabel("On step (after load):"), 9, 0)
        config_layout.addWidget(self.step_cmd_edit, 9, 1)

        groups_layout.addWidget(config_box)

        self.values_box = QtWidgets.QGroupBox("Buckets")
        self.values_layout = QtWidgets.QVBoxLayout(self.values_box)
        self.values_layout.setContentsMargins(10, 10, 10, 10)
        self.values_layout.setSpacing(6)
        groups_layout.addWidget(self.values_box)
        groups_layout.addStretch(1)

        nav_row = QtWidgets.QHBoxLayout()
        keys = ("⌥←", "⌥→", "⌥Z") if sys.platform == "darwin" else ("Alt+Left", "Alt+Right", "Alt+Z")
        previous_key, next_key, undo_key = keys
        self.prev_btn = QtWidgets.QPushButton(f"Prev ({previous_key})")
        self.next_btn = QtWidgets.QPushButton(f"Next ({next_key})")
        self.next_unann_btn = QtWidgets.QPushButton("Next Unannotated")
        self.undo_btn = QtWidgets.QPushButton(f"Undo ({undo_key})")
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
        self.status_label.setTextFormat(QtCore.Qt.PlainText)
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

    def _browse_dir(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Dataset Directory", self.dir_edit.text() or "."
        )
        if directory:
            self.dir_edit.setText(directory)

    def _sync_to_state(self) -> None:
        STATE.dataset_dir = self.dir_edit.text().strip() or "."
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
        STATE.step_cmd = self.step_cmd_edit.toPlainText().strip() or ""
        for value, edit in self._bucket_inputs.items():
            STATE.bucket_cmds[value] = edit.text()

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
        self.step_cmd_edit.setPlainText(STATE.step_cmd or "")

    def _load_dataset(self) -> None:
        self._sync_to_state()
        sa_load_dir(STATE.dataset_dir, STATE.pattern)
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
        active = _enabled_managed_objects() if STATE.current_object else {}
        if active and STATE.index not in active:
            STATE.index = next(iter(active))
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

    def _rebuild_value_controls(self) -> None:
        self._sync_to_state()
        values = STATE.values()

        while self.values_layout.count():
            item = self.values_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._bucket_inputs = {}
        self._value_buttons = []

        if len(values) < 5:
            btn_row = QtWidgets.QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            for i, value in enumerate(values):
                hint = f" ({i + 1})" if i < 9 else ""
                btn = QtWidgets.QPushButton(f"{value}{hint}")
                btn.clicked.connect(lambda checked=False, v=value: self._annotate(v))
                self._value_buttons.append(btn)
                btn_row.addWidget(btn)
            btn_row.addStretch()
            btn_widget = QtWidgets.QWidget()
            btn_widget.setLayout(btn_row)
            self.values_layout.addWidget(btn_widget)

        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        for value in values:
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText("On bucket cmd (optional); e.g. set_name {object}, good_{object}")
            edit.setText(STATE.bucket_cmds.get(value, ""))
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
            self.status_label.setToolTip("")
            self.goto_spin.setRange(1, 1)
            self.undo_btn.setEnabled(False)
            return

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

        self.status_label.setToolTip(path)

        # Highlight the bucket button matching the current file's annotation.
        for btn in self._value_buttons:
            label = btn.text().split(" (")[0]
            if current_value is not None and label == current_value:
                btn.setStyleSheet("font-weight: bold; background-color: #cdebc5; color: #173421;")
            else:
                btn.setStyleSheet("")

        if STATE.current_object and STATE.index not in _enabled_managed_objects((STATE.index,)):
            status_text += "\n\nCurrent structure is unavailable. Click Reload before annotating."

        self.status_label.setText(status_text)


def show_structure_annotator_panel() -> QtWidgets.QDialog:
    global _panel
    if _panel is None:
        parent = QtWidgets.QApplication.activeWindow()
        _panel = StructureAnnotatorPanel(parent=parent)
        handle = parent.windowHandle() if parent is not None else None
        screen = handle.screen() if handle is not None else QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry().size() - QtCore.QSize(32, 48)
        _panel.resize(QtCore.QSize(690, 865).boundedTo(available))
    _panel.show()
    _panel.raise_()
    _panel.activateWindow()
    return _panel


def __init_plugin__(app=None) -> None:
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("Structure Annotator", show_structure_annotator_panel)


cmd.extend("sa_load_dir", sa_load_dir)
cmd.extend("sa_next", sa_next)
cmd.extend("sa_prev", sa_prev)
cmd.extend("sa_annotate", sa_annotate)
cmd.extend("sa_undo", sa_undo)
cmd.extend("sa_goto", sa_goto)
cmd.extend("sa_next_unannotated", sa_next_unannotated)
cmd.extend("sa_export_csv", sa_export_csv)
cmd.extend("show_structure_annotator_panel", show_structure_annotator_panel)
