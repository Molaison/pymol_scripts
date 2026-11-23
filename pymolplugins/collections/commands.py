"""Canonical Collections command; importing this module has no host side effects."""


def pp_collections(manifest=""):
    """Open Collections, optionally loading a saved collection.

    Args:
        manifest: Path to 01_collection.json, or empty to show the panel.
    """
    from .plugin import collections_panel

    panel = collections_panel()
    if manifest:
        panel.load_manifest(manifest)
    return panel
