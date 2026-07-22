"""Legacy ``screen_boost`` commands backed by the canonical navigator.

This module intentionally owns configuration only.  Cursor, batch, window,
pin, managed-object, and key-binding state all live in ``navigation.py``.
Loading this file never binds keys or mutates the current PyMOL scene.
"""

from dataclasses import dataclass
import os
import re
import sys
import traceback
from typing import Dict, Iterable, List, Optional, Tuple

from pymol import cmd

try:
    from pymolplugins.core.parameters import as_bool, as_int
    from pymolplugins.navigation import commands as _navigation
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.parameters import as_bool, as_int  # type: ignore
    from pymolplugins.navigation import commands as _navigation  # type: ignore


STRUCTURE_PATTERN = _navigation.STRUCTURE_PATTERN
OBJECT_PREFIX = "structure"


@dataclass
class _LegacyConfig:
    path: str = "."
    pattern: str = STRUCTURE_PATTERN
    batch_size: int = 20
    window_size: int = 1
    auto_bucket: bool = True
    exclude_selected: bool = False


_CONFIG = globals().get("_CONFIG", _LegacyConfig())
_WARNED = globals().get("_WARNED", set())


def _warn_once(command: str) -> None:
    if command in _WARNED:
        return
    _WARNED.add(command)
    print(f"screen_boost: '{command}' is a compatibility command; prefer pp_nav_*")


def derive_object_name(filename: str) -> str:
    """Return the historical selected-file stem for a structure path."""
    base_name = os.path.basename(str(filename))
    lower_name = base_name.lower()
    for extension in (".pdb.gz", ".cif.gz", ".pdb", ".cif"):
        if lower_name.endswith(extension):
            base_name = base_name[: -len(extension)]
            break
    starts_with_underscore = base_name.startswith("_")
    value = re.sub(r"[^0-9A-Za-z_]+", "_", base_name).strip("_") or OBJECT_PREFIX
    if value[0].isdigit() or starts_with_underscore:
        value = f"{OBJECT_PREFIX}_{value}"
    return value


def _selected_dir() -> str:
    return os.path.join(os.path.abspath(os.path.expanduser(_CONFIG.path)), "selected")


def _selected_output_name(source_path: str, fallback: str) -> str:
    source = source_path or fallback
    return derive_object_name(source) + ".pdb"


def _matching_paths() -> List[str]:
    dataset = os.path.abspath(os.path.expanduser(_CONFIG.path))
    if not os.path.isdir(dataset):
        raise ValueError(f"dataset directory does not exist: {dataset}")
    try:
        pattern = re.compile(_CONFIG.pattern)
    except re.error as exc:
        raise ValueError(f"invalid file regex: {exc}") from exc
    selected = set()
    selected_dir = _selected_dir()
    if _CONFIG.exclude_selected and os.path.isdir(selected_dir):
        selected = {
            name for name in os.listdir(selected_dir) if name.lower().endswith(".pdb")
        }
    paths = []
    for name in sorted(os.listdir(dataset)):
        path = os.path.join(dataset, name)
        if not pattern.match(name) or not os.path.isfile(path):
            continue
        if _selected_output_name(path, name) in selected:
            continue
        paths.append(path)
    return paths


def _install_configured_hooks() -> Dict[str, object]:
    return _navigation.legacy_apply_switch_commands()


def _start_navigation(*, bind_keys: object = 1, replace: object = 1) -> Dict[str, object]:
    entries: object = ""
    if _CONFIG.exclude_selected:
        entries = _matching_paths()
        if not entries:
            raise ValueError("no unselected structure files matched")
    result = _navigation.pp_nav_start(
        source="files",
        entries=entries,
        path=_CONFIG.path,
        pattern=_CONFIG.pattern,
        batch_size=_CONFIG.batch_size,
        window_size=_CONFIG.window_size,
        stride=1,
        wrap=1,
        bind_keys=bind_keys,
        zoom_policy="current",
        replace=replace,
    )
    if result.get("status") == "error":
        return result
    return _install_configured_hooks()


