"""PCA/cyclic orientation and membrane-slab tools for PyMOL."""

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from pymol import cmd
except ImportError:
    cmd = None  # type: ignore

try:
    from pymolplugins.core.math3d import (
        GeometryError,
        add,
        apply_matrix,
        centroid,
        compose_about_center,
        covariance_eigensystem,
        cross,
        dot,
        fit_rigid_transform,
        homogeneous_to_ttt,
        mat4_multiply,
        normalize,
        parse_vector,
        rotation_from_matrix,
        rotation_from_to,
        rigid_inverse,
        scale,
        subtract,
        symmetric_eigensystem,
        ttt_to_homogeneous,
    )
    from pymolplugins.core.parameters import as_bool, as_float, as_int, sanitize_name, split_names
    from pymolplugins.core.pymol_groups import expand_names as expand_group_names
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.math3d import (  # type: ignore
        GeometryError,
        add,
        apply_matrix,
        centroid,
        compose_about_center,
        covariance_eigensystem,
        cross,
        dot,
        fit_rigid_transform,
        homogeneous_to_ttt,
        mat4_multiply,
        normalize,
        parse_vector,
        rotation_from_matrix,
        rotation_from_to,
        rigid_inverse,
        scale,
        subtract,
        symmetric_eigensystem,
        ttt_to_homogeneous,
    )
    from pymolplugins.core.parameters import as_bool, as_float, as_int, sanitize_name, split_names  # type: ignore
    from pymolplugins.core.pymol_groups import expand_names as expand_group_names  # type: ignore


Point = Tuple[float, float, float]


@dataclass(frozen=True)
class AxisResult:
    source: str
    center: Point
    direction: Point
    confidence: float
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    input_selection: str = ""
    secondary_direction: Optional[Point] = None
    eigenvalues: Optional[Tuple[float, ...]] = None
    input_hash: str = ""
    parameters: Mapping[str, object] = field(default_factory=dict)
    backend: Mapping[str, str] = field(
        default_factory=lambda: {"name": "pymolplugins.math3d", "version": "1.0"}
    )
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": "pymolplugins.axis/1.0",
            "source": self.source,
            "center": list(self.center),
            "direction": list(self.direction),
            "secondary_direction": (
                list(self.secondary_direction) if self.secondary_direction else None
            ),
            "eigenvalues": list(self.eigenvalues) if self.eigenvalues else None,
            "confidence": self.confidence,
            "diagnostics": dict(self.diagnostics),
            "input_selection": self.input_selection,
            "input_hash": self.input_hash,
            "parameters": dict(self.parameters),
            "backend": dict(self.backend),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MembraneResult:
    name: str
    center: Point
    normal: Point
    thickness: float
    width: float
    height: float
    plane_centers: Tuple[Point, Point]
    source: str
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    input_hash: str = ""
    parameters: Mapping[str, object] = field(default_factory=dict)
    backend: Mapping[str, str] = field(
        default_factory=lambda: {"name": "pymolplugins.math3d", "version": "1.0"}
    )
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": "pymolplugins.membrane/1.0",
            "name": self.name,
            "center": list(self.center),
            "normal": list(self.normal),
            "thickness": self.thickness,
            "width": self.width,
            "height": self.height,
            "plane_centers": [list(value) for value in self.plane_centers],
            "source": self.source,
            "diagnostics": dict(self.diagnostics),
            "input_hash": self.input_hash,
            "parameters": dict(self.parameters),
            "backend": dict(self.backend),
            "warnings": list(self.warnings),
        }


_AXES: Dict[str, AxisResult] = {}
_MEMBRANES: Dict[str, MembraneResult] = {}
_ORIENTATION_UNDO: Dict[str, Optional[Tuple[float, ...]]] = {}


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _point_payload(points: Sequence[Sequence[float]]) -> List[List[float]]:
    return [[float(value) for value in point] for point in points]


def _stable_direction(vector: Sequence[float]) -> Point:
    direction = normalize(vector)
    dominant = max(range(3), key=lambda index: abs(direction[index]))
    if direction[dominant] < 0.0:
        direction = scale(direction, -1.0)
    return direction


def pca_axis(
    points: Sequence[Sequence[float]],
    axis: str = "long",
    weights: Optional[Sequence[float]] = None,
) -> AxisResult:
    """Return a deterministic covariance-PCA axis."""
    values, vectors, center = covariance_eigensystem(points, weights)
    mode = str(axis).strip().lower()
    indices = {"long": 0, "major": 0, "middle": 1, "short": 2, "minor": 2}
    if mode not in indices:
        raise GeometryError("axis must be long, middle, or short")
    index = indices[mode]
    if index == 0:
        gap = (values[0] - values[1]) / max(abs(values[0]), 1.0e-12)
    elif index == 2:
        gap = (values[1] - values[2]) / max(abs(values[1]), 1.0e-12)
    else:
        gap = min(values[0] - values[1], values[1] - values[2]) / max(abs(values[0]), 1.0e-12)
    confidence = max(0.0, min(1.0, gap))
    return AxisResult(
        source="pca",
        center=center,
        direction=_stable_direction(vectors[index]),
        confidence=confidence,
        diagnostics={
            "axis_mode": mode,
            "roll_constrained": False,
            "weighting": "weighted" if weights is not None else "equal",
        },
        eigenvalues=values,
        input_hash=_json_sha256(
            {
                "points": _point_payload(points),
                "weights": list(weights) if weights is not None else None,
            }
        ),
        parameters={"axis": mode, "weighting": "weighted" if weights is not None else "equal"},
    )


def vector_axis(
    vector: Sequence[float],
    center: Sequence[float] = (0.0, 0.0, 0.0),
    *,
    source: str = "vector",
    input_selection: str = "",
) -> AxisResult:
    """Create a normalized explicit axis without importing PyMOL."""
    return AxisResult(
        source=str(source),
        center=tuple(float(value) for value in center),  # type: ignore[arg-type]
        direction=_stable_direction(vector),
        confidence=1.0,
        diagnostics={"explicit": True, "roll_constrained": False},
        input_selection=str(input_selection),
        input_hash=_json_sha256(
            {"center": list(center), "vector": list(vector), "source": source}
        ),
        parameters={"source": str(source)},
    )


def two_point_axis(points: Sequence[Sequence[float]]) -> AxisResult:
    """Create an axis whose center is the midpoint of two distinct points."""
    if len(points) != 2:
        raise GeometryError("two_points requires exactly two Cartesian points")
    first = tuple(float(value) for value in points[0])
    second = tuple(float(value) for value in points[1])
    if len(first) != 3 or len(second) != 3:
        raise GeometryError("two_points values must each contain three numbers")
    return vector_axis(
        subtract(second, first),
        scale(add(first, second), 0.5),
        source="two_points",
    )


def _plane_basis(normal: Sequence[float]) -> Tuple[Point, Point]:
    n = normalize(normal)
    candidates = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    reference = min(candidates, key=lambda candidate: abs(dot(n, candidate)))
    u = normalize(cross(n, reference))
    v = normalize(cross(n, u))
    return u, v


