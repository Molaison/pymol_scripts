"""Compatibility import for :mod:`pymolplugins.interactions.compat.plip_dialog`."""

import os
import sys

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.interactions.compat.plip_dialog import *  # noqa: E402,F401,F403
