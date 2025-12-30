from pymol import cmd
from pymol.Qt import QtCore, QtWidgets
import datetime
import gzip
import os
import re
import shutil
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import traceback

STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
OBJECT_PREFIX = "sa"
REMARK_PREFIX = "REMARK 950 PYMOL_ANNOTATOR"
SAVE_FRAME_PREFIX = "save_pymol_annotator_"


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
    if path.lower().endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    with open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write_text_file(path: str, text: str) -> None:
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


def _cif_quote_value(value: str) -> List[str]:
    if value is None:
        return ["."]
    value = str(value)
    if "\n" in value or "\r" in value:
        return [";", value.rstrip("\n\r"), ";"]
    if not value or re.search(r"\s|['\"]|;", value):
        # Use semicolon quoting to avoid CIF quoting edge-cases.
        return [";", value, ";"]
    return [value]


def _strip_existing_cif_save_frame(lines: List[str], save_name: str) -> List[str]:
    stripped = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == save_name:
            i += 1
            while i < len(lines):
                if lines[i].strip() == "save_":
                    i += 1
                    break
                i += 1
            continue
        stripped.append(line)
        i += 1
    return stripped


def write_cif_annotation(path: str, label_key: str, label_value: str) -> None:
    text = _read_text_file(path)
    has_trailing_newline = text.endswith("\n")
    lines = text.splitlines(keepends=True)

    save_name = f"{SAVE_FRAME_PREFIX}{_sanitize_token(label_key, 'label')}"
    lines = _strip_existing_cif_save_frame(lines, save_name)

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    frame_lines = [f"\n{save_name}\n"]

    for tag, value in (
        ("_pymol_annotator.label_name", label_key),
        ("_pymol_annotator.label_value", label_value),
        ("_pymol_annotator.timestamp", timestamp),
    ):
        quoted = _cif_quote_value(value)
        if len(quoted) == 1:
            frame_lines.append(f"{tag} {quoted[0]}\n")
        else:
            frame_lines.append(f"{tag}\n")
            for entry in quoted:
                frame_lines.append(f"{entry}\n")

    frame_lines.append("save_\n")
    lines.extend(frame_lines)

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
    preload_enabled: bool = True
    preload_ahead: int = 2
    preload_interval_ms: int = 250
    background_decompress_gz: bool = True
    managed_objects: List[str] = field(default_factory=list)
    current_object: Optional[str] = None

    def values(self) -> List[str]:
        return _split_values(self.values_text) or ["Good", "Bad"]

    def current_file(self) -> Optional[str]:
        if not self.files:
            return None
        return self.files[self.index % len(self.files)]


STATE = globals().get("STATE", AnnotatorState())


