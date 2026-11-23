"""Compare two loaded protein models in the reference model's coordinate frame."""

from math import dist
from uuid import uuid4

import numpy as np

from pymolplugins.core.sequence import residue_to_one_letter


def _model(name):
    return "(%" + name + ")"


def _residue(atom):
    return atom.segi, atom.chain, atom.resi


def _index_selection(model, indices):
    return f"({_model(model)} and index {'+'.join(map(str, sorted(indices)))})"


def _fit(api, mobile, parent, mobile_fit, parent_fit, token):
    def alpha_carbons(name, selection):
        return f"({_model(name)} and polymer.protein and name CA and alt ''+A and ({selection}))"

    moving = alpha_carbons(mobile, mobile_fit)
    fixed = alpha_carbons(parent, parent_fit)
    if min(api.count_atoms(moving), api.count_atoms(fixed)) < 3:
        raise ValueError("Alignment needs at least three protein Cα atoms in each fit selection")
    moving_indices = {index for _, index in api.index(moving)}
    fixed_indices = {index for _, index in api.index(fixed)}
    moving_chain = alpha_carbons(mobile, f"bychain ({moving})")
    fixed_chain = alpha_carbons(parent, f"bychain ({fixed})")
    name = "pp_compare_map_" + token
    try:
        result = api.align(moving_chain, fixed_chain, cycles=0, transform=0, object=name,
                           mobile_state=1, target_state=1, quiet=1)
        if result[1] < 3:
            raise ValueError("Fewer than three Cα atoms could be matched. Check the fit chains.")
        columns = [dict(column) for column in api.get_raw_alignment(name, active_only=0)]
        indices = [(column[mobile], column[parent]) for column in columns
                   if mobile in column and parent in column]
        fit_indices = [(left, right) for left, right in indices
                       if left in moving_indices and right in fixed_indices]
        if len(fit_indices) < 3:
            raise ValueError("PyMOL returned fewer than three Cα correspondences")
        return indices, len(fit_indices), _fit_coordinates(api, mobile, parent, fit_indices)
    finally:
        api.delete(name)


def _fit_coordinates(api, mobile, parent, indices):
    # get_model/get_coords and load_coords use world coordinates, including TTT.
    # align(transform=1) can apply a second transform to an existing object TTT.
    mobile_atoms, parent_atoms = _atoms(api, mobile), _atoms(api, parent)
    moving = np.array([mobile_atoms[left].coord for left, _ in indices])
    fixed = np.array([parent_atoms[right].coord for _, right in indices])
    moving_center, fixed_center = moving.mean(axis=0), fixed.mean(axis=0)
    u, _, vt = np.linalg.svd((moving - moving_center).T @ (fixed - fixed_center))
    handedness = np.eye(3)
    handedness[2, 2] = np.linalg.det(u @ vt)
    rotation = u @ handedness @ vt
    aligned = (moving - moving_center) @ rotation + fixed_center
    coordinates = api.get_coords(_model(mobile), state=1)
    api.load_coords((coordinates - moving_center) @ rotation + fixed_center, _model(mobile), state=1)
    return float(np.sqrt(np.mean(np.sum((aligned - fixed) ** 2, axis=1))))


def _atoms(api, name):
    atoms = api.get_model(f"({_model(name)} and not elem H)", state=1).atom
    return {atom.index: atom for atom in atoms}


def _protein_residues(atoms):
    return {_residue(atom): (atom.resn, residue_to_one_letter(atom.resn))
            for atom in atoms.values() if atom.name == "CA" and atom.symbol == "C"
            and residue_to_one_letter(atom.resn) != "X"}


def _correspondence(indices, mobile_atoms, parent_atoms, mobile_residues, parent_residues):
    pairs = {_residue(mobile_atoms[left]): _residue(parent_atoms[right]) for left, right in indices}
    moving_fit_chains = {key[:2] for key in pairs}
    fixed_fit_chains = {key[:2] for key in pairs.values()}
    # Compare other chains by explicit residue IDs when the fit was restricted.
    pairs.update({key: key for key in mobile_residues.keys() & parent_residues.keys()
                  if key[:2] not in moving_fit_chains and key[:2] not in fixed_fit_chains})
    return pairs


def _difference(pairs, mobile_residues, parent_residues):
    substitutions = [(left, right) for left, right in pairs.items()
                     if left in mobile_residues and right in parent_residues
                     and mobile_residues[left][1] != parent_residues[right][1]]
    insertions = sorted(mobile_residues.keys() - pairs.keys())
    deletions = sorted(parent_residues.keys() - set(pairs.values()))
    return substitutions, insertions, deletions


def _changed_selection(name, atoms, changed):
    indices = {index for index, atom in atoms.items() if _residue(atom) in changed}
    return _index_selection(name, indices) if indices else ""