def setup_loader(
    path=None,
    pattern=STRUCTURE_PATTERN,
    group_size=None,
    objects_per_step=None,
    exclude_selected=None,
) -> Dict[str, object]:
    """Configure the next legacy ``loadpath`` call without loading anything."""
    _warn_once("setup_loader")
    if path is not None:
        _CONFIG.path = str(path)
    if pattern is not None:
        re.compile(str(pattern))
        _CONFIG.pattern = str(pattern)
    if group_size is not None:
        _CONFIG.batch_size = as_int(group_size, minimum=1, name="group_size")
    if objects_per_step is not None:
        _CONFIG.window_size = as_int(
            objects_per_step, minimum=1, name="objects_per_step"
        )
    if _CONFIG.window_size > _CONFIG.batch_size:
        raise ValueError("objects_per_step cannot exceed group_size")
    if exclude_selected is not None:
        _CONFIG.exclude_selected = as_bool(exclude_selected)
    return legacy_status()


def load_by_path(
    path=".",
    pattern=STRUCTURE_PATTERN,
    bucket_size=10,
    objects_per_step=None,
    exclude_selected=0,
    bind_keys=1,
    replace=1,
) -> Dict[str, object]:
    """Start canonical file navigation with historical ``loadpath`` arguments."""
    _warn_once("loadpath")
    setup_loader(
        path=path,
        pattern=pattern,
        group_size=bucket_size,
        objects_per_step=(
            _CONFIG.window_size if objects_per_step is None else objects_per_step
        ),
        exclude_selected=exclude_selected,
    )
    try:
        result = _start_navigation(bind_keys=bind_keys, replace=replace)
        print(
            f"screen_boost adapter: path={_CONFIG.path}, "
            f"batch={_CONFIG.batch_size}, window={_CONFIG.window_size}, "
            f"exclude_selected={_CONFIG.exclude_selected}"
        )
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def load_by_bucket(path="."):
    """Yield canonical batch statuses for old generator-style callers."""
    yield load_by_path(
        path=path,
        pattern=_CONFIG.pattern,
        bucket_size=_CONFIG.batch_size,
        objects_per_step=_CONFIG.window_size,
        exclude_selected=_CONFIG.exclude_selected,
    )
    while True:
        yield next_bucket()


def next_bucket(path=None) -> Dict[str, object]:
    _warn_once("next_bucket")
    if path is not None and str(path) != _CONFIG.path:
        return load_by_path(
            path=path,
            pattern=_CONFIG.pattern,
            bucket_size=_CONFIG.batch_size,
            objects_per_step=_CONFIG.window_size,
            exclude_selected=_CONFIG.exclude_selected,
        )
    return _navigation.pp_nav_next_batch()


def prev_bucket(path=None) -> Dict[str, object]:
    _warn_once("prev_bucket")
    if path is not None and str(path) != _CONFIG.path:
        return load_by_path(
            path=path,
            pattern=_CONFIG.pattern,
            bucket_size=_CONFIG.batch_size,
            objects_per_step=_CONFIG.window_size,
            exclude_selected=_CONFIG.exclude_selected,
        )
    return _navigation.pp_nav_prev_batch()


def _step_without_crossing(direction: int) -> Dict[str, object]:
    controller = _navigation._controller()
    session = controller.session
    candidate = session.cursor + direction * session.stride
    if direction > 0:
        allowed = candidate <= session._max_window_start(session.batch_start, session.batch_end)
    else:
        allowed = candidate >= session.batch_start
    if not allowed:
        return controller.status()
    return controller.transition("next" if direction > 0 else "previous")


def smart_step_forward() -> Dict[str, object]:
    _warn_once("step")
    return (
        _navigation.pp_nav_next()
        if _CONFIG.auto_bucket
        else _step_without_crossing(1)
    )


