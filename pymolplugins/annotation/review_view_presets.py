"""Candidate-local viewing recipes used by Structure Annotator Step commands."""

import csv
from io import StringIO
from itertools import combinations
from functools import partial
from string import ascii_letters, digits
from subprocess import SubprocessError
from uuid import uuid4
from weakref import WeakKeyDictionary
from xml.etree.ElementTree import ParseError

from .command_templates import LIPID_SELECTION, _chain_selection


REVIEW_VIEW_COMMANDS = frozenset({"sa_view_cartoon", "sa_view_interface", "sa_view_membrane", "sa_view_mass"})
QUICK_VIEWS = {"cartoon": "Cartoon", "interface": "Interface", "membrane": "Membrane", "mass": "MASS"}
_BACKBONE = "name N+C+O+OXT"
_OVERLAYS = WeakKeyDictionary()


def encode_review_command(name, *values):
    buffer = StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(values)
    return name + " " + buffer.getvalue().rstrip("\n")


def quick_view_command(view, source, radius="4", mass_chain=None):
    chain = "auto" if mass_chain is None else "chain:" + mass_chain
    args = {"interface": ("auto", radius, "nearby", "auto", "auto"), "mass": (chain, "auto")}
    return encode_review_command("sa_view_" + view, source, *args.get(view, ()))


def _chain_arg(source, value, api):
    if value == "auto":
        return None
    if not value.startswith("chain:"):
        raise ValueError("Chain arguments use chain:<ID> or auto")
    return _chain_selection("object", source, value[6:], api).replace("{object}", source)


def _source_object(api, source):
    if source not in api.get_names("objects") or any(char in source for char in '"\\\r\n;{}'):
        raise ValueError("Choose a loaded candidate before using this preset")
    if api.get_type(source) == "object:group":
        overlays = set().union(*_OVERLAYS.get(api, {}).values())
        molecules = [name for name in api.get_object_list(f"%{source}")
                     if name not in overlays and api.get_type(name) == "object:molecule"]
        if len(molecules) == 1:
            source = molecules[0]
    if api.get_type(source) != "object:molecule":
        raise ValueError("This preset needs one molecular object within the candidate")
    return source


def parse_review_command(line, api):
    """Resolve and validate a local recipe against the active candidate."""
    name, raw = line.split(None, 1)
    source, *args = next(csv.reader([raw], strict=True, skipinitialspace=True))
    if name == "sa_view_cartoon" and not args and source in api.get_names("objects"):
        try:
            source = _source_object(api, source)
        except ValueError:
            if api.get_type(source) != "object:group":
                raise
        return _show_cartoon, source, ()
    source = _source_object(api, source)
    protein = f"%{source} and polymer.protein"
    if not api.count_atoms(protein):
        raise ValueError("This preset requires protein atoms")
    if name == "sa_view_membrane" and not args:
        return _show_membrane, source, (protein,)
    if name == "sa_view_mass" and len(args) == 2:
        binder = _chain_arg(source, args[0], api)
        target = _chain_arg(source, args[1], api)
        if binder is None and target is None:
            chains = tuple(_chain_arg(source, "chain:" + chain, api) for chain in api.get_chains(protein))
            return _show_mass, source, (chains or (protein,),)
        if binder is None:
            raise ValueError("Choose the binder chain")
        if binder is not None and binder == target:
            raise ValueError("Binder and target must be different chains")
        return _show_mass, source, (tuple(filter(None, (binder or protein, target))),)
    if name == "sa_view_interface" and len(args) == 5:
        mode, radius, scope, target_id, binder_id = args
        if mode not in {"auto", "protein", "ligand"} or radius not in {"4", "6"} or scope not in {"nearby", "interacting"}:
            raise ValueError("Choose protein or ligand, 4 or 6 Å, and nearby or interacting sidechains")
        target = _chain_arg(source, target_id, api)
        binder = _chain_arg(source, binder_id, api)
        ligand = f"%{source} and organic and not ({LIPID_SELECTION})"
        has_ligand = api.count_atoms(ligand)
        if mode == "ligand" or (mode == "auto" and has_ligand and binder is None):
            if not has_ligand:
                raise ValueError("No organic ligand found in this structure")
            partners = [(target or protein, ligand)]
        else:
            chains = api.get_chains(protein)
            if len(chains) < 2 or (target is not None and target == binder):
                raise ValueError("Choose two different protein chains")
            if target is not None and binder is not None:
                partners = [(target, binder)]
            elif target or binder:
                chain = target or binder
                partners = [(chain, f"({protein}) and not ({chain})")]
            else:
                partners = list(combinations((_chain_arg(source, "chain:" + value, api) for value in chains), 2))
            ligand = None
        return _draw_interface, source, (protein, partners, ligand, float(radius), scope)
    raise ValueError("Invalid arguments for " + name)


