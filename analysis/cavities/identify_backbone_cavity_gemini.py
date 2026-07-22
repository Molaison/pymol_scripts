"""Compatibility entry for :mod:`extras.cavities.backbone_cavity_experimental`."""
import os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(globals().get("__script__", globals().get("__file__", ""))), "..", ".."))
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
from extras.cavities.backbone_cavity_experimental import *  # noqa: F401,F403,E402