def smart_step_backward() -> Dict[str, object]:
    _warn_once("back")
    return (
        _navigation.pp_nav_prev()
        if _CONFIG.auto_bucket
        else _step_without_crossing(-1)
    )


def step_forward() -> Dict[str, object]:
    return smart_step_forward()


def step_backward() -> Dict[str, object]:
    return smart_step_backward()


def take_step():
    """Yield the current object after each canonical forward transition."""
    while True:
        yield get_current_object()
        smart_step_forward()


def refresh_current_idx() -> int:
    """Explicitly sync from the scene, then return the canonical cursor."""
    result = _navigation.pp_nav_sync_from_scene()
    return int(result.get("cursor", -1))


def _window_objects() -> List[str]:
    context = _navigation.pp_nav_context(quiet=1)
    if context.get("status") == "inactive":
        return []
    return [name for entry in context.get("window", []) for name in entry["objects"]]


def get_current_object(offset=0) -> Optional[str]:
    names = _window_objects()
    if not names:
        return None
    index = as_int(offset, minimum=0, name="offset")
    return names[min(index, len(names) - 1)]


def pin_current(selection=None) -> Dict[str, object]:
    _warn_once("pin")
    return _navigation.pp_nav_pin("" if selection is None else selection)


def unpin_current(selection=None) -> Dict[str, object]:
    _warn_once("unpin")
    return _navigation.pp_nav_unpin("" if selection is None else selection)


def list_pinned() -> List[str]:
    controller = _navigation._controller()
    pinned = [
        entry.display_name
        for entry in controller.session.entries
        if entry.id in controller.session.pinned_entry_ids
    ]
    print("Pinned entries: " + (", ".join(pinned) if pinned else "(none)"))
    return pinned


def clear_all_pins() -> Dict[str, object]:
    return _navigation.pp_nav_clear_pins()


def toggle_auto_bucket(enable=None) -> bool:
    _CONFIG.auto_bucket = (
        not _CONFIG.auto_bucket if enable is None else as_bool(enable)
    )
    print(f"Auto bucket mode {'enabled' if _CONFIG.auto_bucket else 'disabled'}")
    return _CONFIG.auto_bucket


def toggle_exclude_selected(enable=None, reload=1):
    """Toggle selected-file filtering and optionally rebuild the active file session."""
    _CONFIG.exclude_selected = (
        not _CONFIG.exclude_selected if enable is None else as_bool(enable)
    )
    print(
        f"Exclude selected mode "
        f"{'enabled' if _CONFIG.exclude_selected else 'disabled'}"
    )
    status = _navigation.pp_nav_status(quiet=1)
    if as_bool(reload) and status.get("source") == "files":
        return _start_navigation(bind_keys=status.get("keys_bound", False), replace=1)
    return _CONFIG.exclude_selected


def register_switch_command(command, offset=0) -> Dict[str, object]:
    return _navigation.legacy_register_switch_command(command, offset)


def unregister_switch_command(command, offset=None) -> Dict[str, object]:
    return _navigation.legacy_unregister_switch_command(
        command, "" if offset is None else offset
    )


def list_switch_commands() -> List[Dict[str, object]]:
    values = [
        {"command": command, "offset": offset}
        for command, offset in _navigation.legacy_switch_commands()
    ]
    print(values if values else "No switch commands are registered")
    return values


def clear_switch_commands() -> Dict[str, object]:
    return _navigation.legacy_clear_switch_commands()


def execute_switch_commands(object_names: Iterable[str]) -> None:
    names = list(object_names)
    if not names:
        return
    for template, offset in _navigation.legacy_switch_commands():
        target = names[min(offset, len(names) - 1)]
        cmd.do(template.format(object=target))


def register_show_sticks():
    return register_switch_command("show sticks, {object}")


def register_show_cartoon():
    return register_switch_command("show cartoon, {object}")


def register_hide_all():
    return register_switch_command("hide everything, {object}")


