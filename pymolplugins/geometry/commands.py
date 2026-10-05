"""PCA/cyclic orientation and membrane-slab tools for PyMOL."""

from collections import OrderedDict
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
import time
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
    from pymolplugins.core.parameters import (
        as_bool,
        as_float,
        as_int,
        extend_command,
        sanitize_name,
        split_names,
    )
    from pymolplugins.core.pymol_groups import expand_names as expand_group_names
    from pymolplugins.core.sequence import parse_residue_identifier
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
    from pymolplugins.core.parameters import (  # type: ignore
        as_bool,
        as_float,
        as_int,
        extend_command,
        sanitize_name,
        split_names,
    )
    from pymolplugins.core.pymol_groups import expand_names as expand_group_names  # type: ignore
    from pymolplugins.core.sequence import parse_residue_identifier  # type: ignore


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


@dataclass(frozen=True)
class GeometryOutputOwnership:
    """One in-session geometry output bundle owned by this module."""

    id: str
    kind: str
    names: Tuple[str, ...]
    expected_types: Mapping[str, str]
    markers: Mapping[str, str]
    marker_backends: Mapping[str, str]


@dataclass(frozen=True)
class AxisSourceSnapshot:
    """Source state used to reject stale stored-axis reuse."""

    selection: str
    atoms: str
    state: int
    objects: Tuple[str, ...]
    object_ttt: Mapping[str, Optional[Tuple[float, ...]]]
    coordinate_hash: str


@dataclass(frozen=True)
class _SubunitCARecord:
    """One world-coordinate CA atom with explicit subunit/residue identity."""

    object: str
    segi: str
    chain: str
    resi: str
    resn: str
    alt: str
    index: int
    coord: Point


