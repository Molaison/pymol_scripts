"""Windows PyMOL acceptance for review files, TSV and responsive source imports."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
_APPLICATION = None
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_workflow_smoke import fixture, wait_for
from workspace_helpers import isolate_workspace_settings


def table_fixture(directory, name, delimiter):
    directory.mkdir()
    fixture(directory, 3, 8)
    table = directory / name
    columns = ["structure_path", "absolute_path", "label", "score2", "score1"]
    with table.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(columns)
        for index in range(3):
            structure = directory / f"model {index}.pdb"
            writer.writerow([structure.name, str(structure), "representative mode" if index == 0 else "", index, 1 - index / 10])
    review = {"path_column": "structure_path", "display_columns": ["score1", "score2"],
              "label_column": "label", "step_cmd": "hide everything, {object}\nshow cartoon, {object}\ncolor cyan, {object}\nzoom {object}"}
    (directory / "review.json").write_text(json.dumps(review), encoding="utf-8")
    return table, review


def open_table(panel, path, app, qt_test, qt):
    panel._show_import()
    panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData("csv"))
    panel.csv_edit.setText(str(path))
    qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
    wait_for(app, lambda: not panel._csv_task_id and not panel._pending_csv_action)
    assert not panel.error_label.isVisible(), panel.error_label.text()


def check_slow_folder(panel, annotation, directory, app, qt_test, qt):
    """Inject blocked I/O, but run the real Qt worker and PyMOL commit paths."""
    from pymol.Qt import QtCore
    folder = directory / "many structures"
    folder.mkdir()
    fixture(folder, 3, 8)
    content = (folder / "model 0.pdb").read_bytes()
    for index in range(200):
        (folder / f"candidate_{index:04d}.pdb").write_bytes(content)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    real_scan = annotation._list_structure_files
    real_annotations = annotation.read_structure_annotations
    calls = []

    def blocked_scan(*args, **kwargs):
        calls.append(("scan", threading.get_ident()))
        started.set()
        assert release.wait(10), "Test did not release the folder scan"
        try:
            return real_scan(*args, **kwargs)
        finally:
            finished.set()

    def tracked_annotations(*args, **kwargs):
        calls.append(("annotations", threading.get_ident()))
        return real_annotations(*args, **kwargs)

    panel._show_import()
    panel.source_mode_combo.setCurrentIndex(0)
    panel.dir_edit.setText(str(folder))
    old_source, old_object = annotation.STATE.dataset.source_path, annotation.STATE.current_object
    beats = []
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start(10)
    try:
        with patch.object(annotation, "_list_structure_files", blocked_scan):
            begin = time.perf_counter()
            qt_test.mouseClick(panel.load_btn, qt.LeftButton)
            click_ms = (time.perf_counter() - begin) * 1000
            wait_for(app, started.is_set)
            wait_for(app, lambda: len(beats) >= 8)
            assert panel.cancel_task_button.isVisible()
            assert annotation.STATE.dataset.source_path == old_source
            begin = time.perf_counter()
            qt_test.mouseClick(panel.cancel_task_button, qt.LeftButton)
            cancel_ms = (time.perf_counter() - begin) * 1000
            assert not panel._csv_task_id
            release.set()
            wait_for(app, finished.is_set)
            qt_test.qWait(60)
            assert annotation.STATE.current_object == old_object
        with patch.object(annotation, "read_structure_annotations", tracked_annotations):
            qt_test.mouseClick(panel.load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
        assert annotation.STATE.dataset.source_path == str(folder), panel.error_label.text()
        assert len(annotation.STATE.entries) == 203
        assert all(thread != main_thread for _, thread in calls), calls
        assert click_ms < 250 and cancel_ms < 250, (click_ms, cancel_ms)
        return {"rows": 203, "click_ms": click_ms, "cancel_ms": cancel_ms,
                "heartbeat_ticks": len(beats), "filesystem_operations_off_main_thread": len(calls),
                "evidence": "real Windows PyMOL with injected blocked folder I/O"}
    finally:
        release.set()
        timer.stop()


def check_remote_sources(panel, annotation, table, app, qt_test, qt, path_column):
    """Import actual mapped-drive sources while suppressing all source writes."""
    from pymol import cmd
    from pymol.Qt import QtCore
    table = Path(table)
    folder = table.parent / "structures"
    previous = annotation.STATE.dataset.source_path
    saved_paths = [table, Path(annotation.dataset_state_path(str(table), source_mode="csv")),
                   Path(annotation.dataset_state_path(str(folder)))]
    signatures = {path: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in saved_paths}
    results = {}
    main_thread = threading.get_ident()
    real_stat, real_listdir, real_scandir = os.stat, os.listdir, os.scandir
    probes = []

    def track(function):
        def call(path, *args, **kwargs):
            if threading.get_ident() == main_thread and str(path).lower().startswith(("k:", "\\\\?\\k:")):
                probes.append(str(path))
            return function(path, *args, **kwargs)
        return call

    with patch.object(annotation, "_write_persisted_dataset_state", return_value=None):
        try:
            for mode, source in (("csv", table), ("directory", folder)):
                panel._show_import()
                panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData(mode))
                if mode == "csv":
                    panel.csv_edit.setText(str(source))
                    panel._csv_path_edited()
                    wait_for(app, lambda: not panel._csv_task_id)
                    if path_column:
                        panel.path_column_edit.setText(path_column)
                else:
                    panel.dir_edit.setText(str(source))
                beats = []
                timer = QtCore.QTimer()
                timer.timeout.connect(lambda: beats.append(time.perf_counter()))
                timer.start(10)
                begin = time.perf_counter()
                try:
                    with patch.object(os, "stat", track(real_stat)), patch.object(os, "listdir", track(real_listdir)), patch.object(os, "scandir", track(real_scandir)):
                        qt_test.mouseClick(panel.csv_load_btn if mode == "csv" else panel.load_btn, qt.LeftButton)
                        click_ms = (time.perf_counter() - begin) * 1000
                        wait_for(app, lambda: not panel._csv_task_id, timeout=180)
                finally:
                    timer.stop()
                end = time.perf_counter()
                assert annotation.STATE.dataset.source_path == str(source), panel.error_label.text()
                assert len(annotation.STATE.entries) == 600
                assert cmd.count_atoms(annotation.STATE.current_object) > 0
                gaps = [b - a for a, b in zip([begin] + beats, beats + [end])]
                results[mode] = dict(source=str(source), rows=len(annotation.STATE.entries),
                                     click_ms=click_ms, import_seconds=end - begin,
                                     heartbeat_ticks=len(beats), max_heartbeat_gap_ms=max(gaps) * 1000,
                                     current_atoms=cmd.count_atoms(annotation.STATE.current_object))
            assert not probes, probes
        finally:
            annotation.sa_load_dir(previous, restore_saved=False, raise_errors=True)
            panel._sync_from_state()
            panel._refresh_status()
    for path, before in signatures.items():
        after = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        assert after == before, str(path)
    results["source_table_and_state_unchanged"] = True
    results["main_thread_remote_probes"] = probes
    return results


def run(output, remote_table=None, path_column=""):
    global _APPLICATION
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _APPLICATION = app
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-source-review-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        isolate_workspace_settings(directory, window)
        first, review = table_fixture(directory / "first", "sum_scores.csv", ",")
        second, _ = table_fixture(directory / "second", "sum_scores.tsv", "\t")
        originals = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for folder in (first.parent, second.parent) for path in folder.iterdir()}
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        from pymolplugins.annotation.mapping_widgets import MappingProfileDialog
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        panel = annotation.show_structure_annotator_panel(mode="floating")
        qt_test, qt = QtTest.QTest, QtCore.Qt
        clipboard = app.clipboard()
        previous_clipboard = clipboard.text()
        try:
            panel.window().activateWindow()
            panel.window().raise_()
            open_table(panel, first, app, qt_test, qt)
            assert annotation.STATE.csv_path == str(first)
            assert annotation.STATE.display_columns == ["score1", "score2"]
            assert annotation.STATE.label_column == "label"
            assert panel.current_title_label.text() == "representative mode"
            assert annotation.STATE.step_cmd == review["step_cmd"]
            assert cmd.count_atoms(annotation.STATE.current_object) == 3
            assert not annotation.STATE.annotated_entry_ids
            flush_writes()
            assert not (first.parent / "review.override.json").exists()

            panel._columns_selected(["score2"])
            panel.step_cmd_edit.setPlainText("color yellow, {object}")
            assert panel._apply_commands()
            flush_writes()
            override = json.loads((first.parent / "review.override.json").read_text())
            assert override["display_columns"] == ["score2"]
            assert override["step_cmd"] == "color yellow, {object}"

            panel._show_import()
            panel.csv_edit.setFocus()
            panel.csv_edit.selectAll()
            clipboard.setText(str(second))
            qt_test.keyClick(panel.csv_edit, qt.Key_V, qt.ControlModifier)
            assert panel.csv_edit.text() == str(second), "History replaced the pasted table path"
            qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id and not panel._pending_csv_action)
            assert annotation.STATE.csv_path == str(second), (annotation.STATE.csv_path, panel.error_label.text())
            assert annotation.STATE.csv_delimiter == "\t"
            assert annotation.STATE.step_cmd == review["step_cmd"]
            open_table(panel, first, app, qt_test, qt)
            assert annotation.STATE.display_columns == ["score2"]
            assert annotation.STATE.step_cmd == "color yellow, {object}"

            active = annotation.STATE.current_object
            bad = second.parent / "review.json"
            good = bad.read_bytes()
            bad.write_text('{"display_columns": ["not_a_column"]}')
            panel._show_import()
            panel.csv_edit.setText(str(second))
            qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id and not panel._pending_csv_action)
            assert panel.error_label.isVisible() and "unknown table columns" in panel.error_label.text()
            assert annotation.STATE.current_object == active
            bad.write_bytes(good)

            dialog = MappingProfileDialog(panel)
            dialog._new()
            dialog.name_edit.setText("drive root")
            dialog._add_root()
            dialog.roots.item(0, 0).setText("/")
            dialog.roots.item(0, 1).setText("K:")
            dialog.sample_edit.setText("/010_rna/summary.tsv")
            dialog._test()
            assert dialog.error_label.text() == "", dialog.error_label.text()
            assert "K:\\010_rna\\summary.tsv" in dialog.result_label.text()
            assert dialog.draft()["rules"][0]["target_prefix"] == "K:\\"
            dialog.reject()

            folder_result = check_slow_folder(panel, annotation, directory, app, qt_test, qt)
            remote_result = check_remote_sources(panel, annotation, remote_table, app, qt_test, qt, path_column) if remote_table else None
            panel._show_import()
            panel.dir_edit.setText(str(directory / "missing folder"))
            qt_test.mouseClick(panel.load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            assert panel.error_label.isVisible()
            assert len(annotation.STATE.entries) == 203
            for path, before in originals.items():
                assert hashlib.sha256(path.read_bytes()).hexdigest() == before, str(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            panel.grab().save(str(output.with_suffix(".png")))
            result = {"status": "passed", "platform": sys.platform, "pymol": cmd.get_version()[0],
                    "review_import_override_reopen": "passed", "source_label_is_not_annotation": "passed",
                    "csv_tsv_pasted_path_switch": "passed", "invalid_review_preserves_scene": "passed",
                    "drive_root_mapping": "passed", "source_hashes_unchanged": len(originals),
                    "slow_folder": folder_result, "failed_folder_preserves_scene": "passed",
                    "remote_sources": remote_result}
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
        finally:
            clipboard.setText(previous_clipboard)
            panel.close()
            assert annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._delete_managed_objects(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--remote-table", type=Path)
    parser.add_argument("--path-column", default="")
    args = parser.parse_args()
    try:
        result = run(args.output, args.remote_table, args.path_column)
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
