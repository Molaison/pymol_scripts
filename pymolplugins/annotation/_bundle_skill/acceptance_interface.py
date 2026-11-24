"""Atom-level interface details shared by interactive and portable review views."""

from collections import defaultdict, namedtuple
from functools import lru_cache

_PROBE_RADIUS = 1.4  # Å
_BURIED_AREA = 0.5  # Å²
_EXPOSED_AREA = 1.0  # Å²

_AtomSnapshot = namedtuple(
    "_AtomSnapshot", "model index segi chain resi alt q vdw coord")


def _primary_conformer(atoms):
    occupancies = defaultdict(lambda: defaultdict(list))
    for (model, _), atom in atoms:
        if atom.alt:
            occupancies[model, atom.segi, atom.chain, atom.resi][atom.alt].append(atom.q)
    chosen = {residue: min(alts, key=lambda alt: (-sum(alts[alt]) / len(alts[alt]), alt))
              for residue, alts in occupancies.items()}
    return [(key, atom) for key, atom in atoms
            if not atom.alt or atom.alt == chosen[key[0], atom.segi, atom.chain, atom.resi]]


def _pairs(api, first, second, state, cutoff, mode):
    return api.find_pairs(first, second, state1=state, state2=state, cutoff=cutoff, mode=mode, angle=45)


def _state_atoms(api, objects, state):
    atoms = []
    for name in objects:
        selection = f"%{name} and not hydro"
        metadata = []
        api.iterate_state(
            state, selection, "record((index, segi, chain, resi, alt, q, vdw))",
            space={"record": metadata.append},
        )
        coords = api.get_coords(selection, state)
        if coords is None and not metadata:
            continue
        if coords is None or len(metadata) != len(coords):
            raise RuntimeError(f"Atom metadata and coordinate counts differ for {name}")
        atoms.extend(_AtomSnapshot(name, *values, tuple(map(float, coord)))
                     for values, coord in zip(metadata, coords))
    return atoms


def polar_pairs(api, first, second, state, cutoff):
    return {pair for left, right in (("donor", "acceptor"), ("acceptor", "donor"))
            for pair in _pairs(api, f"({first}) and {left}", f"({second}) and {right}", state, cutoff, 1)}


def _satisfied_atoms(api, candidates, source, state, model_indices):
    water = "(solvent and elem O)"
    donors = f"({source}) and not hydro and (donor or {water})"
    acceptors = f"({source}) and not hydro and (acceptor or {water})"
    pairs = (
        (f"({candidates}) and donor", acceptors, 3.6, 1),
        (donors, f"({candidates}) and acceptor", 3.6, 1),
        (f"({candidates}) and acceptor and elem N+O+S", f"({source}) and metals", 2.8, 0),
    )
    satisfied = set()
    for first, second, cutoff, mode in pairs:
        for a, b in _pairs(api, first, second, state, cutoff, mode):
            if a != b and a in model_indices and b in model_indices:
                satisfied.update((a, b))
    return satisfied


def capture_assembly_polar(api, objects, state, focus, partners):
    """Snapshot atom radii, coordinates and polar contacts from the displayed state."""
    if not partners:
        return (), {}
    selection = " or ".join(f"%{name}" for name in objects)
    polar = f"({selection}) and ({focus}) and not hydro and not solvent and (donor or acceptor)"
    candidates = set(api.index(polar))
    if not candidates:
        return (), {}

    atoms = _primary_conformer([((atom.model, atom.index), atom)
                                for atom in _state_atoms(api, objects, state)])
    satisfied = _satisfied_atoms(api, polar, selection, state, {key for key, _ in atoms})
    solvent = set(api.index(f"({selection}) and solvent"))
    rows = [(key, tuple(atom.coord), atom.vdw) for key, atom in atoms if key not in solvent]
    candidates.intersection_update(key for key, _, _ in rows)
    candidates.difference_update(satisfied)

    opposing = defaultdict(set)
    selections = dict.fromkeys(side for pair in partners for side in pair)
    sides = {side: set(api.index(f"({selection}) and ({side})")) for side in selections}
    for first, second in partners:
        opposing[first].update(sides[second])
        opposing[second].update(sides[first])
    # Keep other chains and ligands in the environment when removing the selected partner(s).
    return rows, {index: opposing[side] - ids for side, ids in sides.items() for index in candidates & ids}