class _SubunitCloudMap(dict):
    """Coordinate-cloud mapping carrying Host-resolution diagnostics."""

    def __init__(self, *args, diagnostics=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.resolution_diagnostics = dict(diagnostics or {})


@dataclass
class OrientationRecord:
    """A committed orientation and the exact state needed for safe undo."""

    id: str
    created_at: float
    objects: Tuple[str, ...]
    baseline_ttt: Dict[str, Optional[Tuple[float, ...]]]
    committed_ttt: Dict[str, Optional[Tuple[float, ...]]]
    committed_fingerprint: str
    undone: bool = False


class GeometryOutputCollision(RuntimeError):
    """Raised when a requested output name is not plugin-owned."""

    def __init__(self, kind: str, collisions: Sequence[Mapping[str, str]]):
        self.kind = str(kind)
        self.collisions = tuple(dict(item) for item in collisions)
        rendered = ", ".join(
            "{} ({})".format(item["name"], item.get("type", "unknown"))
            for item in self.collisions
        )
        super().__init__(
            "geometry output name collision: {}; choose a different name".format(
                rendered
            )
        )


class StaleStoredAxis(RuntimeError):
    """Raised when a stored axis no longer describes its source selection."""

    def __init__(self, name: str, reasons: Sequence[str]):
        self.axis_name = str(name)
        self.reasons = tuple(str(reason) for reason in reasons)
        super().__init__(
            "stored axis {!r} is stale: {}; recompute the axis".format(
                self.axis_name, "; ".join(self.reasons)
            )
        )


class StaleOrientationUndo(RuntimeError):
    """Raised when undo would overwrite edits made after orientation."""

    def __init__(self, operation_id: str, objects: Sequence[str]):
        self.operation_id = str(operation_id)
        self.objects = tuple(str(name) for name in objects)
        super().__init__(
            "orientation objects changed after commit: {}; undo refused to "
            "overwrite external changes (use force=1)".format(", ".join(self.objects))
        )


_AXES: Dict[str, AxisResult] = {}
_MEMBRANES: Dict[str, MembraneResult] = {}
# Scene ownership is deliberately in-memory. If the plugin is reloaded while old
# outputs remain in PyMOL, those names are treated as user-owned and never deleted.
_GEOMETRY_OUTPUT_OWNERS: Dict[str, GeometryOutputOwnership] = {}
_AXIS_SOURCES: Dict[str, AxisSourceSnapshot] = {}
_ORIENTATION_UNDO: Dict[str, Optional[Tuple[float, ...]]] = {}
_ORIENTATION_RECORDS: "OrderedDict[str, OrientationRecord]" = OrderedDict()
_LAST_ORIENTATION_ID = ""
_ORIENTATION_RECORD_LIMIT = 32


def _geometry_error_result(exc: Exception) -> Dict[str, object]:
    expected = isinstance(
        exc,
        (
            GeometryOutputCollision,
            StaleStoredAxis,
            StaleOrientationUndo,
            GeometryError,
            ValueError,
        ),
    )
    result: Dict[str, object] = {
        "status": "error",
        "error": str(exc),
        "changed": False,
        "scene_changed": False if expected else None,
        "scene_preserved": expected,
    }
    if isinstance(exc, GeometryOutputCollision):
        result.update(
            {
                "error_code": "output_name_collision",
                "output_kind": exc.kind,
                "collisions": [dict(item) for item in exc.collisions],
                "guidance": "choose a different output name; user-owned names are never replaced",
            }
        )
    elif isinstance(exc, StaleStoredAxis):
        result.update(
            {
                "error_code": "stale_stored_axis",
                "axis_name": exc.axis_name,
                "stale_reasons": list(exc.reasons),
                "guidance": "recompute the stored axis after changing its source objects",
            }
        )
    elif isinstance(exc, StaleOrientationUndo):
        result.update(
            {
                "error_code": "stale_orientation_undo",
                "operation_id": exc.operation_id,
                "stale_objects": list(exc.objects),
                "guidance": "inspect the newer edits, or pass force=1 to overwrite them",
            }
        )
    elif isinstance(exc, GeometryError):
        result.update(
            {
                "error_code": "geometry_request_rejected",
                "guidance": (
                    "check the selected atoms, axis confidence, and explicit force choice"
                ),
            }
        )
    elif isinstance(exc, ValueError):
        result.update(
            {
                "error_code": "invalid_geometry_request",
                "guidance": "check axis source, selections, and command parameters",
            }
        )
    else:
        result.update(
            {
                "error_code": "geometry_internal_error",
                "guidance": "inspect the traceback and verify the PyMOL scene state",
            }
        )
    return result


def _report_geometry_error(exc: Exception) -> Dict[str, object]:
    result = _geometry_error_result(exc)
    print(
        "Geometry error: {} Next: {}.".format(
            exc, result.get("guidance", "inspect the command inputs")
        )
    )
    if not isinstance(
        exc,
        (
            GeometryOutputCollision,
            StaleStoredAxis,
            StaleOrientationUndo,
            GeometryError,
            ValueError,
        ),
    ):
        traceback.print_exc()
    return result


def _axis_command_payload(
    result: AxisResult, name: str, drawn: bool
) -> Dict[str, object]:
    ownership = _GEOMETRY_OUTPUT_OWNERS.get(str(name)) if drawn else None
    payload = result.to_dict()
    payload.update(
        {
            "status": "drawn" if drawn else "computed",
            "changed": bool(drawn),
            "scene_changed": bool(drawn),
            "name": str(name),
            "drawn": bool(drawn),
            "output_names": [str(name)] if drawn else [],
            "ownership_id": ownership.id if ownership is not None else None,
        }
    )
    return payload


def _orientation_command_payload(
    result: AxisResult,
    matrix: Sequence[float],
    objects: Sequence[str],
    operation: Optional[OrientationRecord],
    roll_constrained: bool,
    command_name: str,
) -> Dict[str, object]:
    committed = operation is not None
    next_command = (
        "pp_orientation_undo {}".format(operation.id)
        if operation is not None
        else "re-run {} with the same arguments and commit=1".format(command_name)
    )
    return {
        "schema": "pymolplugins.orientation/1.0",
        "status": "committed" if committed else "preview",
        "changed": committed,
        "scene_changed": committed,
        "axis": result.to_dict(),
        "matrix": list(matrix),
        "move_objects": list(objects),
        "committed": committed,
        "operation_id": operation.id if operation is not None else None,
        "committed_fingerprint": (
            operation.committed_fingerprint if operation is not None else None
        ),
        "roll_constrained": bool(roll_constrained),
        "next_command": next_command,
    }


def _membrane_command_payload(result: MembraneResult) -> Dict[str, object]:
    ownership = _GEOMETRY_OUTPUT_OWNERS.get(result.name)
    payload = result.to_dict()
    payload.update(
        {
            "status": "drawn",
            "changed": True,
            "scene_changed": True,
            "output_names": [result.name, "{}__planes".format(result.name)],
            "ownership_id": ownership.id if ownership is not None else None,
            "next_command": "delete {}; delete {}__planes".format(
                result.name, result.name
            ),
        }
    )
    return payload


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
        owner = str(getattr(atom, "model", "") or "").strip()
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


def _all_scene_names() -> List[str]:
    api = _require_cmd()
    try:
        names = list(api.get_names("all") or [])
    except TypeError:
        names = list(api.get_names() or [])
    try:
        names.extend(api.get_names("selections") or [])
    except Exception:
        pass
    return list(dict.fromkeys(str(name) for name in names))


def _scene_type(name: str) -> str:
    try:
        return str(_require_cmd().get_type(name) or "unknown")
    except Exception:
        return "unknown"


def _name_exists(name: str) -> bool:
    return str(name) in set(_all_scene_names())


def _unused_output_name(
    prefix: str, avoid: Sequence[str] = ()
) -> str:
    base = sanitize_name(prefix, fallback="__pp_geometry_stage")
    api = _require_cmd()
    forbidden = {str(name) for name in avoid}
    if hasattr(api, "get_unused_name"):
        candidate = str(api.get_unused_name(base))
        while candidate in forbidden:
            base = "{}_stage".format(base)
            candidate = str(api.get_unused_name(base))
        return candidate
    candidate = base
    index = 1
    existing = set(_all_scene_names())
    while candidate in existing or candidate in forbidden:
        candidate = "{}_{}".format(base, index)
        index += 1
    return candidate


def _preflight_output_bundle(
    kind: str,
    names: Sequence[str],
    expected_types: Mapping[str, str],
) -> Optional[GeometryOutputOwnership]:
    final_names = tuple(str(name) for name in names)
    existing = [name for name in final_names if _name_exists(name)]
    if not existing:
        for name in final_names:
            _GEOMETRY_OUTPUT_OWNERS.pop(name, None)
        return None

    owners = [_GEOMETRY_OUTPUT_OWNERS.get(name) for name in final_names]
    owner = owners[0] if owners else None
    marked_names = (
        [
            name
            for name in final_names
            if expected_types.get(name) == "object:cgo"
        ]
        if owner is not None
        else []
    )
    markers_match = bool(
        owner
        and marked_names
        and all(_output_marker_matches(owner, name) for name in marked_names)
    )
    is_owned_bundle = bool(
        owner
        and all(item is owner for item in owners)
        and owner.kind == str(kind)
        and owner.names == final_names
        and len(existing) == len(final_names)
        and all(_scene_type(name) == expected_types[name] for name in final_names)
        and markers_match
    )
    if is_owned_bundle:
        return owner
    collisions = [
        {
            "name": name,
            "type": _scene_type(name),
            "owner": (
                _GEOMETRY_OUTPUT_OWNERS[name].kind
                if name in _GEOMETRY_OUTPUT_OWNERS
                else "user"
            ),
        }
        for name in existing
    ]
    raise GeometryOutputCollision(kind, collisions)


def _register_output_bundle(
    kind: str, names: Sequence[str], expected_types: Mapping[str, str]
) -> GeometryOutputOwnership:
    final_names = tuple(str(name) for name in names)
    seed = "{}\0{}\0{}".format(kind, "\0".join(final_names), time.time_ns())
    ownership_id = "geometry_{}_{}".format(
        sanitize_name(kind, fallback="output"),
        hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10],
    )
    markers = {}
    marker_backends = {}
    for name in final_names:
        if expected_types.get(name) != "object:cgo":
            continue
        marker = "pymolplugins.geometry/1:{}:{}".format(ownership_id, name)
        if hasattr(_require_cmd(), "set_property") and hasattr(
            _require_cmd(), "get_property"
        ):
            try:
                _require_cmd().set_property(
                    "_pymolplugins_geometry_owner",
                    marker,
                    object=name,
                    state=0,
                    proptype=6,
                    quiet=1,
                )
                if str(
                    _require_cmd().get_property(
                        "_pymolplugins_geometry_owner", name, state=0, quiet=1
                    )
                    or ""
                ) == marker:
                    markers[name] = marker
                    marker_backends[name] = "property"
                    continue
            except Exception:
                pass
        try:
            _require_cmd().set_title(name, 1, marker)
            if str(_require_cmd().get_title(name, 1) or "") == marker:
                markers[name] = marker
                marker_backends[name] = "title"
        except Exception:
            # Without a durable scene marker the output remains usable, but a
            # later invocation will refuse to replace it rather than trusting
            # stale in-memory ownership after user deletion/recreation.
            pass
    ownership = GeometryOutputOwnership(
        id=ownership_id,
        kind=str(kind),
        names=final_names,
        expected_types=dict(expected_types),
        markers=markers,
        marker_backends=marker_backends,
    )
    for name in final_names:
        _GEOMETRY_OUTPUT_OWNERS[name] = ownership
    return ownership