def execute_review_command(line, api):
    from .bundle_view import CandidateCavity, draw_candidate_cavity, prepare_candidate_cavity

    source, action, compute, draw = prepare_review_command(line, api)
    followup = draw_review_view(api, source, action, draw, compute() if compute else None)
    if isinstance(followup, CandidateCavity):
        try:
            compute, draw = prepare_candidate_cavity(api, followup)
            rendered = draw_review_view(api, source, draw_candidate_cavity, draw, compute())
            print(rendered.status)
        except Exception as error:
            print(f"{followup.title} skipped · {error}")


def prepare_review_command(line, api):
    """Capture PyMOL data on the UI thread and return separate compute and draw calls."""
    action, source, args = parse_review_command(line, api)
    if action is _draw_interface:
        snapshots, scene = _capture_interface(api, source, *args)
        return source, action, partial(_compute_interface, snapshots), partial(_draw_interface, api, source, *scene)
    if action is _show_membrane:
        state, calpha, phosphorus, pdb = _capture_membrane(api, source, *args)
        return source, action, partial(_estimate_membrane, calpha, phosphorus, pdb), partial(action, api, source, *args, state, calpha)
    return source, action, None, lambda _: action(api, source, *args)


def draw_review_view(api, source, action, draw, data, *, exclusive=False):
    existing = set(api.get_names("objects"))
    overlays = _OVERLAYS.setdefault(api, {})
    key = source, action
    try:
        result = draw(data)
    except Exception:
        for name in set(api.get_names("objects")) - existing:
            api.delete(name)
        raise
    old_overlays = set().union(*(names for (obj, recipe), names in overlays.items()
                                 if obj == source and (exclusive or recipe is action)))
    for name in old_overlays & existing:
        api.delete(name)
    current = set(api.get_names("objects"))
    for old in list(overlays):
        if old[0] not in current or not (overlays[old] & current):
            del overlays[old]
    if current - existing:
        overlays[key] = current - existing
    return result


def _indices(source, ids):
    return f"%{source} and index " + "+".join(str(i) for i in sorted(set(ids)))


def _capture_interactions(api, first, second, state):
    from pymolplugins.interactions.contact_queries import find_pairs, hydrogen_bond_specs, salt_bridge_specs

    models = [api.get_model(selection, state) for selection in (first, second)]
    typed = {}
    for kind, specs in (("salt_bridges", salt_bridge_specs(first, second, 4.0)),
                        ("polar_contacts", hydrogen_bond_specs(first, second, 3.6))):
        typed[kind] = {tuple(sorted((a[1], b[1]))) for sel1, sel2, cutoff, mode, *_ in specs
                       for a, b in find_pairs(api, sel1, sel2, cutoff, mode, state)}
    typed["polar_contacts"].difference_update(typed["salt_bridges"])
    return typed, models


def _cylinders(api, source, kind, pairs, color, radius, dash):
    import numpy as np
    from pymol.cgo import CYLINDER

    body = []
    for start, end in pairs:
        start, end = np.asarray(start), np.asarray(end)
        delta = end - start
        count = max(1, round(np.linalg.norm(delta) / dash)) if dash else 1
        for i in range(count):
            a = start + delta * (i / count)
            b = start + delta * ((i + (0.53 if dash else 1)) / count)
            body.extend((CYLINDER, *a, *b, radius, *color, *color))
    if body:
        api.load_cgo([float(value) for value in body], api.get_unused_name(f"{source}_{kind}"))


