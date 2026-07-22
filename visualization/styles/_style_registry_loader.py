"""Load the canonical registry from package imports or PyMOL ``run`` files."""

import importlib
import os
import sys


def load_style_registry():
    """Return the process-wide style registry module."""
    existing = sys.modules.get("pp_style_registry")
    if existing is not None:
        return existing
    try:
        return importlib.import_module("pymolplugins.styles.registry")
    except ImportError:
        style_dir = os.path.dirname(os.path.abspath(__file__))
        remove_after = style_dir not in sys.path
        if remove_after:
            sys.path.insert(0, style_dir)
        try:
            module = importlib.import_module("style_registry")
        finally:
            if remove_after:
                sys.path.remove(style_dir)
        sys.modules.setdefault("pp_style_registry", module)
        return module