def _output_marker_matches(owner: GeometryOutputOwnership, name: str) -> bool:
    marker = str(owner.markers.get(name, ""))
    if not marker:
        return False
    try:
        if owner.marker_backends.get(name) == "property":
            value = _require_cmd().get_property(
                "_pymolplugins_geometry_owner", name, state=0, quiet=1
            )
        elif owner.marker_backends.get(name) == "title":
            value = _require_cmd().get_title(name, 1)
        else:
            return False
        return str(value or "") == marker
    except Exception:
        return False


def _preflight_undrawn_axis_name(name: str) -> None:
    if not _name_exists(name):
        return
    owner = _GEOMETRY_OUTPUT_OWNERS.get(name)
    raise GeometryOutputCollision(
        "axis",
        (
            {
                "name": str(name),
                "type": _scene_type(name),
                "owner": owner.kind if owner is not None else "user",
            },
        ),
    )


def _delete_temporary_names(names: Iterable[str]) -> List[str]:
    failures = []
    for name in tuple(names):
        if not name or not _name_exists(name):
            continue
        try:
            _require_cmd().delete(name)
        except Exception:
            failures.append(name)
    return failures


def _install_axis_cgo(obj: Sequence[float], name: str) -> GeometryOutputOwnership:
    final_name = str(name)
    expected = {final_name: "object:cgo"}
    previous = _preflight_output_bundle("axis", (final_name,), expected)
    temporary = _unused_output_name(
        "__pp_geometry_axis_stage", (final_name,)
    )
    backup = ""
    new_is_final = False
    old_is_backup = False
    try:
        _require_cmd().load_cgo(list(obj), temporary)
        if not _name_exists(temporary) or _scene_type(temporary) != "object:cgo":
            raise RuntimeError("PyMOL did not create the staged axis CGO")
        if previous is not None:
            backup = _unused_output_name(
                "__pp_geometry_axis_backup", (final_name, temporary)
            )
            _require_cmd().set_name(final_name, backup)
            old_is_backup = True
        _require_cmd().set_name(temporary, final_name)
        new_is_final = True
        if backup:
            _require_cmd().delete(backup)
            old_is_backup = False
        return _register_output_bundle("axis", (final_name,), expected)
    except Exception as exc:
        rollback_failures = []
        if new_is_final and _name_exists(final_name):
            try:
                _require_cmd().set_name(final_name, temporary)
                new_is_final = False
            except Exception:
                rollback_failures.append(final_name)
        if old_is_backup and backup and _name_exists(backup):
            try:
                _require_cmd().set_name(backup, final_name)
                old_is_backup = False
            except Exception:
                rollback_failures.append(backup)
        rollback_failures.extend(
            _delete_temporary_names((temporary, backup if old_is_backup else ""))
        )
        detail = (
            "; rollback failed for: {}".format(", ".join(rollback_failures))
            if rollback_failures
            else ""
        )
        raise RuntimeError("axis output creation failed: {}{}".format(exc, detail)) from exc


def _install_membrane_cgo(
    obj: Sequence[float], group_name: str
) -> GeometryOutputOwnership:
    final_group = str(group_name)
    final_child = "{}__planes".format(final_group)
    final_names = (final_group, final_child)
    expected = {
        final_group: "object:group",
        final_child: "object:cgo",
    }
    previous = _preflight_output_bundle("membrane", final_names, expected)
    temporary_child = _unused_output_name(
        "__pp_geometry_membrane_stage", final_names
    )
    temporary_group = ""
    backup_child = ""
    child_is_final = False
    group_is_final = False
    old_child_is_backup = False
    try:
        _require_cmd().load_cgo(list(obj), temporary_child)
        if not _name_exists(temporary_child) or _scene_type(temporary_child) != "object:cgo":
            raise RuntimeError("PyMOL did not create the staged membrane CGO")
        if previous is None:
            temporary_group = _unused_output_name(
                "__pp_geometry_membrane_group_stage",
                final_names + (temporary_child,),
            )
            _require_cmd().group(temporary_group, temporary_child, action="add")
            if not _name_exists(temporary_group) or _scene_type(temporary_group) != "object:group":
                raise RuntimeError("PyMOL did not create the staged membrane group")
            _require_cmd().set_name(temporary_child, final_child)
            child_is_final = True
            _require_cmd().set_name(temporary_group, final_group)
            group_is_final = True
        else:
            backup_child = _unused_output_name(
                "__pp_geometry_membrane_backup",
                final_names + (temporary_child,),
            )
            _require_cmd().set_name(final_child, backup_child)
            old_child_is_backup = True
            _require_cmd().set_name(temporary_child, final_child)
            child_is_final = True
            _require_cmd().group(final_group, final_child, action="add")
            _require_cmd().delete(backup_child)
            old_child_is_backup = False
        return _register_output_bundle("membrane", final_names, expected)
    except Exception as exc:
        rollback_failures = []
        if group_is_final and _name_exists(final_group):
            try:
                _require_cmd().set_name(final_group, temporary_group)
                group_is_final = False
            except Exception:
                rollback_failures.append(final_group)
        if child_is_final and _name_exists(final_child):
            try:
                _require_cmd().set_name(final_child, temporary_child)
                child_is_final = False
            except Exception:
                rollback_failures.append(final_child)
        if old_child_is_backup and backup_child and _name_exists(backup_child):
            try:
                _require_cmd().set_name(backup_child, final_child)
                old_child_is_backup = False
            except Exception:
                rollback_failures.append(backup_child)
        rollback_failures.extend(
            _delete_temporary_names(
                (
                    temporary_group,
                    temporary_child,
                    backup_child if old_child_is_backup else "",
                )
            )
        )
        detail = (
            "; rollback failed for: {}".format(", ".join(rollback_failures))
            if rollback_failures
            else ""
        )
        raise RuntimeError(
            "membrane output creation failed: {}{}".format(exc, detail)
        ) from exc


