"""Compatibility facade for the canonical dataset navigator.

This module intentionally owns no navigation globals.  Historical commands
delegate to :mod:`workflows.dataset_navigation.navigation`, so loading this
file no longer creates a second state machine or steals generic ``next`` /
``prev`` / ``pin`` command names.
"""

import os
import sys
import traceback

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


def load_structures(path=".", pattern=None, bucket_size=10):
    """Load a directory with the canonical file navigator."""
    result = _navigation.pp_nav_start(
        source="files",
        path=path,
        pattern=pattern or _navigation.STRUCTURE_PATTERN,
        batch_size=bucket_size,
        window_size=1,
        bind_keys=1,
        zoom_policy="current",
        replace=1,
    )
    if result.get("status") == "error":
        return result
    return _navigation.legacy_apply_switch_commands()


def next_structure():
    """Move to the next navigation window."""
    return _navigation.pp_nav_next()


def prev_structure():
    """Move to the previous navigation window."""
    return _navigation.pp_nav_prev()


def next_batch():
    """Move to the next resident file batch."""
    return _navigation.pp_nav_next_batch()


def prev_batch():
    """Move to the previous resident file batch."""
    return _navigation.pp_nav_prev_batch()


def pin_current():
    """Pin the current atomic navigation entry."""
    return _navigation.pp_nav_pin()


def unpin_current():
    """Unpin the current atomic navigation entry."""
    return _navigation.pp_nav_unpin()


def clear_pins():
    """Unpin all entries without changing the cursor."""
    return _navigation.legacy_clear_pins()


def _get_current_structure():
    """Return the first leaf name in the current entry."""
    try:
        controller = _navigation._controller()
        entry = controller.session.entries[controller.session.cursor]
        names = controller._entry_names(entry)
        return names[0] if names else None
    except Exception:
        return None


def save_selected():
    """Save the current molecule into a ``selected`` sibling directory."""
    try:
        controller = _navigation._controller()
        entry = controller.session.entries[controller.session.cursor]
        current = _get_current_structure()
        if not current:
            raise RuntimeError("current entry has no loaded object")
        if entry.source_paths:
            base_dir = os.path.dirname(entry.source_paths[0])
        else:
            base_dir = os.getcwd()
        selected_dir = os.path.join(base_dir, "selected")
        os.makedirs(selected_dir, exist_ok=True)
        source_name = os.path.basename(entry.source_paths[0]) if entry.source_paths else current
        for suffix in (".pdb.gz", ".cif.gz", ".pdb", ".cif"):
            if source_name.lower().endswith(suffix):
                source_name = source_name[: -len(suffix)]
                break
        save_path = os.path.join(selected_dir, f"{source_name}.pdb")
        cmd.save(save_path, current)
        print(f"Saved {current} to {save_path}")
        return save_path
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return None


def register_switch_command(command, offset=0):
    """Register a canonical navigation hook with ``{object}`` placeholders."""
    return _navigation.legacy_register_switch_command(command, offset)


def clear_switch_commands():
    """Remove all canonical navigation hooks."""
    return _navigation.legacy_clear_switch_commands()


def auto_show_sticks():
    return register_switch_command("show sticks, {object}")


def auto_show_cartoon():
    return register_switch_command("show cartoon, {object}")


def set_auto_batch(enabled=True):
    """Compatibility mapping: auto batch corresponds to cursor wrapping."""
    try:
        controller = _navigation._controller()
        controller.session.wrap = as_bool(enabled)
        return controller.status()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def set_batch_size(size):
    """Change the active session batch size and reload the current batch."""
    try:
        controller = _navigation._controller()
        value = as_int(size, minimum=controller.session.window_size, name="size")
        current_entry = controller.session.cursor
        controller.session.batch_size = value
        controller.session.cursor = controller.session._clamp_cursor(current_entry)
        return controller.apply_scene()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


LEGACY_COMMANDS = {
    "load_structures": load_structures,
    "load_files": load_structures,
    "next_structure": next_structure,
    "prev_structure": prev_structure,
    "next_batch": next_batch,
    "prev_batch": prev_batch,
    "pin_current": pin_current,
    "unpin_current": unpin_current,
    "clear_pins": _navigation.legacy_clear_pins,
    "save_selected": save_selected,
    "register_switch_cmd": _navigation.legacy_register_switch_command,
    "clear_switch_cmds": _navigation.legacy_clear_switch_commands,
    "auto_show_sticks": auto_show_sticks,
    "auto_show_cartoon": auto_show_cartoon,
    "set_auto_batch": set_auto_batch,
    "set_batch_size": set_batch_size,
}


def register_commands(command_api=None):
    """Register legacy aliases against an explicit PyMOL command API."""
    command_api = cmd if command_api is None else command_api
    for name, function in LEGACY_COMMANDS.items():
        command_api.extend(name, function)


register_commands()

print(
    "file_management compatibility facade loaded. "
    "Use pp_nav_* commands for new workflows; generic next/prev/pin aliases are no longer registered."
)
