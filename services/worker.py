"""Compatibility CLI for :mod:`pymolplugins.worker.app`."""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymolplugins.worker.app import *  # noqa: E402,F401,F403
from pymolplugins.worker.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
