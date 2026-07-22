"""Compatibility import for :mod:`pymolplugins.navigation.compat.file_management`."""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(globals().get("__script__", globals().get("__file__", ""))), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.navigation.compat.file_management import *  # noqa: E402,F401,F403
from pymolplugins.navigation.compat.file_management import register_commands  # noqa: E402
from pymol import cmd as _compat_cmd

register_commands(_compat_cmd)
