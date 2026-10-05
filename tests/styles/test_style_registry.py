import importlib.util
import io
import inspect
import os
from pathlib import Path
import re
import runpy
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

from pymolplugins.styles.presets import StylePreset, validate_presets


ROOT = Path(__file__).resolve().parents[2]
STYLE_DIR = ROOT / "visualization" / "styles"
PLUGIN_DIR = ROOT / "visualization" / "plugins"


REP_BITS = {
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


class FakeCmd:
    def __init__(self):
        self.settings = {
            "ambient": "0.25",
            "bg_rgb": (0.1, 0.2, 0.3),
            "specular": "0.75",
            "stick_radius": "0.2",
        }
        self.selection_settings = {("stick_radius", "all"): "0.15"}
        self.named_colors = {"pp_test_color": (0.9, 0.8, 0.7)}
        self.color_indices = {"pp_test_color": 101}
        self.get_color_tuple_calls = []
        self.next_color_index = 102
        self.atoms = [
            {
                "model": "obj_a",
                "index": 1,
                "color": 7,
                "reps": REP_BITS["cartoon"] | REP_BITS["lines"],
            },
            {
                "model": "obj_a",
                "index": 2,
                "color": 8,
                "reps": REP_BITS["lines"],
            },
        ]
        self.atom_settings = {
            ("obj_a", 1, "stick_radius"): "0.15",
            ("obj_a", 2, "stick_radius"): "0.15",
        }
        self.object_types = {"obj_a": "object:molecule"}
        self.parents = {"obj_a": ""}
        self.selections = {}
        self.extended = {}
        self.ray_calls = []
        self.png_calls = []
        self.zoom_calls = []
        self.spectrum_calls = []
        self.set_calls = []
        self.iterate_calls = []

    def extend(self, name, function=None):
        if function is None:
            def decorator(decorated):
                self.extended[decorated.__name__] = decorated
                return decorated

            return decorator
        self.extended[name] = function
        return function

    def get_setting_text(self, name, selection=None):
        if selection is not None:
            atoms = self._selected(selection)
            if atoms:
                key = (atoms[0]["model"], atoms[0]["index"], name)
                return self.atom_settings.get(key, self.settings.get(name, "0"))
            return self.selection_settings.get(
                (name, selection), self.settings.get(name, "0")
            )
        return self.settings.get(name, "0")

    def set(self, name, value, selection=None):
        self.set_calls.append((name, value, selection))
        if selection is None:
            self.settings[name] = value
        else:
            self.selection_settings[(name, selection)] = value
            for atom in self._selected(selection):
                self.atom_settings[(atom["model"], atom["index"], name)] = value

    def get_color_tuple(self, name):
        self.get_color_tuple_calls.append(name)
        if name not in self.named_colors:
            raise ValueError("unknown color")
        return self.named_colors[name]

    def get_color_index(self, name):
        return self.color_indices.get(name, -1)

    def set_color(self, name, rgb):
        self.named_colors[name] = tuple(float(value) for value in rgb)
        if name not in self.color_indices:
            self.color_indices[name] = self.next_color_index
            self.next_color_index += 1

    def _selected(self, selection):
        if selection in self.selections:
            keys = self.selections[selection]
            return [atom for atom in self.atoms if (atom["model"], atom["index"]) in keys]
        text = str(selection)
        if text.strip().lower() == "none":
            return []
        models = set(re.findall(r'model\s+"((?:\\.|[^"])*)"', text))
        if models:
            selected = [atom for atom in self.atoms if atom["model"] in models]
            indices = {int(value) for value in re.findall(r"\bindex\s+(\d+)", text)}
            if indices:
                selected = [atom for atom in selected if atom["index"] in indices]
            return selected
        if text in self.object_types and self.object_types[text] != "object:group":
            return [atom for atom in self.atoms if atom["model"] == text]
        if text in ("all", "polymer") or "all" in text:
            return list(self.atoms)
        return []

    def count_atoms(self, selection):
        return len(self._selected(selection))

    def get_type(self, name):
        if name in self.selections:
            return "selection"
        return self.object_types.get(name, "")

    def get_names(self, mode="all"):
        return list(self.object_types)

    def get_object_list(self, selection="all"):
        return list(dict.fromkeys(atom["model"] for atom in self._selected(selection)))

    def get_session(self, *args, **kwargs):
        records = []
        for name in self.object_types:
            records.append([name, 0, 0, 0, 0, 0, self.parents.get(name, "")])
        return {"names": records}

    def iterate(self, selection, expression, space=None):
        self.iterate_calls.append((selection, expression))
        for atom in self._selected(selection):
            namespace = dict(space or {})
            namespace.update(atom)
            exec(expression, {}, namespace)

    def alter(self, selection, expression, space=None):
        for atom in self._selected(selection):
            namespace = dict(space or {})
            namespace.update(atom)
            exec(expression, {}, namespace)
            atom["color"] = namespace["color"]

    def recolor(self):
        return None

    def spectrum(self, expression, palette, selection, byres=0, quiet=1):
        names = palette.split()
        self.spectrum_calls.append((expression, tuple(names), selection, byres))
        for offset, atom in enumerate(self._selected(selection)):
            atom["color"] = self.color_indices[names[offset % len(names)]]

    def color(self, color, selection):
        for atom in self._selected(selection):
            atom["color"] = self.color_indices[color]

    def show(self, representation, selection):
        bit = REP_BITS[representation]
        for atom in self._selected(selection):
            atom["reps"] |= bit

    def hide(self, representation, selection):
        if representation == "everything":
            for atom in self._selected(selection):
                atom["reps"] = 0
            return
        bit = REP_BITS[representation]
        for atom in self._selected(selection):
            atom["reps"] &= ~bit

    def select_list(self, name, model, indices, mode="index"):
        self.selections[name] = {(model, int(index)) for index in indices}

    def delete(self, name):
        self.selections.pop(name, None)

    def ray(self, width, height):
        self.ray_calls.append((int(width), int(height)))

    def png(self, filename, **kwargs):
        self.png_calls.append((filename, dict(kwargs)))

    def add_object(self, name, atoms, parent=""):
        self.object_types[name] = "object:molecule"
        self.parents[name] = parent
        self.atoms.extend(atoms)

    def add_group(self, name, parent=""):
        self.object_types[name] = "object:group"
        self.parents[name] = parent

    def add_non_atom_object(self, name, object_type="object:cgo", parent=""):
        self.object_types[name] = object_type
        self.parents[name] = parent


class FailAfterRepresentationMutationCmd(FakeCmd):
    def show(self, representation, selection):
        super().show(representation, selection)
        if representation == "sticks":
            raise RuntimeError("synthetic style operation failure")


class FailApplyAndRollbackCmd(FakeCmd):
    def __init__(self):
        super().__init__()
        self.fail_rollback = False

    def show(self, representation, selection):
        super().show(representation, selection)
        if representation == "sticks" and not str(selection).startswith(
            "pp_style_restore_"
        ):
            self.fail_rollback = True
            raise RuntimeError("synthetic apply failure")

    def alter(self, selection, expression, space=None):
        if self.fail_rollback:
            raise RuntimeError("synthetic apply rollback failure")
        return super().alter(selection, expression, space)


class FailRestoreShowCmd(FakeCmd):
    def __init__(self, failures=1):
        super().__init__()
        self.restore_failures = int(failures)
        self.failure_enabled = False

    def show(self, representation, selection):
        super().show(representation, selection)
        if (
            self.failure_enabled
            and self.restore_failures
            and str(selection).startswith("pp_style_restore_")
        ):
            self.restore_failures -= 1
            raise RuntimeError("synthetic revert restore failure")


def load_registry_module(fake_cmd):
    pymol_module = types.ModuleType("pymol")
    pymol_module.cmd = fake_cmd
    sys.modules["pymol"] = pymol_module
    sys.modules.pop("pp_style_registry", None)
    sys.modules.pop("pymolplugins.styles.registry", None)
    sys.modules.pop("visualization.styles.style_registry", None)
    spec = importlib.util.spec_from_file_location(
        "pymolplugins.styles.registry", ROOT / "pymolplugins" / "styles" / "registry.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StylePresetValidationTests(unittest.TestCase):
    def test_duplicate_alias_is_rejected(self):
        first = StylePreset("first", "first", "lighting", aliases=("shared",))
        second = StylePreset("second", "second", "palette", aliases=("shared",))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_presets((first, second))

    def test_out_of_range_rgb_is_rejected(self):
        preset = StylePreset(
            "bad_rgb", "bad", "palette", colors={"pp_bad": (1.2, 0.0, 0.0)}
        )
        with self.assertRaisesRegex(ValueError, "0..1"):
            validate_presets((preset,))

    def test_unknown_setting_is_rejected(self):
        preset = StylePreset(
            "bad_setting", "bad", "lighting", global_settings={"speculra": 0}
        )
        with self.assertRaisesRegex(ValueError, "unknown PyMOL setting"):
            validate_presets((preset,))

    def test_only_upstream_atom_representations_are_reversible(self):
        valid = StylePreset(
            "nb_spheres_scene",
            "valid atom representation",
            "scene",
            representation_operations=(
                {
                    "action": "show",
                    "representation": "nb_spheres",
                    "selection": "{selection}",
                },
            ),
        )
        validate_presets((valid,))

        invalid = StylePreset(
            "measurement_scene",
            "measurement dashes are not atom reps",
            "scene",
            representation_operations=(
                {
                    "action": "show",
                    "representation": "dashes",
                    "selection": "{selection}",
                },
            ),
        )
        with self.assertRaisesRegex(ValueError, "unknown representation"):
            validate_presets((invalid,))


class StyleRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_pymol = sys.modules.get("pymol")
        cls.fake = FakeCmd()
        cls.registry_module = load_registry_module(cls.fake)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("pp_style_registry", None)
        sys.modules.pop("pymolplugins.styles.registry", None)
        sys.modules.pop("visualization.styles.style_registry", None)
        if cls.previous_pymol is None:
            sys.modules.pop("pymol", None)
        else:
            sys.modules["pymol"] = cls.previous_pymol

    def setUp(self):
        self.fake = FakeCmd()

    @staticmethod
    def _identity_scene_preset():
        return StylePreset(
            name="identity_scene",
            description="exercise stable target identity",
            category="scene",
            colors={"pp_test_color": (0.2, 0.3, 0.4)},
            global_settings={"specular": 0},
            selection_settings={"stick_radius": 0.5},
            color_operations=(
                {
                    "action": "color",
                    "color": "pp_test_color",
                    "selection": "{selection}",
                },
            ),
            representation_operations=(
                {
                    "action": "show",
                    "representation": "sticks",
                    "selection": "{selection}",
                },
                {
                    "action": "hide",
                    "representation": "lines",
                    "selection": "{selection}",
                },
            ),
        )

    def test_apply_defaults_do_not_ray_png_or_zoom(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        registry.apply("macaron_default", selection="polymer", snapshot=False)
        self.assertEqual(self.fake.ray_calls, [])
        self.assertEqual(self.fake.png_calls, [])
        self.assertEqual(self.fake.zoom_calls, [])

    def test_style_preset_resolution_is_case_friendly_and_suggests_typos(self):
        registry = self.registry_module.StyleRegistry(self.fake)

        self.assertEqual(
            registry.resolve("Macaron_Default")[0].name,
            "macaron_default",
        )
        self.assertEqual(
            registry.resolve("MINIMAL+Macaron")[0].name,
            "minimal_light",
        )
        with self.assertRaisesRegex(
            ValueError, "did you mean .*macaron_default"
        ):
            registry.resolve("macron_defalt")

    def test_style_booleans_reject_non_binary_numbers_before_mutation(self):
        for field, kwargs in (
            ("snapshot", {"snapshot": 2}),
            ("render", {"render": -1}),
        ):
            with self.subTest(field=field):
                fake = FakeCmd()
                registry = self.registry_module.StyleRegistry(fake)
                atoms = [dict(atom) for atom in fake.atoms]
                settings = dict(fake.settings)
                colors = dict(fake.named_colors)

                with self.assertRaisesRegex(ValueError, "boolean token"):
                    registry.apply("minimal_light", selection="obj_a", **kwargs)

                self.assertEqual(fake.atoms, atoms)
                self.assertEqual(fake.settings, settings)
                self.assertEqual(fake.named_colors, colors)
                self.assertEqual(registry.snapshots, [])

    def test_registered_style_command_returns_actionable_expected_errors(self):
        fake = FakeCmd()
        self.registry_module.register_commands(fake)
        output = io.StringIO()
        errors = io.StringIO()

        with redirect_stdout(output), redirect_stderr(errors):
            result = fake.extended["pp_style_apply"](
                '"missing_style"', selection='"obj_a"'
            )

        self.assertEqual(result["schema"], "pymolplugins.style_command/1.0")
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        self.assertIn("pp_style_list", result["action"])
        self.assertIn("Scene preserved", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())

    def test_registered_style_command_propagates_partial_scene_status(self):
        fake = FailApplyAndRollbackCmd()
        registry = self.registry_module.StyleRegistry(
            fake, (self._identity_scene_preset(),)
        )
        old_registry = self.registry_module._REGISTRY
        self.registry_module._REGISTRY = registry
        self.registry_module.register_commands(fake)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = fake.extended["pp_style_apply"](
                    "identity_scene", selection="obj_a"
                )
        finally:
            self.registry_module._REGISTRY = old_registry

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["scene_preserved"])
        self.assertIn("may be partial", result["action"].lower())

    def test_global_only_style_preflights_count_without_copying_atom_identity(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        registry.apply("minimal_light", selection="all", snapshot=False)
        self.assertEqual(self.fake.iterate_calls, [])
        self.assertEqual(registry.last_application["target"]["atom_count"], 2)

    def test_revert_uses_captured_atom_identity_after_selection_redefinition(self):
        registry = self.registry_module.StyleRegistry(
            self.fake, (self._identity_scene_preset(),)
        )
        self.fake.selections["focus"] = {("obj_a", 1)}
        original_atoms = [dict(atom) for atom in self.fake.atoms]
        original_settings = dict(self.fake.atom_settings)

        snapshot_id = registry.apply("identity_scene", selection="focus")
        self.fake.selections["focus"] = {("obj_a", 2)}
        registry.revert(snapshot_id)

        self.assertEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.atom_settings, original_settings)

    def test_revert_uses_captured_atom_identity_after_selection_deletion(self):
        registry = self.registry_module.StyleRegistry(
            self.fake, (self._identity_scene_preset(),)
        )
        self.fake.selections["focus"] = {("obj_a", 1)}
        original_atoms = [dict(atom) for atom in self.fake.atoms]
        original_settings = dict(self.fake.atom_settings)

        snapshot_id = registry.apply("identity_scene", selection="focus")
        del self.fake.selections["focus"]
        registry.revert(snapshot_id)

        self.assertEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.atom_settings, original_settings)

    def test_explicit_empty_selection_is_rejected_before_any_mutation(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        original_settings = dict(self.fake.settings)
        original_atoms = [dict(atom) for atom in self.fake.atoms]
        original_colors = dict(self.fake.named_colors)

        with self.assertRaisesRegex(ValueError, "selection.*empty"):
            registry.apply("macaron_default", selection="")

        self.assertEqual(self.fake.settings, original_settings)
        self.assertEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.named_colors, original_colors)
        self.assertEqual(registry.snapshots, [])

    def test_omitted_selection_remains_backward_compatible_with_all(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        before = [atom["color"] for atom in self.fake.atoms]
        registry.apply("macaron_default", snapshot=False)
        self.assertNotEqual([atom["color"] for atom in self.fake.atoms], before)
        self.assertEqual(registry.last_application["target"]["requested"], "all")
        self.assertFalse(registry.last_application["revertible"])
        self.assertEqual(registry.next_snapshot_id, 1)

    def test_unknown_or_zero_atom_selection_cannot_commit_global_settings(self):
        for target in ("missing_object", "none"):
            with self.subTest(target=target):
                fake = FakeCmd()
                registry = self.registry_module.StyleRegistry(fake)
                original_settings = dict(fake.settings)
                original_colors = dict(fake.named_colors)
                with self.assertRaisesRegex(ValueError, "no atoms"):
                    registry.apply("minimal_light", selection=target)
                self.assertEqual(fake.settings, original_settings)
                self.assertEqual(fake.named_colors, original_colors)
                self.assertEqual(registry.snapshots, [])

    def test_group_with_only_non_atom_leaves_cannot_commit_global_settings(self):
        fake = FakeCmd()
        fake.object_types.clear()
        fake.parents.clear()
        fake.atoms.clear()
        fake.add_group("empty_style_group")
        fake.add_non_atom_object("axis_only", parent="empty_style_group")
        registry = self.registry_module.StyleRegistry(fake)
        original_settings = dict(fake.settings)

        with self.assertRaisesRegex(ValueError, "group.*contains no atoms"):
            registry.apply("minimal_light", selection="empty_style_group")

        self.assertEqual(fake.settings, original_settings)
        self.assertEqual(registry.snapshots, [])
        self.assertIsNone(registry.last_application)

    def test_group_target_expands_atom_leaves_and_reports_skipped_objects(self):
        self.fake.add_group("root_style")
        self.fake.parents["obj_a"] = "root_style"
        self.fake.add_group("nested_style", parent="root_style")
        self.fake.add_object(
            "obj_b",
            [
                {
                    "model": "obj_b",
                    "index": 1,
                    "color": 9,
                    "reps": REP_BITS["cartoon"],
                }
            ],
            parent="nested_style",
        )
        self.fake.add_non_atom_object("axis_cgo", parent="root_style")
        registry = self.registry_module.StyleRegistry(self.fake)

        result = registry.apply("macaron_default", selection="root_style")
        report = result.report

        self.assertEqual(report["target"]["kind"], "group")
        self.assertEqual(
            report["target"]["resolved_targets"],
            ["obj_a", "obj_b", "axis_cgo"],
        )
        self.assertEqual(report["target"]["atom_targets"], ["obj_a", "obj_b"])
        self.assertEqual(report["target"]["skipped_targets"], ["axis_cgo"])
        self.assertEqual(report["target"]["atom_count"], 3)
        self.assertTrue(all(atom["color"] >= 102 for atom in self.fake.atoms))
        for setting in registry.resolve("macaron_default")[0].global_settings:
            self.assertEqual(
                len(
                    [
                        call
                        for call in self.fake.set_calls
                        if call[0] == setting and call[2] is None
                    ]
                ),
                1,
            )

    def test_apply_result_and_console_separate_global_and_target_effects(self):
        old_registry = self.registry_module._REGISTRY
        self.registry_module._REGISTRY = self.registry_module.StyleRegistry(self.fake)
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                result = self.registry_module.pp_style_apply(
                    "minimal_light+macaron_default", "obj_a"
                )
        finally:
            self.registry_module._REGISTRY = old_registry

        self.assertIsInstance(result, int)
        self.assertIn("global_effects", result.report)
        self.assertIn("target_effects", result.report)
        self.assertTrue(result.report["global_effects"]["settings"])
        self.assertTrue(result.report["target_effects"]["atom_colors"])
        self.assertTrue(result.report["revert_limitations"])
        rendered = output.getvalue()
        self.assertIn("Global effects:", rendered)
        self.assertIn("Target-scoped effects:", rendered)
        self.assertIn("Revert limitations:", rendered)
        self.assertIn("Undo: pp_style_revert %d" % result, rendered)

    def test_apply_without_snapshot_explicitly_reports_no_undo(self):
        old_registry = self.registry_module._REGISTRY
        self.registry_module._REGISTRY = self.registry_module.StyleRegistry(self.fake)
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                result = self.registry_module.pp_style_apply(
                    "minimal_light", "obj_a", snapshot=0
                )
        finally:
            self.registry_module._REGISTRY = old_registry

        self.assertIsNone(result)
        self.assertIn("Undo: unavailable (snapshot=0)", output.getvalue())

    def test_failed_apply_restores_global_and_exact_atom_state(self):
        for retain_snapshot in (True, False):
            with self.subTest(snapshot=retain_snapshot):
                fake = FailAfterRepresentationMutationCmd()
                fake.atom_settings[("obj_a", 2, "stick_radius")] = "0.27"
                preset = self._identity_scene_preset()
                registry = self.registry_module.StyleRegistry(fake, (preset,))
                original_settings = dict(fake.settings)
                original_atoms = [dict(atom) for atom in fake.atoms]
                original_atom_settings = dict(fake.atom_settings)
                original_color = fake.named_colors["pp_test_color"]

                with self.assertRaisesRegex(
                    RuntimeError, "synthetic style operation failure"
                ):
                    registry.apply(
                        "identity_scene",
                        selection="obj_a",
                        snapshot=retain_snapshot,
                    )

                self.assertEqual(fake.settings, original_settings)
                self.assertEqual(fake.atoms, original_atoms)
                self.assertEqual(fake.atom_settings, original_atom_settings)
                self.assertEqual(fake.named_colors["pp_test_color"], original_color)
                self.assertEqual(registry.snapshots, [])
                self.assertFalse(
                    any(
                        name.startswith("pp_style_restore_")
                        for name in fake.selections
                    )
                )

    def test_failed_apply_and_failed_rollback_reports_partial_scene(self):
        fake = FailApplyAndRollbackCmd()
        registry = self.registry_module.StyleRegistry(
            fake, (self._identity_scene_preset(),)
        )

        with self.assertRaises(self.registry_module.StyleMutationError) as caught:
            registry.apply("identity_scene", selection="obj_a")

        self.assertFalse(caught.exception.scene_preserved)
        self.assertIn("synthetic apply failure", str(caught.exception))
        self.assertIn("synthetic apply rollback failure", str(caught.exception))
        self.assertEqual(registry.snapshots, [])
        self.assertEqual(registry.next_snapshot_id, 1)

    def test_failed_apply_does_not_claim_new_named_colors_were_deleted(self):
        fake = FailAfterRepresentationMutationCmd()
        preset = StylePreset(
            name="new_color_failure",
            description="exercise PyMOL's missing color-delete API",
            category="scene",
            colors={"pp_new_failure_color": (0.2, 0.3, 0.4)},
            color_operations=(
                {
                    "action": "color",
                    "color": "pp_new_failure_color",
                    "selection": "{selection}",
                },
            ),
            representation_operations=(
                {
                    "action": "show",
                    "representation": "sticks",
                    "selection": "{selection}",
                },
            ),
        )
        registry = self.registry_module.StyleRegistry(fake, (preset,))

        with self.assertRaises(self.registry_module.StyleMutationError) as caught:
            registry.apply("new_color_failure", selection="obj_a")

        self.assertFalse(caught.exception.scene_preserved)
        self.assertIn("no color-delete API", str(caught.exception))
        self.assertIn("pp_new_failure_color", fake.named_colors)
        self.assertEqual(registry.snapshots, [])
        self.assertEqual(registry.next_snapshot_id, 1)

    def test_failed_revert_restores_styled_state_and_keeps_retryable_snapshot(self):
        fake = FailRestoreShowCmd(failures=1)
        registry = self.registry_module.StyleRegistry(
            fake, (self._identity_scene_preset(),)
        )
        snapshot_id = registry.apply("identity_scene", selection="obj_a")
        styled_atoms = [dict(atom) for atom in fake.atoms]
        styled_settings = dict(fake.settings)
        styled_atom_settings = dict(fake.atom_settings)
        styled_colors = dict(fake.named_colors)
        fake.failure_enabled = True

        with self.assertRaises(self.registry_module.StyleMutationError) as caught:
            registry.revert(snapshot_id)

        self.assertTrue(caught.exception.scene_preserved)
        self.assertIn("restored the pre-revert styled state", str(caught.exception))
        self.assertEqual(fake.atoms, styled_atoms)
        self.assertEqual(fake.settings, styled_settings)
        self.assertEqual(fake.atom_settings, styled_atom_settings)
        self.assertEqual(fake.named_colors, styled_colors)
        self.assertEqual(registry.snapshots[-1].snapshot_id, snapshot_id)

        fake.failure_enabled = False
        self.assertEqual(registry.revert(snapshot_id), snapshot_id)
        self.assertEqual(registry.snapshots, [])

    def test_failed_revert_and_failed_rollback_reports_partial_scene(self):
        fake = FailRestoreShowCmd(failures=2)
        registry = self.registry_module.StyleRegistry(
            fake, (self._identity_scene_preset(),)
        )
        snapshot_id = registry.apply("identity_scene", selection="obj_a")
        fake.failure_enabled = True

        with self.assertRaises(self.registry_module.StyleMutationError) as caught:
            registry.revert(snapshot_id)

        self.assertFalse(caught.exception.scene_preserved)
        self.assertIn(
            "rollback to the pre-revert styled state also failed",
            str(caught.exception),
        )
        self.assertEqual(registry.snapshots[-1].snapshot_id, snapshot_id)

    def test_snapshot_probes_missing_named_colors_without_error_lookup(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        registry.apply("macaron_default", selection="polymer", snapshot=True)
        palette_names = registry.resolve("macaron_default")[0].colors
        self.assertFalse(
            set(palette_names) & set(self.fake.get_color_tuple_calls)
        )

    def test_lighting_and_palette_can_be_composed(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        registry.apply(
            "rfd2_lighting+rfd2_paper_palette", selection="obj_a", snapshot=False
        )
        self.assertEqual(self.fake.settings["power"], 0.2)
        self.assertEqual(len(self.fake.spectrum_calls), 1)
        self.assertIn("obj_a", self.fake.spectrum_calls[0][2])

    def test_snapshot_revert_restores_settings_background_atoms_and_reps(self):
        preset = StylePreset(
            name="test_scene",
            description="exercise every reversible field",
            category="scene",
            colors={"pp_test_color": (0.2, 0.3, 0.4)},
            global_settings={"specular": 0, "bg_rgb": (1.0, 1.0, 1.0)},
            selection_settings={"stick_radius": 0.5},
            color_operations=(
                {
                    "action": "color",
                    "color": "pp_test_color",
                    "selection": "{selection}",
                },
            ),
            representation_operations=(
                {
                    "action": "show",
                    "representation": "sticks",
                    "selection": "{selection}",
                },
                {
                    "action": "hide",
                    "representation": "cartoon",
                    "selection": "{selection}",
                },
            ),
        )
        registry = self.registry_module.StyleRegistry(self.fake, (preset,))
        self.fake.atom_settings[("obj_a", 2, "stick_radius")] = "0.27"
        original_atoms = [dict(atom) for atom in self.fake.atoms]
        original_atom_settings = dict(self.fake.atom_settings)
        original_color = self.fake.named_colors["pp_test_color"]

        snapshot_id = registry.apply("test_scene", selection="all")
        self.assertEqual(snapshot_id, 1)
        self.assertNotEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.settings["bg_rgb"], (1.0, 1.0, 1.0))

        self.assertEqual(registry.revert(), snapshot_id)
        self.assertEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.settings["specular"], "0.75")
        self.assertEqual(self.fake.settings["bg_rgb"], (0.1, 0.2, 0.3))
        self.assertEqual(self.fake.atom_settings, original_atom_settings)
        self.assertEqual(self.fake.named_colors["pp_test_color"], original_color)
        self.assertEqual(registry.snapshots, [])

    def test_snapshot_stack_enforces_lifo(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        first = registry.apply("publication_light")
        second = registry.apply("minimal_light")
        with self.assertRaisesRegex(ValueError, "LIFO"):
            registry.revert(first)
        self.assertEqual(registry.revert(second), second)
        self.assertEqual(registry.revert(first), first)

    def test_render_is_explicit_and_supports_filename(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        registry.render(800, 600)
        registry.render(1200, 900, "figure.png")
        self.assertEqual(self.fake.ray_calls, [(800, 600)])
        self.assertEqual(
            self.fake.png_calls,
            [("figure.png", {"width": 1200, "height": 900, "ray": 1, "quiet": 0})],
        )

    def test_render_command_reports_viewer_dimensions_and_png_target(self):
        old_registry = self.registry_module._REGISTRY
        self.registry_module._REGISTRY = self.registry_module.StyleRegistry(self.fake)
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                viewer = self.registry_module.pp_style_render(800, 600)
                png = self.registry_module.pp_style_render(
                    1200, 900, "figure with spaces.png"
                )
        finally:
            self.registry_module._REGISTRY = old_registry

        self.assertEqual(viewer, (800, 600))
        self.assertEqual(png, "figure with spaces.png")
        self.assertIn("Rendered in viewer: 800 x 600", output.getvalue())
        self.assertIn("Rendered PNG: figure with spaces.png", output.getvalue())

    def test_rfdiffusion_selection_argument_is_respected(self):
        old_registry = self.registry_module._REGISTRY
        self.registry_module._REGISTRY = self.registry_module.StyleRegistry(self.fake)
        try:
            self.registry_module.legacy_mass_rainbow("obj_a", snapshot=0)
        finally:
            self.registry_module._REGISTRY = old_registry
        self.assertIn("obj_a", self.fake.spectrum_calls[-1][2])

    def test_all_declared_rgb_channels_are_normalized(self):
        for preset in self.registry_module._REGISTRY.presets.values():
            for rgb in preset.colors.values():
                self.assertTrue(all(0.0 <= channel <= 1.0 for channel in rgb))

    def test_describe_classifies_global_and_target_scoped_effects(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        lighting = registry.describe("minimal_light")["effects"]
        palette = registry.describe("macaron_default")["effects"]
        self.assertIn("ambient", lighting["global"]["settings"])
        self.assertEqual(lighting["target_scoped"]["atom_colors"], [])
        self.assertEqual(palette["target_scoped"]["atom_colors"], ["spectrum"])
        self.assertTrue(palette["global"]["named_colors_registered"])

    def test_high_contrast_outline_actually_shows_cartoon_and_reverts(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        original_atoms = [dict(atom) for atom in self.fake.atoms]

        snapshot_id = registry.apply("high_contrast_outline", selection="obj_a")

        self.assertTrue(
            all(atom["reps"] & REP_BITS["cartoon"] for atom in self.fake.atoms)
        )
        self.assertEqual(
            registry.last_application["target_effects"]["representations"],
            ["show:cartoon"],
        )
        description = registry.describe("high_contrast_outline")
        self.assertIn("Show cartoon", description["description"])
        self.assertEqual(description["category"], "scene")
        self.assertEqual(registry.revert(snapshot_id), snapshot_id)
        self.assertEqual(self.fake.atoms, original_atoms)

    def test_representation_bits_match_pymol_atom_rep_order(self):
        self.assertEqual(self.registry_module.REPRESENTATION_BITS, REP_BITS)
        self.assertEqual(
            set(self.registry_module.VALID_REPRESENTATIONS) - {"everything"},
            set(REP_BITS),
        )
        self.assertEqual(REP_BITS["nb_spheres"], 1 << 4)
        self.assertEqual(REP_BITS["nonbonded"], 1 << 11)
        self.assertEqual(REP_BITS["ellipsoids"], 1 << 19)
        self.assertNotIn("dashes", self.registry_module.REPRESENTATION_BITS)

    def test_macaron_qt_plugin_is_a_thin_registry_adapter(self):
        source = (PLUGIN_DIR / "macaron_style_plugin.py").read_text(encoding="utf-8")
        self.assertNotRegex(
            source,
            r"cmd\.(?:set|set_color|color|spectrum|bg_color|ray|png)\s*\(",
        )

        old_registry = self.registry_module._REGISTRY
        previous_qt = sys.modules.get("pymol.Qt")
        previous_plugin = sys.modules.get(
            "visualization.plugins.macaron_style_plugin"
        )
        qt_module = types.ModuleType("pymol.Qt")
        qt_module.QtWidgets = types.SimpleNamespace(QDialog=object)
        sys.modules["pymol.Qt"] = qt_module
        sys.modules.pop("visualization.plugins.macaron_style_plugin", None)
        spec = importlib.util.spec_from_file_location(
            "visualization.plugins.macaron_style_plugin",
            PLUGIN_DIR / "macaron_style_plugin.py",
        )
        plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = plugin
        original_atoms = [dict(atom) for atom in self.fake.atoms]
        self.registry_module._REGISTRY = self.registry_module.StyleRegistry(self.fake)
        try:
            spec.loader.exec_module(plugin)
            snapshot_id = plugin.apply_custom_style("all", "warm")
            self.assertEqual(snapshot_id, 1)
            self.assertEqual(self.fake.ray_calls, [])
            self.assertNotEqual(self.fake.atoms, original_atoms)

            self.assertEqual(plugin.revert_custom_style(), snapshot_id)
            self.assertEqual(self.fake.atoms, original_atoms)

            plugin.render_high_quality(640, 480)
            self.assertEqual(self.fake.ray_calls, [(640, 480)])

            analysis_snapshot = plugin.quick_style_preset(
                "analysis", "all", snapshot=1, render=0
            )
            self.assertEqual(analysis_snapshot, 2)
            self.assertEqual(
                self.registry_module._REGISTRY.snapshots[-1].preset_names,
                ("minimal_light",),
            )
        finally:
            self.registry_module._REGISTRY = old_registry
            sys.modules.pop("visualization.plugins.macaron_style_plugin", None)
            if previous_plugin is not None:
                sys.modules["visualization.plugins.macaron_style_plugin"] = previous_plugin
            if previous_qt is None:
                sys.modules.pop("pymol.Qt", None)
            else:
                sys.modules["pymol.Qt"] = previous_qt

    def test_high_contrast_pml_bootstraps_without_rendering(self):
        source = (STYLE_DIR / "high_contrast_ray_style.pml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "run visualization/styles/style_registry_bootstrap.py", source
        )
        self.assertIn("pp_style_apply high_contrast_outline", source)
        self.assertNotRegex(source, r"(?m)^\s*(?:ray|png)\b")

    def test_style_bootstrap_preserves_process_snapshot_stack(self):
        old_registry = self.registry_module._REGISTRY
        active = self.registry_module.StyleRegistry(self.fake)
        self.registry_module._REGISTRY = active
        try:
            snapshot_id = active.apply("publication_light")
            runpy.run_path(str(STYLE_DIR / "style_registry_bootstrap.py"))
            self.assertIs(self.registry_module._REGISTRY, active)
            self.assertEqual(active.snapshots[-1].snapshot_id, snapshot_id)
        finally:
            self.registry_module._REGISTRY = old_registry

    def test_legacy_load_order_keeps_identical_conflict_signatures(self):
        old_registry = self.registry_module._REGISTRY
        self.registry_module._REGISTRY = self.registry_module.StyleRegistry(self.fake)
        command_fake = self.registry_module.cmd
        try:
            observed = []
            orders = (
                ("macaron_style.py", "publication_presets.py", "set_publication_style.py"),
                ("set_publication_style.py", "publication_presets.py", "macaron_style.py"),
            )
            for order in orders:
                command_fake.extended.clear()
                for filename in order:
                    runpy.run_path(str(STYLE_DIR / filename))
                observed.append(
                    (
                        command_fake.extended["macaron_style"],
                        inspect.signature(command_fake.extended["macaron_style"]),
                        command_fake.extended["pbs"],
                        inspect.signature(command_fake.extended["pbs"]),
                    )
                )
        finally:
            self.registry_module._REGISTRY = old_registry
        self.assertIs(observed[0][0], observed[1][0])
        self.assertEqual(observed[0][1], observed[1][1])
        self.assertIs(observed[0][2], observed[1][2])
        self.assertEqual(observed[0][3], observed[1][3])


class ZPymolRunCompatibilityTests(unittest.TestCase):
    def test_run_namespace_uses_script_path_outside_repository_cwd(self):
        module_names = (
            "pymol",
            "pp_style_registry",
            "style_presets",
            "style_registry",
            "_style_registry_loader",
            "visualization",
            "visualization.styles",
            "visualization.styles.style_presets",
            "visualization.styles.style_registry",
            "visualization.styles._style_registry_loader",
            "pymolplugins.styles.presets",
            "pymolplugins.styles.registry",
        )
        missing = object()
        previous_modules = {
            name: sys.modules.get(name, missing) for name in module_names
        }
        previous_path = list(sys.path)
        previous_cwd = os.getcwd()
        fake = FakeCmd()
        pymol_module = types.ModuleType("pymol")
        pymol_module.cmd = fake
        try:
            for name in module_names:
                sys.modules.pop(name, None)
            sys.modules["pymol"] = pymol_module
            sys.path[:] = [
                item
                for item in sys.path
                if item not in ("", str(ROOT), str(STYLE_DIR))
            ]
            with tempfile.TemporaryDirectory() as temp_dir:
                os.chdir(temp_dir)
                registry_path = STYLE_DIR / "style_registry.py"
                pymol_module.__dict__["__script__"] = str(registry_path)
                exec(
                    compile(registry_path.read_text(), str(registry_path), "exec"),
                    pymol_module.__dict__,
                )
                wrapper_path = STYLE_DIR / "macaron_style.py"
                pymol_module.__dict__["__script__"] = str(wrapper_path)
                exec(
                    compile(wrapper_path.read_text(), str(wrapper_path), "exec"),
                    pymol_module.__dict__,
                )
            self.assertIn("pp_style_apply", fake.extended)
            self.assertIs(fake.extended["macaron_style"], pymol_module.legacy_macaron_style)
        finally:
            os.chdir(previous_cwd)
            sys.path[:] = previous_path
            for name in module_names:
                sys.modules.pop(name, None)
                previous = previous_modules[name]
                if previous is not missing:
                    sys.modules[name] = previous


if __name__ == "__main__":
    unittest.main()
