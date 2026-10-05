"""Eight task-oriented facades over the canonical PyMOL workflows."""

from __future__ import annotations

import importlib
import os
import re
import traceback
from typing import Dict, Mapping, Optional, Sequence, Tuple

try:
    from pymol import cmd
except ImportError:
    cmd = None  # type: ignore

from pymolplugins.core.parameters import as_bool, as_float, as_int, extend_command
from pymolplugins.interactions.presets import PRESETS as INTERACTION_PRESETS
from pymolplugins.targets.resolver import expand_targets, resolve_targets
from pymolplugins.tasks import journal


TASK_SCHEMA = "pymolplugins.task-command/1.0"
_REPRESENTATION_MASKS = {
    "sticks": 1 << 0,
    "spheres": 1 << 1,
    "surface": 1 << 2,
    "labels": 1 << 3,
    "nb_spheres": 1 << 4,
    "cartoon": 1 << 5,
    "ribbon": 1 << 6,
    "lines": 1 << 7,
    "mesh": 1 << 8,
    "dots": 1 << 9,
    "nonbonded": 1 << 11,
    "ellipsoids": 1 << 19,
}
_METRIC_PROFILES = {
    "interface": "cms,bunsat_bound_heavy,bunsat_interface_delta",
    "surface": "cms",
    "unsatisfied": "bunsat_bound_heavy,bunsat_interface_delta",
}
_INTERACTION_LISTENER_TOKEN = globals().get("_INTERACTION_LISTENER_TOKEN", "")
_INTERACTION_LISTENER_MODULE = globals().get("_INTERACTION_LISTENER_MODULE", None)
_SAFE_COPYABLE_TOKEN = re.compile(r"^[A-Za-z0-9_.*?+:/\\-]+$")


def _require_cmd():
    if cmd is None:
        raise RuntimeError("PyMOL cmd API is unavailable")
    return cmd


def _load(name: str):
    return importlib.import_module(name)


def _message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _copyable_console_token(value: object) -> Optional[str]:
    """Quote one value for a single copyable PyMOL STRICT-parser line."""

    text = str(value)
    if not text or any(marker in text for marker in ("\r", "\n", "\0", ";")):
        return None
    if _SAFE_COPYABLE_TOKEN.fullmatch(text):
        return text
    if '"' not in text:
        return '"%s"' % text
    if "'" not in text:
        return "'%s'" % text
    if "'''" not in text:
        return "'''%s'''" % text
    return None


def _contacts_apply_hint(
    source: str,
    focus: str,
    partner: str,
    preset: str,
    group: str,
    state: int,
) -> str:
    values = {
        "source": _copyable_console_token(source),
        "focus": _copyable_console_token(focus) if focus else "",
        "partner": _copyable_console_token(partner) if partner else "",
        "preset": _copyable_console_token(preset) if preset != "auto" else "",
        "group": _copyable_console_token(group) if group else "",
    }
    if any(value is None for value in values.values()):
        return "repeat the same pp_contacts command without preview=1"
    parts = ["pp_contacts %s" % values["source"]]
    for name in ("focus", "partner", "preset", "group"):
        if values[name]:
            parts.append("%s=%s" % (name, values[name]))
    if state != 1:
        parts.append("state=%d" % state)
    return ", ".join(parts)


def _error(task: str, exc: Exception, action: str) -> Dict[str, object]:
    message = _message(exc)
    scene_preserved = bool(getattr(exc, "scene_preserved", True))
    scene_text = "scene preserved" if scene_preserved else "scene may be partially changed"
    print("%s error: %s; %s" % (task, message, scene_text))
    print("Action: %s" % action)
    if not isinstance(exc, (KeyError, ValueError, RuntimeError)):
        traceback.print_exc()
    return {
        "schema": TASK_SCHEMA,
        "task": task,
        "status": "error",
        "error": message,
        "action": action,
        "scene_preserved": scene_preserved,
    }


def _is_error(result: object) -> bool:
    if not isinstance(result, Mapping):
        return False
    return str(result.get("status", "")).lower() in {
        "error",
        "failed",
        "partial_error",
        "degraded",
        "stale",
    }


def _response(
    task: str,
    status: str,
    delegated: str,
    result: object,
    operation: Optional[Mapping[str, object]] = None,
    propagate_result_error: bool = True,
    default_scene_preserved: bool = False,
) -> Dict[str, object]:
    result_is_error = propagate_result_error and _is_error(result)
    value = {
        "schema": TASK_SCHEMA,
        "task": task,
        "status": "error" if result_is_error else str(status),
        "delegated_command": str(delegated),
        "result": result,
    }
    if operation is not None:
        value["operation"] = dict(operation)
        value["operation_id"] = str(operation["operation_id"])
        value["undo"] = "pp_undo %s" % operation["operation_id"]
    if result_is_error and isinstance(result, Mapping):
        value["error"] = str(result.get("error", "delegated command failed"))
        value["scene_preserved"] = bool(
            result.get("scene_preserved", default_scene_preserved)
        )
    return value


def _ambiguous_implicit_matches(matches, mode: str) -> Dict[str, Sequence[str]]:
    grouped: Dict[str, list] = {}
    for match in matches:
        implicit = match.matched_by == "fuzzy" or (
            mode == "auto" and match.matched_by == "contains"
        )
        if implicit:
            grouped.setdefault(str(match.query), []).append(str(match.name))
    return {query: names for query, names in grouped.items() if len(names) > 1}


