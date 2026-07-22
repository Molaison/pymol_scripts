import importlib.util
import inspect
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest

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
        self.selections = {}
        self.extended = {}
        self.ray_calls = []
        self.png_calls = []
        self.zoom_calls = []
        self.spectrum_calls = []

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
            return self.selection_settings.get((name, selection), self.settings.get(name, "0"))
        return self.settings.get(name, "0")

    def set(self, name, value, selection=None):
        if selection is None:
            self.settings[name] = value
        else:
            self.selection_settings[(name, selection)] = value

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
        if "obj_a" in text or text in ("all", "polymer") or "all" in text:
            return list(self.atoms)
        return list(self.atoms)

    def iterate(self, selection, expression, space=None):
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

    def test_apply_defaults_do_not_ray_png_or_zoom(self):
        registry = self.registry_module.StyleRegistry(self.fake)
        registry.apply("macaron_default", selection="polymer", snapshot=False)
        self.assertEqual(self.fake.ray_calls, [])
        self.assertEqual(self.fake.png_calls, [])
        self.assertEqual(self.fake.zoom_calls, [])

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
        original_atoms = [dict(atom) for atom in self.fake.atoms]
        original_color = self.fake.named_colors["pp_test_color"]

        snapshot_id = registry.apply("test_scene", selection="all")
        self.assertEqual(snapshot_id, 1)
        self.assertNotEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.settings["bg_rgb"], (1.0, 1.0, 1.0))

        self.assertEqual(registry.revert(), snapshot_id)
        self.assertEqual(self.fake.atoms, original_atoms)
        self.assertEqual(self.fake.settings["specular"], "0.75")
        self.assertEqual(self.fake.settings["bg_rgb"], (0.1, 0.2, 0.3))
        self.assertEqual(
            self.fake.selection_settings[("stick_radius", "all")], "0.15"
        )
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
