import importlib
import os
import tempfile
import unittest

navigation = importlib.import_module("pymolplugins.navigation.commands")


class FakeCmd:
    def __init__(self):
        self.types = {
            "root_a": "object:group",
            "root_b": "object:group",
            "nested": "object:group",
            "a_model": "object:molecule",
            "a_cgo": "object:cgo",
            "a_map": "object:map",
            "a_measurement": "object:measurement",
            "b_model": "object:molecule",
            "overlay": "object:cgo",
        }
        self.groups = {
            "root_a": ["a_model", "a_cgo", "nested"],
            "nested": ["a_map", "a_measurement"],
            "root_b": ["b_model"],
        }
        self.enabled = set(self.types)
        self.loaded = {}
        self.zoom_calls = []
        self.commands = []
        self.fail_load_path = None
        self.key_mappings = {
            "right": "scene next",
            "CTRL-left": (self.zoom, ("previous",), {}),
        }
        self.key_set_calls = []
        self.extensions = {}
        self.save_calls = []

    def get_names(self, mode="all", enabled_only=0, selection=""):
        if selection:
            # Match real PyMOL semantics: a group used as an atom selection
            # only yields atom-bearing molecule objects, never CGO/map/groups.
            names = [
                name
                for name in self.groups.get(selection, [])
                if self.types.get(name) == "object:molecule"
            ]
        else:
            names = list(self.types)
        if enabled_only:
            names = [name for name in names if name in self.enabled]
        return names

    def get_type(self, name):
        return self.types.get(name, "")

    def get_object_list(self, selection="all"):
        return [name for name in self.groups.get(selection, []) if self.types[name] == "object:molecule"]

    def get_session(self, names="", partial=0, quiet=1):
        parent_by_name = {}
        for parent, children in self.groups.items():
            for child in children:
                parent_by_name[child] = parent
        records = [
            [name, 0, int(name in self.enabled), None, -1, None, parent_by_name.get(name, "")]
            for name in self.types
        ]
        return {"names": records, "partial": None}

    def enable(self, name):
        self.enabled.add(name)

    def disable(self, name):
        self.enabled.discard(name)

    def load(self, path, name, quiet=1):
        if path == self.fail_load_path:
            raise RuntimeError("synthetic load failure")
        self.types[name] = "object:molecule"
        self.loaded[name] = path
        self.enabled.add(name)

    def delete(self, name):
        self.types.pop(name, None)
        self.loaded.pop(name, None)
        self.enabled.discard(name)

    def zoom(self, selection):
        self.zoom_calls.append(selection)

    def do(self, command):
        self.commands.append(command)

    def set_key(self, key, callback):
        self.key_set_calls.append((key, callback))
        self.key_mappings[key] = (callback, (), {})

    def extend(self, name, callback):
        self.extensions[name] = callback

    def save(self, filename, selection):
        self.save_calls.append((filename, selection))
        with open(filename, "w", encoding="ascii") as handle:
            handle.write("END\n")


class NavigationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCmd()
        navigation.cmd = self.fake
        navigation._CONTROLLER = None
        navigation._LEGACY_SWITCH_COMMANDS.clear()

    def tearDown(self):
        if navigation._CONTROLLER is not None:
            navigation.pp_nav_stop()

    def test_group_navigation_keeps_group_atomic_and_leaves_overlay_untouched(self):
        result = navigation.pp_nav_start(
            source="groups", batch_size=2, window_size=1, bind_keys=0, zoom_policy="none"
        )
        self.assertEqual(
            set(result["visible_names"]),
            {"a_model", "a_cgo", "a_map", "a_measurement"},
        )
        self.assertIn("overlay", self.fake.enabled)
        self.assertNotIn("b_model", self.fake.enabled)
        navigation.pp_nav_next()
        self.assertIn("b_model", self.fake.enabled)
        self.assertNotIn("a_model", self.fake.enabled)
        self.assertNotIn("a_cgo", self.fake.enabled)
        self.assertNotIn("a_map", self.fake.enabled)
        self.assertNotIn("a_measurement", self.fake.enabled)
        self.assertIn("overlay", self.fake.enabled)

    def test_pin_keeps_complete_group_visible(self):
        navigation.pp_nav_start(source="groups", batch_size=2, window_size=1, zoom_policy="none")
        navigation.pp_nav_pin()
        navigation.pp_nav_next()
        self.assertTrue(
            {"a_model", "a_cgo", "a_map", "a_measurement", "b_model"}.issubset(
                self.fake.enabled
            )
        )

    def test_file_batches_only_delete_session_managed_objects(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("one.pdb", "two.pdb", "three.pdb"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")
            first = navigation.pp_nav_start(
                source="files", path=dataset, batch_size=2, window_size=1, zoom_policy="none"
            )
            self.assertEqual(len(self.fake.loaded), 2)
            self.assertIn("overlay", self.fake.types)
            navigation.pp_nav_next_batch()
            self.assertEqual(len(self.fake.loaded), 1)
            self.assertIn("overlay", self.fake.types)
            navigation.pp_nav_stop()
            self.assertFalse(self.fake.loaded)
            self.assertIn("overlay", self.fake.types)

    def test_explicit_parent_and_child_group_entries_are_rejected(self):
        result = navigation.pp_nav_start(source="groups", entries="root_a,nested", zoom_policy="none")
        self.assertEqual(result["status"], "error")
        self.assertIn("parent and nested", result["error"])

    def test_bound_keys_restore_exact_previous_mappings(self):
        previous_right = self.fake.key_mappings["right"]
        previous_ctrl_left = self.fake.key_mappings["CTRL-left"]
        result = navigation.pp_nav_start(
            source="groups", bind_keys=1, zoom_policy="none"
        )
        self.assertTrue(result["keys_bound"])
        self.assertNotEqual(self.fake.key_mappings["right"], previous_right)
        self.assertIn("left", self.fake.key_mappings)

        navigation.pp_nav_stop()

        self.assertEqual(self.fake.key_mappings["right"], previous_right)
        self.assertEqual(self.fake.key_mappings["CTRL-left"], previous_ctrl_left)
        self.assertNotIn("left", self.fake.key_mappings)
        self.assertNotIn("CTRL-right", self.fake.key_mappings)

    def test_stop_preserves_a_newer_external_key_binding(self):
        navigation.pp_nav_start(source="groups", bind_keys=1, zoom_policy="none")
        self.fake.key_mappings["right"] = "external command"

        navigation.pp_nav_stop()

        self.assertEqual(self.fake.key_mappings["right"], "external command")

    def test_active_session_is_preserved_when_replacement_validation_fails(self):
        first = navigation.pp_nav_start(source="groups", zoom_policy="none")
        controller = navigation._CONTROLLER
        enabled = set(self.fake.enabled)

        result = navigation.pp_nav_start(
            source="files",
            path="/definitely/missing",
            replace=1,
            zoom_policy="none",
        )

        self.assertEqual(result["status"], "error")
        self.assertIs(navigation._CONTROLLER, controller)
        self.assertEqual(set(self.fake.enabled), enabled)
        self.assertEqual(navigation.pp_nav_status(quiet=1)["cursor"], first["cursor"])

    def test_failed_first_load_restores_replaced_file_session_and_key_lease(self):
        with (
            tempfile.TemporaryDirectory() as old_dataset,
            tempfile.TemporaryDirectory() as new_dataset,
        ):
            for name in ("old_one.pdb", "old_two.pdb"):
                with open(
                    os.path.join(old_dataset, name), "w", encoding="ascii"
                ) as handle:
                    handle.write("END\n")
            new_paths = [
                os.path.join(new_dataset, name)
                for name in ("new_one.pdb", "new_two.pdb")
            ]
            for new_path in new_paths:
                with open(new_path, "w", encoding="ascii") as handle:
                    handle.write("END\n")

            navigation.pp_nav_start(
                source="files",
                path=old_dataset,
                batch_size=1,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
            )
            navigation.pp_nav_pin()
            navigation.pp_nav_register_hook("show sticks, {object}", offset=0)
            old_controller = navigation._controller()
            old_session = old_controller.session
            old_cursor = old_session.cursor
            old_pins = set(old_session.pinned_entry_ids)
            old_managed = set(old_controller.managed_names)
            old_loaded_entries = dict(old_controller.loaded_entry_names)
            old_loaded_objects = dict(self.fake.loaded)
            old_enabled = set(self.fake.enabled)
            old_key_mappings = dict(self.fake.key_mappings)
            old_previous_keys = dict(old_controller.previous_key_mappings)
            old_installed_keys = dict(old_controller.installed_key_mappings)

            # Fail after one new object has loaded, exercising cleanup of a
            # partially applied first batch as well as restoration of the old
            # session.
            self.fake.fail_load_path = new_paths[1]
            result = navigation.pp_nav_start(
                source="files",
                path=new_dataset,
                batch_size=2,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
                replace=1,
            )

            self.assertEqual(result["status"], "error")
            self.assertIn("synthetic load failure", result["error"])
            self.assertIs(navigation._CONTROLLER, old_controller)
            self.assertIs(old_controller.session, old_session)
            self.assertEqual(old_session.cursor, old_cursor)
            self.assertEqual(old_session.pinned_entry_ids, old_pins)
            self.assertEqual(old_controller.managed_names, old_managed)
            self.assertEqual(old_controller.loaded_entry_names, old_loaded_entries)
            self.assertEqual(self.fake.loaded, old_loaded_objects)
            self.assertEqual(set(self.fake.enabled), old_enabled)
            self.assertEqual(self.fake.key_mappings, old_key_mappings)
            self.assertTrue(old_controller.keys_bound)
            self.assertEqual(old_controller.previous_key_mappings, old_previous_keys)
            self.assertEqual(old_controller.installed_key_mappings, old_installed_keys)
            self.assertEqual(
                old_controller.hooks, [("show sticks, {object}", 0)]
            )

    def test_successful_replacement_adopts_matching_owned_file_object(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "same.pdb")
            with open(path, "w", encoding="ascii") as handle:
                handle.write("END\n")
            first = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=1,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
            )
            old_controller = navigation._controller()
            object_name = first["managed_names"][0]

            replaced = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=1,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
                replace=1,
            )

            self.assertNotEqual(replaced.get("status"), "error")
            self.assertIsNot(navigation._CONTROLLER, old_controller)
            self.assertEqual(replaced["managed_names"], [object_name])
            self.assertEqual(self.fake.loaded, {object_name: path})
            self.assertIn(object_name, self.fake.enabled)
            self.assertTrue(navigation._controller().keys_bound)

    def test_failed_replacement_never_deletes_an_adopted_old_object(self):
        with tempfile.TemporaryDirectory() as dataset:
            paths = [
                os.path.join(dataset, name)
                for name in ("one.pdb", "two.pdb")
            ]
            for path in paths:
                with open(path, "w", encoding="ascii") as handle:
                    handle.write("END\n")
            first = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=1,
                window_size=1,
                zoom_policy="none",
            )
            old_controller = navigation._controller()
            old_name = first["managed_names"][0]
            self.fake.fail_load_path = paths[1]

            failed = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=2,
                window_size=1,
                zoom_policy="none",
                replace=1,
            )

            self.assertEqual(failed["status"], "error")
            self.assertIs(navigation._CONTROLLER, old_controller)
            self.assertEqual(old_controller.managed_names, {old_name})
            self.assertEqual(self.fake.loaded, {old_name: paths[0]})
            self.assertIn(old_name, self.fake.types)
            self.assertIn(old_name, self.fake.enabled)

    def test_active_session_requires_explicit_replace(self):
        navigation.pp_nav_start(source="groups", zoom_policy="none")
        controller = navigation._CONTROLLER

        result = navigation.pp_nav_start(
            source="objects", entries="b_model", zoom_policy="none"
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("already active", result["error"])
        self.assertIs(navigation._CONTROLLER, controller)

    def test_file_load_failure_rolls_back_cursor_and_managed_state(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("one.pdb", "two.pdb"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")
            first = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=1,
                window_size=1,
                zoom_policy="none",
            )
            controller = navigation._CONTROLLER
            first_managed = set(controller.managed_names)
            self.fake.fail_load_path = controller.session.entries[1].source_paths[0]

            result = navigation.pp_nav_next_batch()

            self.assertEqual(result["status"], "error")
            self.assertEqual(controller.session.cursor, first["cursor"])
            self.assertEqual(controller.managed_names, first_managed)
            self.assertTrue(first_managed.issubset(self.fake.types))


if __name__ == "__main__":
    unittest.main()