def _ttt_value(name: str) -> Optional[Tuple[float, ...]]:
    value = _require_cmd().get_object_ttt(name, quiet=1)
    return None if value is None else tuple(float(item) for item in value)


def _ttt_close(
    left: Optional[Sequence[float]],
    right: Optional[Sequence[float]],
    tolerance: float = 1.0e-6,
) -> bool:
    return all(
        abs(float(a) - float(b)) <= tolerance
        for a, b in zip(ttt_to_homogeneous(left), ttt_to_homogeneous(right))
    )


def _axis_coordinate_hash(selection: str, atoms: str, state: int) -> str:
    samples = _selection_samples(selection, atoms, state)
    return _json_sha256(
        [
            {
                "coord": [round(float(value), 8) for value in coordinate],
                "element": element,
            }
            for coordinate, element in samples
        ]
    )


def _capture_axis_source(
    selection: str, atoms: str, state: int
) -> AxisSourceSnapshot:
    expression = str(selection)
    object_names = tuple(
        dict.fromkeys(_require_cmd().get_object_list("({})".format(expression)) or [])
    )
    return AxisSourceSnapshot(
        selection=expression,
        atoms=str(atoms),
        state=int(state),
        objects=object_names,
        object_ttt={name: _ttt_value(name) for name in object_names},
        coordinate_hash=_axis_coordinate_hash(expression, str(atoms), int(state)),
    )


def _stored_axis_stale_reasons(name: str) -> List[str]:
    result = _AXES[name]
    snapshot = _AXIS_SOURCES.get(name)
    if snapshot is None:
        return ["source snapshot is unavailable"] if result.input_selection else []
    reasons = []
    current_objects = tuple(
        dict.fromkeys(
            _require_cmd().get_object_list("({})".format(snapshot.selection)) or []
        )
    )
    if set(current_objects) != set(snapshot.objects):
        reasons.append("source object membership changed")
    for object_name in snapshot.objects:
        if object_name not in current_objects:
            continue
        try:
            current_ttt = _ttt_value(object_name)
        except Exception:
            reasons.append("source object is unavailable: {}".format(object_name))
            continue
        if not _ttt_close(current_ttt, snapshot.object_ttt.get(object_name)):
            reasons.append("source object transform changed: {}".format(object_name))
    try:
        current_hash = _axis_coordinate_hash(
            snapshot.selection, snapshot.atoms, snapshot.state
        )
        if current_hash != snapshot.coordinate_hash:
            reasons.append("source coordinates changed")
    except Exception as exc:
        reasons.append("source coordinates cannot be read: {}".format(exc))
    return list(dict.fromkeys(reasons))


def _validate_stored_axis(name: str) -> None:
    reasons = _stored_axis_stale_reasons(name)
    if reasons:
        raise StaleStoredAxis(name, reasons)


def _draw_axis(result: AxisResult, name: str, length: float = 50.0, radius: float = 0.4, color: str = "yellow") -> None:
    from pymol import cgo

    direction = normalize(result.direction)
    start = subtract(result.center, scale(direction, length / 2.0))
    end = add(result.center, scale(direction, length / 2.0))
    rgb = _require_cmd().get_color_tuple(color)
    obj = [cgo.CYLINDER, *start, *end, radius, *rgb, *rgb]
    _install_axis_cgo(obj, name)


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


def _new_orientation_id(objects: Sequence[str]) -> str:
    seed = "{}\0{}".format("\0".join(objects), time.time_ns())
    return "orient_{}".format(
        hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    )


def _orientation_ttt_fingerprint(
    values: Mapping[str, Optional[Sequence[float]]]
) -> str:
    return _json_sha256(
        {
            name: [float(item) for item in ttt_to_homogeneous(value)]
            for name, value in sorted(values.items())
        }
    )


def _latest_orientation_record() -> Optional[OrientationRecord]:
    for record in reversed(tuple(_ORIENTATION_RECORDS.values())):
        if not record.undone:
            return record
    return None


def _sync_legacy_orientation_undo() -> None:
    global _LAST_ORIENTATION_ID
    record = _latest_orientation_record()
    _ORIENTATION_UNDO.clear()
    if record is None:
        _LAST_ORIENTATION_ID = ""
        return
    _LAST_ORIENTATION_ID = record.id
    _ORIENTATION_UNDO.update(record.baseline_ttt)


def _store_orientation_record(record: OrientationRecord) -> None:
    _ORIENTATION_RECORDS[record.id] = record
    _ORIENTATION_RECORDS.move_to_end(record.id)
    while len(_ORIENTATION_RECORDS) > _ORIENTATION_RECORD_LIMIT:
        _ORIENTATION_RECORDS.popitem(last=False)
    _sync_legacy_orientation_undo()


def _orientation_record(operation_id: object = "") -> OrientationRecord:
    key = str(operation_id).strip()
    if key:
        try:
            return _ORIENTATION_RECORDS[key]
        except KeyError as exc:
            raise ValueError("unknown orientation operation: {}".format(key)) from exc
    record = _latest_orientation_record()
    if record is None:
        raise ValueError("no committed orientation to undo")
    return record


def _apply_orientation(
    matrix: Sequence[float], objects: Sequence[str]
) -> OrientationRecord:
    unique_objects = tuple(dict.fromkeys(objects))
    if not unique_objects:
        raise ValueError("no move objects resolved")
    baselines = {}
    for name in unique_objects:
        baseline = _require_cmd().get_object_ttt(name, quiet=1)
        baselines[name] = (
            None if baseline is None else tuple(float(value) for value in baseline)
        )
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
        committed = {name: _ttt_value(name) for name in unique_objects}
        record = OrientationRecord(
            id=_new_orientation_id(unique_objects),
            created_at=time.time(),
            objects=unique_objects,
            baseline_ttt=dict(baselines),
            committed_ttt=committed,
            committed_fingerprint=_orientation_ttt_fingerprint(committed),
        )
    except Exception as exc:
        rollback_failures = _rollback_orientation(baselines, attempted)
        detail = (
            f"; rollback failed for: {', '.join(rollback_failures)}"
            if rollback_failures
            else ""
        )
        raise RuntimeError(f"orientation commit failed: {exc}{detail}") from exc
    _store_orientation_record(record)
    return record