def _contacts(api, model, atoms, changed, canonical, cutoff):
    focus = _changed_selection(model, atoms, changed)
    if not focus:
        return {}
    sidechain = f"({focus} and (sidechain or (resn GLY and name CA)))"
    partners = f"({_model(model)} and not elem H and (polymer or organic))"
    found = {}
    for (_, left), (_, right) in api.find_pairs(sidechain, partners, state1=1, state2=1,
                                                cutoff=cutoff, mode=0):
        a, b = atoms[left], atoms[right]
        key_a, key_b = _residue(a), _residue(b)
        if key_a == key_b:
            continue
        pair = frozenset((canonical.get(key_a, (model, key_a)), canonical.get(key_b, (model, key_b))))
        length = dist(a.coord, b.coord)
        if pair not in found or length < found[pair][0]:
            found[pair] = (length, left, right)
    return found


def _draw_contacts(api, model, contacts, name, color):
    # Cap what is drawn so a large change does not obscure the structure.
    for _, left, right in sorted(contacts)[:12]:
        api.distance(name, _index_selection(model, [left]), _index_selection(model, [right]),
                     state=1, mode=0, label=0, reset=0, quiet=1)
    if contacts:
        api.set("dash_color", color, name)
        api.set("dash_width", 2, name)


def compare(api, mobile, parent, mode="overlay", mobile_fit="all", parent_fit="all", cutoff=4.0):
    """Fit the mobile model, style the pair, and report sequence/contact changes."""
    mobile, parent, mode = str(mobile).strip(), str(parent).strip(), str(mode).strip().lower()
    names = set(api.get_names_of_type("object:molecule"))
    if mobile == parent or mobile not in names or parent not in names:
        raise ValueError("Choose two different molecule objects: candidate, then reference")
    if mode not in {"fit", "overlay", "mutations", "contacts"}:
        raise ValueError("Comparison mode must be fit, overlay, mutations or contacts")
    cutoff = float(cutoff)
    if not 0 < cutoff < float("inf"):
        raise ValueError("Contact cutoff must be a positive distance in Å")
    token = uuid4().hex[:10]
    indices, paired_ca, rmsd = _fit(api, mobile, parent, mobile_fit, parent_fit, token)
    result = {"paired_ca": paired_ca, "rmsd": rmsd}
    if mode == "fit":
        return result

    for name, color, transparency in ((parent, "gray80", 0.35), (mobile, "teal", 0)):
        api.show_as("cartoon", _model(name))
        api.color(color, _model(name))
        api.set("cartoon_transparency", transparency, _model(name))
    api.cartoon("tube", _model(parent))
    api.set("cartoon_tube_radius", 0.35, parent, state=1)
    if mode == "overlay":
        return result

    mobile_atoms, parent_atoms = _atoms(api, mobile), _atoms(api, parent)
    mobile_residues, parent_residues = map(_protein_residues, (mobile_atoms, parent_atoms))
    pairs = _correspondence(indices, mobile_atoms, parent_atoms, mobile_residues, parent_residues)
    substitutions, insertions, deletions = _difference(pairs, mobile_residues, parent_residues)
    changed_mobile = set(insertions) | {left for left, _ in substitutions}
    changed_parent = set(deletions) | {right for _, right in substitutions}
    for name, atoms, changed, color in ((mobile, mobile_atoms, changed_mobile, "orange"),
                                        (parent, parent_atoms, changed_parent, "gray70")):
        selection = _changed_selection(name, atoms, changed)
        if selection:
            api.show("sticks", selection)
            api.color(color, selection)
    result.update(substitutions=[{"reference": right, "from": parent_residues[right][1],
                                  "candidate": left, "to": mobile_residues[left][1]}
                                 for left, right in substitutions],
                  insertions=insertions, deletions=deletions)
    if mode == "contacts":
        _compare_contacts(api, mobile, parent, mobile_atoms, parent_atoms, changed_mobile,
                          changed_parent, pairs, cutoff, token, result)
    return result


def _compare_contacts(api, mobile, parent, mobile_atoms, parent_atoms, changed_mobile,
                      changed_parent, pairs, cutoff, token, result):
    # Include the corresponding unchanged site when comparing contact sets.
    changed_mobile.update(left for left, right in pairs.items() if right in changed_parent)
    changed_parent.update(right for left, right in pairs.items() if left in changed_mobile)
    reference_keys = {_residue(atom) for atom in parent_atoms.values()}
    candidate_keys = {_residue(atom) for atom in mobile_atoms.values()}
    protein_keys = _protein_residues(mobile_atoms).keys() | _protein_residues(parent_atoms).keys()
    canonical = {key: key for key in candidate_keys & reference_keys if key not in protein_keys}
    canonical.update(pairs)
    current = _contacts(api, mobile, mobile_atoms, changed_mobile, canonical, cutoff)
    previous = _contacts(api, parent, parent_atoms, changed_parent,
                         {key: key for key in reference_keys}, cutoff)
    gained = sorted(current[key] for key in current.keys() - previous.keys())
    lost = sorted(previous[key] for key in previous.keys() - current.keys())
    gained_name, lost_name = "compare_gained_" + token, "compare_lost_" + token
    try:
        _draw_contacts(api, mobile, gained, gained_name, "cyan")
        _draw_contacts(api, parent, lost, lost_name, "orange")
    except Exception:
        api.delete(gained_name)
        api.delete(lost_name)
        raise
    result.update(gained_contacts=len(gained), lost_contacts=len(lost))
