"""Draw enclosed, solvent-sized residual space in a ligand-anchored pocket."""

from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from pymol import CmdException, cmd
from pymol.cgo import ALPHA, COLOR, SPHERE

from acceptance_bundle import asset_name


def _fpocket_spheres(protein, ligand):
    executable = shutil.which("fpocket")
    if not executable:
        print("PyMOL pocket void: fpocket unavailable.")
        return []

    def records(selection):
        return [
            line for line in cmd.get_pdbstr(f"({selection}) and not elem H").splitlines()
            if line.startswith(("ATOM  ", "HETATM"))
        ]

    protein_records = records(protein)
    ligand_records = records(ligand)
    used_chains = {line[21] for line in protein_records}
    ligand_chain = next((c for c in "ZXYWVUTSRQPONMLKJIHGFEDCBA" if c not in used_chains), "")
    if not ligand_chain:
        raise CmdException("Fpocket staging requires an unused single-letter chain ID.")
    ligand_records = ["HETATM" + line[6:21] + ligand_chain + line[22:] for line in ligand_records]
    lines = [
        f"{line[:6]}{index:5d}{line[11:]}"
        for index, line in enumerate(protein_records + ligand_records, 1)
    ]
    with TemporaryDirectory(prefix="pymol-pocket-") as tmp:
        path = Path(tmp) / "bound.pdb"
        path.write_text("\n".join(lines + ["END", ""]))
        completed = subprocess.run(
            [executable, "-f", path.name, "-a", ligand_chain], cwd=tmp,
            capture_output=True, text=True, check=False,
        )
        if completed.returncode:
            raise CmdException(completed.stderr.strip() or completed.stdout.strip())
        files = sorted((Path(tmp) / "bound_out" / "pockets").glob("pocket*_vert.pqr"))
        return [_read_spheres(file) for file in files]


def _read_spheres(path):
    return [
        (*map(float, (line[30:38], line[38:46], line[46:54])), float(line.split()[-1]))
        for line in Path(path).read_text().splitlines() if line.startswith("ATOM")
    ]


def pocket_void_centers(identity, protein, ligand, *, alpha_spheres=None):
    """Find unoccupied and bulk-disconnected 1-A cell centers in one matched pocket."""
    try:
        import numpy as np
        from rdkit import Chem
        from scipy.ndimage import binary_propagation, label
        from scipy.spatial.distance import cdist
    except ImportError as error:
        print(f"PyMOL pocket void unavailable: {error}")
        return []

    ligand_heavy = f"({ligand}) and not elem H"
    lig = np.asarray(cmd.get_coords(ligand_heavy), dtype=float)
    pockets = [_read_spheres(alpha_spheres)] if alpha_spheres else _fpocket_spheres(protein, ligand)
    # One pocket, ranked by the number of ligand heavy atoms covered by its
    # alpha spheres. Disjoint detected pockets cannot contribute cells.
    matches = []
    for pocket in pockets:
        if pocket:
            spheres = np.asarray(pocket)
            covered = np.any(cdist(lig, spheres[:, :3]) < spheres[:, 3], axis=1)
            matches.append((int(covered.sum()), spheres))
    if not matches:
        print(f"PyMOL pocket void {identity}: no alpha spheres.")
        return []
    coverage, spheres = max(matches, key=lambda match: match[0])
    if not coverage:
        print(f"PyMOL pocket void {identity}: no pocket overlaps the ligand heavy atoms.")
        return []

    atoms = cmd.get_model(f"(({protein}) or ({ligand_heavy})) and not elem H").atom
    coords = np.asarray([atom.coord for atom in atoms])
    table = Chem.GetPeriodicTable()
    radii = np.asarray([table.GetRvdw(atom.symbol.capitalize()) for atom in atoms])
    margin = 4.0
    padding = 4.0
    probe = 1.4
    lo = np.floor(lig.min(axis=0) - margin - padding).astype(int)
    hi = np.ceil(lig.max(axis=0) + margin + padding).astype(int)
    grid_axes = [np.arange(start, stop + 1) for start, stop in zip(lo, hi)]
    axes = np.meshgrid(*grid_axes, indexing="ij")
    grid = np.column_stack([axis.ravel() for axis in axes])
    near = cdist(grid, lig).min(axis=1) <= margin
    nearby = grid[near]
    inside = np.any(cdist(nearby, spheres[:, :3]) < spheres[:, 3], axis=1)
    candidates = nearby[inside]

    # Compute cube–sphere overlap using the closest point on each 1-A cell.
    local = np.all((coords >= lo - 4) & (coords <= hi + 4), axis=1)
    offsets = np.maximum(np.abs(candidates[:, None, :] - coords[None, local, :]) - 0.5, 0.0)
    occupied = np.any(np.sum(offsets ** 2, axis=2) < radii[None, local] ** 2, axis=1)
    shape = axes[0].shape
    local_empty = candidates[~occupied]

    # Classify access in the padded box before applying the pocket mask. Use
    # diagonal access paths to avoid calling narrow grid artifacts enclosed.
    accessible = np.empty(len(grid), dtype=bool)
    for start in range(0, len(grid), 4096):
        window = slice(start, start + 4096)
        accessible[window] = np.all(
            cdist(grid[window], coords[local]) >= radii[None, local] + probe, axis=1
        )
    accessible = accessible.reshape(shape)
    boundary = np.ones(shape, dtype=bool)
    boundary[1:-1, 1:-1, 1:-1] = False
    bulk = binary_propagation(
        boundary & accessible, structure=np.ones((3, 3, 3), dtype=bool), mask=accessible
    )
    empty = np.zeros(shape, dtype=bool)
    empty[tuple((local_empty - lo).T)] = True
    bulk_count = np.count_nonzero(empty & bulk)
    too_small_count = np.count_nonzero(empty & ~accessible)
    empty &= accessible & ~bulk
    components, _ = label(empty)
    sizes = np.bincount(components.ravel())
    sizes[0] = 0
    centers = np.argwhere(sizes[components] >= 3) + lo
    print(
        f"PyMOL pocket void {identity}: ligand coverage {coverage}/{len(lig)}, "
        f"unoccupied local cells {len(local_empty)}, bulk-connected {bulk_count}, "
        f"probe-excluded {too_small_count}, enclosed cells {len(centers)}."
    )
    return centers.tolist()


def pocket_void_dots(identity, protein, ligand, *, alpha_spheres=None, centers=None):
    """Draw calculated centers or use centers supplied in the same frame."""
    if centers is None:
        centers = pocket_void_centers(identity, protein, ligand, alpha_spheres=alpha_spheres)
    if not len(centers):
        return "", 0
    name = asset_name(identity, "cavity_dots")
    dots = [ALPHA, 0.72, COLOR, 0.10, 0.59, 0.82]
    for xyz in centers:
        dots.extend((SPHERE, *map(float, xyz), 0.26))
    cmd.load_cgo(dots, name)
    return name, len(centers)
