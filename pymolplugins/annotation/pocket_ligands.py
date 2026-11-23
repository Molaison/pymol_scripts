"""Count ligand poses in the reference frame of a pocket comparison."""

from dataclasses import dataclass

from .pocket_analysis import _ligand_coords, partner_ca_coords
from .pocket_variants import VARIANT_COLORS


@dataclass(eq=False)
class LigandPose:
    model: object
    representative: str
    count: int = 0

    @property
    def ligand(self):
        return " + ".join(sorted({atom.resn for atom in self.model.atom}))


def ligand_pose_color(index):
    return "slate" if index == 0 else VARIANT_COLORS[(index - 1) % len(VARIANT_COLORS)]


def _pose_key(model, protein_partner=False):
    import numpy as np

    coords = (partner_ca_coords if protein_partner else _ligand_coords)(model)
    if not coords:
        return None, None
    keys = tuple(sorted(coords))
    if protein_partner:
        chains = tuple(sorted({chain for chain, _ in keys}))
        return chains, coords
    ranks = {key: index for index, key in enumerate(keys)}
    atom_ranks = [ranks[key] for key in coords]
    # The same ligand can have single or aromatic bond orders in different PDB exports.
    bonds = tuple(sorted({tuple(sorted(atom_ranks[i] for i in bond.index)) for bond in model.bond}))
    return (keys, bonds), np.array([coords[key] for key in keys])


class PartnerPoseCounts:
    def __init__(self, reference, name, *, protein_partner=False):
        self.reference = reference
        self.reference_name = name
        self.protein_partner = protein_partner
        self.tolerance = None
        self.poses = []
        self.total = 0
        self._groups = {}

    def update(self, analysis, tolerance):
        if tolerance != self.tolerance:
            self.tolerance = tolerance
            self.poses.clear()
            self._groups.clear()
            self.total = 0
            key, coords = _pose_key(self.reference, self.protein_partner)
            self._add(self.reference, self.reference_name, key, coords)

        for geometry in analysis.geometry[self.total:]:
            model = analysis.previews[geometry.entry_id].ligand
            if not model.atom:
                continue
            key, coords = _pose_key(model, self.protein_partner)
            pose = self._match(key, coords)
            if pose is None:
                pose = self._add(model, geometry.candidate, key, coords)
            pose.count += 1
        self.total = len(analysis.geometry)

    def _match(self, key, coords):
        reference = self.poses[0]
        limit = self.tolerance ** 2
        best, smallest = None, limit
        for center, pose in self._groups.get(key, ()):
            distance = self._distance_sq(coords, center)
            if distance <= limit:
                if pose is reference:
                    return pose
                if best is None or distance < smallest:
                    best, smallest = pose, distance
        return best

    def _distance_sq(self, coords, center):
        if not self.protein_partner:
            delta = coords - center
            return float((delta * delta).sum() / len(delta))
        shared = [site for site in center if site in coords]
        if len(shared) < 3:
            return float("inf")
        return sum(sum((a - b) ** 2 for a, b in zip(coords[site], center[site])) for site in shared) / len(shared)

    def _add(self, model, name, key, coords):
        pose = LigandPose(model, name)
        self.poses.append(pose)
        if key is not None:
            self._groups.setdefault(key, []).append((coords, pose))
        return pose

    def opacity(self, pose):
        return pose.count / self.total if self.total else 0.0
