"""Canonical file/object/group navigator for PyMOL 2.x.

Only names owned by the active navigation session are enabled, disabled, or
deleted.  The cursor lives in :class:`NavigationSession`; scene state is never
silently used as the source of truth.
"""

from dataclasses import dataclass, field
from difflib import get_close_matches
import hashlib
import json
import os
import re
from string import Formatter
import sys
import traceback
from collections.abc import MutableMapping
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from pymol import cmd
except ImportError:
    cmd = None  # type: ignore

try:
    from pymolplugins.core.parameters import (
        as_bool,
        as_int,
        extend_command,
        sanitize_name,
        split_names,
    )
    from pymolplugins.navigation.model import (
        NavigationEntry,
        NavigationError,
        NavigationSession,
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
    from pymolplugins.navigation.model import (  # type: ignore
        NavigationEntry,
        NavigationError,
        NavigationSession,
    )

from pymolplugins.targets.commands import _execute_checked as _execute_checked_command


STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
NAVIGATION_KEYS = ("right", "left", "CTRL-right", "CTRL-left")
_HOOK_FIELDS = frozenset(
    {
        "entry",
        "group",
        "objects",
        "object",
        "window",
        "window_entries",
        "index",
        "position",
        "batch",
        "batch_position",
        "count",
    }
)


def _named_bool(value: object, name: str) -> bool:
    """Parse one console boolean while retaining the public field name."""

    try:
        return as_bool(value)
    except ValueError as exc:
        raise NavigationError(f"{name}: {exc}") from None


def _one_line(value: object) -> str:
    return " ".join(str(value).split()).strip()


def _with_suggestions(message: str, query: str, candidates: Iterable[str]) -> str:
    """Append bounded, case-insensitive suggestions without auto-selecting."""

    canonical: Dict[str, str] = {}
    for candidate in candidates:
        text = str(candidate)
        canonical.setdefault(text.casefold(), text)
    matched = get_close_matches(
        str(query).casefold(), list(canonical), n=3, cutoff=0.55
    )
    if not matched:
        return message
    return message + "; did you mean: " + ", ".join(
        canonical[value] for value in matched
    )


def _summarize_names(names: Iterable[str], limit: int = 4) -> str:
    values = list(dict.fromkeys(str(name) for name in names if name))
    if not values:
        return "(none)"
    if len(values) <= limit:
        return ", ".join(values)
    return ", ".join(values[:limit]) + f", ... (+{len(values) - limit})"


def _error_response(
    command: str,
    exc: Exception,
    next_step: str,
    *,
    status: str = "error",
    include_traceback: bool = False,
    emit: bool = True,
    **fields: object,
) -> Dict[str, object]:
    """Print one actionable first line and return the matching result fields."""

    detail = _one_line(exc) or exc.__class__.__name__
    action = _one_line(next_step)
    punctuation = "" if detail.endswith((".", "!", "?")) else "."
    if emit:
        print(f"{command} error: {detail}{punctuation} Next: {action}")
    if include_traceback and emit:
        traceback.print_exc()
    result: Dict[str, object] = {"status": status, "error": str(exc)}
    result.update(fields)
    return result


def _validate_hook_template(template: str) -> None:
    """Reject unknown hook placeholders before registering scene work."""

    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise NavigationError(f"invalid hook template: {exc}") from None
    for _literal, field_name, format_spec, _conversion in parsed:
        if field_name is None:
            continue
        if not field_name:
            raise NavigationError("hook placeholders must be named")
        if field_name not in _HOOK_FIELDS and not re.fullmatch(
            r"object_\d+", field_name
        ):
            accepted = ", ".join(sorted(_HOOK_FIELDS))
            raise NavigationError(
                f"unknown hook placeholder {{{field_name}}}; use one of: {accepted}"
            )
        if "{" in format_spec or "}" in format_spec:
            raise NavigationError("nested hook format fields are not supported")


class NavigationStaleError(NavigationError):
    """Raised before mutation when a group session no longer matches the scene."""

    def __init__(self, changes: Dict[str, Dict[str, object]]):
        self.changes = changes
        groups = ", ".join(sorted(changes))
        super().__init__(
            "navigation group membership changed since start: %s; "
            "stop/restart the session before navigating" % groups
        )


class NavigationHookError(RuntimeError):
    """One checked navigation hook failed before the hook chain completed."""


class NavigationTransitionError(RuntimeError):
    """A scene transition failed, optionally together with its rollback."""

    def __init__(self, cause: Exception, rollback_error: Optional[Exception] = None):
        self.cause = cause
        self.rollback_error = rollback_error
        message = str(cause)
        if rollback_error is not None:
            message += "; navigation rollback also failed: %s" % rollback_error
        super().__init__(message)


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is not available in this Python process")
    return cmd


def _get_names(mode: str = "all", enabled_only: int = 0, selection: str = "") -> List[str]:
    api = _require_cmd()
    attempts = (
        lambda: api.get_names(mode, enabled_only, selection),
        lambda: api.get_names(mode, enabled_only=enabled_only, selection=selection),
        lambda: api.get_names(mode, enabled_only),
        lambda: api.get_names(mode),
    )
    for attempt in attempts:
        try:
            names = list(attempt() or [])
            if selection:
                return names
            return names
        except (TypeError, AttributeError):
            continue
    return []


def _object_type(name: str) -> str:
    try:
        return str(_require_cmd().get_type(name) or "")
    except Exception:
        return ""


def _is_group(name: str) -> bool:
    return _object_type(name) == "object:group"


def _is_leaf(name: str) -> bool:
    object_type = _object_type(name)
    return object_type.startswith("object:") and object_type != "object:group"


def _group_parent_snapshot() -> Dict[str, str]:
    """Read direct group parents from PyMOL's versioned session records.

    ``get_names(..., selection=group)`` is not a group-membership API: it only
    returns atom-bearing molecules/selections and therefore drops CGO, maps,
    measurements, and nested groups.  PyMOL 2.x stores the direct parent name
    at index 6 of every object record returned by a partial session.
    """
    api = _require_cmd()
    try:
        session = api.get_session("", partial=1, quiet=1)
    except TypeError:
        session = api.get_session("", 1, 1)
    except AttributeError as exc:
        raise NavigationError(
            "this PyMOL build cannot enumerate group membership (get_session missing)"
        ) from exc
    records = session.get("names") if isinstance(session, dict) else None
    if not isinstance(records, (list, tuple)):
        raise NavigationError("PyMOL partial session did not contain object name records")
    available = set(_get_names("all"))
    parents: Dict[str, str] = {}
    for record in records:
        if not isinstance(record, (list, tuple)) or len(record) < 7:
            continue
        name = str(record[0] or "")
        if name and name in available:
            parents[name] = str(record[6] or "")
    missing_groups = sorted(
        name for name in available if _is_group(name) and name not in parents
    )
    if missing_groups:
        raise NavigationError(
            "PyMOL session omitted group metadata for: " + ", ".join(missing_groups)
        )
    return parents


def _group_selected_names(group: str, parents: Dict[str, str]) -> List[str]:
    return [name for name, parent in parents.items() if parent == group]


def _expand_group(
    group: str,
    parents: Dict[str, str],
    active_groups: Optional[Set[str]] = None,
    seen_leaves: Optional[Set[str]] = None,
) -> List[str]:
    active_groups = set() if active_groups is None else active_groups
    seen_leaves = set() if seen_leaves is None else seen_leaves
    if group in active_groups:
        raise NavigationError(f"cyclic PyMOL group membership involving: {group}")
    active_groups.add(group)
    result: List[str] = []
    for name in _group_selected_names(group, parents):
        if _is_group(name):
            result.extend(_expand_group(name, parents, active_groups, seen_leaves))
        elif _is_leaf(name) and name not in seen_leaves:
            seen_leaves.add(name)
            result.append(name)
    active_groups.remove(group)
    return result


def _is_group_descendant(name: str, ancestor: str, parents: Dict[str, str]) -> bool:
    visited = set()
    parent = parents.get(name, "")
    while parent:
        if parent == ancestor:
            return True
        if parent in visited:
            raise NavigationError(f"cyclic PyMOL group ancestry involving: {parent}")
        visited.add(parent)
        parent = parents.get(parent, "")
    return False


def _discover_root_groups(
    explicit: Sequence[str], parents: Dict[str, str]
) -> List[str]:
    if explicit:
        groups = []
        available_groups = sorted(
            candidate for candidate in _get_names("all") if _is_group(candidate)
        )
        for name in explicit:
            if not _is_group(name):
                raise NavigationError(
                    _with_suggestions(
                        f"not a PyMOL group: {name}", name, available_groups
                    )
                )
            if name not in groups:
                groups.append(name)
        for outer in groups:
            nested = [
                inner
                for inner in groups
                if inner != outer and _is_group_descendant(inner, outer, parents)
            ]
            if nested:
                raise NavigationError(
                    f"parent and nested group cannot both be entries: {outer}, {', '.join(nested)}"
                )
        return groups

    all_groups = sorted(name for name in _get_names("all") if _is_group(name))
    return [
        group
        for group in all_groups
        if not any(_is_group_descendant(group, outer, parents) for outer in all_groups if outer != group)
    ]


def _file_object_name(path: str, index: int) -> str:
    basename = os.path.basename(path)
    lowered = basename.lower()
    for suffix in (".pdb.gz", ".cif.gz", ".pdb", ".cif"):
        if lowered.endswith(suffix):
            basename = basename[: -len(suffix)]
            break
    safe = sanitize_name(basename, fallback="structure", max_length=45)
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"ppnav_{index + 1:05d}_{safe}_{digest}"


def _build_entries(source: str, entries: object, path: str, pattern: str) -> List[NavigationEntry]:
    explicit = split_names(entries)
    if source == "groups":
        parents = _group_parent_snapshot()
        result = []
        for index, group in enumerate(_discover_root_groups(explicit, parents)):
            leaves = tuple(_expand_group(group, parents))
            if not leaves:
                print(f"Navigation warning: skipping empty group '{group}'")
                continue
            result.append(
                NavigationEntry(
                    id=f"group:{group}",
                    kind="group",
                    display_name=group,
                    object_names=leaves,
                    group_name=group,
                    metadata={"index": index},
                )
            )
        return result
    if source == "objects":
        names = explicit or sorted(name for name in _get_names("all") if _is_leaf(name))
        available_leaves = sorted(name for name in _get_names("all") if _is_leaf(name))
        result = []
        for name in names:
            if not _is_leaf(name):
                raise NavigationError(
                    _with_suggestions(
                        f"not a leaf PyMOL object: {name}", name, available_leaves
                    )
                )
            result.append(
                NavigationEntry(
                    id=f"object:{name}",
                    kind="object",
                    display_name=name,
                    object_names=(name,),
                )
            )
        return result
    if source == "files":
        dataset = os.path.abspath(os.path.expanduser(path or "."))
        if not os.path.isdir(dataset):
            raise NavigationError(f"dataset directory does not exist: {dataset}")
        if entries:
            if isinstance(entries, str):
                tokens = [
                    token.strip()
                    for token in re.split(r"[,\r\n]+", entries)
                    if token.strip()
                ]
            else:
                tokens = split_names(entries)
            paths = [
                os.path.abspath(
                    os.path.expanduser(
                        token if os.path.isabs(token) else os.path.join(dataset, token)
                    )
                )
                for token in tokens
            ]
            missing = [filename for filename in paths if not os.path.isfile(filename)]
            if missing:
                raise NavigationError("file entries do not exist: " + ", ".join(missing))
        else:
            try:
                compiled = re.compile(pattern or STRUCTURE_PATTERN)
            except re.error as exc:
                raise NavigationError(f"invalid file regex: {exc}") from exc
            paths = [
                os.path.join(dataset, name)
                for name in sorted(os.listdir(dataset))
                if compiled.match(name) and os.path.isfile(os.path.join(dataset, name))
            ]
        return [
            NavigationEntry(
                id=f"file:{filename}",
                kind="file",
                display_name=os.path.basename(filename),
                source_paths=(filename,),
                object_names=(_file_object_name(filename, index),),
            )
            for index, filename in enumerate(paths)
        ]
    raise NavigationError("source must be files, objects, or groups")


@dataclass
class NavigationController:
    source: str
    session: NavigationSession
    zoom_policy: str = "current"
    managed_names: Set[str] = field(default_factory=set)
    retired_file_names: Set[str] = field(default_factory=set)
    loaded_entry_names: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    original_enabled: Dict[str, bool] = field(default_factory=dict)
    hooks: List[Tuple[str, int]] = field(default_factory=list)
    keys_bound: bool = False
    previous_key_mappings: Dict[str, Tuple[bool, object]] = field(default_factory=dict)
    installed_key_mappings: Dict[str, object] = field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str = ""
    degraded_errors: Dict[str, List[str]] = field(default_factory=dict)
    membership_changes: Dict[str, Dict[str, object]] = field(default_factory=dict)
    # During an explicit session replacement, already-loaded files owned by the
    # previous controller can be adopted by the new controller.  The source-path
    # check prevents an unrelated pre-existing object with the same generated
    # name from being claimed silently.
    adoptable_file_sources: Dict[str, str] = field(default_factory=dict, repr=False)
    adopted_names: Set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if self.zoom_policy not in {"current", "current_and_pins", "none"}:
            raise NavigationError("zoom_policy must be current, current_and_pins, or none")
        if self.source != "files":
            for entry in self.session.entries:
                self.managed_names.update(entry.object_names)
                self.loaded_entry_names[entry.id] = entry.object_names
        enabled = set(_get_names("all", 1))
        for name in self.managed_names:
            self.original_enabled[name] = name in enabled

    def _entry_names(self, entry: NavigationEntry) -> Tuple[str, ...]:
        return self.loaded_entry_names.get(entry.id, entry.object_names)

    def _load_file_entry(self, entry: NavigationEntry) -> Tuple[str, ...]:
        if entry.id in self.loaded_entry_names:
            return self.loaded_entry_names[entry.id]
        if len(entry.source_paths) != 1 or len(entry.object_names) != 1:
            raise NavigationError(f"invalid file entry: {entry.id}")
        source_path = entry.source_paths[0]
        name = entry.object_names[0]
        existing = set(_get_names("all"))
        if name in existing and name not in self.managed_names:
            adoptable_source = self.adoptable_file_sources.get(name)
            if not adoptable_source or os.path.normcase(
                os.path.abspath(adoptable_source)
            ) != os.path.normcase(os.path.abspath(source_path)):
                raise NavigationError(
                    f"generated object name collides with existing object: {name}"
                )
            self.adopted_names.add(name)
        elif name not in existing:
            _require_cmd().load(source_path, name, quiet=1)
        self.managed_names.add(name)
        self.loaded_entry_names[entry.id] = (name,)
        return (name,)

    def ensure_resident(self) -> None:
        if self.source != "files":
            return
        keep_entries = list(self.session.resident_entries())
        for entry in self.session.pinned_entries():
            if entry not in keep_entries:
                keep_entries.append(entry)
        newly_loaded: List[str] = []
        try:
            for entry in keep_entries:
                if entry.id not in self.loaded_entry_names:
                    resolved = self._load_file_entry(entry)
                    newly_loaded.extend(
                        name for name in resolved if name not in self.adopted_names
                    )
        except Exception as load_exc:
            cleanup_failures: List[str] = []
            for name in newly_loaded:
                delete_error: Optional[Exception] = None
                try:
                    _require_cmd().delete(name)
                except Exception as exc:
                    delete_error = exc
                if name in set(_get_names("all")):
                    cleanup_failures.append(
                        "%s: %s"
                        % (
                            name,
                            delete_error
                            or "object still exists after cmd.delete",
                        )
                    )
                    # Retain both ownership and entry mapping.  A transition
                    # rollback or later pp_nav_stop must still know this name.
                    continue
                self._forget_managed_name(name, loaded=True)
            if cleanup_failures:
                raise NavigationError(
                    "%s; file-load cleanup failed: %s"
                    % (load_exc, "; ".join(cleanup_failures))
                ) from load_exc
            raise

        keep_ids = {entry.id for entry in keep_entries}
        for entry_id, names in list(self.loaded_entry_names.items()):
            if entry_id in keep_ids:
                continue
            for name in names:
                _require_cmd().delete(name)
                self.managed_names.discard(name)
            self.loaded_entry_names.pop(entry_id, None)

    def _group_membership_changes(self) -> Dict[str, Dict[str, object]]:
        if self.source != "groups":
            return {}
        parents = _group_parent_snapshot()
        changes: Dict[str, Dict[str, object]] = {}
        for entry in self.session.entries:
            group = entry.group_name
            expected = set(entry.object_names)
            group_missing = not _is_group(group)
            current = set() if group_missing else set(_expand_group(group, parents))
            added = sorted(current.difference(expected))
            removed = sorted(expected.difference(current))
            if group_missing or added or removed:
                changes[group] = {
                    "added": added,
                    "removed": removed,
                    "group_missing": group_missing,
                }
        return changes

    def validate_runtime(self) -> None:
        """Refuse transitions from degraded or stale controller state."""

        if self.degraded:
            raise NavigationError(
                "navigation session is degraded after a failed recovery; "
                "retry pp_nav_stop or use pp_nav_stop force=1"
            )
        changes = self._group_membership_changes()
        self.membership_changes = changes
        if changes:
            raise NavigationStaleError(changes)

    def apply_scene(
        self,
        *,
        run_hooks: bool = True,
        validate_runtime: bool = True,
        allow_degraded: bool = False,
    ) -> Dict[str, object]:
        if validate_runtime:
            self.validate_runtime()
        elif self.degraded and not allow_degraded:
            raise NavigationError(
                "navigation session is degraded after a failed recovery"
            )
        self.ensure_resident()
        for name in sorted(self.managed_names.union(self.retired_file_names)):
            if name in _get_names("all"):
                _require_cmd().disable(name)
        visible_names: List[str] = []
        for entry in self.session.visible_entries():
            for name in self._entry_names(entry):
                if name not in visible_names:
                    visible_names.append(name)
                _require_cmd().enable(name)
        existing_after = set(_get_names("all"))
        self.retired_file_names.intersection_update(existing_after)
        missing = sorted(self.managed_names.difference(existing_after))
        if missing:
            raise NavigationError(
                "managed navigation objects disappeared during scene apply: "
                + ", ".join(missing)
            )
        enabled_after = set(_get_names("all", 1))
        expected_enabled = set(visible_names)
        mismatched = sorted(
            name
            for name in self.managed_names
            if (name in enabled_after) != (name in expected_enabled)
        )
        if mismatched:
            raise NavigationError(
                "PyMOL did not apply managed navigation visibility for: "
                + ", ".join(mismatched)
            )
        visible_retired = sorted(
            self.retired_file_names.intersection(enabled_after)
        )
        if visible_retired:
            raise NavigationError(
                "retired navigation file objects could not be hidden: "
                + ", ".join(visible_retired)
            )
        if run_hooks:
            self._run_hooks()
        self._zoom()
        status = self.status()
        current_labels = [
            entry.display_name for entry in self.session.current_entries()
        ]
        print(
            f"Navigation: {status['position']}/{status['count']} | "
            f"batch {status['batch_position']}/{status['batch_count']} | "
            f"current={_summarize_names(current_labels)} | "
            f"visible_objects={len(status['visible_names'])} | "
            f"pins={len(status['pinned_entry_ids'])}"
        )
        return status

    def _run_hooks(self) -> None:
        if not self.hooks:
            return
        current = self.session.entries[self.session.cursor]
        names = self._entry_names(current)
        window_entries = self.session.current_entries()
        window_names = [
            name for entry in window_entries for name in self._entry_names(entry)
        ]
        context = {
            "entry": current.display_name,
            "group": current.group_name,
            "objects": " or ".join(names),
            "object": names[0] if names else "",
            "window": " or ".join(window_names),
            "window_entries": ",".join(entry.display_name for entry in window_entries),
            "index": self.session.cursor,
            "position": self.session.cursor + 1,
            "batch": self.session.batch_index,
            "batch_position": self.session.batch_index + 1,
            "count": self.session.count,
        }
        context.update(
            {f"object_{index}": name for index, name in enumerate(window_names)}
        )
        for hook_index, (template, offset) in enumerate(self.hooks, start=1):
            try:
                hook_context = dict(context)
                if window_names:
                    hook_context["object"] = window_names[
                        min(max(offset, 0), len(window_names) - 1)
                    ]
                rendered = template.format(**hook_context)
                _execute_checked_command(_require_cmd(), rendered)
            except Exception as exc:
                raise NavigationHookError(
                    "navigation hook %d failed %r: %s"
                    % (hook_index, template, exc)
                ) from exc

    def _zoom(self) -> None:
        if self.zoom_policy == "none":
            return
        entries = self.session.current_entries()
        if self.zoom_policy == "current_and_pins":
            entries = self.session.visible_entries()
        names: List[str] = []
        for entry in entries:
            names.extend(name for name in self._entry_names(entry) if _object_type(name) == "object:molecule")
        if not names:
            return
        try:
            _require_cmd().zoom(" or ".join(f"({name})" for name in dict.fromkeys(names)))
        except Exception as exc:
            print(f"Navigation zoom warning: {exc}")

    def transition(self, method: str, *args) -> Dict[str, object]:
        self.validate_runtime()
        previous_cursor = self.session.cursor
        previous_pins = set(self.session.pinned_entry_ids)
        try:
            getattr(self.session, method)(*args)
        except Exception:
            self.session.cursor = previous_cursor
            self.session.pinned_entry_ids = previous_pins
            raise

        changed = (
            self.session.cursor != previous_cursor
            or self.session.pinned_entry_ids != previous_pins
        )
        if not changed:
            status = self.status()
            outcome = (
                "boundary"
                if method in {"next", "previous", "next_batch", "previous_batch"}
                else "no_change"
            )
            status.update(
                {
                    "status": outcome,
                    "no_change": True,
                    "transition": method,
                }
            )
            print(
                "Navigation: %s made no change at position %s/%s"
                % (method, status["position"], status["count"])
            )
            return status

        try:
            return self.apply_scene(validate_runtime=False)
        except Exception as exc:
            self.session.cursor = previous_cursor
            self.session.pinned_entry_ids = previous_pins
            rollback_error: Optional[Exception] = None
            try:
                self.apply_scene(
                    run_hooks=False,
                    validate_runtime=False,
                    allow_degraded=True,
                )
            except Exception as rollback_exc:
                rollback_error = rollback_exc
                self.degraded = True
                self.degraded_reason = "transition_rollback_failed"
                self.degraded_errors = {
                    "transition": [str(exc)],
                    "rollback": [str(rollback_exc)],
                }
            raise NavigationTransitionError(exc, rollback_error) from exc

    def status(self) -> Dict[str, object]:
        status = self.session.status()
        status.update(
            {
                "source": self.source,
                "zoom_policy": self.zoom_policy,
                "managed_names": sorted(self.managed_names),
                "cleanup_pending": bool(self.retired_file_names),
                "retired_file_names": sorted(self.retired_file_names),
                "visible_names": [
                    name
                    for entry in self.session.visible_entries()
                    for name in self._entry_names(entry)
                ],
                "hooks": [
                    {"command": command, "offset": offset}
                    for command, offset in self.hooks
                ],
                "keys_bound": self.keys_bound,
                "degraded": self.degraded,
                "degraded_reason": self.degraded_reason,
                "degraded_errors": {
                    key: list(values) for key, values in self.degraded_errors.items()
                },
                "membership_stale": bool(self.membership_changes),
                "membership_changes": {
                    group: dict(change)
                    for group, change in self.membership_changes.items()
                },
            }
        )
        return status

    def _forget_managed_name(self, name: str, *, loaded: bool) -> None:
        self.managed_names.discard(name)
        self.original_enabled.pop(name, None)
        if not loaded:
            return
        for entry_id, names in list(self.loaded_entry_names.items()):
            remaining = tuple(value for value in names if value != name)
            if remaining:
                self.loaded_entry_names[entry_id] = remaining
            else:
                self.loaded_entry_names.pop(entry_id, None)

    def stop(self, keep_loaded: bool = False) -> Dict[str, List[str]]:
        """Best-effort ownership release, retaining failures for a later retry."""

        errors: Dict[str, List[str]] = {"cleanup": [], "visibility": []}
        attempted_retired: Dict[str, Exception] = {}
        existing_before = set(_get_names("all"))
        for name in sorted(self.retired_file_names.intersection(existing_before)):
            try:
                _require_cmd().delete(name)
            except Exception as exc:
                attempted_retired[name] = exc
        existing_after = set(_get_names("all"))
        for name in sorted(tuple(self.retired_file_names)):
            if name not in existing_after:
                self.retired_file_names.discard(name)
                continue
            errors["cleanup"].append(
                "%s: %s"
                % (
                    name,
                    attempted_retired.get(name)
                    or "retired object still exists after cmd.delete",
                )
            )
        if self.source == "files" and not keep_loaded:
            attempted: Dict[str, Exception] = {}
            existing_before = set(_get_names("all"))
            for name in sorted(self.managed_names.intersection(existing_before)):
                try:
                    _require_cmd().delete(name)
                except Exception as exc:
                    attempted[name] = exc
            existing_after = set(_get_names("all"))
            for name in sorted(tuple(self.managed_names)):
                if name not in existing_after:
                    self._forget_managed_name(name, loaded=True)
                    continue
                detail = attempted.get(name)
                errors["cleanup"].append(
                    "%s: %s"
                    % (
                        name,
                        detail or "object still exists after cmd.delete",
                    )
                )
        elif self.source == "files":
            for name in tuple(self.managed_names):
                self._forget_managed_name(name, loaded=True)
        else:
            attempted_visibility: Dict[str, Exception] = {}
            existing_before = set(_get_names("all"))
            for name in sorted(self.managed_names.intersection(existing_before)):
                was_enabled = bool(self.original_enabled.get(name, False))
                try:
                    (_require_cmd().enable if was_enabled else _require_cmd().disable)(
                        name
                    )
                except Exception as exc:
                    attempted_visibility[name] = exc
            existing_after = set(_get_names("all"))
            enabled_after = set(_get_names("all", 1))
            for name in sorted(tuple(self.managed_names)):
                if name not in existing_after:
                    self._forget_managed_name(name, loaded=False)
                    continue
                was_enabled = bool(self.original_enabled.get(name, False))
                if (name in enabled_after) == was_enabled:
                    self._forget_managed_name(name, loaded=False)
                    continue
                detail = attempted_visibility.get(name)
                errors["visibility"].append(
                    "%s: %s"
                    % (
                        name,
                        detail
                        or "visibility did not change to %s"
                        % ("enabled" if was_enabled else "disabled"),
                    )
                )
        if not self.managed_names and not self.retired_file_names:
            self.loaded_entry_names.clear()
            self.original_enabled.clear()
        return errors


_CONTROLLER: Optional[NavigationController] = None
_LEGACY_SWITCH_COMMANDS: List[Tuple[str, int]] = globals().get(
    "_LEGACY_SWITCH_COMMANDS", []
)


@dataclass
class _ReplacementSnapshot:
    controller: NavigationController
    enabled_state: Dict[str, bool]
    owned_names: Set[str]
    owned_file_sources: Dict[str, str]
    keys_bound: bool
    actual_key_mappings: Dict[str, Tuple[bool, object]]
    previous_key_mappings: Dict[str, Tuple[bool, object]]
    installed_key_mappings: Dict[str, object]


def _controller() -> NavigationController:
    if _CONTROLLER is None:
        raise NavigationError("no navigation session is active")
    return _CONTROLLER


def _bind_keys(controller: NavigationController) -> None:
    api = _require_cmd()
    mappings = getattr(api, "key_mappings", None)
    if not isinstance(mappings, MutableMapping):
        raise RuntimeError(
            "this PyMOL build does not expose cmd.key_mappings; refusing to "
            "overwrite shortcuts that cannot be restored"
        )
    callbacks = {
        "right": pp_nav_next,
        "left": pp_nav_prev,
        "CTRL-right": pp_nav_next_batch,
        "CTRL-left": pp_nav_prev_batch,
    }
    controller.previous_key_mappings = {
        key: (key in mappings, mappings.get(key)) for key in NAVIGATION_KEYS
    }
    try:
        for key, callback in callbacks.items():
            api.set_key(key, callback)
            controller.installed_key_mappings[key] = mappings.get(key)
    except Exception:
        _restore_keys(controller, preserve_external=False)
        raise
    controller.keys_bound = True


def _restore_keys(
    controller: NavigationController, *, preserve_external: bool = True
) -> None:
    """Restore the exact mappings captured before navigation started."""
    api = _require_cmd()
    mappings = getattr(api, "key_mappings", None)
    if not isinstance(mappings, MutableMapping):
        raise RuntimeError("cmd.key_mappings disappeared; shortcuts cannot be restored")
    for key, (existed, previous) in controller.previous_key_mappings.items():
        installed = controller.installed_key_mappings.get(key)
        if preserve_external and mappings.get(key) != installed:
            print(
                f"Navigation shortcut '{key}' changed outside this session; "
                "leaving the newer binding untouched"
            )
            continue
        if existed:
            mappings[key] = previous
        else:
            mappings.pop(key, None)
    controller.previous_key_mappings.clear()
    controller.installed_key_mappings.clear()


def _unbind_keys(controller: NavigationController) -> None:
    if not controller.keys_bound:
        return
    _restore_keys(controller)
    controller.keys_bound = False


def _file_sources(controller: NavigationController) -> Dict[str, str]:
    """Return exact loaded-name -> source-path ownership for a file session."""
    if controller.source != "files":
        return {}
    result: Dict[str, str] = {}
    for entry in controller.session.entries:
        names = controller.loaded_entry_names.get(entry.id, ())
        if len(entry.source_paths) != 1:
            continue
        for name in names:
            result[name] = entry.source_paths[0]
    return result


def _capture_key_mappings() -> Dict[str, Tuple[bool, object]]:
    mappings = getattr(_require_cmd(), "key_mappings", None)
    if not isinstance(mappings, MutableMapping):
        return {}
    return {key: (key in mappings, mappings.get(key)) for key in NAVIGATION_KEYS}


def _restore_key_mappings(
    state: Dict[str, Tuple[bool, object]],
) -> None:
    if not state:
        return
    mappings = getattr(_require_cmd(), "key_mappings", None)
    if not isinstance(mappings, MutableMapping):
        raise RuntimeError("cmd.key_mappings disappeared during session replacement")
    for key, (existed, value) in state.items():
        if existed:
            mappings[key] = value
        else:
            mappings.pop(key, None)


def _set_enabled_state(state: Dict[str, bool]) -> None:
    existing = set(_get_names("all"))
    missing = sorted(set(state).difference(existing))
    if missing:
        raise RuntimeError(
            "replacement rollback cannot restore missing objects: " + ", ".join(missing)
        )
    for name, enabled in state.items():
        (_require_cmd().enable if enabled else _require_cmd().disable)(name)


def _suspend_for_replacement(
    controller: NavigationController,
) -> _ReplacementSnapshot:
    """Temporarily remove one controller's scene/key effects without deletion."""
    enabled = set(_get_names("all", 1))
    snapshot = _ReplacementSnapshot(
        controller=controller,
        enabled_state={name: name in enabled for name in controller.managed_names},
        owned_names=set(controller.managed_names),
        owned_file_sources=_file_sources(controller),
        keys_bound=controller.keys_bound,
        actual_key_mappings=_capture_key_mappings(),
        previous_key_mappings=dict(controller.previous_key_mappings),
        installed_key_mappings=dict(controller.installed_key_mappings),
    )
    try:
        _unbind_keys(controller)
        if controller.source == "files":
            for name in sorted(snapshot.owned_names):
                if name in set(_get_names("all")):
                    _require_cmd().disable(name)
        else:
            _set_enabled_state(dict(controller.original_enabled))
        return snapshot
    except Exception:
        # Suspension itself is transactional too.  Restore exact mappings rather
        # than acquiring a fresh lease, which could overwrite a third-party
        # binding that the old lease had intentionally preserved.
        _set_enabled_state(snapshot.enabled_state)
        _restore_key_mappings(snapshot.actual_key_mappings)
        controller.keys_bound = snapshot.keys_bound
        controller.previous_key_mappings = dict(snapshot.previous_key_mappings)
        controller.installed_key_mappings = dict(snapshot.installed_key_mappings)
        raise


def _restore_replaced_controller(snapshot: _ReplacementSnapshot) -> None:
    """Restore the exact old scene and lease after a failed first apply."""
    _set_enabled_state(snapshot.enabled_state)
    _restore_key_mappings(snapshot.actual_key_mappings)
    controller = snapshot.controller
    controller.keys_bound = snapshot.keys_bound
    controller.previous_key_mappings = dict(snapshot.previous_key_mappings)
    controller.installed_key_mappings = dict(snapshot.installed_key_mappings)


def _discard_failed_controller(
    controller: NavigationController, protected_names: Set[str]
) -> None:
    """Remove only outputs made by a failed replacement attempt."""
    _unbind_keys(controller)
    if controller.source != "files":
        stopped = controller.stop(keep_loaded=False)
        failures = stopped["cleanup"] + stopped["visibility"]
        if failures:
            raise RuntimeError("; ".join(failures))
        return
    for name in sorted(controller.managed_names.difference(protected_names)):
        if name in set(_get_names("all")):
            _require_cmd().delete(name)
    controller.managed_names.clear()
    controller.loaded_entry_names.clear()
    controller.adopted_names.clear()


def _finalize_replacement(
    snapshot: _ReplacementSnapshot, controller: NavigationController
) -> Dict[str, str]:
    """Transfer shared file ownership and retire unused old file objects."""
    old = snapshot.controller
    failures: Dict[str, str] = {}
    if old.source == "files":
        retained = set(controller.managed_names)
        attempted: Dict[str, Exception] = {}
        for name in sorted(snapshot.owned_names.difference(retained)):
            try:
                if name in set(_get_names("all")):
                    _require_cmd().delete(name)
            except Exception as exc:
                attempted[name] = exc
        remaining = sorted(
            snapshot.owned_names.difference(retained).intersection(
                set(_get_names("all"))
            )
        )
        if remaining:
            controller.retired_file_names.update(remaining)
            failures = {
                name: str(
                    attempted.get(name)
                    or "object still exists after cmd.delete"
                )
                for name in remaining
            }
    old.managed_names.clear()
    old.loaded_entry_names.clear()
    controller.adoptable_file_sources.clear()
    controller.adopted_names.clear()
    return failures


def pp_nav_start(
    source: str = "groups",
    entries: object = "",
    path: str = ".",
    pattern: str = STRUCTURE_PATTERN,
    batch_size: object = 20,
    window_size: object = 1,
    stride: object = 1,
    wrap: object = 1,
    bind_keys: object = 0,
    zoom_policy: str = "current",
    replace: object = 0,
) -> Dict[str, object]:
    """Start a file, object, or root-group navigation session."""
    global _CONTROLLER
    previous_controller = _CONTROLLER
    controller: Optional[NavigationController] = None
    replacement: Optional[_ReplacementSnapshot] = None
    mutation_started = False
    try:
        replace_value = _named_bool(replace, "replace")
        bind_value = _named_bool(bind_keys, "bind_keys")
        wrap_value = _named_bool(wrap, "wrap")
        zoom_value = str(zoom_policy).strip().lower()
        if zoom_value not in {"current", "current_and_pins", "none"}:
            raise NavigationError(
                "zoom_policy must be current, current_and_pins, or none"
            )
        if previous_controller is not None and not replace_value:
            raise NavigationError(
                "a navigation session is already active; run pp_nav_stop first "
                "or pass replace=1 explicitly"
            )
        canonical_source = str(source).strip().lower()
        built = _build_entries(canonical_source, entries, path, pattern)
        if not built:
            raise NavigationError(f"no {canonical_source} navigation entries found")
        session = NavigationSession(
            entries=built,
            batch_size=as_int(batch_size, minimum=1, name="batch_size"),
            window_size=as_int(window_size, minimum=1, name="window_size"),
            stride=as_int(stride, minimum=1, name="stride"),
            wrap=wrap_value,
        )
        # All discovery and state-model validation happens before an explicitly
        # requested replacement may suspend the active session.  Suspension is
        # non-destructive: the old controller and its file objects remain
        # available until the new controller completes its first apply.
        if previous_controller is not None:
            mutation_started = True
            replacement = _suspend_for_replacement(previous_controller)
        controller = NavigationController(
            source=canonical_source,
            session=session,
            zoom_policy=zoom_value,
            adoptable_file_sources=(
                dict(replacement.owned_file_sources) if replacement else {}
            ),
        )
        mutation_started = True
        _CONTROLLER = controller
        if bind_value:
            _bind_keys(controller)
        result = controller.apply_scene()
        if replacement is not None:
            retirement_failures = _finalize_replacement(replacement, controller)
            if retirement_failures:
                result.update(
                    {
                        "cleanup_pending": True,
                        "retired_file_names": sorted(retirement_failures),
                        "cleanup_errors": retirement_failures,
                        "cleanup_next": (
                            "run pp_nav_stop to retry deletion of retired file objects"
                        ),
                    }
                )
                print(
                    "Navigation replacement active; cleanup pending for %s. "
                    "Next: run pp_nav_stop to retry"
                    % _summarize_names(sorted(retirement_failures))
                )
        return result
    except Exception as exc:
        cleanup_error: Optional[Exception] = None
        if controller is not None:
            try:
                _discard_failed_controller(
                    controller,
                    replacement.owned_names if replacement is not None else set(),
                )
            except Exception as cleanup_exc:
                cleanup_error = cleanup_exc
                traceback.print_exc()
        if replacement is not None:
            _CONTROLLER = replacement.controller
            try:
                _restore_replaced_controller(replacement)
            except Exception as rollback_exc:
                failure_fields: Dict[str, object] = {
                    "rollback_error": str(rollback_exc),
                    "scene_preserved": False,
                }
                if cleanup_error is not None:
                    failure_fields["cleanup_error"] = str(cleanup_error)
                return _error_response(
                    "pp_nav_start",
                    exc,
                    "run pp_nav_stop force=1 only if the old session cannot be repaired",
                    include_traceback=True,
                    **failure_fields,
                )
        elif controller is not None and _CONTROLLER is controller:
            if cleanup_error is None:
                _CONTROLLER = None
            else:
                # Keep retryable ownership instead of forgetting objects or a
                # shortcut lease that the failed start could not release.
                controller.degraded = True
                controller.degraded_reason = "start_cleanup_failed"
                controller.degraded_errors = {
                    "cleanup": [str(cleanup_error)]
                }
        expected_input = (
            not mutation_started and isinstance(exc, (NavigationError, ValueError))
        )
        retryable_cleanup = cleanup_error is not None and replacement is None
        failure_fields = {
            "scene_preserved": True if expected_input else cleanup_error is None
        }
        if cleanup_error is not None:
            failure_fields["cleanup_error"] = str(cleanup_error)
        return _error_response(
            "pp_nav_start",
            exc,
            (
                "correct the reported option and rerun pp_nav_start; the prior "
                "navigation state was left unchanged"
                if expected_input
                else (
                    "run pp_nav_stop to retry cleanup; use force=1 only to abandon ownership"
                    if retryable_cleanup
                    else (
                        "inspect cleanup_error and remove the reported replacement outputs before retrying"
                        if cleanup_error is not None
                        else "inspect the traceback, fix the PyMOL operation, and retry"
                    )
                )
            ),
            include_traceback=not expected_input,
            degraded=retryable_cleanup,
            **failure_fields,
        )


def _command_transition(method: str, *args) -> Dict[str, object]:
    command_name = {
        "next": "pp_nav_next",
        "previous": "pp_nav_prev",
        "next_batch": "pp_nav_next_batch",
        "previous_batch": "pp_nav_prev_batch",
        "goto": "pp_nav_goto",
        "pin": "pp_nav_pin",
        "unpin": "pp_nav_unpin",
        "clear_pins": "pp_nav_clear_pins",
    }.get(method, "pp_nav")
    try:
        return _controller().transition(method, *args)
    except NavigationStaleError as exc:
        controller = _controller()
        result = controller.status()
        result.update(
            _error_response(
                command_name,
                exc,
                "run pp_nav_stop, then restart navigation to accept the new group membership",
                status="stale",
                membership_changes=exc.changes,
                scene_preserved=True,
            )
        )
        return result
    except NavigationTransitionError as exc:
        controller = _controller()
        result = controller.status()
        scene_preserved = exc.rollback_error is None
        result.update(
            _error_response(
                command_name,
                exc.cause,
                (
                    "repair the reported hook/operation and retry"
                    if scene_preserved
                    else "run pp_nav_stop to retry cleanup; use force=1 only to abandon ownership"
                ),
                include_traceback=(
                    exc.rollback_error is not None
                    or not isinstance(exc.cause, NavigationHookError)
                ),
                rollback=("restored" if scene_preserved else "failed"),
                scene_preserved=scene_preserved,
            )
        )
        if isinstance(exc.cause, NavigationHookError):
            result.update(
                {
                    "hook_policy": "stop",
                    "rollback_scope": "navigation_scene_only",
                    "side_effect_warning": (
                        "hook mutations completed before the failure are not "
                        "generically reversible"
                    ),
                }
            )
        if exc.rollback_error is not None:
            result["rollback_error"] = str(exc.rollback_error)
        return result
    except NavigationError as exc:
        result = _CONTROLLER.status() if _CONTROLLER is not None else {}
        result.update(
            _error_response(
                command_name,
                exc,
                (
                    "run pp_nav_start first"
                    if _CONTROLLER is None
                    else "correct the request or run pp_nav_stop to recover the active session"
                ),
                scene_preserved=True,
            )
        )
        return result
    except Exception as exc:
        return _error_response(
            command_name,
            exc,
            "inspect the traceback before retrying or stopping the session",
            include_traceback=True,
            scene_preserved=False,
        )


def pp_nav_next() -> Dict[str, object]:
    return _command_transition("next")


def pp_nav_prev() -> Dict[str, object]:
    return _command_transition("previous")


def pp_nav_next_batch() -> Dict[str, object]:
    return _command_transition("next_batch")


def pp_nav_prev_batch() -> Dict[str, object]:
    return _command_transition("previous_batch")


def pp_nav_goto(index: object) -> Dict[str, object]:
    try:
        numeric = as_int(index, minimum=1, name="index") - 1
    except ValueError as exc:
        return _error_response(
            "pp_nav_goto",
            exc,
            "use a one-based integer position shown by pp_nav_status",
            scene_preserved=True,
        )
    return _command_transition("goto", numeric)


def _resolve_entry_id(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    candidates: List[str] = []
    for entry in _controller().session.entries:
        candidates.extend(
            alias
            for alias in (entry.id, entry.display_name, entry.group_name, *entry.object_names)
            if alias
        )
        if text in {entry.id, entry.display_name, entry.group_name} or text in entry.object_names:
            return entry.id
    raise NavigationError(
        _with_suggestions(
            f"unknown navigation entry: {text}", text, candidates
        )
    )


def pp_nav_pin(entry: object = "") -> Dict[str, object]:
    try:
        entry_id = _resolve_entry_id(entry)
    except NavigationError as exc:
        return _error_response(
            "pp_nav_pin",
            exc,
            "use an entry, group, or object name listed by pp_nav_status",
            scene_preserved=True,
        )
    except Exception as exc:
        return _error_response(
            "pp_nav_pin",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            scene_preserved=False,
        )
    return _command_transition("pin", entry_id)


def pp_nav_unpin(entry: object = "") -> Dict[str, object]:
    try:
        entry_id = _resolve_entry_id(entry)
    except NavigationError as exc:
        return _error_response(
            "pp_nav_unpin",
            exc,
            "use a pinned entry listed by pp_nav_status",
            scene_preserved=True,
        )
    except Exception as exc:
        return _error_response(
            "pp_nav_unpin",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            scene_preserved=False,
        )
    return _command_transition("unpin", entry_id)


def pp_nav_register_hook(command: str, offset: object = 0) -> Dict[str, object]:
    try:
        template = str(command).strip()
        if not template:
            raise NavigationError("command is required")
        _validate_hook_template(template)
        entry = (template, as_int(offset, minimum=0, name="offset"))
        controller = _controller()
        if entry in controller.hooks:
            result = controller.status()
            result.update(
                {
                    "status": "no_change",
                    "no_change": True,
                    "hook": {"command": template, "offset": entry[1]},
                }
            )
            print("Navigation hook already registered; no change")
            return result
        controller.hooks.append(entry)
        result = controller.status()
        result.update(
            {
                "status": "registered",
                "hook": {"command": template, "offset": entry[1]},
            }
        )
        print(f"Navigation hook registered: {template!r} (offset={entry[1]})")
        return result
    except (NavigationError, ValueError) as exc:
        return _error_response(
            "pp_nav_register_hook",
            exc,
            "use a non-empty command and a documented placeholder such as {object}",
            scene_preserved=True,
        )
    except Exception as exc:
        return _error_response(
            "pp_nav_register_hook",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            scene_preserved=False,
        )


def pp_nav_unregister_hook(command: str, offset: object = "") -> Dict[str, object]:
    try:
        template = str(command).strip()
        if not template:
            raise NavigationError("command is required")
        controller = _controller()
        previous = list(controller.hooks)
        if str(offset).strip() == "":
            controller.hooks = [entry for entry in controller.hooks if entry[0] != template]
        else:
            target = (template, as_int(offset, minimum=0, name="offset"))
            controller.hooks = [entry for entry in controller.hooks if entry != target]
        if controller.hooks == previous:
            raise NavigationError(f"hook is not registered: {template}")
        result = controller.status()
        result.update({"status": "unregistered"})
        print(f"Navigation hook unregistered: {template!r}")
        return result
    except (NavigationError, ValueError) as exc:
        return _error_response(
            "pp_nav_unregister_hook",
            exc,
            "copy the exact command and optional offset from pp_nav_status",
            scene_preserved=True,
        )
    except Exception as exc:
        return _error_response(
            "pp_nav_unregister_hook",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            scene_preserved=False,
        )


def pp_nav_clear_hooks() -> Dict[str, object]:
    try:
        controller = _controller()
        if not controller.hooks:
            result = controller.status()
            result.update({"status": "no_change", "no_change": True})
            print("Navigation hooks are already empty; no change")
            return result
        removed = len(controller.hooks)
        controller.hooks.clear()
        result = controller.status()
        result.update({"status": "cleared", "removed": removed})
        print(f"Navigation hooks cleared: {removed}")
        return result
    except NavigationError as exc:
        return _error_response(
            "pp_nav_clear_hooks",
            exc,
            "run pp_nav_start first, then retry",
            scene_preserved=True,
        )
    except Exception as exc:
        return _error_response(
            "pp_nav_clear_hooks",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            scene_preserved=False,
        )


def pp_nav_clear_pins() -> Dict[str, object]:
    return _command_transition("clear_pins")


def legacy_switch_commands() -> List[Tuple[str, int]]:
    """Return the one shared pending-hook list used by legacy adapters."""
    return list(_LEGACY_SWITCH_COMMANDS)


def legacy_register_switch_command(
    command: str, offset: object = 0
) -> Dict[str, object]:
    """Canonical owner for the overlapping ``register_switch_cmd`` alias."""
    try:
        template = str(command).strip()
        if not template:
            raise NavigationError("command is required")
        _validate_hook_template(template)
        entry = (template, as_int(offset, minimum=0, name="offset"))
        if entry not in _LEGACY_SWITCH_COMMANDS:
            _LEGACY_SWITCH_COMMANDS.append(entry)
        if _CONTROLLER is not None:
            return pp_nav_register_hook(*entry)
        return {
            "status": "configured",
            "hooks": [
                {"command": value, "offset": hook_offset}
                for value, hook_offset in _LEGACY_SWITCH_COMMANDS
            ],
        }
    except Exception as exc:
        return _error_response(
            "register_switch_cmd",
            exc,
            "use a non-empty command and a documented placeholder such as {object}",
            scene_preserved=True,
        )


def legacy_unregister_switch_command(
    command: str, offset: object = ""
) -> Dict[str, object]:
    """Remove one shared pending legacy hook and its active-session copy."""
    try:
        template = str(command).strip()
        if not template:
            raise NavigationError("command is required")
        if str(offset).strip() == "":
            _LEGACY_SWITCH_COMMANDS[:] = [
                entry for entry in _LEGACY_SWITCH_COMMANDS if entry[0] != template
            ]
        else:
            target = (template, as_int(offset, minimum=0, name="offset"))
            _LEGACY_SWITCH_COMMANDS[:] = [
                entry for entry in _LEGACY_SWITCH_COMMANDS if entry != target
            ]
        if _CONTROLLER is not None:
            return pp_nav_unregister_hook(template, offset)
        return {
            "status": "configured",
            "hooks": [
                {"command": value, "offset": hook_offset}
                for value, hook_offset in _LEGACY_SWITCH_COMMANDS
            ],
        }
    except Exception as exc:
        return _error_response(
            "unregister_switch_cmd",
            exc,
            "copy the exact command and optional offset from the configured hook list",
            scene_preserved=True,
        )


def legacy_clear_switch_commands() -> Dict[str, object]:
    """Canonical owner for the overlapping ``clear_switch_cmds`` alias."""
    _LEGACY_SWITCH_COMMANDS.clear()
    if _CONTROLLER is not None:
        return pp_nav_clear_hooks()
    return {"status": "configured", "hooks": []}


def legacy_clear_pins() -> Dict[str, object]:
    """Canonical owner for the overlapping ``clear_pins`` alias."""
    return pp_nav_clear_pins()


def legacy_apply_switch_commands() -> Dict[str, object]:
    """Install shared pending legacy hooks into the active controller."""
    controller = _controller()
    controller.hooks = list(_LEGACY_SWITCH_COMMANDS)
    return controller.apply_scene() if controller.hooks else controller.status()


def pp_nav_context(quiet: object = 0) -> Dict[str, object]:
    """Return the structured current entry/window context for integrations."""
    try:
        quiet_value = _named_bool(quiet, "quiet")
    except Exception as exc:
        return _error_response(
            "pp_nav_context",
            exc,
            "use quiet=0 or quiet=1",
            scene_preserved=True,
        )
    try:
        controller = _controller()
        current = controller.session.entries[controller.session.cursor]
        window_entries = controller.session.current_entries()
        result = {
            "entry": current.display_name,
            "entry_id": current.id,
            "group": current.group_name,
            "objects": list(controller._entry_names(current)),
            "source_paths": list(current.source_paths),
            "index": controller.session.cursor,
            "position": controller.session.cursor + 1,
            "batch": controller.session.batch_index,
            "window": [
                {
                    "entry": entry.display_name,
                    "entry_id": entry.id,
                    "group": entry.group_name,
                    "objects": list(controller._entry_names(entry)),
                    "source_paths": list(entry.source_paths),
                }
                for entry in window_entries
            ],
        }
        if not quiet_value:
            print(result)
        return result
    except NavigationError as exc:
        result = {"status": "inactive", "error": str(exc)}
        if not quiet_value:
            print(
                "pp_nav_context inactive: %s. Next: run pp_nav_start first"
                % _one_line(exc)
            )
        return result
    except Exception as exc:
        return _error_response(
            "pp_nav_context",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            emit=not quiet_value,
            scene_preserved=False,
        )


def pp_nav_sync_from_scene() -> Dict[str, object]:
    """Explicitly choose the first non-pinned entry intersecting enabled objects."""
    try:
        controller = _controller()
        enabled = set(_get_names("all", 1))
        candidates = []
        for index, entry in enumerate(controller.session.entries):
            if entry.id in controller.session.pinned_entry_ids:
                continue
            if enabled.intersection(controller._entry_names(entry)):
                candidates.append(index)
        if not candidates:
            raise NavigationError("enabled scene does not identify a managed non-pinned entry")
        if len(candidates) > controller.session.window_size:
            raise NavigationError("enabled scene is ambiguous; too many managed entries are visible")
    except (NavigationError, ValueError) as exc:
        return _error_response(
            "pp_nav_sync_from_scene",
            exc,
            "enable one managed non-pinned window, or use pp_nav_goto explicitly",
            scene_preserved=True,
        )
    except Exception as exc:
        return _error_response(
            "pp_nav_sync_from_scene",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            scene_preserved=False,
        )
    return _command_transition("goto", min(candidates))


def pp_nav_status(quiet: object = 0) -> Dict[str, object]:
    try:
        quiet_value = _named_bool(quiet, "quiet")
    except Exception as exc:
        return _error_response(
            "pp_nav_status",
            exc,
            "use quiet=0 or quiet=1",
            scene_preserved=True,
        )
    try:
        status = _controller().status()
        if not quiet_value:
            print(status)
        return status
    except NavigationError as exc:
        result = {"status": "inactive", "error": str(exc)}
        if not quiet_value:
            print(
                "pp_nav_status inactive: %s. Next: run pp_nav_start first"
                % _one_line(exc)
            )
        return result
    except Exception as exc:
        return _error_response(
            "pp_nav_status",
            exc,
            "inspect the traceback before retrying",
            include_traceback=True,
            emit=not quiet_value,
            scene_preserved=False,
        )


def pp_nav_stop(keep_loaded: object = 0, force: object = 0) -> Dict[str, object]:
    global _CONTROLLER
    try:
        keep_loaded_value = _named_bool(keep_loaded, "keep_loaded")
        force_value = _named_bool(force, "force")
    except Exception as exc:
        return _error_response(
            "pp_nav_stop",
            exc,
            "use keep_loaded=0|1 and force=0|1; the active session was left unchanged",
            scene_preserved=True,
        )
    if _CONTROLLER is None:
        return {"status": "inactive"}
    controller = _CONTROLLER
    errors: Dict[str, List[str]] = {
        "keys": [],
        "cleanup": [],
        "visibility": [],
    }
    try:
        _unbind_keys(controller)
    except Exception as exc:
        errors["keys"].append(str(exc))
    try:
        released = controller.stop(keep_loaded=keep_loaded_value)
        errors["cleanup"].extend(released.get("cleanup", []))
        errors["visibility"].extend(released.get("visibility", []))
    except Exception as exc:
        errors["cleanup"].append("unexpected ownership cleanup failure: %s" % exc)

    has_errors = any(errors.values())
    if not has_errors:
        controller.degraded = False
        controller.degraded_reason = ""
        controller.degraded_errors = {}
        _CONTROLLER = None
        return {
            "status": "stopped",
            "source": controller.source,
            "forced": False,
            "errors": errors,
        }

    controller.degraded = True
    controller.degraded_reason = "stop_failed"
    controller.degraded_errors = {
        key: list(values) for key, values in errors.items() if values
    }
    result: Dict[str, object] = {
        "status": "forced_stopped" if force_value else "degraded",
        "source": controller.source,
        "forced": force_value,
        "errors": errors,
        "remaining_managed_names": sorted(
            controller.managed_names.union(controller.retired_file_names)
        ),
        "retired_file_names": sorted(controller.retired_file_names),
        "cleanup_pending": bool(controller.retired_file_names),
        "keys_bound": controller.keys_bound,
        "retry": (
            None
            if force_value
            else "repair the reported condition and run pp_nav_stop again; "
            "use force=1 only to abandon unresolved ownership"
        ),
    }
    print(
        "pp_nav_stop incomplete: %s. Next: %s"
        % (
            json.dumps(errors, sort_keys=True),
            (
                "inspect remaining_managed_names; unresolved ownership was explicitly abandoned"
                if force_value
                else "repair the reported condition and run pp_nav_stop again; use force=1 only to abandon ownership"
            ),
        )
    )
    if force_value:
        _CONTROLLER = None
    return result


def register_commands(command_api=None) -> None:
    """Register navigation commands through the shared console boundary."""

    selected = command_api if command_api is not None else cmd
    if selected is None:
        return
    commands = {
        "pp_nav_start": pp_nav_start,
        "pp_nav_next": pp_nav_next,
        "pp_nav_prev": pp_nav_prev,
        "pp_nav_next_batch": pp_nav_next_batch,
        "pp_nav_prev_batch": pp_nav_prev_batch,
        "pp_nav_goto": pp_nav_goto,
        "pp_nav_pin": pp_nav_pin,
        "pp_nav_unpin": pp_nav_unpin,
        "pp_nav_register_hook": pp_nav_register_hook,
        "pp_nav_unregister_hook": pp_nav_unregister_hook,
        "pp_nav_clear_hooks": pp_nav_clear_hooks,
        "pp_nav_clear_pins": pp_nav_clear_pins,
        "pp_nav_context": pp_nav_context,
        "pp_nav_sync_from_scene": pp_nav_sync_from_scene,
        "pp_nav_status": pp_nav_status,
        "pp_nav_stop": pp_nav_stop,
    }
    for name, function in commands.items():
        extend_command(selected, name, function)


register_commands()