@lru_cache(maxsize=1)
def _probe_directions():
    import numpy as np

    steps = np.arange(512, dtype=float) + 0.5
    z = 1.0 - 2.0 * steps / len(steps)
    theta = np.pi * (3.0 - np.sqrt(5.0)) * steps
    r = np.sqrt(1.0 - z * z)
    return np.column_stack((r * np.cos(theta), r * np.sin(theta), z))


def find_buried_unsatisfied(snapshot):
    """Return source atom indices and positions for newly buried, unsatisfied sites."""
    import numpy as np

    rows, opposing = snapshot
    if not opposing:
        return {}
    ids = [index for index, _, _ in rows]
    centers = np.array([coord for _, coord, _ in rows])
    radii = np.array([vdw + _PROBE_RADIUS for _, _, vdw in rows])
    positions = {index: i for i, index in enumerate(ids)}
    directions = _probe_directions()
    buried = {}
    for index, partner_ids in opposing.items():
        i = positions[index]
        delta = centers - centers[i]
        near = np.flatnonzero(np.linalg.norm(delta, axis=1) < radii + radii[i])
        near = near[near != i]
        retained = [ids[j] not in partner_ids for j in near]
        if all(retained):
            continue
        samples = radii[i] * directions[:, None, :]
        blocked = ((samples - delta[near]) ** 2).sum(axis=2) < radii[near] ** 2
        area_per_sample = 4.0 * np.pi * radii[i] ** 2 / len(directions)
        bound_area = area_per_sample * np.count_nonzero(~blocked.any(axis=1))
        if bound_area > _BURIED_AREA:
            continue
        separated_area = area_per_sample * np.count_nonzero(~blocked[:, retained].any(axis=1))
        if separated_area >= _EXPOSED_AREA:
            buried[index] = tuple(centers[i])
    return buried


def atom_selection(keys):
    groups = defaultdict(set)
    for model, index in keys:
        groups[model].add(index)
    return " or ".join(f"(%{model} and index {'+'.join(map(str, sorted(ids)))})"
                       for model, ids in groups.items()) or "none"


def interface_atom_sticks(residues, endpoints="none", buried="none"):
    """Show side chains, the backbone at contact ends and whole buried-polar residues."""
    backbone = "name N+CA+C+O+OXT"
    sidechains = "not (polymer.protein and name N+C+O+OXT)"
    sites = f"byres (({endpoints}) and polymer.protein and ({backbone}))"
    return (f"((({residues}) and ({sidechains}))"
            f" or (({sites}) and ({backbone})) or (byres ({buried}))) and not hydro")


def show_interface_sticks(api, residues, endpoints="none", buried="none"):
    sticks = interface_atom_sticks(residues, endpoints, buried)
    details = interface_atom_sticks("none", endpoints, buried)
    if api.count_atoms(details):
        api.set("cartoon_side_chain_helper", 0, details)
    api.show("sticks", sticks)
    return sticks


def draw_atom_markers(api, prefix, kind, points, color, radius, *, opacity=0.78):
    from pymol.cgo import ALPHA, COLOR, SPHERE

    body = [ALPHA, opacity, COLOR, *color]
    for xyz in points:
        body.extend((SPHERE, *map(float, xyz), radius))
    if len(body) > 6:
        name = api.get_unused_name(f"{prefix}_{kind}")
        api.load_cgo(body, name, zoom=0)
        return name


def draw_buried_unsatisfied(api, prefix, points):
    return draw_atom_markers(api, prefix, "buried_unsatisfied_polar", points, (1.0, 0.52, 0.12), 0.36, opacity=1)


def assembly_details(api, first, second, focus, state):
    objects = api.get_object_list(f"({first}) or ({second})")
    if not objects:
        return {}
    return find_buried_unsatisfied(capture_assembly_polar(api, objects, state, focus, [(first, second)]))
