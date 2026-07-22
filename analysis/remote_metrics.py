"""Compatibility import for :mod:`pymolplugins.remote.commands`."""

import os
import sys

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.remote.commands import *  # noqa: E402,F401,F403