def pp_orientation_undo(
    operation_id: object = "", force: object = 0
) -> Dict[str, object]:
    """Undo one committed orientation without overwriting newer edits by default."""
    try:
        record = _orientation_record(operation_id)
        if record.undone:
            raise ValueError(
                "orientation operation is already undone: {}".format(record.id)
            )
        force_value = as_bool(force)
        current = {name: _ttt_value(name) for name in record.objects}
        stale = [
            name
            for name in record.objects
            if not _ttt_close(current[name], record.committed_ttt[name])
        ]
        if stale and not force_value:
            raise StaleOrientationUndo(record.id, stale)
        restored = []
        try:
            for name, value in record.baseline_ttt.items():
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
        record.undone = True
        _sync_legacy_orientation_undo()
        print("Orientation undone: {}".format(record.id))
        return {
            "status": "restored",
            "changed": True,
            "operation_id": record.id,
            "committed_fingerprint": record.committed_fingerprint,
            "objects": restored,
            "forced": force_value,
            "stale_overwritten": stale if force_value else [],
            "next_command": None,
        }
    except Exception as exc:
        return _report_geometry_error(exc)


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
        parsed_state = as_int(state, minimum=1, name="state")
        key = str(name)
        draw_value = as_bool(draw)
        points, weights, weighting_mode = _pca_inputs(
            str(selection), str(atoms), parsed_state, weighting
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
                    "state": parsed_state,
                    "draw": draw_value,
                },
            }
        )
        source_snapshot = _capture_axis_source(
            str(selection), str(atoms), parsed_state
        )
        if draw_value:
            _draw_axis(result, key)
        else:
            _preflight_undrawn_axis_name(key)
        _AXES[key] = result
        _AXIS_SOURCES[key] = source_snapshot
        print(
            f"PCA axis {name}: confidence={result.confidence:.3f}, "
            f"status={'drawn' if draw_value else 'computed; scene unchanged'}"
        )
        return _axis_command_payload(result, key, draw_value)
    except Exception as exc:
        return _report_geometry_error(exc)


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
        parsed_state = as_int(state, minimum=1, name="state")
        draw_value = as_bool(draw)
        length_value = as_float(length, minimum=0.1, name="length")
        radius_value = as_float(radius, minimum=0.001, name="radius")
        color_value = str(color)
        source_atoms = ""
        source_snapshot = None
        if str(center).strip():
            if str(selection).strip().lower() not in {"", "all"}:
                raise ValueError(
                    "selection and an explicit center are mutually exclusive; "
                    "omit selection or omit center"
                )
            axis_center = parse_vector(center)
            input_selection = ""
        else:
            points = _selection_coords(
                str(selection), "ca", parsed_state
            )
            source_atoms = "ca"
            if not points:
                points = _selection_coords(str(selection), "heavy", parsed_state)
                source_atoms = "heavy"
            axis_center = centroid(points)
            input_selection = str(selection)
            source_snapshot = _capture_axis_source(
                input_selection, source_atoms, parsed_state
            )
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
                    "state": parsed_state,
                    "draw": draw_value,
                    "length": length_value,
                    "radius": radius_value,
                    "color": color_value,
                },
            }
        )
        key = str(name)
        if draw_value:
            _draw_axis(
                result,
                key,
                length_value,
                radius_value,
                color_value,
            )
        else:
            _preflight_undrawn_axis_name(key)
        _AXES[key] = result
        if source_snapshot is None:
            _AXIS_SOURCES.pop(key, None)
        else:
            _AXIS_SOURCES[key] = source_snapshot
        print(
            "Vector axis {}: {} at center {}".format(
                key,
                "drawn" if draw_value else "computed only; scene unchanged",
                ",".join("{:.3f}".format(value) for value in result.center),
            )
        )
        return _axis_command_payload(result, key, draw_value)
    except Exception as exc:
        return _report_geometry_error(exc)


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
        draw_value = as_bool(draw)
        length_value = as_float(length, minimum=0.1, name="length")
        radius_value = as_float(radius, minimum=0.001, name="radius")
        color_value = str(color)
        result = two_point_axis(parsed_points)
        result = AxisResult(
            **{
                **result.__dict__,
                "parameters": {
                    "points": _point_payload(parsed_points),
                    "draw": draw_value,
                    "length": length_value,
                    "radius": radius_value,
                    "color": color_value,
                },
            }
        )
        key = str(name)
        if draw_value:
            _draw_axis(
                result,
                key,
                length_value,
                radius_value,
                color_value,
            )
        else:
            _preflight_undrawn_axis_name(key)
        _AXES[key] = result
        _AXIS_SOURCES.pop(key, None)
        print(
            "Two-point axis {}: {}".format(
                key, "drawn" if draw_value else "computed only; scene unchanged"
            )
        )
        return _axis_command_payload(result, key, draw_value)
    except Exception as exc:
        return _report_geometry_error(exc)


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
        center_value = as_bool(center)
        force_value = as_bool(force)
        commit_value = as_bool(commit)
        threshold = as_float(
            min_confidence, minimum=0.0, maximum=1.0, name="min_confidence"
        )
        secondary_value = str(secondary_selection).strip()
        secondary_target_value = str(secondary_target).strip().lower()
        if not secondary_value and secondary_target_value not in {"", "auto"}:
            raise ValueError(
                "secondary_target requires secondary_selection; otherwise roll is undefined"
            )
        objects = _move_objects(selection, move)
        if not objects:
            raise ValueError("no move objects resolved")
        points, weights, weighting_mode = _pca_inputs(
            str(selection), str(atoms), parsed_state, weighting
        )
        result = pca_axis(points, axis, weights)
        if result.confidence < threshold and not force_value:
            raise GeometryError(
                f"PCA axis confidence {result.confidence:.3f} is below {threshold:.3f}; use force=1"
            )
        secondary_direction = None
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
                    "center_at_origin": center_value,
                    "weighting": weighting_mode,
                    "secondary_selection": secondary_value,
                    "secondary_target": (
                        "auto" if target_secondary is None else list(target_secondary)
                    ),
                    "min_confidence": threshold,
                    "force": force_value,
                    "state": parsed_state,
                },
            }
        )
        target_center = (0.0, 0.0, 0.0) if center_value else result.center
        matrix = compose_about_center(rotation, result.center, target_center)
        operation = _apply_orientation(matrix, objects) if commit_value else None
        if operation is not None:
            print("Orientation committed: {}".format(operation.id))
        else:
            print(
                "Preview only; nothing changed. Re-run pp_orient_pca with "
                "the same arguments and commit=1 to apply."
            )
        return _orientation_command_payload(
            result,
            matrix,
            objects,
            operation,
            roll_constrained,
            "pp_orient_pca",
        )
    except Exception as exc:
        return _report_geometry_error(exc)


