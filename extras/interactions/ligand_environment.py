"""Compatibility commands for the versioned local interaction presets.

The historical command names remain available, but new displays are delegated
to ``pymol_interactions``.  In particular, a ligand selection is never widened
to every object containing one ligand atom, and cleanup only uses names tracked
by exact run manifests.
"""

from __future__ import annotations

import os
import sys
import uuid

from pymol import cmd

try:
    from pymolplugins.interactions.controller import get_controller
except ImportError:  # Supports ``run analysis/interactions/ligand_surrounding.py``.
    _MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
    if _MODULE_DIR not in sys.path:
        sys.path.insert(0, _MODULE_DIR)
    from pymol_interactions import get_controller


_LEGACY_GROUPS = []
_LEGACY_EXACT_NAMES = []


def _remember_group(group_name):
    if group_name not in _LEGACY_GROUPS:
        _LEGACY_GROUPS.append(group_name)
    return group_name


def _run_ligand_preset(
    preset,
    ligand_selection,
    distance,
    surface=False,
    labels=False,
):
    parameters = {"site_cutoff": float(distance)}
    if preset == "ligand_site":
        parameters.update({"surface": bool(surface), "labels": bool(labels)})
    record = get_controller(cmd).run(
        preset,
        "all",
        partner2=str(ligand_selection),
        parameters=parameters,
    )
    _remember_group(record.manifest.root_group)
    return record


def _latest_interface(selection):
    """Resolve the historical default name to the latest generated copy."""

    if selection != "ligand_env_surrounding" or not _LEGACY_GROUPS:
        return selection
    try:
        record = get_controller(cmd).get(_LEGACY_GROUPS[-1])
    except KeyError:
        return selection
    for entry in record.manifest.created:
        if entry.role == "interface_copy":
            return entry.name
    return selection


def show_ligand_environment(ligand_selection="hetatm", distance=5.0, style="sticks"):
    """Display an exact ligand selection and its local environment.

    ``style`` is applied only to the generated ligand copy, so the user's
    source representations and colors remain untouched.
    """

    style_value = str(style).lower()
    if style_value not in {"sticks", "spheres", "cartoon"}:
        raise ValueError("style must be sticks, spheres, or cartoon")
    record = _run_ligand_preset(
        "ligand_quick", ligand_selection, float(distance), surface=False, labels=False
    )
    if style_value != "sticks":
        for entry in record.manifest.created:
            if entry.role == "partner_ligand_copy":
                cmd.hide("sticks", entry.name)
                cmd.show(style_value, entry.name)
    print(
        "Ligand environment displayed: group=%s ligand=%s"
        % (record.manifest.root_group, ligand_selection)
    )
    return record.manifest.root_group


def show_ligand_surface(ligand_selection="hetatm", distance=5.0, transparency=0.3):
    """Show surface on generated binding-site copies, never on source objects."""

    transparency_value = float(transparency)
    if not 0.0 <= transparency_value <= 1.0:
        raise ValueError("transparency must be between 0 and 1")
    record = _run_ligand_preset(
        "ligand_site", ligand_selection, float(distance), surface=True, labels=False
    )
    interface_entries = [
        entry.name
        for entry in record.manifest.created
        if entry.role == "interface_copy"
    ]
    for name in interface_entries:
        cmd.set("transparency", transparency_value, name)
    print("Ligand surface displayed in %s" % record.manifest.root_group)
    return record.manifest.root_group


def color_by_properties(selection="ligand_env_surrounding"):
    """Legacy scoped coloring helper; it never colors ``all`` implicitly."""

    selection_value = str(_latest_interface(selection)).strip()
    if not selection_value:
        raise ValueError("selection must not be empty")
    cmd.color("red", "(%s) and resn ASP+GLU" % selection_value)
    cmd.color("blue", "(%s) and resn LYS+ARG+HIS" % selection_value)
    cmd.color("green", "(%s) and resn SER+THR+ASN+GLN" % selection_value)
    cmd.color(
        "yellow",
        "(%s) and resn ALA+VAL+LEU+ILE+MET+PHE+TRP+TYR" % selection_value,
    )
    cmd.color("orange", "(%s) and resn GLY+PRO+CYS" % selection_value)
    print("Residues colored by properties in selection: %s" % selection_value)


