"""Compatibility entry for :mod:`pymolplugins.alignment.sequence`."""

import os
import sys

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.alignment.sequence import *  # noqa: F401,F403,E402
from pymolplugins.alignment.sequence import _register_commands  # noqa: E402
from pymol import cmd as _compat_cmd  # noqa: E402

_register_commands(_compat_cmd)