def _show_targets(target: object, match: str, all_matches: bool):
    api = _require_cmd()
    mode = str(match).strip().lower()
    matches = resolve_targets(api, target, match=mode, kinds="molecule,group")
    ambiguous = _ambiguous_implicit_matches(matches, mode)
    if ambiguous and not all_matches:
        raise ValueError(
            "target is ambiguous: %s; choose an exact name or pass all_matches=1"
            % "; ".join(
                "%s -> %s" % (query, ", ".join(names))
                for query, names in ambiguous.items()
            )
        )
    expanded = expand_targets(api, matches, "molecules")
    unsupported = [value.target for value in expanded if value.object_type != "object:molecule"]
    if unsupported:
        raise ValueError(
            "representation targets must be molecule objects, not: %s"
            % ", ".join(unsupported)
        )
    return matches, expanded


def pp_show(
    representation="surface",
    target="all",
    mode="show",
    match="auto",
    preview=0,
    all_matches=0,
) -> Dict[str, object]:
    """Show/hide one whitelisted representation on objects or group leaves."""

    try:
        rep = str(representation).strip().lower()
        action = str(mode).strip().lower()
        if rep not in _REPRESENTATION_MASKS:
            raise ValueError(
                "representation must be one of: %s"
                % ", ".join(sorted(_REPRESENTATION_MASKS))
            )
        if action not in {"show", "hide"}:
            raise ValueError("mode must be show or hide")
        preview_value = as_bool(preview)
        allow_all = as_bool(all_matches)
        target_value = str(target).strip()
        match_value = str(match).strip().lower()
        if target_value.lower() == "all" and match_value == "auto":
            target_value, match_value = "*", "glob"
        matches, expanded = _show_targets(target_value, match_value, allow_all)
        names = [value.target for value in expanded]
        mask = _REPRESENTATION_MASKS[rep]
        plan = {
            "representation": rep,
            "mode": action,
            "targets": names,
            "matches": [value.to_dict() for value in matches],
        }
        if preview_value:
            snapshots = journal.capture_bundle(_require_cmd(), names, mask)
            plan["state_counts"] = {
                name: snapshots[name]["state_count"] for name in names
            }
            plan["undo_identity"] = (
                "durable_object_property_on_apply"
                if hasattr(_require_cmd(), "get_property")
                and hasattr(_require_cmd(), "set_property")
                else "atom_fingerprint_only"
            )
            print(
                "Preview only; nothing changed. pp_show would %s %s on %d object(s): %s"
                % (action, rep, len(names), ", ".join(names))
            )
            return _response("pp_show", "preview", "scene representation API", plan)
        baseline, committed = journal.apply_representation(
            _require_cmd(), rep, names, action, mask
        )
        plan["state_counts"] = {
            name: baseline[name]["state_count"] for name in names
        }
        plan["undo_identity"] = (
            "durable_object_property"
            if all(baseline[name].get("object_identity") for name in names)
            else "atom_fingerprint_only"
        )
        if plan["undo_identity"] == "atom_fingerprint_only":
            print(
                "Warning: this Host lacks verified object properties; undo can "
                "detect changed atoms but not an identical same-name reload."
            )
        reference = journal.store_show_record(rep, mask, baseline, committed)
        operation = journal.record_operation(
            "show", reference, "%s %s on %s" % (action, rep, ",".join(names))
        )
        print(
            "pp_show changed %d object(s): %s %s. Undo: pp_undo %s"
            % (len(names), action, rep, operation["operation_id"])
        )
        return _response("pp_show", "changed", "scene representation API", plan, operation)
    except Exception as exc:
        return _error(
            "pp_show",
            exc,
            "Run pp_help surface; use an exact molecule/group target or preview=1.",
        )


def _infer_navigation_source(target: str) -> str:
    if os.path.isdir(os.path.abspath(os.path.expanduser(target))):
        return "files"
    mode = "glob" if any(token in target for token in "*?[") else "exact"
    matches = resolve_targets(
        _require_cmd(), target, match=mode, kinds="object,group"
    )
    kinds = {value.kind for value in matches}
    if kinds == {"group"}:
        return "groups"
    if kinds == {"molecule"}:
        return "objects"
    raise ValueError(
        "source is ambiguous across object types; pass source=files, groups, or objects"
    )


def _start_navigation(
    navigation,
    target: str,
    source: str,
    window: object,
    step: object,
    page: object,
    wrap: object,
    keys: object,
    replace: object,
):
    selected = str(source).strip().lower()
    if selected == "auto":
        if not target:
            raise ValueError("source=auto needs an unambiguous target")
        selected = _infer_navigation_source(target)
    aliases = {"group": "groups", "object": "objects", "file": "files"}
    selected = aliases.get(selected, selected)
    if selected not in {"groups", "objects", "files"}:
        raise ValueError("source must be auto, files, groups, or objects")
    kwargs = {
        "source": selected,
        "batch_size": page,
        "window_size": window,
        "stride": step,
        "wrap": wrap,
        "bind_keys": keys,
        "replace": replace,
    }
    if selected == "files":
        kwargs["path"] = target or "."
    else:
        kwargs["entries"] = target
    return navigation.pp_nav_start(**kwargs)


