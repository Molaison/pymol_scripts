"""Resolve PyMOL names without entering the atom-level ``iterate`` namespace."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import fnmatch
import re
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from pymolplugins.core.parameters import as_bool, as_float, as_int, split_names
from pymolplugins.core.pymol_groups import (
    expand_group_leaves,
    group_parent_snapshot,
    is_group,
    object_type,
)


class TargetResolutionError(ValueError):
    """Raised when a target expression is invalid or resolves to nothing."""


@dataclass(frozen=True)
class TargetMatch:
    query: str
    name: str
    object_type: str
    kind: str
    matched_by: str
    score: float = 1.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "query": self.query,
            "name": self.name,
            "object_type": self.object_type,
            "kind": self.kind,
            "matched_by": self.matched_by,
            "score": self.score,
        }


@dataclass(frozen=True)
class ExpandedTarget:
    source: str
    target: str
    object_type: str
    kind: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "object_type": self.object_type,
            "kind": self.kind,
        }


def _all_names(cmd_api) -> List[str]:
    try:
        values = cmd_api.get_names("all")
    except TypeError:
        values = cmd_api.get_names()
    return list(dict.fromkeys(str(value) for value in (values or ()) if str(value)))


def _kind(object_type_name: str) -> str:
    value = str(object_type_name or "")
    if value == "object:group":
        return "group"
    if value.startswith("object:"):
        return value.split(":", 1)[1] or "object"
    if value.startswith("selection"):
        return "selection"
    return value or "unknown"


def _allowed(object_type_name: str, allowed: Sequence[str]) -> bool:
    values = set(allowed)
    if "all" in values:
        return True
    kind = _kind(object_type_name)
    if kind == "group":
        return "group" in values
    if object_type_name.startswith("object:"):
        return "object" in values or kind in values
    return kind in values


def _parse_kinds(kinds: object) -> Tuple[str, ...]:
    values = tuple(value.lower() for value in split_names(kinds or "object,group"))
    if not values:
        raise TargetResolutionError("kinds must contain at least one value")
    supported = {
        "all",
        "object",
        "group",
        "molecule",
        "cgo",
        "map",
        "measurement",
        "callback",
        "selection",
    }
    unknown = sorted(set(values) - supported)
    if unknown:
        raise TargetResolutionError("unknown target kinds: " + ", ".join(unknown))
    return values


def _match_candidates(
    query: str,
    candidates: Sequence[Tuple[str, str]],
    mode: str,
    case_sensitive: bool,
    fuzzy_cutoff: float,
) -> List[Tuple[str, str, str, float]]:
    def normalized(value: str) -> str:
        return value if case_sensitive else value.casefold()

    needle = normalized(query)
    exact = [
        (name, object_type_name, "exact", 1.0)
        for name, object_type_name in candidates
        if normalized(name) == needle
    ]
    selected_mode = mode
    if mode == "auto":
        if exact:
            return exact
        selected_mode = "glob" if any(character in query for character in "*?[") else "contains"

    if selected_mode == "exact":
        return exact
    if selected_mode == "glob":
        return [
            (name, object_type_name, "glob", 1.0)
            for name, object_type_name in candidates
            if fnmatch.fnmatchcase(normalized(name), needle)
        ]
    if selected_mode == "contains":
        values = [
            (name, object_type_name, "contains", len(needle) / max(len(normalized(name)), 1))
            for name, object_type_name in candidates
            if needle in normalized(name)
        ]
        if values or mode != "auto":
            return values
        selected_mode = "fuzzy"
    if selected_mode == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error as exc:
            raise TargetResolutionError(f"invalid target regex: {exc}") from exc
        return [
            (name, object_type_name, "regex", 1.0)
            for name, object_type_name in candidates
            if pattern.search(name)
        ]
    if selected_mode == "fuzzy":
        values = []
        for name, object_type_name in candidates:
            score = SequenceMatcher(None, needle, normalized(name)).ratio()
            if score >= fuzzy_cutoff:
                values.append((name, object_type_name, "fuzzy", score))
        values.sort(key=lambda value: (-value[3], value[0].casefold(), value[0]))
        return values
    raise TargetResolutionError(
        "match must be auto, exact, glob, contains, regex, or fuzzy"
    )


def _target_queries(targets: object, mode: str) -> Tuple[str, ...]:
    """Return target expressions without treating a regex as a name list.

    Comma and whitespace are useful list separators for object names, but both
    are ordinary regex syntax (for example ``{1,3}`` and a literal space).
    Console quoting has already been normalized before this boundary, so one
    string in regex mode is one complete expression. Direct Python callers may
    still pass an iterable to request several regex expressions explicitly.
    """

    if mode != "regex":
        return tuple(split_names(targets))
    if targets is None:
        return ()
    if isinstance(targets, str):
        return (targets,) if targets else ()
    if isinstance(targets, Iterable):
        values = []
        for value in targets:
            if value is None:
                continue
            text = str(value)
            if text:
                values.append(text)
        return tuple(values)
    text = str(targets)
    return (text,) if text else ()


def resolve_targets(
    cmd_api,
    targets: object,
    *,
    match: str = "auto",
    kinds: object = "object,group",
    fuzzy_cutoff: object = 0.72,
    limit: object = 0,
    case_sensitive: object = 0,
    require_match: object = 1,
) -> Tuple[TargetMatch, ...]:
    """Resolve one or more object/group expressions deterministically.

    ``auto`` tries exact matching, then glob/substring matching, and finally a
    fuzzy fallback.  Mutation callers should preview the returned matches
    before executing commands.
    """

    mode = str(match).strip().lower()
    if mode not in {"auto", "exact", "glob", "contains", "regex", "fuzzy"}:
        raise TargetResolutionError(
            "match must be auto, exact, glob, contains, regex, or fuzzy"
        )
    queries = _target_queries(targets, mode)
    if not queries:
        raise TargetResolutionError("targets must contain at least one expression")
    allowed = _parse_kinds(kinds)
    cutoff = as_float(fuzzy_cutoff, minimum=0.0, maximum=1.0, name="fuzzy_cutoff")
    maximum = as_int(limit, minimum=0, name="limit")
    sensitive = as_bool(case_sensitive)
    candidates = [
        (name, object_type(cmd_api, name))
        for name in _all_names(cmd_api)
    ]
    candidates = [
        value for value in candidates if _allowed(value[1], allowed)
    ]
    candidates.sort(key=lambda value: (value[0].casefold(), value[0]))
    result: List[TargetMatch] = []
    seen = set()
    missing = []
    for query in queries:
        values = _match_candidates(query, candidates, mode, sensitive, cutoff)
        if maximum:
            values = values[:maximum]
        if not values:
            missing.append(query)
            continue
        for name, object_type_name, matched_by, score in values:
            if name in seen:
                continue
            seen.add(name)
            result.append(
                TargetMatch(
                    query=query,
                    name=name,
                    object_type=object_type_name,
                    kind=_kind(object_type_name),
                    matched_by=matched_by,
                    score=float(score),
                )
            )
    if missing and as_bool(require_match):
        raise TargetResolutionError(
            "target expressions matched nothing: " + ", ".join(missing)
        )
    if not result and as_bool(require_match):
        raise TargetResolutionError("target expressions resolved no supported names")
    return tuple(result)


def expand_targets(
    cmd_api,
    matches: Iterable[TargetMatch],
    group_mode: str = "atomic",
) -> Tuple[ExpandedTarget, ...]:
    """Expand matched groups as atomic names, recursive leaves, both, or molecules."""

    mode = str(group_mode).strip().lower()
    if mode not in {"atomic", "leaves", "both", "molecules"}:
        raise TargetResolutionError(
            "group_mode must be atomic, leaves, both, or molecules"
        )
    values = tuple(matches)
    parents: Mapping[str, str] = {}
    if mode != "atomic" and any(match.kind == "group" for match in values):
        parents = group_parent_snapshot(cmd_api)
    result: List[ExpandedTarget] = []
    seen = set()

    def append(source: str, target: str, *, molecule_leaf_only: bool = False) -> None:
        if target in seen:
            return
        object_type_name = object_type(cmd_api, target)
        if molecule_leaf_only and object_type_name != "object:molecule":
            return
        seen.add(target)
        result.append(
            ExpandedTarget(
                source=source,
                target=target,
                object_type=object_type_name,
                kind=_kind(object_type_name),
            )
        )

    for match in values:
        if match.kind != "group":
            append(match.name, match.name)
            continue
        if mode in {"atomic", "both"}:
            append(match.name, match.name)
        if mode in {"leaves", "both", "molecules"}:
            for leaf in expand_group_leaves(cmd_api, match.name, dict(parents)):
                append(
                    match.name,
                    leaf,
                    molecule_leaf_only=(mode == "molecules"),
                )
    if not result:
        raise TargetResolutionError(
            f"resolved targets produced no executable names for group_mode={mode}"
        )
    return tuple(result)
