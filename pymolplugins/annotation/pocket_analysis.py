"""Measure pose and backbone changes and summarize protein–ligand contacts."""
from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Optional

from pymolplugins.interactions.contact_queries import find_pairs, hydrogen_bond_specs, salt_bridge_specs

from .pocket_variants import PRIMARY_HEAVY, aligned_fragment, protein_model, residues, site_key


BACKBONE_ATOMS = frozenset(("N", "CA", "C", "O"))
CONTACT_COLORS = {"Retained": (0.12, 0.65, 0.53), "Gained": (0.16, 0.43, 0.85),
                  "Lost in some": (0.86, 0.34, 0.15), "Lost": (0.86, 0.34, 0.15)}


@dataclass(frozen=True)
class Snapshot:
    protein: object
    ligand: object
    contacts: dict


@dataclass(frozen=True)
class Geometry:
    entry_id: str
    candidate: str
    ligand: Optional[float]
    backbone: Optional[float]

    def status(self, ligand_limit, backbone_limit):
        values = (self.ligand, self.backbone)
        if any(value is not None and value > limit for value, limit in zip(values, (ligand_limit, backbone_limit))):
            return "Outside tolerance"
        return "Unmapped" if None in values else "Within tolerances"

    @property
    def unmapped(self):
        return self.ligand is None or self.backbone is None


@dataclass(frozen=True)
class Contact:
    site: tuple
    kind: str
    in_reference: bool
    present: int
    evaluated: int
    points: tuple

    @property
    def change(self):
        if not self.in_reference:
            return "Gained"
        if not self.present:
            return "Lost"
        return "Lost in some" if self.present < self.evaluated else "Retained"


@dataclass(frozen=True)
class ContactChange:
    site: tuple
    kind: str
    change: str
    points: tuple
    protein_atoms: tuple
    ligand_atoms: tuple
    residue: object


@dataclass(frozen=True)
class ContactAtom:
    site: tuple
    residue: str
    name: str
    point: tuple


@dataclass(frozen=True)
class ContactPose:
    points: tuple
    protein_atoms: tuple
    ligand_atoms: tuple


@dataclass(frozen=True)
class CandidatePreview:
    geometry: Geometry
    ligand: object
    backbone: object
    changes: tuple
    ligand_matched: bool


def _atom_lookup(api, selection):
    atoms = {}
    api.iterate_state(1, selection, "atoms[model, index] = ContactAtom((chain, resi), resn, name, (x, y, z))",
                      space={"atoms": atoms, "ContactAtom": ContactAtom})
    return atoms


def _closest_pairs(contacts, kind, pairs, proteins, ligands):
    for a, b in pairs:
        if a in ligands:
            a, b = b, a
        if a not in proteins or b not in ligands:
            continue
        start, end = proteins[a], ligands[b]
        pose = ContactPose((start.point, end.point), (start,), (end,))
        _record_contact(contacts, (start.site, kind), pose)


def _record_contact(contacts, key, pose):
    if key not in contacts or _distance_sq(pose.points) < _distance_sq(contacts[key].points):
        contacts[key] = pose


def _distance_sq(pair):
    start, end = pair
    return sum((a - b) ** 2 for a, b in zip(start, end))


def _pi_contacts(api, protein, ligand, ligand_atoms, contacts):
    from .review_view_geometry import aromatic_rings, stacking_pairs

    nearby = protein_model(api, f"byres (({protein}) within 6 of ({ligand}))")
    proteins, ligands = _indexed_atoms(nearby), _indexed_atoms(ligand_atoms)
    for start, end, protein_indices, ligand_indices in stacking_pairs(aromatic_rings(nearby),
                                                                     aromatic_rings(ligand_atoms)):
        protein_ring = tuple(proteins.get(i) for i in protein_indices)
        ligand_ring = tuple(ligands.get(i) for i in ligand_indices)
        if None in protein_ring or None in ligand_ring:
            continue
        sites = {atom.site for atom in protein_ring}
        if len(sites) == 1:
            pose = ContactPose((tuple(start), tuple(end)), protein_ring, ligand_ring)
            _record_contact(contacts, (sites.pop(), "π stacking"), pose)


def _indexed_atoms(model):
    result = {}
    for atom in model.atom:
        result[atom.index] = (None if atom.index in result else
                              ContactAtom(site_key(atom), atom.resn, atom.name, tuple(atom.coord)))
    return result


def capture_snapshot(api, protein, ligand, cutoff):
    protein_atoms, ligand_atoms = protein_model(api, protein), protein_model(api, ligand)
    contacts = {}
    if not ligand_atoms.atom:
        return Snapshot(protein_atoms, ligand_atoms, contacts)
    first, second = (f"({selection}) and {PRIMARY_HEAVY}" for selection in (protein, ligand))
    proteins, ligands = _atom_lookup(api, first), _atom_lookup(api, second)
    pairs = find_pairs(api, first, second, cutoff, 0, 1)
    _closest_pairs(contacts, "Heavy-atom contact", pairs, proteins, ligands)
    for kind, specs in (("Polar contact", hydrogen_bond_specs(first, second, 3.6)),
                        ("Salt bridge", salt_bridge_specs(first, second, 4.0))):
        _closest_pairs(contacts, kind, (pair for a, b, limit, mode, *_ in specs
                                        for pair in find_pairs(api, a, b, limit, mode, 1)),
                       proteins, ligands)
    _pi_contacts(api, first, second, ligand_atoms, contacts)
    return Snapshot(protein_atoms, ligand_atoms, contacts)