def _navigation_action(navigation, action: str, target: str, force: object):
    simple = {
        "next": navigation.pp_nav_next,
        "prev": navigation.pp_nav_prev,
        "previous": navigation.pp_nav_prev,
        "back": navigation.pp_nav_prev,
        "next-page": navigation.pp_nav_next_batch,
        "next_page": navigation.pp_nav_next_batch,
        "prev-page": navigation.pp_nav_prev_batch,
        "prev_page": navigation.pp_nav_prev_batch,
        "status": navigation.pp_nav_status,
    }
    if action in simple:
        return simple[action]()
    if action == "goto":
        if not target:
            raise ValueError("goto needs a one-based entry index")
        return navigation.pp_nav_goto(target)
    if action == "pin":
        return navigation.pp_nav_pin(target)
    if action == "unpin":
        return navigation.pp_nav_unpin(target)
    if action == "stop":
        return navigation.pp_nav_stop(force=force)
    raise ValueError(
        "action must be start/next/prev/next-page/prev-page/goto/pin/unpin/status/stop"
    )


def _navigation_status(verb: str, result: object) -> str:
    if not isinstance(result, Mapping):
        return "changed"
    raw = str(result.get("status", ""))
    if raw == "boundary" or result.get("no_change"):
        return "unchanged"
    if raw in {"stopped", "forced_stopped"}:
        return raw
    if verb == "status" or raw == "inactive":
        return "status"
    return "changed"


_NAVIGATION_DELEGATES = {
    "next": "pp_nav_next",
    "prev": "pp_nav_prev",
    "previous": "pp_nav_prev",
    "back": "pp_nav_prev",
    "next-page": "pp_nav_next_batch",
    "next_page": "pp_nav_next_batch",
    "prev-page": "pp_nav_prev_batch",
    "prev_page": "pp_nav_prev_batch",
    "goto": "pp_nav_goto",
    "pin": "pp_nav_pin",
    "unpin": "pp_nav_unpin",
    "status": "pp_nav_status",
    "stop": "pp_nav_stop",
}


def pp_nav(
    action="",
    target="",
    source="auto",
    window=1,
    step=1,
    page=20,
    wrap=1,
    keys=0,
    replace=0,
    force=0,
) -> Dict[str, object]:
    """Start or control one navigation session with task-level action words."""

    try:
        verb = str(action).strip().lower()
        target_value = str(target).strip()
        source_value = str(source).strip().lower()
        window_value = as_int(window, minimum=1, name="window")
        step_value = as_int(step, minimum=1, name="step")
        page_value = as_int(page, minimum=1, name="page")
        wrap_value = as_bool(wrap)
        keys_value = as_bool(keys)
        replace_value = as_bool(replace)
        force_value = as_bool(force)
        source_aliases = {"groups": "groups", "objects": "objects", "files": "files"}
        if not verb and target_value:
            verb = "start"
        elif not verb:
            verb = "status"
        elif verb in source_aliases:
            source_value = source_aliases[verb]
            verb = "start"
        elif verb not in {
            "start", "next", "prev", "previous", "back", "next-page",
            "next_page", "prev-page", "prev_page", "goto", "pin", "unpin",
            "status", "stop",
        }:
            if target_value:
                raise ValueError("both action and target look like navigation targets")
            target_value = str(action).strip()
            verb = "start"
        if not source_value:
            raise ValueError("source must be auto, files, groups, or objects")
        start_only = []
        if source_value != "auto":
            start_only.append("source")
        if window_value != 1:
            start_only.append("window")
        if step_value != 1:
            start_only.append("step")
        if page_value != 20:
            start_only.append("page")
        if wrap_value is not True:
            start_only.append("wrap")
        if keys_value is not False:
            start_only.append("keys")
        if replace_value is not False:
            start_only.append("replace")
        if verb != "start" and start_only:
            raise ValueError(
                "action %s does not accept start-only option(s): %s"
                % (verb, ", ".join(start_only))
            )
        if force_value and verb != "stop":
            raise ValueError("force is only valid with action stop")
        navigation = _load("pymolplugins.navigation.commands")
        if verb == "start":
            if force_value:
                raise ValueError("force is only valid with action stop")
            result = _start_navigation(
                navigation, target_value, source_value, window_value, step_value,
                page_value, int(wrap_value), int(keys_value), int(replace_value),
            )
            delegated = "pp_nav_start"
        else:
            if target_value and verb not in {"goto", "pin", "unpin"}:
                raise ValueError("action %s does not accept target" % verb)
            result = _navigation_action(
                navigation, verb, target_value, int(force_value)
            )
            delegated = _NAVIGATION_DELEGATES[verb]
        status = _navigation_status(verb, result)
        if status == "unchanged":
            print("pp_nav unchanged: already at a non-wrapping boundary.")
        elif verb == "status" and isinstance(result, Mapping):
            print("pp_nav status: %s" % result.get("status", "unknown"))
            if result.get("status") == "inactive":
                print("Start: pp_nav TARGET or pp_nav groups")
        elif verb == "stop":
            print("pp_nav stopped; prior visibility restoration is reported below.")
        return _response(
            "pp_nav",
            status,
            delegated,
            result,
            default_scene_preserved=(
                isinstance(result, Mapping)
                and str(result.get("status", "")).lower() == "stale"
            ),
        )
    except Exception as exc:
        return _error(
            "pp_nav",
            exc,
            "Run pp_help navigate; use target=NAME when an object is named like an action.",
        )


