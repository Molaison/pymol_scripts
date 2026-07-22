"""Compatibility commands for historical RFdiffusion2 PyMOL settings.

The old file mixed 8-bit and normalized RGB, referenced an unimported
``CmdException``, and discarded the selection passed to ``mass_rainbow``.
These stable wrappers delegate to validated registry presets instead.
"""

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

get_colors = _registry.legacy_get_colors
get_lighting = _registry.legacy_get_lighting
mass_rainbow = _registry.legacy_mass_rainbow
mass_paper_rainbow = _registry.legacy_mass_paper_rainbow
mass_paper_rainbow_sel = _registry.legacy_mass_paper_rainbow

_registry.register_legacy_commands(
    (
        "get_colors",
        "get_lighting",
        "mass_rainbow",
        "mass_paper_rainbow",
        "mass_paper_rainbow_sel",
    )
)
