"""Membrane placement, residue landmarks, and the display result for a review view."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from subprocess import SubprocessError
from typing import TYPE_CHECKING
from xml.etree.ElementTree import ParseError

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class MembranePlacement:
    center: np.ndarray
    normal: np.ndarray
    half_width: float
    source: str
    segments: tuple = ()
    helical: bool = False


@dataclass(frozen=True)
class MembraneNotice:
    text: str
    tooltip: str = ""
    placed: bool = False


_HEAD_ATOMS = {
    "ARG": {"CZ", "NH1", "NH2"}, "LYS": {"NZ"},
    "TYR": {"CZ", "OH"}, "TRP": {"CH2", "NE1"},
}


def estimate_membrane(calpha, phosphorus, pdb, chains):
    import numpy as np

    from .review_membrane_tmdet import tmdet_slab
    from .review_view_geometry import _lipid_slab

    leaflets = _lipid_slab(np.asarray(phosphorus))
    if leaflets is not None:
        center, normal, half_width = leaflets
        segments = _slab_crossings(calpha, center, normal, half_width)
        return MembranePlacement(center, normal, half_width, "Modeled lipid phosphorus", segments)
    if not pdb:
        return MembraneNotice("Membrane position unavailable · could not stage this protein for TmDet.")
    try:
        placement = tmdet_slab(pdb)
    except (OSError, RuntimeError, ValueError, ParseError, SubprocessError) as error:
        reason = str(error).strip() or type(error).__name__
        return MembraneNotice(f"Membrane position unavailable · {reason.splitlines()[0]}", reason)
    if placement is None:
        return MembraneNotice("TmDet 4.1.9 · no transmembrane placement found.")
    segments = tuple((chains.get(chain, chain), start, end) for chain, start, end in placement.segments)
    return MembranePlacement(placement.center, placement.normal, placement.half_width,
                             placement.source, segments, placement.helical)


def _slab_crossings(calpha, center, normal, half_width):
    """Find continuous Cα paths that traverse both observed leaflets."""
    import numpy as np

    chains = defaultdict(dict)
    for atom in calpha:
        chains[atom.segi, atom.chain].setdefault(atom.resi, atom)
    spans = []
    for (_, chain), ca in chains.items():
        path = []
        edge = None
        for atom in ca.values():
            if path and np.linalg.norm(np.asarray(atom.coord) - path[-1].coord) > 4.6:
                path.clear()
                edge = None
            depth = float((np.asarray(atom.coord) - center) @ normal)
            side = 1 if depth >= half_width - 2.0 else -1 if depth <= -half_width + 2.0 else 0
            if side:
                if edge is not None and side != edge and len(path) >= 4:
                    spans.append((chain, path[0].resi, atom.resi))
                path = [atom]
                edge = side
            elif path:
                path.append(atom)
    return tuple(spans)


def _landmarks(calpha, marker_atoms, placement):
    import numpy as np

    keys = lambda atom: (atom.segi, atom.chain, atom.resi)
    distinct = {keys(atom): atom for atom in calpha}
    heads = defaultdict(list)
    for atom in marker_atoms:
        if atom.name in _HEAD_ATOMS.get(atom.resn, ()):
            heads[keys(atom)].append(atom.coord)
    aromatic = []
    basic = {-1: [], 1: []}
    for key, coordinates in heads.items():
        atom = distinct.get(key)
        if atom is None:
            continue
        depth = float((np.mean(coordinates, axis=0) - placement.center) @ placement.normal)
        offset = abs(depth) - placement.half_width
        if atom.resn in {"TYR", "TRP"} and abs(offset) <= 4.0:
            aromatic.append(atom)
        elif atom.resn in {"ARG", "LYS"} and -2.0 <= offset <= 8.0:
            basic[1 if depth > 0 else -1].append(atom)
    major, minor = sorted(basic, key=lambda side: len(basic[side]), reverse=True)
    declared = placement.helical and len(basic[major]) >= max(3, 2 * len(basic[minor]))
    leaflet = major if declared else None
    return aromatic, basic[major] if declared else [*basic[-1], *basic[1]], leaflet


def _describe(placement, aromatic, basic, leaflet):
    ranges = defaultdict(list)
    for chain, start, end in placement.segments:
        ranges[chain or "(blank)"].append(f"{start}–{end}")
    lines = [f"{placement.source} · {2 * placement.half_width:.1f} Å membrane thickness"]
    lines += [f"TM chain {chain}: {', '.join(spans)}" for chain, spans in ranges.items()]
    if not ranges:
        lines.append("No continuous transmembrane segment assigned.")
    sidedness = ("cyan · cytosolic side inferred (positive-inside rule)" if leaflet is not None
                 else "cyan · cytosolic side unassigned")
    lines.append(f"Tyr/Trp {len(aromatic)} (orange, interfaces) · Arg/Lys {len(basic)} ({sidedness})")
    return MembraneNotice("\n".join(lines), "\n".join(lines), True)


def draw_membrane(api, source, protein, state, calpha, marker_atoms, placement, draw_edges):
    if isinstance(placement, MembraneNotice):
        return placement
    from pymol import util
    import numpy as np
    from pymolplugins.geometry.commands import membrane_corners
    from .review_view_geometry import HYDROPHOBIC

    surface = api.get_unused_name(f"{source}_membrane_surface")
    api.create(surface, protein, source_state=state, target_state=1, zoom=0)
    api.hide("everything", f"%{surface}")
    api.color("palegreen", f"%{surface}")
    api.color("wheat", f"%{surface} and resn " + "+".join(sorted(HYDROPHOBIC)))
    api.set("transparency", 0.68, surface)
    api.show("surface", f"%{surface}")
    api.show("cartoon", protein)
    aromatic, basic, leaflet = _landmarks(calpha, marker_atoms, placement)
    for atoms, kind, color in ((aromatic, "interface_aromatics", "orange"), (basic, "basic_landmarks", "cyan")):
        if not atoms:
            continue
        indices = "+".join(str(atom.index) for atom in atoms)
        selection = f"(byres (%{source} and index {indices})) and not (name N+C+O+OXT or elem H)"
        name = api.get_unused_name(f"{source}_{kind}")
        api.create(name, selection, source_state=state, target_state=1, zoom=0)
        api.hide("everything", f"%{name}")
        api.show("sticks", f"%{name}")
        api.color(color, f"%{name} and elem C")
        util.cnc(f"%{name}", _self=api)
    center, normal, half_width = placement.center, placement.normal, placement.half_width
    offsets = np.array([atom.coord for atom in calpha]) - center
    depths = offsets @ normal
    inside = offsets[np.abs(depths) <= half_width]
    planar = inside - ((inside @ normal)[:, None] * normal)
    size = max(26.0, 2 * np.linalg.norm(planar, axis=1).max(initial=0) + 8.0)
    for side in (-1, 1):
        corners = membrane_corners(tuple(center + side * half_width * normal), tuple(normal), size, size)
        color = (0.17, 0.80, 0.91) if leaflet == side else (0.50, 0.66, 0.70)
        draw_edges(api, source, "membrane_planes", zip(corners, corners[1:] + corners[:1]), color, 0.14, 2.2)
    return _describe(placement, aromatic, basic, leaflet)
