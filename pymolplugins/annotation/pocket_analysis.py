"""Measure pose and backbone changes and summarize protein–ligand contacts."""
from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Optional

from pymolplugins.interactions.contact_queries import find_pairs, hydrogen_bond_specs, salt_bridge_specs

from .pocket_variants import PRIMARY_HEAVY, aligned_fragment, bonds_by_site, protein_model, residues, site_key


BACKBONE_ATOMS = frozenset(("N", "CA", "C", "O"))


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
    pose: "ContactPose"
    residue: object = None
    partner_site: tuple = ()
    partner_residue: object = None

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
    pose: "ContactPose"
    residue: object
    partner_site: tuple = ()
    partner_residue: object = None


@dataclass(frozen=True)
class ContactAtom:
    site: tuple
    residue: str
    name: str
    element: str
    point: tuple

    @property
    def label(self):
        return f"{self.name} ({self.element})"


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


@dataclass(frozen=True)
class PocketContribution:
    preview: CandidatePreview
    covered_sites: tuple
    present_contacts: tuple
    samples: dict
    covered_partner_sites: tuple = ()


def _atom_lookup(api, selection):
    return {(model, atom.index): ContactAtom(site_key(atom), atom.resn, atom.name,
                                            atom.symbol, tuple(atom.coord))
            for model in api.get_object_list(selection)
            for atom in api.get_model(f"(%{model}) and ({selection})", 1).atom}


def _contact_partner(pose, protein_partner, ligand_sites):
    if protein_partner:
        return pose.ligand_atoms[0].site
    if ligand_sites:
        return tuple(sorted((atom.name, atom.element) for atom in pose.ligand_atoms))
    return ()


def _closest_pairs(contacts, kind, pairs, proteins, ligands, protein_partner, ligand_sites):
    for a, b in pairs:
        if a in ligands:
            a, b = b, a
        if a not in proteins or b not in ligands:
            continue
        start, end = proteins[a], ligands[b]
        pose = ContactPose((start.point, end.point), (start,), (end,))
        key = start.site, kind, _contact_partner(pose, protein_partner, ligand_sites)
        _record_contact(contacts, key, pose)


def _record_contact(contacts, key, pose):
    if key not in contacts or _distance_sq(pose.points) < _distance_sq(contacts[key].points):
        contacts[key] = pose


def _distance_sq(pair):
    start, end = pair
    return sum((a - b) ** 2 for a, b in zip(start, end))


def _pi_contacts(api, protein, ligand, ligand_atoms, contacts, protein_partner, ligand_sites):
    from .review_view_geometry import aromatic_rings, stacking_pairs

    nearby = protein_model(api, f"byres (({protein}) within 6 of ({ligand}))")
    if protein_partner:
        ligand_atoms = protein_model(api, f"byres (({ligand}) within 6 of ({protein}))")
    proteins, ligands = _indexed_atoms(nearby), _indexed_atoms(ligand_atoms)
    for start, end, protein_indices, ligand_indices in stacking_pairs(aromatic_rings(nearby),
                                                                     aromatic_rings(ligand_atoms)):
        protein_ring = tuple(proteins.get(i) for i in protein_indices)
        ligand_ring = tuple(ligands.get(i) for i in ligand_indices)
        if None in protein_ring or None in ligand_ring:
            continue
        sites = {atom.site for atom in protein_ring}
        other_sites = {atom.site for atom in ligand_ring} if protein_partner else {()}
        if len(sites) == 1 and len(other_sites) == 1:
            pose = ContactPose((tuple(start), tuple(end)), protein_ring, ligand_ring)
            key = sites.pop(), "π stacking", _contact_partner(pose, protein_partner, ligand_sites)
            _record_contact(contacts, key, pose)


def _indexed_atoms(model):
    result = {}
    for atom in model.atom:
        result[atom.index] = (None if atom.index in result else
                              ContactAtom(site_key(atom), atom.resn, atom.name, atom.symbol, tuple(atom.coord)))
    return result


