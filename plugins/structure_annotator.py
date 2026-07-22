"""Thin Plugin Manager entry for :mod:`pymolplugins.annotation.plugin`."""

import os
import sys

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.annotation.plugin import *  # noqa: F401,F403,E402
from pymolplugins.annotation.plugin import __init_plugin__  # noqa: E402,F401
