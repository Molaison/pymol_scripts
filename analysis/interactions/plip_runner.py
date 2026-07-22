"""Compatibility CLI for :mod:`pymolplugins.interactions.plip_runner`."""

import sys
import os

_SCRIPT = globals().get("__script__", globals().get("__file__", ""))
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_SCRIPT), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.interactions.plip_runner import *  # noqa: E402,F401,F403
from pymolplugins.interactions.plip_runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
