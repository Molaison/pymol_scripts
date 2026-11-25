"""Residue-level hydropathy and CA contact metrics (standard library only)."""

import math
import numbers

KYTE_DOOLITTLE = {
    "ALA": 1.8,
    "ARG": -4.5,
    "ASN": -3.5,
    "ASP": -3.5,
    "CYS": 2.5,
    "GLN": -3.5,
    "GLU": -3.5,
    "GLY": -0.4,
    "HIS": -3.2,
    "ILE": 4.5,
    "LEU": 3.8,
    "LYS": -3.9,
    "MET": 1.9,
    "PHE": 2.8,
    "PRO": -1.6,
    "SER": -0.8,
    "THR": -0.7,
    "TRP": -0.9,
    "TYR": -1.3,
    "VAL": 4.2,
}

_ALIASES = {
    "ASH": "ASP",
    "CYM": "CYS",
    "CYX": "CYS",
    "FME": "MET",
    "GLH": "GLU",
    "HID": "HIS",
    "HIE": "HIS",
    "HIP": "HIS",
    "HSD": "HIS",
    "HSE": "HIS",
    "HSP": "HIS",
    "KCX": "LYS",
    "LYN": "LYS",
    "MLY": "LYS",
    "MSE": "MET",
    "PCA": "GLU",
    "PTR": "TYR",
    "SEP": "SER",
    "TPO": "THR",
}


def hydropathy(resn):
    """Return the Kyte-Doolittle hydropathy of a residue name.

    Args:
        resn: Three-letter residue name; aliases such as MSE map to their parent.

    Returns:
        Float hydropathy value, or None for an unknown name.
    """
    if not isinstance(resn, str):
        return None
    key = resn.strip().upper()
    key = _ALIASES.get(key, key)
    return KYTE_DOOLITTLE.get(key)


def contact_counts(residues, cutoff=8.0, gap=2):
    """Count CA-CA contacts with a spatial cell grid.

    Args:
        residues: list of dicts with object, segi, chain, resi, sequence_index, ca.
        cutoff: Positive finite contact distance in Angstrom.
        gap: Non-negative sequence-index gap excluded within one chain/segment.

    Returns:
        (counts, pairs): per-residue contact counts (None when CA is missing) and
        contacts as dicts {i, j, distance} sorted by (i, j).
    """
    cutoff = _positive_number(cutoff, "cutoff")
    gap = _nonnegative_int(gap, "gap")
    points = [_ca_point(residue.get("ca")) for residue in residues]

    counts = [None if point is None else 0 for point in points]
    grid = {}
    pairs = []
    for j, point in enumerate(points):
        if point is None:
            continue
        cell = tuple(math.floor(value / cutoff) for value in point)
        for neighbor in _neighbors(cell):
            for i in grid.get(neighbor, ()):
                if not _eligible(residues[i], residues[j], gap):
                    continue
                distance = math.dist(point, points[i])
                if distance <= cutoff:
                    pairs.append({"i": i, "j": j, "distance": distance})
                    counts[i] += 1
                    counts[j] += 1
        grid.setdefault(cell, []).append(j)

    pairs.sort(key=lambda pair: (pair["i"], pair["j"]))
    return counts, pairs


def _neighbors(cell):
    x, y, z = cell
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                yield (x + dx, y + dy, z + dz)


def _eligible(first, second, gap):
    if first.get("object") != second.get("object"):
        return False
    if first.get("segi") != second.get("segi") or first.get("chain") != second.get("chain"):
        return True
    return abs(first["sequence_index"] - second["sequence_index"]) > gap


def _ca_point(ca):
    if ca is None:
        return None
    try:
        point = tuple(ca)
    except TypeError as exc:
        raise ValueError("ca must be None or three finite numbers") from exc
    if len(point) != 3:
        raise ValueError("ca must be None or three finite numbers")
    for value in point:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError("ca must be None or three finite numbers")
        if not math.isfinite(value):
            raise ValueError("ca must contain finite coordinates")
    return tuple(float(value) for value in point)


def _positive_number(value, name):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def _nonnegative_int(value, name):
    if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)
