"""Qt compatibility UI backed entirely by the canonical style registry.

Historical commands retain their effects. The dialog entry opens the shared
Color page.
"""

from pymol import cmd

from pymolplugins.styles import registry as _registry

# Historical button labels are retained, but each now resolves to a reviewed
# canonical preset instead of carrying a second dictionary of PyMOL settings.
_QUICK_PRESETS = {
    "publication": "publication_light",
    "presentation": "macaron_default",
    "analysis": "minimal_light",
}


def apply_custom_style(selection="polymer", color_scheme="default"):
    """Apply a canonical Macaron preset without rendering."""

    return _registry.legacy_macaron_style(selection, ray=0, color_scheme=color_scheme)


def revert_custom_style(snapshot_id=""):
    """Revert the latest canonical style snapshot."""

    return _registry.pp_style_revert(snapshot_id)


def render_high_quality(width=1600, height=1200, filename=""):
    """Explicitly ray trace the current scene through the registry."""

    return _registry.pp_style_render(width, height, filename)


def quick_style_preset(
    preset="publication", selection="all", snapshot=1, render=0
):
    """Apply a historical quick label or any canonical preset name."""

    requested = str(preset or "publication").strip().lower()
    canonical = _QUICK_PRESETS.get(requested, requested)
    return _registry.pp_style_apply(
        canonical, selection, snapshot=snapshot, render=render
    )


def show_macaron_dialog():
    """Open Macaron in the unified Color page."""
    from pymolplugins.host.show_presets import show_presets

    return show_presets(tab="color", palette="macaron_palette")


cmd.extend("apply_custom_style", apply_custom_style)
cmd.extend("revert_custom_style", revert_custom_style)
cmd.extend("render_high_quality", render_high_quality)
cmd.extend("quick_style_preset", quick_style_preset)
cmd.extend("show_macaron_dialog", show_macaron_dialog)


def __init_plugin__(app=None):
    """Register the compatibility dialog in PyMOL's plugin menu."""

    from pymol.plugins import addmenuitemqt

    addmenuitemqt("Macaron Style", show_macaron_dialog)


__author__ = "PymolPlugins contributors"
__version__ = "2.0"
__description__ = "Canonical style registry Qt adapter"
