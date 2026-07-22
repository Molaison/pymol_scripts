"""Compatibility import for :mod:`pymolplugins.follow.commands`."""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(globals().get("__script__", globals().get("__file__", ""))), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.follow.commands import *  # noqa: E402,F401,F403
