"""Idempotently load the canonical style registry from a PyMOL script."""

import os
import sys


try:
    from visualization.styles._style_registry_loader import load_style_registry
except ImportError:
    _script = globals().get("__script__", globals().get("__file__", ""))
    _style_dir = os.path.dirname(os.path.abspath(_script))
    _remove_style_dir = _style_dir not in sys.path
    if _remove_style_dir:
        sys.path.insert(0, _style_dir)
    try:
        from _style_registry_loader import load_style_registry
    finally:
        if _remove_style_dir:
            sys.path.remove(_style_dir)


# The loader reuses ``sys.modules['pp_style_registry']`` when present, keeping
# the process-wide snapshot stack intact on repeated PML loads.
load_style_registry()