def _solve3(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> Tuple[float, float, float]:
    augmented = [list(map(float, row)) + [float(value)] for row, value in zip(matrix, vector)]
    if len(augmented) != 3 or any(len(row) != 4 for row in augmented):
        raise GeometryError("3x3 linear system required")
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= 1.0e-12:
            raise GeometryError("singular 3x3 linear system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column])
            ]
    return tuple(augmented[row][3] for row in range(3))  # type: ignore[return-value]


def _fit_circle_center(points: Sequence[Point], normal: Point, origin: Point) -> Point:
    u, v = _plane_basis(normal)
    projected = [(dot(subtract(point, origin), u), dot(subtract(point, origin), v)) for point in points]
    rows = [(x, y, 1.0) for x, y in projected]
    rhs = [-(x * x + y * y) for x, y in projected]
    normal_matrix = [[sum(row[i] * row[j] for row in rows) for j in range(3)] for i in range(3)]
    normal_rhs = [sum(row[i] * value for row, value in zip(rows, rhs)) for i in range(3)]
    try:
        a, b, _ = _solve3(normal_matrix, normal_rhs)
        return add(origin, add(scale(u, -a / 2.0), scale(v, -b / 2.0)))
    except GeometryError:
        return origin


def _c2_axis(clean: Mapping[str, Sequence[Point]], labels: Sequence[str]) -> AxisResult:
    first, second = labels
    if len(clean[first]) != len(clean[second]):
        raise GeometryError("C2 subunits require equal ordered point counts")
    fit = fit_rigid_transform(clean[first], clean[second])
    rotation = rotation_from_matrix(fit.matrix)
    delta = [
        [
            rotation[row * 3 + column] - (1.0 if row == column else 0.0)
            for column in range(3)
        ]
        for row in range(3)
    ]
    gram = [
        [sum(delta[k][row] * delta[k][column] for k in range(3)) for column in range(3)]
        for row in range(3)
    ]
    _, vectors = symmetric_eigensystem(gram)
    trace = rotation[0] + rotation[4] + rotation[8]
    angle = math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))
    angle_error = abs(math.pi - angle) / math.pi
    return AxisResult(
        source="cyclic",
        center=scale(add(centroid(clean[first]), centroid(clean[second])), 0.5),
        direction=_stable_direction(vectors[0]),
        confidence=max(0.0, min(1.0, (1.0 - angle_error) / (1.0 + fit.rmsd_after))),
        diagnostics={
            "order": 2,
            "rotation_angle_degree": math.degrees(angle),
            "subunit_fit_rmsd": fit.rmsd_after,
            "method": "paired_subunit_rotation",
        },
    )


def _ring_metrics(centers: Sequence[Point], center: Point, normal: Point):
    u, v = _plane_basis(normal)
    relative = [subtract(point, center) for point in centers]
    radii = [math.hypot(dot(point, u), dot(point, v)) for point in relative]
    mean_radius = sum(radii) / len(radii)
    if mean_radius <= 1.0e-9:
        raise GeometryError("subunit centroids do not define a cyclic ring")
    radial_std = math.sqrt(sum((value - mean_radius) ** 2 for value in radii) / len(radii))
    radial_cv = radial_std / mean_radius
    angles = sorted(math.atan2(dot(point, v), dot(point, u)) % (2.0 * math.pi) for point in relative)
    spacings = [
        (angles[(index + 1) % len(angles)] - angles[index]) % (2.0 * math.pi)
        for index in range(len(angles))
    ]
    ideal = 2.0 * math.pi / len(angles)
    spacing_rms = math.sqrt(sum((spacing - ideal) ** 2 for spacing in spacings) / len(spacings))
    axial = [dot(point, normal) for point in relative]
    axial_std = math.sqrt(sum(value * value for value in axial) / len(axial))
    return mean_radius, radial_cv, spacing_rms, ideal, axial_std


def _cn_axis(clean: Mapping[str, Sequence[Point]], labels: Sequence[str]) -> AxisResult:
    centers = [centroid(clean[label]) for label in labels]
    values, vectors, approximate_center = covariance_eigensystem(centers)
    normal = _stable_direction(vectors[2])
    center = _fit_circle_center(centers, normal, approximate_center)
    mean_radius, radial_cv, spacing_rms, ideal, axial_std = _ring_metrics(
        centers, center, normal
    )
    point_counts = [len(clean[label]) for label in labels]
    mean_count = sum(point_counts) / len(point_counts)
    size_cv = (
        math.sqrt(sum((count - mean_count) ** 2 for count in point_counts) / len(point_counts))
        / mean_count
    )
    radial_score = max(0.0, 1.0 - radial_cv)
    spacing_score = max(0.0, 1.0 - spacing_rms / ideal)
    pitch_score = max(0.0, 1.0 - axial_std / mean_radius)
    size_score = max(0.0, 1.0 - size_cv)
    confidence = radial_score * spacing_score * pitch_score * size_score
    return AxisResult(
        source="cyclic",
        center=center,
        direction=normal,
        confidence=max(0.0, min(1.0, confidence)),
        eigenvalues=values,
        diagnostics={
            "order": len(labels),
            "subunits": labels,
            "mean_radius": mean_radius,
            "radial_cv": radial_cv,
            "angular_spacing_rms_degree": math.degrees(spacing_rms),
            "axial_pitch_rms": axial_std,
            "subunit_point_counts": point_counts,
            "subunit_size_cv": size_cv,
            "sequence_compatibility": "not_available_from_coordinate_clouds",
            "classification": "helical" if axial_std / mean_radius > 0.15 else "cyclic",
        },
    )


def cyclic_axis(subunits: Mapping[str, Sequence[Sequence[float]]]) -> AxisResult:
    """Estimate a Cn axis from equally weighted subunit coordinate clouds."""
    if len(subunits) < 2:
        raise GeometryError("at least two subunits are required")
    labels = list(subunits)
    clean = {
        label: [tuple(map(float, point)) for point in subunits[label]]
        for label in labels
    }
    if any(not points for points in clean.values()):
        raise GeometryError("every subunit must contain coordinates")
    result = _c2_axis(clean, labels) if len(clean) == 2 else _cn_axis(clean, labels)
    return AxisResult(
        **{
            **result.__dict__,
            "input_hash": _json_sha256(
                {
                    label: _point_payload(clean[label])
                    for label in sorted(clean)
                }
            ),
            "parameters": {"order": len(clean), "subunits": list(labels)},
        }
    )


def _kmeans_two(values: Sequence[float]) -> Tuple[List[int], Tuple[float, float]]:
    if len(values) < 2:
        raise GeometryError("two clusters require at least two values")
    centers = [min(values), max(values)]
    if abs(centers[1] - centers[0]) <= 1.0e-9:
        raise GeometryError("membrane boundary layers are not separable")
    assignments = [0] * len(values)
    for _ in range(100):
        updated = [0 if abs(value - centers[0]) <= abs(value - centers[1]) else 1 for value in values]
        new_centers = []
        for label in (0, 1):
            group = [value for value, assignment in zip(values, updated) if assignment == label]
            if not group:
                raise GeometryError("membrane boundary clustering produced an empty layer")
            new_centers.append(sum(group) / len(group))
        if updated == assignments and max(abs(a - b) for a, b in zip(centers, new_centers)) < 1.0e-10:
            assignments = updated
            centers = new_centers
            break
        assignments, centers = updated, new_centers
    if centers[0] > centers[1]:
        assignments = [1 - assignment for assignment in assignments]
        centers.reverse()
    return assignments, (centers[0], centers[1])


def _anvil_dummy_atoms(text: str) -> List[Point]:
    dummy = []
    for line in str(text).splitlines():
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        if line[17:20].strip().upper() != "DUM" and line[12:16].strip().upper() != "DUM":
            continue
        try:
            dummy.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
        except ValueError as exc:
            raise GeometryError(f"invalid DUM coordinate line: {line}") from exc
    if len(dummy) < 6:
        raise GeometryError("ANVIL output must contain at least six DUM boundary atoms")
    return dummy


