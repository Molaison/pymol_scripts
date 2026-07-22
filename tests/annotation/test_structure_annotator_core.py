import importlib
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
        pass

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


if __name__ == "__main__":
    unittest.main()
