"""Compatibility facade for the canonical dataset navigator.

This module intentionally owns no navigation globals.  Historical commands
delegate to :mod:`workflows.dataset_navigation.navigation`, so loading this
file no longer creates a second state machine or steals generic ``next`` /
``prev`` / ``pin`` command names.
"""

import os
import sys
import traceback
from functools import wraps

from pymol import cmd

try:
    from pymolplugins.core.parameters import as_bool, as_int, extend_command
    from pymolplugins.navigation import commands as _navigation
    from pymolplugins.navigation.compat.selected_paths import selected_output_name
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.parameters import (  # type: ignore
        as_bool,
        as_int,
        extend_command,
    )
    from pymolplugins.navigation import commands as _navigation  # type: ignore
    from pymolplugins.navigation.compat.selected_paths import (  # type: ignore
        selected_output_name,
    )


def _compat_error(command_name, exc, hint):
    """Return one actionable line for input mistakes and trace internal faults."""

    message = " ".join(str(exc).splitlines())
    print(f"{command_name} error: {message}. Next: {hint}")
    if not isinstance(exc, (ValueError, TypeError, _navigation.NavigationError)):
        traceback.print_exc()
    return {"status": "error", "error": str(exc)}


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
    controller = _navigation._controller()
    entry = controller.session.entries[controller.session.cursor]
    names = controller._entry_names(entry)
    return names[0] if names else None


def save_selected():
    """Save the current molecule into a ``selected`` sibling directory."""
    try:
        controller = _navigation._controller()
        entry = controller.session.entries[controller.session.cursor]
        current = _get_current_structure()
        if not current:
            raise ValueError("current entry has no loaded object")
        if entry.source_paths:
            base_dir = os.path.dirname(entry.source_paths[0])
        else:
            base_dir = os.getcwd()
        selected_dir = os.path.join(base_dir, "selected")
        os.makedirs(selected_dir, exist_ok=True)
        source_path = entry.source_paths[0] if entry.source_paths else ""
        save_path = os.path.join(
            selected_dir,
            selected_output_name(source_path, current),
        )
        # Use the command API owned by the canonical navigator so a manually
        # reloaded compatibility facade cannot retain a stale ``cmd`` object.
        _navigation.cmd.save(save_path, current)
        print(f"Saved {current} to {save_path}")
        return save_path
    except Exception as exc:
        return _compat_error(
            "save_selected",
            exc,
            "start file navigation and move to a loaded structure first",
        )


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
        return _compat_error(
            "set_auto_batch",
            exc,
            "start navigation first and use enabled=0|1",
        )


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
        return _compat_error(
            "set_batch_size",
            exc,
            "start navigation first and use an integer no smaller than window_size",
        )


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
        @wraps(function)
        def guarded(*args, __name=name, __function=function, **kwargs):
            try:
                return __function(*args, **kwargs)
            except Exception as exc:
                return _compat_error(
                    __name,
                    exc,
                    "check the command arguments and run pp_nav_status",
                )

        extend_command(command_api, name, guarded)


register_commands()

print(
    "file_management compatibility facade loaded. "
    "Use pp_nav_* commands for new workflows; generic next/prev/pin aliases are no longer registered."
)
