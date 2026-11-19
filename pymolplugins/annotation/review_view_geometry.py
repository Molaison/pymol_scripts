"""Local geometric estimates for the Structure Annotator viewing presets."""

from collections import defaultdict

import numpy as np


HYDROPHOBIC = frozenset("ALA CYS PHE ILE LEU MET VAL TRP TYR".split())
_RING_NAMES = {
    "PHE": ("CG CD1 CE1 CZ CE2 CD2",),
    "TYR": ("CG CD1 CE1 CZ CE2 CD2",),
    "HIS": ("CG ND1 CE1 NE2 CD2",),
    "TRP": ("CG CD1 NE1 CE2 CD2", "CD2 CE2 CZ2 CH2 CZ3 CE3"),
}


def residue_key(atom):
    return atom.segi, atom.chain, atom.resi, atom.resn


def aromatic_rings(model):
    """Yield fitted ring planes from protein templates or typed ligand bonds."""
    residues = defaultdict(dict)
    for atom in model.atom:
        if atom.resn in _RING_NAMES:
            residues[residue_key(atom)].setdefault(atom.name, atom)
    rings = []
    for key, atoms in residues.items():
        for names in map(str.split, _RING_NAMES[key[-1]]):
            if all(name in atoms for name in names):
                rings.append([atoms[name] for name in names])
    rings.extend([model.atom[i] for i in indices] for indices in _bonded_rings(model))
    for atoms in rings:
        coords = np.asarray([atom.coord for atom in atoms])
        center = coords.mean(axis=0)
        _, _, axes = np.linalg.svd(coords - center, full_matrices=False)
        if np.max(np.abs((coords - center) @ axes[-1])) <= 0.25:
            yield center, axes[-1], [atom.index for atom in atoms]


def _bonded_rings(model):
    neighbors = defaultdict(dict)
    for bond in model.bond:
        i, j = bond.index
        a, b = model.atom[i], model.atom[j]
        if a.resn in _RING_NAMES or residue_key(a) != residue_key(b):
            continue
        if a.symbol.upper() not in {"C", "N", "O", "S"} or b.symbol.upper() not in {"C", "N", "O", "S"}:
            continue
        neighbors[i][j] = neighbors[j][i] = bond.order
    found = set()
    for start in neighbors:
        stack = [(start,)]
        while stack:
            path = stack.pop()
            adjacent = neighbors[path[-1]]
            if len(path) in (5, 6) and start in adjacent:
                key = frozenset(path)
                orders = [neighbors[i][j] for i, j in zip(path, path[1:] + path[:1])]
                aromatic = all(order == 4 for order in orders)
                conjugated = (orders.count(2) == len(path) // 2 and set(orders) <= {1, 2}
                              and not any(a == b == 2 for a, b in zip(orders, orders[1:] + orders[:1])))
                if key not in found and (aromatic or conjugated):
                    found.add(key)
                    yield path
            if len(path) < 6:
                stack.extend(path + (i,) for i in adjacent if i > start and i not in path)


def stacking_pairs(first, second):
    """Yield π–π pairs with parallel or edge-to-face geometry."""
    second = list(second)
    for a, na, ia in first:
        for b, nb, ib in second:
            delta = b - a
            distance = np.linalg.norm(delta)
            if not 2.5 <= distance <= 5.5:
                continue
            angle = abs(np.dot(na, nb))
            offset = min(np.linalg.norm(delta - np.dot(delta, n) * n) for n in (na, nb))
            if offset <= 2.0 and (angle >= 0.866 or angle <= 0.5):
                yield a, b, ia, ib


def _lipid_slab(points):
    if len(points) < 8:
        return None
    center = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - center, full_matrices=False)
    best = None
    for normal in axes:
        depths = (points - center) @ normal
        split = 0.5 * sum(np.percentile(depths, (25, 75)))
        lo, hi = depths[depths < split], depths[depths >= split]
        if min(len(lo), len(hi)) < 4:
            continue
        width, spread = hi.mean() - lo.mean(), max(lo.std(), hi.std())
        if 24 <= width <= 50 and spread <= 4 and (best is None or spread < best[0]):
            best = spread, center + (hi.mean() + lo.mean()) / 2 * normal, normal, width / 2
    return best[1:] if best else None
