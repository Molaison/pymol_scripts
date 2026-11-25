"""Whole-residue SASA in the source object's heavy-atom environment."""

from pymol import cmd

from collections import defaultdict
import math

from ._coretosurf import Residue
from .metrics import contact_counts, hydropathy


_cache = {}


def quoted(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def residue_key(atom):
    return atom.segi, atom.chain, atom.resi, atom.resn


def _state(obj, state):
    requested = int(state)
    if requested < -1:
        raise ValueError("state must be -1, 0 (current), or a positive state")
    resolved = cmd.get_object_state(obj) if requested in (-1, 0) else requested
    if not 1 <= resolved <= cmd.count_states(obj):
        raise ValueError("State %d is unavailable in %s" % (resolved, obj))
    return resolved


def _signature(atoms, protein_indices):
    values = []
    for atom in atoms:
        if not all(math.isfinite(x) for x in (*atom.coord, atom.vdw)):
            raise ValueError("Non-finite atom coordinates or radius")
        values.append((atom.index, residue_key(atom), atom.name, atom.symbol,
                       atom.alt, atom.q, atom.vdw, tuple(atom.coord),
                       atom.index in protein_indices))
    return tuple(values)


def _conformers(atoms):
    occupancies = defaultdict(lambda: defaultdict(float))
    for atom in atoms:
        if atom.alt:
            occupancies[residue_key(atom)][atom.alt] += atom.q
    return {key: max(values, key=lambda alt: (values[alt], alt == "A", alt))
            for key, values in occupancies.items()}


def _keep(atom, conformers):
    return not atom.alt or atom.alt == conformers.get(residue_key(atom))


def _sasa(obj, state, conformers, protein_keys):
    temporary = cmd.get_unused_name("_pp_residue_sasa")
    dropped = cmd.get_unused_name("_pp_residue_alt")
    try:
        cmd.create(temporary, "model %s and not solvent and not hydro" % quoted(obj),
                   source_state=state, target_state=1, zoom=0)
        unwanted = [a.index for a in cmd.get_model(temporary, 1).atom
                    if not _keep(a, conformers)]
        if unwanted:
            cmd.select_list(dropped, temporary, unwanted, mode="index")
            cmd.remove("?" + dropped)
        cmd.set("dot_solvent", 1, temporary)
        cmd.set("dot_density", 3, temporary)
        cmd.set("solvent_radius", 1.4, temporary)
        cmd.get_area(temporary, state=1, load_b=1)
        sums = defaultdict(float)
        for atom in cmd.get_model(temporary, 1).atom:
            key = residue_key(atom)
            if key in protein_keys:
                sums[key] += atom.b
        return sums
    finally:
        cmd.delete(temporary)
        cmd.delete(dropped)


def _object_residues(obj, state, force):
    selection = "model %s and not solvent and not hydro" % quoted(obj)
    atoms = cmd.get_model(selection, state).atom
    protein = cmd.get_model(selection + " and polymer.protein", state).atom
    signature = _signature(atoms, {a.index for a in protein})
    key = obj, state
    if not force and key in _cache and _cache[key][0] == signature:
        return _cache[key][1]
    conformers = _conformers(atoms)
    sums = _sasa(obj, state, conformers, {residue_key(a) for a in protein})
    grouped = {}
    for atom in protein:
        grouped.setdefault(residue_key(atom), []).append(atom)
    pdb_numbers = defaultdict(set)
    for segi, chain, resi, resn in grouped:
        pdb_numbers[chain, resi].add((segi, resn))
    records, positions = [], defaultdict(int)
    for reskey, resatoms in grouped.items():
        segi, chain, resi, resn = reskey
        position = positions[segi, chain]
        positions[segi, chain] += 1
        ca = next((a.coord for a in resatoms if a.name == "CA" and _keep(a, conformers)), None)
        residue = Residue(segi, chain, resi, resn, sums[reskey])
        records.append(dict(object=obj, segi=segi, chain=chain, resi=resi, resn=resn,
                            sasa=residue.sasa, rsa=residue.rel, ca=ca,
                            indices=tuple(a.index for a in resatoms),
                            rosetta_ambiguous=len(pdb_numbers[chain, resi]) > 1,
                            sequence_index=position, hydropathy=hydropathy(resn)))
    _cache[key] = signature, records
    return records


def analyze(selection, state=-1, force=False, cutoff=8.0, gap=2):
    """Return protein residues in selection, retaining full-object occlusion.

    Args:
        selection: PyMOL selection; partial residues are expanded by residue.
        state: Positive state, or 0/-1 for each object's current state.
        force: Recalculate cached SASA.
        cutoff: C-alpha contact distance in angstroms.
        gap: Exclude this many observed sequence neighbors on the same chain.
    Returns:
        JSON-compatible report with residues, contact pairs and resolved states.
    """
    target = "byres ((%s) and polymer.protein)" % selection
    objects = cmd.get_object_list(target)
    if not objects:
        raise ValueError("Selection contains no protein residues: " + selection)
    # Validate contact parameters before running the SASA calculation.
    contact_counts([], cutoff, gap)
    records, states = [], {}
    for obj in objects:
        resolved = _state(obj, state)
        states[obj] = resolved
        selected = {residue_key(a) for a in cmd.get_model(
            "(%s) and model %s" % (target, quoted(obj)), resolved).atom}
        for record in _object_residues(obj, resolved, force):
            if (record["segi"], record["chain"], record["resi"], record["resn"]) in selected:
                records.append(dict(record))
    counts, pairs = contact_counts(records, cutoff, gap)
    for record, count in zip(records, counts):
        record["contacts"] = count
    warnings = []
    unknown = sum(r["rsa"] is None for r in records)
    missing = sum(r["contacts"] is None for r in records)
    if unknown:
        warnings.append("%d residues have no maximum-ASA reference; RSA is unavailable." % unknown)
    if missing:
        warnings.append("%d residues have no C-alpha; contact counts are unavailable." % missing)
    return dict(selection=selection, states=states, residues=records, pairs=pairs,
                contact_cutoff=float(cutoff), sequence_gap=int(gap), warnings=warnings,
                sasa=dict(probe_radius=1.4, dot_density=3, environment="source object heavy atoms"))


def classify(report, core, boundary, unit):
    counts = dict(core=0, boundary=0, surface=0, unclassified=0)
    field = "rsa" if unit == "rel" else "sasa"
    for record in report["residues"]:
        value = record[field]
        layer = ("unclassified" if value is None else "core" if value < core else
                 "boundary" if value < boundary else "surface")
        record["layer"] = layer
        counts[layer] += 1
    report.update(counts=counts, core_cutoff=core, boundary_cutoff=boundary, unit=unit)
    return report


def clear_cache():
    _cache.clear()