def _subunit_identity_label(identity: Tuple[str, str, str]) -> str:
    owner, segi, chain = identity
    fields = ["object={}".format(owner)]
    if segi:
        fields.append("segi={}".format(segi))
    fields.append("chain={}".format(chain or "<blank>"))
    return "|".join(fields)


def _subunit_ca_records(expression: str, state: int) -> List[_SubunitCARecord]:
    model = _require_cmd().get_model("({}) and name CA".format(expression), state)
    owners = list(
        dict.fromkeys(
            _require_cmd().get_object_list("({})".format(expression)) or []
        )
    )
    matrices: Dict[str, Tuple[float, ...]] = {}
    records = []
    for atom in getattr(model, "atom", ()) or ():
        if str(getattr(atom, "name", "") or "").strip().upper() != "CA":
            continue
        owner = str(getattr(atom, "model", "") or "").strip()
        if not owner and len(owners) == 1:
            owner = str(owners[0]).strip()
        if not owner:
            raise GeometryError(
                "CA atom identity is missing its source object; narrow the selection"
            )
        if owner not in matrices:
            try:
                matrix = _require_cmd().get_object_matrix(
                    owner, state, incl_ttt=1
                )
            except Exception:
                matrix = None
            if matrix is None:
                matrix = ttt_to_homogeneous(
                    _require_cmd().get_object_ttt(owner, quiet=1)
                )
            matrices[owner] = tuple(float(value) for value in matrix)
        coordinate = tuple(float(value) for value in atom.coord)
        if len(coordinate) != 3:
            raise GeometryError("CA atom does not have a 3D coordinate")
        records.append(
            _SubunitCARecord(
                object=owner,
                segi=str(getattr(atom, "segi", "") or "").strip(),
                chain=str(getattr(atom, "chain", "") or "").strip(),
                resi=str(getattr(atom, "resi", "") or "").strip(),
                resn=str(getattr(atom, "resn", "") or "").strip().upper(),
                alt=str(getattr(atom, "alt", "") or "").strip(),
                index=int(getattr(atom, "index", 0) or 0),
                coord=apply_matrix(matrices[owner], coordinate),
            )
        )
    return records


def _residue_order(record: _SubunitCARecord) -> Tuple[object, ...]:
    number, insertion = parse_residue_identifier(record.resi)
    if number is None:
        return (1, record.resi, record.resn, record.index)
    return (0, number, insertion, record.resi, record.resn, record.index)


def _unique_c2_residues(
    label: str, records: Sequence[_SubunitCARecord]
) -> Dict[str, _SubunitCARecord]:
    result = {}
    duplicates = []
    for record in records:
        if not record.resi:
            raise GeometryError(
                "C2 residue correspondence requires non-empty residue IDs in {}".format(
                    label
                )
            )
        if not record.resn:
            raise GeometryError(
                "C2 residue correspondence requires non-empty residue names in {}".format(
                    label
                )
            )
        if record.resi in result:
            duplicates.append(record.resi)
        result[record.resi] = record
    if duplicates:
        raise GeometryError(
            "C2 subunit {} has duplicate CA residue identities: {}".format(
                label, ", ".join(sorted(set(duplicates)))
            )
        )
    return result


def _paired_c2_clouds(
    grouped: Mapping[str, Sequence[_SubunitCARecord]], labels: Sequence[str]
) -> Tuple[Dict[str, List[Point]], Dict[str, object]]:
    first_label, second_label = labels
    first = _unique_c2_residues(first_label, grouped[first_label])
    second = _unique_c2_residues(second_label, grouped[second_label])
    missing_first = set(second).difference(first)
    missing_second = set(first).difference(second)
    if missing_first or missing_second:
        raise GeometryError(
            "C2 residue correspondence mismatch: {} missing [{}]; {} missing [{}]".format(
                first_label,
                ", ".join(sorted(missing_first)) or "none",
                second_label,
                ", ".join(sorted(missing_second)) or "none",
            )
        )
    inconsistent = [
        resi
        for resi in first
        if first[resi].resn != second[resi].resn
    ]
    if inconsistent:
        rendered = ", ".join(
            "{}({}/{})".format(
                resi, first[resi].resn or "?", second[resi].resn or "?"
            )
            for resi in sorted(inconsistent)
        )
        raise GeometryError(
            "C2 residue correspondence has inconsistent residue names: {}".format(
                rendered
            )
        )
    ordered = sorted(first, key=lambda resi: _residue_order(first[resi]))
    clouds = {
        first_label: [first[resi].coord for resi in ordered],
        second_label: [second[resi].coord for resi in ordered],
    }
    correspondence_payload = [
        {"resi": resi, "resn": first[resi].resn} for resi in ordered
    ]
    return clouds, {
        "c2_correspondence": "CA by exact resi and resn",
        "c2_correspondence_count": len(ordered),
        "c2_correspondence_hash": _json_sha256(correspondence_payload),
    }


