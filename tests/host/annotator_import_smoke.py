"""Real Windows Qt/PyMOL gate for explicit imports, history and long paths."""

import argparse
import copy
import gzip
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_helpers import isolate_workspace_settings
from annotator_workflow_smoke import fixture, wait_for


def choose_history(panel, app, query, load, qt_test, qt, output):
    from pymol.Qt import QtCore
    from pymolplugins.annotation.history_browser import HistoryRestoreDialog
    errors = []
    if panel.main_tabs.currentIndex() != 1:
        qt_test.mouseClick(panel.import_button, qt.LeftButton)
    assert panel.history_button.isVisible()

    def choose():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, HistoryRestoreDialog)
            dialog.search_edit.setText(query)
            assert dialog.entries_table.rowCount() == 1
            assert dialog.snapshot is None
            dialog.grab().save(str(output.with_name(output.stem + "-history.png")))
            qt_test.mouseClick(dialog.load_button if load else dialog.restore_button, qt.LeftButton)
        except Exception as exc:
            errors.append(exc)
            if dialog is not None:
                dialog.reject()

    QtCore.QTimer.singleShot(0, choose)
    qt_test.mouseClick(panel.history_button, qt.LeftButton)
    if errors:
        raise errors[0]


def make_long_directory(directory, windows_long_path):
    target = directory
    for index in range(3):
        target = target / (f"long path {index} " + "x" * 80)
    os.makedirs(windows_long_path(str(target)))
    content = (directory / "model 0.pdb").read_bytes()
    for name in ("candidate_" + "a" * 65 + ".pdb", "candidate_" + "b" * 65 + ".pdb.gz", "candidate_unicode_候选.pdb"):
        path = windows_long_path(str(target / name))
        with (gzip.open(path, "wb") if name.endswith(".gz") else open(path, "wb")) as handle:
            handle.write(content)
    return str(target)


