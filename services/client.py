"""Compatibility import for :mod:`pymolplugins.remote.client`."""

import os
import sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.remote.client import *  # noqa: E402,F401,F403
