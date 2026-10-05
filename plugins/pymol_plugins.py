"""Thin PyMOL Plugin Manager entry for :mod:`pymolplugins.host.plugin`."""

import os
import sys

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.host.plugin import (  # noqa: E402,F401
    AUTOLOADED_COMPAT_COMMANDS,
    CANONICAL_COMMANDS,
    CANONICAL_MODULES,
    COMMAND_TARGETS,
    LEGACY_GLOBAL_ALIASES,
    REPOSITORY_ROOT,
    __init_plugin__,
    pp_capabilities,
    pp_doctor,
    pp_help,
    pp_load,
    show_structure_annotator_from_toolkit,
)

__all__ = [
    "AUTOLOADED_COMPAT_COMMANDS",
    "CANONICAL_COMMANDS",
    "CANONICAL_MODULES",
    "COMMAND_TARGETS",
    "LEGACY_GLOBAL_ALIASES",
    "REPOSITORY_ROOT",
    "pp_capabilities",
    "pp_doctor",
    "pp_help",
    "pp_load",
    "show_structure_annotator_from_toolkit",
]
