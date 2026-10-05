"""Unified operation references and exact representation snapshots.

The journal never stores a second transform, navigation, style, or interaction
state machine.  It only points at records owned by those canonical modules.
``pp_show`` is the one exception which needs its own atom-representation
snapshot because PyMOL has no public show/hide undo record.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


JOURNAL_SCHEMA = "pymolplugins.operation-journal/1.0"
_JOURNAL_LIMIT = 100
_JOURNAL: List[Dict[str, object]] = globals().get("_JOURNAL", [])
_SHOW_RECORDS: Dict[str, Dict[str, object]] = globals().get("_SHOW_RECORDS", {})
_OBJECT_IDENTITY_PROPERTY = "_pymolplugins_task_object_identity"
_OBJECT_IDENTITY_PREFIX = "pymolplugins.task-object/1:"


class JournalMutationError(RuntimeError):
    """A task mutation failed, with an explicit rollback outcome."""

    def __init__(self, message: str, scene_preserved: bool):
        super().__init__(message)
        self.scene_preserved = bool(scene_preserved)


def _public_entry(entry: Mapping[str, object]) -> Dict[str, object]:
    inactive_reason = str(entry.get("inactive_reason", ""))
    return {
        "operation_id": str(entry["operation_id"]),
        "kind": str(entry["kind"]),
        "reference": str(entry["reference"]),
        "summary": str(entry["summary"]),
        "created_at": float(entry["created_at"]),
        "undone": bool(entry.get("undone", False)),
        "status": inactive_reason or "undoable",
        "superseded_by": str(entry.get("superseded_by", "")),
    }


def record_operation(kind: str, reference: object, summary: str) -> Dict[str, object]:
    """Append one reference to a canonical undo record and return a copy."""

    entry = {
        "operation_id": "op_" + uuid.uuid4().hex[:12],
        "kind": str(kind),
        "reference": str(reference),
        "summary": str(summary),
        "created_at": time.time(),
        "undone": False,
        "inactive_reason": "",
        "superseded_by": "",
    }
    _JOURNAL.append(entry)
    while len(_JOURNAL) > _JOURNAL_LIMIT:
        removed = _JOURNAL.pop(0)
        if removed.get("kind") == "show":
            _SHOW_RECORDS.pop(str(removed.get("reference", "")), None)
    return _public_entry(entry)


def list_operations(include_undone: bool = False) -> List[Dict[str, object]]:
    """Return newest-first detached journal entries."""

    values = reversed(_JOURNAL)
    if not include_undone:
        values = (entry for entry in values if not entry.get("undone", False))
    return [_public_entry(entry) for entry in values]


def find_operation_by_reference(
    reference: object, *, include_inactive: bool = False
) -> Optional[Dict[str, object]]:
    """Return the newest matching entry without ambiguous user-ID semantics."""

    key = str(reference).strip()
    for entry in reversed(_JOURNAL):
        if str(entry.get("reference", "")) != key:
            continue
        if entry.get("undone", False) and not include_inactive:
            continue
        return _public_entry(entry)
    return None


def supersede_operation(reference: object, replacement: object) -> bool:
    """Deactivate one replaced owner record so it cannot mutate newer output."""

    key = str(reference).strip()
    replacement_value = str(replacement).strip()
    for entry in reversed(_JOURNAL):
        if entry.get("undone", False) or str(entry.get("reference", "")) != key:
            continue
        entry["undone"] = True
        entry["inactive_reason"] = "superseded"
        entry["superseded_by"] = replacement_value
        return True
    return False


def resolve_operation(identifier: object = "") -> Dict[str, object]:
    """Resolve the latest undoable entry or one operation/reference ID."""

    key = str(identifier).strip()
    candidates = [entry for entry in _JOURNAL if not entry.get("undone", False)]
    if not key:
        if not candidates:
            raise ValueError("no Tier-1 operation is available to undo")
        return candidates[-1]
    exact = [entry for entry in candidates if entry["operation_id"] == key]
    if not exact:
        exact = [entry for entry in candidates if str(entry["reference"]) == key]
    if not exact:
        raise ValueError("unknown or already-undone operation: %s" % key)
    if len(exact) > 1:
        raise ValueError(
            "operation reference is ambiguous; use one of: %s"
            % ", ".join(str(entry["operation_id"]) for entry in exact)
        )
    return exact[0]


def mark_undone(entry: Mapping[str, object]) -> None:
    """Mark an entry only after its owner confirmed restoration."""

    operation_id = str(entry["operation_id"])
    for stored in _JOURNAL:
        if stored.get("operation_id") == operation_id:
            stored["undone"] = True
            stored["inactive_reason"] = "undone"
            if stored.get("kind") == "show":
                _SHOW_RECORDS.pop(str(stored.get("reference", "")), None)
            return
    raise ValueError("operation disappeared from the journal: %s" % operation_id)


def _atom_identity(atom: object) -> Tuple[object, ...]:
    coord = tuple(round(float(value), 6) for value in getattr(atom, "coord", ()))
    return (
        str(getattr(atom, "model", "") or ""),
        int(getattr(atom, "index")),
        str(getattr(atom, "segi", "") or ""),
        str(getattr(atom, "chain", "") or ""),
        str(getattr(atom, "resi", "") or ""),
        str(getattr(atom, "inscode", "") or ""),
        str(getattr(atom, "resn", "") or ""),
        str(getattr(atom, "name", "") or ""),
        str(getattr(atom, "alt", "") or ""),
        coord,
    )


def _source_hash(atoms: Sequence[Tuple[object, ...]]) -> str:
    payload = json.dumps(list(atoms), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def capture_representation(cmd_api, target: str, mask: int) -> Dict[str, object]:
    """Capture one atomic representation bit for all atoms in one object."""

    try:
        state_count = max(int(cmd_api.count_states(str(target))), 1)
    except (AttributeError, TypeError, ValueError):
        state_count = 1
    state_atoms = []
    for state in range(1, state_count + 1):
        model = cmd_api.get_model(str(target), state)
        state_atoms.append(
            (state, list(getattr(model, "atom", ()) or ()))
        )
    if not any(atoms for _, atoms in state_atoms):
        raise ValueError("target has no atoms in any state: %s" % target)
    identities = sorted(
        (
            (state,) + _atom_identity(atom)
            for state, atoms in state_atoms
            for atom in atoms
        ),
        key=lambda row: (row[0], row[2]),
    )
    # PyMOL exposes representation bits to iterate, not get_model's Atom.
    # iterate covers the object's complete atom table, including discrete states.
    rows = []
    cmd_api.iterate(str(target), "rows.append((index, reps))", space={"rows": rows})
    atoms_by_index = {int(index): int(reps) for index, reps in rows}
    indices = sorted(atoms_by_index)
    owners = {str(row[1]) for row in identities if str(row[1])}
    if owners and owners != {str(target)}:
        raise ValueError("target atom ownership is ambiguous: %s" % target)
    object_identity = ""
    if hasattr(cmd_api, "get_property"):
        try:
            object_identity = str(
                cmd_api.get_property(
                    _OBJECT_IDENTITY_PROPERTY, str(target), state=0, quiet=1
                )
                or ""
            )
        except Exception:
            object_identity = ""
    return {
        "target": str(target),
        "indices": tuple(indices),
        "on_indices": tuple(
            index for index in indices if atoms_by_index[index] & int(mask)
        ),
        "source_hash": _source_hash(identities),
        "state_count": state_count,
        "object_identity": object_identity,
    }


def capture_bundle(cmd_api, targets: Iterable[str], mask: int) -> Dict[str, Dict[str, object]]:
    return {
        str(target): capture_representation(cmd_api, str(target), mask)
        for target in targets
    }


def _assert_same_source(
    current: Mapping[str, Mapping[str, object]],
    expected: Mapping[str, Mapping[str, object]],
) -> None:
    if set(current) != set(expected):
        raise RuntimeError("representation targets changed since the operation")
    replaced = [
        name
        for name in expected
        if expected[name].get("object_identity")
        and current[name].get("object_identity")
        != expected[name].get("object_identity")
    ]
    if replaced:
        raise RuntimeError(
            "representation target object instance changed: %s; undo refuses to "
            "modify a same-name replacement" % ", ".join(replaced)
        )
    stale = [
        name
        for name in expected
        if current[name]["source_hash"] != expected[name]["source_hash"]
        or current[name]["indices"] != expected[name]["indices"]
        or current[name].get("state_count") != expected[name].get("state_count")
    ]
    if stale:
        raise RuntimeError(
            "representation source atoms changed: %s; undo cannot recreate them"
            % ", ".join(stale)
        )


def _restore_one(cmd_api, representation: str, snapshot: Mapping[str, object]) -> None:
    target = str(snapshot["target"])
    selection = "__pp_task_restore_" + uuid.uuid4().hex[:12]
    cmd_api.hide(representation, target)
    indices = list(snapshot["on_indices"])
    if not indices:
        return
    cleanup_error = None
    try:
        selected = cmd_api.select_list(
            selection, target, indices, state=0, mode="index", quiet=1
        )
        if int(selected) != len(indices):
            raise RuntimeError(
                "selected %d/%d atoms while restoring %s"
                % (int(selected), len(indices), target)
            )
        cmd_api.show(representation, selection)
    finally:
        try:
            cmd_api.delete(selection)
        except Exception as exc:
            cleanup_error = exc
    if cleanup_error is not None:
        raise RuntimeError(
            "restored %s but could not delete temporary selection %s: %s"
            % (target, selection, cleanup_error)
        )


def _restore_bundle(
    cmd_api,
    representation: str,
    snapshots: Mapping[str, Mapping[str, object]],
) -> None:
    for target in snapshots:
        _restore_one(cmd_api, representation, snapshots[target])


def _restore_identity_properties(cmd_api, previous: Mapping[str, object]) -> None:
    if not previous or not hasattr(cmd_api, "set_property"):
        return
    errors = []
    for target, value in previous.items():
        try:
            cmd_api.set_property(
                _OBJECT_IDENTITY_PROPERTY,
                "" if value is None else value,
                object=target,
                state=0,
                proptype=6,
                quiet=1,
            )
        except Exception as exc:
            errors.append("%s: %s" % (target, exc))
    if errors:
        raise RuntimeError(
            "could not restore object identity metadata: %s" % "; ".join(errors)
        )


def _ensure_object_identities(cmd_api, targets: Sequence[str]):
    """Attach durable same-session identity tags when Host properties exist."""

    if not (
        hasattr(cmd_api, "get_property") and hasattr(cmd_api, "set_property")
    ):
        return {str(target): "" for target in targets}, {}
    identities = {}
    previous = {}
    try:
        for target in targets:
            name = str(target)
            raw = cmd_api.get_property(
                _OBJECT_IDENTITY_PROPERTY, name, state=0, quiet=1
            )
            current = str(raw or "")
            if current:
                identities[name] = current
                continue
            marker = _OBJECT_IDENTITY_PREFIX + uuid.uuid4().hex
            previous[name] = raw
            cmd_api.set_property(
                _OBJECT_IDENTITY_PROPERTY,
                marker,
                object=name,
                state=0,
                proptype=6,
                quiet=1,
            )
            restored = cmd_api.get_property(
                _OBJECT_IDENTITY_PROPERTY, name, state=0, quiet=1
            )
            if str(restored or "") != marker:
                raise RuntimeError("Host did not retain object identity metadata")
            identities[name] = marker
        return identities, previous
    except Exception:
        _restore_identity_properties(cmd_api, previous)
        raise


def apply_representation(
    cmd_api,
    representation: str,
    targets: Sequence[str],
    mode: str,
    mask: int,
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, Dict[str, object]]]:
    """Apply show/hide transactionally and return baseline/committed snapshots."""

    baseline = capture_bundle(cmd_api, targets, mask)
    attempted: List[str] = []
    identity_previous = {}
    try:
        for target in targets:
            attempted.append(target)
            getattr(cmd_api, mode)(representation, target)
        identities, identity_previous = _ensure_object_identities(cmd_api, targets)
        for target, marker in identities.items():
            baseline[target]["object_identity"] = marker
        committed = capture_bundle(cmd_api, targets, mask)
        _assert_same_source(committed, baseline)
        for snapshot in committed.values():
            expected = set(snapshot["indices"]) if mode == "show" else set()
            if set(snapshot["on_indices"]) != expected:
                raise RuntimeError("PyMOL did not apply %s to every target atom" % mode)
        return baseline, committed
    except Exception as exc:
        rollback_error = ""
        try:
            _restore_bundle(
                cmd_api,
                representation,
                {name: baseline[name] for name in attempted},
            )
        except Exception as rollback_exc:
            rollback_error = "; rollback failed: %s" % rollback_exc
        try:
            _restore_identity_properties(cmd_api, identity_previous)
        except Exception as rollback_exc:
            rollback_error += "; identity rollback failed: %s" % rollback_exc
        raise JournalMutationError(
            "representation apply failed: %s%s" % (exc, rollback_error),
            scene_preserved=not rollback_error,
        ) from exc


def store_show_record(
    representation: str,
    mask: int,
    baseline: Mapping[str, Mapping[str, object]],
    committed: Mapping[str, Mapping[str, object]],
) -> str:
    reference = "show_" + uuid.uuid4().hex[:12]
    _SHOW_RECORDS[reference] = {
        "representation": str(representation),
        "mask": int(mask),
        "baseline": copy.deepcopy(dict(baseline)),
        "committed": copy.deepcopy(dict(committed)),
    }
    return reference


def undo_show(cmd_api, reference: str, force: bool = False) -> Dict[str, object]:
    """Restore an exact show/hide bit snapshot with stale and rollback gates."""

    try:
        record = _SHOW_RECORDS[str(reference)]
    except KeyError:
        raise ValueError("unknown representation operation: %s" % reference)
    representation = str(record["representation"])
    mask = int(record["mask"])
    baseline = record["baseline"]
    committed = record["committed"]
    current = capture_bundle(cmd_api, baseline, mask)
    _assert_same_source(current, baseline)
    stale = [
        target
        for target in committed
        if current[target]["on_indices"] != committed[target]["on_indices"]
    ]
    if stale and not force:
        raise RuntimeError(
            "representation changed after the operation: %s; use force=1 only "
            "if those newer visibility edits may be overwritten" % ", ".join(stale)
        )
    attempted: List[str] = []
    try:
        for target in baseline:
            attempted.append(target)
            _restore_one(cmd_api, representation, baseline[target])
        restored = capture_bundle(cmd_api, baseline, mask)
        _assert_same_source(restored, baseline)
        if any(
            restored[name]["on_indices"] != baseline[name]["on_indices"]
            for name in baseline
        ):
            raise RuntimeError("PyMOL did not restore the representation snapshot")
    except Exception as exc:
        rollback_error = ""
        try:
            _restore_bundle(
                cmd_api,
                representation,
                {name: current[name] for name in attempted},
            )
        except Exception as rollback_exc:
            rollback_error = "; rollback failed: %s" % rollback_exc
        raise JournalMutationError(
            "representation undo failed: %s%s" % (exc, rollback_error),
            scene_preserved=not rollback_error,
        ) from exc
    return {
        "status": "restored",
        "reference": str(reference),
        "representation": representation,
        "targets": list(baseline),
        "forced": bool(force),
        "stale_overwritten": stale if force else [],
    }


def reset_for_tests() -> None:
    _JOURNAL.clear()
    _SHOW_RECORDS.clear()
