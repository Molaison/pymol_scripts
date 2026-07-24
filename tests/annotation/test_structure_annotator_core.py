import importlib
import json
import os
import sys
import tempfile
import types
import unittest


class DummyWidget:
    def __init__(self, *args, **kwargs):
        pass


class DummyApplication:
    @staticmethod
    def instance():
        return None

    @staticmethod
    def activeWindow():
        return None


class FakeCmd:
    def __init__(self):
        self.types = {"overlay": "object:cgo"}
        self.enabled = {"overlay"}
        self.loaded_paths = {}
        self.deleted = []
        self.extensions = {}
        self.commands = []
        self.key_mappings = {"ALT-left": "previous user binding"}

    def get_object_list(self, selection="all"):
        return [name for name, kind in self.types.items() if kind == "object:molecule"]

    def get_names(self, mode="objects", enabled_only=0, selection=""):
        values = list(self.types)
        return [name for name in values if name in self.enabled] if enabled_only else values

    def load(self, path, name, quiet=1):
        self.types[name] = "object:molecule"
        self.loaded_paths[name] = path
        self.enabled.add(name)

    def delete(self, name):
        self.deleted.append(name)
        self.types.pop(name, None)
        self.loaded_paths.pop(name, None)
        self.enabled.discard(name)

    def enable(self, name):
        self.enabled.add(name)

    def disable(self, name):
        self.enabled.discard(name)

    def sync(self):
        pass

    def refresh(self):
        pass

    def do(self, command):
        self.commands.append(command)

    def extend(self, name, callback):
        self.extensions[name] = callback

    def set_key(self, key, callback):
        self.key_mappings[key] = (callback, (), {})


def _qt_modules():
    qt_widgets = types.SimpleNamespace(QApplication=DummyApplication)
    for name in (
        "QCheckBox",
        "QComboBox",
        "QDialog",
        "QFileDialog",
        "QFormLayout",
        "QGridLayout",
        "QGroupBox",
        "QHBoxLayout",
        "QLabel",
        "QLineEdit",
        "QPlainTextEdit",
        "QPushButton",
        "QSpinBox",
        "QVBoxLayout",
        "QWidget",
    ):
        setattr(qt_widgets, name, DummyWidget)
    return types.SimpleNamespace(QTimer=DummyWidget), qt_widgets


