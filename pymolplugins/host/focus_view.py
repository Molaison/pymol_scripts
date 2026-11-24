"""Temporary atom and contact layers for interactive residue focus."""

from collections import defaultdict, deque
from dataclasses import dataclass
from math import dist
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from pymol import util

from pymolplugins.annotation._bundle_skill.acceptance_interface import draw_buried_unsatisfied, interface_atom_sticks
from pymolplugins.annotation.interface_polar import capture_interface_polar, find_buried_unsatisfied
from pymolplugins.annotation.compare_scene import molecule_signature
from pymolplugins.annotation.review_view_presets import (
    _capture_interactions, _compute_interface, _indices, draw_interface_contacts, interface_partners,
)
from pymolplugins.annotation.review_view_geometry import residue_key

if TYPE_CHECKING:
    from pymolplugins.annotation.bundle_view import CavityRender


RADIUS = 5.0


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
    ligand_selection: str
    source_signature: tuple
    buried_count: int = 0
    surface: str = ""
    surface_shape: tuple = ()
    ligand_surface: str = ""
    void_result: Optional["CavityRender"] = None

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

    def pocket_selection(self, radius):
        return (f"(byres ((%{self.source} and not elem H) within {radius:g} "
                f"of ({self.focus_selection}))) and %{self.source} and polymer and not elem H")

    def show_ligand_surface(self, api, enabled):
        if enabled and self.ligand_selection:
            self._ensure_ligand_surface(api)
        if self.ligand_surface:
            (api.enable if enabled else api.disable)(self.ligand_surface)

    def _ensure_ligand_surface(self, api):
        if not self.ligand_surface:
            name = api.get_unused_name(f"{self.copies[0]}_ligand_surface")
            _create_surface(api, name, self.ligand_selection, self.state, "salmon", 0.75)
            api.disable(name)
            self.ligand_surface = name
            self.names.append(name)
        return self.ligand_surface

    def show_surface(self, api, enabled, radius, transparency, facing):
        if not enabled:
            if self.surface:
                api.disable(self.surface)
            return
        selection = self.pocket_selection(radius)
        if not api.count_atoms(selection, state=self.state):
            raise ValueError("No nearby polymer residues")
        anchor = self.copies[0] if facing and self.ligand_selection else ""
        shape = (radius, bool(anchor))
        if self.surface and self.surface_shape == shape:
            api.set("transparency", transparency, self.surface)
            api.enable(self.surface)
            return
        carve = (anchor, radius) if anchor else None
        name = api.get_unused_name(f"{self.copies[0]}_pocket_surface_{uuid4().hex[:8]}")
        _create_surface(api, name, selection, self.state, "gray80", transparency, carve)
        if self.surface:
            api.delete(self.surface)
            self.names.remove(self.surface)
        self.surface, self.surface_shape = name, shape
        self.names.append(name)


def _create_surface(api, name, selection, state, color, transparency, carve=None):
    """Create a masked molecular surface on only the explicitly copied atoms."""
    scope = f"%{name}"
    try:
        api.create(name, selection, source_state=state, target_state=1, zoom=0)
        api.hide("everything", scope)
        api.set("surface_solvent", 0, name)
        api.set("surface_color", color, name)
        api.set("transparency", transparency, name)
        if carve:
            anchor, radius = carve
            api.set("surface_proximity", 0, name)
            api.set("surface_carve_selection", anchor, name)
            api.set("surface_carve_state", 1, name)
            api.set("surface_carve_cutoff", radius, name)
            api.set("surface_carve_normal_cutoff", 0.35, name)
        else:
            api.set("surface_carve_cutoff", 0, name)
        api.mask(scope)
        api.show("surface", scope)
    except Exception:
        api.delete(name)
        raise


def picked_source(api, picked, layers):
    """Map a picked local copy back to the source molecule."""
    atoms = api.index(picked)
    if len(atoms) != 1:
        return None
    source, index = atoms[0]
    for layer in layers:
        if source in layer.copy_indices:
            original = layer.copy_indices[source].get(index)
            if (original is not None and layer.source in api.get_names("objects", enabled_only=1)
                    and molecule_signature(api, layer.source) == layer.source_signature):
                return layer.source, original
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
    residue_selection = f"(byres ({_indices(source, [index])})) and not elem H"
    focus_atoms = api.index(residue_selection)
    if not focus_atoms:
        raise ValueError("The picked residue has no heavy atoms.")
    focus = _indices(source, (atom for _, atom in focus_atoms))
    if not api.count_atoms(focus, state=state):
        raise ValueError("The picked residue has no heavy atoms.")
    nearby = f"byres ((%{source} and not elem H) within {RADIUS:g} of ({focus}))"
    # Resolve the geometric selection once for the contact, polar and stick queries.
    neighborhood = _indices(source, (atom for _, atom in api.index(nearby)))
    surrounding = f"({neighborhood}) and not (({focus}) or elem H)"
    ligand = f"({focus}) and organic"
    if not api.count_atoms(ligand, state=state):
        ligand = ""
    coords, endpoints, polar, salt, pi = _local_contacts(api, focus, surrounding, state)
    partners, _ = interface_partners(api, source)
    buried = find_buried_unsatisfied(capture_interface_polar(api, source, state, neighborhood, partners))
    sticks = interface_atom_sticks(neighborhood, _indices(source, endpoints) if endpoints else "none",
                                   _indices(source, buried) if buried else "none")
    target = f"({focus}) and (({sticks}) or resn GLY)"
    sidechains = f"({surrounding}) and ({sticks})"
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
        buried_layer = draw_buried_unsatisfied(api, prefix, buried.values())
        if buried_layer:
            names.append(buried_layer)
            layers["buried_unsatisfied_polar"] = buried_layer
    except Exception:
        for name in names:
            api.delete(name)
        raise
    _, chain, resi, resn = residue
    label = f"{source} / {chain or '–'} / {resn} {resi}"
    return FocusLayer(
        source, state, residue, label, names, copy_indices, (len(polar), len(salt), len(pi)),
        layers, focus, f"({focus}) or ({surrounding})",
        ligand, molecule_signature(api, source), buried_count=len(buried),
    )