def _fit_anvil_plane(points: Sequence[Point], candidate: Point):
    values, vectors, center = covariance_eigensystem(points)
    if values[1] <= max(values[0] * 1.0e-8, 1.0e-10):
        raise GeometryError("ANVIL boundary layer is collinear")
    normal = normalize(vectors[2])
    if dot(normal, candidate) < 0.0:
        normal = scale(normal, -1.0)
    offsets = [dot(subtract(point, center), normal) for point in points]
    residual = math.sqrt(sum(value * value for value in offsets) / len(offsets))
    return center, normal, residual


def _evaluate_anvil_candidate(dummy: Sequence[Point], candidate: Point):
    projections = [dot(point, candidate) for point in dummy]
    assignments, _ = _kmeans_two(projections)
    groups = [
        [point for point, assignment in zip(dummy, assignments) if assignment == label]
        for label in (0, 1)
    ]
    if min(len(group) for group in groups) < 3:
        raise GeometryError("ANVIL boundary layer has fewer than three atoms")
    fits = [_fit_anvil_plane(group, candidate) for group in groups]
    centers = [fit[0] for fit in fits]
    normals = [fit[1] for fit in fits]
    residuals = [fit[2] for fit in fits]
    parallel = dot(normals[0], normals[1])
    normal = normalize(add(normals[0], normals[1]))
    alignment = abs(dot(normal, normalize(candidate)))
    separation = subtract(centers[1], centers[0])
    thickness = abs(dot(separation, normal))
    if parallel < 0.9 or alignment < 0.8:
        raise GeometryError("ANVIL boundary layers are not parallel to one separation axis")
    if thickness <= max(1.0e-3, 4.0 * max(residuals)):
        raise GeometryError("ANVIL boundary layers are not distinctly separated")
    score = sum(residuals) + (1.0 - parallel) + (1.0 - alignment)
    return score, centers, normal, residuals, thickness, parallel


def parse_anvil_pdb(text: str) -> Tuple[AxisResult, float]:
    """Parse two distinct, approximately parallel ANVIL ``DUM`` planes."""
    dummy = _anvil_dummy_atoms(text)
    _, candidate_vectors, _ = covariance_eigensystem(dummy)
    candidates = []
    for raw_candidate in candidate_vectors:
        candidate = normalize(raw_candidate)
        try:
            candidates.append(_evaluate_anvil_candidate(dummy, candidate))
        except GeometryError:
            continue
    if not candidates:
        raise GeometryError("could not separate two ANVIL membrane boundary planes")
    _, plane_centers, normal, plane_residuals, thickness, parallel = min(
        candidates, key=lambda value: value[0]
    )
    normal = _stable_direction(normal)
    if dot(subtract(plane_centers[1], plane_centers[0]), normal) < 0.0:
        plane_centers.reverse()
    center = scale(add(plane_centers[0], plane_centers[1]), 0.5)
    confidence = parallel / (1.0 + sum(plane_residuals))
    return (
        AxisResult(
            source="anvil",
            center=center,
            direction=normal,
            confidence=confidence,
            diagnostics={
                "dummy_atom_count": len(dummy),
                "plane_centers": [list(value) for value in plane_centers],
                "plane_fit_rms": plane_residuals,
                "plane_normal_parallel_cosine": parallel,
                "thickness": thickness,
            },
            input_hash=hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
            parameters={"parser": "anvil_dum_planes"},
        ),
        thickness,
    )


def _pdb_coordinate_map(text: str) -> Dict[Tuple[str, ...], Point]:
    records: Dict[Tuple[str, ...], Point] = {}
    for line_number, line in enumerate(str(text).splitlines(), start=1):
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        atom_name = line[12:16].strip()
        residue_name = line[17:20].strip()
        if atom_name.upper() == "DUM" or residue_name.upper() == "DUM":
            continue
        key = (
            line[21:22],
            line[22:26].strip(),
            line[26:27],
            residue_name,
            atom_name,
            line[16:17],
            line[72:76].strip(),
        )
        if key in records:
            raise GeometryError(
                "protein coordinate identity is ambiguous on PDB line %d: %r"
                % (line_number, key)
            )
        try:
            records[key] = (
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            )
        except ValueError as exc:
            raise GeometryError(
                "invalid protein coordinate on PDB line %d" % line_number
            ) from exc
    return records


def _reframe_anvil_result(
    input_pdb: str,
    output_pdb: str,
    *,
    rmsd_tolerance: float = 0.25,
) -> Tuple[AxisResult, float]:
    """Map ANVIL planes back to the input frame and validate protein identity."""
    result, thickness = parse_anvil_pdb(output_pdb)
    input_atoms = _pdb_coordinate_map(input_pdb)
    output_atoms = _pdb_coordinate_map(output_pdb)
    diagnostics = dict(result.diagnostics)
    diagnostics.update(
        {
            "input_sha256": hashlib.sha256(input_pdb.encode("utf-8")).hexdigest(),
            "output_sha256": hashlib.sha256(output_pdb.encode("utf-8")).hexdigest(),
        }
    )
    if input_atoms and not output_atoms:
        raise GeometryError(
            "ANVIL output contains no protein coordinates for identity validation"
        )
    if not input_atoms:
        diagnostics.update(
            {
                "protein_identity_validation": "unavailable",
                "protein_atom_count": 0,
                "protein_reoriented": None,
            }
        )
        return AxisResult(**{**result.__dict__, "diagnostics": diagnostics}), thickness
    if set(input_atoms) != set(output_atoms):
        missing = len(set(input_atoms) - set(output_atoms))
        unexpected = len(set(output_atoms) - set(input_atoms))
        raise GeometryError(
            "ANVIL output protein identity differs from input "
            f"(missing={missing}, unexpected={unexpected})"
        )
    keys = sorted(input_atoms)
    if len(keys) < 3:
        raise GeometryError("ANVIL identity validation requires at least three protein atoms")
    fit = fit_rigid_transform(
        [input_atoms[key] for key in keys],
        [output_atoms[key] for key in keys],
    )
    tolerance = as_float(rmsd_tolerance, minimum=0.0, name="rmsd_tolerance")
    if fit.rmsd_after > tolerance:
        raise GeometryError(
            f"ANVIL changed protein coordinates non-rigidly: fitted RMSD "
            f"{fit.rmsd_after:.4f} > {tolerance:.4f}"
        )
    inverse = rigid_inverse(fit.matrix)
    center = apply_matrix(inverse, result.center)
    endpoint = apply_matrix(inverse, add(result.center, result.direction))
    direction = _stable_direction(subtract(endpoint, center))
    output_centers = result.diagnostics.get("plane_centers", ())
    plane_centers = [apply_matrix(inverse, value) for value in output_centers]
    if dot(subtract(plane_centers[1], plane_centers[0]), direction) < 0.0:
        plane_centers.reverse()
    diagnostics.update(
        {
            "plane_centers": [list(value) for value in plane_centers],
            "protein_identity_validation": "exact",
            "protein_atom_count": len(keys),
            "protein_reoriented": fit.rmsd_before > tolerance,
            "protein_rmsd_before": fit.rmsd_before,
            "protein_rmsd_after_rigid_fit": fit.rmsd_after,
            "output_from_input_matrix": list(fit.matrix),
            "planes_mapped_to_input_frame": True,
        }
    )
    return (
        AxisResult(
            source="anvil",
            center=center,
            direction=direction,
            confidence=result.confidence,
            diagnostics=diagnostics,
            input_hash=diagnostics["input_sha256"],
            parameters={
                "parser": "anvil_dum_planes",
                "rmsd_tolerance": tolerance,
            },
            backend=result.backend,
            warnings=result.warnings,
        ),
        thickness,
    )


