"""PyMOL adapter for exact, residue-safe sequence motif selection.

The residue index and matching implementation live in ``utilities.sequence``
and do not import PyMOL.  This module is intentionally limited to reading
models, creating an exact atom-index selection, and optional visualization.
"""

from __future__ import annotations

import sys
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pymol import cmd

try:
    from pymolplugins.core.parameters import as_bool, as_int, extend_command, sanitize_name
    from pymolplugins.core.sequence import (
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
        SequenceOccurrenceError,
        find_sequence_matches,
        index_atom_streams,
        split_queries,
    )


SCHEMA_VERSION = "1.0"
_LEGACY_GAP_DEFAULT = 3


def _empty_result() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "selection_name": None,
        "selection_created": False,
        "objects": [],
        "queries": [],
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


def _select_exact_indices(
    selection_name: str,
    grouped: Mapping[Tuple[str, int], List[int]],
) -> None:
    """Create one selection using exact PyMOL atom indices.

    ``select_list`` avoids expression length limits and accepts an exact object
    name. Temporary selections are always removed. A selection-expression
    fallback supports older PyMOL versions which lack ``select_list``.
    """

    if not grouped:
        cmd.select(selection_name, "none")
        return
    if not hasattr(cmd, "select_list"):
        cmd.select(selection_name, _fallback_index_expression(grouped))
        return

    temporary_names = []
    try:
        for part_index, ((object_name, state), indices) in enumerate(grouped.items()):
            base = sanitize_name(
                "__{}_seq_part_{}".format(selection_name, part_index),
                fallback="__pp_seq_part",
            )
            temporary = (
                cmd.get_unused_name(base)
                if hasattr(cmd, "get_unused_name")
                else base
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
        for temporary in temporary_names:
            try:
                cmd.delete(temporary)
            except Exception:
                traceback.print_exc()


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

    Returns:
        A JSON-serializable dictionary containing tracks, exact matches,
        selected atom count, warnings, and errors.
    """

    result = _empty_result()
    try:
        parsed_state = as_int(state, minimum=0, name="state")
        parsed_gap = as_int(max_gap, minimum=0, name="max_gap")
        parsed_visualize = as_bool(visualize)
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
        )
        grouped_indices = _indices_by_object(matches)
        result["matches"] = [match.to_dict() for match in matches]
        result["match_count"] = len(matches)
        result["selected_atom_count"] = sum(
            len(indices) for indices in grouped_indices.values()
        )

        if output_name:
            _select_exact_indices(output_name, grouped_indices)
            result["selection_created"] = True
            if parsed_visualize and matches:
                try:
                    cmd.show("sticks", output_name)
                    cmd.color(str(color), output_name)
                except Exception as error:
                    result["warnings"].append(
                        "selection was created, but visualization failed: {}".format(
                            error
                        )
                    )
                    traceback.print_exc()

        result["ok"] = True
        print(
            "pp_select_sequence: {} match(es), {} atom(s){}".format(
                result["match_count"],
                result["selected_atom_count"],
                " in %s" % output_name if output_name else "",
            )
        )
    except (ValueError, SequenceOccurrenceError) as error:
        result["errors"].append(str(error))
        print("pp_select_sequence error: {}".format(error))
    except Exception as error:
        result["errors"].append(str(error))
        print("pp_select_sequence error: {}".format(error))
        traceback.print_exc()
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
    )


def show_help():
    """Print concise command help for canonical and legacy names."""

    print(
        """
pp_select_sequence objects, queries [, selection_name [, chain [, state
                   [, separator [, gap_mode [, max_gap [, occurrence
                   [, visualize [, color]]]]]]]]]

  gap_mode: exact | bounded
  occurrence: all | first | unique
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