def label_residues(selection="ligand_env_surrounding"):
    """Legacy scoped labeling helper; settings are selection-local."""

    selection_value = str(_latest_interface(selection)).strip()
    if not selection_value:
        raise ValueError("selection must not be empty")
    cmd.label("(%s) and name CA" % selection_value, "resn+resi")
    cmd.set("label_color", "black", selection_value)
    cmd.set("label_size", 10, selection_value)
    print("Residues labeled in selection: %s" % selection_value)


def clean_ligand_display():
    """Clear only exact outputs created through these compatibility wrappers."""

    controller = get_controller(cmd)
    cleared = 0
    for group_name in list(reversed(_LEGACY_GROUPS)):
        try:
            controller.clear(group_name)
            cleared += 1
        except KeyError:
            pass
        finally:
            _LEGACY_GROUPS.remove(group_name)
    for name in list(reversed(_LEGACY_EXACT_NAMES)):
        cmd.delete(name)
        _LEGACY_EXACT_NAMES.remove(name)
    print("Cleared %d exact ligand interaction run(s)" % cleared)


def show_distances(
    ligand_selection="hetatm", target_selection="ligand_env_surrounding", cutoff=4.0
):
    """Legacy all-distance helper with an exact ligand endpoint.

    New presets intentionally do not create all-pair distance clutter.  This
    compatibility command still does so on request, but never substitutes the
    containing complex/object for ``ligand_selection``.
    """

    cutoff_value = float(cutoff)
    if cutoff_value <= 0.0:
        raise ValueError("cutoff must be positive")
    target_selection = _latest_interface(target_selection)
    name = "ppi_legacy_distance_%s" % uuid.uuid4().hex[:8]
    _LEGACY_EXACT_NAMES.append(name)
    try:
        cmd.distance(
            name,
            "(%s)" % ligand_selection,
            "(%s)" % target_selection,
            cutoff=cutoff_value,
            mode=0,
        )
        cmd.set("dash_color", "magenta", name)
        cmd.set("dash_width", 2.0, name)
        cmd.hide("labels", name)
    except Exception:
        cmd.delete(name)
        _LEGACY_EXACT_NAMES.remove(name)
        raise
    print("Distances shown in exact object %s" % name)
    return name


def organize_objects():
    """Report generated groups; grouping is now performed by the renderer."""

    groups = tuple(_LEGACY_GROUPS)
    print("Interaction renderer already organized groups: %s" % ", ".join(groups))
    return groups


def preset_binding_site(ligand_selection="hetatm", distance=5.0):
    """Apply the legacy comprehensive binding-site view through ligand_site."""

    record = _run_ligand_preset(
        "ligand_site",
        ligand_selection,
        float(distance),
        surface=True,
        labels=True,
    )
    print("Binding-site preset ready: %s" % record.manifest.root_group)
    return record.manifest.root_group


def quick_ligand_view(distance=4.0):
    """Quick view using organic ligand atoms (excluding water and metals)."""

    return show_ligand_environment("organic", float(distance), "sticks")


cmd.extend("show_ligand_env", show_ligand_environment)
cmd.extend("show_ligand_surface", show_ligand_surface)
cmd.extend("show_distances", show_distances)
cmd.extend("organize_objects", organize_objects)
cmd.extend("color_by_props", color_by_properties)
cmd.extend("label_residues", label_residues)
cmd.extend("clean_ligand", clean_ligand_display)
cmd.extend("preset_binding", preset_binding_site)
cmd.extend("quick_ligand", quick_ligand_view)
