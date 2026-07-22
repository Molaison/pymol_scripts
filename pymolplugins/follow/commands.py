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
        groups_containing as metadata_groups_containing,
    )

try:
    from pymolplugins.core.parameters import as_bool, split_names
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
    cgo_policy: str = "skip_callback"
    allow_external_changes: bool = False
    skipped: Dict[str, str] = field(default_factory=dict)
    apply_count: int = 0


_SESSION: Optional[FollowSession] = None
_CALLBACK_OWNED: Set[str] = set()
_REBUILD_HOOKS: Dict[str, RebuildHook] = {}


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
    is ``"apply"`` for a driver delta and ``"undo"`` when the identity delta
    should rebuild the armed/rebased baseline.
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


def pp_follow_arm(
    driver: str,
    group: str = "",
    followers: str = "",
    cgo_policy: str = "skip_callback",
    allow_external_changes: object = 0,
) -> Dict[str, object]:
    """Arm a driver and capture follower object matrices.

    Args:
        driver: Leaf object the user will move.
        group: Optional group whose recursive leaf members should follow.
        followers: Optional comma-delimited object/group names.
        cgo_policy: ``skip_callback`` (safe default), ``static`` (transform
            even callback-owned objects), or ``rebuild``.
        allow_external_changes: Whether to overwrite followers changed after arm.
    """
    global _SESSION
    try:
        _require_cmd()
        driver = str(driver).strip()
        if not driver or not _is_leaf_object(driver):
            raise ValueError(f"driver must be an existing leaf object: '{driver}'")
        policy = str(cgo_policy or "skip_callback").strip().lower()
        if policy not in {"static", "skip_callback", "rebuild"}:
            raise ValueError("cgo_policy must be static, skip_callback, or rebuild")

        explicit = split_names(followers)
        source_group = str(group or "").strip()
        source_group_leaves: Tuple[str, ...] = ()
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
            source_group, candidates = containing[0]
            source_group_leaves = tuple(candidates)

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
        _SESSION = FollowSession(
            driver=driver,
            followers=tuple(accepted),
            baseline_ttt=raw,
            baseline_h=baseline,
            expected_h=dict(baseline),
            source_group=source_group,
            source_group_leaves=source_group_leaves,
            cgo_policy=policy,
            allow_external_changes=as_bool(allow_external_changes),
            skipped=skipped,
        )
        status = pp_follow_status(quiet=1)
        print(
            f"Follow armed: driver={driver}, followers={len(accepted)}, "
            f"group={source_group or '(explicit)'}"
        )
        if skipped:
            print("Follow skipped: " + ", ".join(f"{name} ({reason})" for name, reason in skipped.items()))
        return status
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def _require_session() -> FollowSession:
    if _SESSION is None:
        raise RuntimeError("no follow session is armed; run pp_follow_arm first")
    return _SESSION


def _validate_session_objects(session: FollowSession) -> None:
    if not _is_leaf_object(session.driver):
        raise RuntimeError(f"follow driver no longer exists: '{session.driver}'")
    missing = [name for name in session.followers if not _is_leaf_object(name)]
    if missing:
        raise RuntimeError("follow objects no longer exist: " + ", ".join(missing))
    if session.source_group:
        current = tuple(_expand_names([session.source_group]))
        if set(current) != set(session.source_group_leaves):
            raise RuntimeError(
                "follow group membership changed since arm; stop/re-arm before applying"
            )