def capture_snapshot(api, protein, ligand, cutoff, *, protein_partner=False, ligand_sites=False):
    protein_atoms, ligand_atoms = protein_model(api, protein), protein_model(api, ligand)
    contacts = {}
    if not ligand_atoms.atom:
        return Snapshot(protein_atoms, ligand_atoms, contacts)
    first, second = (f"({selection}) and {PRIMARY_HEAVY}" for selection in (protein, ligand))
    proteins, ligands = _atom_lookup(api, first), _atom_lookup(api, second)
    pairs = find_pairs(api, first, second, cutoff, 0, 1)
    _closest_pairs(contacts, "Heavy-atom contact", pairs, proteins, ligands, protein_partner, ligand_sites)
    for kind, specs in (("Polar contact", hydrogen_bond_specs(first, second, 3.6)),
                        ("Salt bridge", salt_bridge_specs(first, second, 4.0))):
        _closest_pairs(contacts, kind, (pair for a, b, limit, mode, *_ in specs
                                        for pair in find_pairs(api, a, b, limit, mode, 1)),
                       proteins, ligands, protein_partner, ligand_sites)
    _pi_contacts(api, first, second, ligand_atoms, contacts, protein_partner, ligand_sites)
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


def partner_ca_coords(model):
    return {site: residue.anchor for site, residue in residues(model).items()}


def _pocket_backbone(model, sites, rotation=None, offset=None):
    indices = (index for index, atom in enumerate(model.atom)
               if atom.name in BACKBONE_ATOMS and site_key(atom) in sites)
    return aligned_fragment(model, indices, rotation, offset)


def _aligned_points(points, rotation, offset):
    return tuple(tuple(point @ rotation + offset) for point in points)


def _aligned_atoms(atoms, rotation, offset):
    return tuple(ContactAtom(atom.site, atom.residue, atom.name, atom.element, tuple(atom.point @ rotation + offset))
                 for atom in atoms)


def _aligned_pose(pose, rotation, offset):
    return ContactPose(_aligned_points(pose.points, rotation, offset),
                       _aligned_atoms(pose.protein_atoms, rotation, offset),
                       _aligned_atoms(pose.ligand_atoms, rotation, offset))


def _rmsd(mobile, reference, rotation, offset):
    import numpy as np

    source = np.array([mobile[key] for key in reference])
    target = np.array(list(reference.values()))
    delta = source @ rotation + offset - target
    value = float(np.sqrt(np.sum(delta * delta) / len(target)))
    return value if isfinite(value) else None