def _alignment_anchors(mobile: str, reference: str) -> Tuple[str, str]:
    api = _require_cmd()
    try:
        mobile_type = str(api.get_type(mobile) or "")
        reference_type = str(api.get_type(reference) or "")
    except Exception:
        return mobile, reference
    if mobile_type == reference_type == "object:molecule":
        return (
            "(%s) and polymer and name CA" % mobile,
            "(%s) and polymer and name CA" % reference,
        )
    return mobile, reference


def _alignment_status(result: Mapping[str, object]) -> str:
    if _is_error(result):
        return "error"
    return "changed" if result.get("committed") else "preview"


def pp_align(
    mobile,
    reference="",
    motif="",
    ligand="",
    move="",
    preview=0,
    state=1,
) -> Dict[str, object]:
    """Align anchors, objects, one motif, or one ligand with safe inference."""

    try:
        mobile_value = str(mobile).strip()
        reference_value = str(reference).strip()
        motif_value = str(motif).strip()
        ligand_value = str(ligand).strip()
        move_value = str(move).strip()
        preview_value = as_bool(preview)
        if motif_value and ligand_value:
            raise ValueError("motif and ligand are mutually exclusive")
        action_mode = mobile_value.lower() in {"apply", "commit"}
        if action_mode:
            if not reference_value:
                raise ValueError("apply needs an alignment preview/record ID")
            state_value = as_int(state, minimum=1, name="state")
            if motif_value or ligand_value or move_value or preview_value or state_value != 1:
                raise ValueError(
                    "apply accepts only an alignment record ID; motif, ligand, move, "
                    "preview, and state are not applicable"
                )
        alignment = _load("pymolplugins.alignment.rigid")
        if action_mode:
            result = alignment.pp_align_commit(reference_value)
            delegated = "pp_align_commit"
        else:
            if not mobile_value or not reference_value:
                raise ValueError("mobile and reference are required")
            common = {
                "move": move,
                "state": state,
                "preview": int(preview_value),
                "commit": int(not preview_value),
            }
            if motif_value:
                result = alignment.pp_align_motif(
                    mobile_value, reference_value, motif_value, **common
                )
                delegated = "pp_align_motif"
            elif ligand_value:
                result = alignment.pp_align_ligand(
                    mobile_value,
                    reference_value,
                    mobile_ligand=ligand_value,
                    target_ligand=ligand_value,
                    **common,
                )
                delegated = "pp_align_ligand"
            else:
                mobile_anchor, reference_anchor = _alignment_anchors(
                    mobile_value, reference_value
                )
                result = alignment.pp_align_anchor(
                    mobile_anchor, reference_anchor, **common
                )
                delegated = "pp_align_anchor"
        operation = None
        if isinstance(result, Mapping) and result.get("committed") and not _is_error(result):
            operation = journal.record_operation(
                "alignment", result["id"], "alignment %s" % result["id"]
            )
            print("Unified undo: pp_undo %s" % operation["operation_id"])
        return _response(
            "pp_align",
            _alignment_status(result) if isinstance(result, Mapping) else "changed",
            delegated,
            result,
            operation,
        )
    except Exception as exc:
        return _error(
            "pp_align",
            exc,
            "Run pp_help align; provide one mobile/reference pair and only one of motif/ligand.",
        )


def _center_at_origin(value: object) -> bool:
    text = str(value).strip().lower()
    if text == "origin":
        return True
    if text in {"keep", "current"}:
        return False
    raise ValueError("center must be origin or keep")


def _orient_explicit_vector(
    geometry,
    target: str,
    vector: str,
    destination: str,
    move: object,
    center_origin: bool,
    state: int,
    commit: bool,
) -> Dict[str, object]:
    objects = geometry._move_objects(target, move)
    if not objects:
        raise ValueError("no move objects resolved")
    points = geometry._selection_coords(target, "ca", state)
    if not points:
        points = geometry._selection_coords(target, "heavy", state)
    center = geometry.centroid(points)
    result = geometry.vector_axis(
        geometry._target_axis(vector), center, source="vector", input_selection=target
    )
    target_direction = geometry._target_axis(destination)
    rotation = geometry.rotation_from_to(result.direction, target_direction)
    target_center = (0.0, 0.0, 0.0) if center_origin else result.center
    matrix = geometry.compose_about_center(rotation, result.center, target_center)
    operation = geometry._apply_orientation(matrix, objects) if commit else None
    return {
        "schema": "pymolplugins.orientation/1.0",
        "axis": result.to_dict(),
        "matrix": list(matrix),
        "move_objects": objects,
        "committed": bool(commit),
        "operation_id": operation.id if operation is not None else None,
        "committed_fingerprint": (
            operation.committed_fingerprint if operation is not None else None
        ),
        "roll_constrained": False,
    }


