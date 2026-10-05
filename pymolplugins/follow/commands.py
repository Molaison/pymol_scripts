"""Apply one object's incremental rigid motion to related PyMOL objects.

PyMOL groups are organizational containers, not scene-graph transform nodes.
This module implements an explicit ``arm -> move driver -> apply`` workflow by
comparing the driver's object TTT matrix with its armed baseline.
"""

from dataclasses import dataclass, field
import os
import sys
import traceback
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from pymol import cmd
except ImportError:  # Allows dependency-free unit tests in WSL.
    cmd = None  # type: ignore

try:
    from pymolplugins.core.math3d import (
        GeometryError,
        homogeneous_to_ttt,
        identity4,
        mat4_multiply,
        rigid_inverse,
        ttt_to_homogeneous,
        validate_rigid_matrix,
    )
    from pymolplugins.core.pymol_groups import (
        expand_names as expand_group_names,
        group_parent_snapshot,
        groups_containing as metadata_groups_containing,
    )
except ImportError:  # ``run /absolute/path/group_follow.py`` from PyMOL.
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.core.math3d import (  # type: ignore
        GeometryError,
        homogeneous_to_ttt,
        identity4,
        mat4_multiply,
        rigid_inverse,
        ttt_to_homogeneous,
        validate_rigid_matrix,
    )
    from pymolplugins.core.pymol_groups import (  # type: ignore
        expand_names as expand_group_names,
        group_parent_snapshot,
        groups_containing as metadata_groups_containing,
    )

try:
    from pymolplugins.core.parameters import as_bool, extend_command, split_names
