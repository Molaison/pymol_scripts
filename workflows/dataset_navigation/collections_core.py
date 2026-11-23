"""Legacy import and CLI alias for the canonical Collections core."""

from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymolplugins.collections import core

if __name__ == "__main__":
    core.main()
else:
    sys.modules[__name__] = core
