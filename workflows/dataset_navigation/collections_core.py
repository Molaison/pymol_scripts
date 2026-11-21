"""Score-stratified, structurally diverse collections and headless PyMOL export."""

import argparse
import csv
import errno
import hashlib
import html
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import CancelledError, ThreadPoolExecutor, wait
from collections import deque
from contextlib import contextmanager
from itertools import product
from uuid import uuid4

ORIENTATION_KEYS = ("target", "binder", "orientation", "reference",
                    "frame_selection", "symmetry_axis")
STALE_POSE = "源结构坐标已变化，请重新应用朝向后再导出。"
SAMPLE_CSV_COLUMNS = ("id", "path", "stratum", "nearest_rmsd")


def orientation_config(config):
    defaults = {"target": "chain A", "binder": "chain B", "orientation": "target",
                "reference": "", "frame_selection": "polymer.protein", "symmetry_axis": None}
    return {key: config.get(key, value) for key, value in defaults.items()}


class ExportInProgress(RuntimeError):
    """Another export already owns this output directory."""


class CollectionEditInProgress(RuntimeError):
    """Another operation is modifying this Collection directory."""


class TaskControl:
    """Cancellation and progress shared by a task's coordinator and UI thread."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.state = ("准备", 0, 0)
        self.processes = set()

    def report(self, phase, completed, total):
        with self.lock:
            self.state = (phase, completed, total)

    def snapshot(self):
        with self.lock:
            return self.state

    def check(self):
        if self.cancelled.is_set():
            raise CancelledError("已取消任务，后台进程已退出。")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


import numpy as np


def _retry_windows_report_io(operation):
    """Wait briefly if native Windows sharing rules interrupt a worker receipt."""
    if os.name != "nt":
        return operation()
    deadline = time.monotonic() + 0.25
    while True:
        try:
            return operation()
        except PermissionError as error:
            winerror = getattr(error, "winerror", None)
            sharing_error = winerror in (5, 32) or (winerror is None and error.errno == errno.EACCES)
            if not sharing_error or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def write_json(path, value, *, retry_sharing=False):
    """Atomically write a JSON result so progress readers see complete records."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if retry_sharing:
        _retry_windows_report_io(lambda: temporary.replace(path))
    else:
        temporary.replace(path)


def _publish_task_progress(path, phase, completed, total):
    """Keep optional progress from interrupting a worker on a Windows sharing conflict."""
    try:
        write_json(path, {"phase": phase, "completed": completed, "total": total})
    except PermissionError:
        if os.name != "nt":
            raise
        path = Path(path)
        try:
            path.with_suffix(path.suffix + ".tmp").unlink(missing_ok=True)
        except PermissionError:
            pass


def _is_rosetta_score_header(parts):
    if not parts or parts[0] != "SCORE:" or parts[-1] != "description":
        return False
    for value in parts[1:-1]:
        try:
            float(value)
        except ValueError:
            continue
        return False
    return True


def metric_bounds(spec):
    """Return validated numeric score bounds, preserving None as unbounded."""
    name = spec.get("name", "打分")
    bounds = []
    for key, label in (("min", "最低值"), ("max", "最高值")):
        value = spec.get(key)
        if value is None:
            bounds.append(None)
            continue
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} {label}必须是数字。") from None
        if not math.isfinite(value):
            raise ValueError(f"{name} {label}必须是有限数值。")
        bounds.append(value)
    minimum, maximum = bounds
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(f"{name} 最低值大于最高值。")
    return minimum, maximum


