"""Compatibility entry for :mod:`extras.geometry.inertia`."""
import os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(globals().get("__script__", globals().get("__file__", ""))), "..", ".."))
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
from extras.geometry.inertia import *  # noqa: F401,F403,E402
