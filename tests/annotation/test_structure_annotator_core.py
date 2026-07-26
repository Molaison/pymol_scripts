import csv
import concurrent.futures
import importlib
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from pymolplugins.annotation.state_store import STATE_SCHEMA


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
        self.fail_next_load = False

    def get_object_list(self, selection="all"):
        return [name for name, kind in self.types.items() if kind == "object:molecule"]

    def get_names(self, mode="objects", enabled_only=0, selection=""):
        values = list(self.types)
        return [name for name in values if name in self.enabled] if enabled_only else values

    def load(self, path, name, quiet=1):
        if self.fail_next_load:
            self.fail_next_load = False
            raise RuntimeError("synthetic annotator load failure")
        self.types[name] = "object:molecule"
        self.loaded_paths[name] = path
        self.enabled.add(name)

    def delete(self, name):
        self.deleted.append(name)
        self.types.pop(name, None)
        self.loaded_paths.pop(name, None)
        self.enabled.discard(name)

    def set_name(self, old_name, new_name):
        self.types[new_name] = self.types.pop(old_name)
        if old_name in self.loaded_paths:
            self.loaded_paths[new_name] = self.loaded_paths.pop(old_name)
        if old_name in self.enabled:
            self.enabled.discard(old_name)
            self.enabled.add(new_name)

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
    return types.SimpleNamespace(
        QTimer=DummyWidget,
        QAbstractTableModel=DummyWidget,
    ), qt_widgets