def pp_follow_apply() -> Dict[str, object]:
    """Apply the driver's motion since arm/rebase to all followers."""
    try:
        session = _require_session()
        _validate_session_objects(session)
        driver_now = ttt_to_homogeneous(_get_ttt(session.driver))
        delta = mat4_multiply(driver_now, rigid_inverse(session.baseline_h[session.driver]))
        validate_rigid_matrix(delta)

        current_followers: Dict[str, Matrix] = {}
        for name in session.followers:
            current = ttt_to_homogeneous(_get_ttt(name))
            current_followers[name] = current
            expected = session.expected_h[name]
            if not session.allow_external_changes and not _matrices_close(current, expected):
                raise RuntimeError(
                    f"follower '{name}' changed outside this follow session; "
                    "rebase or arm with allow_external_changes=1"
                )

        applied: List[str] = []
        try:
            for name in session.followers:
                target = mat4_multiply(delta, session.baseline_h[name])
                if name in _CALLBACK_OWNED and session.cgo_policy == "rebuild":
                    hook = _REBUILD_HOOKS.get(name)
                    if hook is None:
                        continue
                    hook(
                        name,
                        delta,
                        {
                            "driver": session.driver,
                            "group": session.source_group,
                            "operation": "apply",
                        },
                    )
                    session.expected_h[name] = ttt_to_homogeneous(_get_ttt(name))
                else:
                    _set_homogeneous(name, target)
                    session.expected_h[name] = target
                applied.append(name)
        except Exception:
            for name in reversed(applied):
                if name in _CALLBACK_OWNED and session.cgo_policy == "rebuild":
                    continue
                try:
                    _set_homogeneous(name, current_followers[name])
                    session.expected_h[name] = current_followers[name]
                except Exception:
                    traceback.print_exc()
            raise
        session.expected_h[session.driver] = driver_now
        session.apply_count += 1
        print(f"Follow applied: {len(applied)} objects, apply_count={session.apply_count}")
        return {
            "status": "applied",
            "driver": session.driver,
            "followers": applied,
            "delta": list(delta),
            "apply_count": session.apply_count,
        }
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_follow_rebase() -> Dict[str, object]:
    """Use current matrices as a new arm baseline."""
    try:
        session = _require_session()
        _validate_session_objects(session)
        raw, baseline = _capture([session.driver] + list(session.followers))
        session.baseline_ttt = raw
        session.baseline_h = baseline
        session.expected_h = dict(baseline)
        session.apply_count = 0
        print("Follow baseline rebased")
        return pp_follow_status(quiet=1)
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


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
            raise RuntimeError(
                "follow objects changed after the most recent apply: %s; "
                "undo refused to overwrite external changes (use force=1)"
                % ", ".join(stale)
            )
        restored: List[str] = []
        for name in [session.driver] + list(session.followers):
            if name in _CALLBACK_OWNED and session.cgo_policy == "rebuild":
                hook = _REBUILD_HOOKS.get(name)
                if hook is None:
                    raise RuntimeError(
                        f"callback-owned object '{name}' lost its rebuild hook"
                    )
                hook(
                    name,
                    identity4(),
                    {
                        "driver": session.driver,
                        "group": session.source_group,
                        "operation": "undo",
                    },
                )
                session.expected_h[name] = ttt_to_homogeneous(_get_ttt(name))
            else:
                original = session.baseline_ttt[name]
                if original is None:
                    _set_homogeneous(name, identity4())
                else:
                    _require_cmd().set_object_ttt(name, list(original), quiet=1)
                session.expected_h[name] = session.baseline_h[name]
            restored.append(name)
        session.apply_count = 0
        print(f"Follow undo restored {len(restored)} objects")
        return {
            "status": "restored",
            "objects": restored,
            "forced": force_value,
            "stale_overwritten": stale if force_value else [],
        }
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return {"status": "error", "error": str(exc)}


def pp_follow_status(quiet: object = 0) -> Dict[str, object]:
    if _SESSION is None:
        status: Dict[str, object] = {"status": "inactive"}
    else:
        status = {
            "status": "armed",
            "driver": _SESSION.driver,
            "followers": list(_SESSION.followers),
            "group": _SESSION.source_group,
            "cgo_policy": _SESSION.cgo_policy,
            "allow_external_changes": _SESSION.allow_external_changes,
            "skipped": dict(_SESSION.skipped),
            "apply_count": _SESSION.apply_count,
        }
    if not as_bool(quiet):
        print(status)
    return status


def pp_follow_stop() -> Dict[str, object]:
    """Forget the current follow session without changing object matrices."""
    global _SESSION
    previous = _SESSION
    _SESSION = None
    if previous is None:
        print("Follow is already inactive")
        return {"status": "inactive"}
    print(f"Follow stopped: driver={previous.driver}")
    return {"status": "stopped", "driver": previous.driver}


if cmd is not None:
    cmd.extend("pp_follow_arm", pp_follow_arm)
    cmd.extend("pp_follow_apply", pp_follow_apply)
    cmd.extend("pp_follow_rebase", pp_follow_rebase)
    cmd.extend("pp_follow_undo", pp_follow_undo)
    cmd.extend("pp_follow_status", pp_follow_status)
    cmd.extend("pp_follow_stop", pp_follow_stop)