def run(output, real_source):
    assert os.name == "nt", "Use Windows PyMOL's bundled Python"
    assert not os.getcwd().startswith("\\\\"), "Use a Windows-local working directory"
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    from pymolplugins.core.windows_paths import windows_long_path
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-import-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        isolate_workspace_settings(directory, window)
        csv_path = fixture(directory, 12, 8)
        from pymolplugins.annotation import plugin as annotation
        from pymolplugins.annotation.history_store import record_config_history
        from pymolplugins.annotation.state_store import write_dataset_state
        from pymolplugins.annotation.persistence import flush_writes
        from pymolplugins.annotation.sources.directory import DirectoryDatasetSource
        from pymolplugins.annotation.materializer import DirectMaterializer, CopyMaterializer
        from pymolplugins.annotation.cache import PersistentStructureCache
        annotation.STATE = annotation.AnnotatorState()
        chosen = annotation._persisted_config()
        chosen.update(source_mode="csv", csv_path=str(csv_path), path_column="structure_file", id_column="row_id",
                      display_columns=["AC.iptm"], display_columns_selected=True, step_cmd="color cyan, {object}",
                      restore_last_dataset=True, filter_text="where:AC.iptm >= 0.05")
        old = copy.deepcopy(chosen)
        old["step_cmd"] = "color magenta, {object}"
        for config in (chosen, old):
            record_config_history(str(directory), config, source_mode="csv", csv_path=str(csv_path),
                                  path_column="structure_file", id_column="row_id", display_columns=["AC.iptm"])
        write_dataset_state(str(csv_path), {
            "config": old, "position": {"index": 8, "file": "", "entry_id": ""},
            "source": {"mode": "csv", "csv_path": str(csv_path)},
            "csv": {"path_column": "structure_file", "id_column": "row_id", "display_columns": ["AC.iptm"]},
            "view": {"display_columns": ["AC.iptm"], "filters": {"text": ""}, "sort": {"column": "", "descending": False}},
        }, source_mode="csv", csv_path=str(csv_path))
        history_before = (directory / "history.json").read_bytes()
        cmd.pseudoatom("existing_scene")
        objects_before = cmd.get_names("objects")
        panel = annotation.show_structure_annotator_panel()
        qt_test, qt = QtTest.QTest, QtCore.Qt
        result = {}
        long_dir = None
        try:
            qt_test.qWait(200)
            assert not panel._csv_task_id and annotation.STATE.dataset is None
            assert annotation.STATE.step_cmd == "zoom {object}"
            assert panel.csv_edit.text() == "" and panel.main_tabs.currentIndex() == 1
            assert not panel.import_button.isVisible() and panel.history_button.isVisible()
            assert panel.directory_mode_button.isVisible() and panel.dir_browse.isVisible()
            assert (directory / "history.json").read_bytes() == history_before
            with patch.object(cmd, "load", side_effect=AssertionError("Configuration-only restore loaded a structure")):
                choose_history(panel, app, "cyan", False, qt_test, qt, output)
                qt_test.qWait(50)
            assert annotation.STATE.dataset is None and annotation.STATE.csv_path == ""
            assert cmd.get_names("objects") == objects_before
            assert panel.csv_edit.text() == str(csv_path)
            assert annotation.STATE.step_cmd == chosen["step_cmd"]
            choose_history(panel, app, "cyan", True, qt_test, qt, output)
            wait_for(app, lambda: not panel._csv_task_id and len(annotation.STATE.entries) == 7)
            assert annotation.STATE.step_cmd == chosen["step_cmd"], "A newer sidecar replaced the chosen history configuration"
            assert annotation.STATE.filter_text == chosen["filter_text"]
            assert annotation.STATE.display_columns == ["AC.iptm"]
            result.update(startup_without_restore="passed", configuration_only_without_scene_changes="passed",
                          explicit_history_load="passed", selected_snapshot_precedence="passed")
            panel.grab().save(str(output.with_suffix(".png")))

            long_dir = make_long_directory(directory, windows_long_path)
            dataset = DirectoryDatasetSource(long_dir).load()
            assert dataset.count == 3
            path = next(entry.source_path for entry in dataset.entries if entry.source_path.endswith("a.pdb"))
            assert len(path) > 400
            direct = DirectMaterializer().materialize(path)
            cmd.load(direct.path, "direct_long_path", quiet=1)
            assert cmd.count_atoms("direct_long_path") == 3
            cmd.delete("direct_long_path")
            copied = CopyMaterializer(PersistentStructureCache(str(directory / "copy-cache"))).materialize(path)
            assert os.path.isfile(copied.path)
            qt_test.mouseClick(panel.import_button, qt.LeftButton)
            assert panel.main_tabs.currentIndex() == 1
            panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData("directory"))
            panel.dir_edit.setText(long_dir)
            qt_test.mouseClick(panel.load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            assert annotation.STATE.source_mode == "directory" and len(annotation.STATE.entries) == 3
            assert cmd.count_atoms(annotation.STATE.current_object) == 3
            identity_before_error = (annotation.STATE.dataset_dir, annotation.STATE.current_object)
            panel.dir_edit.setText(str(directory / "missing-directory"))
            panel._load_dataset()
            wait_for(app, lambda: not panel._csv_task_id)
            assert panel.error_label.isVisible() and "Directory import failed" in panel.error_label.text()
            assert (annotation.STATE.dataset_dir, annotation.STATE.current_object) == identity_before_error
            panel.dir_edit.setText(long_dir)
            panel.error_label.hide()
            panel.grab().save(str(output.with_name(output.stem + "-long-path.png")))
            result.update(long_directory_length=len(long_dir), long_file_length=len(path),
                          long_path_directory_import="passed", direct_and_cached_long_path_loading="passed")

            if real_source:
                discovered = DirectoryDatasetSource(real_source).load()
                assert discovered.count > 0
                before = [(entry.source_path, os.stat(windows_long_path(entry.source_path))) for entry in discovered.entries]
                hidden = os.path.join(real_source, ".pymolplugins")
                had_hidden = os.path.exists(windows_long_path(hidden))
                # Keep every write in this test's temporary profile, including timers.
                with patch.object(annotation, "_write_persisted_dataset_state", return_value=None):
                    panel.dir_edit.setText(real_source)
                    qt_test.mouseClick(panel.load_btn, qt.LeftButton)
                    wait_for(app, lambda: not panel._csv_task_id, timeout=120)
                    assert annotation.STATE.dataset_dir == real_source
                    assert len(annotation.STATE.entries) == discovered.count
                    assert cmd.count_atoms(annotation.STATE.current_object) > 0
                    result.update(real_source_count=discovered.count,
                                  real_source_longest_path=max(len(entry.source_path) for entry in discovered.entries),
                                  real_source_atoms=cmd.count_atoms(annotation.STATE.current_object))
                    panel.dir_edit.setText(long_dir)
                    panel._load_dataset()
                    wait_for(app, lambda: not panel._csv_task_id)
                assert os.path.exists(windows_long_path(hidden)) == had_hidden
                for source, stat in before:
                    after = os.stat(windows_long_path(source))
                    assert (after.st_size, after.st_mtime_ns) == (stat.st_size, stat.st_mtime_ns)
                result["real_source_preserved"] = "passed"

            # Restoring settings against an already loaded dataset keeps its identity.
            identity = (annotation.STATE.source_mode, annotation.STATE.dataset_dir, annotation.STATE.current_object)
            snapshot = {"source_mode": "csv", "csv_path": str(csv_path), "dataset_dir": str(directory), "config": chosen}
            with patch.object(cmd, "load", side_effect=AssertionError("Settings restore changed the scene")):
                panel._restore_history_snapshot(snapshot)
            assert (annotation.STATE.source_mode, annotation.STATE.dataset_dir, annotation.STATE.current_object) == identity
            panel.close()
            with patch.object(cmd, "load", side_effect=AssertionError("Reopening loaded structures")):
                panel = annotation.show_structure_annotator_panel()
                qt_test.qWait(100)
            assert (annotation.STATE.source_mode, annotation.STATE.dataset_dir, annotation.STATE.current_object) == identity
            result["live_session_preserved_on_reopen"] = "passed"
            result.update(status="passed", pymol=cmd.get_version()[0])
            return result
        finally:
            panel.close()
            futures = list(annotation._materialization_futures.values())
            annotation._reset_bg_cache()
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=30)
            annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._delete_managed_objects(False)
            if long_dir is not None:
                shutil.rmtree(windows_long_path(long_dir))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output, args.source_directory)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
