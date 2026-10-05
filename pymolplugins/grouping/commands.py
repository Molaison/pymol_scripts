"""Non-destructive grouping helpers for loaded PyMOL molecule objects."""

from collections import defaultdict
import os
import sys
import traceback
from typing import Dict, List

from pymol import cmd

try:
    from pymolplugins.core.parameters import (
        as_bool,
        as_int,
        extend_command,
        sanitize_name,
        split_names,
    )
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.parameters import (  # type: ignore
        as_bool,
        as_int,
        extend_command,
        sanitize_name,
        split_names,
    )


def _error_result(exc: Exception, result_field: str) -> Dict[str, object]:
    """Return a structured command error without tracing expected input errors."""

    print(f"Error: {exc}")
    if not isinstance(exc, (TypeError, ValueError)):
        traceback.print_exc()
    return {"status": "error", "error": str(exc), result_field: {}}


def split_firstn_sep(full_name, sep="_", num=1):
    """Return ``(full_name, group_key, remainder)`` after the first *num* fields."""
    count = as_int(num, minimum=1, name="num")
    parts = str(full_name).split(str(sep), count)
    if len(parts) != count + 1:
        raise ValueError(f"Expected at least {count + 1} fields in '{full_name}'")
    group_key = str(sep).join(parts[:count])
    remainder = parts[count]
    return str(full_name), group_key, remainder


def split_lastn_sep(full_name, sep="_", num=1):
    """Return ``(full_name, prefix, final *num* fields)``."""
    count = as_int(num, minimum=1, name="num")
    parts = str(full_name).rsplit(str(sep), count)
    if len(parts) != count + 1:
        raise ValueError(f"Expected at least {count + 1} fields in '{full_name}'")
    prefix = parts[0]
    identifier = str(sep).join(parts[1:])
    return str(full_name), prefix, identifier


def split_prefix_identifier(full_name, method="split_lastn_sep", **params):
    """Dispatch a supported object-name split strategy."""
    strategies = {
        "split_lastn_sep": split_lastn_sep,
        "split_firstn_sep": split_firstn_sep,
    }
    try:
        strategy = strategies[str(method)]
    except KeyError as exc:
        raise ValueError(f"Unknown grouping method: {method}") from exc
    return strategy(full_name, **params)


def _selected_molecules(selection="all") -> List[str]:
    names = list(cmd.get_object_list(f"({selection})") or [])
    return sorted(dict.fromkeys(names))


def _partition_names(selection, method, sep, num) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for name in _selected_molecules(selection):
        _, key, _ = split_prefix_identifier(name, method=method, sep=sep, num=num)
        groups[key].append(name)
    return dict(sorted(groups.items()))


def _group_creation_plan(selection, method, sep, num, prefix) -> Dict[str, List[str]]:
    partitions = _partition_names(selection, method, sep, num)
    if not partitions:
        raise ValueError(f"No molecule objects found for selection '{selection}'")
    plan: Dict[str, List[str]] = {}
    source_keys: Dict[str, str] = {}
    for key, members in partitions.items():
        group_name = sanitize_name(
            f"{prefix}{key}", fallback="group", max_length=120
        )
        if group_name in plan:
            raise ValueError(
                "group keys %r and %r both normalize to %r"
                % (source_keys[group_name], key, group_name)
            )
        source_keys[group_name] = key
        plan[group_name] = list(members)
    return plan


def _preflight_group_names(plan: Dict[str, List[str]]) -> Dict[str, str]:
    existing = {str(name) for name in (cmd.get_names("all") or ())}
    types = {}
    for group_name in plan:
        if group_name not in existing:
            continue
        object_type = str(cmd.get_type(group_name) or "unknown")
        types[group_name] = object_type
        if object_type != "object:group":
            raise ValueError(
                "group name %r collides with existing %s; scene was not changed"
                % (group_name, object_type)
            )
    return types


def group_objects_by_name(
    selection="all",
    method="split_lastn_sep",
    sep="_",
    num=1,
    prefix="",
    replace=0,
):
    """Create real PyMOL groups without merging or deleting source objects.

    Args:
        selection: Molecule objects to partition.
        method: ``split_lastn_sep`` or ``split_firstn_sep``.
        sep: Object-name separator.
        num: Number of leading/trailing fields used by the strategy.
        prefix: Optional prefix for generated group names.
        replace: Delete an existing group of the same name before adding members.

    Returns:
        Mapping from created group name to member object names.
    """
    try:
        replace_value = as_bool(replace)
        plan = _group_creation_plan(
            selection, str(method), str(sep), num, str(prefix)
        )
        existing_types = _preflight_group_names(plan)
        if replace_value:
            for group_name in plan:
                if existing_types.get(group_name) == "object:group":
                    cmd.delete(group_name)
        for group_name, members in plan.items():
            for member in members:
                cmd.group(group_name, member, action="add")
            print(f"Grouped {len(members)} objects into {group_name}")
        return plan
    except Exception as exc:
        return _error_result(exc, "groups")