def _dots(api, source, kind, points, color, radius):
    from pymol.cgo import ALPHA, COLOR, SPHERE

    body = [ALPHA, 0.78, COLOR, *color]
    for xyz in points:
        body.extend((SPHERE, *map(float, xyz), radius))
    if len(body) > 6:
        api.load_cgo(body, api.get_unused_name(f"{source}_{kind}"))


def _capture_interface(api, source, protein, partners, ligand, radius, scope):
    state = api.get_object_state(source)
    proximity, snapshots = [], []
    for first, second in partners:
        left = f"(({first}) and not elem H) within {radius:g} of (({second}) and not elem H)"
        right = f"(({second}) and not elem H) within {radius:g} of (({first}) and not elem H)"
        if not api.count_atoms(left, state=state):
            continue
        proximity.append((left, right))
        typed, models = _capture_interactions(api, f"byres ({left})", f"byres ({right})", state)
        snapshots.append((typed, models))
    if not proximity:
        raise ValueError(f"No interface atoms found within {radius:g} Å")
    focus = "byres (" + " or ".join(f"({part})" for pair in proximity for part in pair) + ")"
    pocket = ligand is not None and partners[0][0] == protein
    return snapshots, (protein, ligand, scope, state, focus, proximity, pocket)


def _compute_interface(snapshots):
    from .review_view_geometry import aromatic_rings, stacking_pairs

    coords, contacts = {}, set()
    polar, salt, pi_pairs = set(), set(), []
    for typed, models in snapshots:
        coords.update({atom.index: atom.coord for model in models for atom in model.atom})
        polar.update(typed["polar_contacts"])
        salt.update(typed["salt_bridges"])
        for start, end, ids1, ids2 in stacking_pairs(*(aromatic_rings(model) for model in models)):
            pi_pairs.append((start, end))
            contacts.update(ids1 + ids2)
    for a, b in polar | salt:
        contacts.update((a, b))
    return coords, contacts, polar, salt, pi_pairs


def _draw_interface(api, source, protein, ligand, scope, state, focus, proximity, pocket, data):
    from pymol import util
    from .bundle_view import CandidateCavity

    coords, contacts, polar, salt, pi_pairs = data
    residues = focus if scope == "nearby" else ("byres (" + _indices(source, contacts) + ")" if contacts else "none")
    sidechains = f"(({residues}) and polymer.protein) and not ({_BACKBONE})"
    api.show("cartoon", protein)
    if ligand is None:
        util.cbc(protein, _self=api)
    api.hide("sticks", protein)
    api.show("sticks", sidechains)
    util.cnc(sidechains, _self=api)
    if ligand:
        api.show("sticks", ligand)
        api.color("yellow", f"({ligand}) and elem C")
        util.cnc(ligand, _self=api)
    _cylinders(api, source, "polar_contacts", ((coords[a], coords[b]) for a, b in polar), (0.12, 0.76, 0.69), 0.06, 0.45)
    _cylinders(api, source, "salt_bridges", ((coords[a], coords[b]) for a, b in salt), (0.91, 0.26, 0.62), 0.13, 1.1)
    _dots(api, source, "pi_stacks", (a + (b - a) * t for a, b in pi_pairs for t in (0, .25, .5, .75, 1)),
          (0.46, 0.51, 1.0), 0.19)
    api.zoom(focus, 5, state=state)
    print(f"Interface: {len(polar)} polar contacts, {len(salt)} salt bridges, {len(pi_pairs)} π stacks")

    def capture(renderer):
        if pocket:
            return renderer.capture_cavity(api, protein, ligand, state)
        return renderer.capture_interface_cavity(api, source, proximity, state)

    return CandidateCavity("Interface cavity", f"{source}_{uuid4().hex[:12]}", capture, interface=True)


