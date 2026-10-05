"""PyMOL adapter for exact, residue-safe sequence motif selection.

The residue index and matching implementation live in ``utilities.sequence``
and do not import PyMOL.  This module is intentionally limited to reading
models, creating an exact atom-index selection, and optional visualization.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pymol import cmd

try:
    from pymolplugins.core.parameters import as_bool, as_int, extend_command, sanitize_name
    from pymolplugins.core.sequence import (
        SequenceMatchLimitError,
        SequenceOccurrenceError,
        find_sequence_matches,
        index_atom_streams,
        split_queries,
    )
except ModuleNotFoundError as error:
    # ``run /path/to/script.py`` does not consistently add the repository root
    # to sys.path in Host PyMOL. Resolve it from this file, including UNC paths.
    if not error.name or not (
        error.name == "pymolplugins" or error.name.startswith("pymolplugins.")
    ):
        raise
    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    from pymolplugins.core.parameters import as_bool, as_int, extend_command, sanitize_name
    from pymolplugins.core.sequence import (
        SequenceMatchLimitError,
        SequenceOccurrenceError,
        find_sequence_matches,
        index_atom_streams,
        split_queries,
    )


SCHEMA_VERSION = "1.0"
_LEGACY_GAP_DEFAULT = 3


@dataclass(frozen=True)
class SelectionOwnership:
    """In-process proof that a named selection is safe to replace."""

    id: str
    name: str
    state: int
    fingerprint: str


class SelectionNameCollision(ValueError):
    """Raised when an output name belongs to the user or another scene type."""

    def __init__(self, name: str, kind: str, reason: str):
        self.name = str(name)
        self.kind = str(kind)
        self.reason = str(reason)
        super().__init__(
            "selection name {!r} is already used by {} ({})".format(
                self.name, self.kind, self.reason
            )
        )


class SelectionTransactionError(RuntimeError):
    """Raised when a staged selection could not be installed atomically."""

    def __init__(self, message: str, rollback_failures: Iterable[str] = ()):
        self.rollback_failures = tuple(str(name) for name in rollback_failures)
        detail = (
            "; rollback failed for: {}".format(", ".join(self.rollback_failures))
            if self.rollback_failures
            else ""
        )
        super().__init__(str(message) + detail)


class SequenceNoMatchError(ValueError):
    """Raised when a requested output selection would otherwise be emptied."""

    def __init__(self, queries: Iterable[str]):
        self.queries = tuple(str(query) for query in queries)
        super().__init__(
            "no sequence match was found for {}".format(
                ", ".join(repr(query) for query in self.queries)
            )
        )


_SELECTION_OWNERS: Dict[str, SelectionOwnership] = {}


def _empty_result() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "ok": False,
        "error_code": None,
        "scene_preserved": True,
        "scene_changed": False,
        "selection_name": None,
        "selection_created": False,
        "selection_ownership": None,
        "visualization_status": "not_requested",
        "objects": [],
        "queries": [],
        "unmatched_queries": [],
        "partial_reasons": [],
        "tracks": [],
        "parameters": {},
        "matches": [],
        "match_count": 0,
        "selected_atom_count": 0,
        "warnings": [],
        "errors": [],
    }


def _object_names(selection: object) -> List[str]:
    expression = str(selection).strip()
    if not expression:
        raise ValueError("objects selection cannot be empty")
    try:
        names = cmd.get_object_list("({})".format(expression))
    except (AttributeError, TypeError):
        names = cmd.get_names("objects", 0, expression)
    # Preserve PyMOL order, but never scan the same object twice.
    return list(OrderedDict((str(name), None) for name in names))


def _read_atom_streams(
    object_names: Iterable[str], state: int, warnings: List[str]
) -> List[Tuple[str, int, Iterable[object]]]:
    streams = []
    for object_name in object_names:
        try:
            model = cmd.get_model("(%{})".format(object_name), state)
        except Exception as error:
            warnings.append(
                "could not read object {!r} state {}: {}".format(
                    object_name, state, error
                )
            )
            continue
        atoms = tuple(getattr(model, "atom", ()) or ())
        if not atoms:
            warnings.append(
                "object {!r} state {} contains no atoms".format(object_name, state)
            )
            continue
        streams.append((object_name, state, atoms))
    return streams


def _indices_by_object(matches: Iterable[object]) -> Mapping[Tuple[str, int], List[int]]:
    grouped: "OrderedDict[Tuple[str, int], List[int]]" = OrderedDict()
    seen: Dict[Tuple[str, int], set] = {}
    for match in matches:
        key = (match.object, match.state)
        indices = grouped.setdefault(key, [])
        object_seen = seen.setdefault(key, set())
        for residue in match.residues:
            for atom_index in residue.atom_indices:
                if atom_index not in object_seen:
                    object_seen.add(atom_index)
                    indices.append(atom_index)
    return grouped


def _fallback_index_expression(
    grouped: Mapping[Tuple[str, int], List[int]]
) -> str:
    clauses = []
    for (object_name, _state), indices in grouped.items():
        if not indices:
            continue
        clauses.append(
            "(%{} and index {})".format(
                object_name, "+".join(str(index) for index in indices)
            )
        )
    return " or ".join(clauses) if clauses else "none"


def _name_set(mode: str) -> set:
    try:
        return {str(name) for name in (cmd.get_names(mode) or ())}
    except Exception:
        return set()


def _scene_kind(name: str) -> str:
    if name in _name_set("objects"):
        try:
            return str(cmd.get_type(name) or "object")
        except Exception:
            return "object"
    if name in _name_set("selections"):
        return "selection"
    try:
        kind = str(cmd.get_type(name) or "")
    except Exception:
        kind = ""
    return kind


def _selection_identity(name: str, state: int) -> Tuple[Tuple[str, int], ...]:
    expression = "(%{})".format(name)
    if hasattr(cmd, "index"):
        try:
            rows = cmd.index(expression, state=state)
        except TypeError:
            rows = cmd.index(expression)
        return tuple(
            sorted({(str(object_name), int(index)) for object_name, index in rows})
        )
    model = cmd.get_model(expression, state)
    owners = list(cmd.get_object_list(expression) or ())
    result = []
    for atom in getattr(model, "atom", ()) or ():
        owner = str(getattr(atom, "model", "") or "")
        if not owner and len(owners) == 1:
            owner = str(owners[0])
        if not owner:
            raise RuntimeError("selection atom identity has no source object")
        result.append((owner, int(getattr(atom, "index"))))
    return tuple(sorted(set(result)))


def _identity_fingerprint(identity: Iterable[Tuple[str, int]]) -> str:
    payload = json.dumps(
        [[str(object_name), int(index)] for object_name, index in identity],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selection_fingerprint(name: str, state: int) -> str:
    return _identity_fingerprint(_selection_identity(name, state))


def _expected_identity(
    grouped: Mapping[Tuple[str, int], List[int]]
) -> Tuple[Tuple[str, int], ...]:
    return tuple(
        sorted(
            {
                (str(object_name), int(index))
                for (object_name, _state), indices in grouped.items()
                for index in indices
            }
        )
    )


def _delete_names(names: Iterable[str]) -> List[str]:
    failures = []
    for name in tuple(names):
        if not name or not _scene_kind(name):
            continue
        try:
            cmd.delete(name)
        except Exception:
            if _scene_kind(name):
                failures.append(name)
    return failures


def _unused_selection_name(base: str, reserved: Iterable[str]) -> str:
    unavailable = set(str(name) for name in reserved)
    candidate_base = sanitize_name(base, fallback="__pp_seq_stage")
    for attempt in range(100):
        requested = (
            candidate_base
            if attempt == 0
            else "{}_{}".format(candidate_base, attempt)
        )
        candidate = (
            str(cmd.get_unused_name(requested))
            if hasattr(cmd, "get_unused_name")
            else requested
        )
        if candidate not in unavailable and not _scene_kind(candidate):
            return candidate
    raise RuntimeError("could not allocate a temporary selection name")


def _preflight_selection_name(
    selection_name: str, state: int, replace: bool
) -> Optional[SelectionOwnership]:
    kind = _scene_kind(selection_name)
    if kind and kind != "selection":
        raise SelectionNameCollision(
            selection_name, kind, "non-selection scene names are never replaced"
        )
    if kind != "selection":
        _SELECTION_OWNERS.pop(selection_name, None)
        return None
    owner = _SELECTION_OWNERS.get(selection_name)
    if owner is None:
        raise SelectionNameCollision(
            selection_name,
            "selection",
            "the selection is not owned by this plugin process",
        )
    current = _selection_fingerprint(selection_name, owner.state)
    if current != owner.fingerprint:
        raise SelectionNameCollision(
            selection_name,
            "selection",
            "the plugin-owned selection was modified outside the command",
        )
    if not replace:
        raise SelectionNameCollision(
            selection_name, "selection", "replace=0 preserves the existing selection"
        )
    return owner


def _populate_selection(
    selection_name: str,
    grouped: Mapping[Tuple[str, int], List[int]],
) -> None:
    """Populate a temporary selection from exact atom identities."""

    if not grouped:
        cmd.select(selection_name, "none")
        return
    if not hasattr(cmd, "select_list"):
        cmd.select(selection_name, _fallback_index_expression(grouped))
        return

    temporary_names = []
    try:
        for part_index, ((object_name, state), indices) in enumerate(grouped.items()):
            temporary = _unused_selection_name(
                "__{}_part_{}".format(selection_name, part_index),
                (selection_name, *temporary_names),
            )
            cmd.select_list(
                temporary,
                object_name,
                indices,
                state=state,
                mode="index",
            )
            temporary_names.append(temporary)
        cmd.select(
            selection_name,
            " or ".join("%{}".format(name) for name in temporary_names),
        )
    except (AttributeError, TypeError):
        # A few older builds expose select_list with a narrower signature.
        cmd.select(selection_name, _fallback_index_expression(grouped))
    finally:
        cleanup_failures = _delete_names(temporary_names)
        if cleanup_failures:
            raise SelectionTransactionError(
                "temporary selection cleanup failed", cleanup_failures
            )


def _install_exact_selection(
    selection_name: str,
    grouped: Mapping[Tuple[str, int], List[int]],
    state: int,
    replace: bool,
) -> Tuple[SelectionOwnership, bool]:
    """Stage, verify, and transactionally install one named selection."""

    previous = _preflight_selection_name(selection_name, state, replace)
    stage = _unused_selection_name(
        "__{}_stage".format(selection_name), (selection_name,)
    )
    backup = ""
    old_is_backup = False
    new_is_final = False
    expected_fingerprint = ""
    try:
        _populate_selection(stage, grouped)
        if _scene_kind(stage) != "selection":
            raise RuntimeError("PyMOL did not create the staged selection")
        expected_fingerprint = _identity_fingerprint(_expected_identity(grouped))
        if _selection_fingerprint(stage, state) != expected_fingerprint:
            raise RuntimeError(
                "staged selection atom identity did not match the result"
            )
        if not hasattr(cmd, "set_name"):
            raise RuntimeError("PyMOL does not expose transactional selection rename")
        if previous is not None:
            backup = _unused_selection_name(
                "__{}_backup".format(selection_name), (selection_name, stage)
            )
            try:
                cmd.set_name(selection_name, backup)
                old_is_backup = True
            except Exception:
                old_is_backup = (
                    _scene_kind(backup) == "selection"
                    and not _scene_kind(selection_name)
                    and _selection_fingerprint(backup, previous.state)
                    == previous.fingerprint
                )
                raise
        try:
            cmd.set_name(stage, selection_name)
            new_is_final = True
        except Exception:
            new_is_final = (
                _scene_kind(selection_name) == "selection"
                and _selection_fingerprint(selection_name, state)
                == expected_fingerprint
            )
            raise
        if _selection_fingerprint(selection_name, state) != expected_fingerprint:
            raise RuntimeError(
                "installed selection atom identity did not match the result"
            )
        if backup:
            try:
                cmd.delete(backup)
            except Exception:
                # Some command implementations report an error after the
                # deletion took effect. The desired final state is complete
                # in that case, so do not destroy the new valid selection.
                if _scene_kind(backup):
                    raise
            old_is_backup = False
        ownership_id = "sequence_selection_{}".format(
            hashlib.sha1(
                "{}\0{}\0{}".format(
                    selection_name, expected_fingerprint, time.time_ns()
                ).encode("utf-8")
            ).hexdigest()[:12]
        )
        ownership = SelectionOwnership(
            id=ownership_id,
            name=selection_name,
            state=state,
            fingerprint=expected_fingerprint,
        )
        _SELECTION_OWNERS[selection_name] = ownership
        return ownership, previous is not None
    except Exception as error:
        rollback_failures = []
        try:
            final_is_new = (
                bool(expected_fingerprint)
                and _scene_kind(selection_name) == "selection"
                and _selection_fingerprint(selection_name, state)
                == expected_fingerprint
            )
        except Exception:
            final_is_new = new_is_final
        if final_is_new:
            rollback_stage = stage
            if _scene_kind(rollback_stage):
                rollback_stage = _unused_selection_name(
                    "__{}_rollback".format(selection_name),
                    (selection_name, stage, backup),
                )
            try:
                cmd.set_name(selection_name, rollback_stage)
                stage = rollback_stage
                new_is_final = False
            except Exception:
                rollback_failures.append(selection_name)
        try:
            backup_is_old = bool(
                previous is not None
                and backup
                and _scene_kind(backup) == "selection"
                and _selection_fingerprint(backup, previous.state)
                == previous.fingerprint
            )
        except Exception:
            backup_is_old = old_is_backup
        if backup_is_old:
            try:
                cmd.set_name(backup, selection_name)
                old_is_backup = False
            except Exception:
                rollback_failures.append(backup)
        rollback_failures.extend(
            _delete_names((stage, backup if old_is_backup else ""))
        )
        if isinstance(error, SelectionTransactionError):
            rollback_failures.extend(error.rollback_failures)
        try:
            if previous is None:
                if _scene_kind(selection_name):
                    rollback_failures.append(selection_name)
            elif (
                _scene_kind(selection_name) != "selection"
                or _selection_fingerprint(selection_name, previous.state)
                != previous.fingerprint
            ):
                rollback_failures.append(selection_name)
        except Exception:
            rollback_failures.append(selection_name)
        raise SelectionTransactionError(
            "sequence selection transaction failed: {}".format(error),
            tuple(dict.fromkeys(rollback_failures)),
        ) from error


def pp_select_sequence(
    objects,
    queries,
    selection_name="selected_residues",
    chain="",
    state=1,
    separator="|",
    gap_mode="exact",
    max_gap=0,
    occurrence="all",
    visualize=1,
    color="red",
    max_matches=10000,
    replace=1,
):
    """Select sequence motifs and return a JSON-serializable result.

    Args:
        objects: PyMOL object selection.
        queries: One or more motifs separated by ``separator``.
        selection_name: Output selection, or an empty string for no selection.
        chain: Exact chain filter. Empty means all chains, including blank.
        state: PyMOL state to read.
        separator: Literal query separator.
        gap_mode: ``exact`` or ``bounded``.
        max_gap: Maximum intervening residues between adjacent query letters.
        occurrence: ``all``, ``first``, or ``unique`` per query.
        visualize: Strict boolean controlling sticks/color side effects.
        color: PyMOL color used when visualization is enabled.
        max_matches: Total safety bound for ``occurrence=all``.
        replace: Replace only an unchanged selection owned by this process.

    Returns:
        A JSON-serializable dictionary containing tracks, exact matches,
        selected atom count, warnings, and errors.
    """

    result = _empty_result()
    try:
        parsed_state = as_int(state, minimum=0, name="state")
        parsed_gap = as_int(max_gap, minimum=0, name="max_gap")
        parsed_limit = as_int(
            max_matches, minimum=1, maximum=100000, name="max_matches"
        )
        parsed_visualize = as_bool(visualize)
        parsed_replace = as_bool(replace)
        normalized_queries = split_queries(queries, str(separator))
        names = _object_names(objects)
        requested_selection_name = (
            "" if selection_name is None else str(selection_name).strip()
        )
        chain_text = "" if chain is None else str(chain)
        mode_text = str(gap_mode).strip().lower()
        if mode_text in ("gap", "gapped"):
            mode_text = "bounded"
        occurrence_text = str(occurrence).strip().lower()
        output_name = (
            sanitize_name(requested_selection_name) if requested_selection_name else ""
        )
        if requested_selection_name and output_name != requested_selection_name:
            result["warnings"].append(
                "selection name {!r} was sanitized to {!r}".format(
                    requested_selection_name, output_name
                )
            )

        result.update(
            {
                "selection_name": output_name or None,
                "objects": names,
                "queries": list(normalized_queries),
                "parameters": {
                    "chain": chain_text,
                    "state": parsed_state,
                    "separator": str(separator),
                    "gap_mode": mode_text,
                    "max_gap": parsed_gap,
                    "occurrence": occurrence_text,
                    "visualize": parsed_visualize,
                    "color": str(color),
                    "max_matches": parsed_limit,
                    "replace": parsed_replace,
                },
            }
        )
        if not names:
            raise ValueError("no molecule objects matched {!r}".format(objects))

        streams = _read_atom_streams(names, parsed_state, result["warnings"])
        sequence_index = index_atom_streams(streams, protein_only=True)
        result["tracks"] = [
            {
                "object": track.object,
                "state": track.state,
                "segi": track.segi,
                "chain": track.chain,
                "sequence": track.sequence,
                "residue_count": len(track.residues),
            }
            for track in sequence_index.tracks
        ]
        if not sequence_index.tracks:
            raise ValueError("matched objects contain no protein residue tracks")

        chain_filter: Optional[str] = chain_text if chain_text else None
        matches = find_sequence_matches(
            sequence_index,
            normalized_queries,
            gap_mode=mode_text,
            max_gap=parsed_gap,
            occurrence=occurrence_text,
            chain=chain_filter,
            max_matches=parsed_limit,
        )
        grouped_indices = _indices_by_object(matches)
        result["matches"] = [match.to_dict() for match in matches]
        result["match_count"] = len(matches)
        result["selected_atom_count"] = sum(
            len(indices) for indices in grouped_indices.values()
        )
        matched_query_indices = {match.query_index for match in matches}
        unmatched_queries = [
            query
            for query_index, query in enumerate(normalized_queries)
            if query_index not in matched_query_indices
        ]
        if unmatched_queries:
            result["unmatched_queries"] = unmatched_queries
            result["warnings"].append(
                "no match for: {}".format(
                    ", ".join(repr(query) for query in unmatched_queries)
                )
            )
            result["partial_reasons"].append("some queries had no match")
            result["status"] = "partial"

        if output_name and not matches:
            raise SequenceNoMatchError(normalized_queries)

        if output_name:
            ownership, replaced = _install_exact_selection(
                output_name,
                grouped_indices,
                parsed_state,
                parsed_replace,
            )
            result["selection_created"] = True
            result["scene_changed"] = True
            result["scene_preserved"] = False
            result["selection_ownership"] = {
                "owner": "pymolplugins.alignment.sequence",
                "id": ownership.id,
                "fingerprint": ownership.fingerprint,
                "replaced_owned_selection": replaced,
            }
            if parsed_visualize and matches:
                try:
                    cmd.show("sticks", output_name)
                    cmd.color(str(color), output_name)
                    result["visualization_status"] = "applied"
                except Exception as error:
                    result["warnings"].append(
                        "selection was created, but visualization failed: {}".format(
                            error
                        )
                    )
                    result["visualization_status"] = "failed"
                    result["partial_reasons"].append("visualization failed")
                    result["status"] = "partial"
                    print(
                        "Sequence partial: selection {!r} is valid, but "
                        "visualization failed: {}".format(output_name, error),
                        flush=True,
                    )
                    traceback.print_exc()
            elif parsed_visualize:
                result["visualization_status"] = "skipped_no_matches"
            else:
                result["visualization_status"] = "disabled"

        result["ok"] = True
        if result["status"] != "partial":
            result["status"] = "success"
        print(
            "Sequence {}: {} match(es), {} atom(s){}{}".format(
                "partially completed" if result["status"] == "partial" else "ready",
                result["match_count"],
                result["selected_atom_count"],
                " in %s" % output_name if output_name else "",
                (
                    "; " + "; ".join(result["partial_reasons"])
                    if result["status"] == "partial"
                    else ""
                ),
            )
        )
    except SelectionNameCollision as error:
        collision_guidance = (
            "rerun with replace=1 to replace this unchanged plugin-owned selection"
            if error.reason.startswith("replace=0")
            else "choose a new selection_name; user-owned scene names are never replaced"
        )
        result.update(
            {
                "error_code": "selection_name_collision",
                "collision": {
                    "name": error.name,
                    "kind": error.kind,
                    "reason": error.reason,
                },
                "guidance": collision_guidance,
            }
        )
        result["errors"].append(str(error))
        print(
            "Sequence error: {}. Scene unchanged; {}.".format(
                error, collision_guidance
            )
        )
    except SequenceMatchLimitError as error:
        result.update(
            {
                "error_code": "sequence_match_limit",
                "match_limit": error.limit,
                "observed_match_count_at_least": error.observed_at_least,
                "limited_query": error.query,
                "guidance": (
                    "use occurrence=first, narrow chain/query, reduce max_gap, "
                    "or raise max_matches deliberately"
                ),
            }
        )
        result["errors"].append(str(error))
        print(
            "Sequence error: {}. Scene unchanged; narrow the query or use "
            "occurrence=first.".format(error)
        )
    except SequenceOccurrenceError as error:
        result.update(
            {
                "error_code": (
                    "sequence_no_match"
                    if error.match_count == 0
                    else "sequence_ambiguous"
                ),
                "occurrence_match_count": error.match_count,
                "occurrence_count_is_lower_bound": error.at_least,
                "guidance": (
                    "check the query and chain"
                    if error.match_count == 0
                    else "use a longer query, narrow chain, or choose occurrence=first explicitly"
                ),
            }
        )
        result["errors"].append(str(error))
        print(
            "Sequence error: {}. Scene unchanged; {}.".format(
                error, result["guidance"]
            )
        )
    except SequenceNoMatchError as error:
        result.update(
            {
                "error_code": "sequence_no_match",
                "missing_queries": list(error.queries),
                "guidance": "check the query and chain; the previous selection was preserved",
            }
        )
        result["errors"].append(str(error))
        print(
            "Sequence error: {}. Scene unchanged; check the query and chain.".format(
                error
            )
        )
    except SelectionTransactionError as error:
        result.update(
            {
                "error_code": "selection_transaction_failed",
                "rollback_failures": list(error.rollback_failures),
                "scene_preserved": not bool(error.rollback_failures),
                "scene_changed": bool(error.rollback_failures),
                "guidance": "keep the prior selection and retry with a different selection_name",
            }
        )
        result["errors"].append(str(error))
        print(
            "Sequence error: {}. {}".format(
                error,
                "Prior selection restored."
                if result["scene_preserved"]
                else "Rollback was incomplete; inspect the listed temporary selections.",
            ),
            flush=True,
        )
        traceback.print_exc()
    except ValueError as error:
        result.update(
            {
                "error_code": "invalid_sequence_request",
                "guidance": "check objects, queries, occurrence, gap_mode, and numeric limits",
            }
        )
        result["errors"].append(str(error))
        print(
            "Sequence error: {}. Scene unchanged; check command parameters.".format(
                error
            )
        )
    except Exception as error:
        result.update(
            {
                "error_code": "sequence_internal_error",
                "scene_preserved": False,
                "scene_changed": True,
                "guidance": "inspect the traceback and retry with a new selection_name",
            }
        )
        result["errors"].append(str(error))
        print("Sequence internal error: {}.".format(error), flush=True)
        traceback.print_exc()
    if not result["ok"]:
        result["status"] = "error"
    return result


def select_by_sequence(
    protein_obj_sele,
    queries,
    selection_name="selected_residues",
    visualize=True,
    case_sensitive=True,
    allow_gaps=False,
    separator="\\",
    color="red",
    max_gap=_LEGACY_GAP_DEFAULT,
    occurrence="all",
    state=1,
    chain="",
    max_matches=10000,
    replace=1,
):
    """Compatibility wrapper for ``sbs/select_sequence/seq_select``.

    Query matching now always normalizes case. Legacy ``allow_gaps=1`` maps to
    bounded gaps (default maximum three intervening residues) rather than the
    old unbounded greedy regular expression.
    """

    try:
        as_bool(case_sensitive)  # Validate legacy string values; case is normalized.
        use_gaps = as_bool(allow_gaps)
    except ValueError as error:
        result = _empty_result()
        result["errors"].append(str(error))
        print("select_by_sequence error: {}".format(error))
        return result
    return pp_select_sequence(
        protein_obj_sele,
        queries,
        selection_name=selection_name,
        chain=chain,
        state=state,
        separator=separator,
        gap_mode="bounded" if use_gaps else "exact",
        max_gap=max_gap if use_gaps else 0,
        occurrence=occurrence,
        visualize=visualize,
        color=color,
        max_matches=max_matches,
        replace=replace,
    )


def show_help():
    """Print concise command help for canonical and legacy names."""

    print(
        """
