"""Candidate-local viewing recipes used by Structure Annotator Step commands."""

import csv
from io import StringIO
from itertools import combinations
from functools import partial
from string import ascii_letters, digits
from uuid import uuid4
from weakref import WeakKeyDictionary

from pymolplugins.core.parameters import as_bool, as_float

from .command_templates import LIPID_SELECTION, _chain_selection
from .review_membrane import MembraneNotice, draw_membrane, estimate_membrane


REVIEW_VIEW_COMMANDS = frozenset({"pp_view", "sa_view_cartoon", "sa_view_interface", "sa_view_membrane", "sa_view_mass"})
QUICK_VIEWS = {
    "overview": "Structure overview", "interface": "Interface & pocket",
    "membrane": "Membrane topology", "mass": "MASS",
}
SHOW_PRESETS = QUICK_VIEWS
_OVERVIEW_ALIASES = {
    "cartoon": (False, 0.5, False), "surface": (True, 0.25, False),
    "nucleic": (False, 0.5, True), "oligomer": (True, 0.65, False),
}
_BACKBONE = "name N+C+O+OXT"
_OVERLAYS = WeakKeyDictionary()


def loaded_molecules(api):
    overlays = set().union(*_OVERLAYS.get(api, {}).values())
    return [name for name in api.get_names("objects")
            if name not in overlays and not name.startswith("_pp_focus_")
            and api.get_type(name) == "object:molecule"]


def encode_review_command(name, *values):
    buffer = StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(values)
    return name + " " + buffer.getvalue().rstrip("\n")


def quick_view_command(view, source, radius="4", mass_chain=None):
    return show_preset_command(view, source, radius=radius, binder_chain=mass_chain)


def _chain_option(value):
    if value is None or value == "auto":
        return "auto"
    value = str(value)
    return value if value.startswith("chain:") else "chain:" + value


def show_preset_command(view, source, mode="auto", radius="4", scope="nearby",
                        target_chain="auto", binder_chain="auto", surface="0",
                        transparency="0.5", nucleic="1"):
    if view not in SHOW_PRESETS and view not in _OVERVIEW_ALIASES:
        raise ValueError("Choose " + ", ".join(SHOW_PRESETS))
    options = ()
    if view in ("interface", "mass"):
        options = (mode, radius, scope, _chain_option(target_chain), _chain_option(binder_chain))
    if view in {"overview", "interface"}:
        if as_bool(surface):
            options += ("surface=1", f"transparency={transparency}")
        if view == "overview" and not as_bool(nucleic):
            options += ("nucleic=0",)
    return encode_review_command("pp_view", view, source, *options)


def _preset_fields(name, raw):
    values = next(csv.reader([raw], strict=True, skipinitialspace=True))
    if name != "pp_view":
        source, *args = values
        return name.removeprefix("sa_view_"), source, args
    view, source, *options = values
    defaults = dict(mode="auto", radius="4", scope="nearby", target_chain="auto", binder_chain="auto",
                    surface="0", transparency="0.5", nucleic="1")
    keys = tuple(defaults)
    if len(options) > len(keys):
        raise ValueError("Too many display preset options.")
    for index, option in enumerate(options):
        key, separator, value = option.partition("=")
        if separator and key in defaults:
            defaults[key] = value
        else:
            defaults[keys[index]] = option
    mode, radius, scope, target, binder, surface, transparency, nucleic = defaults.values()
    if view == "overview":
        return view, source, (as_bool(surface), _surface_alpha(transparency), as_bool(nucleic))
    if view == "interface":
        return view, source, (mode, radius, scope, _chain_option(target), _chain_option(binder),
                              as_bool(surface), _surface_alpha(transparency))
    if view == "mass":
        return view, source, (_chain_option(binder), _chain_option(target))
    return view, source, ()


def _surface_alpha(value):
    return as_float(value, minimum=0, maximum=1, name="surface transparency")


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
    if name not in REVIEW_VIEW_COMMANDS:
        raise ValueError(f"Unknown display command: {name}")
    view, source, args = _preset_fields(name, raw)
    if view in _OVERVIEW_ALIASES:
        args = _OVERVIEW_ALIASES[view]
        view = "overview"
    if view == "overview":
        surface, transparency, nucleic = args
        try:
            source = _source_object(api, source)
        except ValueError:
            if surface or source not in api.get_names("objects") or api.get_type(source) != "object:group":
                raise
        return _show_overview, source, (surface, transparency, nucleic)
    source = _source_object(api, source)
    protein = f"%{source} and polymer.protein"
    if not api.count_atoms(protein):
        raise ValueError("This preset requires protein atoms")
    if view == "membrane" and not args:
        return draw_membrane, source, (protein,)
    if view == "mass" and len(args) == 2:
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
    if view == "interface" and len(args) in (5, 7):
        mode, radius, scope, target_id, binder_id = args[:5]
        surface, transparency = args[5:] or (False, 0.5)
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
        return _draw_interface, source, (protein, partners, ligand, float(radius), scope, surface, transparency)
    raise ValueError(f"Invalid arguments for {view} preset.")


def execute_review_command(line, api, *, exclusive=False):
    from .bundle_view import CandidateCavity, draw_candidate_cavity, prepare_candidate_cavity

    source, action, compute, draw = prepare_review_command(line, api)
    followup = draw_review_view(api, source, action, draw, compute() if compute else None, exclusive=exclusive)
    if isinstance(followup, MembraneNotice):
        print(followup.text.translate(str.maketrans({"Å": "A", "–": "-", "·": "|"})))
        return followup
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
    if action is draw_membrane:
        state, calpha, markers, phosphorus, pdb, chains = _capture_membrane(api, source, *args)
        compute = partial(estimate_membrane, calpha, phosphorus, pdb, chains)
        draw = partial(draw_membrane, api, source, *args, state, calpha, markers, draw_edges=_cylinders)
        return source, action, compute, draw
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
    if isinstance(result, MembraneNotice) and not result.placed:
        exclusive = False
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