def _tmdet_pdb(api, source, protein, state):
    chains = api.get_chains(protein)
    if all(len(chain) <= 1 for chain in chains):
        return api.get_pdbstr(protein, state=state)
    # Use unique one-character chain IDs in the staged input only.
    symbols = ascii_letters + digits
    if len(chains) > len(symbols):
        raise ValueError("Too many chains for local TmDet PDB staging")
    rows = []
    for chain, symbol in zip(chains, symbols):
        selection = _chain_arg(source, "chain:" + chain, api)
        rows.extend(line[:21] + symbol + line[22:] for line in api.get_pdbstr(selection, state=state).splitlines()
                    if line.startswith(("ATOM  ", "HETATM")))
        rows.append("TER")
    return "\n".join(rows + ["END", ""])


def _capture_membrane(api, source, protein):
    state = api.get_object_state(source)
    calpha = api.get_model(f"({protein}) and name CA", state).atom
    phosphorus = [atom.coord for atom in api.get_model(f"%{source} and ({LIPID_SELECTION}) and elem P", state).atom]
    try:
        pdb = _tmdet_pdb(api, source, protein, state)
    except ValueError:
        pdb = ""
    return state, calpha, phosphorus, pdb


def _estimate_membrane(calpha, phosphorus, pdb):
    import numpy as np
    from .review_membrane_tmdet import tmdet_slab
    from .review_view_geometry import _lipid_slab, membrane_slab

    leaflet = _lipid_slab(np.asarray(phosphorus))
    if leaflet is not None:
        return (*leaflet, "lipid phosphorus leaflets")
    try:
        return tmdet_slab(pdb) if pdb else membrane_slab(calpha, ())
    except (OSError, RuntimeError, ValueError, ParseError, SubprocessError) as exc:
        print(f"Membrane: local TmDet could not place this structure ({exc}). Trying helix geometry.")
        return membrane_slab(calpha, ())


def _show_membrane(api, source, protein, state, calpha, slab):
    import numpy as np
    from pymol import util
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
    if slab is None:
        print("Membrane: hydrophobic surface shown. No supported bilayer orientation found.")
        return
    center, normal, half_width, method = slab
    depths = np.array([(atom.coord - center) @ normal for atom in calpha])
    boundary = [atom.index for atom, depth in zip(calpha, depths)
                if atom.resn in {"TYR", "TRP", "ARG", "LYS"} and abs(abs(depth) - half_width) <= 4.0]
    if boundary:
        sidechains = f"byres ({_indices(source, boundary)}) and not ({_BACKBONE})"
        api.show("sticks", sidechains)
        util.cnc(sidechains, _self=api)
    inside = np.asarray([atom.coord for atom, depth in zip(calpha, depths) if abs(depth) <= half_width])
    if len(inside):
        planar = (inside - center) - ((inside - center) @ normal)[:, None] * normal
        size = max(26.0, 2 * np.linalg.norm(planar, axis=1).max() + 8.0)
    else:
        size = 28.0
    edges = []
    for sign in (-1, 1):
        corners = membrane_corners(tuple(center + sign * half_width * normal), tuple(normal), size, size)
        edges.extend(zip(corners, corners[1:] + corners[:1]))
    _cylinders(api, source, "membrane_planes", edges, (0.26, 0.65, 0.72), 0.14, 2.2)
    print(f"Membrane: {2 * half_width:.1f} Å slab estimated from {method}. "
          f"{int(np.count_nonzero(np.abs(depths) <= half_width))} core Cα atoms, {len(boundary)} boundary residues.")


def _show_mass(api, source, chains):
    from pymolplugins.styles.presets import PRESETS

    preset = next(preset for preset in PRESETS if preset.name == "rfd2_paper_palette")
    palette = preset.color_operations[0]["palette"]
    for name in palette:
        api.set_color(name, preset.colors[name])
    for chain in chains:
        api.show("cartoon", chain)
        api.spectrum("resi", " ".join(palette), selection=chain)


def _show_cartoon(api, source):
    from pymol import util

    target = f"%{source}"
    api.hide("everything", target)
    api.show("cartoon", target + " and polymer")
    api.show("sticks", target + " and not polymer and not solvent")
    util.cbc(target, _self=api)
    api.zoom(target, 3)
