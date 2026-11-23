"""Legacy PyMOL ``run`` entry; explicitly registers the four old commands."""

from pathlib import Path
import sys

# PyMOL run may supply its own __file__ rather than this script's path.
_repository = str(Path(sys._getframe().f_code.co_filename).resolve().parents[1])
if _repository not in sys.path:
    sys.path.insert(0, _repository)

from pymol import cmd
from pymolplugins.collections.plugin import (
    collections_panel, collections_open, collections_preview, collections_capture_view,
)

cmd.extend("collections_panel", collections_panel)
cmd.extend("collections_open", collections_open)
cmd.extend("collections_preview", collections_preview)
cmd.extend("collections_capture_view", collections_capture_view)
