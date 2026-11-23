"""Draw aligned pocket contacts with the shared Interface representations."""

from dataclasses import replace

from pymol import util

from ._bundle_skill.acceptance_interface import (
    capture_assembly_polar, draw_buried_unsatisfied, find_buried_unsatisfied, show_interface_sticks,
)
from .pocket_analysis import _atom_lookup
from .review_view_presets import _indices, draw_interface_segments


def _color_atoms(api, selection, color):
    api.color(color, selection)
    util.cnc(selection, _self=api)


def _endpoint_selection(api, name, atoms):
    keys = {(atom.site, atom.name) for atom in atoms}
    indices = []
    api.iterate(f"%{name}", "indices.append(index) if ((chain, resi), name) in keys else None",
                space={"indices": indices, "keys": keys})
    return _indices(name, indices) if indices else "none"


def reference_buried_atoms(api, scope, target, partner, radius):
    first = f"(({target}) and not hydro) within {radius:g} of (({partner}) and not hydro)"
    second = f"(({partner}) and not hydro) within {radius:g} of (({target}) and not hydro)"
    focus = f"byres (({first}) or ({second}))"
    snapshot = capture_assembly_polar(api, api.get_object_list(scope), 1, focus, ((target, partner),))
    buried = find_buried_unsatisfied(snapshot)
    if not buried:
        return ()
    atoms = _atom_lookup(api, f"({scope}) and ({focus})")
    return tuple(replace(atoms[key], point=point) for key, point in buried.items() if key in atoms)


def _draw_residue_context(api, reference, contacts, names, buried):
    groups = {}
    for contact in contacts:
        sides = [(contact.residue, contact.pose.protein_atoms)]
        if contact.partner_site:
            sides.append((contact.partner_residue, contact.pose.ligand_atoms))
        for fragment, endpoints in sides:
            atoms, _, _ = groups.setdefault(id(fragment), (set(), fragment, contact.change))
            atoms.update(endpoints)
    if buried:
        atoms, _, _ = groups.setdefault(id(None), (set(), None, "Reference"))
        atoms.update(buried)
    for atoms, fragment, change in groups.values():
        name = api.get_unused_name(f"{reference}_contact_residues")
        if fragment is None:
            selection = _endpoint_selection(api, reference, atoms)
            if selection == "none":
                continue
            names.append(name)
            api.create(name, f"byres ({selection})", 1, 1, zoom=0)
        else:
            names.append(name)
            api.load_model(fragment, name, quiet=1, zoom=0)
        api.hide("everything", name)
        api.set("stick_color", -1, name)
        _color_atoms(api, f"%{name}", "violet" if change == "Gained" else "gray70")
        buried_atoms = _endpoint_selection(api, name, buried) if fragment is None and buried else "none"
        show_interface_sticks(api, f"%{name}", _endpoint_selection(api, name, atoms), buried_atoms)


def draw_pocket_interactions(api, reference, contacts, names, *, selected=None, buried=()):
    """Draw chemistry-specific interactions and label the selected endpoint atoms."""
    segments = {"Polar contact": {}, "Salt bridge": {}, "π stacking": {}}
    for contact in contacts:
        if contact.kind in segments:
            segments[contact.kind][contact.pose.points] = None
    salts = segments["Salt bridge"]
    polar = (pair for pair in segments["Polar contact"] if pair not in salts)
    draw_interface_segments(api, reference, polar, salts, segments["π stacking"], names)
    detail = [contact for contact in contacts if contact.kind in segments or contact is selected]
    _draw_residue_context(api, reference, detail, names, buried)
    marker = draw_buried_unsatisfied(api, reference, (atom.point for atom in buried)) if buried else None
    if marker:
        names.append(marker)
    if selected is not None:
        name = api.get_unused_name(f"{reference}_contact_atom_labels")
        names.append(name)
        for atom in dict.fromkeys((*selected.pose.protein_atoms, *selected.pose.ligand_atoms)):
            api.pseudoatom(name, pos=atom.point, label=atom.label, quiet=1)
        api.hide("everything", name)
        api.show("labels", name)


def load_fragment(api, name, model, color, radius, transparency):
    api.load_model(model, name, quiet=1, zoom=0)
    api.hide("everything", name)
    _color_atoms(api, f"%{name}", color)
    if model.bond:
        api.show("sticks", name)
        api.set("stick_radius", radius, name)
        api.set("stick_transparency", transparency, name)
    else:
        api.show("spheres", name)
        api.set("sphere_scale", radius, name)
        api.set("sphere_transparency", transparency, name)


def load_protein_partner(api, name, model, color, transparency):
    api.load_model(model, name, quiet=1, zoom=0)
    api.hide("everything", name)
    _color_atoms(api, f"%{name}", color)
    api.set("cartoon_color", color, name)
    api.set("cartoon_transparency", transparency, name)
    api.show("cartoon", name)


def draw_candidate(api, reference, group, preview, reference_backbone, *, protein_partner=False):
    objects, interactions = {}, []
    try:
        for kind, model, color, radius, transparency in (
            ("reference_backbone", reference_backbone, "gray55", .10, .32),
            ("candidate_backbone", preview.backbone, "violet", .13, .12),
        ):
            if model.atom:
                name = objects[kind] = api.get_unused_name(f"{reference}_{kind}")
                load_fragment(api, name, model, color, radius, transparency)
        if preview.ligand.atom:
            kind = "candidate_partner" if protein_partner else "candidate_ligand"
            name = objects[kind] = api.get_unused_name(f"{reference}_{kind}")
            if protein_partner:
                load_protein_partner(api, name, preview.ligand, "magenta", .03)
            else:
                load_fragment(api, name, preview.ligand, "magenta", .24, .03)
        draw_pocket_interactions(api, reference, preview.changes, interactions)
        objects.update((f"interaction_{name}", name) for name in interactions)
        if objects:
            api.group(group, " ".join(objects.values()))
    except Exception:
        for name in {*objects.values(), *interactions}:
            api.delete(name)
        raise
    return objects


def draw_contact_focus(api, reference, group, contact):
    names = []
    try:
        draw_pocket_interactions(api, reference, (contact,), names, selected=contact)
        api.group(group, " ".join(names))
    except Exception:
        for name in names:
            api.delete(name)
        raise
    return names