def pp_orient(
    target="",
    axis="pca",
    to="z",
    move="",
    center="origin",
    preview=0,
    subunits="",
    force=0,
    state=1,
) -> Dict[str, object]:
    """Orient a payload from PCA, cyclic symmetry, or an explicit vector."""

    try:
        target_value = str(target).strip()
        if not target_value:
            raise ValueError("target is required; pass an object/group or explicit all")
        axis_value = str(axis).strip().lower()
        preview_value = as_bool(preview)
        force_value = as_bool(force)
        state_value = as_int(state, minimum=1, name="state")
        subunits_value = str(subunits).strip()
        commit = not preview_value
        center_origin = _center_at_origin(center)
        geometry = _load("pymolplugins.geometry.commands")
        if axis_value == "pca":
            if subunits_value:
                raise ValueError("subunits is only valid with axis=cyclic")
            result = geometry.pp_orient_pca(
                selection=target_value,
                move=move,
                target_axis=to,
                center=int(center_origin),
                force=int(force_value),
                state=state_value,
                commit=int(commit),
            )
            delegated = "pp_orient_pca"
        elif axis_value in {"cyclic", "symmetry", "symmetric"}:
            result = geometry.pp_orient_cyclic(
                selection=target_value,
                subunits=subunits_value,
                move=move,
                target_axis=to,
                center=int(center_origin),
                force=int(force_value),
                state=state_value,
                commit=int(commit),
            )
            delegated = "pp_orient_cyclic"
        else:
            if subunits_value:
                raise ValueError("subunits is only valid with axis=cyclic")
            if force_value:
                raise ValueError("force is only valid with axis=pca or axis=cyclic")
            result = _orient_explicit_vector(
                geometry, target_value, str(axis), str(to), move,
                center_origin, state_value, commit,
            )
            delegated = "explicit-vector orientation"
        operation = None
        if isinstance(result, Mapping) and result.get("committed") and not _is_error(result):
            reference = str(result["operation_id"])
            operation = journal.record_operation(
                "orientation", reference, "orientation %s" % reference
            )
            confidence = result.get("axis", {}).get("confidence", "")
            print(
                "pp_orient changed %d object(s); confidence=%s. Undo: pp_undo %s"
                % (
                    len(result.get("move_objects", ())),
                    confidence,
                    operation["operation_id"],
                )
            )
        elif isinstance(result, Mapping) and not _is_error(result):
            print("Preview only; nothing changed. Re-run pp_orient with preview=0 to apply.")
        return _response(
            "pp_orient", "changed" if commit else "preview", delegated,
            result, operation,
        )
    except Exception as exc:
        return _error(
            "pp_orient",
            exc,
            "Run pp_help orient; use axis=pca, axis=cyclic, or axis=X,Y,Z.",
        )


def _ligand_residue_choices(interactions, source: str, state: int):
    model = interactions.get_controller().cmd.get_model(
        "(%s) and organic" % source, state
    )
    residues = {
        (
            str(getattr(atom, "model", "")),
            str(getattr(atom, "segi", "")),
            str(getattr(atom, "chain", "")),
            str(getattr(atom, "resi", "")),
            str(getattr(atom, "inscode", "")),
            str(getattr(atom, "resn", "")),
        )
        for atom in getattr(model, "atom", ()) or ()
    }
    return sorted(residues)


def _contact_plan(
    interactions, source: str, focus: str, partner: str, preset: str, state: int
):
    controller = interactions.get_controller()
    source_value = interactions._resolve_public_source(controller, source, state)
    canonical = str(preset).strip().lower() or "auto"
    partner1, partner2 = "", ""
    if canonical == "auto" and focus and partner:
        canonical, partner1, partner2 = "interchain_basic", focus, partner
    elif canonical == "auto" and focus:
        canonical, partner2 = "ligand_contacts", focus
    elif canonical == "auto" and partner:
        raise ValueError("partner requires focus for a two-sided contact analysis")
    elif canonical == "auto":
        choices = _ligand_residue_choices(interactions, source_value, state)
        if len(choices) != 1:
            labels = ["%s/%s/%s/%s" % (row[0], row[2], row[3], row[5]) for row in choices]
            raise ValueError(
                "preset=auto requires exactly one organic ligand; found %d%s"
                % (len(choices), ": " + ", ".join(labels) if labels else "")
            )
        canonical = "ligand_contacts"
    else:
        canonical = INTERACTION_PRESETS.resolve(canonical).name
        ligand_presets = {
            "ligand_contacts", "ligand_quick", "ligand_site",
            "plip_overview", "plip_publication",
        }
        if canonical in ligand_presets:
            partner1, partner2 = partner, focus
        else:
            partner1, partner2 = focus, partner
    _validate_contact_partners(
        interactions, controller, source_value, partner1, partner2, state
    )
    return source_value, canonical, partner1, partner2


def _validate_contact_partners(
    interactions,
    controller,
    source: str,
    partner1: str,
    partner2: str,
    state: int,
) -> None:
    values = []
    for label, partner in (("focus/receptor", partner1), ("partner/ligand", partner2)):
        if not partner:
            values.append((label, "", 0))
            continue
        selection = interactions._bounded_selection(source, partner)
        count = interactions._count_atoms(controller.cmd, selection, state)
        if count <= 0:
            raise ValueError("%s selection has no atoms: %s" % (label, partner))
        values.append((label, selection, count))
    if partner1 and partner2:
        overlap = interactions._count_atoms(
            controller.cmd,
            "(%s) and (%s)" % (values[0][1], values[1][1]),
            state,
        )
        if overlap:
            raise ValueError("contact partners overlap by %d atom(s)" % overlap)


