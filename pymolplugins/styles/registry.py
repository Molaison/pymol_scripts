"""Canonical PyMOL style registry, commands, snapshots, and legacy adapters.

Load this file once in PyMOL (``run visualization/styles/style_registry.py``)
to register the ``pp_style_*`` commands.  Applying a style never changes the
view or starts a render unless the caller explicitly opts in.
"""

from dataclasses import dataclass, field
from difflib import get_close_matches
from functools import wraps
import os
import sys
import traceback

from pymol import cmd
from pymolplugins.core.parameters import extend_command
from pymolplugins.core.pymol_groups import (
    GroupMetadataError,
    expand_group_leaves,
    object_type,
)

try:
    if not globals().get("__package__"):
        raise ImportError("PyMOL run namespace has no package context")
    from .presets import PRESETS, VALID_REPRESENTATIONS, validate_presets
except (ImportError, ValueError):
    # PyMOL's ``run`` executes files outside package context.  The file's own
    # directory is a reliable fallback even when PyMOL was launched elsewhere.
    _SCRIPT_FILE = globals().get("__script__", globals().get("__file__", ""))
    _STYLE_DIR = os.path.dirname(os.path.abspath(_SCRIPT_FILE))
    _REMOVE_STYLE_DIR = _STYLE_DIR not in sys.path
    if _REMOVE_STYLE_DIR:
        sys.path.insert(0, _STYLE_DIR)
    try:
        from presets import PRESETS, VALID_REPRESENTATIONS, validate_presets
    finally:
        if _REMOVE_STYLE_DIR:
            sys.path.remove(_STYLE_DIR)


_MODULE = sys.modules.get(__name__)
if _MODULE is not None:
    # Legacy shims use this stable key, so any load order shares one snapshot
    # stack and the exact same command callables.
    sys.modules["pp_style_registry"] = _MODULE