class StructureAnnotatorCoreTests(unittest.TestCase):
    def setUp(self):
        self.history_temporary = tempfile.TemporaryDirectory()
        self.previous_history_path = os.environ.get(
            "PYMOLPLUGINS_ANNOTATOR_HISTORY"
        )
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = os.path.join(
            self.history_temporary.name, "history.json"
        )
        self.previous_mapping_path = os.environ.get(
            "PYMOLPLUGINS_ANNOTATOR_MAPPINGS"
        )
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = os.path.join(
            self.history_temporary.name, "path_mappings.json"
        )
        self.previous_cache_path = os.environ.get(
            "PYMOLPLUGINS_STRUCTURE_CACHE_DIR"
        )
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = os.path.join(
            self.history_temporary.name, "structure-cache"
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
            if self.previous_mapping_path is None:
                os.environ.pop("PYMOLPLUGINS_ANNOTATOR_MAPPINGS", None)
            else:
                os.environ[
                    "PYMOLPLUGINS_ANNOTATOR_MAPPINGS"
                ] = self.previous_mapping_path
            if self.previous_cache_path is None:
                os.environ.pop("PYMOLPLUGINS_STRUCTURE_CACHE_DIR", None)
            else:
                os.environ[
                    "PYMOLPLUGINS_STRUCTURE_CACHE_DIR"
                ] = self.previous_cache_path
            self.history_temporary.cleanup()

    @staticmethod
    def _write_pdb(path, marker):
        with open(path, "w", encoding="ascii") as handle:
            handle.write(f"REMARK {marker}\nEND\n")

    def _session_signature(self):
        state = self.module.STATE
        navigation = state.navigation_session
        materialized = tuple(
            sorted(
                (
                    entry_id,
                    value.path,
                    value.mode,
                    value.cache_key,
                    value.cache_hit,
                )
                for entry_id, value in state.materialized_paths.items()
            )
        )
        return {
            "source_mode": state.source_mode,
            "dataset_dir": state.dataset_dir,
            "csv_path": state.csv_path,
            "path_column": state.path_column,
            "id_column": state.id_column,
            "display_columns": tuple(state.display_columns),
            "csv_encoding": state.csv_encoding,
            "csv_delimiter": state.csv_delimiter,
            "source_base": state.source_base,
            "mapping_profile": state.mapping_profile,
            "filter_text": state.filter_text,
            "sort_column": state.sort_column,
            "sort_descending": state.sort_descending,
            "stage_root": state.stage_root,
            "stage_alias": state.stage_alias,
            "dataset": state.dataset,
            "dataset_generation": state.dataset_generation,
            "all_entry_ids": tuple(entry.id for entry in state.all_entries),
            "entry_ids": tuple(entry.id for entry in state.entries),
            "files": tuple(state.files),
            "index": state.index,
            "current_entry_id": state.current_entry_id(),
            "current_object": state.current_object,
            "managed_objects": tuple(state.managed_objects),
            "undo_history": tuple(dict(entry) for entry in state.undo_history),
            "annotated_indices": frozenset(state.annotated_indices),
            "annotated_entry_ids": frozenset(state.annotated_entry_ids),
            "row_store_identity": id(state.row_annotation_store),
            "row_store_path": (
                state.row_annotation_store.path
                if state.row_annotation_store is not None
                else ""
            ),
            "materialized": materialized,
            "materialization_errors": tuple(sorted(state.materialization_errors.items())),
            "navigation_identity": id(navigation),
            "navigation_generation": state.navigation_generation,
            "navigation_cursor": navigation.cursor if navigation is not None else None,
            "navigation_ids": (
                tuple(entry.id for entry in navigation.entries)
                if navigation is not None
                else ()
            ),
            "config": self.module._persisted_config(),
        }

    def _pymol_scene_signature(self):
        return {
            "types": dict(self.fake.types),
            "enabled": frozenset(self.fake.enabled),
            "loaded_paths": dict(self.fake.loaded_paths),
            "current_object": self.module.STATE.current_object,
        }

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
            self.assertNotIn(first_object, self.fake.types)
            self.assertTrue(
                any(name.startswith("sa_tx_") for name in self.fake.deleted)
            )
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

    def test_qt_class_enum_supports_qt5_flat_and_qt6_scoped_values(self):
        flat = types.SimpleNamespace(NoEditTriggers=17)

        class Scoped:
            class EditTrigger:
                NoEditTriggers = 23

        self.assertEqual(
            self.module._qt_class_enum(flat, "EditTrigger", "NoEditTriggers"),
            17,
        )
        self.assertEqual(
            self.module._qt_class_enum(Scoped, "EditTrigger", "NoEditTriggers"),
            23,
        )

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
                STATE_SCHEMA,
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
                STATE_SCHEMA,
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

    def test_deferred_history_restore_recovers_intent_without_source_probe(self):
        remote_csv = r"K:\remote dataset\scores.csv"
        config = self.module._persisted_config()
        config.update(
            {
                "source_mode": "csv",
                "csv_path": remote_csv,
                "path_column": "structure_path",
                "id_column": "case",
                "display_columns": ["score"],
                "mapping_profile": "x_k",
                "restore_last_dataset": True,
            }
        )
        self.module.record_config_history(
            r"K:\remote dataset",
            config,
            source_mode="csv",
            csv_path=remote_csv,
            path_column="structure_path",
            id_column="case",
            display_columns=["score"],
            mapping_profile="x_k",
        )
        self.module.STATE.files = []
        self.module.STATE.navigation_session = None

        with mock.patch.object(
            self.module,
            "_long_path",
            side_effect=AssertionError("deferred restore probed the remote source"),
        ):
            pending = self.module._restore_latest_config_from_history(
                defer_dataset=True
            )

        self.assertTrue(pending)
        self.assertEqual(self.module.STATE.source_mode, "csv")
        self.assertEqual(self.module.STATE.csv_path, remote_csv)
        self.assertEqual(self.module.STATE.display_columns, ["score"])

    def test_csv_preview_does_not_change_existing_session_or_pymol_scene(self):
        with tempfile.TemporaryDirectory() as root:
            dataset = os.path.join(root, "directory dataset")
            os.mkdir(dataset)
            for name in ("a.pdb", "b.pdb"):
                self._write_pdb(os.path.join(dataset, name), name)
            self.module.sa_load_dir(dataset)
            self.module.sa_goto(2)
            self.module.STATE.undo_history.append({"marker": "keep-preview-pure"})

            preview_structure = os.path.join(root, "preview.pdb")
            self._write_pdb(preview_structure, "PREVIEW")
            csv_path = os.path.join(root, "preview scores.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case,score\n"
                    f"{preview_structure},preview_row,0.75\n"
                )

            state_path = self.module.dataset_state_path(dataset)
            history_path = os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"]
            mapping_path = os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"]
            self.assertFalse(os.path.exists(mapping_path))
            with open(state_path, "rb") as handle:
                state_before = handle.read()
            with open(history_path, "rb") as handle:
                history_before = handle.read()
            session_before = self._session_signature()
            scene_before = self._pymol_scene_signature()
            fake_before = {
                "deleted": list(self.fake.deleted),
                "commands": list(self.fake.commands),
                "key_mappings": dict(self.fake.key_mappings),
            }

            preview = self.module.sa_csv_preview(
                csv_path,
                "structure_path",
                "case",
                "score",
            )

            self.assertEqual(preview["status"], "ok")
            self.assertEqual(preview["row_count"], 1)
            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)
            self.assertEqual(
                {
                    "deleted": self.fake.deleted,
                    "commands": self.fake.commands,
                    "key_mappings": self.fake.key_mappings,
                },
                fake_before,
            )
            with open(state_path, "rb") as handle:
                self.assertEqual(handle.read(), state_before)
            with open(history_path, "rb") as handle:
                self.assertEqual(handle.read(), history_before)
            self.assertFalse(
                os.path.exists(
                    self.module.dataset_state_path(csv_path, source_mode="csv")
                )
            )
            self.assertFalse(os.path.exists(mapping_path))

    def test_csv_prepare_is_pure_and_honors_pre_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "prepared.pdb")
            self._write_pdb(structure, "PREPARED")
            csv_path = os.path.join(root, "prepared.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case,score\n"
                    f"{structure},prepared,0.91\n"
                )
            runtime = self.module._capture_csv_runtime_context()
            session_before = self._session_signature()
            scene_before = self._pymol_scene_signature()

            with mock.patch.object(
                self.fake,
                "load",
                side_effect=AssertionError("prepare called cmd.load"),
            ):
                plan = self.module._prepare_csv_load(
                    csv_path,
                    "structure_path",
                    "case",
                    "score",
                    "utf-8-sig",
                    "auto",
                    "x_k",
                    "",
                    "",
                    "",
                    runtime,
                )

            self.assertEqual(plan.dataset.count, 1)
            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)

            cancelled = threading.Event()
            cancelled.set()
            with mock.patch.object(
                self.module,
                "_csv_file_fingerprint",
                side_effect=AssertionError("pre-cancel still probed the CSV"),
            ):
                with self.assertRaises(self.module._CsvPreparationCancelled):
                    self.module._prepare_csv_preview(
                        csv_path,
                        "structure_path",
                        cancel_event=cancelled,
                    )
                with self.assertRaises(self.module._CsvPreparationCancelled):
                    self.module._prepare_csv_load(
                        csv_path,
                        "structure_path",
                        "",
                        "",
                        "utf-8-sig",
                        "auto",
                        "x_k",
                        "",
                        "",
                        "",
                        runtime,
                        cancel_event=cancelled,
                    )
            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)

    def test_csv_async_cancel_unregisters_before_late_completion(self):
        cancel_event = threading.Event()
        completed = []
        panel = types.SimpleNamespace(
            _complete_csv_task=lambda *args: completed.append(args)
        )
        job_id = "cancel-registry"
        self.module._CSV_ASYNC_JOBS[job_id] = {
            "panel": lambda: panel,
            "cancel_event": cancel_event,
        }

        self.assertTrue(self.module._cancel_csv_async_job(job_id))
        self.assertTrue(cancel_event.is_set())
        self.assertNotIn(job_id, self.module._CSV_ASYNC_JOBS)
        self.assertFalse(self.module._cancel_csv_async_job(job_id))

        self.module._complete_csv_async_job(job_id, object(), None)
        self.assertEqual(completed, [])
        self.assertNotIn(job_id, self.module._CSV_ASYNC_JOBS)

    def test_csv_prepare_rejects_source_changed_during_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "model.pdb")
            self._write_pdb(structure, "MODEL")
            csv_path = os.path.join(root, "changing.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(f"structure_path,score\n{structure},0.1\n")
            original_resolve = self.module._resolve_csv_dataset

            def resolve_then_change(*args, **kwargs):
                dataset = original_resolve(*args, **kwargs)
                with open(csv_path, "a", encoding="utf-8", newline="") as handle:
                    handle.write("\n")
                return dataset

            with mock.patch.object(
                self.module,
                "_resolve_csv_dataset",
                side_effect=resolve_then_change,
            ):
                with self.assertRaisesRegex(RuntimeError, "CSV changed"):
                    self.module._prepare_csv_preview(
                        csv_path,
                        "structure_path",
                    )

    def test_load_prepare_reads_annotations_after_materialization(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "model.pdb")
            self._write_pdb(structure, "MODEL")
            csv_path = os.path.join(root, "annotation timing.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(f"structure_path,case\n{structure},model\n")
            original_materialize = self.module._materialize_entry

            def materialize_then_annotate(entry, context, **kwargs):
                materialized = original_materialize(
                    entry, context, **kwargs
                )
                store = self.module.RowAnnotationStore(
                    self.module.annotation_sidecar_path(csv_path)
                )
                store.set(entry.id, "quality", "Keep")
                return materialized

            with mock.patch.object(
                self.module,
                "_materialize_entry",
                side_effect=materialize_then_annotate,
            ):
                plan = self.module._prepare_csv_load(
                    csv_path,
                    "structure_path",
                    "case",
                    "",
                    "utf-8-sig",
                    "auto",
                    "x_k",
                    "",
                    "",
                    "",
                    self.module._capture_csv_runtime_context(),
                )

            self.assertEqual(
                plan.annotated_entry_ids,
                (plan.selected_entry.id,),
            )

    def test_annotation_generation_is_monotonic_for_set_and_undo(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "model.pdb")
            self._write_pdb(structure, "MODEL")
            csv_path = os.path.join(root, "generation.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(f"structure_path\n{structure}\n")
            self.module.sa_load_csv(csv_path, "structure_path")
            before = self.module.STATE.annotation_generation

            self.module.sa_annotate("Keep")
            after_set = self.module.STATE.annotation_generation
            self.module.sa_undo()
            after_undo = self.module.STATE.annotation_generation

            self.assertEqual(after_set, before + 1)
            self.assertEqual(after_undo, before + 2)

    def test_invalid_filter_and_failed_swap_preserve_keep_previous_scene(self):
        with tempfile.TemporaryDirectory() as root:
            first_dir = os.path.join(root, "first")
            second_dir = os.path.join(root, "second")
            os.mkdir(first_dir)
            os.mkdir(second_dir)
            for name in ("a.pdb", "b.pdb"):
                self._write_pdb(os.path.join(first_dir, name), name)
            self._write_pdb(os.path.join(second_dir, "new.pdb"), "NEW")

            self.module.STATE.keep_previous = True
            self.module.sa_load_dir(first_dir)
            self.module.sa_goto(2)
            self.assertEqual(len(self.module.STATE.managed_objects), 2)
            session_before = self._session_signature()
            scene_before = self._pymol_scene_signature()

            result = self.module.sa_filter("", "", "not-a-boolean")

            self.assertEqual(result["status"], "error")
            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)

            self.fake.fail_next_load = True
            self.module.sa_load_dir(second_dir)

            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)
            self.assertEqual(len(self.module.STATE.managed_objects), 2)

    def test_materialization_future_wait_is_finalized_and_removed(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = os.path.join(dataset, "model.pdb")
            self._write_pdb(path, "MODEL")
            self.module.sa_load_dir(dataset)
            entry = self.module.STATE.current_entry()
            materialized = self.module._get_direct_materializer().materialize(path)
            generation = self.module._materialization_generation
            key = (generation, entry.id)

            class PendingResult:
                @staticmethod
                def done():
                    return False

                @staticmethod
                def result():
                    return materialized

            self.module.STATE.materialized_paths = {}
            self.module._materialization_futures = {key: PendingResult()}

            result = self.module._materialize_index(0, allow_background=False)

            self.assertIs(result, materialized)
            self.assertIs(
                self.module.STATE.materialized_paths[entry.id], materialized
            )
            self.assertNotIn(key, self.module._materialization_futures)

    def test_structure_cache_singleton_initialization_is_thread_safe(self):
        previous_cache = self.module._structure_cache
        previous_copy = self.module._copy_materializer
        sentinel = object()
        construct_calls = []

        def construct_cache():
            construct_calls.append(threading.get_ident())
            threading.Event().wait(0.01)
            return sentinel

        try:
            self.module._structure_cache = None
            self.module._copy_materializer = None
            with mock.patch.object(
                self.module,
                "PersistentStructureCache",
                side_effect=construct_cache,
            ):
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=8
                ) as executor:
                    results = list(
                        executor.map(
                            lambda _index: self.module._get_structure_cache(),
                            range(16),
                        )
                    )
            self.assertEqual(len(construct_calls), 1)
            self.assertTrue(all(result is sentinel for result in results))
        finally:
            self.module._structure_cache = previous_cache
            self.module._copy_materializer = previous_copy

    def test_cache_identity_uses_resolved_target_and_direct_k_is_remote(self):
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "first.pdb")
            second = os.path.join(root, "second.pdb")
            self._write_pdb(first, "AAAA")
            self._write_pdb(second, "BBBB")
            fixed_ns = 1_700_000_000_000_000_000
            os.utime(first, ns=(fixed_ns, fixed_ns))
            os.utime(second, ns=(fixed_ns, fixed_ns))

            def entry(entry_id, path):
                resolution = self.module.PathResolution(
                    raw_path="/remote/model.pdb",
                    resolved_path=path,
                    status="available",
                    provider="sshfs-win",
                    method="mapping",
                    matched_rule="remote",
                )
                return self.module.AnnotationEntry(
                    id=entry_id,
                    ordinal=0,
                    display_name="model.pdb",
                    source_path="/remote/model.pdb",
                    source_kind="csv",
                    resolved_path=path,
                    path_resolution=resolution,
                    availability="available",
                )

            context = self.module._MaterializationContext(
                generation=self.module._materialization_generation,
                cache_enabled=True,
                stage_root="",
                stage_alias="",
            )
            first_result = self.module._materialize_entry(
                entry("first", first), context
            )
            second_result = self.module._materialize_entry(
                entry("second", second), context
            )

            self.assertNotEqual(first_result.cache_key, second_result.cache_key)
            with open(first_result.path, "rb") as handle:
                first_bytes = handle.read()
            with open(second_result.path, "rb") as handle:
                second_bytes = handle.read()
            self.assertNotEqual(first_bytes, second_bytes)
            self.assertTrue(
                self.module._is_remote_host_path("K:\\models\\a.pdb", "windows-host")
            )
            self.assertFalse(
                self.module._is_remote_host_path("C:\\models\\a.pdb", "windows-host")
            )

    def test_wsl_stage_resolution_stats_missing_paths_and_can_be_unchecked(self):
        entry = self.module.AnnotationEntry(
            id="stage-entry",
            ordinal=0,
            display_name="missing.pdb",
            source_path="/allowed/missing.pdb",
            source_kind="csv",
        )
        failure = self.module.WorkerRequestError(
            "not found",
            code="stage_file_not_found",
            endpoint="/v1/stage/stat",
            http_status=404,
        )
        with mock.patch.object(
            self.module, "stat_staged_file", side_effect=failure
        ) as stat_call:
            missing = self.module._stage_resolution(
                entry,
                "/allowed",
                "structures",
                require_exists=True,
            )
            unchecked = self.module._stage_resolution(
                entry,
                "/allowed",
                "structures",
                require_exists=False,
            )

        self.assertEqual(missing.status, "missing")
        self.assertIn("stage_file_not_found", missing.error)
        self.assertFalse(missing.is_resolved)
        self.assertEqual(unchecked.status, "unchecked")
        self.assertTrue(unchecked.is_resolved)
        stat_call.assert_called_once_with("structures", "missing.pdb")

    def test_csv_load_reuses_fresh_stage_stat_for_preflight_materialization(self):
        with tempfile.TemporaryDirectory() as root:
            cached_path = os.path.join(root, "cached.pdb")
            self._write_pdb(cached_path, "STAGED")
            csv_path = os.path.join(root, "staged.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    "/allowed/nested/model.pdb,stage-row\n"
                )
            size = os.path.getsize(cached_path)
            observed = types.SimpleNamespace(
                token="v1-" + "a" * 64,
                device=1,
                inode=2,
                size=size,
                mtime_ns=3,
                mode=0o100644,
            )
            identity = self.module.SourceIdentity(
                provider="wsl-stage:structures",
                canonical_source="structures/nested/model.pdb",
                size=size,
                mtime_ns=3,
                version_token=observed.token,
            )
            materialized = self.module.MaterializedStructure(
                source_path="/allowed/nested/model.pdb",
                path=cached_path,
                identity=identity,
                mode="cache",
                cache_key=identity.cache_key,
                cache_hit=True,
            )

            with mock.patch.object(
                self.module, "stat_staged_file", return_value=observed
            ) as stat_call, mock.patch.object(
                self.module, "_materialize_entry", return_value=materialized
            ) as materialize_call:
                loaded = self.module.sa_load_csv(
                    csv_path,
                    "structure_path",
                    "case",
                    stage_root="/allowed",
                    stage_alias="structures",
                )

            self.assertEqual(loaded["status"], "loaded")
            stat_call.assert_called_once_with("structures", "nested/model.pdb")
            materialize_call.assert_called_once()
            self.assertIs(
                materialize_call.call_args.kwargs["staged_source_identity"],
                observed,
            )

    def test_csv_stage_batch_deduplicates_canonical_paths_and_keeps_row_errors(self):
        with tempfile.TemporaryDirectory() as root:
            csv_path = os.path.join(root, "batch staged.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    "/allowed/a/model.pdb,first\n"
                    "/allowed/a/../a/model.pdb,canonical-equivalent\n"
                    "/allowed/a/model.pdb,duplicate\n"
                    "/allowed/missing.pdb,missing\n"
                )
            observed = types.SimpleNamespace(
                token="v1-" + "d" * 64,
                device=1,
                inode=2,
                size=3,
                mtime_ns=4,
                mode=0o100644,
            )
            missing = self.module.WorkerRequestError(
                "missing",
                code="stage_file_not_found",
                endpoint="/v1/stage/stat-batch",
                http_status=404,
            )

            def batch_result(_alias, relative_paths):
                self.assertEqual(relative_paths, ["a/model.pdb", "missing.pdb"])
                return (
                    types.SimpleNamespace(
                        relative_path="a/model.pdb", source=observed, error=None
                    ),
                    types.SimpleNamespace(
                        relative_path="missing.pdb", source=None, error=missing
                    ),
                )

            source = self.module.CsvDatasetSource(
                csv_path, "structure_path", id_column="case"
            )
            with mock.patch.object(
                self.module, "stat_staged_files", side_effect=batch_result
            ) as batch_call, mock.patch.object(
                self.module,
                "stat_staged_file",
                side_effect=AssertionError("batch candidates were restatted"),
            ):
                dataset = self.module._resolve_csv_dataset(
                    source.load(),
                    "x_k",
                    stage_root="/allowed",
                    stage_alias="structures",
                )

            batch_call.assert_called_once()
            self.assertEqual(
                [entry.status for entry in dataset.entries],
                ["available", "available", "available", "missing"],
            )
            self.assertEqual(
                [entry.metadata["case"] for entry in self.module._view_entries(dataset.entries)],
                ["first", "canonical-equivalent", "duplicate"],
            )
            self.assertIs(
                dataset.entries[0].path_resolution,
                dataset.entries[2].path_resolution,
            )
            self.assertIn("stage_file_not_found", dataset.entries[3].error)

    def test_csv_stage_single_stat_deduplicates_canonical_equivalent_rows(self):
        with tempfile.TemporaryDirectory() as root:
            csv_path = os.path.join(root, "canonical single.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    "/allowed/a/../model.pdb,noncanonical-spelling\n"
                    "/allowed/model.pdb,canonical-spelling\n"
                )
            observed = types.SimpleNamespace(
                token="v1-" + "1" * 64,
                device=1,
                inode=2,
                size=3,
                mtime_ns=4,
                mode=0o100644,
            )
            source = self.module.CsvDatasetSource(
                csv_path, "structure_path", id_column="case"
            )
            with mock.patch.object(
                self.module, "stat_staged_file", return_value=observed
            ) as single_call, mock.patch.object(
                self.module,
                "stat_staged_files",
                side_effect=AssertionError("one unique path should use single stat"),
            ):
                dataset = self.module._resolve_csv_dataset(
                    source.load(),
                    "x_k",
                    stage_root="/allowed",
                    stage_alias="structures",
                )

            single_call.assert_called_once_with("structures", "model.pdb")
            self.assertEqual(
                [entry.status for entry in dataset.entries],
                ["available", "available"],
            )
            self.assertEqual(
                [entry.path_resolution.raw_path for entry in dataset.entries],
                ["/allowed/a/../model.pdb", "/allowed/model.pdb"],
            )

    def test_csv_local_duplicate_rows_probe_once_per_resolve_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "same.pdb")
            self._write_pdb(structure, "SAME")
            csv_path = os.path.join(root, "local duplicates.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    f"{structure},first\n"
                    f"{structure},second\n"
                )
            source = self.module.CsvDatasetSource(
                csv_path, "structure_path", id_column="case"
            )
            original_isfile = os.path.isfile
            original_access = os.access
            isfile_paths = []
            access_paths = []

            def counted_isfile(path):
                if os.path.abspath(str(path)) == os.path.abspath(structure):
                    isfile_paths.append(str(path))
                return original_isfile(path)

            def counted_access(path, mode):
                if os.path.abspath(str(path)) == os.path.abspath(structure):
                    access_paths.append(str(path))
                return original_access(path, mode)

            with mock.patch.object(
                self.module.os.path, "isfile", side_effect=counted_isfile
            ), mock.patch.object(
                self.module.os, "access", side_effect=counted_access
            ):
                dataset = self.module._resolve_csv_dataset(
                    source.load(), "x_k"
                )

            self.assertEqual(
                [entry.status for entry in dataset.entries],
                ["available", "available"],
            )
            self.assertEqual(len(isfile_paths), 1)
            self.assertEqual(len(access_paths), 1)

    def test_duplicate_relative_paths_with_different_bases_are_not_reused(self):
        with tempfile.TemporaryDirectory() as root:
            first_base = os.path.join(root, "first")
            second_base = os.path.join(root, "second")
            os.mkdir(first_base)
            os.mkdir(second_base)
            first_path = os.path.join(first_base, "same.pdb")
            self._write_pdb(first_path, "FIRST")
            entries = tuple(
                self.module.AnnotationEntry(
                    id=f"heterogeneous-{index}",
                    ordinal=index,
                    display_name=f"row-{index}",
                    source_path="same.pdb",
                    source_kind="csv",
                    source_base=base,
                    metadata={"case": f"row-{index}"},
                )
                for index, base in enumerate((first_base, second_base))
            )
            dataset = self.module.AnnotationDataset(
                source_kind="csv",
                source_path=os.path.join(root, "heterogeneous.csv"),
                source_base=root,
                entries=entries,
                columns=("structure_path", "case"),
                path_column="structure_path",
            )

            resolved = self.module._resolve_csv_dataset(dataset, "x_k")

            self.assertEqual(
                [entry.status for entry in resolved.entries],
                ["available", "missing"],
            )
            self.assertEqual(resolved.entries[0].resolved_path, first_path)
            self.assertNotEqual(
                resolved.entries[0].resolved_path,
                resolved.entries[1].resolved_path,
            )

    def test_absolute_duplicate_ignores_heterogeneous_irrelevant_bases(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "absolute.pdb")
            self._write_pdb(structure, "ABSOLUTE")
            entries = tuple(
                self.module.AnnotationEntry(
                    id=f"absolute-{index}",
                    ordinal=index,
                    display_name=f"absolute-{index}",
                    source_path=structure,
                    source_kind="csv",
                    source_base=os.path.join(root, f"unused-{index}"),
                    metadata={"case": str(index)},
                )
                for index in range(2)
            )
            dataset = self.module.AnnotationDataset(
                source_kind="csv",
                source_path=os.path.join(root, "absolute.csv"),
                source_base=root,
                entries=entries,
                columns=("structure_path", "case"),
                path_column="structure_path",
            )
            original_isfile = os.path.isfile
            original_access = os.access
            probe_counts = {"isfile": 0, "access": 0}

            def counted_isfile(path):
                if os.path.abspath(str(path)) == structure:
                    probe_counts["isfile"] += 1
                return original_isfile(path)

            def counted_access(path, mode):
                if os.path.abspath(str(path)) == structure:
                    probe_counts["access"] += 1
                return original_access(path, mode)

            with mock.patch.object(
                self.module.os.path, "isfile", side_effect=counted_isfile
            ), mock.patch.object(
                self.module.os, "access", side_effect=counted_access
            ):
                resolved = self.module._resolve_csv_dataset(dataset, "x_k")

            self.assertEqual(
                [entry.status for entry in resolved.entries],
                ["available", "available"],
            )
            self.assertEqual(probe_counts, {"isfile": 1, "access": 1})

    def test_csv_stage_batch_global_failure_only_marks_stage_rows(self):
        with tempfile.TemporaryDirectory() as root:
            local_path = os.path.join(root, "local.pdb")
            self._write_pdb(local_path, "LOCAL")
            csv_path = os.path.join(root, "mixed sources.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    "/allowed/a.pdb,stage-a\n"
                    "/allowed/b.pdb,stage-b\n"
                    f"{local_path},local\n"
                )
            failure = self.module.WorkerRequestError(
                "worker unavailable",
                code="unreachable",
                endpoint="/v1/stage/stat-batch",
                retriable=True,
            )
            source = self.module.CsvDatasetSource(
                csv_path, "structure_path", id_column="case"
            )
            with mock.patch.object(
                self.module, "stat_staged_files", side_effect=failure
            ) as batch_call, mock.patch.object(
                self.module,
                "stat_staged_file",
                side_effect=AssertionError("global batch failure was retried per row"),
            ):
                dataset = self.module._resolve_csv_dataset(
                    source.load(),
                    "x_k",
                    stage_root="/allowed",
                    stage_alias="structures",
                )

            batch_call.assert_called_once()
            self.assertEqual(
                [entry.status for entry in dataset.entries],
                ["error", "error", "available"],
            )
            self.assertEqual(
                [entry.metadata["case"] for entry in self.module._view_entries(dataset.entries)],
                ["local"],
            )
            self.assertTrue(all("unreachable" in entry.error for entry in dataset.entries[:2]))
            self.assertEqual(dataset.entries[2].error, "")

    def test_csv_annotation_progress_rescan_uses_one_label_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for name in ("a.pdb", "b.pdb", "c.pdb"):
                path = os.path.join(root, name)
                self._write_pdb(path, name)
                paths.append(path)
            csv_path = os.path.join(root, "progress.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write("structure_path,case\n")
                for path in paths:
                    handle.write(f"{path},{os.path.basename(path)}\n")
            loaded = self.module.sa_load_csv(
                csv_path, "structure_path", "case"
            )
            self.assertEqual(loaded["status"], "loaded")
            store = self.module.STATE.row_annotation_store
            store.set(self.module.STATE.entries[0].id, "quality", "Keep")
            store.set(self.module.STATE.entries[2].id, "quality", "Reject")

            with mock.patch.object(
                store,
                "snapshot_for_label",
                wraps=store.snapshot_for_label,
            ) as snapshot_call, mock.patch.object(
                store,
                "annotations_for",
                side_effect=AssertionError("rescan replayed the log per row"),
            ):
                self.module._rescan_annotated_indices()

            snapshot_call.assert_called_once_with("quality")
            self.assertEqual(self.module.STATE.annotated_indices, {0, 2})
            self.assertEqual(
                self.module.STATE.annotated_entry_ids,
                {self.module.STATE.entries[0].id, self.module.STATE.entries[2].id},
            )

    def test_csv_load_reuses_batch_identity_for_selected_preflight(self):
        with tempfile.TemporaryDirectory() as root:
            cached_path = os.path.join(root, "cached.pdb")
            self._write_pdb(cached_path, "STAGED")
            csv_path = os.path.join(root, "batch preflight.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    "/allowed/a.pdb,a\n"
                    "/allowed/b.pdb,b\n"
                )
            size = os.path.getsize(cached_path)
            first = types.SimpleNamespace(
                token="v1-" + "e" * 64,
                device=1,
                inode=2,
                size=size,
                mtime_ns=3,
                mode=0o100644,
            )
            second = types.SimpleNamespace(
                token="v1-" + "f" * 64,
                device=1,
                inode=3,
                size=size,
                mtime_ns=3,
                mode=0o100644,
            )
            batch_results = (
                types.SimpleNamespace(relative_path="a.pdb", source=first, error=None),
                types.SimpleNamespace(relative_path="b.pdb", source=second, error=None),
            )
            identity = self.module.SourceIdentity(
                provider="wsl-stage:structures",
                canonical_source="structures/a.pdb",
                size=size,
                mtime_ns=3,
                version_token=first.token,
            )
            materialized = self.module.MaterializedStructure(
                source_path="/allowed/a.pdb",
                path=cached_path,
                identity=identity,
                mode="cache",
                cache_key=identity.cache_key,
                cache_hit=True,
            )

            with mock.patch.object(
                self.module, "stat_staged_files", return_value=batch_results
            ) as batch_call, mock.patch.object(
                self.module,
                "stat_staged_file",
                side_effect=AssertionError("selected row was restatted"),
            ) as single_call, mock.patch.object(
                self.module, "_materialize_entry", return_value=materialized
            ) as materialize_call:
                loaded = self.module.sa_load_csv(
                    csv_path,
                    "structure_path",
                    "case",
                    stage_root="/allowed",
                    stage_alias="structures",
                )

            self.assertEqual(loaded["status"], "loaded")
            batch_call.assert_called_once_with("structures", ["a.pdb", "b.pdb"])
            single_call.assert_not_called()
            self.assertIs(
                materialize_call.call_args.kwargs["staged_source_identity"], first
            )

    def test_wsl_materializer_uses_supplied_stat_identity_without_restating(self):
        with tempfile.TemporaryDirectory() as root:
            downloaded_path = os.path.join(root, "downloaded.pdb")
            self._write_pdb(downloaded_path, "STAGED")
            size = os.path.getsize(downloaded_path)
            observed = types.SimpleNamespace(
                token="v1-" + "b" * 64,
                device=1,
                inode=2,
                size=size,
                mtime_ns=3,
                mode=0o100644,
            )
            resolution = self.module._StagedPathResolution(
                raw_path="/allowed/nested/model.pdb",
                resolved_path="/allowed/nested/model.pdb",
                status="available",
                provider="wsl-stage:structures",
                method="wsl-stage-stat",
                matched_rule="/allowed",
                staged_source_identity=observed,
            )
            entry = self.module.AnnotationEntry(
                id="stage-materialize",
                ordinal=0,
                display_name="model.pdb",
                source_path="/allowed/nested/model.pdb",
                source_kind="csv",
            ).with_resolution(resolution)
            context = self.module._MaterializationContext(
                generation=self.module._materialization_generation,
                cache_enabled=True,
                stage_root="/allowed",
                stage_alias="structures",
            )
            download_result = types.SimpleNamespace(
                destination=downloaded_path,
                source=observed,
            )

            with mock.patch.object(
                self.module,
                "stat_staged_file",
                side_effect=AssertionError("fresh resolution stat was not reused"),
            ) as stat_call, mock.patch.object(
                self.module,
                "download_staged_file",
                return_value=download_result,
            ) as download_call:
                materialized = self.module._materialize_entry(
                    entry,
                    context,
                    staged_source_identity=observed,
                )

            stat_call.assert_not_called()
            self.assertFalse(materialized.cache_hit)
            self.assertEqual(materialized.identity.version_token, observed.token)
            self.assertEqual(
                download_call.call_args.args[:2],
                ("structures", "nested/model.pdb"),
            )

    def test_wsl_csv_cold_duplicate_warm_and_same_entry_memo_request_counts(self):
        with tempfile.TemporaryDirectory() as root:
            downloaded_path = os.path.join(root, "downloaded.pdb")
            self._write_pdb(downloaded_path, "STAGED")
            csv_path = os.path.join(root, "staged duplicates.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case\n"
                    "/allowed/nested/model.pdb,first\n"
                    "/allowed/nested/model.pdb,second\n"
                )
            size = os.path.getsize(downloaded_path)
            observed = types.SimpleNamespace(
                token="v1-" + "c" * 64,
                device=1,
                inode=2,
                size=size,
                mtime_ns=3,
                mode=0o100644,
            )
            download_result = types.SimpleNamespace(
                destination=downloaded_path,
                source=observed,
            )

            with mock.patch.object(
                self.module, "stat_staged_file", return_value=observed
            ) as stat_call, mock.patch.object(
                self.module,
                "download_staged_file",
                return_value=download_result,
            ) as download_call:
                loaded = self.module.sa_load_csv(
                    csv_path,
                    "structure_path",
                    "case",
                    stage_root="/allowed",
                    stage_alias="structures",
                )
                self.assertEqual(loaded["status"], "loaded")
                self.assertEqual(stat_call.call_count, 1)
                self.assertEqual(download_call.call_count, 1)
                first = self.module.STATE.current_entry()
                self.assertFalse(
                    self.module.STATE.materialized_paths[first.id].cache_hit
                )

                self.module.sa_goto(2)
                second = self.module.STATE.current_entry()
                self.assertEqual(stat_call.call_count, 2)
                self.assertEqual(download_call.call_count, 1)
                self.assertTrue(
                    self.module.STATE.materialized_paths[second.id].cache_hit
                )

                self.module.sa_goto(1)
                self.assertEqual(stat_call.call_count, 2)
                self.assertEqual(download_call.call_count, 1)
                self.assertFalse(
                    self.module.STATE.materialized_paths[first.id].cache_hit
                )

    def test_new_csv_does_not_inherit_source_config_and_empty_clears_saved_id(self):
        with tempfile.TemporaryDirectory() as root:
            first_structure = os.path.join(root, "first.pdb")
            second_structure = os.path.join(root, "second.pdb")
            self._write_pdb(first_structure, "FIRST")
            self._write_pdb(second_structure, "SECOND")
            first_csv = os.path.join(root, "first.csv")
            second_csv = os.path.join(root, "second.csv")
            with open(first_csv, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "path,id,score_one\n"
                    f"{first_structure},first-id,0.9\n"
                )
            with open(second_csv, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "path,score_two\n"
                    f"{second_structure},0.4\n"
                )

            first_loaded = self.module.sa_load_csv(
                first_csv, "path", "id", "score_one"
            )
            self.assertEqual(first_loaded["status"], "loaded")

            second_loaded = self.module.sa_load_csv(second_csv, "path")

            self.assertEqual(second_loaded["status"], "loaded")
            self.assertEqual(self.module.STATE.id_column, "")
            self.assertEqual(self.module.STATE.display_columns, ["score_two"])
            self.assertEqual(self.module.STATE.csv_delimiter, ",")

            self.module.sa_load_csv(first_csv, "path", "", "[]")
            self.assertEqual(self.module.STATE.id_column, "")
            self.assertEqual(self.module.STATE.display_columns, [])

    def test_csv_export_uses_collision_safe_annotation_column_and_reimports(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "model.pdb")
            self._write_pdb(structure, "MODEL")
            csv_path = os.path.join(root, "quality scores.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,quality\n"
                    f"{structure},0.83\n"
                )
            with open(csv_path, "rb") as handle:
                source_before = handle.read()

            self.module.sa_load_csv(csv_path, "structure_path")
            self.module.sa_annotate("Keep")
            exported = self.module.sa_export_csv(
                os.path.join(root, "quality reviewed.csv")
            )

            with open(exported, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                self.assertEqual(
                    reader.fieldnames,
                    [
                        "structure_path",
                        "quality",
                        "pymol_annotation_quality",
                    ],
                )
            self.assertEqual(rows[0]["quality"], "0.83")
            self.assertEqual(rows[0]["pymol_annotation_quality"], "Keep")
            with open(csv_path, "rb") as handle:
                self.assertEqual(handle.read(), source_before)

            reloaded = self.module.sa_load_csv(exported, "structure_path")
            self.assertEqual(reloaded["status"], "loaded")
            self.assertIn(
                "pymol_annotation_quality", self.module.STATE.display_columns
            )

    def test_csv_export_log_counts_existing_annotation_column_not_last(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "model.pdb")
            self._write_pdb(structure, "MODEL")
            csv_path = os.path.join(root, "existing annotation.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,pymol_annotation_quality,trailing\n"
                    f"{structure},Old,\n"
                )

            self.module.sa_load_csv(csv_path, "structure_path")
            self.module.sa_annotate("Keep")
            with mock.patch("builtins.print") as printer:
                exported = self.module.sa_export_csv(
                    os.path.join(root, "existing annotation reviewed.csv")
                )

            messages = [
                " ".join(str(value) for value in call.args)
                for call in printer.call_args_list
            ]
            self.assertTrue(any("(1 annotated)" in value for value in messages))
            with open(exported, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertEqual(
                reader.fieldnames,
                ["structure_path", "pymol_annotation_quality", "trailing"],
            )
            self.assertEqual(rows[0]["pymol_annotation_quality"], "Keep")
            self.assertEqual(rows[0]["trailing"], "")

    def test_failed_csv_load_fully_restores_directory_session(self):
        with tempfile.TemporaryDirectory() as root:
            dataset = os.path.join(root, "directory dataset")
            os.mkdir(dataset)
            for name in ("a.pdb", "b.pdb"):
                self._write_pdb(os.path.join(dataset, name), name)
            self.module.sa_load_dir(dataset)
            self.module.sa_goto(2)
            self.module.STATE.label_name = "directory_review"
            self.module.STATE.undo_history.append({"marker": "directory-undo"})

            bad_csv = os.path.join(root, "bad directory replacement.csv")
            with open(bad_csv, "w", encoding="utf-8", newline="") as handle:
                handle.write("wrong_column,score\nmissing.pdb,0.1\n")
            old_state_path = self.module.dataset_state_path(dataset)
            with open(old_state_path, "rb") as handle:
                old_state_before = handle.read()
            session_before = self._session_signature()
            scene_before = self._pymol_scene_signature()

            result = self.module.sa_load_csv(bad_csv, "structure_path")

            self.assertIsNone(result)
            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)
            self.assertEqual(self.module.STATE.source_mode, "directory")
            self.assertEqual(os.path.basename(self.module.STATE.current_file()), "b.pdb")
            with open(old_state_path, "rb") as handle:
                self.assertEqual(handle.read(), old_state_before)
            self.assertFalse(
                os.path.exists(
                    self.module.dataset_state_path(bad_csv, source_mode="csv")
                )
            )

    def test_failed_csv_load_fully_restores_previous_csv_session(self):
        with tempfile.TemporaryDirectory() as root:
            structures = []
            for name in ("first.pdb", "second.pdb"):
                path = os.path.join(root, name)
                self._write_pdb(path, name)
                structures.append(path)
            csv_path = os.path.join(root, "active scores.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case,score\n"
                    f"{structures[0]},first,0.9\n"
                    f"{structures[1]},second,0.4\n"
                )
            loaded = self.module.sa_load_csv(
                csv_path, "structure_path", "case", "score"
            )
            self.assertEqual(loaded["status"], "loaded")
            self.module.sa_goto(2)
            self.module.sa_annotate("Keep")
            active_entry_id = self.module.STATE.current_entry_id()
            store = self.module.STATE.row_annotation_store

            bad_csv = os.path.join(root, "bad csv replacement.csv")
            with open(bad_csv, "w", encoding="utf-8", newline="") as handle:
                handle.write("not_a_path,score\nmissing.pdb,0.0\n")
            state_path = self.module.dataset_state_path(csv_path, source_mode="csv")
            annotation_path = store.path
            with open(state_path, "rb") as handle:
                state_before = handle.read()
            with open(annotation_path, "rb") as handle:
                annotations_before = handle.read()
            session_before = self._session_signature()
            scene_before = self._pymol_scene_signature()

            result = self.module.sa_load_csv(bad_csv, "structure_path")

            self.assertIsNone(result)
            self.assertEqual(self._session_signature(), session_before)
            self.assertEqual(self._pymol_scene_signature(), scene_before)
            self.assertIs(self.module.STATE.row_annotation_store, store)
            self.assertEqual(self.module.STATE.current_entry_id(), active_entry_id)
            self.assertEqual(store.get(active_entry_id, "quality"), "Keep")
            with open(state_path, "rb") as handle:
                self.assertEqual(handle.read(), state_before)
            with open(annotation_path, "rb") as handle:
                self.assertEqual(handle.read(), annotations_before)
            self.assertFalse(
                os.path.exists(
                    self.module.dataset_state_path(bad_csv, source_mode="csv")
                )
            )

    def test_prepared_commit_failure_restores_fresh_completion_time_cursor(self):
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, "active")
            os.mkdir(directory)
            for name in ("a.pdb", "b.pdb"):
                self._write_pdb(os.path.join(directory, name), name)
            self.module.sa_load_dir(directory)

            replacement = os.path.join(root, "replacement.pdb")
            self._write_pdb(replacement, "REPLACEMENT")
            csv_path = os.path.join(root, "replacement.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(f"structure_path,case\n{replacement},new\n")
            plan = self.module._prepare_csv_load(
                csv_path,
                "structure_path",
                "case",
                "",
                "utf-8-sig",
                "auto",
                "x_k",
                "",
                "",
                "",
                self.module._capture_csv_runtime_context(),
            )

            self.module.sa_goto(2)
            session_at_completion = self._session_signature()
            scene_at_completion = self._pymol_scene_signature()
            self.fake.fail_next_load = True

            with self.assertRaisesRegex(RuntimeError, "synthetic annotator"):
                self.module._commit_prepared_csv_load(plan)

            self.assertEqual(self._session_signature(), session_at_completion)
            self.assertEqual(self._pymol_scene_signature(), scene_at_completion)
            self.assertEqual(os.path.basename(self.module.STATE.current_file()), "b.pdb")

    def test_csv_filter_uses_entry_id_cursor_and_restores_persisted_view(self):
        with tempfile.TemporaryDirectory() as root:
            rows = []
            for name, score, cohort in (
                ("design_a", "0.9", "keep"),
                ("design_b", "0.4", "keep"),
                ("design_c", "0.7", "drop"),
            ):
                path = os.path.join(root, name + ".pdb")
                self._write_pdb(path, name)
                rows.append((path, name, score, cohort))
            csv_path = os.path.join(root, "filter scores.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write("structure_path,case,score,cohort\n")
                for path, name, score, cohort in rows:
                    handle.write(f"{path},{name},{score},{cohort}\n")

            loaded = self.module.sa_load_csv(
                csv_path,
                "structure_path",
                "case",
                "score,cohort",
            )
            self.assertEqual(loaded["status"], "loaded")
            self.module.sa_goto(2)
            selected_id = self.module.STATE.current_entry_id()
            self.assertEqual(self.module.STATE.current_entry().metadata["case"], "design_b")

            filtered = self.module.sa_filter("keep", "score", 0)

            self.assertEqual(filtered["status"], "ok")
            self.assertEqual(filtered["active_count"], 2)
            self.assertEqual(self.module.STATE.current_entry_id(), selected_id)
            self.assertEqual(self.module.STATE.index, 0)
            self.assertEqual(
                [entry.metadata["case"] for entry in self.module.STATE.entries],
                ["design_b", "design_a"],
            )
            state_path = self.module.dataset_state_path(csv_path, source_mode="csv")
            with open(state_path, "r", encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["position"]["entry_id"], selected_id)
            self.assertEqual(persisted["view"]["filters"], {"text": "keep"})
            self.assertEqual(
                persisted["view"]["sort"],
                {"column": "score", "descending": False},
            )

            self.module.STATE.filter_text = "mutated-only-in-memory"
            self.module.STATE.sort_column = ""
            self.module.STATE.sort_descending = True
            self.module.STATE.navigation_session.goto(1)
            self.module.STATE.index = 1
            self.assertNotEqual(self.module.STATE.current_entry_id(), selected_id)

            reloaded = self.module.sa_load_csv(csv_path, "structure_path", "case")

            self.assertEqual(reloaded["status"], "loaded")
            self.assertEqual(self.module.STATE.filter_text, "keep")
            self.assertEqual(self.module.STATE.sort_column, "score")
            self.assertFalse(self.module.STATE.sort_descending)
            self.assertEqual(self.module.STATE.current_entry_id(), selected_id)
            self.assertEqual(
                [entry.metadata["case"] for entry in self.module.STATE.entries],
                ["design_b", "design_a"],
            )

    def test_csv_cursor_survives_row_reorder_and_falls_back_if_id_disappears(self):
        with tempfile.TemporaryDirectory() as root:
            structures = {}
            for name in ("a", "b", "c"):
                path = os.path.join(root, name + ".pdb")
                self._write_pdb(path, name)
                structures[name] = path
            csv_path = os.path.join(root, "reordered.csv")

            def write_rows(names):
                with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                    handle.write("path,id,score\n")
                    for offset, name in enumerate(names):
                        handle.write(
                            f"{structures[name]},{name},{0.9 - offset * 0.1}\n"
                        )

            write_rows(("a", "b", "c"))
            self.module.sa_load_csv(csv_path, "path", "id")
            self.module.sa_goto(2)
            selected_id = self.module.STATE.current_entry_id()
            self.assertEqual(self.module.STATE.current_entry().metadata["id"], "b")
            self.module.sa_save_state()

            write_rows(("c", "b", "a"))
            self.module.sa_load_csv(csv_path, "path", "id")

            self.assertEqual(self.module.STATE.current_entry_id(), selected_id)
            self.assertEqual(self.module.STATE.current_entry().metadata["id"], "b")

            write_rows(("c", "a"))
            reloaded = self.module.sa_load_csv(csv_path, "path", "id")

            self.assertEqual(reloaded["status"], "loaded")
            self.assertNotEqual(self.module.STATE.current_entry_id(), selected_id)
            self.assertIn(self.module.STATE.current_entry().metadata["id"], {"a", "c"})

    def test_invalid_and_missing_csv_rows_remain_in_table_but_not_navigation(self):
        with tempfile.TemporaryDirectory() as root:
            valid = os.path.join(root, "valid.pdb")
            self._write_pdb(valid, "VALID")
            missing = os.path.join(root, "does-not-exist.pdb")
            csv_path = os.path.join(root, "mixed validity.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case,score\n"
                    f"{valid},valid_row,0.9\n"
                    ",empty_path_row,0.5\n"
                    f"{missing},missing_row,0.1\n"
                )

            loaded = self.module.sa_load_csv(
                csv_path, "structure_path", "case", "score"
            )

            self.assertEqual(loaded["status"], "loaded")
            self.assertEqual(loaded["row_count"], 3)
            self.assertEqual(loaded["active_count"], 1)
            self.assertEqual(
                loaded["status_counts"],
                {"available": 1, "error": 1, "missing": 1},
            )
            self.assertEqual(len(loaded["rows"]), 3)
            self.assertEqual(
                [row["metadata"]["case"] for row in loaded["rows"]],
                ["valid_row", "empty_path_row", "missing_row"],
            )
            self.assertEqual(self.module.STATE.dataset.count, 3)
            self.assertEqual(len(self.module.STATE.all_entries), 3)
            self.assertEqual(len(self.module.STATE.dataset.error_entries), 2)
            by_case = {
                entry.metadata["case"]: entry
                for entry in self.module.STATE.all_entries
            }
            self.assertEqual(by_case["valid_row"].availability, "available")
            self.assertEqual(by_case["empty_path_row"].availability, "invalid")
            self.assertEqual(by_case["missing_row"].availability, "missing")
            self.assertEqual(
                [entry.metadata["case"] for entry in self.module.STATE.entries],
                ["valid_row"],
            )
            self.assertEqual(self.module.STATE.files, [valid])
            self.assertEqual(self.module.STATE.navigation_session.count, 1)
            self.assertEqual(
                [
                    entry.id
                    for entry in self.module.STATE.navigation_session.entries
                ],
                [by_case["valid_row"].id],
            )

    def test_csv_rows_keep_metadata_duplicate_paths_and_annotations_independent(self):
        with tempfile.TemporaryDirectory() as root:
            structure = os.path.join(root, "shared.pdb")
            self._write_pdb(structure, "SHARED")
            csv_path = os.path.join(root, "scores.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "structure_path,case,iptm\n"
                    f"{structure},design_a,0.91\n"
                    f"{structure},design_b,0.37\n"
                )
            mapping_path = os.path.join(root, "mapping.json")
            previous_mapping = os.environ.get("PYMOLPLUGINS_ANNOTATOR_MAPPINGS")
            os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = mapping_path
            try:
                loaded = self.module.sa_load_csv(
                    csv_path,
                    "structure_path",
                    "case",
                )
                self.assertEqual(loaded["status"], "loaded")
                self.assertEqual(self.module.STATE.source_mode, "csv")
                self.assertEqual(len(self.module.STATE.entries), 2)
                self.assertEqual(len(set(entry.id for entry in self.module.STATE.entries)), 2)
                self.assertEqual(
                    [entry.metadata["iptm"] for entry in self.module.STATE.entries],
                    ["0.91", "0.37"],
                )
                self.assertEqual(self.module.STATE.files, [structure, structure])

                first_id = self.module.STATE.entries[0].id
                second_id = self.module.STATE.entries[1].id
                self.module.sa_annotate("Keep")
                self.module.sa_goto(2)
                self.module.sa_annotate("Reject")
                store = self.module.STATE.row_annotation_store
                self.assertEqual(store.get(first_id, "quality"), "Keep")
                self.assertEqual(store.get(second_id, "quality"), "Reject")
                with open(structure, encoding="ascii") as handle:
                    self.assertNotIn("PYMOL_ANNOTATOR", handle.read())

                exported = self.module.sa_export_csv(os.path.join(root, "reviewed.csv"))
                with open(exported, "r", encoding="utf-8-sig") as handle:
                    exported_text = handle.read()
                self.assertIn("design_a,0.91,Keep", exported_text)
                self.assertIn("design_b,0.37,Reject", exported_text)

                self.module.STATE.label_name = "wrong"
                self.module.sa_load_csv(csv_path, "structure_path", "case")
                self.assertEqual(self.module.STATE.index, 1)
                self.assertEqual(self.module.STATE.label_name, "quality")
                self.assertEqual(
                    self.module.STATE.row_annotation_store.get(second_id, "quality"),
                    "Reject",
                )
            finally:
                if previous_mapping is None:
                    os.environ.pop("PYMOLPLUGINS_ANNOTATOR_MAPPINGS", None)
                else:
                    os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = previous_mapping


if __name__ == "__main__":
    unittest.main()
