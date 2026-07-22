"""Reliable PyMOL group membership from partial-session parent metadata."""

from typing import Dict, Iterable, List, Optional, Set, Tuple


class GroupMetadataError(RuntimeError):
    pass


def _all_names(cmd_api) -> List[str]:
    try:
        return list(cmd_api.get_names("all") or [])
    except TypeError:
        return list(cmd_api.get_names() or [])


def object_type(cmd_api, name: str) -> str:
    try:
        return str(cmd_api.get_type(name) or "")
    except Exception:
        return ""


def is_group(cmd_api, name: str) -> bool:
    return object_type(cmd_api, name) == "object:group"


def is_leaf(cmd_api, name: str) -> bool:
    value = object_type(cmd_api, name)
    return value.startswith("object:") and value != "object:group"


def group_parent_snapshot(cmd_api) -> Dict[str, str]:
    """Return ``name -> direct parent group`` for every current object name."""
    try:
        session = cmd_api.get_session("", partial=1, quiet=1)
    except TypeError:
        session = cmd_api.get_session("", 1, 1)
    except AttributeError as exc:
        raise GroupMetadataError(
            "this PyMOL build lacks get_session group metadata"
        ) from exc
    records = session.get("names") if isinstance(session, dict) else None
    if not isinstance(records, (list, tuple)):
        raise GroupMetadataError("PyMOL partial session has no name records")
    available = set(_all_names(cmd_api))
    result: Dict[str, str] = {}
    for record in records:
        if not isinstance(record, (list, tuple)) or len(record) < 7:
            continue
        name = str(record[0] or "")
        if name and name in available:
            result[name] = str(record[6] or "")
    missing = sorted(name for name in available if is_group(cmd_api, name) and name not in result)
    if missing:
        raise GroupMetadataError(
            "PyMOL omitted parent metadata for groups: " + ", ".join(missing)
        )
    return result


def direct_children(group: str, parents: Dict[str, str]) -> List[str]:
    return [name for name, parent in parents.items() if parent == group]


def expand_group_leaves(
    cmd_api,
    group: str,
    parents: Optional[Dict[str, str]] = None,
    _active: Optional[Set[str]] = None,
    _seen_leaves: Optional[Set[str]] = None,
) -> List[str]:
    mapping = group_parent_snapshot(cmd_api) if parents is None else parents
    active = set() if _active is None else _active
    seen_leaves = set() if _seen_leaves is None else _seen_leaves
    if not is_group(cmd_api, group):
        raise GroupMetadataError(f"not a PyMOL group: {group}")
    if group in active:
        raise GroupMetadataError(f"cyclic group membership involving: {group}")
    active.add(group)
    result = []
    for name in direct_children(group, mapping):
        if is_group(cmd_api, name):
            result.extend(
                expand_group_leaves(cmd_api, name, mapping, active, seen_leaves)
            )
        elif is_leaf(cmd_api, name) and name not in seen_leaves:
            seen_leaves.add(name)
            result.append(name)
    active.remove(group)
    return result


def expand_names(cmd_api, names: Iterable[str]) -> List[str]:
    parents = group_parent_snapshot(cmd_api)
    result: List[str] = []
    seen: Set[str] = set()
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        values = (
            expand_group_leaves(cmd_api, name, parents)
            if is_group(cmd_api, name)
            else [name]
        )
        for value in values:
            if not is_leaf(cmd_api, value):
                raise GroupMetadataError(
                    f"name is not a leaf object or group: {value}"
                )
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def root_groups(cmd_api, parents: Optional[Dict[str, str]] = None) -> List[str]:
    mapping = group_parent_snapshot(cmd_api) if parents is None else parents
    groups = sorted(name for name in _all_names(cmd_api) if is_group(cmd_api, name))
    return [name for name in groups if not mapping.get(name)]


def groups_containing(cmd_api, leaf: str) -> List[Tuple[str, List[str]]]:
    parents = group_parent_snapshot(cmd_api)
    matches = []
    for group in sorted(name for name in _all_names(cmd_api) if is_group(cmd_api, name)):
        leaves = expand_group_leaves(cmd_api, group, parents)
        if leaf in leaves:
            matches.append((group, leaves))
    matches.sort(key=lambda item: (len(item[1]), item[0]))
    return matches
