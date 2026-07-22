"""Compatibility import for :mod:`pymolplugins.worker.backends.plip`."""

import os
import sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.worker.backends.plip import *  # noqa: E402,F401,F403