class StructureAnnotatorCoreTests(unittest.TestCase):
    def setUp(self):
        self.history_temporary = tempfile.TemporaryDirectory()
        self.previous_history_path = os.environ.get(
            "PYMOLPLUGINS_ANNOTATOR_HISTORY"
        )
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = os.path.join(
            self.history_temporary.name, "history.json"
        )
        self.fake = FakeCmd()
        self.saved = {name: sys.modules.get(name) for name in ("pymol", "pymol.Qt")}
        pymol = types.ModuleType("pymol")
        pymol.cmd = self.fake
        qt_core, qt_widgets = _qt_modules()
        qt = types.ModuleType("pymol.Qt")
        qt.QtCore = qt_core
        qt.QtWidgets = qt_widgets
        sys.modules["pymol"] = pymol
        sys.modules["pymol.Qt"] = qt
        sys.modules.pop("pymolplugins.annotation.plugin", None)
        self.module = importlib.import_module("pymolplugins.annotation.plugin")
        self.module.STATE.preload_enabled = False
        self.module.STATE.step_cmd = ""
        self.module.STATE.auto_advance = False

    def tearDown(self):
        try:
            self.module.sa_unbind_keys()
            self.module._delete_managed_objects(False)
            self.module._reset_bg_cache()
        finally:
            sys.modules.pop("pymolplugins.annotation.plugin", None)
            for name, value in self.saved.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
            if self.previous_history_path is None:
                os.environ.pop("PYMOLPLUGINS_ANNOTATOR_HISTORY", None)
            else:
                os.environ[
                    "PYMOLPLUGINS_ANNOTATOR_HISTORY"
                ] = self.previous_history_path
            self.history_temporary.cleanup()

    @staticmethod
    def _write_pdb(path, marker):
        with open(path, "w", encoding="ascii") as handle:
            handle.write(f"REMARK {marker}\nEND\n")

    def test_same_basename_in_new_dataset_never_reuses_old_object(self):
        with tempfile.TemporaryDirectory() as root:
            first_dir = os.path.join(root, "first")
            second_dir = os.path.join(root, "second")
            os.mkdir(first_dir)
            os.mkdir(second_dir)
            first = os.path.join(first_dir, "model.pdb")
            second = os.path.join(second_dir, "model.pdb")
            self._write_pdb(first, "FIRST")
            self._write_pdb(second, "SECOND")

            self.module.sa_load_dir(first_dir)
            first_object = self.module.STATE.current_object
            self.module.sa_load_dir(second_dir)
            second_object = self.module.STATE.current_object

            self.assertNotEqual(first_object, second_object)
            self.assertIn(first_object, self.fake.deleted)
            self.assertEqual(self.fake.loaded_paths[second_object], second)
            self.assertEqual(self.module.STATE.current_file(), second)

    def test_annotation_aborts_if_expected_object_is_not_enabled(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "model.pdb")
            self._write_pdb(path, "ORIGINAL")
            self.module.sa_load_dir(dataset)
            current = self.module.STATE.current_object
            self.fake.disable(current)

            self.module.sa_annotate("Good")

            with open(path, "r", encoding="ascii") as handle:
                self.assertNotIn("PYMOL_ANNOTATOR", handle.read())

    def test_key_lease_is_explicit_and_restores_previous_bindings(self):
        self.assertEqual(self.fake.key_mappings, {"ALT-left": "previous user binding"})
        result = self.module.sa_bind_keys()
        self.assertEqual(result["status"], "bound")
        self.assertIn("ALT-1", self.fake.key_mappings)
        self.module.sa_unbind_keys()
        self.assertEqual(self.fake.key_mappings, {"ALT-left": "previous user binding"})

    def test_loading_dataset_creates_versioned_hidden_state(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "model.pdb")
            self._write_pdb(path, "MODEL")

            self.module.sa_load_dir(dataset)

            state_path = self.module.dataset_state_path(dataset)
            self.assertEqual(
                state_path,
                os.path.join(dataset, ".pymolplugins", "structure_annotator.json"),
            )
            with open(state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(
                payload["schema"],
                "pymolplugins.structure-annotator-state/1.0",
            )
            self.assertEqual(payload["position"], {"file": "model.pdb", "index": 0})
            self.assertEqual(payload["config"]["step_cmd"], "")
            self.assertNotIn("files", payload)
            self.assertNotIn("managed_objects", payload)
            self.assertNotIn("undo_history", payload)
            self.assertEqual(
                [name for name in os.listdir(os.path.dirname(state_path)) if name.endswith(".tmp")],
                [],
            )

    def test_oversized_dataset_state_is_rejected_before_replace(self):
        from pymolplugins.annotation.state_store import MAX_STATE_BYTES

        with tempfile.TemporaryDirectory() as dataset:
            with self.assertRaisesRegex(
                self.module.StateStoreError, "state document exceeds"
            ):
                self.module.write_dataset_state(
                    dataset,
                    {
                        "config": {
                            "step_cmd": "x" * (MAX_STATE_BYTES + 1)
                        },
                        "position": {"file": "", "index": 0},
                    },
                )
            self.assertFalse(
                os.path.exists(self.module.dataset_state_path(dataset))
            )

    def test_each_dataset_restores_its_commands_config_and_position(self):
        with tempfile.TemporaryDirectory() as root:
            first_dir = os.path.join(root, "first")
            second_dir = os.path.join(root, "second")
            os.mkdir(first_dir)
            os.mkdir(second_dir)
            for name in ("a.pdb", "b.pdb"):
                self._write_pdb(os.path.join(first_dir, name), name)
            self._write_pdb(os.path.join(second_dir, "other.pdb"), "OTHER")

            self.module.sa_load_dir(first_dir)
            self.module.STATE.label_name = "review"
            self.module.STATE.values_text = "Keep,Reject"
            self.module.STATE.step_cmd = "color cyan, {object}"
            self.module.STATE.bucket_cmds = {
                "Keep": "show sticks, {object}",
                "Reject": "hide everything, {object}",
            }
            self.module.STATE.auto_advance = True
            self.module.STATE.preload_ahead = 4
            self.module.STATE.undo_history.append({"runtime_only": True})
            self.module.sa_goto(2)
            first_state_path = self.module.sa_save_state()

            self.module.sa_load_dir(second_dir)
            self.module.STATE.label_name = "second"
            self.module.STATE.step_cmd = "zoom {object}"
            self.module.STATE.bucket_cmds = {"Good": "orient {object}"}
            second_state_path = self.module.sa_save_state()

            self.fake.commands.clear()
            self.module.sa_load_dir(first_dir)

            self.assertNotEqual(first_state_path, second_state_path)
            self.assertEqual(self.module.STATE.label_name, "review")
            self.assertEqual(self.module.STATE.values_text, "Keep,Reject")
            self.assertEqual(self.module.STATE.step_cmd, "color cyan, {object}")
            self.assertEqual(
                self.module.STATE.bucket_cmds,
                {
                    "Keep": "show sticks, {object}",
                    "Reject": "hide everything, {object}",
                },
            )
            self.assertTrue(self.module.STATE.auto_advance)
            self.assertEqual(self.module.STATE.preload_ahead, 4)
            self.assertEqual(os.path.basename(self.module.STATE.current_file()), "b.pdb")
            self.assertEqual(self.module.STATE.index, 1)
            self.assertEqual(self.module.STATE.undo_history, [])
            self.assertEqual(len(self.fake.commands), 1)
            self.assertRegex(self.fake.commands[0], r"^color cyan, sa_00002_b_")

    def test_invalid_state_warns_but_does_not_block_loading(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "model.pdb")
            self._write_pdb(path, "MODEL")
            state_path = self.module.dataset_state_path(dataset)
            os.makedirs(os.path.dirname(state_path))
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")

            self.module.sa_load_dir(dataset)

            self.assertEqual(self.module.STATE.current_file(), path)
            with open(state_path, "r", encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(
                repaired["schema"],
                "pymolplugins.structure-annotator-state/1.0",
            )

    def test_state_position_falls_back_when_saved_file_disappears(self):
        with tempfile.TemporaryDirectory() as dataset:
            first = os.path.join(dataset, "a.pdb")
            second = os.path.join(dataset, "b.pdb")
            self._write_pdb(first, "A")
            self._write_pdb(second, "B")
            self.module.sa_load_dir(dataset)
            self.module.sa_goto(2)

            os.unlink(second)
            self.module.sa_load_dir(dataset)

            self.assertEqual(self.module.STATE.current_file(), first)
            self.assertEqual(self.module.STATE.index, 0)

    def test_global_history_reopens_last_dataset_config_and_cursor(self):
        with tempfile.TemporaryDirectory() as dataset:
            for name in ("a.pdb", "b.pdb"):
                self._write_pdb(os.path.join(dataset, name), name)
            self.module.sa_load_dir(dataset)
            self.module.STATE.label_name = "history_review"
            self.module.STATE.values_text = "Pass,Fail"
            self.module.STATE.step_cmd = "show cartoon, {object}"
            self.module.STATE.bucket_cmds = {"Pass": "color cyan, {object}"}
            self.module.STATE.restore_last_dataset = True
            self.module.sa_goto(2)
            self.module.sa_save_state()

            history_result = self.module.sa_history_search("step_cmd", "cartn")
            self.assertEqual(history_result["status"], "ok")
            self.assertEqual(
                history_result["suggestions"][0], "show cartoon, {object}"
            )

            self.module._delete_managed_objects(False)
            self.module.STATE.files = []
            self.module.STATE.navigation_session = None
            self.module.STATE.current_object = None
            self.module.STATE.dataset_dir = "."
            self.module.STATE.label_name = "wrong"
            self.module.STATE.step_cmd = ""
            self.fake.commands.clear()

            restored = self.module._restore_latest_config_from_history()

            self.assertTrue(restored)
            self.assertEqual(self.module.STATE.label_name, "history_review")
            self.assertEqual(self.module.STATE.step_cmd, "show cartoon, {object}")
            self.assertEqual(os.path.basename(self.module.STATE.current_file()), "b.pdb")
            self.assertRegex(self.fake.commands[-1], r"^show cartoon, sa_00002_b_")


if __name__ == "__main__":
    unittest.main()