class PocketAnalysis:
    def __init__(self, reference, sites, *, protein_partner=False):
        self.reference = reference
        self.protein_partner = protein_partner
        self.sites = frozenset(sites)
        self.reference_ligand = (partner_ca_coords if protein_partner else _ligand_coords)(reference.ligand)
        self.reference_backbone = _backbone_coords(reference.protein, self.sites)
        self.reference_fragment = _pocket_backbone(reference.protein, self.sites)
        self.reference_residues = residues(reference.protein)
        self.reference_partner = residues(reference.ligand) if protein_partner else {}
        self.geometry = []
        self.previews = {}
        self.coverage = Counter()
        self.target_membership = {}
        self.partner_membership = {}
        self.present = Counter()
        self.samples = {}

    def prepare(self, entry_id, name, snapshot, mapped, rotation, offset):
        partner = residues(snapshot.ligand) if self.protein_partner else {}
        ligand = {site: residue.anchor for site, residue in partner.items()} if self.protein_partner else _ligand_coords(snapshot.ligand)
        backbone = _backbone_coords(snapshot.protein, self.sites)
        if self.protein_partner:
            shared = self.reference_ligand.keys() & ligand.keys()
            match = {key: self.reference_ligand[key] for key in self.reference_ligand if key in shared}
            compared = _rmsd(ligand, match, rotation, offset) if len(match) >= 3 else None
            ligand_matched = bool(partner)
        else:
            ligand_matched = bool(self.reference_ligand) and ligand.keys() == self.reference_ligand.keys()
            compared = _rmsd(ligand, self.reference_ligand, rotation, offset) if ligand_matched else None
        backbone_matched = bool(self.reference_backbone) and self.reference_backbone.keys() <= backbone.keys()
        geometry = Geometry(
            entry_id, name, compared,
            _rmsd(backbone, self.reference_backbone, rotation, offset) if backbone_matched else None,
        )
        contacts_available = bool(partner) if self.protein_partner else bool(snapshot.ligand.atom)
        changes = self._contact_changes(snapshot, mapped, rotation, offset, partner=partner) if contacts_available else ()
        preview = CandidatePreview(
            geometry, aligned_fragment(snapshot.ligand, range(len(snapshot.ligand.atom)), rotation, offset),
            _pocket_backbone(snapshot.protein, self.sites, rotation, offset), changes, ligand_matched)
        if not contacts_available:
            return PocketContribution(preview, (), (), {})
        samples = {(change.site, change.kind, change.partner_site): change for change in changes if change.change == "Gained"}
        present = []
        uncaptured = {}
        for key, pose in snapshot.contacts.items():
            site, kind, other = key
            if site not in mapped or (other and other not in partner):
                continue
            present.append(key)
            if key not in self.reference.contacts and key not in samples:
                uncaptured[key] = pose
        if uncaptured:
            for change in _make_changes(uncaptured, snapshot, mapped, partner, "Gained", rotation, offset):
                samples[change.site, change.kind, change.partner_site] = change
        return PocketContribution(preview, tuple(mapped), tuple(present), samples, tuple(partner))

    def add(self, contribution):
        preview = contribution.preview
        if self.protein_partner:
            bit = 1 << len(self.geometry)
            for sites, membership in ((contribution.covered_sites, self.target_membership),
                                      (contribution.covered_partner_sites, self.partner_membership)):
                for site in sites:
                    membership[site] = membership.get(site, 0) | bit
        self.geometry.append(preview.geometry)
        self.previews[preview.geometry.entry_id] = preview
        self.coverage.update(contribution.covered_sites)
        self.present.update(contribution.present_contacts)
        for key, sample in contribution.samples.items():
            self.samples.setdefault(key, sample)

    def _contact_changes(self, snapshot, mapped, rotation, offset, *, partner):
        reference, candidate = self.reference.contacts, snapshot.contacts
        shared = self.reference_residues.keys() & mapped.keys()
        other_shared = self.reference_partner.keys() & partner.keys()

        def mapped_changes(contacts, baseline):
            return {key: contacts[key] for key in contacts.keys() - baseline.keys()
                    if key[0] in shared and (not key[2] or key[2] in other_shared)}

        gained = _make_changes(mapped_changes(candidate, reference), snapshot, mapped, partner, "Gained", rotation, offset)
        lost = _make_changes(mapped_changes(reference, candidate), self.reference, self.reference_residues,
                             self.reference_partner, "Lost")
        return (*gained, *lost)

    def interactions(self):
        contacts = []
        reference = self.reference.contacts
        for key in sorted(reference.keys() | self.present.keys()):
            site, kind, other = key
            evaluated = self.coverage[site]
            if other:
                shared = self.target_membership.get(site, 0) & self.partner_membership.get(other, 0)
                evaluated = bin(shared).count("1")
            if not evaluated:
                continue
            sample = self.samples.get(key)
            pose = reference[key] if key in reference else sample.pose
            contacts.append(Contact(site, kind, key in reference, self.present[key], evaluated,
                                    pose, sample.residue if sample else None, other,
                                    sample.partner_residue if sample else None))
        return contacts


def _make_changes(contacts, snapshot, residue_map, partner_map, change, rotation=None, offset=None):
    if not contacts:
        return
    fragments, partner_fragments = {}, {}
    target_bonds = bonds_by_site(snapshot.protein)
    partner_bonds = bonds_by_site(snapshot.ligand) if partner_map else {}
    for site, kind, other in sorted(contacts):
        pose = contacts[site, kind, other]
        if site not in fragments:
            fragments[site] = aligned_fragment(snapshot.protein, residue_map[site].atoms, rotation, offset,
                                               bonds=target_bonds[site])
        if other and other not in partner_fragments:
            partner_fragments[other] = aligned_fragment(snapshot.ligand, partner_map[other].atoms, rotation, offset,
                                                        bonds=partner_bonds[other])
        if rotation is not None:
            pose = _aligned_pose(pose, rotation, offset)
        yield ContactChange(site, kind, change, pose, fragments[site], other, partner_fragments.get(other))