def _ligand_coords(model):
    # Use atom labels when unique so the same small molecule can be renumbered.
    keys = [(atom.resn, atom.name, atom.symbol) for atom in model.atom]
    if len(set(keys)) != len(keys):
        keys = [(site_key(atom), *key) for atom, key in zip(model.atom, keys)]
    return dict(zip(keys, (atom.coord for atom in model.atom))) if len(set(keys)) == len(keys) else {}


def _backbone_coords(model, sites):
    return {(site_key(atom), atom.name): atom.coord for atom in model.atom
            if atom.name in BACKBONE_ATOMS and site_key(atom) in sites}


def _pocket_backbone(model, sites, rotation=None, offset=None):
    indices = (index for index, atom in enumerate(model.atom)
               if atom.name in BACKBONE_ATOMS and site_key(atom) in sites)
    return aligned_fragment(model, indices, rotation, offset)


def _aligned_points(points, rotation, offset):
    return tuple(tuple(point @ rotation + offset) for point in points)


def _aligned_atoms(atoms, rotation, offset):
    return tuple(ContactAtom(atom.site, atom.residue, atom.name, tuple(atom.point @ rotation + offset))
                 for atom in atoms)


def _rmsd(mobile, reference, rotation, offset):
    import numpy as np

    source = np.array([mobile[key] for key in reference])
    target = np.array(list(reference.values()))
    delta = source @ rotation + offset - target
    value = float(np.sqrt(np.sum(delta * delta) / len(target)))
    return value if isfinite(value) else None


class PocketAnalysis:
    def __init__(self, reference, sites):
        self.reference = reference
        self.sites = frozenset(sites)
        self.reference_ligand = _ligand_coords(reference.ligand)
        self.reference_backbone = _backbone_coords(reference.protein, self.sites)
        self.reference_fragment = _pocket_backbone(reference.protein, self.sites)
        self.reference_residues = residues(reference.protein)
        self.geometry = []
        self.previews = {}
        self.coverage = Counter()
        self.present = Counter()
        self.samples = {}

    def add(self, entry_id, name, snapshot, mapped, rotation, offset):
        ligand = _ligand_coords(snapshot.ligand)
        backbone = _backbone_coords(snapshot.protein, self.sites)
        ligand_matched = bool(self.reference_ligand) and ligand.keys() == self.reference_ligand.keys()
        backbone_matched = bool(self.reference_backbone) and self.reference_backbone.keys() <= backbone.keys()
        geometry = Geometry(
            entry_id, name, _rmsd(ligand, self.reference_ligand, rotation, offset) if ligand_matched else None,
            _rmsd(backbone, self.reference_backbone, rotation, offset) if backbone_matched else None,
        )
        self.geometry.append(geometry)
        changes = self._contact_changes(snapshot, mapped, rotation, offset) if ligand_matched else ()
        self.previews[entry_id] = CandidatePreview(
            geometry, aligned_fragment(snapshot.ligand, range(len(snapshot.ligand.atom)), rotation, offset),
            _pocket_backbone(snapshot.protein, self.sites, rotation, offset), changes, ligand_matched)
        if ligand_matched:
            self.coverage.update(mapped.keys())
            for key, pose in snapshot.contacts.items():
                if key[0] in mapped:
                    self.present[key] += 1
                    if key not in self.samples:
                        self.samples[key] = _aligned_points(pose.points, rotation, offset)

    def _contact_changes(self, snapshot, mapped, rotation, offset):
        reference, candidate = self.reference.contacts, snapshot.contacts
        shared = self.reference_residues.keys() & mapped.keys()
        gained = _make_changes(candidate, reference, snapshot.protein, mapped, shared, "Gained", rotation, offset)
        lost = _make_changes(reference, candidate, self.reference.protein, self.reference_residues, shared, "Lost")
        return (*gained, *lost)

    def interactions(self):
        contacts = []
        reference = self.reference.contacts
        for key in sorted(reference.keys() | self.present.keys()):
            site, kind = key
            if not self.coverage[site]:
                continue
            points = reference[key].points if key in reference else self.samples[key]
            contacts.append(Contact(site, kind, key in reference, self.present[key], self.coverage[site], points))
        return contacts


def _make_changes(contacts, baseline, protein, residue_map, shared, change, rotation=None, offset=None):
    fragments = {}
    for site, kind in sorted(contacts.keys() - baseline.keys()):
        if site not in shared:
            continue
        pose = contacts[site, kind]
        if site not in fragments:
            fragments[site] = aligned_fragment(protein, residue_map[site].atoms, rotation, offset)
        if rotation is not None:
            pose = ContactPose(_aligned_points(pose.points, rotation, offset),
                               _aligned_atoms(pose.protein_atoms, rotation, offset),
                               _aligned_atoms(pose.ligand_atoms, rotation, offset))
        yield ContactChange(site, kind, change, pose.points, pose.protein_atoms, pose.ligand_atoms, fragments[site])

