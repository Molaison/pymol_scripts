import importlib
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

                self.assertEqual(first_owner, expected)
                self.assertEqual(final_owner, expected)
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
