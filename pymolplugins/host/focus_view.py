"""Temporary atom and contact layers for interactive residue focus."""

from collections import defaultdict, deque
from dataclasses import dataclass
from math import dist
from uuid import uuid4

from pymol import util

from pymolplugins.annotation.review_view_presets import (
    _capture_interactions, _compute_interface, _indices, draw_interface_contacts,
)
from pymolplugins.annotation.review_view_geometry import residue_key


RADIUS = 5.0
_BACKBONE_ENDS = "polymer.protein and name N+C+O+OXT"


@dataclass
class FocusLayer:
    source: str
    state: int
    residue: tuple
    label: str
    names: list
    copy_indices: dict
    counts: tuple
    contact_layers: dict
    focus_selection: str
    frame_selection: str
    pocket_selection: str
    surface: str = ""

    @property
    def copies(self):
        return tuple(self.copy_indices)

    def delete(self, api):
        for name in self.names:
            api.delete(name)

    def show_contacts(self, api, kinds):
        for kind, name in self.contact_layers.items():
            (api.enable if kind in kinds else api.disable)(name)

    def move_camera(self, api, camera):
        if camera == "center":
            api.center(self.focus_selection, state=self.state, animate=0.25)
        elif camera == "frame":
            api.zoom(self.frame_selection, 4, state=self.state, complete=1, animate=0.3)

    def show_surface(self, api, enabled):
        if self.surface:
            (api.enable if enabled else api.disable)(self.surface)
        elif enabled:
            self.surface = _create_pocket_surface(api, self)
            self.names.append(self.surface)


def _create_pocket_surface(api, layer):
    """Calculate a molecular surface on only the extracted polymer shell."""
    if not api.count_atoms(layer.pocket_selection, state=layer.state):
        raise ValueError("No nearby polymer residues")
    name = api.get_unused_name(f"{layer.copies[0]}_pocket_surface")
    scope = f"%{name}"
    try:
        api.create(name, layer.pocket_selection, source_state=layer.state, target_state=1, zoom=0)
        api.hide("everything", scope)
        api.set("surface_solvent", 0, name)
        api.set("surface_color", "gray80", name)
        api.set("transparency", 0.55, name)
        api.mask(scope)
        api.show("surface", scope)
    except Exception:
        api.delete(name)
        raise
    return name


def picked_source(api, picked, current):
    """Map a picked local copy back to the source molecule."""
    atoms = api.index(picked)
    if len(atoms) != 1:
        return None
    source, index = atoms[0]
    if current is not None and source in current.copy_indices:
        original = current.copy_indices[source].get(index)
        if original is not None and current.source in api.get_names("objects"):
            return current.source, original
        return None
    if source.startswith("_pp_focus_"):
        return None
    return source, index


def focus_identity(api, source, index):
    state = api.get_object_state(source)
    atoms = api.get_model(_indices(source, [index]), state).atom
    if len(atoms) != 1:
        raise ValueError("The picked atom is unavailable in this state.")
    atom = atoms[0]
    return state, (atom.segi, atom.chain, atom.resi, atom.resn)


def _atom_identity(atom):
    return (*residue_key(atom), atom.name, atom.alt, atom.id)


def _copy_residues(api, name, selection, state, stick_radius):
    originals = defaultdict(deque)
    for atom in api.get_model(selection, state).atom:
        originals[_atom_identity(atom)].append(atom.index)
    scope = f"%{name}"
    api.create(name, selection, source_state=state, target_state=1, zoom=0)
    copy_indices = {}
    for atom in api.get_model(scope, 1).atom:
        matching = originals[_atom_identity(atom)]
        if not matching:
            raise ValueError("Focus copy did not preserve source atom identity.")
        copy_indices[atom.index] = matching.popleft()
    api.hide("everything", scope)
    api.set("stick_ball", 0, name)
    api.set("stick_radius", stick_radius, name)
    api.set("valence", 0, name)
    api.show("sticks", scope)
    api.show("nonbonded", f"{scope} and not polymer")
    return copy_indices


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
    target = f"({focus}) and (not ({_BACKBONE_ENDS}) or resn GLY)"
    sidechains = f"({surrounding}) and not ({_BACKBONE_ENDS})"
    if endpoints:
        selected_endpoints = _indices(source, endpoints)
        target += f" or (({focus}) and ({selected_endpoints}))"
        sidechains += f" or (({surrounding}) and ({selected_endpoints}))"
    names, copy_indices = [], {}
    prefix = f"{prefix}_{uuid4().hex[:8]}"
    try:
        for label, selection, radius in (
            ("target", target, 0.20),
            ("neighbors", sidechains, 0.15),
        ):
            if not api.count_atoms(selection, state=state):
                continue
            name = api.get_unused_name(f"{prefix}_{label}")
            names.append(name)
            copy_indices[name] = _copy_residues(api, name, selection, state, radius)
        scope = "(" + " or ".join(f"%{name}" for name in copy_indices) + ")"
        util.cbc(scope, _self=api)
        api.color("yellow", f"{scope} and organic and elem C")
        util.cnc(scope, _self=api)
        layers = draw_interface_contacts(api, prefix, coords, polar, salt, pi, names)
    except Exception:
        for name in names:
            api.delete(name)
        raise
    _, chain, resi, resn = residue
    label = f"{source} / {chain or '–'} / {resn} {resi}"
    return FocusLayer(
        source, state, residue, label, names, copy_indices, (len(polar), len(salt), len(pi)),
        layers, focus, f"({focus}) or ({surrounding})",
        f"({neighborhood}) and %{source} and polymer and not elem H",
    )
