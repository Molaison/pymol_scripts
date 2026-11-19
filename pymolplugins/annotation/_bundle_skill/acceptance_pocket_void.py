"""Capture and display residual space around the ligand in a bound complex."""

from pymol import CmdException, cmd
from pymol.cgo import ALPHA, COLOR, SPHERE

from acceptance_bundle import asset_name
from acceptance_cavity_runtime import calculate_cavity


_LAYERS = {
    "recesses": ("cavity_recess", (0.01, 0.52, 0.80), "Cyan dots: solvent-accessible recess"),
    "enclosed": ("cavity_dots", (0.66, 0.30, 0.82), "Violet dots: enclosed space"),
}


def _atom_rows(atoms):
    return [[*(round(x, 3) for x in atom.coord), round(atom.vdw, 3)] for atom in atoms]


def capture_cavity(api, protein, ligand, state=1):
    protein_atoms = api.get_model(f"({protein}) and not elem H", state).atom
    ligand_atoms = api.get_model(f"({ligand}) and not elem H", state).atom
    if not protein_atoms or not ligand_atoms:
        raise CmdException("Cavity needs protein and ligand heavy atoms.")
    atoms = _atom_rows(protein_atoms + ligand_atoms)
    return {"atoms": atoms, "ligand_indices": list(range(len(protein_atoms), len(atoms)))}


def capture_interface_cavity(api, source, pairs, state):
    atoms = api.get_model(f"%{source} and not elem H", state).atom
    indices = {atom.index: index for index, atom in enumerate(atoms)}
    blocks = [
        [sorted(indices[atom.index] for atom in api.get_model(side, state).atom) for side in pair]
        for pair in pairs
    ]
    return {"atoms": _atom_rows(atoms), "interface_pairs": blocks}


def cavity_object_names(identity):
    return {key: asset_name(identity, role) for key, (role, _, _) in _LAYERS.items()}


def draw_cavity(identity, result, *, api=cmd):
    """Draw sampled probe centers using colors that distinguish accessibility."""
    layers = {}
    names = cavity_object_names(identity)
    for key, (_, color, legend) in _LAYERS.items():
        points = [xyz for region in result[key] for xyz in region["points"]]
        if not points:
            continue
        dots = [ALPHA, 0.86, COLOR, *color]
        for xyz in points:
            dots.extend((SPHERE, *map(float, xyz), 0.20))
        name = names[key]
        api.load_cgo(dots, name, zoom=0)
        layers[name] = legend, len(points)
    return layers


def pocket_cavity_layers(identity, protein, ligand, *, centers=None):
    if centers is not None:
        result = {"recesses": [], "enclosed": [{"points": centers}]}
    else:
        try:
            result, _ = calculate_cavity(capture_cavity(cmd, protein, ligand))
        except Exception as error:
            print(f"PyMOL pocket cavity unavailable: {error}")
            return {}
    return draw_cavity(identity, result)
