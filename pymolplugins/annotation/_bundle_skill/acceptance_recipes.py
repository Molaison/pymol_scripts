"""Composed review recipes for recurring ligand-pocket and protein-interface cases.

These functions own representation, camera, and scene composition after a
task has bound the scientific roles to verified PyMOL selections.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
from typing import Dict

from pymol import CmdException, cmd
from pymol.cgo import ALPHA, BEGIN, COLOR, END, LINE_LOOP, TRIANGLE_STRIP, VERTEX


_SCRIPT_DIR = Path(inspect.currentframe().f_code.co_filename).absolute().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from acceptance_bundle import (
    acc_bind_review_keys,
    acc_contact_sidechains,
    acc_ligand_sticks,
    acc_polar_contacts,
    acc_publication_defaults,
    acc_sidechain_sticks,
    acc_store_scene,
    asset_name,
)
from acceptance_pocket_void import pocket_cavity_layers


def _text(value, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _truthy(value, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _count(selection: str) -> int:
    try:
        return int(cmd.count_atoms(selection))
    except Exception:
        return 0


def _require_atoms(selection: str, role: str) -> None:
    if _count(selection) == 0:
        raise CmdException(f"{role} selection has no atoms: {selection}")


def _set(name: str, value, selection: str = "") -> None:
    if selection:
        cmd.set(name, value, selection)
    else:
        cmd.set(name, value)


def _contact_residue_sticks(residue_selection: str) -> str:
    return f"(({residue_selection}) and polymer.protein and not elem H)"


def _pocket_distances(identity, protein, ligand, cutoff, covalent_link):
    polar = f"({protein}) and elem N+O+S"
    ligand_polar = f"({ligand}) and elem N+O+S"
    interactions = {}
    if _count(polar) and _count(ligand_polar):
        pairs = set()
        for left, right in (("donors", "acceptors"), ("acceptors", "donors")):
            pairs.update(cmd.find_pairs(
                f"({polar}) and {left}", f"({ligand_polar}) and {right}",
                cutoff=cutoff, mode=1, angle=45,
            ))
        for (receptor, receptor_id), (binder, binder_id) in sorted(pairs):
            first = f"{receptor} and index {receptor_id}"
            second = f"{binder} and index {binder_id}"
            kind = "short_contacts" if cmd.get_distance(first, second) < 2.4 else "polar_contacts"
            reset = kind not in interactions
            name = interactions.setdefault(kind, asset_name(identity, kind))
            cmd.distance(name, first, second, label=1, quiet=1, reset=reset)
    if covalent_link is not None:
        first, second = covalent_link
        if _count(first) != 1 or _count(second) != 1:
            raise CmdException("A covalent link requires two single-atom selections.")
        name = asset_name(identity, "covalent_link")
        cmd.distance(name, first, second, label=1, quiet=1, reset=1)
        interactions["covalent_link"] = name

    for kind, name in interactions.items():
        color = {"polar_contacts": "teal", "short_contacts": "magenta", "covalent_link": "orange"}[kind]
        cmd.set("dash_color", color, name)
        cmd.set("dash_radius", 0.07, name)
        cmd.set("label_size", 11, name)
        cmd.disable(name)
    return interactions


def membrane_surface_review(
    identity, protein, surface_residues, *, bounds=None, biased_residues=(),
    side_labels=(), core_bounds=(),
):
    """Show surface hydropathy and bias-matched side chains in a known z frame."""
    surface_ca = f"({protein}) and ({surface_residues}) and name CA"
    _require_atoms(surface_ca, "membrane surface C-alpha")
    cmd.set_color("review_membrane_hydrophobic", [0.82, 0.55, 0.17])
    cmd.set_color("review_membrane_hydrophilic", [0.12, 0.54, 0.37])
    # Positive Kyte–Doolittle scores. All other canonical scores are negative.
    hydrophobic = "ALA+CYS+PHE+ILE+LEU+MET+VAL"
    for selector, color in (
        (f"resn {hydrophobic}", "review_membrane_hydrophobic"),
        (f"not resn {hydrophobic}", "review_membrane_hydrophilic"),
    ):
        markers = f"({surface_ca}) and ({selector})"
        if _count(markers):
            cmd.show("spheres", markers)
            cmd.set("sphere_scale", 0.30, markers)
            cmd.set("sphere_color", color, markers)
            cmd.set("cartoon_color", color, f"byres ({markers})")

    labels = []
    # The represented charge belongs to the side chain at near-neutral pH.
    charges = {
        "ARG": ("CZ", "+1", "marine"), "LYS": ("NZ", "+1", "marine"),
        "ASP": ("CG", "-1", "firebrick"), "GLU": ("CD", "-1", "firebrick"),
        "HIS": ("NE2", "pH", "purple"), "TRP": ("NE1", "0", "gray35"),
        "TYR": ("OH", "0", "gray35"),
    }
    for entry in biased_residues:
        residue = f"({protein}) and ({entry['selection']}) and resn {entry['resn']}"
        if not entry["weight"] or _count(f"({surface_ca}) and ({residue})") != 1:
            continue
        acc_sidechain_sticks(residue, carbon_color="gray65", stick_radius=0.17)
        if entry["resn"] in charges:
            anchor, charge, color = charges[entry["resn"]]
        else:
            anchor, charge, color = "CB", "0", "gray35"
        selected = f"({residue}) and name {anchor}"
        if not _count(selected):
            selected = f"({residue}) and name CA"
        cmd.label(selected, 'resn + resi + ' + repr(f" {charge}"))
        cmd.set("label_color", color, selected)
        cmd.set("label_size", 13, selected)
        cmd.set("label_bg_color", "white", selected)
        cmd.set("label_position", (0.0, 0.5, 1.1), selected)
        labels.append(selected)

    planes, markers = [], []
    if bounds is not None:
        lower, upper = map(float, bounds)
        if lower >= upper:
            raise CmdException("Membrane z bounds must be increasing.")
        xyz = cmd.get_coords(protein)
        x0, y0 = xyz[:, :2].min(axis=0) - 4.0
        x1, y1 = xyz[:, :2].max(axis=0) + 4.0
        for side, z in (("lower", lower), ("upper", upper)):
            name = asset_name(identity, f"membrane_{side}")
            panel = [COLOR, 0.43, 0.55, 0.69, ALPHA, 0.18, BEGIN, TRIANGLE_STRIP]
            for x, y in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
                panel.extend([VERTEX, float(x), float(y), z])
            panel.extend([END, ALPHA, 0.74, BEGIN, LINE_LOOP])
            for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
                panel.extend([VERTEX, float(x), float(y), z])
            panel.append(END)
            cmd.load_cgo(panel, name)
            planes.append(name)
        if side_labels:
            for side, z, text in zip(("lower", "upper"), (lower, upper), side_labels):
                marker = asset_name(identity, f"membrane_{side}_label")
                cmd.pseudoatom(marker, pos=[float(x0), float(y0), z], label=str(text))
                cmd.hide("nonbonded", marker)
                cmd.set("label_size", 13, marker)
                cmd.set("label_color", "gray35", marker)
                markers.append(marker)
        if core_bounds:
            core_lower, core_upper = map(float, core_bounds)
            if not lower < core_lower < core_upper < upper:
                raise CmdException("Membrane core bounds must lie inside the membrane planes.")
            rims = [COLOR, 0.43, 0.55, 0.69, ALPHA, 0.75]
            for z in (core_lower, core_upper):
                rims.extend([BEGIN, LINE_LOOP])
                for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
                    rims.extend([VERTEX, float(x), float(y), z])
                rims.append(END)
            marker = asset_name(identity, "membrane_core")
            cmd.load_cgo(rims, marker)
            markers.append(marker)
    return {"planes": planes, "markers": markers, "bias_labels": labels}


def ligand_pocket_review(
    identity: str,
    protein_selection: str,
    ligand_selection: str,
    contact_cutoff=4.0,
    polar_cutoff=3.6,
    surface_radius=8.0,
    protein_color: str = "gray70",
    ligand_carbon_color: str = "gray35",
    pocket_carbon_color: str = "gray65",
    surface_color: str = "gray80",
    overview_surface_transparency=0.82,
    focus_surface_transparency=0.72,
    context_transparency=0.45,
    overview_scene: str = "overview",
    focus_scene: str = "ligand_pocket",
    initial_scene: str = "focus",
    overview_padding=3.0,
    focus_padding=4.5,
    show_polar_contacts=1,
    background: str = "white",
    show_cavity=1,
    membrane=None,
    covalent_link=None,
    anchor_residues="",
    bind_keys=True,
    cavity_centers=None,
) -> Dict[str, object]:
    """Create stable overview and pocket scenes from verified semantic roles.

    Contact residues use any protein heavy atom within ``contact_cutoff`` of a
    ligand heavy atom.  Complete contact-residue heavy atoms are submitted to
    the sticks representation while the cartoon stays visible; PyMOL's cartoon
    side-chain helper removes redundant backbone sticks and keeps side chains
    attached. Membrane metadata binds the z bounds, surface selection, and
    optional task-specific bias positions to this same scene.
    """

    protein = _text(protein_selection, "polymer.protein")
    ligand = _text(ligand_selection, "organic")
    _require_atoms(protein, "protein")
    _require_atoms(ligand, "ligand")

    ligand_name = asset_name(identity, "ligand")
    pocket_name = asset_name(identity, "pocket_residues")
    pocket_surface_name = asset_name(identity, "pocket_surface")

    acc_publication_defaults(background)
    _set("depth_cue", 1)
    _set("fog_start", 0.62)
    cmd.select(ligand_name, ligand)
    _require_atoms(ligand_name, "named ligand")
    cmd.hide("everything", f"({protein}) or ({ligand_name})")
    cmd.show("cartoon", protein)
    cmd.color(protein_color, protein)
    _set("cartoon_transparency", float(context_transparency), protein)
    _set("surface_color", surface_color, protein)
    pocket_name = acc_contact_sidechains(
        selection=protein,
        partner_selection=ligand_name,
        cutoff=float(contact_cutoff),
        name=pocket_name,
        carbon_color=pocket_carbon_color,
        stick_radius=0.22,
        cb_sphere=0,
        contact_scope="all",
    )
    cmd.select(
        pocket_surface_name,
        f"byres (({protein}) within {float(surface_radius)} of ({ligand_name}))",
    )
    _require_atoms(pocket_surface_name, "pocket surface")
    _set("surface_color", surface_color, pocket_surface_name)
    acc_ligand_sticks(
        ligand_name,
        carbon_color=ligand_carbon_color,
        stick_radius=0.30,
    )
    pocket_sticks = _contact_residue_sticks(pocket_name)
    _require_atoms(pocket_sticks, "pocket contact-residue display")

    contact_scope = pocket_name if _truthy(show_polar_contacts, True) else "none"
    distances = _pocket_distances(identity, contact_scope, ligand_name, float(polar_cutoff), covalent_link)
    cavity_layers = {}
    if _truthy(show_cavity, True):
        cavity_layers = pocket_cavity_layers(identity, protein, ligand_name, centers=cavity_centers)
    for cavity_object in cavity_layers:
        cmd.disable(cavity_object)

    # Membrane complexes use a single view that retains both kinds of evidence.
    scenes = []
    if membrane is None and overview_scene:
        cmd.show("surface", protein)
        _set("transparency", float(overview_surface_transparency), protein)
        cmd.hide("sticks", pocket_sticks)
        acc_store_scene(
            overview_scene,
            zoom_selection=f"({protein}) or ({ligand_name})",
            padding=float(overview_padding),
            message=str(identity),
        )
        scenes.append(overview_scene)

    # Keep the cartoon at contact residues: the side-chain helper needs it to
    # preserve the CA-to-side-chain connection without duplicate backbone sticks.
    cmd.hide("surface", protein)
    cmd.show("surface", pocket_surface_name)
    _set("transparency", float(focus_surface_transparency), pocket_surface_name)
    cmd.show("cartoon", protein)
    cmd.show("sticks", pocket_sticks)
    if anchor_residues:
        anchors = f"({protein}) and ({anchor_residues})"
        _require_atoms(anchors, "pocket anchors")
        acc_sidechain_sticks(anchors, carbon_color=pocket_carbon_color, stick_radius=0.22)
    if distances:
        for distance in distances.values():
            cmd.enable(distance)
        cmd.set("label_distance_digits", 2)
    for cavity_object in cavity_layers:
        cmd.enable(cavity_object)
    message = [str(identity)]
    message.extend(legend for legend, count in cavity_layers.values())
    for kind, legend in (
        ("polar_contacts", "Teal dashes: candidate polar contacts"),
        ("short_contacts", "Magenta dashes: short contacts"),
        ("covalent_link", "Orange dashes: covalent link"),
    ):
        if kind in distances:
            message.append(legend)
    overlay = {"planes": [], "bias_labels": []}
    if membrane is not None:
        cmd.hide("surface", protein)
        overlay = membrane_surface_review(identity, protein, **membrane)
        message.append("Ochre / green: hydrophobic / hydrophilic surface")
        if membrane.get("bounds"):
            # PyMOL's scene-message wizard expects ASCII text.
            message.append("Sheets: membrane-water interfaces")
        if membrane.get("core_bounds"):
            message.append("Rims: hydrophobic core boundaries")
        if overlay["bias_labels"]:
            message.append("Labeled sticks: bias-matched side chains")
        cmd.reset()
        cmd.turn("x", 70)
        cmd.zoom(f"({protein}) or ({ligand_name})", float(focus_padding) + 4)
        if focus_scene:
            cmd.scene(focus_scene, "store", message="\n".join(message))
    else:
        focus_selection = f"({ligand_name}) or ({pocket_name})"
        if focus_scene:
            acc_store_scene(
                focus_scene,
                zoom_selection=focus_selection,
                padding=float(focus_padding),
                message="\n".join(message),
            )
        else:
            cmd.orient(focus_selection)
            cmd.zoom(focus_selection, float(focus_padding))

    if focus_scene:
        scenes.append(focus_scene)
    chosen = overview_scene if overview_scene in scenes and _text(initial_scene, "focus").lower() == "overview" else focus_scene
    if chosen:
        cmd.scene(chosen, "recall")
    if scenes and _truthy(bind_keys, True):
        acc_bind_review_keys("scene")
    return {
        "identity": str(identity),
        "ligand": ligand_name,
        "pocket_residues": pocket_name,
        "pocket_surface": pocket_surface_name,
        "polar_contacts": distances.get("polar_contacts", ""),
        "distances": distances,
        "cavity_dots": next(iter(cavity_layers), ""),
        "cavity_count": sum(count for legend, count in cavity_layers.values()),
        "membrane": overlay,
        "scenes": scenes,
        "initial_scene": chosen,
        "contact_residue_atoms": _count(pocket_name),
    }


def protein_interface_review(
    identity: str,
    target_selection: str,
    partner_selection: str,
    interface_cutoff=4.0,
    polar_cutoff=3.6,
    target_color: str = "marine",
    partner_color: str = "orange",
    target_interface_color: str = "cyan",
    partner_interface_color: str = "salmon",
    target_surface_color: str = "lightblue",
    surface_transparency=0.72,
    context_transparency=0.45,
    overview_scene: str = "overview_surface",
    focus_scene: str = "interface_focus",
    initial_scene: str = "focus",
    overview_padding=5.0,
    focus_padding=9.0,
    show_polar_contacts=1,
    background: str = "white",
) -> Dict[str, object]:
    """Create whole-complex and local-interface scenes from verified partners."""

    target = _text(target_selection, "chain A and polymer.protein")
    partner = _text(partner_selection, "chain B and polymer.protein")
    _require_atoms(target, "target")
    _require_atoms(partner, "partner")
    if _count(f"({target}) and ({partner})"):
        raise CmdException("target and partner selections overlap")

    target_contacts = asset_name(identity, "target_interface")
    partner_contacts = asset_name(identity, "partner_interface")
    polar_name = asset_name(identity, "polar_contacts")

    acc_publication_defaults(background)
    _set("depth_cue", 1)
    _set("fog_start", 0.62)
    cmd.hide("everything", f"({target}) or ({partner})")
    cmd.show("cartoon", target)
    cmd.show("cartoon", partner)
    cmd.color(target_color, target)
    cmd.color(partner_color, partner)
    _set("cartoon_transparency", float(context_transparency), target)
    _set("cartoon_transparency", float(context_transparency), partner)
    _set("surface_color", target_surface_color, target)

    target_contacts = acc_contact_sidechains(
        selection=target,
        partner_selection=partner,
        cutoff=float(interface_cutoff),
        name=target_contacts,
        carbon_color=target_interface_color,
        stick_radius=0.22,
        cb_sphere=0,
        contact_scope="all",
    )
    partner_contacts = acc_contact_sidechains(
        selection=partner,
        partner_selection=target,
        cutoff=float(interface_cutoff),
        name=partner_contacts,
        carbon_color=partner_interface_color,
        stick_radius=0.22,
        cb_sphere=0,
        contact_scope="all",
    )
    target_sticks = _contact_residue_sticks(target_contacts)
    partner_sticks = _contact_residue_sticks(partner_contacts)
    _require_atoms(target_sticks, "target interface contact-residue display")
    _require_atoms(partner_sticks, "partner interface contact-residue display")

    polar_object = ""
    if _truthy(show_polar_contacts, True):
        target_polar = f"({target_contacts}) and not elem H and elem N+O+S"
        partner_polar = f"({partner_contacts}) and not elem H and elem N+O+S"
        if _count(target_polar) and _count(partner_polar):
            polar_object = acc_polar_contacts(
                polar_name,
                target_polar,
                partner_polar,
                cutoff=float(polar_cutoff),
                color="yelloworange",
                dash_radius=0.06,
                hide_distance_labels=1,
            )

    cmd.hide("sticks", target_sticks)
    cmd.hide("sticks", partner_sticks)
    if polar_object:
        cmd.disable(polar_object)
    cmd.show("surface", target)
    _set("transparency", float(surface_transparency), target)
    acc_store_scene(
        overview_scene,
        zoom_selection=f"({target}) or ({partner})",
        padding=float(overview_padding),
        message="Whole target-partner assembly with target surface context.",
    )

    cmd.hide("surface", target)
    cmd.show("cartoon", target)
    cmd.show("cartoon", partner)
    cmd.show("sticks", target_sticks)
    cmd.show("sticks", partner_sticks)
    if polar_object:
        cmd.enable(polar_object)
    acc_store_scene(
        focus_scene,
        zoom_selection=f"({target_contacts}) or ({partner_contacts})",
        padding=float(focus_padding),
        message=(
            f"Interface residues: any cross-partner heavy-atom contact <= "
            f"{float(interface_cutoff):g} A; dashes are donor/acceptor geometry candidates."
        ),
    )

    chosen = overview_scene if _text(initial_scene, "focus").lower() == "overview" else focus_scene
    cmd.scene(chosen, "recall")
    acc_bind_review_keys("scene")
    return {
        "identity": str(identity),
        "target_interface": target_contacts,
        "partner_interface": partner_contacts,
        "polar_contacts": polar_object,
        "scenes": [overview_scene, focus_scene],
        "initial_scene": chosen,
        "target_interface_atoms": _count(target_contacts),
        "partner_interface_atoms": _count(partner_contacts),
    }


cmd.extend("acc_ligand_pocket_review", ligand_pocket_review)
cmd.extend("acc_protein_interface_review", protein_interface_review)