except ImportError:
    def as_bool(value, name="value"):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        raise ValueError(f"{name} must be a boolean value")

    def split_names(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
        return [str(part).strip() for part in value if str(part).strip()]

    def extend_command(command_api, name, function):
        command_api.extend(name, function)
        return function


Matrix = Tuple[float, ...]
RebuildHook = Callable[[str, Matrix, Dict[str, object]], None]


@dataclass
class FollowSession:
    driver: str
    followers: Tuple[str, ...]
    baseline_ttt: Dict[str, Optional[Tuple[float, ...]]]
    baseline_h: Dict[str, Matrix]
    expected_h: Dict[str, Matrix]
    source_group: str = ""
    source_group_leaves: Tuple[str, ...] = ()
    group_inferred: bool = False
    group_candidates: Tuple[str, ...] = ()
    cgo_policy: str = "skip_callback"
    allow_external_changes: bool = False
    skipped: Dict[str, str] = field(default_factory=dict)
    rebuild_followers: Tuple[str, ...] = ()
    apply_count: int = 0


_SESSION: Optional[FollowSession] = None
_CALLBACK_OWNED: Set[str] = set()
_REBUILD_HOOKS: Dict[str, RebuildHook] = {}


class FollowWorkflowError(RuntimeError):
    """Expected follow state/input error with a concise recovery action."""

    def __init__(self, message: str, error_code: str, guidance: str):
        self.error_code = str(error_code)
        self.guidance = str(guidance)
        super().__init__(str(message))


def _follow_error_result(
    exc: Exception,
    *,
    operation: str = "request",
    rollback_failures: Optional[Sequence[Dict[str, str]]] = None,
    objects_attempted: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    failures = [dict(item) for item in (rollback_failures or ())]
    result: Dict[str, object] = {
        "status": "error",
        "error": str(exc),
        "changed": False,
        "scene_preserved": not bool(failures),
        "scene_changed": False if not failures else None,
        "rollback_failures": failures,
    }
    if objects_attempted is not None:
        result["objects_attempted"] = list(objects_attempted)
    if isinstance(exc, FollowWorkflowError):
        result.update(
            {
                "error_code": exc.error_code,
                "guidance": exc.guidance,
            }
        )
    elif isinstance(exc, (ValueError, GeometryError)):
        result.update(
            {
                "error_code": "invalid_follow_request",
                "guidance": (
                    "check driver/group/followers and use pp_follow_status before retrying"
                ),
            }
        )
    else:
        result.update(
            {
                "error_code": "follow_{}_failed".format(operation),
                "guidance": (
                    "inspect rollback_failures and affected objects before retrying"
                    if failures
                    else "the prior scene state was restored; inspect the PyMOL error"
                ),
            }
        )
    return result


def _report_follow_error(
    exc: Exception,
    *,
    operation: str = "request",
    rollback_failures: Optional[Sequence[Dict[str, str]]] = None,
    objects_attempted: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    result = _follow_error_result(
        exc,
        operation=operation,
        rollback_failures=rollback_failures,
        objects_attempted=objects_attempted,
    )
    print(
        "Follow error: {} Scene {} Next: {}.".format(
            exc,
            "unchanged." if result["scene_preserved"] else "may be partial.",
            result["guidance"],
        )
    )
    if not isinstance(exc, (FollowWorkflowError, ValueError, GeometryError)):
        traceback.print_exc()
    return result


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is not available in this Python process")
    for capability in ("get_object_ttt", "set_object_ttt", "get_names", "get_type"):
        if not hasattr(cmd, capability):
            raise RuntimeError(f"PyMOL lacks required cmd.{capability} capability")
    return cmd


def _all_names() -> List[str]:
    api = _require_cmd()
    try:
        return list(api.get_names("all") or [])
    except TypeError:
        return list(api.get_names() or [])


def _object_type(name: str) -> str:
    try:
        return str(_require_cmd().get_type(name) or "")
    except Exception:
        return ""


def _is_group(name: str) -> bool:
    return _object_type(name) == "object:group"


def _is_leaf_object(name: str) -> bool:
    object_type = _object_type(name)
    return object_type.startswith("object:") and object_type != "object:group"


def _direct_or_recursive_group_names(group: str) -> List[str]:
    """Compatibility helper; membership comes from session parent metadata."""
    return expand_group_names(_require_cmd(), [group])


def _expand_names(names: Iterable[str], _seen: Optional[Set[str]] = None) -> List[str]:
    return expand_group_names(_require_cmd(), names)


def _groups_containing(driver: str) -> List[Tuple[str, List[str]]]:
    return metadata_groups_containing(_require_cmd(), driver)


def _direct_parent_group(name: str) -> str:
    parent = str(group_parent_snapshot(_require_cmd()).get(str(name), "") or "")
    return parent if parent and _is_group(parent) else ""


def _get_ttt(name: str) -> Optional[Tuple[float, ...]]:
    value = _require_cmd().get_object_ttt(name, quiet=1)
    if value is None:
        return None
    if len(value) != 16:
        raise RuntimeError(f"PyMOL returned an invalid TTT for '{name}'")
    return tuple(float(item) for item in value)


def _set_homogeneous(name: str, matrix: Sequence[float]) -> None:
    ttt = homogeneous_to_ttt(matrix)
    _require_cmd().set_object_ttt(name, list(ttt), quiet=1)


def _matrices_close(left: Sequence[float], right: Sequence[float], tolerance: float = 1.0e-7) -> bool:
    return len(left) == len(right) and all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))


def register_callback_owned_object(name: str, rebuild_hook: Optional[RebuildHook] = None) -> None:
    """Mark a callback object so follow sessions can safely skip or rebuild it.

    A rebuild hook receives ``(name, delta, context)``. ``context["operation"]``
    is ``"apply"`` for a driver delta, ``"undo"`` when the identity delta
    should rebuild the armed/rebased baseline, or ``"rollback"`` when a failed
    command must replay the state from immediately before that command.
    """
    clean_name = str(name).strip()
    if not clean_name:
        raise ValueError("name is required")
    _CALLBACK_OWNED.add(clean_name)
    if rebuild_hook is None:
        _REBUILD_HOOKS.pop(clean_name, None)
    else:
        _REBUILD_HOOKS[clean_name] = rebuild_hook


def unregister_callback_owned_object(name: str) -> None:
    clean_name = str(name).strip()
    _CALLBACK_OWNED.discard(clean_name)
    _REBUILD_HOOKS.pop(clean_name, None)


def _capture(names: Iterable[str]) -> Tuple[Dict[str, Optional[Tuple[float, ...]]], Dict[str, Matrix]]:
    raw: Dict[str, Optional[Tuple[float, ...]]] = {}
    homogeneous: Dict[str, Matrix] = {}
    for name in names:
        ttt = _get_ttt(name)
        raw[name] = ttt
        homogeneous[name] = ttt_to_homogeneous(ttt)
    return raw, homogeneous


def _restore_raw_ttt(name: str, value: Optional[Tuple[float, ...]]) -> None:
    """Restore one captured TTT, treating PyMOL's ``None`` as identity."""

    if value is None:
        _set_homogeneous(name, identity4())
    else:
        _require_cmd().set_object_ttt(name, list(value), quiet=1)


def _uses_rebuild_hook(session: FollowSession, name: str) -> bool:
    return name in session.rebuild_followers


def _hook_context(
    session: FollowSession, operation: str, **extra: object
) -> Dict[str, object]:
    context: Dict[str, object] = {
        "driver": session.driver,
        "group": session.source_group,
        "operation": operation,
    }
    context.update(extra)
    return context


def _preflight_rebuild_hooks(
    session: FollowSession, names: Iterable[str]
) -> Dict[str, RebuildHook]:
    rebuild_names = [name for name in names if _uses_rebuild_hook(session, name)]
    missing = [
        name
        for name in rebuild_names
        if name not in _REBUILD_HOOKS
    ]
    if missing:
        raise FollowWorkflowError(
            "callback-owned objects lost their rebuild hooks: " + ", ".join(missing),
            "missing_follow_rebuild_hook",
            "register the missing hooks again or stop and re-arm with cgo_policy=skip_callback",
        )
    return {name: _REBUILD_HOOKS[name] for name in rebuild_names}


def _driver_delta(session: FollowSession, driver_h: Sequence[float]) -> Matrix:
    delta = mat4_multiply(
        driver_h, rigid_inverse(session.baseline_h[session.driver])
    )
    validate_rigid_matrix(delta)
    return tuple(delta)


def _incremental_driver_delta(
    session: FollowSession, driver_h: Sequence[float]
) -> Matrix:
    delta = mat4_multiply(
        driver_h, rigid_inverse(session.expected_h[session.driver])
    )
    validate_rigid_matrix(delta)
    return tuple(delta)


def _rollback_attempted(
    session: FollowSession,
    attempted: Iterable[str],
    raw_before: Dict[str, Optional[Tuple[float, ...]]],
    previous_delta: Matrix,
    failed_operation: str,
    rebuild_hooks: Dict[str, RebuildHook],
) -> List[Dict[str, str]]:
    """Best-effort reverse rollback with an explicit callback replay boundary."""

    failures: List[Dict[str, str]] = []
    for name in reversed(list(attempted)):
        errors = []
        if _uses_rebuild_hook(session, name):
            hook = rebuild_hooks.get(name)
            if hook is None:
                errors.append("rebuild hook is missing")
            else:
                try:
                    hook(
                        name,
                        previous_delta,
                        _hook_context(
                            session,
                            "rollback",
                            failed_operation=failed_operation,
                        ),
                    )
                except Exception as exc:
                    errors.append(f"callback replay failed: {exc}")
        try:
            _restore_raw_ttt(name, raw_before[name])
        except Exception as exc:
            errors.append(f"TTT restore failed: {exc}")
        if errors:
            failures.append({"object": name, "error": "; ".join(errors)})
    return failures


def pp_follow_arm(
    driver: str,
    group: str = "",
    followers: str = "",
    cgo_policy: str = "skip_callback",
    allow_external_changes: object = 0,
    replace: object = 0,
) -> Dict[str, object]:
    """Arm a driver and capture follower object matrices.

    Args:
        driver: Leaf object the user will move.
        group: Optional group whose recursive leaf members should follow.
        followers: Optional comma-delimited object/group names.
        cgo_policy: ``skip_callback`` (safe default), ``static`` (transform
            even callback-owned objects), or ``rebuild``.
        allow_external_changes: Whether to overwrite followers changed after arm.
        replace: Explicitly replace an already armed session after preflight.
    """
    global _SESSION
    try:
        _require_cmd()
        previous_session = _SESSION
        replace_value = as_bool(replace)
        if previous_session is not None and not replace_value:
            raise FollowWorkflowError(
                "a follow session is already armed",
                "follow_session_active",
                "run pp_follow_stop first or pass replace=1 explicitly",
            )
        driver = str(driver).strip()
        if not driver or not _is_leaf_object(driver):
            raise ValueError(f"driver must be an existing leaf object: '{driver}'")
        policy = str(cgo_policy or "skip_callback").strip().lower()
        if policy not in {"static", "skip_callback", "rebuild"}:
            raise ValueError("cgo_policy must be static, skip_callback, or rebuild")

        explicit = split_names(followers)
        source_group = str(group or "").strip()
        source_group_leaves: Tuple[str, ...] = ()
        group_inferred = False
        group_candidates: Tuple[str, ...] = ()
        if source_group:
            if not _is_group(source_group):
                raise ValueError(f"group is not an object:group: '{source_group}'")
            candidates = _expand_names([source_group])
            source_group_leaves = tuple(candidates)
        elif explicit:
            candidates = _expand_names(explicit)
        else:
            containing = _groups_containing(driver)
            if not containing:
                raise ValueError(
                    "driver is not inside a discoverable group; provide group= or followers="
                )
            direct_parent = _direct_parent_group(driver)
            direct_match = next(
                (item for item in containing if item[0] == direct_parent), None
            )
            minimum_size = len(containing[0][1])
            equally_specific = [item for item in containing if len(item[1]) == minimum_size]
            if direct_match is not None:
                selected_group = direct_match
            elif len(equally_specific) == 1:
                selected_group = equally_specific[0]
            else:
                names = [item[0] for item in equally_specific]
                raise FollowWorkflowError(
                    "driver belongs to equally specific groups: {}".format(
                        ", ".join(names)
                    ),
                    "ambiguous_follow_group",
                    "pass group=<name> or followers=<names> explicitly",
                )
            source_group, candidates = selected_group
            source_group_leaves = tuple(candidates)
            group_inferred = True
            group_candidates = tuple(item[0] for item in containing)

        if explicit and source_group:
            candidates.extend(_expand_names(explicit))
        unique_candidates = list(dict.fromkeys(candidates))
        follower_names = [name for name in unique_candidates if name != driver]
        if not follower_names:
            raise ValueError("no follower leaf objects were resolved")

        skipped: Dict[str, str] = {}
        accepted: List[str] = []
        for name in follower_names:
            if name in _CALLBACK_OWNED and policy == "skip_callback":
                skipped[name] = "callback-owned object"
                continue
            if name in _CALLBACK_OWNED and policy == "rebuild" and name not in _REBUILD_HOOKS:
                skipped[name] = "callback-owned object has no rebuild hook"
                continue
            accepted.append(name)
        if not accepted:
            raise ValueError("all resolved followers were skipped")

        all_capture = [driver] + accepted
        raw, baseline = _capture(all_capture)
        new_session = FollowSession(
            driver=driver,
            followers=tuple(accepted),
            baseline_ttt=raw,
            baseline_h=baseline,
            expected_h=dict(baseline),
            source_group=source_group,
            source_group_leaves=source_group_leaves,
            group_inferred=group_inferred,
            group_candidates=group_candidates,
            cgo_policy=policy,
            allow_external_changes=as_bool(allow_external_changes),
            skipped=skipped,
            rebuild_followers=tuple(
                name
                for name in accepted
                if name in _CALLBACK_OWNED and policy == "rebuild"
            ),
        )
        _SESSION = new_session
        status = pp_follow_status(quiet=1)
        status["replaced"] = previous_session is not None
        status["replaced_apply_count"] = (
            previous_session.apply_count if previous_session is not None else 0
        )
        status["discarded_undo_baseline"] = bool(
            previous_session is not None and previous_session.apply_count > 0
        )
        print(
            f"Follow armed: driver={driver}, followers={len(accepted)}, "
            f"group={source_group or '(explicit)'}"
        )
        if skipped:
            print("Follow skipped: " + ", ".join(f"{name} ({reason})" for name, reason in skipped.items()))
        if status["discarded_undo_baseline"]:
            print(
                "Follow warning: replace=1 discarded the prior session's "
                "applied undo baseline."
            )
        return status
    except Exception as exc:
        return _report_follow_error(exc, operation="arm")


def _require_session() -> FollowSession:
    if _SESSION is None:
        raise FollowWorkflowError(
            "no follow session is armed",
            "no_follow_session",
            "run pp_follow_arm first",
        )
    return _SESSION


def _validate_session_objects(session: FollowSession) -> None:
    if not _is_leaf_object(session.driver):
        raise FollowWorkflowError(
            f"follow driver no longer exists: '{session.driver}'",
            "stale_follow_session",
            "restore the driver or stop and re-arm the session",
        )
    missing = [name for name in session.followers if not _is_leaf_object(name)]
    if missing:
        raise FollowWorkflowError(
            "follow objects no longer exist: " + ", ".join(missing),
            "stale_follow_session",
            "restore the objects or stop and re-arm the session",
        )
    if session.source_group:
        current = tuple(_expand_names([session.source_group]))
        if set(current) != set(session.source_group_leaves):
            raise FollowWorkflowError(
                "follow group membership changed since arm",
                "stale_follow_group",
                "run pp_follow_stop and pp_follow_arm again before applying",
            )


def pp_follow_apply() -> Dict[str, object]:
    """Apply the driver's motion since arm/rebase to all followers."""
    try:
        session = _require_session()
        _validate_session_objects(session)
        driver_now = ttt_to_homogeneous(_get_ttt(session.driver))
        delta = _driver_delta(session, driver_now)
        incremental_delta = _incremental_driver_delta(session, driver_now)
        raw_before, current_followers = _capture(session.followers)
        for name in session.followers:
            current = current_followers[name]
            expected = session.expected_h[name]
            if not session.allow_external_changes and not _matrices_close(current, expected):
                raise FollowWorkflowError(
                    f"follower '{name}' changed outside this follow session",
                    "stale_follow_follower",
                    "rebase intentionally or re-arm with allow_external_changes=1",
                )
        if _matrices_close(incremental_delta, identity4()):
            print("Follow apply: driver has not moved; no follower was changed")
            return {
                "status": "no_change",
                "changed": False,
                "scene_changed": False,
                "driver": session.driver,
                "followers": [],
                "delta": list(incremental_delta),
                "absolute_delta": list(delta),
                "apply_count": session.apply_count,
                "next_command": "move {}, then run pp_follow_apply".format(
                    session.driver
                ),
            }

        rebuild_hooks = _preflight_rebuild_hooks(session, session.followers)

        expected_before = dict(session.expected_h)
        apply_count_before = session.apply_count
        previous_delta = _driver_delta(
            session, session.expected_h[session.driver]
        )
        expected_after = dict(expected_before)
        applied: List[str] = []
        attempted: List[str] = []
        try:
            for name in session.followers:
                attempted.append(name)
                target = mat4_multiply(delta, session.baseline_h[name])
                if _uses_rebuild_hook(session, name):
                    hook = rebuild_hooks[name]
                    hook(
                        name,
                        delta,
                        _hook_context(session, "apply"),
                    )
                    expected_after[name] = ttt_to_homogeneous(_get_ttt(name))
                else:
                    _set_homogeneous(name, target)
                    expected_after[name] = tuple(target)
                applied.append(name)
        except Exception as exc:
            rollback_failures = _rollback_attempted(
                session,
                attempted,
                raw_before,
                previous_delta,
                "apply",
                rebuild_hooks,
            )
            session.expected_h = expected_before
            session.apply_count = apply_count_before
            return _report_follow_error(
                exc,
                operation="apply",
                rollback_failures=rollback_failures,
                objects_attempted=attempted,
            )
        expected_after[session.driver] = tuple(driver_now)
        session.expected_h = expected_after
        session.apply_count = apply_count_before + 1
        print(f"Follow applied: {len(applied)} objects, apply_count={session.apply_count}")
        return {
            "status": "applied",
            "changed": True,
            "scene_changed": True,
            "driver": session.driver,
            "followers": applied,
            "delta": list(delta),
            "apply_count": session.apply_count,
            "next_command": "pp_follow_undo",
        }
    except Exception as exc:
        return _report_follow_error(exc, operation="apply")


def pp_follow_rebase() -> Dict[str, object]:
    """Use current matrices as a new arm baseline."""
    try:
        session = _require_session()
        _validate_session_objects(session)
        previous_apply_count = session.apply_count
        raw, baseline = _capture([session.driver] + list(session.followers))
        session.baseline_ttt = raw
        session.baseline_h = baseline
        session.expected_h = dict(baseline)
        session.apply_count = 0
        print("Follow baseline rebased")
        result = pp_follow_status(quiet=1)
        result.update(
            {
                "status": "rebased",
                "changed": False,
                "scene_changed": False,
                "previous_apply_count": previous_apply_count,
                "undo_baseline_replaced": True,
                "next_command": "move {}, then run pp_follow_apply".format(
                    session.driver
                ),
            }
        )
        return result
    except Exception as exc:
        return _report_follow_error(exc, operation="rebase")


def _stale_undo_objects(session: FollowSession) -> List[str]:
    """Return objects changed since the most recent successful apply."""

    if session.apply_count < 1:
        return []
    stale = []
    for name in [session.driver] + list(session.followers):
        current = ttt_to_homogeneous(_get_ttt(name))
        if not _matrices_close(current, session.expected_h[name]):
            stale.append(name)
    return stale


def pp_follow_undo(force: object = 0) -> Dict[str, object]:
    """Restore the current arm/rebase baseline without clobbering newer edits.

    Args:
        force: Overwrite driver/follower changes made after the most recent
            successful apply. The default refuses such a stale undo.
    """
    try:
        session = _require_session()
        _validate_session_objects(session)
        force_value = as_bool(force)
        stale = _stale_undo_objects(session)
        if stale and not force_value:
            raise FollowWorkflowError(
                "follow objects changed after the most recent apply: %s"
                % ", ".join(stale),
                "stale_follow_undo",
                "inspect newer edits, then use force=1 only if they may be overwritten",
            )
        names = [session.driver] + list(session.followers)
        rebuild_hooks = _preflight_rebuild_hooks(session, names)
        raw_before, _current_before = _capture(names)
        previous_delta = _driver_delta(
            session, session.expected_h[session.driver]
        )
        expected_before = dict(session.expected_h)
        apply_count_before = session.apply_count
        expected_after = dict(expected_before)
        restored: List[str] = []
        attempted: List[str] = []
        try:
            for name in names:
                attempted.append(name)
                if _uses_rebuild_hook(session, name):
                    hook = rebuild_hooks[name]
                    hook(
                        name,
                        identity4(),
                        _hook_context(session, "undo"),
                    )
                    expected_after[name] = ttt_to_homogeneous(_get_ttt(name))
                else:
                    _restore_raw_ttt(name, session.baseline_ttt[name])
                    expected_after[name] = session.baseline_h[name]
                restored.append(name)
        except Exception as exc:
            rollback_failures = _rollback_attempted(
                session,
                attempted,
                raw_before,
                previous_delta,
                "undo",
                rebuild_hooks,
            )
            session.expected_h = expected_before
            session.apply_count = apply_count_before
            result = _report_follow_error(
                exc,
                operation="undo",
                rollback_failures=rollback_failures,
                objects_attempted=attempted,
            )
            result["forced"] = force_value
            return result
        session.expected_h = expected_after
        session.apply_count = 0
        print(f"Follow undo restored {len(restored)} objects")
        return {
            "status": "restored",
            "changed": True,
            "scene_changed": True,
            "objects": restored,
            "forced": force_value,
            "stale_overwritten": stale if force_value else [],
            "rollback_failures": [],
            "next_command": "move {}, then run pp_follow_apply".format(
                session.driver
            ),
        }
    except Exception as exc:
        return _report_follow_error(exc, operation="undo")


def pp_follow_status(quiet: object = 0) -> Dict[str, object]:
    try:
        quiet_value = as_bool(quiet)
        if _SESSION is None:
            status: Dict[str, object] = {
                "status": "inactive",
                "changed": False,
                "scene_changed": False,
                "next_command": "pp_follow_arm DRIVER, group=GROUP",
            }
        else:
            status = {
                "status": "armed",
                "changed": False,
                "scene_changed": False,
                "driver": _SESSION.driver,
                "followers": list(_SESSION.followers),
                "group": _SESSION.source_group,
                "group_inferred": _SESSION.group_inferred,
                "group_candidates": list(_SESSION.group_candidates),
                "cgo_policy": _SESSION.cgo_policy,
                "allow_external_changes": _SESSION.allow_external_changes,
                "skipped": dict(_SESSION.skipped),
                "rebuild_followers": list(_SESSION.rebuild_followers),
                "apply_count": _SESSION.apply_count,
                "next_command": (
                    "pp_follow_undo"
                    if _SESSION.apply_count
                    else "move {}, then run pp_follow_apply".format(
                        _SESSION.driver
                    )
                ),
            }
        if not quiet_value:
            print(status)
        return status
    except Exception as exc:
        return _report_follow_error(exc, operation="status")


def pp_follow_stop() -> Dict[str, object]:
    """Forget the current follow session without changing object matrices."""
    global _SESSION
    previous = _SESSION
    _SESSION = None
    if previous is None:
        print("Follow is already inactive")
        return {
            "status": "inactive",
            "changed": False,
            "scene_changed": False,
            "next_command": "pp_follow_arm DRIVER, group=GROUP",
        }
    print(f"Follow stopped: driver={previous.driver}")
    return {
        "status": "stopped",
        "changed": False,
        "scene_changed": False,
        "driver": previous.driver,
        "discarded_undo_baseline": previous.apply_count > 0,
        "next_command": "pp_follow_arm DRIVER, group=GROUP",
    }


if cmd is not None:
    extend_command(cmd, "pp_follow_arm", pp_follow_arm)
    extend_command(cmd, "pp_follow_apply", pp_follow_apply)
    extend_command(cmd, "pp_follow_rebase", pp_follow_rebase)
    extend_command(cmd, "pp_follow_undo", pp_follow_undo)
    extend_command(cmd, "pp_follow_status", pp_follow_status)
    extend_command(cmd, "pp_follow_stop", pp_follow_stop)