pp_select_sequence objects, queries [, selection_name [, chain [, state
                   [, separator [, gap_mode [, max_gap [, occurrence
                   [, visualize [, color [, max_matches [, replace]]]]]]]]]]]

  gap_mode: exact | bounded
  occurrence: all | first | unique
  max_matches: 1..100000 safety limit for occurrence=all
  replace: 1 replaces only an unchanged selection owned by this process
  X matches one residue; whitespace and '-' in queries are ignored.

Legacy aliases remain available:
  sbs / select_sequence / seq_select protein, queries, selection_name,
      visualize, case_sensitive, allow_gaps, separator, color
""".strip()
    )


def _register_commands(command_api=None) -> None:
    selected = cmd if command_api is None else command_api
    extend_command(selected, "pp_select_sequence", pp_select_sequence)
    extend_command(selected, "sbs_help", show_help)
    extend_command(selected, "sbs", select_by_sequence)
    extend_command(selected, "select_sequence", select_by_sequence)
    extend_command(selected, "seq_select", select_by_sequence)

    try:
        selected.auto_arg[0]["pp_select_sequence"] = [selected.object_sc, "objects", ""]
        selected.auto_arg[0]["sbs"] = [selected.object_sc, "object_name", ""]
        query_shortcut = lambda: selected.Shortcut(["LLFGYPVYV", "MGKSL", "AXA"])
        selected.auto_arg[1]["pp_select_sequence"] = [
            query_shortcut,
            "query_sequences",
            "",
        ]
        selected.auto_arg[1]["sbs"] = [query_shortcut, "query_sequences", ""]
    except (AttributeError, KeyError, TypeError):
        # Some headless/fake command implementations do not expose completion.
        pass


_register_commands()


__all__ = [
    "pp_select_sequence",
    "select_by_sequence",
    "show_help",
]
