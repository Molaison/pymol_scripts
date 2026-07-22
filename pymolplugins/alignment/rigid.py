"""Selection-, motif-, and ligand-driven rigid alignment for PyMOL.

Anchor atoms determine a matrix.  ``move`` independently determines which
whole PyMOL objects receive that matrix.  Preview is the default; mutation is
explicit through ``commit=1`` or ``pp_align_commit``.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import sys
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
        RigidFit,
        apply_matrix,
        fit_rigid_transform,
        homogeneous_to_ttt,
        identity4,
        mat4_multiply,
        ttt_to_homogeneous,
    )
    from pymolplugins.core.parameters import as_bool, as_int, sanitize_name, split_names
    from pymolplugins.core.pymol_groups import expand_names as expand_group_names
    from pymolplugins.core.sequence import find_sequence_matches, index_atom_streams
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.math3d import (  # type: ignore
        GeometryError,
        RigidFit,
        apply_matrix,
        fit_rigid_transform,
        homogeneous_to_ttt,
        identity4,
        mat4_multiply,
        ttt_to_homogeneous,
    )
    from pymolplugins.core.parameters import as_bool, as_int, sanitize_name, split_names  # type: ignore
    from pymolplugins.core.pymol_groups import expand_names as expand_group_names  # type: ignore
    from pymolplugins.core.sequence import find_sequence_matches, index_atom_streams  # type: ignore


@dataclass(frozen=True)
class AtomPoint:
    object: str
    index: int
    name: str
    element: str
    resn: str
    resi: str
    chain: str
    segi: str
    alt: str
    coord: Tuple[float, float, float]


@dataclass
class AlignmentRecord:
    id: str
    kind: str
    created_at: float
    mobile_anchor: str
    target_anchor: str
    match: str
    move_objects: Tuple[str, ...]
    matrix: Tuple[float, ...]
    rmsd_before: float
    rmsd_after: float
    point_count: int
    baseline_ttt: Dict[str, Optional[Tuple[float, ...]]]
    state: int
    anchor_fingerprint: str
    anchor_refs: Tuple[Tuple[str, str, int], ...]
    parameters: Dict[str, object]
    backend_version: str = "1.0"
    committed: bool = False
    committed_ttt: Dict[str, Optional[Tuple[float, ...]]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": "pymolplugins.alignment/1.0",
            "id": self.id,
            "kind": self.kind,
            "created_at": self.created_at,
            "mobile_anchor": self.mobile_anchor,
            "target_anchor": self.target_anchor,
            "match": self.match,
            "move_objects": list(self.move_objects),
            "matrix": list(self.matrix),
            "rmsd_before": self.rmsd_before,
            "rmsd_after": self.rmsd_after,
            "point_count": self.point_count,
            "state": self.state,
            "input_hash": self.anchor_fingerprint,
            "parameters": dict(self.parameters),
            "backend": {
                "name": "pymolplugins.math3d",
                "version": self.backend_version,
            },
            "committed": self.committed,
            "warnings": list(self.warnings),
        }


_RECORDS: Dict[str, AlignmentRecord] = {}
_LAST_RECORD_ID = ""


class AtomMappingAmbiguity(ValueError):
    """Raised when atom identifiers do not define one rigid correspondence."""


def _error_result(exc: Exception) -> Dict[str, object]:
    result: Dict[str, object] = {"status": "error", "error": str(exc)}
    if isinstance(exc, AtomMappingAmbiguity):
        result.update(
            {
                "error_code": "ambiguous_atom_mapping",
                "guidance": (
                    "narrow both ligand selections to unique atom names, or use "
                    "match=order only after verifying identical atom order"
                ),
            }
        )
    return result


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is not available in this Python process")
    return cmd


def _get_ttt(name: str) -> Optional[Tuple[float, ...]]:
    value = _require_cmd().get_object_ttt(name, quiet=1)
    if value is None:
        return None
    return tuple(float(item) for item in value)


def _set_ttt_h(name: str, matrix: Sequence[float]) -> None:
    _require_cmd().set_object_ttt(name, list(homogeneous_to_ttt(matrix)), quiet=1)


def _restore_ttt(name: str, baseline: Optional[Sequence[float]]) -> None:
    if baseline is None:
        _set_ttt_h(name, identity4())
    else:
        _require_cmd().set_object_ttt(name, list(baseline), quiet=1)


def _ttt_close(
    left: Optional[Sequence[float]],
    right: Optional[Sequence[float]],
    tolerance: float = 1.0e-6,
) -> bool:
    left_matrix = ttt_to_homogeneous(left)
    right_matrix = ttt_to_homogeneous(right)
    return all(
        abs(float(a) - float(b)) <= tolerance
        for a, b in zip(left_matrix, right_matrix)
    )


def _rollback_ttt(
    baselines: Mapping[str, Optional[Sequence[float]]], names: Iterable[str]
) -> List[str]:
    failures = []
    for name in reversed(tuple(names)):
        try:
            _restore_ttt(name, baselines[name])
        except Exception:
            failures.append(name)
    return failures


def _object_world_matrix(name: str, state: int) -> Tuple[float, ...]:
    try:
        value = _require_cmd().get_object_matrix(name, state, incl_ttt=1)
        if value is not None and len(value) == 16:
            matrix = tuple(float(item) for item in value)
            # get_object_matrix is already homogeneous, unlike raw TTT.
            if all(abs(matrix[index]) < 1.0e-7 for index in (12, 13, 14)):
                return matrix
    except Exception:
        pass
    return ttt_to_homogeneous(_get_ttt(name))


def _atom_value(atom, name, default=""):
    if isinstance(atom, Mapping):
        return atom.get(name, default)
    return getattr(atom, name, default)


def _atom_points(selection: str, state: int) -> List[AtomPoint]:
    model = _require_cmd().get_model(selection, state)
    result = []
    matrices: Dict[str, Tuple[float, ...]] = {}
    for atom in getattr(model, "atom", ()) or ():
        object_name = str(_atom_value(atom, "model", "")).strip()
        if not object_name:
            owners = list(_require_cmd().get_object_list(selection) or [])
            if len(owners) != 1:
                raise ValueError("anchor selection must resolve each atom to one object")
            object_name = owners[0]
        matrices.setdefault(object_name, _object_world_matrix(object_name, state))
        raw_coord = tuple(float(value) for value in _atom_value(atom, "coord", ()))
        if len(raw_coord) != 3:
            raise ValueError(f"atom {object_name}`{_atom_value(atom, 'index', '?')} has no 3D coordinate")
        result.append(
            AtomPoint(
                object=object_name,
                index=int(_atom_value(atom, "index", 0)),
                name=str(_atom_value(atom, "name", "")).strip().upper(),
                element=str(_atom_value(atom, "symbol", _atom_value(atom, "elem", ""))).strip().upper(),
                resn=str(_atom_value(atom, "resn", "")).strip().upper(),
                resi=str(_atom_value(atom, "resi", "")).strip(),
                chain=str(_atom_value(atom, "chain", "")).strip(),
                segi=str(_atom_value(atom, "segi", "")).strip(),
                alt=str(_atom_value(atom, "alt", "")).strip(),
                coord=apply_matrix(matrices[object_name], raw_coord),
            )
        )
    return result


def _unique_map(points: Sequence[AtomPoint], key_fn, label: str) -> Dict[Tuple[object, ...], AtomPoint]:
    result: Dict[Tuple[object, ...], AtomPoint] = {}
    duplicates = []
    for point in points:
        key = tuple(key_fn(point))
        if key in result:
            duplicates.append(key)
        result[key] = point
    if duplicates:
        rendered = ", ".join(str(key) for key in sorted(set(duplicates)))
        raise AtomMappingAmbiguity(
            f"{label} atom matching is ambiguous for keys: {rendered}"
        )
    return result


def _pair_atoms(
    mobile: Sequence[AtomPoint], target: Sequence[AtomPoint], match: str
) -> Tuple[List[AtomPoint], List[AtomPoint]]:
    strategy = str(match).strip().lower()
    if strategy == "order":
        if len(mobile) != len(target):
            raise ValueError(
                f"order matching requires equal atom counts ({len(mobile)} != {len(target)})"
            )
        return list(mobile), list(target)
    if strategy == "name":
        key_fn = lambda atom: (atom.name, atom.element)
    elif strategy == "identifier":
        key_fn = lambda atom: (atom.segi, atom.chain, atom.resi, atom.resn, atom.name, atom.element)
    else:
        raise ValueError("match must be order, name, or identifier")
    mobile_map = _unique_map(mobile, key_fn, "mobile")
    target_map = _unique_map(target, key_fn, "target")
    shared = sorted(set(mobile_map).intersection(target_map))
    missing_mobile = sorted(set(target_map).difference(mobile_map))
    missing_target = sorted(set(mobile_map).difference(target_map))
    if missing_mobile or missing_target:
        raise ValueError(
            "anchor atom keys differ; missing_mobile={}, missing_target={}".format(
                missing_mobile, missing_target
            )
        )
    return [mobile_map[key] for key in shared], [target_map[key] for key in shared]


def _anchor_payload(
    mobile: Sequence[AtomPoint], target: Sequence[AtomPoint], state: int
) -> List[Dict[str, object]]:
    payload = []
    for role, points in (("mobile", mobile), ("target", target)):
        for point in points:
            payload.append(
                {
                    "role": role,
                    "object": point.object,
                    "index": point.index,
                    "name": point.name,
                    "element": point.element,
                    "resn": point.resn,
                    "resi": point.resi,
                    "chain": point.chain,
                    "segi": point.segi,
                    "alt": point.alt,
                    "coord": [float(value) for value in point.coord],
                    "state": state,
                }
            )
    return payload


def _anchor_fingerprint(
    mobile: Sequence[AtomPoint], target: Sequence[AtomPoint], state: int
) -> str:
    serialized = json.dumps(
        _anchor_payload(mobile, target, state),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _anchor_refs(
    mobile: Sequence[AtomPoint], target: Sequence[AtomPoint]
) -> Tuple[Tuple[str, str, int], ...]:
    return tuple(
        (role, point.object, point.index)
        for role, points in (("mobile", mobile), ("target", target))
        for point in points
    )


def _current_anchor_fingerprint(record: AlignmentRecord) -> str:
    objects = sorted({object_name for _, object_name, _ in record.anchor_refs})
    current: Dict[Tuple[str, int], AtomPoint] = {}
    for object_name in objects:
        for point in _atom_points(f"({object_name})", record.state):
            key = (object_name, point.index)
            if key in current:
                raise RuntimeError(
                    f"alignment anchor identity became ambiguous: {object_name}`{point.index}"
                )
            current[key] = point
    mobile = []
    target = []
    for role, object_name, index in record.anchor_refs:
        try:
            point = current[(object_name, index)]
        except KeyError as exc:
            raise RuntimeError(
                f"alignment anchor atom no longer exists: {object_name}`{index}"
            ) from exc
        (mobile if role == "mobile" else target).append(point)
    return _anchor_fingerprint(mobile, target, record.state)


def _selection_objects(selection: str) -> List[str]:
    return list(dict.fromkeys(_require_cmd().get_object_list(f"({selection})") or []))


def _is_leaf(name: str) -> bool:
    object_type = str(_require_cmd().get_type(name) or "")
    return object_type.startswith("object:") and object_type != "object:group"


def _expand_move_name(name: str, seen: Optional[set] = None) -> List[str]:
    return expand_group_names(_require_cmd(), [name])


def _resolve_move_objects(move: object, default_object: str) -> Tuple[str, ...]:
    requested = split_names(move) or [default_object]
    result = []
    seen = set()
    for name in requested:
        for leaf in _expand_move_name(name):
            if leaf not in seen:
                seen.add(leaf)
                result.append(leaf)
    if not result:
        raise ValueError("move resolved to no leaf objects")
    return tuple(result)


def _new_record_id(kind: str, mobile_anchor: str, target_anchor: str) -> str:
    seed = f"{kind}\0{mobile_anchor}\0{target_anchor}\0{time.time_ns()}"
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"align_{sanitize_name(kind, fallback='rigid')}_{digest}"


def _create_record(
    kind: str,
    mobile_anchor: str,
    target_anchor: str,
    match: str,
    move_objects: Tuple[str, ...],
    fit: RigidFit,
    paired_mobile: Sequence[AtomPoint],
    paired_target: Sequence[AtomPoint],
    state: int,
    parameters: Mapping[str, object],
) -> AlignmentRecord:
    global _LAST_RECORD_ID
    baseline = {name: _get_ttt(name) for name in move_objects}
    record = AlignmentRecord(
        id=_new_record_id(kind, mobile_anchor, target_anchor),
        kind=kind,
        created_at=time.time(),
        mobile_anchor=mobile_anchor,
        target_anchor=target_anchor,
        match=match,
        move_objects=move_objects,
        matrix=fit.matrix,
        rmsd_before=fit.rmsd_before,
        rmsd_after=fit.rmsd_after,
        point_count=fit.point_count,
        baseline_ttt=baseline,
        state=state,
        anchor_fingerprint=_anchor_fingerprint(
            paired_mobile, paired_target, state
        ),
        anchor_refs=_anchor_refs(paired_mobile, paired_target),
        parameters=dict(parameters),
    )
    _RECORDS[record.id] = record
    _LAST_RECORD_ID = record.id
    return record


def _commit_record(record: AlignmentRecord) -> None:
    if record.committed:
        raise RuntimeError(f"alignment record is already committed: {record.id}")
    current = {name: _get_ttt(name) for name in record.move_objects}
    stale = [
        name
        for name in record.move_objects
        if not _ttt_close(current[name], record.baseline_ttt[name])
    ]
    if stale:
        raise RuntimeError(
            "alignment preview is stale because move objects changed: "
            + ", ".join(stale)
            + "; create a new alignment record"
        )
    if _current_anchor_fingerprint(record) != record.anchor_fingerprint:
        raise RuntimeError(
            "alignment preview is stale because anchor atoms or transforms changed; "
            "create a new alignment record"
        )
    attempted = []
    try:
        for name in record.move_objects:
            attempted.append(name)
            matrix = ttt_to_homogeneous(current[name])
            _set_ttt_h(name, mat4_multiply(record.matrix, matrix))
    except Exception as exc:
        rollback_failures = _rollback_ttt(current, attempted)
        detail = (
            f"; rollback failed for: {', '.join(rollback_failures)}"
            if rollback_failures
            else ""
        )
        raise RuntimeError(f"alignment commit failed: {exc}{detail}") from exc
    record.committed = True
    record.committed_ttt = {name: _get_ttt(name) for name in record.move_objects}


def _fit_and_record(
    *,
    kind: str,
    mobile_points: Sequence[AtomPoint],
    target_points: Sequence[AtomPoint],
    mobile_anchor: str,
    target_anchor: str,
    match: str,
    move: object,
    default_mobile_object: str,
    state: int,
    parameters: Mapping[str, object],
    commit: object,
) -> Dict[str, object]:
    if not mobile_points or not target_points:
        raise ValueError("both anchor selections must contain atoms")
    paired_mobile, paired_target = _pair_atoms(mobile_points, target_points, match)
    fit = fit_rigid_transform(
        [atom.coord for atom in paired_mobile], [atom.coord for atom in paired_target]
    )
    move_objects = _resolve_move_objects(move, default_mobile_object)
    record = _create_record(
        kind,
        mobile_anchor,
        target_anchor,
        str(match).lower(),
        move_objects,
        fit,
        paired_mobile,
        paired_target,
        state,
        parameters,
    )
    if as_bool(commit):
        _commit_record(record)
    print(
        f"Alignment {record.id}: points={record.point_count}, "
        f"RMSD {record.rmsd_before:.4f} -> {record.rmsd_after:.4f}, "
        f"move={','.join(record.move_objects)}, committed={record.committed}"
    )
    return record.to_dict()


def pp_align_anchor(
    mobile_anchor: str,
    target_anchor: str,
    move: object = "",
    match: str = "identifier",
    state: object = 1,
    preview: object = 1,
    commit: object = 0,
) -> Dict[str, object]:
    """Fit arbitrary anchor selections and optionally move whole objects."""
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        as_bool(preview)  # Kept for an explicit, stable command signature.
        mobile_objects = _selection_objects(mobile_anchor)
        if len(mobile_objects) != 1:
            raise ValueError("mobile_anchor must belong to exactly one molecule object")
        return _fit_and_record(
            kind="anchor",
            mobile_points=_atom_points(f"({mobile_anchor})", parsed_state),
            target_points=_atom_points(f"({target_anchor})", parsed_state),
            mobile_anchor=str(mobile_anchor),
            target_anchor=str(target_anchor),
            match=match,
            move=move,
            default_mobile_object=mobile_objects[0],
            state=parsed_state,
            parameters={
                "mobile_anchor": str(mobile_anchor),
                "target_anchor": str(target_anchor),
                "match": str(match).lower(),
                "state": parsed_state,
            },
            commit=commit,
        )
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def _motif_points(
    object_name: str,
    motif: str,
    chain: str,
    atom_names: Sequence[str],
    occurrence: str,
    state: int,
) -> Tuple[List[AtomPoint], str]:
    model = _require_cmd().get_model(f"({object_name})", state)
    atoms = tuple(getattr(model, "atom", ()) or ())
    index = index_atom_streams([(object_name, state, atoms)], protein_only=True)
    matches = find_sequence_matches(
        index,
        [str(motif)],
        gap_mode="exact",
        max_gap=0,
        occurrence=str(occurrence).lower(),
        chain=str(chain) if str(chain) else None,
    )
    if len(matches) != 1:
        raise ValueError(
            f"motif '{motif}' in {object_name} must resolve to one match; found {len(matches)}"
        )
    all_points = {point.index: point for point in _atom_points(f"({object_name})", state)}
    result = []
    for residue in matches[0].residues:
        for atom_name in atom_names:
            indices = tuple(residue.atom_names.get(atom_name, ()))
            if len(indices) != 1:
                raise ValueError(
                    f"residue {object_name}/{residue.chain}/{residue.resi} "
                    f"requires one {atom_name} atom; found {len(indices)}"
                )
            result.append(all_points[indices[0]])
    description = f"{object_name} motif {motif} chain {chain or '*'}"
    return result, description


def pp_align_motif(
    mobile_obj: str,
    target_obj: str,
    mobile_motif: str,
    target_motif: str = "",
    mobile_chain: str = "",
    target_chain: str = "",
    atoms: str = "N+CA+C+O",
    move: object = "",
    occurrence: str = "unique",
    state: object = 1,
    preview: object = 1,
    commit: object = 0,
) -> Dict[str, object]:
    """Align a whole payload from exact sequence-motif residue anchors."""
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        as_bool(preview)
        atom_names = [name.strip().upper() for name in str(atoms).replace(",", "+").split("+") if name.strip()]
        if not atom_names:
            raise ValueError("atoms must contain at least one atom name")
        target_query = str(target_motif).strip() or str(mobile_motif).strip()
        mobile_points, mobile_description = _motif_points(
            str(mobile_obj), str(mobile_motif), str(mobile_chain), atom_names, occurrence, parsed_state
        )
        target_points, target_description = _motif_points(
            str(target_obj), target_query, str(target_chain), atom_names, occurrence, parsed_state
        )
        return _fit_and_record(
            kind="motif",
            mobile_points=mobile_points,
            target_points=target_points,
            mobile_anchor=mobile_description,
            target_anchor=target_description,
            match="order",
            move=move,
            default_mobile_object=str(mobile_obj),
            state=parsed_state,
            parameters={
                "mobile_object": str(mobile_obj),
                "target_object": str(target_obj),
                "mobile_motif": str(mobile_motif),
                "target_motif": target_query,
                "mobile_chain": str(mobile_chain),
                "target_chain": str(target_chain),
                "atoms": list(atom_names),
                "occurrence": str(occurrence).lower(),
                "state": parsed_state,
            },
            commit=commit,
        )
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def pp_align_ligand(
    mobile_obj: str,
    target_obj: str,
    mobile_ligand: str = "organic",
    target_ligand: str = "organic",
    match: str = "name",
    move: object = "",
    state: object = 1,
    preview: object = 1,
    commit: object = 0,
) -> Dict[str, object]:
    """Align a payload by explicitly selected ligand atoms."""
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        as_bool(preview)
        mobile_selection = f"({mobile_obj}) and ({mobile_ligand})"
        target_selection = f"({target_obj}) and ({target_ligand})"
        return _fit_and_record(
            kind="ligand",
            mobile_points=_atom_points(mobile_selection, parsed_state),
            target_points=_atom_points(target_selection, parsed_state),
            mobile_anchor=mobile_selection,
            target_anchor=target_selection,
            match=match,
            move=move,
            default_mobile_object=str(mobile_obj),
            state=parsed_state,
            parameters={
                "mobile_object": str(mobile_obj),
                "target_object": str(target_obj),
                "mobile_ligand": str(mobile_ligand),
                "target_ligand": str(target_ligand),
                "match": str(match).lower(),
                "state": parsed_state,
            },
            commit=commit,
        )
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def _record(record_id: object = "") -> AlignmentRecord:
    key = str(record_id or _LAST_RECORD_ID).strip()
    if not key or key not in _RECORDS:
        raise ValueError(f"unknown alignment record: {key or '(none)'}")
    return _RECORDS[key]


def pp_align_commit(record_id: object = "") -> Dict[str, object]:
    try:
        record = _record(record_id)
        _commit_record(record)
        print(f"Alignment committed: {record.id}")
        return record.to_dict()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def pp_align_undo(record_id: object = "", force: object = 0) -> Dict[str, object]:
    try:
        record = _record(record_id)
        if not record.committed:
            raise RuntimeError(f"alignment record is not committed: {record.id}")
        force_value = as_bool(force)
        current = {name: _get_ttt(name) for name in record.move_objects}
        stale = [
            name
            for name in record.move_objects
            if name in record.committed_ttt
            and not _ttt_close(current[name], record.committed_ttt[name])
        ]
        if stale and not force_value:
            raise RuntimeError(
                "alignment objects changed after commit: %s; undo refused to "
                "overwrite external changes (use force=1)" % ", ".join(stale)
            )
        attempted = []
        try:
            for name in record.move_objects:
                attempted.append(name)
                _restore_ttt(name, record.baseline_ttt[name])
        except Exception as exc:
            rollback_failures = _rollback_ttt(current, attempted)
            detail = (
                f"; rollback failed for: {', '.join(rollback_failures)}"
                if rollback_failures
                else ""
            )
            raise RuntimeError(f"alignment undo failed: {exc}{detail}") from exc
        record.committed = False
        record.committed_ttt.clear()
        print(f"Alignment undone: {record.id}")
        result = record.to_dict()
        result.update(
            {
                "forced": force_value,
                "stale_overwritten": stale if force_value else [],
            }
        )
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def pp_align_export(record_id: object = "", filename: str = "") -> Optional[str]:
    try:
        record = _record(record_id)
        path = os.path.abspath(os.path.expanduser(str(filename).strip() or f"{record.id}.json"))
        temporary = f"{path}.tmp.{os.getpid()}.{time.time_ns()}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(record.to_dict(), handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(f"Alignment exported: {path}")
        return path
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return None


def pp_align_list() -> List[Dict[str, object]]:
    result = [record.to_dict() for record in _RECORDS.values()]
    for record in result:
        print(
            f"{record['id']}: kind={record['kind']}, points={record['point_count']}, "
            f"after={record['rmsd_after']:.4f}, committed={record['committed']}"
        )
    return result


if cmd is not None:
    cmd.extend("pp_align_anchor", pp_align_anchor)
    cmd.extend("pp_align_motif", pp_align_motif)
    cmd.extend("pp_align_ligand", pp_align_ligand)
    cmd.extend("pp_align_commit", pp_align_commit)
    cmd.extend("pp_align_undo", pp_align_undo)
    cmd.extend("pp_align_export", pp_align_export)
    cmd.extend("pp_align_list", pp_align_list)
