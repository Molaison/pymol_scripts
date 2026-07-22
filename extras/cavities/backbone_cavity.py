"""PyMOL plugin for cavity identification and visualization."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import alphashape
import numpy as np
import trimesh
from pymol import cmd
from pymol.cgo import ALPHA, COLOR, SPHERE
from scipy.spatial import Delaunay, KDTree

try:  # Optional Qt UI support if available
    from pymol.Qt import QtWidgets
except Exception:  # pragma: no cover - headless PyMOL builds
    QtWidgets = None

try:
    from pymol.plugins import addmenuitemqt
except Exception:  # pragma: no cover
    addmenuitemqt = None

MAX_GRID_POINTS = 2_000_000


@dataclass(frozen=True, order=True)
class ResidueID:
    """Descriptor for a residue in the current PyMOL session."""

    chain: str
    resi: str
    resn: str


def identify_backbone_cavity(
    selection: str = "all",
    alpha_value: float = 2.0,
    grid_spacing: float = 1.0,
    atom_dist_threshold: float = 2.5,
    lining_radius: float = 5.0,
    cavity_object: str = "backbone_cavity_points",
    residue_selection: str = "backbone_cavity_residues",
    sphere_radius: float = 0.7,
    cavity_color: str = "tv_orange",
    residue_color: str = "magenta",
):
    """Identify and visualize backbone cavities for a PyMOL selection.

    Args:
        selection: PyMOL atom selection string to analyze.
        alpha_value: Alpha-shape tightness; lower captures more detail.
        grid_spacing: Spacing of the cavity search grid (Angstrom).
        atom_dist_threshold: Minimum distance from any atom to consider a point empty (Angstrom).
        lining_radius: Residues with atoms within this distance from a cavity point are highlighted (Angstrom).
        cavity_object: Name of the CGO object that stores cavity spheres.
        residue_selection: Name of the selection containing lining residues.
        sphere_radius: Radius for rendering each cavity sphere (Angstrom).
        cavity_color: PyMOL color for the cavity spheres.
        residue_color: PyMOL color applied to lining residues.
    """

    try:
        if grid_spacing <= 0:
            raise ValueError("grid_spacing must be positive")
        if atom_dist_threshold <= 0:
            raise ValueError("atom_dist_threshold must be positive")
        if lining_radius <= 0:
            raise ValueError("lining_radius must be positive")
        if sphere_radius <= 0:
            raise ValueError("sphere_radius must be positive")

        atom_coords, residue_map = _collect_selection_atoms(selection)
        cavity_points = _locate_cavity_points(
            atom_coords,
            alpha_value=alpha_value,
            grid_spacing=grid_spacing,
            atom_dist_threshold=atom_dist_threshold,
        )

        if cavity_points.size == 0:
            cmd.delete(cavity_object)
            cmd.delete(residue_selection)
            print("[identify_backbone_cavity] No cavities detected with the current parameters.")
            return [], []

        lining_residues = _find_lining_residues(
            atom_coords, residue_map, cavity_points, lining_radius
        )

        _render_cavity_points(cavity_points, cavity_object, sphere_radius, cavity_color)
        _build_residue_selection(
            selection, lining_residues, residue_selection, residue_color
        )

        _log_results(selection, cavity_points, lining_residues, cavity_object, residue_selection)
        return cavity_points, sorted(lining_residues)
    except Exception as exc:  # noqa: BLE001
        print(f"[identify_backbone_cavity] Error: {exc}")
        traceback.print_exc()
        raise


def _collect_selection_atoms(selection: str) -> Tuple[np.ndarray, List[ResidueID]]:
    """Extract heavy-atom coordinates and residue identifiers for a selection."""

    model = cmd.get_model(selection)
    coords = []
    residues: List[ResidueID] = []
    solvent_names = {"HOH", "WAT", "SOL"}

    for atom in model.atom:
        symbol = (atom.symbol or atom.name[:1]).upper()
        if symbol == "H":
            continue
        resn = atom.resn.strip()
        if resn in solvent_names:
            continue
        coords.append(atom.coord)
        residues.append(
            ResidueID(
                chain=(atom.chain or "").strip(),
                resi=str(atom.resi).strip(),
                resn=resn,
            )
        )

    if not coords:
        raise ValueError("Selection contains no heavy atoms to analyze.")
    return np.asarray(coords, dtype=float), residues


def _locate_cavity_points(
    atom_coords: np.ndarray,
    alpha_value: float,
    grid_spacing: float,
    atom_dist_threshold: float,
) -> np.ndarray:
    """Run the grid-based cavity search returning empty points."""

    print(
        f"[identify_backbone_cavity] Processing {len(atom_coords)} atoms with alpha={alpha_value}"
    )
    alpha_mesh = alphashape.alphashape(atom_coords, alpha_value)

    grid_points = _build_search_grid(atom_coords, grid_spacing)
    print(f"[identify_backbone_cavity] Scanning {len(grid_points):,} grid points...")

    interior_points = _filter_points_inside(alpha_mesh, atom_coords, grid_points)
    if interior_points.size == 0:
        print("[identify_backbone_cavity] No interior points survived the alpha shape test.")
        return np.empty((0, 3), dtype=float)

    tree = KDTree(atom_coords)
    distances, _ = tree.query(interior_points)
    cavity_points = interior_points[distances > atom_dist_threshold]
    print(f"[identify_backbone_cavity] Found {len(cavity_points):,} cavity points.")
    return cavity_points


def _build_search_grid(atom_coords: np.ndarray, spacing: float) -> np.ndarray:
    """Create a dense grid that covers the bounding box of the atom set."""

    padding = spacing
    min_box = np.min(atom_coords, axis=0) - padding
    max_box = np.max(atom_coords, axis=0) + padding
    axes = [
        np.arange(min_box[i], max_box[i] + spacing, spacing, dtype=float) for i in range(3)
    ]
    grid = (
        np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)
        .reshape(-1, 3)
        .astype(float)
    )
    if grid.shape[0] > MAX_GRID_POINTS:
        raise ValueError(
            f"Grid would contain {grid.shape[0]:,} points. Increase grid_spacing to limit memory use."
        )
    return grid


def _filter_points_inside(
    alpha_mesh: object, atom_coords: np.ndarray, grid_points: np.ndarray
) -> np.ndarray:
    """Keep only points that fall inside the Alpha Shape boundary."""

    if isinstance(alpha_mesh, trimesh.base.Trimesh):
        mask = alpha_mesh.contains(grid_points)
        return grid_points[mask]

    print(
        "[identify_backbone_cavity] Alpha shape is not a watertight mesh; falling back to Delaunay hull."
    )
    hull = Delaunay(atom_coords)
    mask = hull.find_simplex(grid_points) >= 0
    return grid_points[mask]


def _find_lining_residues(
    atom_coords: np.ndarray,
    residue_map: Sequence[ResidueID],
    cavity_points: np.ndarray,
    lining_radius: float,
) -> Iterable[ResidueID]:
    """Return residues that have atoms within a radius of any cavity point."""

    if cavity_points.size == 0:
        return []

    atom_tree = KDTree(atom_coords)
    cavity_tree = KDTree(cavity_points)
    neighbor_indices = atom_tree.query_ball_tree(cavity_tree, r=lining_radius)

    residues = {
        residue_map[i]
        for i, nearby in enumerate(neighbor_indices)
        if nearby
    }
    print(f"[identify_backbone_cavity] Highlighting {len(residues)} lining residues.")
    return residues


def _render_cavity_points(
    cavity_points: np.ndarray, object_name: str, sphere_radius: float, color_name: str
) -> None:
    """Load the cavity points into PyMOL as CGO spheres."""

    cmd.delete(object_name)
    if cavity_points.size == 0:
        return

    color_tuple = _safe_color_tuple(color_name)
    cgo_obj: List[float] = [COLOR, *color_tuple, ALPHA, 0.6]
    for point in cavity_points:
        cgo_obj.extend([SPHERE, float(point[0]), float(point[1]), float(point[2]), sphere_radius])
    cmd.load_cgo(cgo_obj, object_name, state=1)


def _safe_color_tuple(color_name: str) -> Tuple[float, float, float]:
    try:
        return cmd.get_color_tuple(color_name)
    except Exception:  # pragma: no cover - fallback when color is unknown
        print(
            f"[identify_backbone_cavity] Unknown color '{color_name}', using tv_orange as fallback."
        )
        return cmd.get_color_tuple("tv_orange")


def _build_residue_selection(
    base_selection: str,
    residues: Sequence[ResidueID],
    selection_name: str,
    color_name: str,
) -> None:
    """Create or refresh the PyMOL selection containing lining residues."""

    cmd.delete(selection_name)
    if not residues:
        print("[identify_backbone_cavity] No lining residues found.")
        return

    clauses = []
    for residue in sorted(residues):
        if residue.chain:
            clause = f"(chain {residue.chain} and resi {residue.resi})"
        else:
            clause = f"(resi {residue.resi})"
        clauses.append(clause)

    selection_expr = f"(({base_selection}) and ({' or '.join(clauses)}))"
    cmd.select(selection_name, selection_expr)
    cmd.show("sticks", selection_name)
    cmd.color(color_name, selection_name)


def _log_results(
    selection: str,
    cavity_points: np.ndarray,
    residues: Iterable[ResidueID],
    cavity_object: str,
    residue_selection: str,
) -> None:
    """Print a concise summary to the PyMOL console."""

    residue_list = sorted(residues)
    print("\n=== identify_backbone_cavity ===")
    print(f"Selection          : {selection}")
    print(f"Cavity object      : {cavity_object} ({len(cavity_points)} spheres)")
    print(f"Residue selection  : {residue_selection}")
    print("Lining residues    :")
    if not residue_list:
        print("  (none)")
        return
    for residue in residue_list:
        chain_label = residue.chain or "-"
        print(f"  Chain {chain_label} | {residue.resn} {residue.resi}")


_cmd_registered = False


def __init_plugin__(app=None):
    """Register the plugin entry point with PyMOL."""

    _ensure_command_registered()
    if addmenuitemqt is not None and QtWidgets is not None:
        addmenuitemqt("Identify Backbone Cavity", _launch_dialog)


def _ensure_command_registered():
    global _cmd_registered
    if not _cmd_registered:
        cmd.extend("identify_backbone_cavity", identify_backbone_cavity)
        _cmd_registered = True


def _launch_dialog():
    if QtWidgets is None:
        print("[identify_backbone_cavity] Qt interface is not available in this PyMOL build.")
        return
    global _dialog
    if _dialog is None:
        _dialog = _IdentifyBackboneCavityDialog()
    _dialog.show()
    _dialog.raise_()


if QtWidgets is not None:

    class _IdentifyBackboneCavityDialog(QtWidgets.QDialog):
        """Minimal Qt dialog that triggers the cavity calculation."""

        def __init__(self):
            super().__init__()
            self.setWindowTitle("Identify Backbone Cavity")
            self.setMinimumWidth(320)

            layout = QtWidgets.QFormLayout(self)
            self.selection_edit = QtWidgets.QLineEdit("all")
            self.alpha_spin = _make_double_spin(0.1, 50.0, 0.1, 2.0)
            self.grid_spin = _make_double_spin(0.1, 5.0, 0.1, 1.0)
            self.empty_spin = _make_double_spin(0.5, 10.0, 0.1, 2.5)
            self.radius_spin = _make_double_spin(1.0, 12.0, 0.5, 5.0)
            self.sphere_spin = _make_double_spin(0.1, 3.0, 0.1, 0.7)
            self.cavity_name = QtWidgets.QLineEdit("backbone_cavity_points")
            self.residue_name = QtWidgets.QLineEdit("backbone_cavity_residues")

            layout.addRow("Selection", self.selection_edit)
            layout.addRow("Alpha", self.alpha_spin)
            layout.addRow("Grid spacing (A)", self.grid_spin)
            layout.addRow("Empty threshold (A)", self.empty_spin)
            layout.addRow("Residue radius (A)", self.radius_spin)
            layout.addRow("Sphere radius (A)", self.sphere_spin)
            layout.addRow("Cavity object", self.cavity_name)
            layout.addRow("Residue selection", self.residue_name)

            button = QtWidgets.QPushButton("Run")
            button.clicked.connect(self._run_command)
            layout.addRow(button)

        def _run_command(self):
            identify_backbone_cavity(
                selection=self.selection_edit.text() or "all",
                alpha_value=self.alpha_spin.value(),
                grid_spacing=self.grid_spin.value(),
                atom_dist_threshold=self.empty_spin.value(),
                lining_radius=self.radius_spin.value(),
                cavity_object=self.cavity_name.text() or "backbone_cavity_points",
                residue_selection=self.residue_name.text() or "backbone_cavity_residues",
                sphere_radius=self.sphere_spin.value(),
            )


    def _make_double_spin(minimum: float, maximum: float, step: float, value: float):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setDecimals(2)
        return spin


else:
    _IdentifyBackboneCavityDialog = None

_dialog = None
_ensure_command_registered()