def _record_interaction_completion(event: Mapping[str, object]):
    """Mirror one owner-qualified interaction completion into unified undo."""

    if str(event.get("status", "")) != "completed":
        return None
    reference = str(event.get("owner_reference", "")).strip()
    if not reference:
        raise ValueError("interaction completion event has no owner reference")
    replaced = str(event.get("replaced_owner_reference", "")).strip()
    if replaced:
        journal.supersede_operation(replaced, reference)
    existing = journal.find_operation_by_reference(
        reference, include_inactive=True
    )
    if existing is not None:
        return existing
    preset = str(event.get("preset", "interaction"))
    group = str(event.get("group", ""))
    operation = journal.record_operation(
        "contacts",
        reference,
        "%s interaction manifest %s" % (preset, group),
    )
    print(
        "Interaction completed; unified undo: pp_undo %s"
        % operation["operation_id"]
    )
    return operation


def _ensure_interaction_completion_listener(interactions=None) -> str:
    """Install exactly one reload-safe listener when the owner API is present."""

    global _INTERACTION_LISTENER_MODULE, _INTERACTION_LISTENER_TOKEN
    module = (
        _load("pymolplugins.interactions.controller")
        if interactions is None
        else interactions
    )
    register = getattr(module, "register_interaction_completion_listener", None)
    unregister = getattr(module, "unregister_interaction_completion_listener", None)
    if not callable(register) or not callable(unregister):
        return ""
    if _INTERACTION_LISTENER_TOKEN and _INTERACTION_LISTENER_MODULE is not None:
        try:
            _INTERACTION_LISTENER_MODULE.unregister_interaction_completion_listener(
                _INTERACTION_LISTENER_TOKEN
            )
        except Exception:
            pass
    _INTERACTION_LISTENER_TOKEN = str(register(_record_interaction_completion))
    _INTERACTION_LISTENER_MODULE = module
    return _INTERACTION_LISTENER_TOKEN


def pp_contacts(
    source="",
    focus="",
    partner="",
    preset="auto",
    preview=0,
    group="",
    wait=0,
    state=1,
) -> Dict[str, object]:
    """Run one local/explicit interaction preset with safe partner inference."""

    try:
        parsed_state = as_int(state, minimum=1, name="state")
        preview_value = as_bool(preview)
        wait_value = as_bool(wait)
        if preview_value and wait_value:
            raise ValueError("wait is not applicable to a contacts preview")
        interactions = _load("pymolplugins.interactions.controller")
        source_value, canonical, partner1, partner2 = _contact_plan(
            interactions,
            str(source).strip(),
            str(focus).strip(),
            str(partner).strip(),
            str(preset),
            parsed_state,
        )
        plan = {
            "source": source_value,
            "preset": canonical,
            "partner1": partner1,
            "partner2": partner2,
            "group": str(group).strip(),
        }
        if preview_value:
            preview_api = getattr(interactions, "preview_interaction", None)
            if callable(preview_api):
                validation = preview_api(
                    canonical,
                    source_value,
                    partner1=partner1,
                    partner2=partner2,
                    state=parsed_state,
                    group=str(group).strip(),
                )
                result = dict(validation)
                result["partner1"] = partner1
                result["partner2"] = partner2
                result["task_plan"] = dict(plan)
            else:
                result = plan
            print(
                "Preview only; nothing changed. pp_contacts would run %s on %s."
                % (canonical, source_value)
            )
            print(
                "Next: %s"
                % _contacts_apply_hint(
                    source_value,
                    str(focus).strip(),
                    str(partner).strip(),
                    str(preset).strip().lower() or "auto",
                    str(group).strip(),
                    parsed_state,
                )
            )
            return _response(
                "pp_contacts", "preview", "preview_interaction", result
            )
        result = interactions.pp_interact_run(
            canonical,
            source_value,
            partner1=partner1,
            partner2=partner2,
            state=parsed_state,
            group=group,
            async_mode=int(not wait_value),
        )
        operation = None
        if isinstance(result, str):
            owner_api = getattr(interactions, "interaction_owner_reference", None)
            reference = str(owner_api(result)) if callable(owner_api) else result
            operation = journal.find_operation_by_reference(reference)
            if operation is None:
                operation = journal.record_operation(
                    "contacts", reference, "interaction manifest %s" % result
                )
                print("Unified undo: pp_undo %s" % operation["operation_id"])
            status = "changed"
        elif isinstance(result, Mapping):
            status = str(result.get("status", "submitted"))
        else:
            status = "changed"
        return _response("pp_contacts", status, "pp_interact_run", result, operation)
    except Exception as exc:
        return _error(
            "pp_contacts",
            exc,
            "Run pp_help interactions; specify focus or two disjoint partners when auto is ambiguous.",
        )


def _rosetta_preflight(remote, url: str, timeout: float) -> Mapping[str, object]:
    capabilities = remote.worker_request(
        "/v1/capabilities",
        url=url,
        timeout=min(3.0, timeout),
        expected_schema="pymolplugins.worker/1.0",
    )
    rosetta = capabilities.get("rosetta", {})
    health = rosetta.get("health", {}) if isinstance(rosetta, Mapping) else {}
    if not isinstance(health, Mapping) or not health.get("available"):
        detail = str(health.get("error", "PyRosetta is unavailable"))
        raise ValueError(
            "%s; install the separately licensed backend in the WSL worker environment"
            % detail
        )
    return capabilities