def _structure_dedupe_key(file_path: str) -> str:
    name = os.path.basename(file_path).lower()
    if name.endswith(".gz"):
        name = name[: -len(".gz")]
    for ext in (".pdb", ".cif"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return name


def _list_structure_files(path: str, pattern: str, prefer_gz: bool) -> List[str]:
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
    return f"{OBJECT_PREFIX}_{index + 1:05d}_{base_token}"


def _object_exists(object_name: str) -> bool:
    try:
        return object_name in set(cmd.get_object_list())
    except Exception:
        return False


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
            if requested and os.path.exists(requested):
                load_path = requested
            elif not allow_gz_fallback:
                return None
        cmd.load(load_path, obj_name, quiet=1)
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


def _load_current_structure() -> None:
    if not STATE.current_file():
        print("Structure Annotator: no dataset loaded")
        return
    if not STATE.keep_previous and not STATE.preload_enabled:
        _delete_managed_objects(False)

    _ensure_loaded(STATE.index, focus=True, allow_gz_fallback=True)
    _evict_unneeded_objects()
    _kick_preload()


def sa_load_dir(path: str = ".", pattern: str = STRUCTURE_PATTERN) -> None:
    """
    Load a directory of structures for annotation.

    Args:
        path: Directory containing .pdb/.cif (optionally .gz).
        pattern: Regex to filter files.
    """
    try:
        STATE.dataset_dir = path
        STATE.pattern = pattern
        _reset_bg_cache()
        STATE.files = _list_structure_files(path, pattern, STATE.prefer_gz)
        STATE.index = 0
        STATE.managed_objects.clear()
        STATE.current_object = None
        if not STATE.files:
            print(f"Structure Annotator: no files matched in {path}")
            return
        _load_current_structure()
        print(f"Structure Annotator: loaded {len(STATE.files)} files from {path}")
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
        obj_name = STATE.current_object
        if not path or not obj_name:
            print("Structure Annotator: nothing to annotate")
            return

        label_key = normalize_label_key(STATE.label_name)
        annotate_structure_file(_resolve_annotation_path(path), label_key, value)

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


@cmd.set_key("ALT-right")
def _sa_key_next():
    sa_next()


@cmd.set_key("ALT-left")
def _sa_key_prev():
    sa_prev()


_panel = globals().get("_panel", None)


class StructureAnnotatorPanel(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Structure Annotator")
        self.setMinimumWidth(520)
        self._bucket_inputs: Dict[str, QtWidgets.QLineEdit] = {}
        self._value_buttons: List[QtWidgets.QPushButton] = []
        self._build_ui()
        self._sync_from_state()
        self._rebuild_value_controls()
        self._refresh_status()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        dataset_box = QtWidgets.QGroupBox("Dataset")
        dataset_layout = QtWidgets.QGridLayout(dataset_box)

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

        layout.addWidget(dataset_box)

        config_box = QtWidgets.QGroupBox("Annotation")
        config_layout = QtWidgets.QGridLayout(config_box)

        self.label_edit = QtWidgets.QLineEdit()
        self.values_edit = QtWidgets.QLineEdit()
        self.values_edit.editingFinished.connect(self._rebuild_value_controls)

        self.auto_advance_cb = QtWidgets.QCheckBox("Auto-advance after annotate")
        self.keep_previous_cb = QtWidgets.QCheckBox("Keep previous loaded objects")
        self.prefer_gz_cb = QtWidgets.QCheckBox("Prefer .gz when both exist")
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
        self.step_cmd_edit.setFixedHeight(80)

        config_layout.addWidget(QtWidgets.QLabel("Label name:"), 0, 0)
        config_layout.addWidget(self.label_edit, 0, 1)
        config_layout.addWidget(QtWidgets.QLabel("Values:"), 1, 0)
        config_layout.addWidget(self.values_edit, 1, 1)
        config_layout.addWidget(self.auto_advance_cb, 2, 1)
        config_layout.addWidget(self.keep_previous_cb, 3, 1)
        config_layout.addWidget(self.prefer_gz_cb, 4, 1)
        config_layout.addWidget(self.preload_enabled_cb, 5, 1)
        preload_row = QtWidgets.QHBoxLayout()
        preload_row.addWidget(QtWidgets.QLabel("Preload ahead:"))
        preload_row.addWidget(self.preload_ahead_spin)
        preload_row.addSpacing(12)
        preload_row.addWidget(QtWidgets.QLabel("Interval:"))
        preload_row.addWidget(self.preload_interval_spin)
        preload_row.addStretch()
        preload_row_widget = QtWidgets.QWidget()
        preload_row_widget.setLayout(preload_row)
        config_layout.addWidget(preload_row_widget, 6, 1)
        config_layout.addWidget(self.bg_decompress_cb, 7, 1)

        config_layout.addWidget(QtWidgets.QLabel("On step (after load):"), 8, 0)
        config_layout.addWidget(self.step_cmd_edit, 8, 1)

        layout.addWidget(config_box)

        self.values_box = QtWidgets.QGroupBox("Buckets")
        self.values_layout = QtWidgets.QVBoxLayout(self.values_box)
        layout.addWidget(self.values_box)

        nav_row = QtWidgets.QHBoxLayout()
        self.prev_btn = QtWidgets.QPushButton("Prev (Alt+Left)")
        self.next_btn = QtWidgets.QPushButton("Next (Alt+Right)")
        self.reload_btn = QtWidgets.QPushButton("Reload")

        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.reload_btn.clicked.connect(self._reload)

        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        nav_row.addWidget(self.reload_btn)
        nav_row.addStretch()
        layout.addLayout(nav_row)

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
        _load_current_structure()
        self._refresh_status()

    def _apply_selected_value(self) -> None:
        self._sync_to_state()
        value = self.value_combo.currentText().strip()
        if not value:
            return
        sa_annotate(value)
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
            for value in values:
                btn = QtWidgets.QPushButton(value)
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
            return
        path = STATE.current_file() or ""
        basename = os.path.basename(path)
        self.status_label.setText(
            f"{STATE.index + 1}/{len(STATE.files)}: {basename}\n{path}"
        )


def show_structure_annotator_panel() -> QtWidgets.QDialog:
    global _panel
    if _panel is not None:
        _panel.raise_()
        _panel.activateWindow()
        return _panel
    _panel = StructureAnnotatorPanel(parent=QtWidgets.QApplication.activeWindow())
    _panel.show()
    return _panel


def __init_plugin__(app=None) -> None:
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("Structure Annotator", show_structure_annotator_panel)


cmd.extend("sa_load_dir", sa_load_dir)
cmd.extend("sa_next", sa_next)
cmd.extend("sa_prev", sa_prev)
cmd.extend("sa_annotate", sa_annotate)
cmd.extend("show_structure_annotator_panel", show_structure_annotator_panel)
