"""Aggregate observed residue identities over one reference-defined pocket."""
from collections import Counter, defaultdict
from copy import copy
from dataclasses import dataclass

from chempy.models import Indexed


PROTEIN = "polymer.protein"
LIGAND = "organic and not solvent"
PRIMARY_HEAVY = "not elem H and alt ''+A"
BACKBONE = frozenset(("N", "C", "O", "OXT"))
VARIANT_COLORS = ("marine", "orange", "forest", "magenta", "cyan", "salmon",
                  "purple", "lime", "deepteal", "tv_yellow")


def site_key(atom):
    return atom.chain, atom.resi


def site_label(site):
    chain, number = site
    return f"{chain}:{number}" if chain else number


@dataclass(frozen=True)
class Residue:
    name: str
    atoms: list
    anchor: tuple


@dataclass(frozen=True)
class PocketVariant:
    site: tuple
    name: str
    count: int
    total: int
    missing: int
    model: object

    @property
    def opacity(self):
        return self.count / self.total


def protein_model(api, selection):
    return api.get_model(f"({selection}) and {PRIMARY_HEAVY}", state=1)


def residues(model):
    atoms_by_site = defaultdict(list)
    anchors = {}
    for index, atom in enumerate(model.atom):
        site = site_key(atom)
        atoms_by_site[site].append(index)
        if atom.name == "CA":
            if site in anchors:
                raise ValueError(f"Residue {site_label(site)} occurs more than once. Use unique chain and residue IDs.")
            anchors[site] = (atom.resn, tuple(atom.coord))
    return {site: Residue(name, atoms_by_site[site], coord)
            for site, (name, coord) in anchors.items()}


def reference_pocket(api, protein, ligand, radius, *, side_name="ligand"):
    if not api.count_atoms(f"({ligand}) and not elem H"):
        raise ValueError(f"The reference candidate has no {side_name} atoms in the selected interface.")
    pocket = api.get_model(
        f"byres ((({protein}) and not elem H) within {radius:g} of (({ligand}) and not elem H))",
        state=1,
    )
    return set(map(site_key, pocket.atom))


def fit_backbones(mobile, reference):
    import numpy as np

    shared = [site for site in reference if site in mobile]
    if len(shared) < 3:
        raise ValueError("Fewer than three Cα atoms share the reference's chain and residue IDs.")
    source = np.array([mobile[site].anchor for site in shared])
    target = np.array([reference[site].anchor for site in shared])
    src_center, dst_center = source.mean(axis=0), target.mean(axis=0)
    u, singular, vt = np.linalg.svd((source - src_center).T @ (target - dst_center))
    if singular[1] <= 1e-8:
        raise ValueError("Matching Cα atoms cannot define a rotation.")
    if np.linalg.det(u @ vt) < 0:
        u[:, -1] *= -1
    rotation = u @ vt
    return rotation, dst_center - src_center @ rotation


def aligned_fragment(model, indices, rotation=None, offset=None, bonds=None):
    result = Indexed()
    mapping = {}
    for index in indices:
        atom = model.atom[index]
        saved = copy(atom)
        saved.coord = list(atom.coord) if rotation is None else (atom.coord @ rotation + offset).tolist()
        mapping[index] = len(result.atom)
        result.atom.append(saved)
    for bond in model.bond if bonds is None else bonds:
        if all(index in mapping for index in bond.index):
            saved = copy(bond)
            saved.index = [mapping[index] for index in bond.index]
            result.bond.append(saved)
    return result


def side_chain(model, residue, bonds, rotation, offset):
    indices = (index for index in residue.atoms if model.atom[index].name not in BACKBONE)
    result = aligned_fragment(model, indices, rotation, offset, bonds=bonds)
    return result if result.bond else None


def bonds_by_site(model):
    bonds = defaultdict(list)
    for bond in model.bond:
        first, second = bond.index
        site = site_key(model.atom[first])
        if site == site_key(model.atom[second]):
            bonds[site].append(bond)
    return bonds


class PocketTally:
    def __init__(self, reference, sites, partner=None):
        self.reference = residues(reference)
        self.target_sites = tuple(site for site in self.reference if site in sites)
        if not self.target_sites:
            raise ValueError("No numbered target residues were found at the reference interface.")
        self.partner_sites = tuple(site for site in residues(partner) if site in sites) if partner is not None else ()
        self.sites = self.target_sites + self.partner_sites
        self.counts = defaultdict(Counter)
        self.fragments = {}
        self.total = 0

    def prepare(self, model, partner=None):
        mapped = residues(model)
        rotation, offset = fit_backbones(mapped, self.reference)
        observations = self._observations(model, mapped, self.target_sites, rotation, offset)
        if partner is not None:
            mobile_partner = residues(partner)
            if len(mobile_partner) < 3:
                raise ValueError("The selected partner chains need at least three Cα atoms.")
            observations.extend(self._observations(partner, mobile_partner, self.partner_sites, rotation, offset))
        return (mapped, rotation, offset), observations

    @staticmethod
    def _observations(model, mapped, sites, rotation, offset):
        observations = []
        bonds = bonds_by_site(model)
        for site in sites:
            residue = mapped.get(site)
            if residue is None:
                continue
            fragment = side_chain(model, residue, bonds[site], rotation, offset)
            observations.append((site, residue.name, fragment))
        return observations

    def add(self, observations):
        for site, name, fragment in observations:
            self.counts[site][name] += 1
            existing = self.fragments.get((site, name))
            if fragment is not None and (existing is None or len(fragment.atom) > len(existing.atom)):
                self.fragments[site, name] = fragment
        self.total += 1

    def variants(self):
        rows = []
        for site in self.sites:
            counts = self.counts[site]
            if len(counts) < 2:
                continue
            missing = self.total - sum(counts.values())
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
                rows.append(PocketVariant(site, name, count, self.total, missing, self.fragments.get((site, name))))
        return rows