def _current_file_context(offset=0) -> Tuple[Optional[str], Optional[str]]:
    context = _navigation.pp_nav_context(quiet=1)
    window = context.get("window", [])
    flattened = [
        (name, entry.get("source_paths", [""])[0] if entry.get("source_paths") else "")
        for entry in window
        for name in entry.get("objects", [])
    ]
    if not flattened:
        return None, None
    index = as_int(offset, minimum=0, name="offset")
    return flattened[min(index, len(flattened) - 1)]


def save_selected(offset=0) -> Optional[str]:
    object_name, source_path = _current_file_context(offset)
    if not object_name:
        print("No current object to save")
        return None
    selected_dir = _selected_dir()
    os.makedirs(selected_dir, exist_ok=True)
    output = os.path.join(
        selected_dir, _selected_output_name(source_path or "", object_name)
    )
    cmd.save(output, object_name)
    print(f"Saved {object_name} to {output}")
    return output


def unselect_current(offset=0) -> bool:
    object_name, source_path = _current_file_context(offset)
    if not object_name:
        print("No current object to unselect")
        return False
    output = os.path.join(
        _selected_dir(), _selected_output_name(source_path or "", object_name)
    )
    if not os.path.isfile(output):
        print(f"Selected file not found: {output}")
        return False
    os.remove(output)
    print(f"Removed selected file: {output}")
    return True


def list_selected() -> List[str]:
    directory = _selected_dir()
    values = (
        sorted(name for name in os.listdir(directory) if name.lower().endswith(".pdb"))
        if os.path.isdir(directory)
        else []
    )
    print(values if values else "No selected files")
    return values


def clear_selected() -> int:
    directory = _selected_dir()
    removed = 0
    for name in list_selected():
        os.remove(os.path.join(directory, name))
        removed += 1
    print(f"Removed {removed} selected files")
    return removed


def is_selected(offset=0) -> bool:
    object_name, source_path = _current_file_context(offset)
    if not object_name:
        return False
    output = os.path.join(
        _selected_dir(), _selected_output_name(source_path or "", object_name)
    )
    result = os.path.isfile(output)
    print(f"Current entry is {'selected' if result else 'not selected'}")
    return result


def legacy_status() -> Dict[str, object]:
    return {
        "path": _CONFIG.path,
        "pattern": _CONFIG.pattern,
        "batch_size": _CONFIG.batch_size,
        "window_size": _CONFIG.window_size,
        "auto_bucket": _CONFIG.auto_bucket,
        "exclude_selected": _CONFIG.exclude_selected,
        "switch_commands": list_switch_commands(),
    }


LEGACY_COMMANDS = {
    "loadpath": load_by_path,
    "step": step_forward,
    "back": step_backward,
    "save_select": save_selected,
    "unselect": unselect_current,
    "list_selected": list_selected,
    "clear_selected": clear_selected,
    "is_selected": is_selected,
    "next_bucket": next_bucket,
    "prev_bucket": prev_bucket,
    "setup_loader": setup_loader,
    "pin": pin_current,
    "unpin": unpin_current,
    "list_pins": list_pinned,
    "clear_pins": _navigation.legacy_clear_pins,
    "auto_bucket": toggle_auto_bucket,
    "exclude_selected": toggle_exclude_selected,
    "register_switch_cmd": _navigation.legacy_register_switch_command,
    "unregister_switch_cmd": unregister_switch_command,
    "list_switch_cmds": list_switch_commands,
    "clear_switch_cmds": _navigation.legacy_clear_switch_commands,
    "reg_show_sticks": register_show_sticks,
    "reg_show_cartoon": register_show_cartoon,
    "reg_hide_all": register_hide_all,
}


def register_commands(command_api=None):
    """Register legacy aliases against an explicit PyMOL command API."""
    command_api = cmd if command_api is None else command_api
    for name, function in LEGACY_COMMANDS.items():
        command_api.extend(name, function)


register_commands()

print(
    "screen_boost compatibility adapter loaded; no keys were bound. "
    "Use loadpath(..., bind_keys=1) or pp_nav_start(..., bind_keys=1)."
)
