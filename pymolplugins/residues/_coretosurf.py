"""Pure algorithms adapted from CoreToSurf at 58dd4a5dc0d6cbe9b2efa6d811948734c2cfabae.

Copyright (c) 2026, Ajasja Ljubetič. BSD-3-Clause.
See LICENSE.CoreToSurf for the original license and provenance.
Changes: no PyMOL dependency; unknown RSA remains unavailable.
"""

# Theoretical maximum ASA per residue (Å²), Tien et al. 2013, PLoS ONE 8:e80635.
MAX_ASA = {
    "ALA": 129.0, "ARG": 274.0, "ASN": 195.0, "ASP": 193.0, "CYS": 167.0,
    "GLN": 225.0, "GLU": 223.0, "GLY": 104.0, "HIS": 224.0, "ILE": 197.0,
    "LEU": 201.0, "LYS": 236.0, "MET": 224.0, "PHE": 240.0, "PRO": 159.0,
    "SER": 155.0, "THR": 172.0, "TRP": 285.0, "TYR": 263.0, "VAL": 174.0,
}
# Common non-standard / force-field residue names mapped to their parent.
ALIASES = {
    "MSE": "MET", "HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS",
    "HSE": "HIS", "HSP": "HIS", "CYX": "CYS", "CYM": "CYS", "ASH": "ASP",
    "GLH": "GLU", "LYN": "LYS", "SEP": "SER", "TPO": "THR", "PTR": "TYR",
}

LAYERS = ("core", "boundary", "surface")
COLORS = {"core": "orange", "boundary": "green", "surface": "blue"}

# Default cutoffs per unit: (core, boundary).
# CoreToSurf defaults; whole-residue PyMOL SASA is not Rosetta LayerSelector.
DEFAULT_CUTOFFS = {"abs": (20.0, 40.0), "rel": (20.0, 50.0)}

class Residue(object):
    __slots__ = ("segi", "chain", "resi", "resn", "sasa", "rel")

    def __init__(self, segi, chain, resi, resn, sasa):
        self.segi, self.chain, self.resi, self.resn = segi, chain, resi, resn
        self.sasa = sasa
        ref = MAX_ASA.get(ALIASES.get(resn, resn))
        self.rel = None if ref is None else 100.0 * sasa / ref

    def value(self, unit):
        return self.rel if unit == "rel" else self.sasa


def _split_resi(resi):
    """'52' -> (52, ''), '52A' -> (52, 'A'), '-3' -> (-3, ''). None if odd."""
    i = len(resi)
    while i > 0 and not resi[i - 1].isdigit():
        i -= 1
    try:
        return int(resi[:i]), resi[i:]
    except ValueError:
        return None


def _runs(numbers):
    """Sorted unique ints -> list of (start, end) runs of consecutive ints."""
    runs = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return runs


def _group_by_chain(residues):
    """{chain: (sorted plain resi ints, [resi with insertion code / odd])}.

    Chains keep their order of first appearance. Segment ids are ignored.
    """
    chains = {}
    for r in residues:
        plain, other = chains.setdefault(r.chain, (set(), []))
        split = _split_resi(r.resi)
        if split is not None and split[1] == "":
            plain.add(split[0])
        elif r.resi not in other:
            other.append(r.resi)
    return {c: (sorted(p), o) for c, (p, o) in chains.items()}


def format_rosetta(residues):
    """Rosetta ResidueIndexSelector PDB numbering: 1A-5A,9A,3B.

    Returns (string, skipped) where skipped lists residues that cannot be
    expressed (insertion codes, missing or multi-letter chain ids).
    """
    items, skipped = [], []
    for chain, (plain, other) in _group_by_chain(residues).items():
        label = "chain %s" % chain if chain.strip() else "no chain"
        if len(chain) != 1 or not chain.isascii() or not chain.isalpha():
            skipped.extend("%s (%s)" % (n, label) for n in plain)
            skipped.extend("%s (%s)" % (x, label) for x in other)
            continue
        # Negative numbers would be ambiguous with the range dash.
        skipped.extend("%d (%s)" % (n, label) for n in plain if n < 0)
        for a, b in _runs([n for n in plain if n >= 0]):
            if a == b:
                items.append("%d%s" % (a, chain))
            else:
                items.append("%d%s-%d%s" % (a, chain, b, chain))
        skipped.extend("%s (%s)" % (x, label) for x in other)
    return ",".join(items), skipped