def _cylinders(api, source, kind, pairs, color, radius, dash, *, zoom=-1):
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
        name = api.get_unused_name(f"{source}_{kind}")
        api.load_cgo([float(value) for value in body], name, zoom=zoom)
        return name


def _dots(api, source, kind, points, color, radius):
    from pymol.cgo import ALPHA, COLOR, SPHERE

    body = [ALPHA, 0.78, COLOR, *color]
    for xyz in points:
        body.extend((SPHERE, *map(float, xyz), radius))
    if len(body) > 6:
        name = api.get_unused_name(f"{source}_{kind}")
        api.load_cgo(body, name, zoom=0)
        return name


def draw_interface_contacts(api, source, coords, polar, salt, pi_pairs, names):
    """Draw the contact layers shared by Interface and Interactive focus."""
    layers = {}
    for kind, pairs, color, radius, dash in (
        ("polar_contacts", polar, (0.12, 0.76, 0.69), 0.06, 0.45),
        ("salt_bridges", salt, (0.91, 0.26, 0.62), 0.13, 1.1),
    ):
        name = _cylinders(api, source, kind, ((coords[a], coords[b]) for a, b in sorted(pairs)),
                          color, radius, dash, zoom=0)
        if name is not None:
            names.append(name)
            layers[kind] = name
    name = _dots(api, source, "pi_stacks",
                 (a + (b - a) * t for a, b in pi_pairs for t in (0, .25, .5, .75, 1)),
                 (0.46, 0.51, 1.0), 0.19)
    if name is not None:
        names.append(name)
        layers["pi_stacks"] = name
    return layers


def _capture_interface(api, source, protein, partners, ligand, radius, scope, surface, transparency):
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
    return snapshots, (protein, ligand, scope, state, focus, proximity, pocket, surface, transparency)


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


def _draw_interface(api, source, protein, ligand, scope, state, focus, proximity, pocket, surface, transparency, data):
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
    draw_interface_contacts(api, source, coords, polar, salt, pi_pairs, [])
    if surface:
        _surface_overlay(api, source, f"({focus}) and polymer.protein", state, transparency, "interface")
    api.zoom(focus, 5, state=state)
    print(f"Interface: {len(polar)} polar contacts, {len(salt)} salt bridges, {len(pi_pairs)} π stacks")

    def capture(renderer):
        if pocket:
            return renderer.capture_cavity(api, protein, ligand, state)
        return renderer.capture_interface_cavity(api, source, proximity, state)

    return CandidateCavity("Interface cavity", f"{source}_{uuid4().hex[:12]}", capture, interface=True)


def _tmdet_pdb(api, source, protein, state):
    chains = api.get_chains(protein)
    if all(len(chain) == 1 for chain in chains):
        return api.get_pdbstr(protein, state=state), {}
    symbols = ascii_letters + digits
    if len(chains) > len(symbols):
        raise ValueError("Too many chains for local TmDet PDB staging")
    rows = []
    mapping = dict(zip(symbols, chains))
    for symbol, chain in mapping.items():
        selection = _chain_arg(source, "chain:" + chain, api)
        rows.extend(line[:21] + symbol + line[22:] for line in api.get_pdbstr(selection, state=state).splitlines()
                    if line.startswith(("ATOM  ", "HETATM")))
        rows.append("TER")
    return "\n".join(rows + ["END", ""]), mapping


def _capture_membrane(api, source, protein):
    state = api.get_object_state(source)
    calpha = api.get_model(f"({protein}) and name CA", state).atom
    markers = api.get_model(f"({protein}) and resn ARG+LYS+TYR+TRP and not elem H", state).atom
    phosphorus = [atom.coord for atom in api.get_model(f"%{source} and ({LIPID_SELECTION}) and elem P", state).atom]
    try:
        pdb, chains = _tmdet_pdb(api, source, protein, state)
    except ValueError:
        pdb, chains = "", {}
    return state, calpha, markers, phosphorus, pdb, chains


def _show_mass(api, source, chains):
    from pymolplugins.styles.presets import PRESETS

    preset = next(preset for preset in PRESETS if preset.name == "rfd2_paper_palette")
    palette = preset.color_operations[0]["palette"]
    for name in palette:
        api.set_color(name, preset.colors[name])
    for chain in chains:
        api.show("cartoon", chain)
        api.spectrum("resi", " ".join(palette), selection=chain)


def _surface_overlay(api, source, selection, state, transparency, label):
    name = api.get_unused_name(f"{source}_{label}_surface")
    api.create(name, selection, source_state=state, target_state=1, zoom=0)
    api.hide("everything", f"%{name}")
    api.set("transparency", transparency, name)
    api.show("surface", f"%{name}")


def _show_overview(api, source, surface, transparency, nucleic):
    from pymol import util

    target = f"%{source}"
    api.hide("everything", target)
    api.show("cartoon", target + " and polymer")
    api.show("sticks", target + " and not polymer and not solvent")
    util.cbc(target, _self=api)
    if nucleic and api.count_atoms(target + " and polymer.nucleic"):
        api.show("sticks", target + " and polymer.nucleic")
        util.cnc(target + " and polymer.nucleic", _self=api)
    if surface:
        _surface_overlay(api, source, target + " and not solvent", api.get_object_state(source), transparency, "overview")