def _subunit_clouds(
    selection: str, subunits: object, state: int
) -> Dict[str, List[Point]]:
    labels = split_names(subunits)
    if len(set(labels)) != len(labels):
        raise GeometryError("explicit subunit selections must be unique")
    grouped: "OrderedDict[str, List[_SubunitCARecord]]" = OrderedDict()
    resolved_identities = []
    if labels:
        for label in labels:
            records = _subunit_ca_records(
                "({}) and ({})".format(selection, label), state
            )
            if not records:
                raise GeometryError(
                    "explicit subunit {!r} contains no CA atoms".format(label)
                )
            grouped[label] = records
            identities = sorted(
                {
                    (record.object, record.segi, record.chain)
                    for record in records
                }
            )
            resolved_identities.append(
                {
                    "label": label,
                    "members": [list(identity) for identity in identities],
                    "ca_count": len(records),
                }
            )
        identity_mode = "explicit_selection"
    else:
        automatic = _subunit_ca_records(selection, state)
        if not automatic:
            raise GeometryError("selection contains no CA atoms")
        by_identity: "OrderedDict[Tuple[str, str, str], List[_SubunitCARecord]]" = (
            OrderedDict()
        )
        for record in automatic:
            identity = (record.object, record.segi, record.chain)
            by_identity.setdefault(identity, []).append(record)
        for identity in sorted(by_identity):
            label = _subunit_identity_label(identity)
            records = by_identity[identity]
            grouped[label] = records
            resolved_identities.append(
                {
                    "label": label,
                    "object": identity[0],
                    "segi": identity[1],
                    "chain": identity[2],
                    "ca_count": len(records),
                }
            )
        identity_mode = "object+segi+chain"

    ordered_grouped = {
        label: sorted(records, key=_residue_order)
        for label, records in grouped.items()
    }
    correspondence = {}
    if len(ordered_grouped) == 2:
        clouds, correspondence = _paired_c2_clouds(
            ordered_grouped, list(ordered_grouped)
        )
    else:
        clouds = {
            label: [record.coord for record in records]
            for label, records in ordered_grouped.items()
        }
    diagnostics = {
        "subunit_identity_mode": identity_mode,
        "resolved_subunit_identities": resolved_identities,
        **correspondence,
    }
    return _SubunitCloudMap(clouds, diagnostics=diagnostics)


