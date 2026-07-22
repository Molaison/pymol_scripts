"""Compatibility loader for the historical ``macaron_style`` command."""

try:
    from visualization.styles._style_registry_loader import load_style_registry
except ImportError:
    import os
    import sys

    _script = globals().get("__script__", globals().get("__file__", ""))
    _style_dir = os.path.dirname(os.path.abspath(_script))
    if _style_dir not in sys.path:
        sys.path.insert(0, _style_dir)
    from _style_registry_loader import load_style_registry


_registry = load_style_registry()
apply_macaron_style = _registry.legacy_macaron_style
_registry.register_legacy_commands(("macaron_style",))