def run_anvil_executable(
    executable: str,
    input_pdb: str,
    *,
    arguments: str = "{input}",
    timeout: float = 120.0,
) -> Tuple[str, Dict[str, object]]:
    """Run an explicitly configured ANVIL command without a shell."""
    configured = os.path.expanduser(str(executable).strip())
    resolved = (
        os.path.abspath(configured)
        if os.path.sep in configured or (os.altsep and os.altsep in configured)
        else shutil.which(configured)
    )
    if not configured or not resolved or not os.path.isfile(resolved):
        raise ValueError("ANVIL executable does not exist or is not on PATH")
    timeout_value = as_float(timeout, minimum=0.1, name="timeout")
    if timeout_value > 3600.0:
        raise ValueError("timeout must be <= 3600 seconds")
    template = shlex.split(str(arguments), posix=os.name != "nt")
    if not template or not any("{input}" in value for value in template):
        raise ValueError("ANVIL arguments must include the {input} placeholder")
    for value in template:
        reduced = value.replace("{input}", "").replace("{output}", "")
        if "{" in reduced or "}" in reduced:
            raise ValueError("ANVIL arguments contain an unsupported placeholder")
    with tempfile.TemporaryDirectory(prefix="pymolplugins_anvil_") as directory:
        input_path = os.path.join(directory, "input.pdb")
        output_path = os.path.join(directory, "output.pdb")
        with open(input_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(str(input_pdb))
            if not str(input_pdb).endswith("\n"):
                handle.write("\n")
        argv = [
            value.replace("{input}", input_path).replace("{output}", output_path)
            for value in template
        ]
        completed = subprocess.run(
            [resolved] + argv,
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=timeout_value,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
            raise RuntimeError(
                f"ANVIL exited with code {completed.returncode}: {detail}"
            )
        candidates = []
        if os.path.isfile(output_path):
            candidates.append(("output_placeholder", output_path))
        for filename in sorted(os.listdir(directory)):
            path = os.path.join(directory, filename)
            if path not in {input_path, output_path} and filename.lower().endswith(".pdb"):
                candidates.append(("generated_pdb", path))
        output_text = ""
        output_source = ""
        for label, path in candidates:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                value = handle.read()
            if _anvil_dummy_atoms_or_empty(value):
                output_text, output_source = value, label
                break
        if not output_text and _anvil_dummy_atoms_or_empty(completed.stdout):
            output_text, output_source = completed.stdout, "stdout"
        if not output_text:
            raise RuntimeError("ANVIL produced no PDB output containing DUM boundary atoms")
        provenance = {
            "executable": resolved,
            "arguments": [
                value.replace(input_path, "{input}").replace(output_path, "{output}")
                for value in argv
            ],
            "returncode": completed.returncode,
            "output_source": output_source,
            "stderr_tail": completed.stderr.strip()[-1000:],
        }
        return output_text, provenance


def _anvil_dummy_atoms_or_empty(text: str) -> bool:
    try:
        _anvil_dummy_atoms(text)
        return True
    except GeometryError:
        return False


def membrane_corners(center: Point, normal: Point, width: float, height: float) -> Tuple[Point, ...]:
    u, v = _plane_basis(normal)
    hu, hv = width / 2.0, height / 2.0
    return (
        add(center, add(scale(u, -hu), scale(v, -hv))),
        add(center, add(scale(u, hu), scale(v, -hv))),
        add(center, add(scale(u, hu), scale(v, hv))),
        add(center, add(scale(u, -hu), scale(v, hv))),
    )


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is not available in this Python process")
    return cmd


_ELEMENT_MASSES = {
    "H": 1.008,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998,
    "P": 30.974,
    "S": 32.06,
    "CL": 35.45,
    "K": 39.098,
    "CA": 40.078,
    "MN": 54.938,
    "FE": 55.845,
    "CO": 58.933,
    "NI": 58.693,
    "CU": 63.546,
    "ZN": 65.38,
    "BR": 79.904,
    "I": 126.904,
}


def _selection_expression(selection: str, atoms: str) -> str:
    mode = str(atoms).strip().lower()
    if mode in {"ca", "alpha"}:
        return f"({selection}) and name CA"
    if mode in {"heavy", "all-heavy", "all_heavy"}:
        return f"({selection}) and not hydro"
    if mode in {"all", "*"}:
        return f"({selection})"
    return f"({selection}) and ({atoms})"


def _selection_samples(
    selection: str, atoms: str = "ca", state: int = 1
) -> List[Tuple[Point, str]]:
    expression = _selection_expression(selection, atoms)
    model = _require_cmd().get_model(expression, state)
    matrices: Dict[str, Tuple[float, ...]] = {}
    owners = list(_require_cmd().get_object_list(expression) or [])
    result: List[Tuple[Point, str]] = []
    for atom in getattr(model, "atom", ()) or ():
        owner = str(getattr(atom, "model", "") or "")
        if not owner and len(owners) == 1:
            owner = owners[0]
        if owner and owner not in matrices:
            try:
                matrix = _require_cmd().get_object_matrix(owner, state, incl_ttt=1)
            except Exception:
                matrix = None
            if matrix is None:
                matrix = ttt_to_homogeneous(
                    _require_cmd().get_object_ttt(owner, quiet=1)
                )
            matrices[owner] = tuple(float(value) for value in matrix)
        coordinate = tuple(float(value) for value in atom.coord)
        coordinate = apply_matrix(matrices[owner], coordinate) if owner else coordinate
        element = str(
            getattr(atom, "symbol", "") or getattr(atom, "elem", "") or ""
        ).strip().upper()
        result.append((coordinate, element))
    return result


def _selection_coords(selection: str, atoms: str = "ca", state: int = 1) -> List[Point]:
    return [coordinate for coordinate, _ in _selection_samples(selection, atoms, state)]


def _pca_inputs(
    selection: str, atoms: str, state: int, weighting: object
) -> Tuple[List[Point], Optional[List[float]], str]:
    samples = _selection_samples(selection, atoms, state)
    points = [coordinate for coordinate, _ in samples]
    mode = str(weighting).strip().lower().replace("-", "_")
    if mode in {"", "equal", "uniform", "residue"}:
        return points, None, "equal"
    if mode not in {"mass", "mass_weighted"}:
        raise ValueError("weighting must be equal or mass")
    unknown = sorted({element or "<blank>" for _, element in samples if element not in _ELEMENT_MASSES})
    if unknown:
        raise GeometryError(
            "mass weighting has unknown element(s): %s" % ", ".join(unknown)
        )
    return points, [_ELEMENT_MASSES[element] for _, element in samples], "mass"


def _draw_axis(result: AxisResult, name: str, length: float = 50.0, radius: float = 0.4, color: str = "yellow") -> None:
    from pymol import cgo

    direction = normalize(result.direction)
    start = subtract(result.center, scale(direction, length / 2.0))
    end = add(result.center, scale(direction, length / 2.0))
    rgb = _require_cmd().get_color_tuple(color)
    obj = [cgo.CYLINDER, *start, *end, radius, *rgb, *rgb]
    _require_cmd().delete(name)
    _require_cmd().load_cgo(obj, name)


def _target_axis(value: object) -> Point:
    text = str(value).strip().lower()
    axes = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
    return axes[text] if text in axes else parse_vector(value)


def _parse_two_points(value: object) -> Tuple[Point, Point]:
    text = str(value).strip().replace("|", ",").replace(";", ",")
    fields = [field for field in text.replace(" ", ",").split(",") if field]
    if len(fields) != 6:
        raise GeometryError(
            "two_points axis must contain six numbers: x1,y1,z1,x2,y2,z2"
        )
    try:
        numbers = [float(field) for field in fields]
    except ValueError as exc:
        raise GeometryError("two_points axis contains a non-numeric value") from exc
    if not all(math.isfinite(value) for value in numbers):
        raise GeometryError("two_points axis contains a non-finite value")
    return (tuple(numbers[:3]), tuple(numbers[3:]))  # type: ignore[return-value]


def _rotate_vector(rotation: Sequence[float], vector: Sequence[float]) -> Point:
    if len(rotation) != 9:
        raise GeometryError("rotation must contain nine values")
    return tuple(
        sum(float(rotation[row * 3 + column]) * float(vector[column]) for column in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def _axis_angle_rotation(axis: Sequence[float], angle: float) -> Tuple[float, ...]:
    x, y, z = normalize(axis)
    cosine = math.cos(float(angle))
    sine = math.sin(float(angle))
    one_minus = 1.0 - cosine
    return (
        cosine + x * x * one_minus,
        x * y * one_minus - z * sine,
        x * z * one_minus + y * sine,
        y * x * one_minus + z * sine,
        cosine + y * y * one_minus,
        y * z * one_minus - x * sine,
        z * x * one_minus - y * sine,
        z * y * one_minus + x * sine,
        cosine + z * z * one_minus,
    )


def _mat3_multiply(left: Sequence[float], right: Sequence[float]) -> Tuple[float, ...]:
    if len(left) != 9 or len(right) != 9:
        raise GeometryError("3x3 matrix multiplication requires nine values")
    return tuple(
        sum(left[row * 3 + inner] * right[inner * 3 + column] for inner in range(3))
        for row in range(3)
        for column in range(3)
    )


def _project_perpendicular(vector: Sequence[float], axis: Sequence[float]) -> Point:
    normal = normalize(axis)
    return normalize(subtract(vector, scale(normal, dot(vector, normal))))


def orientation_rotation(
    source_axis: Sequence[float],
    target_axis: Sequence[float],
    secondary_source: Optional[Sequence[float]] = None,
    secondary_target: Optional[Sequence[float]] = None,
) -> Tuple[Tuple[float, ...], bool]:
    """Align a primary axis and optionally fix roll with projected directions."""
    primary = rotation_from_to(source_axis, target_axis)
    if secondary_source is None:
        return primary, False
    target = normalize(target_axis)
    source_secondary = _project_perpendicular(secondary_source, source_axis)
    rotated_secondary = _project_perpendicular(
        _rotate_vector(primary, source_secondary), target
    )
    desired = (
        _plane_basis(target)[0]
        if secondary_target is None
        else _project_perpendicular(secondary_target, target)
    )
    angle = math.atan2(
        dot(target, cross(rotated_secondary, desired)),
        max(-1.0, min(1.0, dot(rotated_secondary, desired))),
    )
    roll = _axis_angle_rotation(target, angle)
    return _mat3_multiply(roll, primary), True


def _move_objects(selection: str, move: object) -> List[str]:
    requested = split_names(move)
    if requested:
        return expand_group_names(_require_cmd(), requested)
    return list(dict.fromkeys(_require_cmd().get_object_list(f"({selection})") or []))


def _set_ttt_baseline(name: str, value: Optional[Sequence[float]]) -> None:
    matrix = ttt_to_homogeneous(value)
    _require_cmd().set_object_ttt(
        name, list(homogeneous_to_ttt(matrix)), quiet=1
    )


def _rollback_orientation(
    baselines: Mapping[str, Optional[Sequence[float]]], names: Iterable[str]
) -> List[str]:
    failures = []
    for name in reversed(tuple(names)):
        try:
            _set_ttt_baseline(name, baselines[name])
        except Exception:
            failures.append(name)
    return failures


def _apply_orientation(matrix: Sequence[float], objects: Sequence[str]) -> None:
    global _ORIENTATION_UNDO
    unique_objects = tuple(dict.fromkeys(objects))
    if not unique_objects:
        raise ValueError("no move objects resolved")
    baselines = {}
    for name in unique_objects:
        baseline = _require_cmd().get_object_ttt(name, quiet=1)
        baselines[name] = (
            None if baseline is None else tuple(float(value) for value in baseline)
        )
    previous_undo = dict(_ORIENTATION_UNDO)
    attempted = []
    try:
        for name in unique_objects:
            attempted.append(name)
            current = ttt_to_homogeneous(baselines[name])
            _require_cmd().set_object_ttt(
                name,
                list(homogeneous_to_ttt(mat4_multiply(matrix, current))),
                quiet=1,
            )
    except Exception as exc:
        rollback_failures = _rollback_orientation(baselines, attempted)
        _ORIENTATION_UNDO = previous_undo
        detail = (
            f"; rollback failed for: {', '.join(rollback_failures)}"
            if rollback_failures
            else ""
        )
        raise RuntimeError(f"orientation commit failed: {exc}{detail}") from exc
    _ORIENTATION_UNDO = baselines


def pp_orientation_undo() -> Dict[str, object]:
    try:
        if not _ORIENTATION_UNDO:
            raise RuntimeError("no committed orientation to undo")
        baselines = dict(_ORIENTATION_UNDO)
        current = {}
        for name in baselines:
            value = _require_cmd().get_object_ttt(name, quiet=1)
            current[name] = (
                None if value is None else tuple(float(item) for item in value)
            )
        restored = []
        try:
            for name, value in baselines.items():
                restored.append(name)
                _set_ttt_baseline(name, value)
        except Exception as exc:
            rollback_failures = _rollback_orientation(current, restored)
            detail = (
                f"; rollback failed for: {', '.join(rollback_failures)}"
                if rollback_failures
                else ""
            )
            raise RuntimeError(f"orientation undo failed: {exc}{detail}") from exc
        _ORIENTATION_UNDO.clear()
        return {"status": "restored", "objects": restored}
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_axis_pca(
    selection="all",
    atoms="ca",
    axis="long",
    name="pp_pca_axis",
    state=1,
    draw=1,
    weighting="equal",
) -> Dict[str, object]:
    try:
        points, weights, weighting_mode = _pca_inputs(
            str(selection), str(atoms), as_int(state, minimum=1, name="state"), weighting
        )
        result = pca_axis(points, axis, weights)
        diagnostics = dict(result.diagnostics)
        diagnostics.update({"atom_mode": str(atoms), "weighting": weighting_mode})
        result = AxisResult(
            **{
                **result.__dict__,
                "input_selection": str(selection),
                "diagnostics": diagnostics,
                "parameters": {
                    "selection": str(selection),
                    "atoms": str(atoms),
                    "axis": str(axis),
                    "weighting": weighting_mode,
                    "state": as_int(state, minimum=1, name="state"),
                },
            }
        )
        _AXES[str(name)] = result
        if as_bool(draw):
            _draw_axis(result, str(name))
        print(f"PCA axis {name}: confidence={result.confidence:.3f}")
        return result.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_axis_vector(
    vector="0,0,1",
    center="",
    selection="all",
    name="pp_vector_axis",
    length=50.0,
    radius=0.4,
    color="yellow",
    state=1,
    draw=1,
) -> Dict[str, object]:
    try:
        if str(center).strip():
            axis_center = parse_vector(center)
            input_selection = ""
        else:
            points = _selection_coords(
                str(selection), "ca", as_int(state, minimum=1, name="state")
            )
            if not points:
                points = _selection_coords(str(selection), "heavy", as_int(state, minimum=1, name="state"))
            axis_center = centroid(points)
            input_selection = str(selection)
        result = vector_axis(
            _target_axis(vector),
            axis_center,
            source="vector",
            input_selection=input_selection,
        )
        result = AxisResult(
            **{
                **result.__dict__,
                "parameters": {
                    "vector": list(_target_axis(vector)),
                    "center": list(axis_center),
                    "selection": input_selection,
                    "state": as_int(state, minimum=1, name="state"),
                },
            }
        )
        key = str(name)
        _AXES[key] = result
        if as_bool(draw):
            _draw_axis(
                result,
                key,
                as_float(length, minimum=0.1, name="length"),
                as_float(radius, minimum=0.001, name="radius"),
                str(color),
            )
        return result.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_axis_two_points(
    points,
    name="pp_two_point_axis",
    length=50.0,
    radius=0.4,
    color="yellow",
    draw=1,
) -> Dict[str, object]:
    try:
        parsed_points = _parse_two_points(points)
        result = two_point_axis(parsed_points)
        result = AxisResult(
            **{
                **result.__dict__,
                "parameters": {"points": _point_payload(parsed_points)},
            }
        )
        key = str(name)
        _AXES[key] = result
        if as_bool(draw):
            _draw_axis(
                result,
                key,
                as_float(length, minimum=0.1, name="length"),
                as_float(radius, minimum=0.001, name="radius"),
                str(color),
            )
        return result.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_orient_pca(
    selection="all",
    move="",
    target_axis="z",
    atoms="ca",
    axis="long",
    center=1,
    min_confidence=0.05,
    weighting="equal",
    secondary_selection="",
    secondary_target="auto",
    force=0,
    state=1,
    commit=0,
) -> Dict[str, object]:
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        objects = _move_objects(selection, move)
        if not objects:
            raise ValueError("no move objects resolved")
        points, weights, weighting_mode = _pca_inputs(
            str(selection), str(atoms), parsed_state, weighting
        )
        result = pca_axis(points, axis, weights)
        threshold = as_float(min_confidence, minimum=0.0, maximum=1.0, name="min_confidence")
        if result.confidence < threshold and not as_bool(force):
            raise GeometryError(
                f"PCA axis confidence {result.confidence:.3f} is below {threshold:.3f}; use force=1"
            )
        secondary_direction = None
        secondary_value = str(secondary_selection).strip()
        if secondary_value:
            secondary_points = _selection_coords(secondary_value, "ca", parsed_state)
            if not secondary_points:
                secondary_points = _selection_coords(secondary_value, "heavy", parsed_state)
            secondary_direction = subtract(centroid(secondary_points), result.center)
        target_direction = _target_axis(target_axis)
        target_secondary = (
            None
            if str(secondary_target).strip().lower() in {"", "auto"}
            else _target_axis(secondary_target)
        )
        rotation, roll_constrained = orientation_rotation(
            result.direction,
            target_direction,
            secondary_direction,
            target_secondary,
        )
        diagnostics = dict(result.diagnostics)
        diagnostics.update(
            {
                "atom_mode": str(atoms),
                "weighting": weighting_mode,
                "roll_constrained": roll_constrained,
                "secondary_selection": secondary_value,
            }
        )
        result = AxisResult(
            **{
                **result.__dict__,
                "input_selection": str(selection),
                "secondary_direction": (
                    _project_perpendicular(secondary_direction, result.direction)
                    if secondary_direction is not None
                    else None
                ),
                "diagnostics": diagnostics,
                "parameters": {
                    "selection": str(selection),
                    "move": list(objects),
                    "target_axis": list(target_direction),
                    "atoms": str(atoms),
                    "axis": str(axis),
                    "center_at_origin": as_bool(center),
                    "weighting": weighting_mode,
                    "secondary_selection": secondary_value,
                    "secondary_target": (
                        "auto" if target_secondary is None else list(target_secondary)
                    ),
                    "state": parsed_state,
                },
            }
        )
        target_center = (0.0, 0.0, 0.0) if as_bool(center) else result.center
        matrix = compose_about_center(rotation, result.center, target_center)
        if as_bool(commit):
            _apply_orientation(matrix, objects)
        return {
            "schema": "pymolplugins.orientation/1.0",
            "axis": result.to_dict(),
            "matrix": list(matrix),
            "move_objects": objects,
            "committed": as_bool(commit),
            "roll_constrained": roll_constrained,
        }
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def _subunit_clouds(selection: str, subunits: object, state: int) -> Dict[str, List[Point]]:
    labels = split_names(subunits)
    if labels:
        clouds = {label: _selection_coords(f"({selection}) and ({label})", "ca", state) for label in labels}
    else:
        # Group directly from one atom stream. This preserves a real blank
        # chain and multi-character mmCIF chain IDs without constructing a
        # fragile ``chain`` selection expression.
        model = _require_cmd().get_model(f"({selection}) and name CA", state)
        owners = list(_require_cmd().get_object_list(selection) or [])
        matrices: Dict[str, Tuple[float, ...]] = {}
        clouds = {}
        for atom in getattr(model, "atom", ()) or ():
            chain = str(getattr(atom, "chain", "") or "")
            owner = str(getattr(atom, "model", "") or "")
            if not owner and len(owners) == 1:
                owner = owners[0]
            if owner and owner not in matrices:
                try:
                    matrix = _require_cmd().get_object_matrix(owner, state, incl_ttt=1)
                except Exception:
                    matrix = None
                if matrix is None:
                    matrix = ttt_to_homogeneous(
                        _require_cmd().get_object_ttt(owner, quiet=1)
                    )
                matrices[owner] = tuple(float(value) for value in matrix)
            coordinate = tuple(float(value) for value in atom.coord)
            if owner:
                coordinate = apply_matrix(matrices[owner], coordinate)
            clouds.setdefault(chain or "<blank>", []).append(coordinate)
    return {label: points for label, points in clouds.items() if points}


def pp_axis_cyclic(
    selection="all",
    subunits="",
    order="auto",
    name="pp_cyclic_axis",
    state=1,
    draw=1,
    min_confidence=0.5,
    force=0,
) -> Dict[str, object]:
    try:
        clouds = _subunit_clouds(selection, subunits, as_int(state, minimum=1, name="state"))
        auto_order = str(order).strip().lower() == "auto"
        if not auto_order and as_int(order, minimum=2, name="order") != len(clouds):
            raise ValueError(f"requested order {order} but resolved {len(clouds)} subunits")
        result = cyclic_axis(clouds)
        threshold = as_float(
            min_confidence, minimum=0.0, maximum=1.0, name="min_confidence"
        )
        classification = result.diagnostics.get("classification", "cyclic")
        explicit_subunits = bool(split_names(subunits)) or not auto_order
        if classification != "cyclic" and not as_bool(force):
            raise GeometryError(
                f"resolved subunits are classified as {classification}; use force=1"
            )
        if (
            result.confidence < threshold
            and not explicit_subunits
            and not as_bool(force)
        ):
            raise GeometryError(
                f"automatic cyclic-axis confidence {result.confidence:.3f} is below "
                f"{threshold:.3f}; pass explicit subunits/order or use force=1"
            )
        diagnostics = dict(result.diagnostics)
        diagnostics.update(
            {
                "explicit_subunits_or_order": explicit_subunits,
                "minimum_confidence": threshold,
            }
        )
        result = AxisResult(
            **{
                **result.__dict__,
                "input_selection": str(selection),
                "diagnostics": diagnostics,
                "parameters": {
                    "selection": str(selection),
                    "subunits": split_names(subunits),
                    "order": "auto" if auto_order else int(order),
                    "state": as_int(state, minimum=1, name="state"),
                    "min_confidence": threshold,
                    "force": as_bool(force),
                },
            }
        )
        _AXES[str(name)] = result
        if as_bool(draw):
            _draw_axis(result, str(name))
        print(
            f"Cyclic axis {name}: C{result.diagnostics.get('order')}, "
            f"confidence={result.confidence:.3f}, class={result.diagnostics.get('classification', 'cyclic')}"
        )
        return result.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_orient_cyclic(
    selection="all",
    subunits="",
    move="",
    target_axis="z",
    center=1,
    min_confidence=0.5,
    force=0,
    state=1,
    commit=0,
) -> Dict[str, object]:
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        clouds = _subunit_clouds(selection, subunits, parsed_state)
        objects = _move_objects(selection, move)
        if not objects:
            raise ValueError("no move objects resolved")
        result = cyclic_axis(clouds)
        threshold = as_float(
            min_confidence, minimum=0.0, maximum=1.0, name="min_confidence"
        )
        classification = result.diagnostics.get("classification", "cyclic")
        if not as_bool(force) and (
            result.confidence < threshold or classification != "cyclic"
        ):
            raise GeometryError(
                f"cyclic-axis confidence/classification is "
                f"{result.confidence:.3f}/{classification}; use force=1"
            )
        target_direction = _target_axis(target_axis)
        diagnostics = dict(result.diagnostics)
        diagnostics.update(
            {
                "minimum_confidence": threshold,
                "roll_constrained": False,
            }
        )
        result = AxisResult(
            **{
                **result.__dict__,
                "input_selection": str(selection),
                "diagnostics": diagnostics,
                "parameters": {
                    "selection": str(selection),
                    "subunits": split_names(subunits),
                    "move": list(objects),
                    "target_axis": list(target_direction),
                    "center_at_origin": as_bool(center),
                    "min_confidence": threshold,
                    "force": as_bool(force),
                    "state": parsed_state,
                },
            }
        )
        rotation = rotation_from_to(result.direction, target_direction)
        target_center = (0.0, 0.0, 0.0) if as_bool(center) else result.center
        matrix = compose_about_center(rotation, result.center, target_center)
        if as_bool(commit):
            _apply_orientation(matrix, objects)
        return {
            "schema": "pymolplugins.orientation/1.0",
            "axis": result.to_dict(),
            "matrix": list(matrix),
            "move_objects": objects,
            "committed": as_bool(commit),
            "roll_constrained": False,
        }
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def _auto_extent(points: Sequence[Point], center: Point, normal: Point, padding: float) -> Tuple[float, float]:
    u, v = _plane_basis(normal)
    projected_u = [dot(subtract(point, center), u) for point in points]
    projected_v = [dot(subtract(point, center), v) for point in points]
    return (
        max(10.0, max(projected_u) - min(projected_u) + 2.0 * padding),
        max(10.0, max(projected_v) - min(projected_v) + 2.0 * padding),
    )


def build_membrane_slab(
    points: Sequence[Point],
    axis_result: AxisResult,
    name: str = "pp_membrane",
    thickness: float = 30.0,
    width: Optional[float] = None,
    height: Optional[float] = None,
    padding: float = 8.0,
    center: Optional[Point] = None,
    source: Optional[str] = None,
) -> MembraneResult:
    """Build slab geometry without importing or mutating PyMOL."""
    clean = [tuple(map(float, point)) for point in points]
    if not clean:
        raise GeometryError("membrane slab requires at least one structure point")
    slab_center = axis_result.center if center is None else tuple(map(float, center))
    normal = normalize(axis_result.direction)
    slab_thickness = as_float(thickness, minimum=0.1, name="thickness")
    pad = as_float(padding, minimum=0.0, name="padding")
    auto_width, auto_height = _auto_extent(clean, slab_center, normal, pad)
    slab_width = auto_width if width is None else as_float(width, minimum=0.1, name="width")
    slab_height = auto_height if height is None else as_float(height, minimum=0.1, name="height")
    plane_centers = (
        subtract(slab_center, scale(normal, slab_thickness / 2.0)),
        add(slab_center, scale(normal, slab_thickness / 2.0)),
    )
    return MembraneResult(
        name=sanitize_name(name, fallback="pp_membrane"),
        center=slab_center,
        normal=normal,
        thickness=slab_thickness,
        width=slab_width,
        height=slab_height,
        plane_centers=plane_centers,
        source=source or axis_result.source,
        diagnostics={"axis_confidence": axis_result.confidence},
        input_hash=_json_sha256(
            {
                "points": _point_payload(clean),
                "axis_input_hash": axis_result.input_hash,
            }
        ),
        parameters={
            "thickness": slab_thickness,
            "width": slab_width,
            "height": slab_height,
            "padding": pad,
            "axis_source": source or axis_result.source,
        },
        warnings=axis_result.warnings,
    )


def _draw_membrane(result: MembraneResult, color: str, transparency: float) -> None:
    from pymol import cgo

    rgb = _require_cmd().get_color_tuple(color)
    alpha = 1.0 - transparency
    obj = []
    for plane_center in result.plane_centers:
        corners = membrane_corners(plane_center, result.normal, result.width, result.height)
        obj.extend([cgo.BEGIN, cgo.TRIANGLES, cgo.COLOR, *rgb, cgo.ALPHA, alpha, cgo.NORMAL, *result.normal])
        for index in (0, 1, 2, 0, 2, 3):
            obj.extend([cgo.VERTEX, *corners[index]])
        obj.append(cgo.END)
        obj.extend([cgo.BEGIN, cgo.LINES, cgo.COLOR, *rgb, cgo.ALPHA, 1.0])
        for index in range(4):
            obj.extend([cgo.VERTEX, *corners[index], cgo.VERTEX, *corners[(index + 1) % 4]])
        obj.append(cgo.END)
    object_name = f"{result.name}__planes"
    _require_cmd().delete(object_name)
    _require_cmd().load_cgo(obj, object_name)
    _require_cmd().group(result.name, object_name, action="add")


def _resolve_slab_axis(
    axis_source: object,
    axis: object,
    points: Sequence[Point],
    selection: str,
    subunits: object,
    state: int,
    force: object,
) -> Tuple[AxisResult, str]:
    raw_source = str(axis_source).strip()
    source = raw_source.lower()
    if source == "pca":
        return pca_axis(points, "long"), source
    if source == "cyclic":
        result = cyclic_axis(_subunit_clouds(selection, subunits, state))
        if (
            result.diagnostics.get("classification", "cyclic") != "cyclic"
            or result.confidence < 0.5
        ) and not as_bool(force):
            raise GeometryError(
                "resolved cyclic axis is low-confidence or non-cyclic; use force=1 to draw anyway"
            )
        return result, source
    if source == "vector":
        return vector_axis(_target_axis(axis or "z"), centroid(points)), source
    if source == "two_points":
        return two_point_axis(_parse_two_points(axis)), source
    if source == "anvil":
        path = os.path.abspath(os.path.expanduser(str(axis).strip()))
        if not str(axis).strip() or not os.path.isfile(path):
            raise ValueError("axis_source=anvil requires axis=<existing ANVIL output PDB>")
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            result, _ = parse_anvil_pdb(handle.read())
        return result, source
    if raw_source in _AXES:
        return _AXES[raw_source], raw_source
    raise ValueError(
        "axis_source must be pca, cyclic, vector, two_points, anvil, or a stored axis name"
    )


def _optional_extent(value: object, name: str) -> Optional[float]:
    if str(value).strip().lower() == "auto":
        return None
    return as_float(value, minimum=0.1, name=name)


def pp_membrane_slab(
    selection="all",
    axis_source="pca",
    axis="",
    center="",
    subunits="",
    thickness=30.0,
    width="auto",
    height="auto",
    padding=8.0,
    name="pp_membrane",
    color="gray70",
    transparency=0.65,
    force=0,
    state=1,
) -> Dict[str, object]:
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        points = _selection_coords(selection, "ca", parsed_state)
        if not points:
            points = _selection_coords(selection, "heavy", parsed_state)
        axis_result, source = _resolve_slab_axis(
            axis_source, axis, points, str(selection), subunits, parsed_state, force
        )
        result = build_membrane_slab(
            points,
            axis_result,
            name=str(name),
            thickness=thickness,
            width=_optional_extent(width, "width"),
            height=_optional_extent(height, "height"),
            padding=padding,
            center=parse_vector(center) if str(center).strip() else None,
            source=source,
        )
        _MEMBRANES[result.name] = result
        _draw_membrane(
            result,
            str(color),
            as_float(transparency, minimum=0.0, maximum=1.0, name="transparency"),
        )
        print(
            f"Membrane slab {result.name}: thickness={result.thickness:.2f}, "
            f"size={result.width:.2f}x{result.height:.2f}"
        )
        return result.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_membrane_from_anvil(
    input_or_selection: str,
    selection="all",
    executable="",
    arguments="{input}",
    timeout=120.0,
    name="pp_membrane_anvil",
    width="auto",
    height="auto",
    padding=8.0,
    color="gray70",
    transparency=0.65,
    state=1,
) -> Dict[str, object]:
    """Run configured ANVIL or parse output, then draw planes in the input frame."""
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        configured = str(executable).strip() or os.environ.get(
            "PYMOLPLUGINS_ANVIL_EXECUTABLE", ""
        ).strip()
        runner_provenance: Dict[str, object] = {}
        if configured:
            source_selection = str(input_or_selection).strip()
            if not source_selection:
                raise ValueError("input selection must not be empty")
            input_pdb = _require_cmd().get_pdbstr(
                f"({source_selection}) and name CA", state=parsed_state
            )
            if len(_pdb_coordinate_map(input_pdb)) < 3:
                raise GeometryError(
                    "ANVIL input selection must export at least three unambiguous CA atoms"
                )
            output_text, runner_provenance = run_anvil_executable(
                configured,
                input_pdb,
                arguments=str(arguments),
                timeout=as_float(timeout, minimum=0.1, name="timeout"),
            )
            axis_result, thickness = _reframe_anvil_result(input_pdb, output_text)
        else:
            path = os.path.abspath(os.path.expanduser(str(input_or_selection)))
            if not os.path.isfile(path):
                raise ValueError(
                    "ANVIL output PDB does not exist; pass executable=... to run it"
                )
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                output_text = handle.read()
            source_selection = str(selection)
            input_pdb = _require_cmd().get_pdbstr(
                f"({source_selection}) and name CA", state=parsed_state
            )
            if _pdb_coordinate_map(input_pdb):
                axis_result, thickness = _reframe_anvil_result(input_pdb, output_text)
            else:
                axis_result, thickness = parse_anvil_pdb(output_text)
        points = _selection_coords(source_selection, "ca", parsed_state)
        if not points:
            points = _selection_coords(source_selection, "heavy", parsed_state)
        pad = as_float(padding, minimum=0.0, name="padding")
        auto_width, auto_height = _auto_extent(points, axis_result.center, axis_result.direction, pad)
        diagnostics = dict(axis_result.diagnostics)
        if runner_provenance:
            diagnostics["runner"] = runner_provenance
        slab_width = (
            auto_width
            if str(width).strip().lower() == "auto"
            else as_float(width, minimum=0.1, name="width")
        )
        slab_height = (
            auto_height
            if str(height).strip().lower() == "auto"
            else as_float(height, minimum=0.1, name="height")
        )
        backend = {
            "name": "anvil" if configured else "anvil-output-parser",
            "version": "unknown" if configured else "1.0",
        }
        result = MembraneResult(
            name=sanitize_name(name, fallback="pp_membrane_anvil"),
            center=axis_result.center,
            normal=axis_result.direction,
            thickness=thickness,
            width=slab_width,
            height=slab_height,
            plane_centers=tuple(tuple(value) for value in axis_result.diagnostics["plane_centers"]),  # type: ignore[arg-type]
            source="anvil",
            diagnostics=diagnostics,
            input_hash=_json_sha256(
                {
                    "selection_points": _point_payload(points),
                    "axis_input_hash": axis_result.input_hash,
                }
            ),
            parameters={
                "input_or_selection": str(input_or_selection),
                "selection": source_selection,
                "mode": "executable" if configured else "precomputed_output",
                "arguments": str(arguments) if configured else None,
                "timeout": (
                    as_float(timeout, minimum=0.1, name="timeout")
                    if configured
                    else None
                ),
                "state": parsed_state,
                "width": slab_width,
                "height": slab_height,
                "padding": pad,
            },
            backend=backend,
            warnings=axis_result.warnings,
        )
        _MEMBRANES[result.name] = result
        _draw_membrane(
            result,
            str(color),
            as_float(transparency, minimum=0.0, maximum=1.0, name="transparency"),
        )
        return result.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_geometry_export(name: str, filename: str = "") -> Optional[str]:
    temporary_path = ""
    try:
        key = str(name)
        if key in _AXES:
            payload = _AXES[key].to_dict()
        elif key in _MEMBRANES:
            payload = _MEMBRANES[key].to_dict()
        else:
            raise ValueError(f"unknown axis or membrane result: {key}")
        path = os.path.abspath(os.path.expanduser(filename or f"{key}.json"))
        directory = os.path.dirname(path) or os.curdir
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as handle:
            temporary_path = handle.name
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = ""
        return path
    except Exception as exc:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        print(f"Error: {exc}")
        traceback.print_exc()
        return None


if cmd is not None:
    cmd.extend("pp_axis_pca", pp_axis_pca)
    cmd.extend("pp_axis_vector", pp_axis_vector)
    cmd.extend("pp_axis_two_points", pp_axis_two_points)
    cmd.extend("pp_orient_pca", pp_orient_pca)
    cmd.extend("pp_axis_cyclic", pp_axis_cyclic)
    cmd.extend("pp_orient_cyclic", pp_orient_cyclic)
    cmd.extend("pp_orientation_undo", pp_orientation_undo)
    cmd.extend("pp_membrane_slab", pp_membrane_slab)
    cmd.extend("pp_membrane_from_anvil", pp_membrane_from_anvil)
    cmd.extend("pp_geometry_export", pp_geometry_export)