def pp_metrics(
    source="",
    target="",
    binder="",
    profile="interface",
    preview=0,
    wait=0,
    output_json="",
    url="",
    timeout=120.0,
) -> Dict[str, object]:
    """Submit an explicit target/binder Rosetta metric profile."""

    try:
        source_value = str(source).strip()
        if not source_value:
            raise ValueError("source is required; pass one explicit complex object/selection")
        target_value = str(target).strip()
        binder_value = str(binder).strip()
        if not target_value or not binder_value:
            raise ValueError("target and binder chain lists are required; they are never guessed")
        target_parts = [
            value for value in target_value.replace(",", " ").split() if value
        ]
        binder_parts = [
            value for value in binder_value.replace(",", " ").split() if value
        ]
        if len(set(target_parts)) != len(target_parts) or len(set(binder_parts)) != len(binder_parts):
            raise ValueError("target and binder chain lists must not contain duplicates")
        target_names = set(target_parts)
        binder_names = set(binder_parts)
        overlap = sorted(target_names.intersection(binder_names))
        if overlap:
            raise ValueError("target and binder overlap: %s" % ", ".join(overlap))
        profile_value = str(profile).strip().lower()
        if profile_value not in _METRIC_PROFILES:
            raise ValueError(
                "profile must be one of: %s" % ", ".join(sorted(_METRIC_PROFILES))
            )
        timeout_value = as_float(timeout, minimum=0.1, name="timeout")
        preview_value = as_bool(preview)
        wait_value = as_bool(wait)
        if preview_value and wait_value:
            raise ValueError("wait is not applicable to a metrics preview")
        remote = _load("pymolplugins.remote.commands")
        if preview_value:
            result = remote.preview_rosetta_metrics(
                selection=source_value,
                target_chains=target_value,
                binder_chains=binder_value,
                metrics=_METRIC_PROFILES[profile_value],
                output_json=output_json,
                url=url,
                timeout=timeout_value,
            )
            print(
                "Preview only; nothing submitted. pp_metrics would analyze %s "
                "with target=%s binder=%s."
                % (source_value, target_value, binder_value)
            )
            return _response(
                "pp_metrics",
                "preview",
                "preview_rosetta_metrics",
                result,
            )
        if wait_value:
            _rosetta_preflight(remote, str(url), timeout_value)
        function = remote.pp_rosetta_metrics if wait_value else remote.pp_rosetta_metrics_async
        result = function(
            selection=source_value,
            target_chains=target_value,
            binder_chains=binder_value,
            metrics=_METRIC_PROFILES[profile_value],
            output_json=output_json,
            url=url,
            timeout=timeout_value,
        )
        status = "completed" if wait_value else "submitted"
        return _response(
            "pp_metrics",
            status,
            function.__name__,
            result,
            default_scene_preserved=True,
        )
    except Exception as exc:
        return _error(
            "pp_metrics",
            exc,
            "Start/check the WSL worker, then pass explicit disjoint target and binder chains.",
        )


def _find_job(jobs: Mapping[str, object], job_id: str):
    active = jobs.get("active", {})
    if isinstance(active, Mapping) and job_id in active:
        return {"job_id": job_id, **dict(active[job_id])}
    history = jobs.get("history", ())
    for entry in history if isinstance(history, (list, tuple)) else ():
        if isinstance(entry, Mapping) and str(entry.get("job_id", "")) == job_id:
            return dict(entry)
    raise ValueError("unknown job: %s" % job_id)


def pp_jobs(action="list", job_id="") -> Dict[str, object]:
    """List, inspect, or request result suppression for worker jobs."""

    try:
        verb = str(action).strip() or "list"
        identifier = str(job_id).strip()
        action_words = {"list", "status", "history", "cancel"}
        if identifier and verb.lower() not in action_words:
            raise ValueError(
                "received two possible job IDs (%s, %s); pass one exact job ID"
                % (verb, identifier)
            )
        remote = _load("pymolplugins.remote.commands")
        if verb.lower() == "cancel":
            if not identifier:
                raise ValueError("cancel needs a job ID")
            result = remote.pp_worker_cancel(identifier)
            print("Cancel only suppresses result handling; backend work may continue.")
            return _response(
                "pp_jobs",
                str(result.get("status", "changed")),
                "pp_worker_cancel",
                result,
                default_scene_preserved=True,
            )
        jobs = remote.pp_worker_jobs(quiet=1)
        if verb.lower() in {"list", "status", "history"} and not identifier:
            active = jobs.get("active", {})
            history = jobs.get("history", [])
            print("Worker jobs: %d active, %d recent terminal" % (len(active), len(history)))
            for key, value in list(active.items())[:10]:
                print("  %s %-16s %s" % (key, value.get("kind"), value.get("status")))
            for value in list(history)[:5]:
                print("  %s %-16s %s" % (value.get("job_id"), value.get("kind"), value.get("status")))
            return _response("pp_jobs", "listed", "pp_worker_jobs", jobs)
        lookup = identifier or verb
        result = _find_job(jobs, lookup)
        print("Worker job %s: %s" % (lookup, result.get("status")))
        return _response(
            "pp_jobs",
            "inspected",
            "pp_worker_jobs",
            result,
            propagate_result_error=False,
        )
    except Exception as exc:
        return _error(
            "pp_jobs", exc, "Run pp_jobs to list active and recent job IDs."
        )


