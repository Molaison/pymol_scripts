import contextlib
import importlib
import inspect
import io
import os
import sys
import tempfile
import types
import unittest

from tests.navigation.test_navigation_adapter import FakeCmd
from pymolplugins.navigation import commands as navigation


class ScreenBoostCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCmd()
        navigation.cmd = self.fake
        navigation._CONTROLLER = None
        self.previous_pymol = sys.modules.get("pymol")
        pymol = types.ModuleType("pymol")
        pymol.cmd = self.fake
        sys.modules["pymol"] = pymol
        sys.modules.pop("workflows.dataset_navigation.screen_boost", None)
        sys.modules.pop("workflows.dataset_navigation.file_management", None)
        navigation._LEGACY_SWITCH_COMMANDS.clear()
        self.adapter = importlib.import_module(
            "workflows.dataset_navigation.screen_boost"
        )

    def tearDown(self):
        if navigation._CONTROLLER is not None:
            navigation.pp_nav_stop()
        sys.modules.pop("workflows.dataset_navigation.screen_boost", None)
        sys.modules.pop("workflows.dataset_navigation.file_management", None)
        navigation._LEGACY_SWITCH_COMMANDS.clear()
        if self.previous_pymol is None:
            sys.modules.pop("pymol", None)
        else:
            sys.modules["pymol"] = self.previous_pymol

    def test_import_does_not_bind_keys_or_mutate_scene(self):
        self.assertEqual(self.fake.key_set_calls, [])
        self.assertEqual(self.fake.enabled, set(self.fake.types))

    def test_screen_boost_public_commands_share_console_normalization(self):
        self.assertTrue(self.fake.extensions)
        self.assertTrue(
            all(
                getattr(callback, "_pymolplugins_console_adapter", False)
                for callback in self.fake.extensions.values()
            )
        )

    def test_file_management_public_commands_share_console_normalization(self):
        self.fake.extensions.clear()
        sys.modules.pop("workflows.dataset_navigation.file_management", None)

        importlib.import_module("workflows.dataset_navigation.file_management")

        self.assertTrue(self.fake.extensions)
        self.assertTrue(
            all(
                getattr(callback, "_pymolplugins_console_adapter", False)
                for callback in self.fake.extensions.values()
            )
        )

    def test_registered_loadpath_normalizes_quoted_path_and_booleans(self):
        with tempfile.TemporaryDirectory() as parent:
            dataset = os.path.join(parent, "path with spaces")
            os.mkdir(dataset)
            for name in ("one.pdb", "two.pdb"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")

            result = self.fake.extensions["loadpath"](
                '"%s"' % dataset,
                bucket_size='"2"',
                objects_per_step='"1"',
                exclude_selected='"0"',
                bind_keys='"0"',
                replace='"1"',
            )

            self.assertEqual(result["count"], 2)
            self.assertEqual(navigation._controller().session.batch_size, 2)

    def test_compat_parameter_errors_are_one_line_and_trace_free(self):
        output = io.StringIO()
        errors = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            result = self.fake.extensions["auto_bucket"]('"not-a-boolean"')

        lines = [line for line in output.getvalue().splitlines() if line]
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(lines), 1)
        self.assertIn("auto_bucket error:", lines[0])
        self.assertIn("Next:", lines[0])
        self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())

    def test_invalid_regex_is_actionable_and_preserves_loader_config(self):
        compat = importlib.import_module("pymolplugins.navigation.compat.screen_boost")
        before = dict(vars(compat._CONFIG))
        output = io.StringIO()
        errors = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            result = self.fake.extensions["loadpath"](
                '"."',
                pattern='"["',
                bind_keys='"0"',
            )

        lines = [line for line in output.getvalue().splitlines() if line]
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(lines), 1)
        self.assertIn("invalid file regex", lines[0])
        self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())
        self.assertEqual(dict(vars(compat._CONFIG)), before)

    def test_invalid_reload_flag_does_not_toggle_exclude_selected(self):
        compat = importlib.import_module("pymolplugins.navigation.compat.screen_boost")
        before = compat._CONFIG.exclude_selected
        requested = '"0"' if before else '"1"'
        output = io.StringIO()
        errors = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            result = self.fake.extensions["exclude_selected"](
                requested,
                reload='"later"',
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(compat._CONFIG.exclude_selected, before)
        self.assertIn("exclude_selected error:", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())

    def test_compat_unexpected_failures_keep_traceback_diagnostics(self):
        with tempfile.TemporaryDirectory() as dataset:
            with open(os.path.join(dataset, "one.pdb"), "w", encoding="ascii") as handle:
                handle.write("END\n")
            self.adapter.load_by_path(
                dataset,
                bucket_size=1,
                objects_per_step=1,
                bind_keys=0,
            )

            def fail_save(*args, **kwargs):
                raise RuntimeError("synthetic save failure")

            self.fake.save = fail_save
            output = io.StringIO()
            errors = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                result = self.fake.extensions["save_select"]()

        self.assertEqual(result["status"], "error")
        self.assertIn("save_select error: synthetic save failure", output.getvalue())
        self.assertIn("Next:", output.getvalue())
        self.assertIn("Traceback", errors.getvalue())
        self.assertIn("RuntimeError", errors.getvalue())

    def test_file_management_save_uses_current_api_and_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as dataset:
            with open(os.path.join(dataset, "one.pdb"), "w", encoding="ascii") as handle:
                handle.write("END\n")
            self.adapter.load_by_path(
                dataset,
                bucket_size=1,
                objects_per_step=1,
                bind_keys=0,
            )
            self.fake.extensions.clear()
            sys.modules.pop("workflows.dataset_navigation.file_management", None)
            importlib.import_module("workflows.dataset_navigation.file_management")

            def fail_save(*args, **kwargs):
                raise RuntimeError("synthetic file-management failure")

            self.fake.save = fail_save
            output = io.StringIO()
            errors = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                result = self.fake.extensions["save_selected"]()

        self.assertEqual(result["status"], "error")
        self.assertIn(
            "save_selected error: synthetic file-management failure",
            output.getvalue(),
        )
        self.assertIn("Next:", output.getvalue())
        self.assertIn("Traceback", errors.getvalue())
        self.assertIn("RuntimeError", errors.getvalue())

    def test_exclude_selected_uses_canonical_file_entries(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("one.pdb", "two.pdb"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")
            os.mkdir(os.path.join(dataset, "selected"))
            with open(
                os.path.join(dataset, "selected", "one.pdb"),
                "w",
                encoding="ascii",
            ) as handle:
                handle.write("END\n")

            result = self.adapter.load_by_path(
                dataset,
                bucket_size=2,
                objects_per_step=1,
                exclude_selected="true",
                bind_keys="false",
            )

            self.assertEqual(result["count"], 1)
            self.assertEqual(navigation._controller().session.entries[0].display_name, "two.pdb")
            self.assertIn("overlay", self.fake.types)
            self.assertEqual(self.fake.key_set_calls, [])

    def test_same_stem_sources_save_to_distinct_stable_paths(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("same.pdb", "same.cif"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")
            self.adapter.load_by_path(
                dataset,
                bucket_size=2,
                objects_per_step=1,
                exclude_selected=0,
                bind_keys=0,
            )

            first = self.adapter.save_selected()
            navigation.pp_nav_next()
            second = self.adapter.save_selected()

            self.assertNotEqual(first, second)
            self.assertEqual(
                {os.path.basename(first), os.path.basename(second)},
                {"same__cif.pdb", "same__pdb.pdb"},
            )
            self.assertTrue(os.path.isfile(first))
            self.assertTrue(os.path.isfile(second))
            compat = importlib.import_module(
                "pymolplugins.navigation.compat.screen_boost"
            )
            compat._CONFIG.exclude_selected = True
            self.assertEqual(compat._matching_paths(), [])

    def test_hook_offsets_are_owned_by_canonical_controller(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("one.pdb", "two.pdb"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")
            self.adapter.register_switch_command("show sticks, {object}", offset=1)
            self.adapter.load_by_path(
                dataset,
                bucket_size=2,
                objects_per_step=2,
                bind_keys=0,
            )

            controller = navigation._controller()
            self.assertEqual(controller.hooks, [("show sticks, {object}", 1)])
            second = controller._entry_names(controller.session.current_entries()[1])[0]
            self.assertIn(f"show sticks, {second}", self.fake.commands)

    def test_overlapping_legacy_aliases_have_one_owner_in_both_load_orders(self):
        module_names = (
            "workflows.dataset_navigation.screen_boost",
            "workflows.dataset_navigation.file_management",
        )
        expected = {
            "clear_pins": navigation.legacy_clear_pins,
            "register_switch_cmd": navigation.legacy_register_switch_command,
            "clear_switch_cmds": navigation.legacy_clear_switch_commands,
        }
        for order in (module_names, tuple(reversed(module_names))):
            with self.subTest(order=order):
                if navigation._CONTROLLER is not None:
                    navigation.pp_nav_stop()
                navigation._LEGACY_SWITCH_COMMANDS.clear()
                self.fake.extensions.clear()
                for module_name in module_names:
                    sys.modules.pop(module_name, None)

                importlib.import_module(order[0])
                first_owner = {
                    name: self.fake.extensions[name] for name in expected
                }
                importlib.import_module(order[1])
                final_owner = {
                    name: self.fake.extensions[name] for name in expected
                }

                self.assertEqual(
                    {name: inspect.unwrap(callback) for name, callback in first_owner.items()},
                    expected,
                )
                self.assertEqual(
                    {name: inspect.unwrap(callback) for name, callback in final_owner.items()},
                    expected,
                )
                configured = self.fake.extensions["register_switch_cmd"](
                    "show sticks, {object}", 2
                )
                self.assertEqual(configured["status"], "configured")
                self.assertEqual(
                    navigation.legacy_switch_commands(),
                    [("show sticks, {object}", 2)],
                )
                cleared = self.fake.extensions["clear_switch_cmds"]()
                self.assertEqual(cleared, {"status": "configured", "hooks": []})
                self.assertEqual(navigation.legacy_switch_commands(), [])


if __name__ == "__main__":
    unittest.main()
