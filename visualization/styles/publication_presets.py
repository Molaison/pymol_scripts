"""Compatibility commands for the former publication preset collection.

New code should use ``pp_style_apply`` and ``pp_style_render``.  The short
legacy aliases below are registered only when this compatibility file is
explicitly loaded, avoiding pollution for users who load the canonical
registry alone.
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

publication_style = _registry.legacy_publication_style
macaron_style = _registry.legacy_macaron_style
cartoon_style = _registry.legacy_cartoon_style
watercolor_style = _registry.legacy_watercolor_style
minimal_style = _registry.legacy_minimal_style
apply_protein_colors = _registry.legacy_apply_protein_colors
render_publication = _registry.legacy_render_publication
quick_preset = _registry.legacy_quick_preset
_setup_colors = _registry.legacy_setup_colors

# ``setup_ligand_view`` intentionally no longer lives in styles: it combines
# interaction selection, representations, and zoom, and belongs to an
# interaction scene preset under the unified contract.
_registry.register_legacy_commands(
    (
        "publication_style",
        "pbs",
        "macaron_style",
        "macaron",
        "cartoon_style",
        "watercolor_style",
        "watercolor",
        "minimal_style",
        "minimal",
        "apply_protein_colors",
        "render_publication",
        "quick_preset",
        "preset",
    )
)