# PyMOL stores per-atom visibility in ``AtomInfoType.visRep``/the ``reps``
# iterate property.  These bit positions follow the upstream cRep* enum.  In
# particular, nonbonded spheres and nonbonded lines are distinct atom
# representations.  Dashes have their own object-representation bit upstream,
# but are deliberately absent from ``cRepsAtomMask`` and cannot be restored
# from per-atom ``reps`` state.
REPRESENTATION_BITS = {
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


class StyleMutationError(RuntimeError):
    """A style transaction failed with an explicit scene rollback outcome."""

    def __init__(self, message, scene_preserved):
        super().__init__(message)
        self.scene_preserved = bool(scene_preserved)


LEGACY_COLOR_ALIASES = {
    "pp_macaron_1": "color1",
    "pp_macaron_2": "color2",
    "pp_macaron_3": "color3",
    "pp_macaron_4": "color4",
    "pp_macaron_5": "color5",
    "pp_macaron_6": "color6",
    "pp_macaron_7": "color7",
    "pp_professional_blue": "blue_protein",
    "pp_professional_red": "red_protein",
    "pp_professional_green": "green_protein",
    "pp_rfd2_nitrogen": "nitrogen",
    "pp_rfd2_gray": "pymol_gray",
    "pp_rfd2_black": "pymol_black",
    "pp_rfd2_yellow": "good_yellow",
    "pp_rfd2_teal": "good_teal",
    "pp_rfd2_green": "good_green",
    "pp_rfd2_pink": "good_pink",
    "pp_rfd2_blue": "good_blue",
    "pp_rfd2_light_gray": "good_gray",
    "pp_rfd2_red": "good_red",
    "pp_rfd2_light_green": "good_light_green",
    "pp_rfd2_paper_teal": "paper_teal",
    "pp_rfd2_paper_navajo": "paper_navaho",
    "pp_rfd2_paper_melon": "paper_melon",
    "pp_rfd2_paper_pink": "paper_pink",
    "pp_rfd2_paper_purple": "paper_purple",
    "pp_rfd2_paper_lightblue": "paper_lightblue",
    "pp_rfd2_paper_blue": "paper_blue",
    "pp_rfd2_paper_darkblue": "paper_darkblue",
}


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", "", "none"):
        return False
    raise ValueError(
        "expected a boolean token (1/0, true/false, on/off, yes/no), got %r"
        % value
    )


def _positive_int(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % label)
    if result <= 0:
        raise ValueError("%s must be positive" % label)
    return result


def _selection(operation, selection):
    return operation.get("selection", "{selection}").format(selection=selection)


@dataclass(frozen=True)
class StyleTarget:
    """One validated target and the stable atom identities it resolves to."""

    requested: str
    selection: str
    kind: str
    resolved_targets: tuple
    atom_targets: tuple
    skipped_targets: tuple
    atom_keys: tuple
    atom_count: int

    def as_dict(self):
        return {
            "requested": self.requested,
            "effective_selection": self.selection,
            "kind": self.kind,
            "resolved_targets": list(self.resolved_targets),
            "atom_targets": list(self.atom_targets),
            "skipped_targets": list(self.skipped_targets),
            "atom_count": self.atom_count,
        }


def _selector_literal(value):
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def _model_selection(name):
    return "(model %s)" % _selector_literal(name)


def _models_selection(names):
    values = tuple(names)
    if not values:
        return "none"
    return "(%s)" % " or ".join(_model_selection(name) for name in values)


def _capture_atom_keys(command_api, selection, label):
    keys = set()
    try:
        command_api.iterate(
            selection,
            "_pp_atom_keys.add((model, index))",
            space={"_pp_atom_keys": keys},
        )
    except Exception as exc:
        raise ValueError("invalid style %s %r: %s" % (label, selection, exc)) from exc
    return tuple(sorted((str(model), int(index)) for model, index in keys))


def _count_target_atoms(command_api, selection, label):
    try:
        return int(command_api.count_atoms(selection))
    except Exception as exc:
        raise ValueError("invalid style %s %r: %s" % (label, selection, exc)) from exc


def _resolve_group_target(command_api, requested, capture_identity):
    try:
        leaves = tuple(expand_group_leaves(command_api, requested))
    except GroupMetadataError as exc:
        raise ValueError(
            "could not expand style target group %r: %s" % (requested, exc)
        ) from exc
    atom_targets = []
    skipped_targets = []
    atom_keys = set()
    atom_count = 0
    for name in leaves:
        if object_type(command_api, name) != "object:molecule":
            skipped_targets.append(name)
            continue
        member_selection = _model_selection(name)
        count = _count_target_atoms(command_api, member_selection, "group member")
        destination = atom_targets if count else skipped_targets
        destination.append(name)
        atom_count += count
        if count and capture_identity:
            atom_keys.update(
                _capture_atom_keys(command_api, member_selection, "group member")
            )
    if not atom_count:
        raise ValueError("style target group %r contains no atoms" % requested)
    return StyleTarget(
        requested=requested,
        selection=_models_selection(atom_targets),
        kind="group",
        resolved_targets=leaves,
        atom_targets=tuple(atom_targets),
        skipped_targets=tuple(skipped_targets),
        atom_keys=tuple(sorted(atom_keys)),
        atom_count=atom_count,
    )


def _resolve_non_group_target(
    command_api, requested, target_type, capture_identity
):
    if target_type.startswith("object:"):
        effective = _model_selection(requested)
        atom_count = _count_target_atoms(command_api, effective, "object")
        if not atom_count:
            raise ValueError("style target object %r contains no atoms" % requested)
        atom_keys = (
            _capture_atom_keys(command_api, effective, "object")
            if capture_identity
            else ()
        )
        return StyleTarget(
            requested=requested,
            selection=effective,
            kind="object",
            resolved_targets=(requested,),
            atom_targets=(requested,),
            skipped_targets=(),
            atom_keys=atom_keys,
            atom_count=atom_count,
        )

    atom_count = _count_target_atoms(command_api, requested, "selection")
    if not atom_count:
        raise ValueError("style target selection %r contains no atoms" % requested)
    atom_keys = (
        _capture_atom_keys(command_api, requested, "selection")
        if capture_identity
        else ()
    )
    try:
        atom_targets = tuple(command_api.get_object_list("(%s)" % requested))
    except Exception:
        atom_targets = tuple(dict.fromkeys(model for model, _ in atom_keys))
    return StyleTarget(
        requested=requested,
        selection=requested,
        kind="named_selection" if target_type.startswith("selection") else "selection",
        resolved_targets=atom_targets,
        atom_targets=atom_targets,
        skipped_targets=(),
        atom_keys=atom_keys,
        atom_count=atom_count,
    )


def _resolve_style_target(command_api, selection, capture_identity):
    if selection is None or not str(selection).strip():
        raise ValueError("style selection must not be empty")
    requested = str(selection).strip()
    target_type = object_type(command_api, requested)
    if target_type == "object:group":
        return _resolve_group_target(command_api, requested, capture_identity)
    return _resolve_non_group_target(
        command_api, requested, target_type, capture_identity
    )


@dataclass
class StyleSnapshot:
    """State needed to reverse one successful style application."""

    snapshot_id: int
    preset_names: tuple
    selection: str
    atom_keys: tuple = ()
    settings: dict = field(default_factory=dict)
    target_settings: dict = field(default_factory=dict)
    named_colors: dict = field(default_factory=dict)
    atom_state: dict = None
    has_atom_colors: bool = False
    has_representations: bool = False
    representations: tuple = ()


class StyleApplyResult(int):
    """Backward-compatible snapshot ID carrying a structured apply report."""

    def __new__(cls, snapshot_id, report):
        value = int.__new__(cls, int(snapshot_id))
        value.report = report
        return value

    def to_dict(self):
        return dict(self.report)


def _read_setting(command_api, name, selection=None):
    if selection is None:
        return command_api.get_setting_text(name)
    return command_api.get_setting_text(name, selection)


def _capture_named_colors(command_api, presets):
    colors = {}
    for preset in presets:
        for name in preset.colors:
            if name in colors:
                continue
            try:
                color_index = command_api.get_color_index(name)
            except (AttributeError, TypeError):
                color_index = None
            if color_index is not None and int(color_index) < 0:
                # ``get_color_tuple`` prints a cmd-Error for a missing color
                # instead of raising. Probe the quiet index API first so a
                # normal first application does not emit false errors.
                colors[name] = None
                continue
            try:
                rgb = command_api.get_color_tuple(name)
                colors[name] = tuple(rgb) if rgb is not None else None
            except Exception:
                # PyMOL has no supported API for deleting a newly introduced
                # named color.  None records that limitation explicitly.
                colors[name] = None
    return colors


def _capture_atom_state(
    command_api, selection, atom_keys, need_colors, need_reps
):
    if not need_colors and not need_reps:
        return None, False, False
    expected = set(atom_keys)
    atom_state = {}
    if need_reps:
        try:
            command_api.iterate(
                selection,
                "_pp_atom_state[(model, index)] = (color, reps)",
                space={"_pp_atom_state": atom_state},
            )
            if set(atom_state) != expected:
                raise RuntimeError(
                    "style target changed while its snapshot was being captured"
                )
            return atom_state, need_colors, True
        except Exception:
            atom_state = {}
    if need_colors:
        command_api.iterate(
            selection,
            "_pp_atom_state[(model, index)] = (color, None)",
            space={"_pp_atom_state": atom_state},
        )
        if set(atom_state) != expected:
            raise RuntimeError(
                "style target changed while its snapshot was being captured"
            )
        return atom_state, True, False
    return None, False, False


def _capture_atom_state_by_keys(
    command_api, atom_keys, need_colors, need_reps
):
    """Capture live atom state by immutable ``(model, index)`` identities."""

    if not need_colors and not need_reps:
        return None, False, False
    expected = set(atom_keys)
    if not expected:
        raise RuntimeError("style snapshot has no stable atom identities")
    selection = _models_selection(sorted({model for model, _ in expected}))
    all_state = {}
    if need_reps:
        try:
            command_api.iterate(
                selection,
                "_pp_atom_state[(model, index)] = (color, reps)",
                space={"_pp_atom_state": all_state},
            )
            atom_state = {
                key: value for key, value in all_state.items() if key in expected
            }
            if set(atom_state) != expected:
                raise RuntimeError(
                    "style target atoms changed since the snapshot was captured"
                )
            return atom_state, need_colors, True
        except Exception:
            all_state = {}
    if need_colors:
        command_api.iterate(
            selection,
            "_pp_atom_state[(model, index)] = (color, None)",
            space={"_pp_atom_state": all_state},
        )
        atom_state = {
            key: value for key, value in all_state.items() if key in expected
        }
        if set(atom_state) != expected:
            raise RuntimeError(
                "style target atoms changed since the snapshot was captured"
            )
        return atom_state, True, False
    return None, False, False


def _changed_representations(presets):
    names = set()
    for preset in presets:
        for operation in preset.representation_operations:
            representation = operation["representation"]
            if representation == "everything":
                names.update(REPRESENTATION_BITS)
            else:
                names.add(representation)
    return tuple(sorted(names))


def _needs_stable_atom_identity(presets):
    return any(
        preset.selection_settings
        or preset.color_operations
        or preset.representation_operations
        for preset in presets
    )


def _snapshot_settings(command_api, presets):
    result = {}
    for preset in presets:
        for name in preset.global_settings:
            key = (name, None)
            if key not in result:
                result[key] = _read_setting(command_api, name)
    return result


def _target_setting_names(presets):
    return tuple(
        sorted({name for preset in presets for name in preset.selection_settings})
    )


def _capture_target_settings(command_api, atom_keys, names):
    values = {}
    for model, index in atom_keys:
        selection = _fallback_atom_selector(model, index)
        for name in names:
            values[(model, int(index), name)] = _read_setting(
                command_api, name, selection
            )
    return values


def _group_visible_atoms(atom_state, representation):
    bit = REPRESENTATION_BITS[representation]
    grouped = {}
    for (model, index), (_, reps) in atom_state.items():
        if reps is not None and int(reps) & bit:
            grouped.setdefault(model, []).append(int(index))
    return grouped


def _fallback_atom_selector(model, index):
    escaped = str(model).replace("\\", "\\\\").replace('"', '\\"')
    return '(model "%s" and index %d)' % (escaped, int(index))


def _capture_current_settings(command_api, snapshot):
    """Capture the current values for exactly the fields owned by a snapshot."""

    current = {}
    for name, selection in snapshot.settings:
        if selection is None:
            current[(name, None)] = _read_setting(command_api, name)
        else:
            # Compatibility for process-local snapshots created by an older
            # registry instance before per-atom target settings were added.
            identity_selection = _fallback_atom_selector(*snapshot.atom_keys[0])
            current[(name, selection)] = _read_setting(
                command_api, name, identity_selection
            )
    return current


def _group_atom_keys(atom_keys):
    grouped = {}
    for model, index in atom_keys:
        grouped.setdefault(str(model), []).append(int(index))
    return grouped


def _apply_to_atom_groups(
    command_api, snapshot, operation_name, grouped, callback
):
    serial = 0
    for model, indices in grouped.items():
        serial += 1
        temp_name = "pp_style_restore_%d_%s_%d" % (
            snapshot.snapshot_id,
            operation_name,
            serial,
        )
        try:
            command_api.select_list(temp_name, model, indices, mode="index")
        except (AttributeError, TypeError):
            for index in indices:
                callback(_fallback_atom_selector(model, index))
        else:
            try:
                callback(temp_name)
            finally:
                try:
                    command_api.delete(temp_name)
                except Exception:
                    pass


def _effect_inventory(presets):
    return {
        "global_settings": sorted(
            {name for preset in presets for name in preset.global_settings}
        ),
        "named_colors": sorted(
            {name for preset in presets for name in preset.colors}
        ),
        "target_settings": sorted(
            {name for preset in presets for name in preset.selection_settings}
        ),
        "color_operations": [
            operation["action"]
            for preset in presets
            for operation in preset.color_operations
        ],
        "representation_operations": [
            "%s:%s" % (operation["action"], operation["representation"])
            for preset in presets
            for operation in preset.representation_operations
        ],
    }


def _revert_limitations(snapshot_enabled, inventory):
    if snapshot_enabled:
        limitations = [
            "revert is LIFO and snapshots are process-local, not stored in a PSE",
            "deleted, replaced, or renamed target objects cannot be safely restored",
        ]
        if inventory["named_colors"]:
            limitations.append(
                "new named colors remain registered because PyMOL has no color-delete API"
            )
        if inventory["target_settings"]:
            limitations.append(
                "target-scoped settings are restored per atom by captured model/index identity"
            )
    else:
        limitations = ["snapshot was disabled; this successful apply cannot be reverted"]
        if inventory["named_colors"]:
            limitations.append("registered named colors remain in the PyMOL process")
    return limitations


def _style_effect_report(
    presets, target, snapshot_enabled, snapshot_id, render_requested
):
    inventory = _effect_inventory(presets)
    return {
        "schema": "pymolplugins.style_apply/1.0",
        "presets": [preset.name for preset in presets],
        "target": target.as_dict(),
        "global_effects": {
            "settings": inventory["global_settings"],
            "named_colors_registered": inventory["named_colors"],
            "render_requested": bool(render_requested),
        },
        "target_effects": {
            "settings": inventory["target_settings"],
            "atom_colors": inventory["color_operations"],
            "representations": inventory["representation_operations"],
        },
        "snapshot_id": snapshot_id,
        "revertible": bool(snapshot_enabled),
        "revert_limitations": _revert_limitations(snapshot_enabled, inventory),
    }


class StyleRegistry:
    """Validate, apply, snapshot, revert, and render style presets."""

    def __init__(self, command_api, presets=PRESETS):
        self.cmd = command_api
        self.presets, self.lookup = validate_presets(tuple(presets))
        self.snapshots = []
        self.next_snapshot_id = 1
        self.last_application = None

    def resolve(self, expression):
        """Resolve a name or ``name+name`` composition to ordered presets."""
        if isinstance(expression, (tuple, list)):
            names = [str(item).strip().lower() for item in expression]
        else:
            names = [item.strip().lower() for item in str(expression).split("+")]
        if not names or any(not name for name in names):
            raise ValueError("A non-empty preset name is required")
        resolved = []
        for name in names:
            try:
                preset = self.lookup[name]
            except KeyError:
                suggestions = get_close_matches(
                    name, sorted(self.lookup), n=3, cutoff=0.55
                )
                hint = (
                    "; did you mean %s?" % ", ".join(suggestions)
                    if suggestions
                    else ""
                )
                raise ValueError(
                    "Unknown style preset %r%s. Available: %s"
                    % (name, hint, ", ".join(sorted(self.presets)))
                )
            if preset not in resolved:
                resolved.append(preset)
        return tuple(resolved)

    def list_presets(self, category=""):
        """Return canonical preset names, optionally filtered by category."""
        category = str(category or "").strip().lower()
        if category and category not in ("lighting", "palette", "scene", "render"):
            raise ValueError("Unknown style category %r" % category)
        return [
            name
            for name, preset in sorted(self.presets.items())
            if not category or preset.category == category
        ]

    def describe(self, name):
        """Return one canonical preset description as a plain dictionary."""
        presets = self.resolve(name)
        if len(presets) != 1:
            raise ValueError("Describe accepts one preset, not a composition")
        return presets[0].as_dict()

    def register_colors(self, expression, legacy_aliases=False):
        """Register preset colors without changing atoms or scene settings."""
        presets = self.resolve(expression)
        for preset in presets:
            for name, rgb in preset.colors.items():
                self.cmd.set_color(name, list(rgb))
                alias = LEGACY_COLOR_ALIASES.get(name) if legacy_aliases else None
                if alias:
                    self.cmd.set_color(alias, list(rgb))

    def _new_snapshot(self, presets, target, consume_id=True):
        representations = _changed_representations(presets)
        need_colors = any(preset.color_operations for preset in presets)
        atom_state, has_colors, has_reps = _capture_atom_state(
            self.cmd,
            target.selection,
            target.atom_keys,
            need_colors,
            bool(representations),
        )
        if representations and not has_reps:
            raise RuntimeError(
                "this PyMOL build cannot capture atom representation state; "
                "refusing a non-revertible representation apply"
            )
        snapshot = StyleSnapshot(
            snapshot_id=self.next_snapshot_id,
            preset_names=tuple(preset.name for preset in presets),
            selection=target.selection,
            atom_keys=target.atom_keys,
            settings=_snapshot_settings(self.cmd, presets),
            target_settings=_capture_target_settings(
                self.cmd,
                target.atom_keys,
                _target_setting_names(presets),
            ),
            named_colors=_capture_named_colors(self.cmd, presets),
            atom_state=atom_state,
            has_atom_colors=has_colors,
            has_representations=has_reps,
            representations=representations,
        )
        if consume_id:
            self.next_snapshot_id += 1
        return snapshot

    def _capture_current_like(self, snapshot):
        """Capture the styled state needed to roll back a failed revert."""

        atom_state, has_colors, has_reps = _capture_atom_state_by_keys(
            self.cmd,
            snapshot.atom_keys,
            snapshot.has_atom_colors,
            snapshot.has_representations,
        )
        if snapshot.has_representations and not has_reps:
            raise RuntimeError(
                "this PyMOL build cannot capture the current representation state; "
                "revert was not started"
            )
        if snapshot.has_atom_colors and not has_colors:
            raise RuntimeError(
                "this PyMOL build cannot capture the current atom colors; "
                "revert was not started"
            )
        presets = tuple(self.presets[name] for name in snapshot.preset_names)
        return StyleSnapshot(
            snapshot_id=snapshot.snapshot_id,
            preset_names=snapshot.preset_names,
            selection=snapshot.selection,
            atom_keys=snapshot.atom_keys,
            settings=_capture_current_settings(self.cmd, snapshot),
            target_settings=_capture_target_settings(
                self.cmd,
                snapshot.atom_keys,
                sorted({key[2] for key in snapshot.target_settings}),
            ),
            named_colors=_capture_named_colors(self.cmd, presets),
            atom_state=atom_state,
            has_atom_colors=has_colors,
            has_representations=has_reps,
            representations=snapshot.representations,
        )

    def _apply_settings(self, presets, selection):
        for preset in presets:
            for name, value in preset.global_settings.items():
                self.cmd.set(name, value)
            for name, value in preset.selection_settings.items():
                self.cmd.set(name, value, selection)

    def _apply_operations(self, presets, selection):
        for preset in presets:
            for operation in preset.color_operations:
                target = _selection(operation, selection)
                if operation["action"] == "color":
                    self.cmd.color(operation["color"], target)
                else:
                    self.cmd.spectrum(
                        expression=operation.get("expression", "count"),
                        palette=" ".join(operation["palette"]),
                        selection=target,
                        byres=int(operation.get("byres", 0)),
                        quiet=1,
                    )
            for operation in preset.representation_operations:
                function = getattr(self.cmd, operation["action"])
                function(operation["representation"], _selection(operation, selection))

    def _restore_atom_colors(self, snapshot):
        if not snapshot.has_atom_colors or snapshot.atom_state is None:
            return
        colors = {key: value[0] for key, value in snapshot.atom_state.items()}
        _apply_to_atom_groups(
            self.cmd,
            snapshot,
            "colors",
            _group_atom_keys(snapshot.atom_keys),
            lambda selection: self.cmd.alter(
                selection,
                "color = _pp_colors.get((model, index), color)",
                space={"_pp_colors": colors},
            ),
        )
        try:
            self.cmd.recolor()
        except (AttributeError, TypeError):
            pass

    def _restore_representations(self, snapshot):
        if not snapshot.has_representations or snapshot.atom_state is None:
            return
        all_atoms = _group_atom_keys(snapshot.atom_keys)
        for representation in snapshot.representations:
            _apply_to_atom_groups(
                self.cmd,
                snapshot,
                "hide_%s" % representation,
                all_atoms,
                lambda selection, value=representation: self.cmd.hide(
                    value, selection
                ),
            )
            grouped = _group_visible_atoms(snapshot.atom_state, representation)
            _apply_to_atom_groups(
                self.cmd,
                snapshot,
                "show_%s" % representation,
                grouped,
                lambda selection, value=representation: self.cmd.show(
                    value, selection
                ),
            )

    def _restore_target_setting(self, snapshot, name, value):
        _apply_to_atom_groups(
            self.cmd,
            snapshot,
            "setting_%s" % name,
            _group_atom_keys(snapshot.atom_keys),
            lambda selection: self.cmd.set(name, value, selection),
        )

    def _restore_exact_target_settings(self, snapshot):
        grouped = {}
        for (model, index, name), value in snapshot.target_settings.items():
            grouped.setdefault((name, value), []).append((model, int(index)))
        for serial, ((name, value), atom_keys) in enumerate(
            grouped.items(), start=1
        ):
            _apply_to_atom_groups(
                self.cmd,
                snapshot,
                "setting_%s_%d" % (name, serial),
                _group_atom_keys(atom_keys),
                lambda selection, setting=name, current=value: self.cmd.set(
                    setting, current, selection
                ),
            )

    def _restore_snapshot(self, snapshot):
        self._restore_atom_colors(snapshot)
        self._restore_representations(snapshot)
        for (name, selection), value in reversed(tuple(snapshot.settings.items())):
            if selection is None:
                self.cmd.set(name, value)
            else:
                self._restore_target_setting(snapshot, name, value)
        self._restore_exact_target_settings(snapshot)
        for name, rgb in snapshot.named_colors.items():
            if rgb is not None:
                self.cmd.set_color(name, list(rgb))

    def apply(self, expression, selection="all", snapshot=True, render=False):
        """Apply atomically and optionally return a report-bearing snapshot ID.

        The default return value is an ``int`` subclass for compatibility with
        existing scripts.  Its ``report`` attribute separates global effects,
        target-scoped effects, resolved group members, and revert limitations.
        With ``snapshot=0`` the historical ``None`` return is preserved and the
        same report is available as ``last_application``.
        """
        self.last_application = None
        presets = self.resolve(expression)
        target = _resolve_style_target(
            self.cmd, selection, _needs_stable_atom_identity(presets)
        )
        snapshot_enabled = _as_bool(snapshot)
        render_requested = _as_bool(render)
        # A transient snapshot is always captured so a failed apply is atomic,
        # even when the caller opts out of retaining a successful snapshot.
        record = self._new_snapshot(presets, target, consume_id=False)
        try:
            self.register_colors(tuple(preset.name for preset in presets))
            self._apply_settings(presets, target.selection)
            self._apply_operations(presets, target.selection)
            if render_requested:
                defaults = presets[-1].render_defaults
                self.render(
                    defaults.get("width", 1600),
                    defaults.get("height", 1200),
                    defaults.get("filename", ""),
                )
        except Exception as exc:
            try:
                self._restore_snapshot(record)
            except Exception as rollback_exc:
                raise StyleMutationError(
                    "style apply failed: %s; rollback failed: %s"
                    % (exc, rollback_exc),
                    scene_preserved=False,
                ) from exc
            irreversible_colors = sorted(
                name for name, rgb in record.named_colors.items() if rgb is None
            )
            if irreversible_colors:
                raise StyleMutationError(
                    "style apply failed: %s; rollback limitation: newly registered "
                    "colors may remain because PyMOL has no color-delete API (%s)"
                    % (exc, ", ".join(irreversible_colors)),
                    scene_preserved=False,
                ) from exc
            raise
        snapshot_id = record.snapshot_id if snapshot_enabled else None
        report = _style_effect_report(
            presets,
            target,
            snapshot_enabled,
            snapshot_id,
            render_requested,
        )
        self.last_application = report
        if snapshot_enabled:
            self.next_snapshot_id += 1
            self.snapshots.append(record)
            return StyleApplyResult(record.snapshot_id, report)
        return None

    def revert(self, snapshot_id=None):
        """Restore and pop the top snapshot; explicit IDs must also be top."""
        if not self.snapshots:
            return None
        expected = self.snapshots[-1]
        if snapshot_id not in (None, "") and int(snapshot_id) != expected.snapshot_id:
            raise ValueError(
                "Style snapshots are LIFO; next snapshot is %d" % expected.snapshot_id
            )
        current = self._capture_current_like(expected)
        try:
            self._restore_snapshot(expected)
        except Exception as exc:
            try:
                self._restore_snapshot(current)
            except Exception as rollback_exc:
                raise StyleMutationError(
                    "style revert failed: %s; rollback to the pre-revert styled "
                    "state also failed: %s; snapshot %d was retained"
                    % (exc, rollback_exc, expected.snapshot_id),
                    scene_preserved=False,
                ) from exc
            raise StyleMutationError(
                "style revert failed: %s; restored the pre-revert styled state; "
                "snapshot %d was retained and can be retried"
                % (exc, expected.snapshot_id),
                scene_preserved=True,
            ) from exc
        self.snapshots.pop()
        return expected.snapshot_id

    def render(self, width=1600, height=1200, filename=""):
        """Run the sole ray/PNG side-effect entry point."""
        width = _positive_int(width, "width")
        height = _positive_int(height, "height")
        filename = str(filename or "").strip()
        if filename:
            self.cmd.png(filename, width=width, height=height, ray=1, quiet=0)
            return filename
        self.cmd.ray(width, height)
        return (width, height)


_REGISTRY = StyleRegistry(cmd)


def pp_style_list(category=""):
    """List canonical presets, optionally within one category."""
    names = _REGISTRY.list_presets(category)
    for name in names:
        preset = _REGISTRY.presets[name]
        print("%-34s %-8s %s" % (name, preset.category, preset.description))
    return names


def pp_style_describe(preset):
    """Print and return one preset's declarative description."""
    description = _REGISTRY.describe(preset)
    print("%s [%s]" % (description["name"], description["category"]))
    print(description["description"])
    if description["aliases"]:
        print("aliases: %s" % ", ".join(description["aliases"]))
    effects = description["effects"]
    global_effects = effects["global"]
    target_effects = effects["target_scoped"]
    print(
        "global effects: settings=%s; named_colors_registered=%d"
        % (
            ",".join(global_effects["settings"]) or "none",
            len(global_effects["named_colors_registered"]),
        )
    )
    print(
        "target-scoped effects: settings=%s; atom_colors=%s; representations=%s"
        % (
            ",".join(target_effects["settings"]) or "none",
            ",".join(target_effects["atom_colors"]) or "none",
            ",".join(target_effects["representations"]) or "none",
        )
    )
    return description


def pp_style_apply(preset, selection="all", snapshot=1, render=0):
    """Apply without rendering by default; return a report-bearing snapshot ID."""
    pp_style_apply.last_report = None
    snapshot_id = _REGISTRY.apply(preset, selection, snapshot, render)
    report = _REGISTRY.last_application
    target = report["target"]
    message = "Applied style %s to %s" % (preset, target["requested"])
    if snapshot_id is not None:
        message += " (snapshot %d)" % snapshot_id
    print(message)
    if snapshot_id is not None:
        print("Undo: pp_style_revert %d" % snapshot_id)
    else:
        print("Undo: unavailable (snapshot=0)")
    resolved = ", ".join(target["resolved_targets"]) or "(selection expression)"
    print(
        "Resolved target: kind=%s atoms=%d names=%s"
        % (target["kind"], target["atom_count"], resolved)
    )
    if target["skipped_targets"]:
        print(
            "Skipped non-atom group members: %s"
            % ", ".join(target["skipped_targets"])
        )
    global_effects = report["global_effects"]
    global_parts = []
    if global_effects["settings"]:
        global_parts.append("settings=" + ",".join(global_effects["settings"]))
    if global_effects["named_colors_registered"]:
        global_parts.append(
            "named_colors_registered=%d"
            % len(global_effects["named_colors_registered"])
        )
    if global_effects["render_requested"]:
        global_parts.append("render=requested")
    print("Global effects: %s" % ("; ".join(global_parts) or "none"))
    target_effects = report["target_effects"]
    target_parts = []
    if target_effects["settings"]:
        target_parts.append("settings=" + ",".join(target_effects["settings"]))
    if target_effects["atom_colors"]:
        target_parts.append(
            "atom_colors=" + ",".join(target_effects["atom_colors"])
        )
    if target_effects["representations"]:
        target_parts.append(
            "representations=" + ",".join(target_effects["representations"])
        )
    print("Target-scoped effects: %s" % ("; ".join(target_parts) or "none"))
    print("Revert limitations: %s" % "; ".join(report["revert_limitations"]))
    pp_style_apply.last_report = report
    return snapshot_id


def pp_style_revert(snapshot_id=""):
    """Revert the top style snapshot, optionally asserting its ID."""
    reverted = _REGISTRY.revert(snapshot_id)
    if reverted is None:
        print("No style snapshot to revert")
    else:
        print("Reverted style snapshot %d" % reverted)
    return reverted


def pp_style_render(width=1600, height=1200, filename=""):
    """Ray trace the current scene, optionally writing a PNG."""
    result = _REGISTRY.render(width, height, filename)
    if isinstance(result, str):
        print("Rendered PNG: %s" % result)
    else:
        print("Rendered in viewer: %d x %d" % result)
    return result


CANONICAL_COMMANDS = {
    "pp_style_list": pp_style_list,
    "pp_style_describe": pp_style_describe,
    "pp_style_apply": pp_style_apply,
    "pp_style_revert": pp_style_revert,
    "pp_style_render": pp_style_render,
}


def _style_console_error_text(exc):
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _style_console_guard(command_name, function):
    """Return actionable error records at the PyMOL console boundary."""

    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            message = _style_console_error_text(exc)
            scene_preserved = bool(getattr(exc, "scene_preserved", True))
            expected = False
            if isinstance(exc, StyleMutationError):
                if scene_preserved:
                    action = (
                        "The pre-command styled scene was restored. Fix the "
                        "reported Host operation, then retry pp_style_revert "
                        "with the same retained snapshot ID."
                    )
                else:
                    action = (
                        "The style transaction may be partial. Inspect atom colors, "
                        "representations, and settings before retrying."
                    )
            elif isinstance(exc, (KeyError, ValueError)):
                expected = True
                if command_name == "pp_style_revert":
                    action = (
                        "Run pp_style_revert with no ID to restore the newest "
                        "snapshot, or pass that exact newest snapshot ID."
                    )
                elif command_name == "pp_style_render":
                    action = (
                        "Use positive integer width/height values and a writable "
                        "PNG filename, or omit filename to render in the viewer."
                    )
                else:
                    action = (
                        "Run pp_style_list, then pp_style_describe PRESET; retry "
                        "with an exact preset and non-empty object/group selection."
                    )
            elif isinstance(exc, OSError):
                expected = True
                action = "Check the output path and permissions, then retry."
            else:
                action = (
                    "The prior scene was restored; inspect the traceback and retry."
                    if scene_preserved
                    else "Inspect the traceback and current scene before retrying."
                )
            print("%s error: %s" % (command_name, message))
            print(
                "Scene %s. Action: %s"
                % ("preserved" if scene_preserved else "may be partial", action)
            )
            if not expected:
                traceback.print_exc()
            return {
                "schema": "pymolplugins.style_command/1.0",
                "status": "error",
                "command": command_name,
                "error": message,
                "action": action,
                "scene_preserved": scene_preserved,
            }

    return guarded


def register_commands(command_api=cmd):
    """Register canonical commands; repeated calls are deterministic."""
    for name, function in CANONICAL_COMMANDS.items():
        extend_command(command_api, name, _style_console_guard(name, function))


# Legacy adapters intentionally contain no direct PyMOL side effects.  They
# only select arguments and forward to canonical registry entry points.
def legacy_publication_style(selection="all", snapshot=1, render=0):
    return pp_style_apply("publication_light", selection, snapshot, render)


def legacy_macaron_style(selection="polymer", ray=0, color_scheme="default"):
    scheme = str(color_scheme or "default").strip().lower()
    presets = {
        "default": "macaron_default",
        "warm": "macaron_warm",
        "cool": "macaron_cool",
    }
    if scheme not in presets:
        raise ValueError("color_scheme must be default, warm, or cool")
    snapshot_id = pp_style_apply(presets[scheme], selection, 1, 0)
    if _as_bool(ray):
        defaults = _REGISTRY.presets[presets[scheme]].render_defaults
        pp_style_render(defaults["width"], defaults["height"])
    return snapshot_id


def legacy_ink_wash_style(ray_size=0, selection="all"):
    snapshot_id = pp_style_apply("ink_wash", selection, 1, 0)
    if int(ray_size or 0) > 0:
        pp_style_render(ray_size, ray_size)
    return snapshot_id


def legacy_cartoon_style(selection="all", snapshot=1, render=0):
    return pp_style_apply("high_contrast_outline", selection, snapshot, render)


def legacy_watercolor_style(selection="all", snapshot=1, render=0):
    return pp_style_apply("ink_wash", selection, snapshot, render)


def legacy_minimal_style(selection="all", snapshot=1, render=0):
    return pp_style_apply("minimal_light", selection, snapshot, render)


def legacy_apply_protein_colors(selection="all", snapshot=1):
    return pp_style_apply("professional_secondary_structure", selection, snapshot, 0)


def legacy_render_publication(width=1200, height=1200, dpi=None, filename=""):
    # ``dpi`` is accepted solely for old command lines.  PyMOL's ray command
    # does not consume it, so it is neither forwarded nor reported as applied.
    pp_style_apply("publication_light", "all", 1, 0)
    return pp_style_render(width, height, filename)


def legacy_quick_preset(style_name="publication", selection="all", snapshot=1, render=0):
    return pp_style_apply(style_name, selection, snapshot, render)


def legacy_setup_colors():
    return _REGISTRY.register_colors(
        ("macaron_default", "professional_secondary_structure"),
        legacy_aliases=True,
    )


def legacy_get_colors():
    return _REGISTRY.register_colors(
        ("rfd2_sam_palette", "rfd2_paper_palette"), legacy_aliases=True
    )


def legacy_get_lighting(selection="all", snapshot=1):
    return pp_style_apply("rfd2_lighting", selection, snapshot, 0)


def legacy_mass_rainbow(name="all", snapshot=1):
    # Unlike the old implementation, ``name`` is respected and never replaced
    # by an implicit loop variable.
    return pp_style_apply("rfd2_sam_palette", name, snapshot, 0)


def legacy_mass_paper_rainbow(name="all", snapshot=1):
    return pp_style_apply("rfd2_paper_palette", name, snapshot, 0)


LEGACY_COMMANDS = {
    "publication_style": legacy_publication_style,
    "pbs": legacy_publication_style,
    "macaron_style": legacy_macaron_style,
    "macaron": legacy_macaron_style,
    "ink_wash_style": legacy_ink_wash_style,
    "cartoon_style": legacy_cartoon_style,
    "watercolor_style": legacy_watercolor_style,
    "watercolor": legacy_watercolor_style,
    "minimal_style": legacy_minimal_style,
    "minimal": legacy_minimal_style,
    "apply_protein_colors": legacy_apply_protein_colors,
    "render_publication": legacy_render_publication,
    "quick_preset": legacy_quick_preset,
    "preset": legacy_quick_preset,
    "get_colors": legacy_get_colors,
    "get_lighting": legacy_get_lighting,
    "mass_rainbow": legacy_mass_rainbow,
    "mass_paper_rainbow": legacy_mass_paper_rainbow,
    "mass_paper_rainbow_sel": legacy_mass_paper_rainbow,
}


def register_legacy_commands(names, command_api=cmd):
    """Register selected compatibility names using stable central callables."""
    for name in names:
        try:
            function = LEGACY_COMMANDS[name]
        except KeyError:
            raise ValueError("Unknown legacy style command %r" % name)
        command_api.extend(name, function)


register_commands()