def _undo_command_name(entry: Mapping[str, object]) -> str:
    kind = str(entry["kind"])
    names = {
        "show": "representation snapshot API",
        "alignment": "pp_align_undo",
        "orientation": "pp_orientation_undo",
        "contacts": "pp_interact_clear",
        "style": "pp_style_revert",
    }
    try:
        return names[kind]
    except KeyError:
        raise ValueError("unsupported operation kind: %s" % kind)


def _undo_delegate(entry: Mapping[str, object], force: bool):
    kind = str(entry["kind"])
    reference = str(entry["reference"])
    if kind == "show":
        return journal.undo_show(_require_cmd(), reference, force=force)
    if kind == "alignment":
        return _load("pymolplugins.alignment.rigid").pp_align_undo(reference, int(force))
    if kind == "orientation":
        return _load("pymolplugins.geometry.commands").pp_orientation_undo(reference, int(force))
    if kind == "contacts":
        return _load("pymolplugins.interactions.controller").pp_interact_clear(reference)
    if kind == "style":
        return _load("pymolplugins.styles.registry").pp_style_revert(reference)
    raise ValueError("unsupported operation kind: %s" % kind)


def pp_undo(operation_id="", force=0, preview=0) -> Dict[str, object]:
    """Undo the latest successful Tier-1 mutation or one exact operation ID."""

    try:
        key = str(operation_id).strip()
        force_value = as_bool(force)
        preview_value = as_bool(preview)
        if key.lower() == "list":
            if force_value or preview_value:
                raise ValueError("pp_undo list does not accept force or preview")
            operations = journal.list_operations()
            print("Undoable Tier-1 operations: %d" % len(operations))
            for entry in operations[:20]:
                print("  %s %-12s %s" % (entry["operation_id"], entry["kind"], entry["summary"]))
            return _response("pp_undo", "listed", "operation journal", operations)
        entry = journal.resolve_operation(key)
        delegated = _undo_command_name(entry)
        if preview_value:
            if force_value:
                raise ValueError("force is not applicable to an undo preview")
            public = journal.resolve_operation(entry["operation_id"])
            result = {
                "operation_id": public["operation_id"],
                "kind": public["kind"],
                "reference": public["reference"],
                "summary": public["summary"],
            }
            print("Preview only; nothing changed. Would undo %s." % public["summary"])
            return _response("pp_undo", "preview", delegated, result)
        result = _undo_delegate(entry, force_value)
        if _is_error(result) or result is None:
            return _response("pp_undo", "error", delegated, result)
        journal.mark_undone(entry)
        print("pp_undo restored %s (%s)" % (entry["operation_id"], entry["kind"]))
        return _response("pp_undo", "restored", delegated, result)
    except Exception as exc:
        return _error(
            "pp_undo",
            exc,
            "Run pp_undo list; stale operations require force=1 only after review.",
        )


TASK_COMMANDS = {
    "pp_show": pp_show,
    "pp_nav": pp_nav,
    "pp_align": pp_align,
    "pp_orient": pp_orient,
    "pp_contacts": pp_contacts,
    "pp_metrics": pp_metrics,
    "pp_jobs": pp_jobs,
    "pp_undo": pp_undo,
}


def register_commands(command_api=None) -> None:
    selected = cmd if command_api is None else command_api
    if selected is None:
        return
    for name, function in TASK_COMMANDS.items():
        extend_command(selected, name, function)
    try:
        _ensure_interaction_completion_listener()
    except Exception as exc:
        print("Warning: unified async interaction undo is unavailable: %s" % exc)
    try:
        shortcut = selected.Shortcut
        selected.auto_arg[0]["pp_show"] = [
            lambda: shortcut(sorted(_REPRESENTATION_MASKS)),
            "representation",
            "",
        ]
        selected.auto_arg[1]["pp_show"] = [selected.object_sc, "target", ""]
        selected.auto_arg[0]["pp_nav"] = [
            lambda: shortcut(
                [
                    "start", "next", "prev", "next-page", "prev-page",
                    "goto", "pin", "unpin", "status", "stop",
                    "files", "groups", "objects",
                ]
            ),
            "action_or_target",
            "",
        ]
        selected.auto_arg[1]["pp_nav"] = [selected.object_sc, "target", ""]
        for name in ("pp_align", "pp_orient", "pp_contacts", "pp_metrics"):
            selected.auto_arg[0][name] = [selected.object_sc, "object_or_selection", ""]
        selected.auto_arg[1]["pp_align"] = [selected.object_sc, "reference", ""]
        selected.auto_arg[1]["pp_orient"] = [
            lambda: shortcut(["pca", "cyclic", "x", "y", "z"]),
            "axis",
            "",
        ]
        selected.auto_arg[0]["pp_jobs"] = [
            lambda: shortcut(["list", "status", "history", "cancel"]),
            "action_or_job_id",
            "",
        ]
        selected.auto_arg[0]["pp_undo"] = [
            lambda: shortcut(
                ["list"]
                + [entry["operation_id"] for entry in journal.list_operations()]
            ),
            "operation_id",
            "",
        ]
    except (AttributeError, KeyError, TypeError):
        # Headless fakes and older Hosts may expose no completion dictionaries.
        pass


register_commands()
