"""Small dependency-free helpers for rigid 3D geometry.

Matrices in this module are flat, row-major, homogeneous 4x4 matrices.  PyMOL
TTT matrices use the same flat layout but have an additional pre-translation
in elements 12:15.  Conversion helpers make that distinction explicit.
"""

from dataclasses import dataclass
import math
from typing import Iterable, List, Optional, Sequence, Tuple


EPSILON = 1.0e-12


class GeometryError(ValueError):
    """Raised when a requested geometric result is undefined."""


def _vector3(value: Sequence[float], name: str = "vector") -> Tuple[float, float, float]:
    if len(value) != 3:
        raise GeometryError(f"{name} must contain exactly three values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise GeometryError(f"{name} contains a non-finite value")
    return result  # type: ignore[return-value]


def _matrix4(value: Sequence[float], name: str = "matrix") -> Tuple[float, ...]:
    if len(value) != 16:
        raise GeometryError(f"{name} must contain exactly 16 values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise GeometryError(f"{name} contains a non-finite value")
    return result


def identity4() -> Tuple[float, ...]:
    """Return a row-major 4x4 identity matrix."""
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    av = _vector3(a, "a")
    bv = _vector3(b, "b")
    return sum(x * y for x, y in zip(av, bv))


def cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    ax, ay, az = _vector3(a, "a")
    bx, by, bz = _vector3(b, "b")
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def add(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    av = _vector3(a, "a")
    bv = _vector3(b, "b")
    return tuple(x + y for x, y in zip(av, bv))  # type: ignore[return-value]


def subtract(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    av = _vector3(a, "a")
    bv = _vector3(b, "b")
    return tuple(x - y for x, y in zip(av, bv))  # type: ignore[return-value]


def scale(value: Sequence[float], factor: float) -> Tuple[float, float, float]:
    vector = _vector3(value)
    factor = float(factor)
    return tuple(item * factor for item in vector)  # type: ignore[return-value]


def norm(value: Sequence[float]) -> float:
    vector = _vector3(value)
    return math.sqrt(sum(item * item for item in vector))


def normalize(value: Sequence[float], *, tolerance: float = EPSILON) -> Tuple[float, float, float]:
    vector = _vector3(value)
    length = norm(vector)
    if length <= tolerance:
        raise GeometryError("cannot normalize a zero-length vector")
    return scale(vector, 1.0 / length)


def centroid(
    points: Sequence[Sequence[float]], weights: Optional[Sequence[float]] = None
) -> Tuple[float, float, float]:
    """Return the weighted centroid of one or more points."""
    if not points:
        raise GeometryError("at least one point is required")
    clean_points = [_vector3(point, "point") for point in points]
    if weights is None:
        clean_weights = [1.0] * len(clean_points)
    else:
        if len(weights) != len(clean_points):
            raise GeometryError("weights and points must have the same length")
        clean_weights = [float(weight) for weight in weights]
    if any((not math.isfinite(weight)) or weight < 0.0 for weight in clean_weights):
        raise GeometryError("weights must be finite and non-negative")
    total = sum(clean_weights)
    if total <= EPSILON:
        raise GeometryError("the total point weight must be positive")
    return tuple(
        sum(weight * point[axis] for point, weight in zip(clean_points, clean_weights)) / total
        for axis in range(3)
    )  # type: ignore[return-value]


def mat4_multiply(a: Sequence[float], b: Sequence[float]) -> Tuple[float, ...]:
    """Return ``a @ b`` for two row-major homogeneous matrices."""
    left = _matrix4(a, "a")
    right = _matrix4(b, "b")
    return tuple(
        sum(left[row * 4 + inner] * right[inner * 4 + column] for inner in range(4))
        for row in range(4)
        for column in range(4)
    )


def apply_matrix(matrix: Sequence[float], point: Sequence[float]) -> Tuple[float, float, float]:
    """Apply a row-major homogeneous matrix to a Cartesian point."""
    values = _matrix4(matrix)
    x, y, z = _vector3(point, "point")
    result = (
        values[0] * x + values[1] * y + values[2] * z + values[3],
        values[4] * x + values[5] * y + values[6] * z + values[7],
        values[8] * x + values[9] * y + values[10] * z + values[11],
    )
    w = values[12] * x + values[13] * y + values[14] * z + values[15]
    if abs(w) <= EPSILON:
        raise GeometryError("homogeneous transform produced w=0")
    if abs(w - 1.0) > EPSILON:
        result = tuple(item / w for item in result)
    return result  # type: ignore[return-value]


def determinant3(rotation: Sequence[float]) -> float:
    """Return the determinant of a flat 3x3 matrix."""
    if len(rotation) != 9:
        raise GeometryError("rotation must contain exactly nine values")
    a, b, c, d, e, f, g, h, i = (float(value) for value in rotation)
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def rotation_from_matrix(matrix: Sequence[float]) -> Tuple[float, ...]:
    values = _matrix4(matrix)
    return (
        values[0], values[1], values[2],
        values[4], values[5], values[6],
        values[8], values[9], values[10],
    )


def validate_rigid_matrix(
    matrix: Sequence[float], *, tolerance: float = 1.0e-6
) -> Tuple[float, ...]:
    """Validate a homogeneous proper rigid transform and return it."""
    values = _matrix4(matrix)
    if any(abs(values[index]) > tolerance for index in (12, 13, 14)):
        raise GeometryError("matrix has a projective last row")
    if abs(values[15] - 1.0) > tolerance:
        raise GeometryError("matrix[15] must be one")
    rotation = rotation_from_matrix(values)
    for row in range(3):
        for column in range(3):
            value = sum(rotation[inner * 3 + row] * rotation[inner * 3 + column] for inner in range(3))
            expected = 1.0 if row == column else 0.0
            if abs(value - expected) > tolerance:
                raise GeometryError("matrix rotation contains scale or shear")
    determinant = determinant3(rotation)
    if abs(determinant - 1.0) > tolerance:
        raise GeometryError("matrix rotation is not a proper rotation")
    return values


def rigid_inverse(matrix: Sequence[float]) -> Tuple[float, ...]:
    """Invert a validated homogeneous rigid transform."""
    values = validate_rigid_matrix(matrix)
    rotation = rotation_from_matrix(values)
    translation = (values[3], values[7], values[11])
    transpose = (
        rotation[0], rotation[3], rotation[6],
        rotation[1], rotation[4], rotation[7],
        rotation[2], rotation[5], rotation[8],
    )
    inverse_translation = tuple(
        -sum(transpose[row * 3 + column] * translation[column] for column in range(3))
        for row in range(3)
    )
    return (
        transpose[0], transpose[1], transpose[2], inverse_translation[0],
        transpose[3], transpose[4], transpose[5], inverse_translation[1],
        transpose[6], transpose[7], transpose[8], inverse_translation[2],
        0.0, 0.0, 0.0, 1.0,
    )


def ttt_to_homogeneous(ttt: Optional[Sequence[float]]) -> Tuple[float, ...]:
    """Convert a PyMOL TTT matrix into a standard homogeneous matrix."""
    if ttt is None:
        return identity4()
    values = _matrix4(ttt, "ttt")
    rotation = rotation_from_matrix(values)
    pre = (values[12], values[13], values[14])
    post = (values[3], values[7], values[11])
    translation = tuple(
        sum(rotation[row * 3 + column] * pre[column] for column in range(3)) + post[row]
        for row in range(3)
    )
    homogeneous = (
        rotation[0], rotation[1], rotation[2], translation[0],
        rotation[3], rotation[4], rotation[5], translation[1],
        rotation[6], rotation[7], rotation[8], translation[2],
        0.0, 0.0, 0.0, 1.0,
    )
    return validate_rigid_matrix(homogeneous)


def homogeneous_to_ttt(matrix: Sequence[float]) -> Tuple[float, ...]:
    """Convert a homogeneous rigid matrix to a canonical PyMOL TTT matrix.

    The canonical representation uses zero pre-translation and stores the full
    translation in the post-translation column.  This avoids the misleading
    ``homogenous=1`` branch in ``cmd.set_object_ttt``.
    """
    values = validate_rigid_matrix(matrix)
    return (
        values[0], values[1], values[2], values[3],
        values[4], values[5], values[6], values[7],
        values[8], values[9], values[10], values[11],
        0.0, 0.0, 0.0, 1.0,
    )


def compose_rigid(
    rotation: Sequence[float], translation: Sequence[float]
) -> Tuple[float, ...]:
    """Build a homogeneous matrix from a flat row-major 3x3 rotation."""
    if len(rotation) != 9:
        raise GeometryError("rotation must contain exactly nine values")
    r = tuple(float(value) for value in rotation)
    tx, ty, tz = _vector3(translation, "translation")
    return validate_rigid_matrix((
        r[0], r[1], r[2], tx,
        r[3], r[4], r[5], ty,
        r[6], r[7], r[8], tz,
        0.0, 0.0, 0.0, 1.0,
    ))


def compose_about_center(
    rotation: Sequence[float],
    center: Sequence[float],
    target_center: Optional[Sequence[float]] = None,
) -> Tuple[float, ...]:
    """Build a rotation about ``center``, optionally moving it to a target."""
    r = tuple(float(value) for value in rotation)
    if len(r) != 9:
        raise GeometryError("rotation must contain exactly nine values")
    source = _vector3(center, "center")
    target = source if target_center is None else _vector3(target_center, "target_center")
    rotated_source = tuple(
        sum(r[row * 3 + column] * source[column] for column in range(3))
        for row in range(3)
    )
    translation = subtract(target, rotated_source)
    return compose_rigid(r, translation)


def _jacobi_eigen_symmetric(
    matrix: Sequence[Sequence[float]], *, tolerance: float = 1.0e-14, max_iterations: int = 200
) -> Tuple[List[float], List[List[float]]]:
    """Eigen-decompose a small real symmetric matrix with Jacobi rotations."""
    size = len(matrix)
    if size == 0 or any(len(row) != size for row in matrix):
        raise GeometryError("matrix must be non-empty and square")
    a = [[float(matrix[row][column]) for column in range(size)] for row in range(size)]
    for row in range(size):
        for column in range(size):
            if abs(a[row][column] - a[column][row]) > 1.0e-9:
                raise GeometryError("matrix must be symmetric")
    vectors = [[1.0 if row == column else 0.0 for column in range(size)] for row in range(size)]
    for _ in range(max_iterations):
        p, q = 0, 1 if size > 1 else 0
        maximum = 0.0
        for row in range(size):
            for column in range(row + 1, size):
                candidate = abs(a[row][column])
                if candidate > maximum:
                    maximum = candidate
                    p, q = row, column
        if maximum <= tolerance:
            break
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        phi = 0.5 * math.atan2(2.0 * apq, aqq - app)
        cosine, sine = math.cos(phi), math.sin(phi)
        for index in range(size):
            if index in (p, q):
                continue
            aip, aiq = a[index][p], a[index][q]
            a[index][p] = a[p][index] = cosine * aip - sine * aiq
            a[index][q] = a[q][index] = sine * aip + cosine * aiq
        a[p][p] = cosine * cosine * app - 2.0 * sine * cosine * apq + sine * sine * aqq
        a[q][q] = sine * sine * app + 2.0 * sine * cosine * apq + cosine * cosine * aqq
        a[p][q] = a[q][p] = 0.0
        for index in range(size):
            vip, viq = vectors[index][p], vectors[index][q]
            vectors[index][p] = cosine * vip - sine * viq
            vectors[index][q] = sine * vip + cosine * viq
    else:
        raise GeometryError("symmetric eigensolver did not converge")
    values = [a[index][index] for index in range(size)]
    return values, vectors


def symmetric_eigensystem(
    matrix: Sequence[Sequence[float]], *, descending: bool = False
) -> Tuple[Tuple[float, ...], Tuple[Tuple[float, ...], ...]]:
    """Return eigenvalues and matching column eigenvectors."""
    values, vectors = _jacobi_eigen_symmetric(matrix)
    order = sorted(range(len(values)), key=lambda index: values[index], reverse=descending)
    ordered_values = tuple(values[index] for index in order)
    ordered_vectors = tuple(
        tuple(vectors[row][index] for row in range(len(vectors))) for index in order
    )
    return ordered_values, ordered_vectors


def covariance_eigensystem(
    points: Sequence[Sequence[float]], weights: Optional[Sequence[float]] = None
) -> Tuple[Tuple[float, ...], Tuple[Tuple[float, ...], ...], Tuple[float, float, float]]:
    """Return descending covariance eigenpairs and the point centroid."""
    if len(points) < 2:
        raise GeometryError("at least two points are required for covariance")
    clean_points = [_vector3(point, "point") for point in points]
    clean_weights = [1.0] * len(points) if weights is None else [float(value) for value in weights]
    if len(clean_weights) != len(clean_points):
        raise GeometryError("weights and points must have the same length")
    center = centroid(clean_points, clean_weights)
    total = sum(clean_weights)
    covariance = [[0.0] * 3 for _ in range(3)]
    for point, weight in zip(clean_points, clean_weights):
        relative = subtract(point, center)
        for row in range(3):
            for column in range(3):
                covariance[row][column] += weight * relative[row] * relative[column] / total
    values, vectors = symmetric_eigensystem(covariance, descending=True)
    return values, vectors, center


def rotation_from_to(
    source: Sequence[float], target: Sequence[float], *, tolerance: float = 1.0e-10
) -> Tuple[float, ...]:
    """Return a proper 3x3 minimal rotation from one direction to another."""
    a = normalize(source)
    b = normalize(target)
    cosine = max(-1.0, min(1.0, dot(a, b)))
    if cosine >= 1.0 - tolerance:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    if cosine <= -1.0 + tolerance:
        basis = (1.0, 0.0, 0.0) if abs(a[0]) <= abs(a[1]) and abs(a[0]) <= abs(a[2]) else (
            (0.0, 1.0, 0.0) if abs(a[1]) <= abs(a[2]) else (0.0, 0.0, 1.0)
        )
        axis = normalize(cross(a, basis))
        x, y, z = axis
        return (
            2.0 * x * x - 1.0, 2.0 * x * y, 2.0 * x * z,
            2.0 * y * x, 2.0 * y * y - 1.0, 2.0 * y * z,
            2.0 * z * x, 2.0 * z * y, 2.0 * z * z - 1.0,
        )
    axis = cross(a, b)
    sine = norm(axis)
    x, y, z = scale(axis, 1.0 / sine)
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


def rmsd(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    if len(left) != len(right) or not left:
        raise GeometryError("RMSD inputs must have the same positive length")
    total = 0.0
    for a, b in zip(left, right):
        delta = subtract(a, b)
        total += dot(delta, delta)
    return math.sqrt(total / len(left))


@dataclass(frozen=True)
class RigidFit:
    """Result of fitting one ordered point cloud onto another."""

    matrix: Tuple[float, ...]
    rmsd_before: float
    rmsd_after: float
    point_count: int
    mobile_center: Tuple[float, float, float]
    target_center: Tuple[float, float, float]
    spread_eigenvalues: Tuple[float, ...]


def fit_rigid_transform(
    mobile: Sequence[Sequence[float]],
    target: Sequence[Sequence[float]],
    weights: Optional[Sequence[float]] = None,
    *,
    degeneracy_tolerance: float = 1.0e-8,
) -> RigidFit:
    """Fit a proper rigid transform with Horn's quaternion method.

    At least three non-collinear corresponding points are required.  Point
    order defines correspondence.
    """
    if len(mobile) != len(target):
        raise GeometryError("mobile and target must contain the same number of points")
    if len(mobile) < 3:
        raise GeometryError("at least three corresponding points are required")
    mobile_points = [_vector3(point, "mobile point") for point in mobile]
    target_points = [_vector3(point, "target point") for point in target]
    clean_weights = [1.0] * len(mobile_points) if weights is None else [float(value) for value in weights]
    if len(clean_weights) != len(mobile_points):
        raise GeometryError("weights and points must have the same length")
    if any((not math.isfinite(value)) or value <= 0.0 for value in clean_weights):
        raise GeometryError("rigid-fit weights must be finite and positive")

    spread, _, mobile_center = covariance_eigensystem(mobile_points, clean_weights)
    target_spread, _, target_center = covariance_eigensystem(target_points, clean_weights)
    scale_reference = max(spread[0], target_spread[0], EPSILON)
    if spread[1] <= degeneracy_tolerance * scale_reference:
        raise GeometryError("mobile anchor points are collinear or coincident")
    if target_spread[1] <= degeneracy_tolerance * scale_reference:
        raise GeometryError("target anchor points are collinear or coincident")

    covariance = [[0.0] * 3 for _ in range(3)]
    for p, q, weight in zip(mobile_points, target_points, clean_weights):
        pc = subtract(p, mobile_center)
        qc = subtract(q, target_center)
        for row in range(3):
            for column in range(3):
                covariance[row][column] += weight * pc[row] * qc[column]
    sxx, sxy, sxz = covariance[0]
    syx, syy, syz = covariance[1]
    szx, szy, szz = covariance[2]
    trace = sxx + syy + szz
    horn = (
        (trace, syz - szy, szx - sxz, sxy - syx),
        (syz - szy, sxx - syy - szz, sxy + syx, szx + sxz),
        (szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy),
        (sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz),
    )
    _, eigenvectors = symmetric_eigensystem(horn, descending=True)
    quaternion = eigenvectors[0]
    if quaternion[0] < 0.0:
        quaternion = tuple(-value for value in quaternion)
    qw, qx, qy, qz = normalize_quaternion(quaternion)
    rotation = quaternion_to_rotation((qw, qx, qy, qz))
    rotated_center = (
        rotation[0] * mobile_center[0] + rotation[1] * mobile_center[1] + rotation[2] * mobile_center[2],
        rotation[3] * mobile_center[0] + rotation[4] * mobile_center[1] + rotation[5] * mobile_center[2],
        rotation[6] * mobile_center[0] + rotation[7] * mobile_center[1] + rotation[8] * mobile_center[2],
    )
    translation = subtract(target_center, rotated_center)
    matrix = compose_rigid(rotation, translation)
    transformed = [apply_matrix(matrix, point) for point in mobile_points]
    return RigidFit(
        matrix=matrix,
        rmsd_before=rmsd(mobile_points, target_points),
        rmsd_after=rmsd(transformed, target_points),
        point_count=len(mobile_points),
        mobile_center=mobile_center,
        target_center=target_center,
        spread_eigenvalues=spread,
    )


def normalize_quaternion(value: Sequence[float]) -> Tuple[float, float, float, float]:
    if len(value) != 4:
        raise GeometryError("quaternion must contain exactly four values")
    values = tuple(float(item) for item in value)
    length = math.sqrt(sum(item * item for item in values))
    if length <= EPSILON:
        raise GeometryError("cannot normalize a zero quaternion")
    return tuple(item / length for item in values)  # type: ignore[return-value]


def quaternion_to_rotation(quaternion: Sequence[float]) -> Tuple[float, ...]:
    w, x, y, z = normalize_quaternion(quaternion)
    return (
        1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w),
        2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w),
        2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y),
    )


def parse_vector(value: object) -> Tuple[float, float, float]:
    """Parse a three-vector from a sequence or comma/space-delimited string."""
    if isinstance(value, str):
        parts = [part for part in value.replace(",", " ").split() if part]
        return _vector3([float(part) for part in parts])
    if isinstance(value, Iterable):
        return _vector3(list(value))  # type: ignore[arg-type]
    raise GeometryError("vector must be a sequence or string")
