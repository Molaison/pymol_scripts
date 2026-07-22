"""Non-destructive grouping helpers for loaded PyMOL molecule objects."""

from collections import defaultdict
import os
import sys
import traceback
from typing import Dict, Iterable, List, Tuple

from pymol import cmd

try:
    from pymolplugins.core.parameters import as_bool, as_int, sanitize_name
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.parameters import as_bool, as_int, sanitize_name  # type: ignore


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
        partitions = _partition_names(selection, str(method), str(sep), num)
        if not partitions:
            raise ValueError(f"No molecule objects found for selection '{selection}'")
        result = {}
        for key, members in partitions.items():
            group_name = sanitize_name(f"{prefix}{key}", fallback="group", max_length=120)
            if as_bool(replace) and group_name in (cmd.get_names("all") or []):
                cmd.delete(group_name)
            for member in members:
                cmd.group(group_name, member, action="add")
            result[group_name] = members
            print(f"Grouped {len(members)} objects into {group_name}")
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {}


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
        partitions = _partition_names(selection, str(method), str(sep), num)
        result = {}
        for key, members in partitions.items():
            if not members:
                continue
            reference = members[0]
            if as_bool(align):
                for mobile in members[1:]:
                    cmd.align(mobile, reference)
            merged_name = sanitize_name(f"{prefix}{key}", fallback="merged", max_length=120)
            cmd.create(merged_name, " or ".join(f"({name})" for name in members))
            if as_bool(delete_sources):
                for member in members:
                    cmd.delete(member)
            result[merged_name] = members
            print(
                f"Merged {len(members)} objects into {merged_name}; "
                f"delete_sources={as_bool(delete_sources)}"
            )
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {}


def align_in_groups(selection="all"):
    """Align molecule members within each selected PyMOL group."""
    try:
        group_names = [
            name for name in (cmd.get_names("all", 0, selection) or cmd.get_names("all") or [])
            if cmd.get_type(name) == "object:group"
        ]
        summary = {}
        for group_name in sorted(dict.fromkeys(group_names)):
            members = list(cmd.get_object_list(group_name) or [])
            if len(members) < 2:
                summary[group_name] = []
                continue
            reference = members[0]
            aligned = []
            for mobile in members[1:]:
                aligned.append((mobile, cmd.align(mobile, reference)))
            summary[group_name] = aligned
        return summary
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {}


def merge_groups(model_selection="all", prefix=None, methods="split_lastn_sep", **params):
    """Deprecated compatibility wrapper for the old destructive function.

    It now creates real, non-destructive groups.  Call
    ``merge_models_by_name(..., delete_sources=1)`` for the old behavior.
    """
    print("merge_groups is deprecated; using non-destructive group_objects_by_name")
    return group_objects_by_name(
        selection=model_selection,
        method=methods,
        prefix=prefix or "",
        **params,
    )


cmd.extend("group_objects_by_name", group_objects_by_name)
cmd.extend("merge_models_by_name", merge_models_by_name)
cmd.extend("align_in_groups", align_in_groups)
cmd.extend("merge_groups", merge_groups)