def _command_cyclic_axis(
    clouds: Mapping[str, Sequence[Sequence[float]]]
) -> AxisResult:
    result = cyclic_axis(clouds)
    resolution = dict(getattr(clouds, "resolution_diagnostics", {}) or {})
    if not resolution:
        return result
    diagnostics = dict(result.diagnostics)
    diagnostics.update(resolution)
    return AxisResult(**{**result.__dict__, "diagnostics": diagnostics})


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
        parsed_state = as_int(state, minimum=1, name="state")
        key = str(name)
        draw_value = as_bool(draw)
        force_value = as_bool(force)
        threshold = as_float(
            min_confidence, minimum=0.0, maximum=1.0, name="min_confidence"
        )
        auto_order = str(order).strip().lower() == "auto"
        requested_order = (
            None if auto_order else as_int(order, minimum=2, name="order")
        )
        clouds = _subunit_clouds(selection, subunits, parsed_state)
        if requested_order is not None and requested_order != len(clouds):
            raise ValueError(f"requested order {order} but resolved {len(clouds)} subunits")
        result = _command_cyclic_axis(clouds)
        classification = result.diagnostics.get("classification", "cyclic")
        explicit_subunits = bool(split_names(subunits)) or not auto_order
        if classification != "cyclic" and not force_value:
            raise GeometryError(
                f"resolved subunits are classified as {classification}; use force=1"
            )
        if (
            result.confidence < threshold
            and not explicit_subunits
            and not force_value
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
                    "resolved_subunits": list(clouds),
                    "subunit_identity_mode": result.diagnostics.get(
                        "subunit_identity_mode", "coordinate_cloud"
                    ),
                    "order": "auto" if auto_order else requested_order,
                    "state": parsed_state,
                    "min_confidence": threshold,
                    "force": force_value,
                    "draw": draw_value,
                },
            }
        )
        source_snapshot = _capture_axis_source(
            str(selection), "ca", parsed_state
        )
        if draw_value:
            _draw_axis(result, key)
        else:
            _preflight_undrawn_axis_name(key)
        _AXES[key] = result
        _AXIS_SOURCES[key] = source_snapshot
        print(
            f"Cyclic axis {name}: C{result.diagnostics.get('order')}, "
            f"confidence={result.confidence:.3f}, "
            f"class={result.diagnostics.get('classification', 'cyclic')}, "
            f"status={'drawn' if draw_value else 'computed; scene unchanged'}"
        )
        return _axis_command_payload(result, key, draw_value)
    except Exception as exc:
        return _report_geometry_error(exc)


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
        center_value = as_bool(center)
        force_value = as_bool(force)
        commit_value = as_bool(commit)
        threshold = as_float(
            min_confidence, minimum=0.0, maximum=1.0, name="min_confidence"
        )
        clouds = _subunit_clouds(selection, subunits, parsed_state)
        objects = _move_objects(selection, move)
        if not objects:
            raise ValueError("no move objects resolved")
        result = _command_cyclic_axis(clouds)
        classification = result.diagnostics.get("classification", "cyclic")
        if not force_value and (
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
                    "resolved_subunits": list(clouds),
                    "subunit_identity_mode": result.diagnostics.get(
                        "subunit_identity_mode", "coordinate_cloud"
                    ),
                    "move": list(objects),
                    "target_axis": list(target_direction),
                    "center_at_origin": center_value,
                    "min_confidence": threshold,
                    "force": force_value,
                    "state": parsed_state,
                },
            }
        )
        rotation = rotation_from_to(result.direction, target_direction)
        target_center = (0.0, 0.0, 0.0) if center_value else result.center
        matrix = compose_about_center(rotation, result.center, target_center)
        operation = _apply_orientation(matrix, objects) if commit_value else None
        if operation is not None:
            print("Orientation committed: {}".format(operation.id))
        else:
            print(
                "Preview only; nothing changed. Re-run pp_orient_cyclic with "
                "the same arguments and commit=1 to apply."
            )
        return _orientation_command_payload(
            result,
            matrix,
            objects,
            operation,
            False,
            "pp_orient_cyclic",
        )
    except Exception as exc:
        return _report_geometry_error(exc)


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
        diagnostics={
            "axis_confidence": axis_result.confidence,
            "axis": dict(axis_result.diagnostics),
        },
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
    _install_membrane_cgo(obj, result.name)


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
    force_value = as_bool(force)
    requested_subunits = split_names(subunits)
    if requested_subunits and source != "cyclic":
        raise ValueError(
            "subunits is only valid with axis_source=cyclic; remove it for {}".format(
                raw_source or "the selected axis source"
            )
        )
    if source == "pca":
        mode = str(axis).strip() or "long"
        result = pca_axis(points, mode)
        if result.confidence < 0.05 and not force_value:
            raise GeometryError(
                "PCA axis confidence {:.3f} is below 0.050; choose a better "
                "axis/selection or use force=1".format(result.confidence)
            )
        return result, source
    if source == "cyclic":
        if str(axis).strip():
            raise ValueError(
                "axis is not used with axis_source=cyclic; omit axis or choose "
                "axis_source=vector"
            )
        result = _command_cyclic_axis(
            _subunit_clouds(selection, subunits, state)
        )
        if (
            result.diagnostics.get("classification", "cyclic") != "cyclic"
            or result.confidence < 0.5
        ) and not force_value:
            raise GeometryError(
                "resolved cyclic axis is low-confidence or non-cyclic; use force=1 to draw anyway"
            )
        return result, source
    if source == "vector":
        if force_value:
            raise ValueError("force is only valid for PCA or cyclic axis inference")
        return vector_axis(_target_axis(axis or "z"), centroid(points)), source
    if source == "two_points":
        if force_value:
            raise ValueError("force is only valid for PCA or cyclic axis inference")
        return two_point_axis(_parse_two_points(axis)), source
    if source == "anvil":
        if force_value:
            raise ValueError("force is only valid for PCA or cyclic axis inference")
        path = os.path.abspath(os.path.expanduser(str(axis).strip()))
        if not str(axis).strip() or not os.path.isfile(path):
            raise ValueError("axis_source=anvil requires axis=<existing ANVIL output PDB>")
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            result, _ = parse_anvil_pdb(handle.read())
        return result, source
    if raw_source in _AXES:
        if str(axis).strip():
            raise ValueError(
                "axis is not used when axis_source names a stored axis"
            )
        if force_value:
            raise ValueError("force cannot bypass stored-axis stale checks")
        _validate_stored_axis(raw_source)
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
        force_value = as_bool(force)
        color_value = str(color)
        transparency_value = as_float(
            transparency, minimum=0.0, maximum=1.0, name="transparency"
        )
        explicit_center = (
            parse_vector(center) if str(center).strip() else None
        )
        points = _selection_coords(selection, "ca", parsed_state)
        if not points:
            points = _selection_coords(selection, "heavy", parsed_state)
        axis_result, source = _resolve_slab_axis(
            axis_source,
            axis,
            points,
            str(selection),
            subunits,
            parsed_state,
            force_value,
        )
        result = build_membrane_slab(
            points,
            axis_result,
            name=str(name),
            thickness=thickness,
            width=_optional_extent(width, "width"),
            height=_optional_extent(height, "height"),
            padding=padding,
            center=explicit_center,
            source=source,
        )
        result = MembraneResult(
            **{
                **result.__dict__,
                "parameters": {
                    **dict(result.parameters),
                    "selection": str(selection),
                    "axis_source": source,
                    "axis": (
                        axis_result.parameters.get("axis", str(axis).strip() or "long")
                        if source == "pca"
                        else str(axis).strip() or ("z" if source == "vector" else "")
                    ),
                    "subunits": split_names(subunits),
                    "center": (
                        list(explicit_center)
                        if explicit_center is not None
                        else "axis_center"
                    ),
                    "color": color_value,
                    "transparency": transparency_value,
                    "force": force_value,
                    "state": parsed_state,
                },
            }
        )
        _draw_membrane(
            result,
            color_value,
            transparency_value,
        )
        _MEMBRANES[result.name] = result
        print(
            f"Membrane slab {result.name}: thickness={result.thickness:.2f}, "
            f"size={result.width:.2f}x{result.height:.2f}"
        )
        return _membrane_command_payload(result)
    except Exception as exc:
        return _report_geometry_error(exc)


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
        color_value = str(color)
        transparency_value = as_float(
            transparency, minimum=0.0, maximum=1.0, name="transparency"
        )
        arguments_value = str(arguments)
        timeout_value = as_float(timeout, minimum=0.1, name="timeout")
        configured = str(executable).strip() or os.environ.get(
            "PYMOLPLUGINS_ANVIL_EXECUTABLE", ""
        ).strip()
        selection_value = str(selection).strip() or "all"
        if configured and selection_value != "all":
            raise ValueError(
                "selection is only used when parsing precomputed ANVIL output; "
                "executable mode uses input_or_selection as the source selection"
            )
        if not configured and (
            arguments_value != "{input}" or abs(timeout_value - 120.0) > 1.0e-12
        ):
            raise ValueError(
                "arguments and timeout are only valid when executable is configured"
            )
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
                arguments=arguments_value,
                timeout=timeout_value,
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
            source_selection = selection_value
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
                "arguments": arguments_value if configured else None,
                "timeout": timeout_value if configured else None,
                "state": parsed_state,
                "width": slab_width,
                "height": slab_height,
                "padding": pad,
                "color": color_value,
                "transparency": transparency_value,
            },
            backend=backend,
            warnings=axis_result.warnings,
        )
        _draw_membrane(
            result,
            color_value,
            transparency_value,
        )
        _MEMBRANES[result.name] = result
        print(
            f"Membrane slab {result.name}: thickness={result.thickness:.2f}, "
            f"size={result.width:.2f}x{result.height:.2f}, source=anvil"
        )
        return _membrane_command_payload(result)
    except Exception as exc:
        return _report_geometry_error(exc)


def pp_geometry_export(name: str, filename: str = "") -> Optional[str]:
    temporary_path = ""
    try:
        key = str(name)
        if key in _AXES:
            _validate_stored_axis(key)
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
    extend_command(cmd, "pp_axis_pca", pp_axis_pca)
    extend_command(cmd, "pp_axis_vector", pp_axis_vector)
    extend_command(cmd, "pp_axis_two_points", pp_axis_two_points)
    extend_command(cmd, "pp_orient_pca", pp_orient_pca)
    extend_command(cmd, "pp_axis_cyclic", pp_axis_cyclic)
    extend_command(cmd, "pp_orient_cyclic", pp_orient_cyclic)
    extend_command(cmd, "pp_orientation_undo", pp_orientation_undo)
    extend_command(cmd, "pp_membrane_slab", pp_membrane_slab)
    extend_command(cmd, "pp_membrane_from_anvil", pp_membrane_from_anvil)
    extend_command(cmd, "pp_geometry_export", pp_geometry_export)