def merge_models_by_name(
    selection="all",
    method="split_lastn_sep",
    sep="_",
    num=1,
    prefix="merged_",
    align=0,
    delete_sources=0,
):
    """Explicit legacy operation that creates merged molecule objects.

    Source deletion is opt-in through ``delete_sources=1``.  Use
    :func:`group_objects_by_name` when a real ``object:group`` is desired.
    """
    try:
        align_value = as_bool(align)
        delete_value = as_bool(delete_sources)
        partitions = _partition_names(selection, str(method), str(sep), num)
        if not partitions:
            raise ValueError(f"No molecule objects found for selection '{selection}'")
        result = {}
        for key, members in partitions.items():
            if not members:
                continue
            reference = members[0]
            if align_value:
                for mobile in members[1:]:
                    cmd.align(mobile, reference)
            merged_name = sanitize_name(f"{prefix}{key}", fallback="merged", max_length=120)
            cmd.create(merged_name, " or ".join(f"({name})" for name in members))
            if delete_value:
                for member in members:
                    cmd.delete(member)
            result[merged_name] = members
            print(
                f"Merged {len(members)} objects into {merged_name}; "
                f"delete_sources={delete_value}"
            )
        return result
    except Exception as exc:
        return _error_result(exc, "models")


def _resolve_explicit_groups(selection) -> List[str]:
    requested = split_names(selection)
    if not requested:
        raise ValueError("group selection must contain at least one group name")
    available = sorted(
        str(name)
        for name in (cmd.get_names("all") or ())
        if str(cmd.get_type(name) or "") == "object:group"
    )
    if len(requested) == 1 and requested[0].lower() == "all":
        if not available:
            raise ValueError("no PyMOL groups are loaded")
        return available
    available_set = set(available)
    missing = [name for name in requested if name not in available_set]
    if missing:
        raise ValueError("unknown PyMOL group: " + ", ".join(missing))
    return list(dict.fromkeys(requested))


def _alignment_plan(selection, reference) -> Dict[str, Dict[str, object]]:
    groups = _resolve_explicit_groups(selection)
    explicit_reference = str(reference or "").strip()
    plan: Dict[str, Dict[str, object]] = {}
    for group_name in groups:
        members = sorted(
            dict.fromkeys(
                str(name)
                for name in (cmd.get_object_list(group_name) or ())
                if str(cmd.get_type(name) or "") == "object:molecule"
            )
        )
        if explicit_reference and explicit_reference not in members:
            raise ValueError(
                "reference %r is not a molecule member of group %r; "
                "scene was not changed" % (explicit_reference, group_name)
            )
        plan[group_name] = {
            "members": members,
            "reference": explicit_reference or (members[0] if members else ""),
        }
    return plan


def align_in_groups(selection="all", reference=""):
    """Align molecule members in exact groups using a deterministic reference."""
    try:
        plan = _alignment_plan(selection, reference)
        summary = {}
        for group_name, entry in plan.items():
            members = entry["members"]
            reference_name = entry["reference"]
            if len(members) < 2:
                summary[group_name] = []
                continue
            aligned = []
            for mobile in members:
                if mobile == reference_name:
                    continue
                aligned.append((mobile, cmd.align(mobile, reference_name)))
            summary[group_name] = aligned
        return summary
    except Exception as exc:
        return _error_result(exc, "groups")


def merge_groups(
    model_selection="all",
    prefix=None,
    methods="split_lastn_sep",
    sep="_",
    num=1,
    replace=0,
):
    """Deprecated compatibility wrapper for the old destructive function.

    It now creates real, non-destructive groups.  Call
    ``merge_models_by_name(..., delete_sources=1)`` for the old behavior.
    """
    try:
        print("merge_groups is deprecated; using non-destructive group_objects_by_name")
        return group_objects_by_name(
            selection=model_selection,
            method=methods,
            sep=sep,
            num=num,
            prefix=prefix or "",
            replace=replace,
        )
    except ValueError as exc:
        return _error_result(exc, "groups")


def register_commands(command_api=None) -> None:
    selected = cmd if command_api is None else command_api
    commands = {
        "group_objects_by_name": group_objects_by_name,
        "merge_models_by_name": merge_models_by_name,
        "align_in_groups": align_in_groups,
        "merge_groups": merge_groups,
    }
    for name, function in commands.items():
        extend_command(selected, name, function)


register_commands()
