"""Canonical PyMOL style registry, commands, snapshots, and legacy adapters.

Load this file once in PyMOL (``run visualization/styles/style_registry.py``)
to register the ``pp_style_*`` commands.  Applying a style never changes the
view or starts a render unless the caller explicitly opts in.
"""

from dataclasses import dataclass, field
import os
import sys

from pymol import cmd

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
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", "", "none"):
        return False
    raise ValueError("Expected a boolean value, got %r" % value)


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


@dataclass
class StyleSnapshot:
    """State needed to reverse one successful style application."""

    snapshot_id: int
    preset_names: tuple
    selection: str
    settings: dict = field(default_factory=dict)
    named_colors: dict = field(default_factory=dict)
    atom_state: dict = None
    has_atom_colors: bool = False
    has_representations: bool = False
    representations: tuple = ()


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


def _capture_atom_state(command_api, selection, need_colors, need_reps):
    if not need_colors and not need_reps:
        return None, False, False
    atom_state = {}
    if need_reps:
        try:
            command_api.iterate(
                selection,
                "_pp_atom_state[(model, index)] = (color, reps)",
                space={"_pp_atom_state": atom_state},
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


def _snapshot_settings(command_api, presets, selection):
    result = {}
    for preset in presets:
        for name in preset.global_settings:
            key = (name, None)
            if key not in result:
                result[key] = _read_setting(command_api, name)
        for name in preset.selection_settings:
            key = (name, selection)
            if key not in result:
                result[key] = _read_setting(command_api, name, selection)
    return result


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


def _show_atom_groups(command_api, snapshot, representation, grouped):
    serial = 0
    for model, indices in grouped.items():
        serial += 1
        temp_name = "pp_style_restore_%d_%d" % (snapshot.snapshot_id, serial)
        try:
            command_api.select_list(temp_name, model, indices, mode="index")
            command_api.show(representation, temp_name)
        except (AttributeError, TypeError):
            for index in indices:
                command_api.show(
                    representation, _fallback_atom_selector(model, index)
                )
        finally:
            try:
                command_api.delete(temp_name)
            except Exception:
                pass


class StyleRegistry:
    """Validate, apply, snapshot, revert, and render style presets."""

    def __init__(self, command_api, presets=PRESETS):
        self.cmd = command_api
        self.presets, self.lookup = validate_presets(tuple(presets))
        self.snapshots = []
        self.next_snapshot_id = 1

    def resolve(self, expression):
        """Resolve a name or ``name+name`` composition to ordered presets."""
        if isinstance(expression, (tuple, list)):
            names = [str(item).strip() for item in expression]
        else:
            names = [item.strip() for item in str(expression).split("+")]
        if not names or any(not name for name in names):
            raise ValueError("A non-empty preset name is required")
        resolved = []
        for name in names:
            try:
                preset = self.lookup[name]
            except KeyError:
                raise ValueError(
                    "Unknown style preset %r. Available: %s"
                    % (name, ", ".join(sorted(self.presets)))
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

    def _new_snapshot(self, presets, selection):
        representations = _changed_representations(presets)
        need_colors = any(preset.color_operations for preset in presets)
        atom_state, has_colors, has_reps = _capture_atom_state(
            self.cmd, selection, need_colors, bool(representations)
        )
        snapshot = StyleSnapshot(
            snapshot_id=self.next_snapshot_id,
            preset_names=tuple(preset.name for preset in presets),
            selection=selection,
            settings=_snapshot_settings(self.cmd, presets, selection),
            named_colors=_capture_named_colors(self.cmd, presets),
            atom_state=atom_state,
            has_atom_colors=has_colors,
            has_representations=has_reps,
            representations=representations,
        )
        self.next_snapshot_id += 1
        return snapshot

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
        self.cmd.alter(
            snapshot.selection,
            "color = _pp_colors.get((model, index), color)",
            space={"_pp_colors": colors},
        )
        try:
            self.cmd.recolor()
        except (AttributeError, TypeError):
            pass

    def _restore_representations(self, snapshot):
        if not snapshot.has_representations or snapshot.atom_state is None:
            return
        for representation in snapshot.representations:
            self.cmd.hide(representation, snapshot.selection)
            grouped = _group_visible_atoms(snapshot.atom_state, representation)
            _show_atom_groups(self.cmd, snapshot, representation, grouped)

    def _restore_snapshot(self, snapshot):
        self._restore_atom_colors(snapshot)
        self._restore_representations(snapshot)
        for (name, selection), value in reversed(tuple(snapshot.settings.items())):
            if selection is None:
                self.cmd.set(name, value)
            else:
                self.cmd.set(name, value, selection)
        for name, rgb in snapshot.named_colors.items():
            if rgb is not None:
                self.cmd.set_color(name, list(rgb))

    def apply(self, expression, selection="all", snapshot=True, render=False):
        """Apply one preset/composition and optionally push a snapshot."""
        presets = self.resolve(expression)
        selection = str(selection or "all")
        record = self._new_snapshot(presets, selection) if _as_bool(snapshot) else None
        try:
            self.register_colors(tuple(preset.name for preset in presets))
            self._apply_settings(presets, selection)
            self._apply_operations(presets, selection)
            if _as_bool(render):
                defaults = presets[-1].render_defaults
                self.render(
                    defaults.get("width", 1600),
                    defaults.get("height", 1200),
                    defaults.get("filename", ""),
                )
        except Exception:
            if record is not None:
                self._restore_snapshot(record)
            raise
        if record is not None:
            self.snapshots.append(record)
            return record.snapshot_id
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
        self._restore_snapshot(expected)
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
    return description


def pp_style_apply(preset, selection="all", snapshot=1, render=0):
    """Apply a style without rendering by default and return snapshot ID."""
    snapshot_id = _REGISTRY.apply(preset, selection, snapshot, render)
    message = "Applied style %s to %s" % (preset, selection)
    if snapshot_id is not None:
        message += " (snapshot %d)" % snapshot_id
    print(message)
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
    return _REGISTRY.render(width, height, filename)


CANONICAL_COMMANDS = {
    "pp_style_list": pp_style_list,
    "pp_style_describe": pp_style_describe,
    "pp_style_apply": pp_style_apply,
    "pp_style_revert": pp_style_revert,
    "pp_style_render": pp_style_render,
}


def register_commands(command_api=cmd):
    """Register canonical commands; repeated calls are deterministic."""
    for name, function in CANONICAL_COMMANDS.items():
        command_api.extend(name, function)


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
    return pp_style_apply("rfd2_sam_palette", name or "all", snapshot, 0)


def legacy_mass_paper_rainbow(name="all", snapshot=1):
    return pp_style_apply("rfd2_paper_palette", name or "all", snapshot, 0)


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
