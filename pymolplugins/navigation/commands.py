"""Canonical file/object/group navigator for PyMOL 2.x.

Only names owned by the active navigation session are enabled, disabled, or
deleted.  The cursor lives in :class:`NavigationSession`; scene state is never
silently used as the source of truth.
"""

from dataclasses import dataclass, field
import hashlib
import os
import re
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


STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
NAVIGATION_KEYS = ("right", "left", "CTRL-right", "CTRL-left")


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
        for name in explicit:
            if not _is_group(name):
                raise NavigationError(f"not a PyMOL group: {name}")
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
        result = []
        for name in names:
            if not _is_leaf(name):
                raise NavigationError(f"not a leaf PyMOL object: {name}")
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
    loaded_entry_names: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    original_enabled: Dict[str, bool] = field(default_factory=dict)
    hooks: List[Tuple[str, int]] = field(default_factory=list)
    keys_bound: bool = False
    previous_key_mappings: Dict[str, Tuple[bool, object]] = field(default_factory=dict)
    installed_key_mappings: Dict[str, object] = field(default_factory=dict)
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
        except Exception:
            for name in newly_loaded:
                try:
                    _require_cmd().delete(name)
                except Exception:
                    pass
                self.managed_names.discard(name)
            for entry_id, names in list(self.loaded_entry_names.items()):
                if any(name in newly_loaded for name in names):
                    self.loaded_entry_names.pop(entry_id, None)
            raise

        keep_ids = {entry.id for entry in keep_entries}
        for entry_id, names in list(self.loaded_entry_names.items()):
            if entry_id in keep_ids:
                continue
            for name in names:
                _require_cmd().delete(name)
                self.managed_names.discard(name)
            self.loaded_entry_names.pop(entry_id, None)

    def apply_scene(self) -> Dict[str, object]:
        self.ensure_resident()
        for name in sorted(self.managed_names):
            if name in _get_names("all"):
                _require_cmd().disable(name)
        visible_names: List[str] = []
        for entry in self.session.visible_entries():
            for name in self._entry_names(entry):
                if name not in visible_names:
                    visible_names.append(name)
                _require_cmd().enable(name)
        self._run_hooks()
        self._zoom()
        status = self.status()
        print(
            f"Navigation: {status['position']}/{status['count']} | "
            f"batch {status['batch_position']}/{status['batch_count']} | "
            f"visible={', '.join(status['visible_names'])}"
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
        for template, offset in self.hooks:
            try:
                hook_context = dict(context)
                if window_names:
                    hook_context["object"] = window_names[
                        min(max(offset, 0), len(window_names) - 1)
                    ]
                _require_cmd().do(template.format(**hook_context))
            except Exception as exc:
                print(f"Navigation hook failed '{template}': {exc}")
                traceback.print_exc()

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
        previous = self.session.cursor
        try:
            getattr(self.session, method)(*args)
            return self.apply_scene()
        except Exception:
            self.session.cursor = previous
            raise

    def status(self) -> Dict[str, object]:
        status = self.session.status()
        status.update(
            {
                "source": self.source,
                "zoom_policy": self.zoom_policy,
                "managed_names": sorted(self.managed_names),
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
            }
        )
        return status

    def stop(self, keep_loaded: bool = False) -> None:
        if self.source == "files" and not keep_loaded:
            for name in sorted(self.managed_names):
                try:
                    _require_cmd().delete(name)
                except Exception:
                    traceback.print_exc()
        else:
            for name, was_enabled in self.original_enabled.items():
                try:
                    (_require_cmd().enable if was_enabled else _require_cmd().disable)(name)
                except Exception:
                    traceback.print_exc()
        self.managed_names.clear()
        self.loaded_entry_names.clear()


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
        raise RuntimeError("no navigation session; run pp_nav_start first")
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
    try:
        _restore_keys(controller)
    finally:
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
        controller.stop(keep_loaded=False)
        return
    for name in sorted(controller.managed_names.difference(protected_names)):
        if name in set(_get_names("all")):
            _require_cmd().delete(name)
    controller.managed_names.clear()
    controller.loaded_entry_names.clear()
    controller.adopted_names.clear()


def _finalize_replacement(
    snapshot: _ReplacementSnapshot, controller: NavigationController
) -> None:
    """Transfer shared file ownership and retire unused old file objects."""
    old = snapshot.controller
    if old.source == "files":
        retained = set(controller.managed_names)
        for name in sorted(snapshot.owned_names.difference(retained)):
            try:
                if name in set(_get_names("all")):
                    _require_cmd().delete(name)
            except Exception:
                traceback.print_exc()
    old.managed_names.clear()
    old.loaded_entry_names.clear()
    controller.adoptable_file_sources.clear()
    controller.adopted_names.clear()


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
    try:
        replace_value = as_bool(replace)
        bind_value = as_bool(bind_keys)
        wrap_value = as_bool(wrap)
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
            replacement = _suspend_for_replacement(previous_controller)
        controller = NavigationController(
            source=canonical_source,
            session=session,
            zoom_policy=zoom_value,
            adoptable_file_sources=(
                dict(replacement.owned_file_sources) if replacement else {}
            ),
        )
        _CONTROLLER = controller
        if bind_value:
            _bind_keys(controller)
        result = controller.apply_scene()
        if replacement is not None:
            _finalize_replacement(replacement, controller)
        return result
    except Exception as exc:
        if controller is not None:
            try:
                _discard_failed_controller(
                    controller,
                    replacement.owned_names if replacement is not None else set(),
                )
            except Exception:
                traceback.print_exc()
        if replacement is not None:
            _CONTROLLER = replacement.controller
            try:
                _restore_replaced_controller(replacement)
            except Exception as rollback_exc:
                traceback.print_exc()
                print(f"Navigation replacement rollback failed: {rollback_exc}")
                return {
                    "status": "error",
                    "error": str(exc),
                    "rollback_error": str(rollback_exc),
                }
        elif controller is not None and _CONTROLLER is controller:
            _CONTROLLER = None
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def _command_transition(method: str, *args) -> Dict[str, object]:
    try:
        return _controller().transition(method, *args)
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


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
        return _controller().transition("goto", numeric)
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def _resolve_entry_id(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    for entry in _controller().session.entries:
        if text in {entry.id, entry.display_name, entry.group_name} or text in entry.object_names:
            return entry.id
    raise NavigationError(f"unknown navigation entry: {text}")


def pp_nav_pin(entry: object = "") -> Dict[str, object]:
    try:
        _controller().session.pin(_resolve_entry_id(entry))
        return _controller().apply_scene()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_nav_unpin(entry: object = "") -> Dict[str, object]:
    try:
        _controller().session.unpin(_resolve_entry_id(entry))
        return _controller().apply_scene()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_nav_register_hook(command: str, offset: object = 0) -> Dict[str, object]:
    try:
        template = str(command).strip()
        if not template:
            raise ValueError("command is required")
        entry = (template, as_int(offset, minimum=0, name="offset"))
        if entry not in _controller().hooks:
            _controller().hooks.append(entry)
        return pp_nav_status(quiet=1)
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_nav_unregister_hook(command: str, offset: object = "") -> Dict[str, object]:
    try:
        template = str(command).strip()
        if not template:
            raise ValueError("command is required")
        controller = _controller()
        if str(offset).strip() == "":
            controller.hooks = [entry for entry in controller.hooks if entry[0] != template]
        else:
            target = (template, as_int(offset, minimum=0, name="offset"))
            controller.hooks = [entry for entry in controller.hooks if entry != target]
        return pp_nav_status(quiet=1)
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_nav_clear_hooks() -> Dict[str, object]:
    try:
        _controller().hooks.clear()
        return pp_nav_status(quiet=1)
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_nav_clear_pins() -> Dict[str, object]:
    try:
        controller = _controller()
        controller.session.pinned_entry_ids.clear()
        return controller.apply_scene()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


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
            raise ValueError("command is required")
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
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def legacy_unregister_switch_command(
    command: str, offset: object = ""
) -> Dict[str, object]:
    """Remove one shared pending legacy hook and its active-session copy."""
    try:
        template = str(command).strip()
        if not template:
            raise ValueError("command is required")
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
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


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
        if not as_bool(quiet):
            print(result)
        return result
    except Exception as exc:
        result = {"status": "inactive", "error": str(exc)}
        if not as_bool(quiet):
            print(result)
        return result


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
        controller.session.goto(min(candidates))
        return controller.apply_scene()
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_nav_status(quiet: object = 0) -> Dict[str, object]:
    try:
        status = _controller().status()
        if not as_bool(quiet):
            print(status)
        return status
    except Exception as exc:
        result = {"status": "inactive", "error": str(exc)}
        if not as_bool(quiet):
            print(result)
        return result


def pp_nav_stop(keep_loaded: object = 0) -> Dict[str, object]:
    global _CONTROLLER
    if _CONTROLLER is None:
        return {"status": "inactive"}
    controller = _CONTROLLER
    try:
        _unbind_keys(controller)
        controller.stop(keep_loaded=as_bool(keep_loaded))
        return {"status": "stopped", "source": controller.source}
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}
    finally:
        _CONTROLLER = None


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
