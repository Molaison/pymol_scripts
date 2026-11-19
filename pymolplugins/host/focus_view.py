"""Temporary atom and contact layers for interactive residue focus."""

from dataclasses import dataclass
from math import dist

from pymol import util

from pymolplugins.annotation.review_view_presets import (
    _capture_interactions, _compute_interface, _cylinders, _indices,
)
from pymolplugins.annotation.review_view_geometry import residue_key


RADIUS = 5.0
_BACKBONE_ENDS = "polymer.protein and name N+C+O+OXT"
_CONTACT_STYLE = (
    ("hydrogen_bonds", (0.169, 0.514, 0.729), 0.07, 0.53),
    ("salt_bridges", (0.941, 0.784, 0.078), 0.09, 0.78),
    ("pi_stacks", (0.549, 0.702, 0.400), 0.08, 0.54),
)


@dataclass
class FocusLayer:
    source: str
    state: int
    residue: tuple
    label: str
    names: list
    copies: tuple
    counts: tuple

    def delete(self, api):
        for name in self.names:
            api.delete(name)


def picked_source(api, picked, current):
    """Map a picked local copy back to the source molecule."""
    atoms = api.index(picked)
    if len(atoms) != 1:
        return None
    source, index = atoms[0]
    if current is not None and source in current.copies:
        if current.source not in api.get_names("objects"):
            return None
        original = api.index(f"%{current.source} in ({_indices(source, [index])})")
        return original[0] if len(original) == 1 else None
    return source, index


def focus_identity(api, source, index):
    state = api.get_object_state(source)
    atoms = api.get_model(_indices(source, [index]), state).atom
    if len(atoms) != 1:
        raise ValueError("The picked atom is unavailable in this state.")
    atom = atoms[0]
    return state, (atom.segi, atom.chain, atom.resi, atom.resn)


def _copy_residues(api, name, selection, state, carbon, ball_scale, stick_radius):
    scope = f"%{name}"
    api.create(name, selection, source_state=state, target_state=1, zoom=0)
    api.hide("everything", scope)
    api.color(carbon, f"{scope} and elem C")
    util.cnc(scope, _self=api)
    api.set("stick_radius", stick_radius, name)
    api.set("sphere_scale", ball_scale, name)
    api.set("valence", 0, name)
    api.show("sticks", scope)
    api.show("spheres", scope)


def _local_contacts(api, focus, surrounding, state):
    typed, models = _capture_interactions(api, focus, surrounding, state)
    neighbor = {atom.index: residue_key(atom) for atom in models[1].atom}
    coords = {atom.index: atom.coord for model in models for atom in model.atom}
    closest = {}
    for pair in sorted(typed["salt_bridges"]):
        left, right = pair
        residue = neighbor[left] if left in neighbor else neighbor[right]
        distance = dist(coords[left], coords[right])
        if residue not in closest or distance < closest[residue][0]:
            closest[residue] = (distance, pair)
    typed["salt_bridges"] = {pair for _, pair in closest.values()}
    return _compute_interface([(typed, models)])


def draw_focus(api, prefix, source, index, state, residue):
    """Create replacement layers and report the local typed contact counts."""
    focus = f"(byres ({_indices(source, [index])})) and not elem H"
    if not api.count_atoms(focus, state=state):
        raise ValueError("The picked residue has no heavy atoms.")
    neighborhood = f"(byres ((%{source} and not elem H) within {RADIUS:g} of ({focus})))"
    surrounding = f"({neighborhood}) and not (({focus}) or elem H)"
    coords, endpoints, polar, salt, pi = _local_contacts(api, focus, surrounding, state)
    sidechains = f"({surrounding}) and not ({_BACKBONE_ENDS})"
    if endpoints:
        sidechains += f" or (({surrounding}) and ({_indices(source, endpoints)}))"
    names, copies = [], []
    try:
        for label, selection, carbon, scale, radius in (
            ("target", focus, "0xf0b364", 0.22, 0.17),
            ("neighbors", sidechains, "0xb9bdc7", 0.15, 0.11),
        ):
            if not api.count_atoms(selection, state=state):
                continue
            name = api.get_unused_name(f"{prefix}_{label}")
            names.append(name)
            _copy_residues(api, name, selection, state, carbon, scale, radius)
            copies.append(name)
        pairs = (
            ((coords[a], coords[b]) for a, b in sorted(polar)),
            ((coords[a], coords[b]) for a, b in sorted(salt)),
            iter(pi),
        )
        for (kind, color, width, dash), segments in zip(_CONTACT_STYLE, pairs):
            name = _cylinders(api, prefix, kind, segments, color, width, dash)
            if name is not None:
                names.append(name)
        api.zoom(" or ".join(f"%{name}" for name in copies), 4, state=1, animate=0)
        api.center(f"%{copies[0]}", state=1, animate=0)
    except Exception:
        for name in names:
            api.delete(name)
        raise
    _, chain, resi, resn = residue
    label = f"{source} / {chain or '–'} / {resn} {resi}"
    return FocusLayer(source, state, residue, label, names, tuple(copies), (len(polar), len(salt), len(pi)))