def _reject_duplicate_json_keys(pairs):
    row = dict(pairs)
    if len(row) != len(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                raise ValueError(f"JSON 打分文件的对象字段“{key}”重复。")
            seen.add(key)
    return row


def read_scores(path):
    """Read CSV/TSV, a JSON row list, or a Rosetta SCORE table.

    Args:
        path: Score table filename.
    Returns:
        List of row dictionaries.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        text = path.read_text(encoding="utf-8-sig")
        rows = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON 打分文件必须是由行对象组成的列表。")
        return rows
    if suffix in (".sc", ".score"):
        text = path.read_text(encoding="utf-8-sig")
        header, rows = None, []
        for line in text.splitlines():
            parts = line.split()
            if not parts or parts[0] != "SCORE:":
                continue
            if _is_rosetta_score_header(parts):
                header = parts[1:]
                if len(header) != len(set(header)):
                    raise ValueError("Rosetta SCORE 表头存在重复字段。")
            elif header:
                if len(parts) - 1 != len(header):
                    raise ValueError("Rosetta SCORE 行的列数与表头不一致。")
                rows.append(dict(zip(header, parts[1:])))
        if header is None:
            raise ValueError("未找到 Rosetta SCORE 表头。")
        return rows
    delimiter = "\t" if suffix == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return _read_delimited_scores(stream, delimiter)


def _read_delimited_scores(stream, delimiter):
    reader = csv.reader(stream, delimiter=delimiter)
    fieldnames = next(reader, [])
    width = len(fieldnames)
    repeated_fields = []
    if width != len(set(fieldnames)):
        prefix_width = len(SAMPLE_CSV_COLUMNS)
        if (delimiter != "," or tuple(fieldnames[:prefix_width]) != SAMPLE_CSV_COLUMNS
                or any(name in SAMPLE_CSV_COLUMNS for name in fieldnames[prefix_width:])):
            raise ValueError("打分表的列名不能重复。")
        # Legacy sample exports could write the same metric in two identical columns.
        first_column = {}
        for column, name in enumerate(fieldnames):
            first = first_column.setdefault(name, column)
            if first != column:
                repeated_fields.append((first, column))
    rows = []
    for values in reader:
        if not values:
            continue
        if repeated_fields and (len(values) != width or any(
            values[first] != values[column] for first, column in repeated_fields
        )):
            raise ValueError("打分表的重复列包含不一致的分数。")
        row = dict(zip(fieldnames, values))
        if len(values) < width:
            row.update(dict.fromkeys(fieldnames[len(values):]))
        if len(values) > width:
            row[None] = values[width:]
        rows.append(row)
    if rows and (None in rows[0] or not all(rows[0])):
        raise ValueError("打分表的列名或分隔符不正确。")
    return rows


def relative_source_fields(config):
    """Return saved dataset paths whose original base directory is unknown."""
    return tuple(name for name in ("scores", "root")
                 if config.get(name) and not Path(config[name]).is_absolute())


def prepare_rows(config):
    """Resolve structure paths and filter rows against selected numeric scores."""
    score_path = Path(config["scores"]).resolve()
    root = Path(config.get("root") or score_path.parent).resolve()
    specs = config["metrics"]
    if not specs:
        raise ValueError("请至少选择一个打分列。")
    bounds = [(spec["name"], *metric_bounds(spec)) for spec in specs]
    rows, skipped, seen = [], [], set()
    path_column = config.get("path_column", "path")
    for number, source in enumerate(read_scores(score_path), 2):
        try:
            scores = {spec["name"]: float(source[spec["name"]]) for spec in specs}
            if not all(math.isfinite(score) for score in scores.values()):
                raise ValueError("打分不是有限数值")
            if any(
                (minimum is not None and scores[name] < minimum)
                or (maximum is not None and scores[name] > maximum)
                for name, minimum, maximum in bounds
            ):
                skipped.append({"row": number, "reason": "分数范围筛选"})
                continue
            raw = source[path_column]
            value = "" if raw is None else str(raw)
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("结构路径为空")
            try:
                path = _resolve_structure_path(root, value)
            except ValueError:
                if value == trimmed:
                    raise
                value = trimmed
                path = _resolve_structure_path(root, value)
            path = str(path.resolve())
            if os.path.normcase(path) in seen:
                raise ValueError("重复结构路径")
            seen.add(os.path.normcase(path))
            rows.append({"id": str(source.get("id") or source.get("description") or
                                   Path(value).name), "path": path, "scores": scores})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            skipped.append({"row": number, "reason": str(exc)})
    if not rows:
        raise ValueError("筛选后没有可用的结构与打分，请检查列名、路径和范围。")
    return rows, skipped


def _resolve_structure_path(root, value):
    path = root / value
    if path.is_file():
        return path
    extensions = (".pdb", ".cif", ".pdb.gz", ".cif.gz")
    if not value.lower().endswith(extensions):
        for ext in extensions:
            candidate = Path(str(path) + ext)
            if candidate.is_file():
                return candidate
    raise ValueError(f"找不到结构文件: {path}")


def _quantile_edges(values, bins):
    fractions = np.arange(1, bins) / bins
    with np.errstate(over="ignore", invalid="ignore"):
        estimates = np.quantile(values, fractions)
    overflowed = ~np.isfinite(estimates)
    if overflowed.any():
        # Halving the inputs keeps the gaps within the float64 range.
        estimates[overflowed] = 2 * np.quantile(values * 0.5, fractions[overflowed])
    cuts = np.unique(estimates)
    minimum = values.min()
    if cuts.size and cuts[0] == minimum:
        cuts = cuts[1:]
        if cuts.size:
            return cuts.tolist()
        higher = values[values > minimum]
        return [float(higher.min())] if higher.size else []
    return cuts.tolist()


def make_strata(rows, specs, seed, saved_edges=None):
    """Joint quantile cells with ordered cutoffs for repeated metric names."""
    ordered = isinstance(saved_edges, list)
    if ordered and len(saved_edges) != len(specs):
        raise ValueError("保存的分层边界与打分指标数量不一致。")
    axes, assignments, groups = [], [], {}
    for index, spec in enumerate(specs):
        bins = int(spec.get("bins", 3))
        if bins < 1:
            raise ValueError("每个打分的层数必须至少为 1。")
        name = spec["name"]
        values = np.asarray([row["scores"][name] for row in rows])
        if ordered:
            cuts = saved_edges[index]
        elif saved_edges is not None and name in saved_edges:
            cuts = saved_edges[name]
        else:
            cuts = _quantile_edges(values, bins)
        axes.append((name, cuts))
        assignments.append(np.searchsorted(cuts, values, side="right").tolist())
    for index, row in enumerate(rows):
        cell = tuple(positions[index] for positions in assignments)
        row["stratum"] = list(cell)
        groups.setdefault(cell, []).append(row)
    rng = random.Random(int(seed))
    cells = sorted(groups)
    rng.shuffle(cells)
    for cell in cells:
        rng.shuffle(groups[cell])
    edges = dict(axes)
    if len(edges) != len(axes):
        edges = [cuts for _, cuts in axes]
    return [groups[cell] for cell in cells], edges


def rigid_fit(mobile, reference):
    """Return a proper rigid transform, using row vectors (xyz @ rotation + shift)."""
    mobile, reference = np.asarray(mobile), np.asarray(reference)
    if len(mobile) < 3 or mobile.shape != reference.shape:
        raise ValueError("刚体配准需要至少 3 对对应原子。")
    left, _, right = np.linalg.svd(
        (mobile - mobile.mean(0)).T @ (reference - reference.mean(0)))
    correction = np.eye(3)
    correction[2, 2] = np.linalg.det(left @ right)
    rotation = left @ correction @ right
    return rotation, reference.mean(0) - mobile.mean(0) @ rotation


def pca_frame(coords):
    """Return a right-handed PCA frame with signs anchored to atom order."""
    coords = np.asarray(coords)
    if len(coords) < 3:
        raise ValueError("PCA 朝向需要至少 3 个原子。")
    center = coords.mean(0)
    _, _, vectors = np.linalg.svd(coords - center, full_matrices=False)
    axes = vectors.T
    for axis in range(2):
        projected = (coords - center) @ axes[:, axis]
        anchor = stable_extreme(np.abs(projected))
        if projected[anchor] < 0:
            axes[:, axis] *= -1
    axes[:, 2] = np.cross(axes[:, 0], axes[:, 1])
    return center, axes


def stable_extreme(values):
    """Use atom order to resolve geometric ties instead of floating-point noise."""
    values = np.asarray(values)
    maximum = values.max()
    return int(np.flatnonzero(values >= maximum - max(abs(maximum), 1) * 1e-7)[0])


def symmetry_frame(coords, centroids, axis=None):
    """Place a supplied or geometrically estimated cyclic axis vertically."""
    coords = np.asarray(coords)
    center = coords.mean(0)
    if axis is None:
        if len(centroids) < 3:
            raise ValueError("自动对称轴需要至少 3 条环状排列的链；C2 请填写轴向量。")
        centroids = np.asarray(centroids)
        center = centroids.mean(0)
        _, singular, vectors = np.linalg.svd(centroids - center, full_matrices=False)
        axis = vectors[-1]
        ring = centroids - center
        radii = np.linalg.norm(ring, axis=1)
        if (radii.mean() < 1e-6 or radii.std() / radii.mean() > 0.25
                or singular[-1] / max(singular[0], 1e-6) > 0.15):
            raise ValueError("链质心不符合平面环状对称，请指定对称选择或轴向量。")
        angles = np.sort(np.arctan2(ring @ vectors[1], ring @ vectors[0]))
        gaps = np.diff(np.r_[angles, angles[0] + 2 * np.pi])
        if gaps.std() / gaps.mean() > 0.25:
            raise ValueError("链质心角间距不符合环状对称，请指定轴向量。")
    axis = np.asarray(axis, dtype=float)
    if axis.shape != (3,) or not np.all(np.isfinite(axis)) or np.linalg.norm(axis) < 1e-8:
        raise ValueError("对称轴应为非零的有限三维向量，例如 0,0,1。")
    vertical = axis / np.linalg.norm(axis)
    projected = (coords - center) @ vertical
    anchor = stable_extreme(np.abs(projected))
    sign = projected[anchor]
    if abs(sign) < 1e-8 and len(centroids) >= 3:
        sign = np.dot(np.cross(np.asarray(centroids)[1] - centroids[0],
                               np.asarray(centroids)[2] - centroids[0]), vertical)
    if sign < 0:
        vertical = -vertical
    perpendicular = coords - center
    perpendicular -= (perpendicular @ vertical)[:, None] * vertical
    horizontal = perpendicular[stable_extreme(np.linalg.norm(perpendicular, axis=1))]
    if np.linalg.norm(horizontal) < 1e-8:
        raise ValueError("原子都位于对称轴上，无法确定侧向。")
    horizontal = horizontal / np.linalg.norm(horizontal)
    return center, np.column_stack((horizontal, vertical, np.cross(horizontal, vertical)))


def selection(obj, expression, ca=False):
    result = f"(model {obj}) and ({expression})"
    return result + " and polymer.protein and name CA" if ca else result


def ca_coordinates(cmd, expression, cache):
    if expression not in cache:
        cache[expression] = {atom.index: atom.coord for atom in cmd.get_model(expression, 1).atom}
    return cache[expression]


def matched_atoms(cmd, mobile, reference, expression, coverage=0.8, atom_cache=None):
    """Use native sequence/structure matching without rejecting high-RMSD atoms."""
    first, second = selection(mobile, expression, True), selection(reference, expression, True)
    cache = {} if atom_cache is None else atom_cache
    first_atoms = ca_coordinates(cmd, first, cache)
    second_atoms = ca_coordinates(cmd, second, cache)
    if min(len(first_atoms), len(second_atoms)) < 3:
        return None
    # Native super can collapse repetitive/poly-Gly models to a single match.
    for method in (cmd.super, cmd.align):
        method(first, second, cycles=0, transform=0, object="col_match",
               mobile_state=1, target_state=1, quiet=1)
        points = []
        for pair in cmd.get_raw_alignment("col_match"):
            indices = dict(pair)
            if mobile in indices and reference in indices:
                points.append((first_atoms[indices[mobile]], second_atoms[indices[reference]]))
        cmd.delete("col_match")
        if len(points) >= 3 and len(points) / max(len(first_atoms), len(second_atoms), 1) >= coverage:
            return np.array([x[0] for x in points]), np.array([x[1] for x in points])
    return None


def structural_distance(cmd, mobile, reference, config, atom_cache=None):
    """Binder/selection shape RMSD or binder-pose RMSD after target alignment."""
    expression = config.get("diversity_selection") or config.get("binder", "chain B")
    pairs = matched_atoms(cmd, mobile, reference, expression,
                          float(config.get("coverage", 0.8)), atom_cache)
    if pairs is None:
        return None
    if config.get("diversity", "shape") == "pose":
        anchors = matched_atoms(cmd, mobile, reference, config.get("target", "chain A"),
                                float(config.get("coverage", 0.8)), atom_cache)
        if anchors is None:
            raise ValueError("结合姿态去重无法配准 target；请检查 target 选择与覆盖率。")
        rotation, shift = rigid_fit(*anchors)
    else:
        rotation, shift = rigid_fit(*pairs)
    difference = pairs[0] @ rotation + shift - pairs[1]
    return float(np.sqrt(np.mean(np.sum(difference ** 2, axis=1))))


def candidate_pose(cmd, obj, config):
    mode = config.get("orientation", "target")
    required = [config.get("diversity_selection") or config.get("binder", "chain B")]
    if mode in ("target", "binder"):
        required.append(config.get(mode, "chain A" if mode == "target" else "chain B"))
    if config.get("diversity", "shape") == "pose":
        required.append(config.get("target", "chain A"))
    for expression in required:
        if cmd.count_atoms(selection(obj, expression, True)) < 3:
            raise ValueError(f"选择中少于 3 个 CA 原子: {expression}")
    if mode in ("target", "binder") and "col_reference" not in cmd.get_names("objects"):
        cmd.create("col_reference", obj, source_state=1, target_state=1)
    return canonical_pose(cmd, obj, config)


def check_diversity(cmd, row, config, selected):
    threshold = float(config.get("rmsd", 2))
    shape = config.get("diversity", "shape") == "shape"
    comparable, incomparable = [], 0
    atom_cache = {}
    for index in range(len(selected)):
        distance = structural_distance(cmd, "col_candidate", f"col_keep_{index}", config, atom_cache)
        if distance is None:
            incomparable += 1
        elif shape and distance < threshold:
            return "duplicate"
        else:
            comparable.append(distance)
    nearest = min(comparable) if comparable else None
    row["nearest_rmsd"] = nearest
    row["incomparable_with_previous"] = incomparable
    row["diversity_status"] = "unverified" if incomparable else "verified"
    if nearest is not None and nearest < threshold:
        return "duplicate"
    if incomparable and not config.get("allow_unverified", False):
        return "unverified"
    return "accepted"


def select_diverse(cmd, queues, config, selected=None, progress=None):
    """Round-robin occupied score cells, rejecting near-duplicates globally."""
    selected, rejected, invalid, unverified = list(selected or []), 0, [], []
    count, threshold = int(config.get("count", 24)), float(config.get("rmsd", 2.0))
    if count < 1 or not 0 < threshold < math.inf or not 0 < float(config.get("coverage", 0.8)) <= 1:
        raise ValueError("样本数、RMSD 阈值和覆盖率必须为有效正数。")
    processed, total = 0, sum(len(queue) for queue in queues)
    while any(queues) and len(selected) < count:
        for queue in queues:
            while queue:
                row = queue.pop()
                try:
                    cmd.load(row["path"], "col_candidate", quiet=1)
                    row["matrix"] = candidate_pose(cmd, "col_candidate", config).tolist()
                    decision = check_diversity(cmd, row, config, selected)
                    if decision == "duplicate":
                        rejected += 1
                        continue
                    if decision == "unverified":
                        unverified.append({"path": row["path"], "reason": "CA 对应覆盖率不足，未验证结构差异"})
                        continue
                    cmd.set_name("col_candidate", f"col_keep_{len(selected)}")
                    selected.append(row)
                    break
                except Exception as exc:
                    traceback.print_exc()
                    invalid.append({"path": row["path"], "reason": str(exc)})
                finally:
                    cmd.delete("col_candidate")
                    processed += 1
                    if progress:
                        progress(processed, total)
            if len(selected) >= count:
                break
    return selected, rejected, invalid, unverified


def canonical_pose(cmd, obj, config):
    mode = config.get("orientation", "target")
    rotation, shift = np.eye(3), np.zeros(3)
    if mode in ("target", "binder"):
        pairs = matched_atoms(cmd, obj, "col_reference", config.get(mode, f"chain {'A' if mode == 'target' else 'B'}"),
                              float(config.get("coverage", 0.8)))
        if pairs is None:
            raise ValueError(f"{mode} 对齐的 CA 覆盖率不足，请检查链选择或参考结构。")
        rotation, shift = rigid_fit(*pairs)
        anchor = cmd.get_coords(selection("col_reference", config.get(mode, "polymer.protein"), True), 1)
        center, axes = pca_frame(anchor)
    else:
        expression = config.get("frame_selection", "polymer.protein")
        coords = cmd.get_coords(selection(obj, expression, True), 1)
        if coords is None:
            raise ValueError("PCA / 对称轴选择为空。")
        if mode == "pca":
            center, axes = pca_frame(coords)
        elif mode == "symmetry":
            centroids = [cmd.get_coords(selection(obj, f"({expression}) and chain \"{chain}\"", True), 1).mean(0)
                         for chain in cmd.get_chains(selection(obj, expression, True))]
            center, axes = symmetry_frame(coords, centroids, config.get("symmetry_axis"))
        else:
            raise ValueError(f"未知朝向模式: {mode}")
    rotation, shift = rotation @ axes, (shift - center) @ axes
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = rotation.T, shift
    return matrix


def coordinate_signature(coords):
    return hashlib.sha256(np.asarray(coords, dtype="<f4").tobytes()).hexdigest()


def _reference_frame_signature(cmd, config):
    """Identify the native reference used for matching and orienting the collection."""
    mode = config.get("orientation", "target")
    align = config.get(mode, "chain A" if mode == "target" else "chain B")
    axes = config.get(mode, "polymer.protein")
    model = cmd.get_model("model col_reference", 1)
    # Native super uses the reference's residue identities and bonds as well as its selected CAs.
    atoms = [(atom.index, atom.segi, atom.chain, atom.resi, atom.resn, atom.name,
              atom.alt, atom.symbol, atom.flags, atom.coord) for atom in model.atom]
    bonds = [(bond.index, bond.order) for bond in model.bond]
    anchors = [[index for _, index in cmd.index(selection("col_reference", expression, True))]
               for expression in dict.fromkeys((align, axes))]
    return hashlib.sha256(json.dumps([atoms, bonds, anchors], separators=(",", ":")).encode("utf-8")).hexdigest()


def apply_pose(cmd, obj, matrix, *, expected_coordinates=None, bounds=None):
    matrix = np.asarray(matrix)
    coords = cmd.get_coords(f"model {obj}", 1)
    if expected_coordinates is not None and coordinate_signature(coords) != expected_coordinates:
        raise ValueError(STALE_POSE)
    coords = coords @ matrix[:3, :3].T + matrix[:3, 3]
    if bounds is not None and expected_coordinates is None:
        # Older manifests have only the collection bounds to check a stored pose against.
        low, high = np.asarray(bounds)
        if np.any(coords.min(axis=0) < low - 0.001) or np.any(coords.max(axis=0) > high + 0.001):
            raise ValueError(STALE_POSE)
    cmd.load_coords(coords, f"model {obj}", 1)


def common_view(low, high, width, height, rotation=None):
    """Orthographic camera with a common center and scale across the collection."""
    low, high = np.array(low), np.array(high)
    center = (low + high) / 2
    rotation = np.eye(3) if rotation is None else np.asarray(rotation).reshape(3, 3)
    corners = np.array(list(product(*zip(low, high))))
    projected = (corners - center) @ rotation.T
    size = np.ptp(projected, axis=0)
    extent = max(size[1], size[0] * height / width, 4) * 1.15
    distance = extent / (2 * math.tan(math.radians(10)))
    depth = max(size[2] / 2, 2) + extent
    return [*rotation.ravel().tolist(), 0., 0., -distance,
            *center.tolist(), max(0.1, distance - depth), distance + depth, -20.]


def collection_view(manifest, width, height):
    saved = manifest.get("view")
    return common_view(*manifest["bounds"], width, height, rotation=saved[:9] if saved else None)


def preview_size(width, height):
    scale = max(64 / min(width, height), min(1, 640 / max(width, height)))
    return round(width * scale), round(height * scale)


def update_bounds(cmd, samples):
    low, high = np.full(3, np.inf), np.full(3, -np.inf)
    for index, row in enumerate(samples):
        coords = cmd.get_coords(f"model col_keep_{index}", 1)
        row["coordinate_signature"] = coordinate_signature(coords)
        matrix = np.asarray(row["matrix"])
        coords = coords @ matrix[:3, :3].T + matrix[:3, 3]
        low, high = np.minimum(low, coords.min(0)), np.maximum(high, coords.max(0))
        token = "".join(character if character.isalnum() or character in "_-" else "_"
                        for character in row["id"])[:70]
        row.setdefault("image", f"{index + 1:04d}_{token or 'structure'}.png")
    return [low.tolist(), high.tolist()] if samples else None


def save_collection(path, manifest):
    manifest["covered_strata"] = len({tuple(row["stratum"]) for row in manifest["samples"]})
    manifest["shortfall"] = max(0, int(manifest["config"].get("count", 24)) - len(manifest["samples"]))
    path = Path(path)
    csv_path = path.parent / "02_samples.csv"
    pending = Path(mkdtemp(prefix=".collection-save-", dir=path.parent))
    backup = pending / "previous_samples.csv"
    keep_backup = False
    try:
        staged_manifest = pending / "manifest.json"
        staged_csv = pending / csv_path.name
        write_json(staged_manifest, manifest)
        write_samples_csv(pending, manifest)
        had_csv = csv_path.exists()
        if had_csv:
            shutil.copy2(csv_path, backup)
            shutil.copymode(csv_path, staged_csv)
        staged_csv.replace(csv_path)
        try:
            # Publish the authoritative manifest after the sample table.
            staged_manifest.replace(path)
        except BaseException:
            try:
                if had_csv:
                    backup.replace(csv_path)
                else:
                    csv_path.unlink()
            except OSError as recovery_error:
                keep_backup = True
                raise RuntimeError(f"无法恢复原样本表，请查看 {pending}。") from recovery_error
            raise
    finally:
        if not keep_backup:
            shutil.rmtree(pending, ignore_errors=True)


@contextmanager
def edit_collection(manifest_path):
    """Read and save the manifest under the same directory lock."""
    manifest_path = Path(manifest_path).resolve()
    with _collection_edit_lock(manifest_path.parent):
        manifest = read_json(manifest_path)
        yield manifest
        save_collection(manifest_path, manifest)


def create_collection(config, output, progress=None, *, require_new=False):
    """Select samples, freeze their poses, and save a reproducible manifest."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with _collection_edit_lock(output):
        if require_new and (output / "01_collection.json").exists():
            raise ValueError("此目录已存在 collection，请选择新目录。")
        return _create_collection(config, output, progress)


def _create_collection(config, output, progress):
    import pymol2

    config = {**config, "scores": str(Path(config["scores"]).resolve())}
    if config.get("root"):
        config["root"] = str(Path(config["root"]).resolve())
    rows, skipped = prepare_rows(config)
    queues, edges = make_strata(rows, config["metrics"], config.get("seed", 0))
    reference = str(Path(config["reference"]).resolve()) if config.get("reference") else None
    if reference:
        config = {**config, "reference": reference}
    reference_signature = None
    with pymol2.PyMOL() as instance:
        command = instance.cmd
        command.set("max_threads", 1)
        if reference:
            command.load(reference, "col_reference", quiet=1)
        selected, rejected, invalid, unverified = select_diverse(command, queues, config, progress=progress)
        reference = reference or (selected[0]["path"] if selected else None)
        if selected and config.get("orientation", "target") in ("target", "binder"):
            reference_signature = _reference_frame_signature(command, config)
        bounds = update_bounds(command, selected)
    manifest = {"version": 1, "config": config, "reference": reference,
                "samples": selected, "quantile_edges": edges, "occupied_strata": len(queues),
                "eligible": len(rows), "rejected_duplicates": rejected, "skipped": skipped,
                "invalid_structures": invalid, "unverified_candidates": unverified, "bounds": bounds}
    if reference_signature is not None:
        manifest["reference_frame_signature"] = reference_signature
    save_collection(output / "01_collection.json", manifest)
    return manifest


def write_samples_csv(output, manifest):
    metrics = dict.fromkeys(spec["name"] for spec in manifest["config"]["metrics"])
    occupied = set(SAMPLE_CSV_COLUMNS).union(metrics)
    for name in metrics:
        column = name
        if name in SAMPLE_CSV_COLUMNS:
            while column in occupied:
                column = f"score_{column}"
            occupied.add(column)
        metrics[name] = column
    with (Path(output) / "02_samples.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*SAMPLE_CSV_COLUMNS, *metrics.values()])
        writer.writeheader()
        for row in manifest["samples"]:
            values = {key: row[key] for key in ("id", "path", "nearest_rmsd")}
            values["stratum"] = "/".join(str(x + 1) for x in row["stratum"])
            values.update({metrics[name]: value for name, value in row["scores"].items()})
            writer.writerow(values)


def _load_saved_sample(command, row, obj):
    expected = row.get("coordinate_signature")
    if expected is None:
        raise ValueError(f"{row['id']}：旧清单缺少坐标签名，请先重新应用朝向。")
    command.load(row["path"], obj, quiet=1)
    coords = command.get_coords(f"model {obj}", 1)
    if coords is None or coordinate_signature(coords) != expected:
        raise ValueError(f"{row['id']}：{STALE_POSE}")


def reorient_samples(command, manifest, progress=None):
    config = manifest["config"]
    saved_reference = manifest.get("reference")
    reference = config.get("reference") or saved_reference
    aligns_to_reference = config.get("orientation", "target") in ("target", "binder")
    if reference and aligns_to_reference:
        if not Path(reference).is_absolute():
            if not saved_reference or not Path(saved_reference).is_absolute():
                raise ValueError("旧清单没有记录参考结构的绝对路径，请重新选择参考结构后应用朝向。")
            reference = saved_reference
        command.load(reference, "col_reference", quiet=1)
    valid, failed = [], []
    for index, row in enumerate(manifest["samples"]):
        try:
            command.load(row["path"], "col_candidate", quiet=1)
            row["matrix"] = candidate_pose(command, "col_candidate", config).tolist()
            command.set_name("col_candidate", f"col_keep_{len(valid)}")
            valid.append(row)
        except Exception as error:
            traceback.print_exc()
            failed.append({"path": row["path"], "reason": str(error)})
        finally:
            command.delete("col_candidate")
        if progress:
            progress(index + 1, len(manifest["samples"]))
    if not valid:
        raise ValueError(f"没有样本可以应用新朝向，原 collection 已保留。错误: {failed[:1]}")
    if any(row.get("pinned") and row["path"] in {failure["path"] for failure in failed}
           for row in manifest["samples"]):
        raise ValueError("固定样本无法应用新朝向，原 collection 已保留；请修正链选择或解除固定。")
    manifest["samples"] = valid
    manifest["invalid_structures"].extend(failed)
    manifest["bounds"] = update_bounds(command, valid)
    if aligns_to_reference:
        reference = reference or valid[0]["path"]
        manifest["reference_frame_signature"] = _reference_frame_signature(command, config)
    else:
        manifest.pop("reference_frame_signature", None)
    manifest["reference"] = reference


def replace_sample(command, manifest, index, progress=None):
    samples, config = manifest["samples"], dict(manifest["config"])
    original = samples[index]
    if config.get("orientation", "target") in ("target", "binder"):
        reference = manifest.get("reference")
        expected = manifest.get("reference_frame_signature")
        if not reference or expected is None:
            raise ValueError("清单缺少参考结构朝向签名，请先重新应用朝向。")
        command.load(reference, "col_reference", quiet=1)
        if _reference_frame_signature(command, config) != expected:
            raise ValueError("参考结构的对齐数据已变化，请先重新应用朝向。")
    rows, _ = prepare_rows(config)
    queues, _ = make_strata(rows, config["metrics"], config.get("seed", 0), manifest.get("quantile_edges"))
    excluded = {row["path"] for row in samples} | set(manifest.get("excluded_paths", []))
    queues = [[row for row in queue if row["path"] not in excluded] for queue in queues]
    queues.sort(key=lambda queue: not queue or queue[0]["stratum"] != original["stratum"])
    for position, row in enumerate(samples):
        _load_saved_sample(command, row, f"col_keep_{position}")
    config["count"] = len(samples) + 1
    chosen, _, _, _ = select_diverse(command, queues, config, selected=samples, progress=progress)
    if len(chosen) == len(samples):
        raise ValueError("没有其他满足分层与结构差异要求的候选；原样本已保留。")
    replacement = chosen[-1]
    coords = command.get_coords(f"model col_keep_{len(samples)}", 1)
    replacement["coordinate_signature"] = coordinate_signature(coords)
    _load_saved_sample(command, replacement, "col_candidate")
    command.delete(f"col_keep_{len(samples)}")
    command.delete(f"col_keep_{index}")
    command.set_name("col_candidate", f"col_keep_{index}")
    replacement["image"] = original["image"]
    samples[index] = replacement
    manifest.setdefault("excluded_paths", []).append(original["path"])


def revise_collection(manifest_path, request, progress=None):
    """Apply an orientation or sample edit, committing only a usable result."""
    with edit_collection(manifest_path) as manifest:
        _revise_collection(manifest, request, progress)
    return manifest


def sample_identity(sample):
    """Identify a selected sample by its saved identifier and source path."""
    return [sample["id"], sample["path"]]


def view_frame(manifest):
    """Values that define the coordinate frame shown in the structure preview."""
    samples = [(sample_identity(row), row["matrix"], row.get("coordinate_signature"))
               for row in manifest["samples"]]
    return orientation_config(manifest["config"]), manifest.get("reference"), manifest["bounds"], samples


def _selected_indices(manifest, request):
    if "selected_samples" not in request:
        return request.get("indices", [])
    indices = []
    for selected in request["selected_samples"]:
        matches = [index for index, row in enumerate(manifest["samples"])
                   if sample_identity(row) == selected]
        if len(matches) != 1:
            raise ValueError("所选样本已变化，请刷新后重新选择。")
        indices.append(matches[0])
    return indices


def _revise_collection(manifest, request, progress):
    import pymol2

    action = request["action"]
    indices = _selected_indices(manifest, request)
    for index in indices:
        if not 0 <= index < len(manifest["samples"]):
            raise ValueError("样本编号超出范围。")
        if action in ("remove", "replace") and manifest["samples"][index].get("pinned"):
            raise ValueError("请先解除固定，再删除或替换该样本。")
    if action == "pin":
        if "pinned" in request:
            pinned = request["pinned"]
            if not isinstance(pinned, bool):
                raise ValueError("固定状态必须为 true 或 false。")
        else:
            pinned = not all(manifest["samples"][index].get("pinned", False) for index in indices)
        for index in indices:
            manifest["samples"][index]["pinned"] = pinned
    else:
        if action == "orient":
            previous_orientation = orientation_config(manifest["config"])
            updates = dict(request["config"])
            chosen_reference = updates.get("reference")
            if chosen_reference and chosen_reference != manifest["config"].get("reference"):
                updates["reference"] = str(Path(chosen_reference).resolve())
            manifest["config"].update(updates)
            if orientation_config(manifest["config"]) != previous_orientation:
                manifest.pop("view", None)
        elif action == "remove":
            removed = [row for index, row in enumerate(manifest["samples"]) if index in indices]
            manifest["samples"] = [row for index, row in enumerate(manifest["samples"]) if index not in indices]
            manifest.setdefault("excluded_paths", []).extend(row["path"] for row in removed)
        elif action == "replace":
            config = manifest["config"]
            relative_fields = relative_source_fields(config)
            if relative_fields:
                supplied = request.get("source_paths", {})
                resolved = {}
                for name in relative_fields:
                    path = supplied.get(name)
                    if not path or not Path(path).is_absolute():
                        raise ValueError("旧清单未记录打分表或结构目录的绝对路径。请在数据与采样中重新选择，然后重试替换。")
                    resolved[name] = str(Path(path).resolve())
                manifest["config"] = {**config, **resolved}
        else:
            raise ValueError(f"未知修改: {action}")
        if not manifest["samples"]:
            raise ValueError("请保留至少一个样本。")
        with pymol2.PyMOL() as instance:
            command = instance.cmd
            command.set("max_threads", 1)
            if action == "orient":
                reorient_samples(command, manifest, progress)
                return
            if action == "replace":
                if len(indices) != 1:
                    raise ValueError("替换时请选择一个样本。")
                replace_sample(command, manifest, indices[0], progress)
            else:
                for position, row in enumerate(manifest["samples"]):
                    _load_saved_sample(command, row, f"col_keep_{position}")
                    if progress:
                        progress(position + 1, len(manifest["samples"]))
            manifest["bounds"] = update_bounds(command, manifest["samples"])


def style_structure(cmd, obj, config, render=False):
    cmd.hide("everything", f"model {obj}")
    cmd.show("cartoon", selection(obj, "polymer.protein"))
    cmd.show("sticks", selection(obj, "organic"))
    cmd.color("gray70", f"model {obj}")
    cmd.color("slate", selection(obj, config.get("target", "chain A")))
    cmd.color("orange", selection(obj, config.get("binder", "chain B")))
    if render:
        cmd.bg_color("white")
        for setting, value in {"orthoscopic": 1, "ray_opaque_background": 1,
                               "ray_shadows": 0, "depth_cue": 0, "specular": 0.2,
                               "antialias": 2}.items():
            cmd.set(setting, value)


def render_worker(manifest_path, indices, directory, width, height, threads, report_path,
                  run_id="", scratch_dir=None):
    import pymol2

    started = time.time()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    view = collection_view(manifest, width, height)
    results = []
    write_json(report_path, {"run_id": run_id, "results": []}, retry_sharing=True)
    with pymol2.PyMOL() as pm:
        cmd = pm.cmd
        cmd.set("max_threads", threads)
        cmd.viewport(width, height)
        for index in indices:
            row = manifest["samples"][index]
            destination = Path(directory) / row["image"]
            temporary = destination.with_name(destination.stem + ".part.png")
            try:
                cmd.delete("all")
                signature = render_signature(manifest, row, width, height)
                with TemporaryDirectory(prefix="source-", dir=scratch_dir) as staging:
                    snapshot = Path(staging) / Path(row["path"]).name
                    with snapshot.open("wb") as staged:
                        source_sha256 = _file_sha256(row["path"], copy_to=staged)
                    cmd.load(str(snapshot), "col_render", quiet=1)
                apply_pose(cmd, "col_render", row["matrix"],
                           expected_coordinates=row.get("coordinate_signature"), bounds=manifest["bounds"])
                style_structure(cmd, "col_render", manifest["config"], render=True)
                cmd.set_view(view)
                cmd.png(str(temporary), width=width, height=height, dpi=300, ray=1, quiet=1)
                if not temporary.is_file() or temporary.stat().st_size == 0:
                    raise RuntimeError(f"PyMOL 未写出图像: {destination}")
                image_sha256 = _file_sha256(temporary)
                temporary.replace(destination)
                results.append({"index": index, "image": str(destination), "ok": True,
                                "signature": signature, "source_sha256": source_sha256,
                                "image_sha256": image_sha256,
                                "source_snapshot": True})
            except Exception as exc:
                traceback.print_exc()
                results.append({"index": index, "ok": False, "error": str(exc)})
            finally:
                if temporary.exists():
                    temporary.unlink()
            write_json(report_path, {"run_id": run_id, "pid": os.getpid(), "started": started,
                                     "finished": time.time(), "results": results}, retry_sharing=True)
    return results


def worker_python():
    """Locate the Python interpreter belonging to the running PyMOL installation."""
    current = Path(sys.executable)
    if current.stem.lower().startswith("python"):
        return str(current)
    candidate = Path(sys.prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    if not candidate.is_file():
        raise ValueError("请填写安装了 pymol2 与 numpy 的 Python 可执行文件。")
    return str(candidate)


def run_process(arguments, log_path, control=None, progress_path=None, *, published_log=None):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with Path(log_path).open("w", encoding="utf-8") as log:
        if control:
            control.check()
        process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                                   env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if control:
            with control.lock:
                control.processes.add(process.pid)
        try:
            while process.poll() is None:
                if control and control.cancelled.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise CancelledError("已取消，后台进程已退出。")
                if control and progress_path:
                    try:
                        state = read_json(progress_path)
                    except FileNotFoundError:
                        pass
                    except PermissionError:
                        if os.name != "nt":
                            raise
                    else:
                        control.report(state["phase"], state["completed"], state["total"])
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
            if control:
                with control.lock:
                    control.processes.discard(process.pid)
    if process.returncode:
        with Path(log_path).open(encoding="utf-8", errors="replace") as handle:
            last_line = next(iter(deque((line.strip() for line in handle if line.strip()), maxlen=1)), "")
        raise RuntimeError(f"{last_line}\n后台退出码 {process.returncode}；日志: {published_log or log_path}")


def analyze_external(config, output, python=None, control=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with _collection_request_lock(output):
        if (output / "01_collection.json").exists():
            raise ValueError("此目录已存在 collection，请选择新目录。")
        write_json(output / "00_config.json", config)
        progress_path = output / "01_progress.json"
        _publish_task_progress(progress_path, "采样", 0, 0)
        run_process([python or worker_python(), str(Path(__file__).resolve()), "analyze",
                     "--config", str(output / "00_config.json"), "--output", str(output), "--require-new",
                     "--progress", str(progress_path)], output / "01_analysis.log", control, progress_path)
        return json.loads((output / "01_collection.json").read_text(encoding="utf-8"))


def revise_external(manifest_path, request, python=None, control=None):
    directory = Path(manifest_path).resolve().parent
    with _collection_request_lock(directory):
        request_path, progress_path = directory / "00_edit.json", directory / "01_progress.json"
        write_json(request_path, request)
        _publish_task_progress(progress_path, "调整 collection", 0, 0)
        run_process([python or worker_python(), str(Path(__file__).resolve()), "revise",
                     "--manifest", str(Path(manifest_path).resolve()), "--request", str(request_path),
                     "--progress", str(progress_path)], directory / "01_edit.log", control, progress_path)
        return read_json(manifest_path)


def render_signature(manifest, row, width, height):
    path = Path(row["path"])
    stat = path.stat() if path.is_file() else None
    identity = [row["path"], [stat.st_size, stat.st_mtime_ns] if stat else None,
                row.get("coordinate_signature"),
                row["matrix"], collection_view(manifest, width, height), width, height,
                manifest["config"].get("target", "chain A"), manifest["config"].get("binder", "chain B")]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _file_sha256(path, *, copy_to=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            if copy_to is not None:
                copy_to.write(chunk)
    return digest.hexdigest()


def _file_matches(path, previous_digest):
    if not previous_digest:
        return False
    try:
        return _file_sha256(path) == previous_digest
    except OSError:
        return False


def current_images(manifest, directory, width=None, height=None, *, verify_contents=False,
                   allow_legacy=False, control=None):
    report = Path(directory) / "03_export.json"
    if not report.exists() or not manifest["samples"]:
        return {}
    previous = read_json(report)
    width, height = width or previous["width"], height or previous["height"]
    by_image = {Path(row["image"]).name: row for row in previous["results"] if row.get("ok")}
    valid = {}
    for index, row in enumerate(manifest["samples"]):
        if control:
            control.check()
        previous_row = by_image.get(row["image"])
        image = Path(directory) / row["image"]
        if (not previous_row or not image.is_file()
                or previous_row.get("signature") != render_signature(manifest, row, width, height)):
            continue
        if verify_contents:
            if previous_row.get("source_snapshot") is True:
                if not (_file_matches(row["path"], previous_row.get("source_sha256"))
                        and _file_matches(image, previous_row.get("image_sha256"))):
                    continue
            elif not allow_legacy:
                continue
        valid[index] = dict(previous_row, index=index)
    return valid


def worker_report(path, run_id):
    try:
        report = _retry_windows_report_io(lambda: read_json(path))
    except FileNotFoundError:
        return {"results": []}
    return report if report.get("run_id") == run_id else {"results": []}


def publish_render_result(row, directory):
    """Publish a completed worker image at its stable collection path."""
    if not row["ok"]:
        return row
    image = Path(row["image"])
    destination = directory / image.name
    try:
        image.replace(destination)
    except OSError as error:
        traceback.print_exc()
        return {"index": row["index"], "ok": False, "error": str(error)}
    return dict(row, image=str(destination))


def collect_workers(futures, run_id, retained, count, control, progress, directory):
    pending, results, processes = set(futures), dict(retained), []
    while pending:
        completed, pending = wait(pending, timeout=0.1)
        for future, (report_path, indices) in futures.items():
            report = worker_report(report_path, run_id)
            for row in report["results"]:
                index = row["index"]
                if index not in results:
                    results[index] = publish_render_result(row, directory)
            if future not in completed:
                continue
            error, cancelled = None, False
            try:
                future.result()
            except Exception as exception:
                error, cancelled = str(exception), isinstance(exception, CancelledError)
            for index in indices:
                results.setdefault(index, {"index": index, "ok": False, "cancelled": cancelled,
                                           "error": error or "后台未返回此样本的结果"})
            if "pid" in report:
                processes.append({key: report[key] for key in ("pid", "started", "finished")})
        if control:
            control.report("渲染", len(results), count)
        if progress:
            progress(len(results), count)
    return list(results.values()), processes


def publish_worker_diagnostics(results, directory, scratch=None):
    """Keep public diagnostic files associated with the current export."""
    logs = list(scratch.glob("01_worker_*.log")) if scratch is not None else []
    reports = list(scratch.glob("02_worker_*.json")) if scratch is not None else []
    current_names = {path.name for path in (*logs, *reports)}
    for path in directory.iterdir():
        if (path.name not in current_names and path.is_file()
                and re.fullmatch(r"01_worker_[0-9]+\.log|02_worker_[0-9]+\.json", path.name)):
            path.unlink()
    for log in logs:
        log.replace(directory / log.name)
    by_index = {row["index"]: row for row in results}
    for report_path in reports:
        report = _retry_windows_report_io(lambda: read_json(report_path))
        report["results"] = [by_index[row["index"]] for row in report["results"]]
        write_json(directory / report_path.name, report)


def run_render_chunks(manifest, pending, directory, workers, size, python, retained, control, progress):
    width, height = size
    threads, run_id = max(1, (os.cpu_count() or 1) // workers), uuid4().hex
    # Workers can only write into this export's private scratch directory.
    with TemporaryDirectory(dir=directory, prefix=".render-") as scratch_dir:
        scratch = Path(scratch_dir)
        manifest_path = scratch / "01_collection.json"
        write_json(manifest_path, manifest)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for worker in range(workers):
                indices = pending[worker::workers]
                report = scratch / f"02_worker_{worker + 1:02d}.json"
                log = scratch / f"01_worker_{worker + 1:02d}.log"
                arguments = [python or worker_python(), str(Path(__file__).resolve()), "render",
                             "--manifest", str(manifest_path), "--indices", ",".join(map(str, indices)),
                             "--directory", scratch_dir, "--width", str(width), "--height", str(height),
                             "--threads", str(threads), "--report", str(report), "--run-id", run_id,
                             "--scratch-dir", scratch_dir]
                future = executor.submit(run_process, arguments, log, control,
                                         published_log=directory / log.name)
                futures[future] = report, indices
            results, processes = collect_workers(futures, run_id, retained, len(pending) + len(retained),
                                                control, progress, directory)
        publish_worker_diagnostics(results, directory, scratch)
    return results, processes, threads


def export_directory_lock(directory):
    """Own publication of one output directory for this export."""
    return _operation_lock(
        directory / ".export.lock",
        ExportInProgress(f"此目录已有导出任务，请等待任务结束（{directory}）。"),
    )


def _collection_edit_lock(directory):
    return _operation_lock(
        directory / ".manifest-edit.lock",
        CollectionEditInProgress(f"此目录已有修改任务，请等待任务结束（{directory}）。"),
    )


def _collection_request_lock(directory):
    return _operation_lock(
        directory / ".collection-request.lock",
        CollectionEditInProgress(f"此目录已有修改任务，请等待任务结束（{directory}）。"),
    )


@contextmanager
def _operation_lock(lock_path, conflict):
    # Keep the lock file in place so all processes lock the same file.
    with lock_path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            raise conflict from error
        yield


def export_collection(manifest_path, workers=2, width=1600, height=1200,
                      python=None, preview=False, progress=None, control=None, retry_failed=False):
    """Render independent chunks; retry only missing, failed or outdated images."""
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    workers, width, height = int(workers), int(width), int(height)
    if workers < 1 or width < 64 or height < 64:
        raise ValueError("并发数至少为 1，图像宽高至少为 64。")
    directory = manifest_path.parent / ("previews" if preview else "images")
    directory.mkdir(exist_ok=True)
    with export_directory_lock(directory):
        retained = (current_images(manifest, directory, width, height, verify_contents=True, control=control)
                    if retry_failed else {})
        pending = [index for index in range(len(manifest["samples"])) if index not in retained]
        workers = min(workers, len(pending), os.cpu_count() or 1)
        if pending:
            results, processes, threads = run_render_chunks(manifest, pending, directory, workers,
                                                           (width, height), python, retained, control, progress)
        else:
            results, processes, threads = list(retained.values()), [], 0
            publish_worker_diagnostics(results, directory)
        result = {"workers": workers, "threads_per_worker": threads, "processes": processes,
                  "width": width, "height": height, "reused": len(retained),
                  "cancelled": bool(control and control.cancelled.is_set()),
                  "results": sorted(results, key=lambda row: row["index"])}
        write_json(directory / "03_export.json", result)
        write_gallery(manifest, directory, result)
        return result


def gallery_page(manifest_path, control):
    """Refresh a viewable gallery from successful images under its publication lock."""
    control.report("核对展示页", 0, 0)
    busy = None
    for folder in ("images", "previews"):
        control.check()
        directory = manifest_path.parent / folder
        page = directory / "04_gallery.html"
        if not page.exists():
            continue
        try:
            with export_directory_lock(directory):
                manifest = read_json(manifest_path)
                current = current_images(manifest, directory, verify_contents=True,
                                         allow_legacy=True, control=control)
                if not current:
                    continue
                report = {"results": [current.get(index, {"index": index, "ok": False})
                                      for index in range(len(manifest["samples"]))]}
                control.check()
                write_gallery(manifest, directory, report)
        except ExportInProgress as error:
            busy = error
            continue
        return page
    if busy is not None:
        raise busy
    raise ValueError("没有当前设置的展示页，请先刷新预览或导出。")


def write_gallery(manifest, directory, export):
    cards = []
    failures = {row.get("index") for row in export["results"] if not row["ok"]}
    for index, row in enumerate(manifest["samples"]):
        caption = " · ".join(f"{html.escape(name)}: {value:.4g}" for name, value in row["scores"].items())
        image = (f'<img loading="lazy" src="{html.escape(row["image"], quote=True)}">'
                 if index not in failures and (Path(directory) / row["image"]).exists()
                 else "<p>渲染失败，请查看 03_export.json</p>")
        cards.append(f'<figure>{image}<figcaption><b>{html.escape(row["id"])}</b>'
                     f'<p>{caption}</p><small>分层: {html.escape(str(row["stratum"]))}</small>'
                     + ('<p>结构差异未验证（用户允许纳入）</p>'
                        if row.get("diversity_status") == "unverified"
                        or row.get("incomparable_with_previous", 0) else '')
                     + ('<p>已固定</p>' if row.get("pinned") else '')
                     + '</figcaption></figure>')
    content = ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>Structure Collections</title>'
               '<style>body{font:15px system-ui;background:#f4f6fa;color:#263144;margin:24px}'
               'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}'
               'figure{margin:0;background:white;border-radius:10px;overflow:hidden}'
               'img{width:100%;display:block}figcaption{padding:16px}p{margin:8px 0}</style>'
               f'<h1>Structure Collections · {len(cards)} 个样本</h1>'
               '<p>target 蓝色 · binder 橙色 · 同一相机与比例</p><main>' + "".join(cards) + '</main></html>')
    (Path(directory) / "04_gallery.html").write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    analysis = subparsers.add_parser("analyze")
    analysis.add_argument("--config", required=True)
    analysis.add_argument("--output", required=True)
    analysis.add_argument("--progress")
    analysis.add_argument("--require-new", action="store_true", help=argparse.SUPPRESS)
    revise = subparsers.add_parser("revise")
    revise.add_argument("--manifest", required=True)
    revise.add_argument("--request", required=True)
    revise.add_argument("--progress")
    render = subparsers.add_parser("render")
    render.add_argument("--manifest", required=True)
    render.add_argument("--indices", required=True)
    render.add_argument("--directory", required=True)
    render.add_argument("--width", type=int, required=True)
    render.add_argument("--height", type=int, required=True)
    render.add_argument("--threads", type=int, required=True)
    render.add_argument("--report", required=True)
    render.add_argument("--run-id", default="")
    render.add_argument("--scratch-dir")
    export = subparsers.add_parser("export")
    export.add_argument("--manifest", required=True)
    export.add_argument("--workers", type=int, default=2)
    export.add_argument("--width", type=int, default=1600)
    export.add_argument("--height", type=int, default=1200)
    export.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    def progress(completed, total):
        if getattr(args, "progress", None):
            phase = "采样" if args.action == "analyze" else "调整朝向"
            _publish_task_progress(args.progress, phase, completed, total)
    if args.action == "analyze":
        create_collection(read_json(args.config), args.output, progress, require_new=args.require_new)
    elif args.action == "revise":
        revise_collection(args.manifest, read_json(args.request), progress)
    elif args.action == "render":
        render_worker(args.manifest, [int(x) for x in args.indices.split(",")],
                      args.directory, args.width, args.height, args.threads, args.report,
                      args.run_id, args.scratch_dir)
    else:
        try:
            result = export_collection(args.manifest, args.workers, args.width, args.height,
                                       retry_failed=args.retry_failed)
        except ExportInProgress as error:
            raise SystemExit(str(error)) from None
        if not all(row["ok"] for row in result["results"]):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
