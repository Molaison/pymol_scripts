import contextlib
import importlib
import io
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
        self.fail_delete_names = set()
        self.fail_enable_names = set()
        self.fail_disable_names = set()
        self.silent_enable_names = set()
        self.key_mappings = {
            "right": "scene next",
            "CTRL-left": (self.zoom, ("previous",), {}),
        }
        self.key_set_calls = []
        self.extensions = {}
        self.keyword = {}
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
        if name in self.fail_enable_names:
            raise RuntimeError(f"synthetic enable failure for {name}")
        if name in self.silent_enable_names:
            return
        self.enabled.add(name)

    def disable(self, name):
        if name in self.fail_disable_names:
            raise RuntimeError(f"synthetic disable failure for {name}")
        self.enabled.discard(name)

    def load(self, path, name, quiet=1):
        if path == self.fail_load_path:
            raise RuntimeError("synthetic load failure")
        self.types[name] = "object:molecule"
        self.loaded[name] = path
        self.enabled.add(name)

    def delete(self, name):
        if name in self.fail_delete_names:
            raise RuntimeError(f"synthetic delete failure for {name}")
        self.types.pop(name, None)
        self.loaded.pop(name, None)
        self.enabled.discard(name)

    def zoom(self, selection):
        self.zoom_calls.append(selection)

    def do(self, command, echo=1):
        marker_clause = "; /__import__('pymolplugins.targets.commands'"
        if marker_clause not in command:
            self.commands.append(command)
            return None
        original, marker_tail = command.rsplit(marker_clause, 1)
        self.commands.append(original)
        if "; silent_parser_error" in original:
            prefix = original.split(";", 1)[0].strip()
            if prefix.startswith("delete "):
                self.delete(prefix.split(None, 1)[1])
            return None
        if original.startswith("silent_parser_error "):
            # Match real PyMOL: cmd.do reports the parser failure in the console
            # but returns None and never reaches the same-line sentinel.
            return None
        from pymolplugins.targets.commands import _complete_execution
        _complete_execution(marker_tail.rsplit("'", 2)[1])
        return None

    def set_key(self, key, callback):
        self.key_set_calls.append((key, callback))
        self.key_mappings[key] = (callback, (), {})

    def extend(self, name, callback):
        self.extensions[name] = callback
        self.keyword[name] = [callback, 0, 0, ",", 11]

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

    @staticmethod
    def capture_output(function, *args, **kwargs):
        output = io.StringIO()
        errors = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            result = function(*args, **kwargs)
        return result, output.getvalue(), errors.getvalue()

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

    def test_registered_start_normalizes_quoted_entries_and_scalars(self):
        navigation.register_commands(self.fake)

        result = self.fake.extensions["pp_nav_start"](
            source='"groups"',
            entries='"root_a,root_b"',
            batch_size='"2"',
            window_size='"1"',
            stride='"1"',
            wrap='"0"',
            bind_keys='"0"',
            zoom_policy='"none"',
        )

        self.assertEqual(result["source"], "groups")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["current_entry_ids"], ["group:root_a"])
        self.assertEqual(
            set(result["visible_names"]),
            {"a_model", "a_cgo", "a_map", "a_measurement"},
        )

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

    def test_replacement_retirement_failure_transfers_retryable_ownership(self):
        with (
            tempfile.TemporaryDirectory() as old_dataset,
            tempfile.TemporaryDirectory() as new_dataset,
        ):
            old_paths = [
                os.path.join(old_dataset, name)
                for name in ("01.pdb", "02.pdb")
            ]
            new_path = os.path.join(new_dataset, "new.pdb")
            for path in old_paths + [new_path]:
                with open(path, "w", encoding="ascii") as handle:
                    handle.write("END\n")
            started = navigation.pp_nav_start(
                source="files",
                path=old_dataset,
                batch_size=2,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
            )
            old_controller = navigation._controller()
            failed_retirement = started["managed_names"][0]
            self.fake.fail_delete_names.add(failed_retirement)

            replaced, output, errors = self.capture_output(
                navigation.pp_nav_start,
                source="files",
                path=new_dataset,
                batch_size=1,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
                replace=1,
            )

            controller = navigation._controller()
            self.assertIsNot(controller, old_controller)
            self.assertTrue(replaced["cleanup_pending"])
            self.assertEqual(
                replaced["retired_file_names"], [failed_retirement]
            )
            self.assertIn(failed_retirement, replaced["cleanup_errors"])
            self.assertIn("cleanup pending", output)
            self.assertNotIn("Traceback", output + errors)
            self.assertEqual(controller.retired_file_names, {failed_retirement})
            self.assertIn(failed_retirement, self.fake.types)
            self.assertNotIn(failed_retirement, self.fake.enabled)
            self.assertIn(new_path, self.fake.loaded.values())
            self.assertEqual(old_controller.managed_names, set())

            self.fake.fail_delete_names.clear()
            stopped = navigation.pp_nav_stop()
            self.assertEqual(stopped["status"], "stopped")
            self.assertNotIn(failed_retirement, self.fake.types)

    def test_active_session_requires_explicit_replace(self):
        navigation.pp_nav_start(source="groups", zoom_policy="none")
        controller = navigation._CONTROLLER

        result = navigation.pp_nav_start(
            source="objects", entries="b_model", zoom_policy="none"
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("already active", result["error"])
        self.assertIs(navigation._CONTROLLER, controller)

    def test_expected_start_errors_are_one_line_and_preserve_active_state(self):
        navigation.pp_nav_start(
            source="groups", bind_keys=1, zoom_policy="none"
        )
        navigation.pp_nav_pin("root_a")
        navigation.pp_nav_register_hook("mark {object}")
        controller = navigation._controller()
        cursor = controller.session.cursor
        pins = set(controller.session.pinned_entry_ids)
        hooks = list(controller.hooks)
        enabled = set(self.fake.enabled)
        key_mappings = dict(self.fake.key_mappings)
        cases = {
            "active": lambda: navigation.pp_nav_start(
                source="objects", entries="b_model", zoom_policy="none"
            ),
            "missing_path": lambda: navigation.pp_nav_start(
                source="files", path="/definitely/missing", replace=1
            ),
            "parent_nested": lambda: navigation.pp_nav_start(
                source="groups", entries="root_a,nested", replace=1
            ),
            "source": lambda: navigation.pp_nav_start(source="typo", replace=1),
            "zoom": lambda: navigation.pp_nav_start(
                zoom_policy="typo", replace=1
            ),
            "bool": lambda: navigation.pp_nav_start(wrap="typo", replace=1),
            "int": lambda: navigation.pp_nav_start(
                batch_size="1.5", replace=1
            ),
            "window": lambda: navigation.pp_nav_start(
                batch_size=1, window_size=2, replace=1
            ),
        }

        for label, invoke in cases.items():
            with self.subTest(label=label):
                result, output, errors = self.capture_output(invoke)
                lines = [line for line in output.splitlines() if line]
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["scene_preserved"])
                self.assertEqual(len(lines), 1)
                self.assertIn("pp_nav_start error:", lines[0])
                self.assertIn("Next:", lines[0])
                self.assertNotIn("Traceback", output + errors)
                self.assertIs(navigation._CONTROLLER, controller)
                self.assertEqual(controller.session.cursor, cursor)
                self.assertEqual(controller.session.pinned_entry_ids, pins)
                self.assertEqual(controller.hooks, hooks)
                self.assertEqual(set(self.fake.enabled), enabled)
                self.assertEqual(self.fake.key_mappings, key_mappings)

    def test_expected_command_errors_are_trace_free_and_non_mutating(self):
        navigation.pp_nav_start(
            source="groups", bind_keys=1, zoom_policy="none"
        )
        navigation.pp_nav_register_hook("mark {object}")
        controller = navigation._controller()
        cursor = controller.session.cursor
        hooks = list(controller.hooks)
        enabled = set(self.fake.enabled)
        key_mappings = dict(self.fake.key_mappings)
        cases = {
            "bad_goto_token": lambda: navigation.pp_nav_goto("two"),
            "bad_goto_range": lambda: navigation.pp_nav_goto(99),
            "unknown_pin": lambda: navigation.pp_nav_pin("missing_entry"),
            "unknown_unpin": lambda: navigation.pp_nav_unpin("missing_entry"),
            "unknown_hook_field": lambda: navigation.pp_nav_register_hook(
                "show sticks, {target}"
            ),
            "unknown_hook": lambda: navigation.pp_nav_unregister_hook(
                "show cartoon, {object}"
            ),
            "bad_status_bool": lambda: navigation.pp_nav_status(quiet="typo"),
            "bad_stop_bool": lambda: navigation.pp_nav_stop(keep_loaded="typo"),
        }

        for label, invoke in cases.items():
            with self.subTest(label=label):
                result, output, errors = self.capture_output(invoke)
                lines = [line for line in output.splitlines() if line]
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["scene_preserved"])
                self.assertEqual(len(lines), 1)
                self.assertIn("Next:", lines[0])
                self.assertNotIn("Traceback", output + errors)
                self.assertIs(navigation._CONTROLLER, controller)
                self.assertEqual(controller.session.cursor, cursor)
                self.assertEqual(controller.hooks, hooks)
                self.assertEqual(set(self.fake.enabled), enabled)
                self.assertEqual(self.fake.key_mappings, key_mappings)

    def test_unknown_entry_reports_a_fuzzy_suggestion_without_auto_matching(self):
        navigation.pp_nav_start(source="groups", zoom_policy="none")
        controller = navigation._controller()
        enabled = set(self.fake.enabled)

        result, output, errors = self.capture_output(
            navigation.pp_nav_pin, "rot_a"
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("did you mean: root_a", result["error"])
        self.assertIn("did you mean: root_a", output)
        self.assertNotIn("Traceback", output + errors)
        self.assertEqual(controller.session.pinned_entry_ids, set())
        self.assertEqual(set(self.fake.enabled), enabled)

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

            result, output, errors = self.capture_output(
                navigation.pp_nav_next_batch
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["rollback"], "restored")
            self.assertTrue(result["scene_preserved"])
            self.assertIn("Traceback", output + errors)
            self.assertEqual(controller.session.cursor, first["cursor"])
            self.assertEqual(controller.managed_names, first_managed)
            self.assertTrue(first_managed.issubset(self.fake.types))

    def test_failed_initial_load_cleanup_retains_controller_for_stop_retry(self):
        with tempfile.TemporaryDirectory() as dataset:
            paths = [os.path.join(dataset, name) for name in ("01.pdb", "02.pdb")]
            for path in paths:
                with open(path, "w", encoding="ascii") as handle:
                    handle.write("END\n")
            first_name = navigation._file_object_name(paths[0], 0)
            self.fake.fail_load_path = paths[1]
            self.fake.fail_delete_names.add(first_name)

            failed, output, errors = self.capture_output(
                navigation.pp_nav_start,
                source="files",
                path=dataset,
                batch_size=2,
                window_size=1,
                zoom_policy="none",
            )

            controller = navigation._controller()
            self.assertEqual(failed["status"], "error")
            self.assertTrue(failed["degraded"])
            self.assertFalse(failed["scene_preserved"])
            self.assertIn("cleanup_error", failed)
            self.assertIn("pp_nav_stop", output)
            self.assertIn("Traceback", output + errors)
            self.assertTrue(controller.degraded)
            self.assertEqual(controller.degraded_reason, "start_cleanup_failed")
            self.assertIn(first_name, controller.managed_names)
            self.assertIn(first_name, self.fake.types)

            self.fake.fail_load_path = None
            self.fake.fail_delete_names.clear()
            stopped = navigation.pp_nav_stop()
            self.assertEqual(stopped["status"], "stopped")
            self.assertNotIn(first_name, self.fake.types)

    def test_partial_batch_load_cleanup_failure_retains_retryable_ownership(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("01.pdb", "02.pdb", "03.pdb", "04.pdb"):
                with open(os.path.join(dataset, name), "w", encoding="ascii") as handle:
                    handle.write("END\n")
            navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=2,
                window_size=1,
                zoom_policy="none",
            )
            controller = navigation._controller()
            third = controller.session.entries[2]
            fourth = controller.session.entries[3]
            orphan_candidate = third.object_names[0]
            self.fake.fail_load_path = fourth.source_paths[0]
            self.fake.fail_delete_names.add(orphan_candidate)

            failed, output, errors = self.capture_output(
                navigation.pp_nav_next_batch
            )

            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["rollback"], "failed")
            self.assertFalse(failed["scene_preserved"])
            self.assertTrue(failed["degraded"])
            self.assertIn("file-load cleanup failed", failed["error"])
            self.assertIn("Traceback", output + errors)
            self.assertIn(orphan_candidate, self.fake.types)
            self.assertIn(orphan_candidate, controller.managed_names)
            self.assertEqual(
                controller.loaded_entry_names[third.id], (orphan_candidate,)
            )

            self.fake.fail_load_path = None
            self.fake.fail_delete_names.clear()
            stopped = navigation.pp_nav_stop()
            self.assertEqual(stopped["status"], "stopped")
            self.assertNotIn(orphan_candidate, self.fake.types)

    def test_stop_validates_every_flag_before_mutating_the_active_session(self):
        started = navigation.pp_nav_start(
            source="groups",
            entries="root_a,root_b",
            bind_keys=1,
            zoom_policy="none",
        )
        controller = navigation._controller()
        enabled = set(self.fake.enabled)
        key_mappings = dict(self.fake.key_mappings)

        invalid_keep = navigation.pp_nav_stop(keep_loaded="typo")

        self.assertEqual(invalid_keep["status"], "error")
        self.assertIs(navigation._CONTROLLER, controller)
        self.assertEqual(set(self.fake.enabled), enabled)
        self.assertEqual(self.fake.key_mappings, key_mappings)
        self.assertTrue(controller.keys_bound)
        self.assertEqual(
            navigation.pp_nav_status(quiet=1)["cursor"], started["cursor"]
        )

        invalid_force = navigation.pp_nav_stop(force="typo")

        self.assertEqual(invalid_force["status"], "error")
        self.assertIs(navigation._CONTROLLER, controller)
        self.assertEqual(set(self.fake.enabled), enabled)
        self.assertEqual(self.fake.key_mappings, key_mappings)
        self.assertTrue(controller.keys_bound)

    def test_stop_aggregates_key_and_cleanup_errors_then_can_retry(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "one.pdb")
            with open(path, "w", encoding="ascii") as handle:
                handle.write("END\n")
            started = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=1,
                window_size=1,
                bind_keys=1,
                zoom_policy="none",
            )
            controller = navigation._controller()
            managed = started["managed_names"][0]
            live_mappings = self.fake.key_mappings
            self.fake.key_mappings = None
            self.fake.fail_delete_names.add(managed)

            failed = navigation.pp_nav_stop()

            self.assertEqual(failed["status"], "degraded")
            self.assertIs(navigation._CONTROLLER, controller)
            self.assertTrue(controller.keys_bound)
            self.assertTrue(controller.degraded)
            self.assertIn(managed, controller.managed_names)
            self.assertIn(managed, self.fake.types)
            self.assertTrue(failed["errors"]["keys"])
            self.assertTrue(failed["errors"]["cleanup"])
            self.assertEqual(failed["errors"]["visibility"], [])
            refused_transition, output, errors = self.capture_output(
                navigation.pp_nav_next
            )
            self.assertEqual(refused_transition["status"], "error")
            self.assertIn("degraded", refused_transition["error"])
            self.assertTrue(refused_transition["scene_preserved"])
            self.assertIn("Next:", output)
            self.assertNotIn("Traceback", output + errors)
            self.assertIs(navigation._CONTROLLER, controller)

            self.fake.key_mappings = live_mappings
            self.fake.fail_delete_names.clear()
            retried = navigation.pp_nav_stop()

            self.assertEqual(retried["status"], "stopped")
            self.assertIsNone(navigation._CONTROLLER)
            self.assertNotIn(managed, self.fake.types)
            self.assertEqual(self.fake.key_mappings["right"], "scene next")

    def test_stop_visibility_failure_retains_only_unrestored_ownership(self):
        navigation.pp_nav_start(
            source="groups",
            entries="root_a,root_b",
            zoom_policy="none",
        )
        controller = navigation._controller()
        self.assertNotIn("b_model", self.fake.enabled)
        self.fake.silent_enable_names.add("b_model")

        failed = navigation.pp_nav_stop()

        self.assertEqual(failed["status"], "degraded")
        self.assertIs(navigation._CONTROLLER, controller)
        self.assertEqual(failed["errors"]["cleanup"], [])
        self.assertTrue(failed["errors"]["visibility"])
        self.assertEqual(controller.managed_names, {"b_model"})
        self.assertIn("visibility did not change", failed["errors"]["visibility"][0])
        self.assertTrue(
            {
                "a_model",
                "a_cgo",
                "a_map",
                "a_measurement",
            }.issubset(self.fake.enabled)
        )

        self.fake.silent_enable_names.clear()
        retried = navigation.pp_nav_stop()

        self.assertEqual(retried["status"], "stopped")
        self.assertIsNone(navigation._CONTROLLER)
        self.assertIn("b_model", self.fake.enabled)

    def test_force_stop_explicitly_abandons_unresolved_ownership(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "one.pdb")
            with open(path, "w", encoding="ascii") as handle:
                handle.write("END\n")
            started = navigation.pp_nav_start(
                source="files",
                path=dataset,
                batch_size=1,
                window_size=1,
                zoom_policy="none",
            )
            managed = started["managed_names"][0]
            self.fake.fail_delete_names.add(managed)

            forced = navigation.pp_nav_stop(force=1)

            self.assertEqual(forced["status"], "forced_stopped")
            self.assertTrue(forced["forced"])
            self.assertIsNone(navigation._CONTROLLER)
            self.assertIn(managed, forced["remaining_managed_names"])
            self.assertIn(managed, self.fake.types)
            self.assertIsNone(forced["retry"])

    def test_nonwrapping_boundary_does_not_reapply_scene_or_hooks(self):
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model",
            batch_size=2,
            window_size=1,
            wrap=0,
            zoom_policy="none",
        )
        navigation.pp_nav_register_hook("mark {object}")
        navigation.pp_nav_next()
        calls_at_end = list(self.fake.commands)
        enabled_at_end = set(self.fake.enabled)

        boundary = navigation.pp_nav_next()

        self.assertEqual(boundary["status"], "boundary")
        self.assertTrue(boundary["no_change"])
        self.assertEqual(boundary["transition"], "next")
        self.assertEqual(self.fake.commands, calls_at_end)
        self.assertEqual(set(self.fake.enabled), enabled_at_end)

        navigation.pp_nav_goto(1)
        calls_at_start = list(self.fake.commands)
        previous_boundary = navigation.pp_nav_prev()
        self.assertEqual(previous_boundary["status"], "boundary")
        self.assertEqual(previous_boundary["transition"], "previous")
        self.assertEqual(self.fake.commands, calls_at_start)

    def test_repeated_step_and_batch_boundaries_never_run_hooks(self):
        for name in ("c_model", "d_model"):
            self.fake.types[name] = "object:molecule"
            self.fake.enabled.add(name)
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model,c_model,d_model",
            batch_size=2,
            window_size=1,
            wrap=0,
            zoom_policy="none",
        )
        navigation.pp_nav_register_hook("mark {object}")
        navigation.pp_nav_next()
        navigation.pp_nav_next_batch()
        navigation.pp_nav_next()
        commands_at_end = list(self.fake.commands)

        for invoke in (
            navigation.pp_nav_next,
            navigation.pp_nav_next,
            navigation.pp_nav_next_batch,
            navigation.pp_nav_next_batch,
        ):
            boundary = invoke()
            self.assertEqual(boundary["status"], "boundary")
        self.assertEqual(self.fake.commands, commands_at_end)

        navigation.pp_nav_prev()
        navigation.pp_nav_prev_batch()
        navigation.pp_nav_prev()
        commands_at_start = list(self.fake.commands)
        for invoke in (
            navigation.pp_nav_prev,
            navigation.pp_nav_prev,
            navigation.pp_nav_prev_batch,
            navigation.pp_nav_prev_batch,
        ):
            boundary = invoke()
            self.assertEqual(boundary["status"], "boundary")
        self.assertEqual(self.fake.commands, commands_at_start)

    def test_pin_unpin_and_clear_pins_report_no_change_without_scene_work(self):
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model",
            batch_size=2,
            window_size=1,
            zoom_policy="none",
        )
        enabled = set(self.fake.enabled)

        empty_clear = navigation.pp_nav_clear_pins()
        absent_unpin = navigation.pp_nav_unpin("b_model")
        navigation.pp_nav_pin("b_model")
        pinned_enabled = set(self.fake.enabled)
        duplicate_pin = navigation.pp_nav_pin("b_model")
        navigation.pp_nav_unpin("b_model")
        duplicate_unpin = navigation.pp_nav_unpin("b_model")
        second_clear = navigation.pp_nav_clear_pins()

        for result in (
            empty_clear,
            absent_unpin,
            duplicate_pin,
            duplicate_unpin,
            second_clear,
        ):
            self.assertEqual(result["status"], "no_change")
            self.assertTrue(result["no_change"])
        self.assertEqual(set(self.fake.enabled), enabled)
        self.assertIn("b_model", pinned_enabled)
        self.assertEqual(self.fake.commands, [])

    def test_goto_tail_then_default_pin_targets_the_requested_entry(self):
        for name in ("c_model", "d_model", "e_model"):
            self.fake.types[name] = "object:molecule"
            self.fake.enabled.add(name)
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model,c_model,d_model,e_model",
            batch_size=5,
            window_size=2,
            wrap=0,
            zoom_policy="none",
        )

        jumped = navigation.pp_nav_goto(5)
        pinned = navigation.pp_nav_pin()

        self.assertEqual(jumped["position"], 5)
        self.assertEqual(jumped["current_entry_ids"], ["object:e_model"])
        self.assertEqual(pinned["pinned_entry_ids"], ["object:e_model"])

    def test_group_batch_window_moves_every_group_as_one_entry(self):
        for group, member in (("root_c", "c_model"), ("root_d", "d_model")):
            self.fake.types[group] = "object:group"
            self.fake.types[member] = "object:molecule"
            self.fake.groups[group] = [member]
            self.fake.enabled.update({group, member})
        started = navigation.pp_nav_start(
            source="groups",
            entries="root_a,root_b,root_c,root_d",
            batch_size=2,
            window_size=2,
            stride=1,
            wrap=0,
            zoom_policy="none",
        )

        self.assertEqual(
            started["current_entry_ids"], ["group:root_a", "group:root_b"]
        )
        self.assertTrue(
            {
                "a_model",
                "a_cgo",
                "a_map",
                "a_measurement",
                "b_model",
            }.issubset(self.fake.enabled)
        )
        second = navigation.pp_nav_next()
        self.assertEqual(
            second["current_entry_ids"], ["group:root_c", "group:root_d"]
        )
        self.assertTrue({"c_model", "d_model"}.issubset(self.fake.enabled))
        self.assertNotIn("a_model", self.fake.enabled)
        self.assertEqual(navigation.pp_nav_next()["status"], "boundary")

    def test_group_membership_change_blocks_transition_before_scene_mutation(self):
        navigation.pp_nav_start(
            source="groups",
            entries="root_a,root_b",
            batch_size=2,
            window_size=1,
            wrap=0,
            zoom_policy="none",
        )
        navigation.pp_nav_register_hook("mark {object}")
        enabled_before = set(self.fake.enabled)
        self.fake.types["b_new"] = "object:molecule"
        self.fake.groups["root_b"].append("b_new")
        self.fake.enabled.add("b_new")

        stale, output, errors = self.capture_output(navigation.pp_nav_next)

        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["scene_preserved"])
        self.assertIn("Next:", output)
        self.assertNotIn("Traceback", output + errors)
        self.assertEqual(stale["cursor"], 0)
        self.assertEqual(self.fake.commands, [])
        self.assertEqual(
            set(self.fake.enabled).difference({"b_new"}), enabled_before
        )
        self.assertNotIn("b_new", stale["managed_names"])
        self.assertEqual(stale["membership_changes"]["root_b"]["added"], ["b_new"])
        status = navigation.pp_nav_status(quiet=1)
        self.assertTrue(status["membership_stale"])

    def test_checked_hook_failure_rolls_back_cursor_and_managed_visibility(self):
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model",
            batch_size=2,
            window_size=1,
            wrap=0,
            zoom_policy="none",
        )
        navigation.pp_nav_register_hook("silent_parser_error {object}")
        navigation.pp_nav_register_hook("must_not_run {object}")
        enabled_before = set(self.fake.enabled)

        failed, output, errors = self.capture_output(navigation.pp_nav_next)

        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["rollback"], "restored")
        self.assertTrue(failed["scene_preserved"])
        self.assertIn("Next:", output)
        self.assertNotIn("Traceback", output + errors)
        self.assertEqual(failed["hook_policy"], "stop")
        self.assertEqual(failed["rollback_scope"], "navigation_scene_only")
        self.assertIn("not generically reversible", failed["side_effect_warning"])
        self.assertEqual(failed["cursor"], 0)
        self.assertEqual(set(self.fake.enabled), enabled_before)
        self.assertEqual(self.fake.commands, ["silent_parser_error b_model"])
        self.assertFalse(
            any(name.startswith("ppfe_ack_") for name in self.fake.types)
        )
        self.assertIn("parser did not complete", failed["error"])

    def test_checked_hook_failure_also_rolls_back_pin_mutation(self):
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model",
            batch_size=2,
            window_size=1,
            wrap=0,
            zoom_policy="none",
        )
        navigation.pp_nav_register_hook("silent_parser_error {object}")
        enabled_before = set(self.fake.enabled)

        failed, output, errors = self.capture_output(
            navigation.pp_nav_pin, "b_model"
        )

        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["rollback"], "restored")
        self.assertTrue(failed["scene_preserved"])
        self.assertNotIn("Traceback", output + errors)
        self.assertEqual(failed["pinned_entry_ids"], [])
        self.assertEqual(set(self.fake.enabled), enabled_before)

    def test_unrecoverable_hook_mutation_marks_controller_degraded(self):
        navigation.pp_nav_start(
            source="objects",
            entries="a_model,b_model",
            batch_size=2,
            window_size=1,
            wrap=0,
            zoom_policy="none",
        )
        controller = navigation._controller()
        navigation.pp_nav_register_hook(
            "delete {object}; silent_parser_error"
        )

        failed, output, errors = self.capture_output(navigation.pp_nav_next)

        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["rollback"], "failed")
        self.assertFalse(failed["scene_preserved"])
        self.assertIn("Traceback", output + errors)
        self.assertIn("disappeared", failed["rollback_error"])
        self.assertTrue(failed["degraded"])
        self.assertTrue(controller.degraded)
        self.assertEqual(controller.session.cursor, 0)
        self.assertNotIn("b_model", self.fake.types)


if __name__ == "__main__":
    unittest.main()
