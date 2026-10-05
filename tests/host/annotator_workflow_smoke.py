"""Exercise the real Windows Qt panel, PyMOL commands, and CSV import together."""

import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import statistics
import shutil
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_helpers import isolate_workspace_settings


def wait_for(app, condition, timeout=30):
    from pymol.Qt import QtCore
    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer()
    timer.setInterval(10)
    deadline = time.monotonic() + timeout
    errors = []
    def check():
        try:
            if condition():
                loop.quit()
            elif time.monotonic() >= deadline:
                raise TimeoutError("Qt operation did not reach its expected terminal state")
        except Exception as exc:
            errors.append(exc)
            loop.quit()
    timer.timeout.connect(check)
    timer.start()
    check()
    if not condition() and not errors:
        loop.exec_()
    timer.stop()
    if errors:
        raise errors[0]


def fixture(directory, rows, columns):
    for index in range(3):
        (directory / f"model {index}.pdb").write_text(
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  ALA A   2       3.800   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  GLY A   3       6.100   2.500   0.000  1.00 20.00           C\nEND\n"
        )
    path = directory / "wide scores.csv"
    headers = ["structure_file", "row_id", "AC.iptm", "full_pass"]
    headers += [f"score_{index}" for index in range(max(0, columns - 4))]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for index in range(rows):
            writer.writerow([f"model {index % 3}.pdb", f"row-{index}", f"{index % 100 / 100:.2f}", index % 2 == 0]
                            + [str(index + column) for column in range(len(headers) - 4)])
    return path


def check_column_selection(panel, annotation, qt_test, qt, app):
    from pymol.Qt import QtCore
    picker = panel.column_picker
    picker.open_menu()
    qt_test.keyClicks(picker.search, "AC.ip")
    app.processEvents()
    matches = [picker.items.item(i) for i in range(picker.items.count()) if not picker.items.item(i).isHidden()]
    assert [item.text() for item in matches] == ["AC.iptm"]
    item = matches[0]
    rectangle = picker.items.visualItemRect(item)
    qt_test.mouseClick(picker.items.viewport(), qt.LeftButton, pos=rectangle.topLeft() + QtCore.QPoint(10, rectangle.height() // 2))
    qt_test.mouseClick(picker.apply_button, qt.LeftButton)
    assert annotation.STATE.display_columns == ["AC.iptm"]
    assert panel._dataset_model.columnCount() == 5
    picker.open_menu()
    qt_test.mouseClick(picker.all_button, qt.LeftButton)
    assert len(picker._choices()) == picker.items.count()
    qt_test.mouseClick(picker.clear_button, qt.LeftButton)
    assert picker._choices() == []
    picker.menu.hide()
    wait_for(app, lambda: app.activePopupWidget() is None)


def check_filters(panel, annotation, qt_test, qt, app, rows):
    qt_test.mouseClick(panel.filter_button, qt.LeftButton)
    assert panel.filter_dialog.isVisible()
    wait_for(app, panel.filter_dialog.isActiveWindow)
    panel.filter_mode_combo.setCurrentIndex(1)
    panel.filter_edit.setFocus()
    popup = panel._column_bindings["filter"].completer.popup()
    initial_filter = annotation.STATE.filter_text
    for key, target in ((qt.Key_Return, popup), (qt.Key_Enter, popup),
                        (qt.Key_Return, panel.filter_edit), (qt.Key_Tab, panel.filter_edit)):
        panel.filter_edit.clear()
        panel.filter_edit.setFocus()
        qt_test.keyClicks(panel.filter_edit, "AC.ip")
        wait_for(app, popup.isVisible)
        qt_test.keyClick(target, key)
        app.processEvents()
        assert panel.filter_edit.text() == "AC.iptm", panel.filter_edit.text()
        assert panel.filter_dialog.isVisible(), "Accepting a column completion submitted the filter"
        assert not panel._csv_task_id and annotation.STATE.filter_text == initial_filter
        assert not popup.isVisible()
    panel.filter_edit.setText("AC.iptm >= 0.8 and score_")
    panel.filter_edit.setCursorPosition(len(panel.filter_edit.text()))
    panel._column_bindings["filter"].refresh()
    wait_for(app, popup.isVisible)
    qt_test.keyClick(popup, qt.Key_Down)
    highlighted = str(popup.currentIndex().data())
    qt_test.keyClick(popup, qt.Key_Return)
    assert panel.filter_edit.text() == "AC.iptm >= 0.8 and " + highlighted
    assert panel.filter_dialog.isVisible() and not panel._csv_task_id
    panel.filter_edit.setText("AC.iptm >= 0.8 and full_pass == True")
    panel.sort_column_combo.setCurrentText("score_0")
    panel.sort_desc_cb.setChecked(True)
    panel.filter_edit.setFocus()
    qt_test.keyClick(panel.filter_edit, qt.Key_Return)
    wait_for(app, lambda: not panel._csv_task_id)
    expected = sum(index % 100 >= 80 and index % 2 == 0 for index in range(rows))
    assert not panel.error_label.isVisible(), panel.error_label.text()
    assert len(annotation.STATE.entries) == expected
    assert panel._dataset_model.rowCount() == expected
    assert all(float(entry.metadata["AC.iptm"]) >= .8 for entry in annotation.STATE.entries)
    observed = [float(entry.metadata["score_0"]) for entry in annotation.STATE.entries]
    assert observed == sorted(observed, reverse=True)
    panel.filter_edit.setText("AC.iptm > 2")
    panel._apply_view()
    wait_for(app, lambda: not panel._csv_task_id)
    assert len(annotation.STATE.entries) == panel._dataset_model.rowCount() == 0
    assert not panel.next_btn.isEnabled()
    panel._clear_view()
    wait_for(app, lambda: not panel._csv_task_id)
    assert len(annotation.STATE.entries) == rows
    panel.filter_edit.setText("nonexistent_column > 0")
    panel._apply_view()
    wait_for(app, lambda: not panel._csv_task_id)
    assert panel.error_label.isVisible() and "Unknown CSV column" in panel.error_label.text()
    assert len(annotation.STATE.entries) == rows
    panel.filter_edit.clear()
    panel.error_label.hide()


def check_completion(panel, annotation, qt_test, qt, app, directory):
    from pymol import cmd
    from pymol.Qt import QtGui
    from pymolplugins.annotation.persistence import flush_writes
    flush_writes()
    history = directory / "history.json"
    before = history.read_bytes()
    original = panel.step_cmd_edit.toPlainText()
    panel.main_tabs.setCurrentIndex(2)
    from pymolplugins.annotation.qt_widgets import CollapsibleSection
    for section in panel.findChildren(CollapsibleSection):
        if section.toggle.text() == "Automation":
            section.toggle.setChecked(True)
    panel.step_cmd_edit.setFocus()
    panel.step_cmd_edit.clear()
    qt_test.keyClicks(panel.step_cmd_edit, "colo")
    expected = cmd._parser.complete("colo")
    qt_test.keyClick(panel.step_cmd_edit, qt.Key_Tab)
    assert panel.step_cmd_edit.toPlainText() == expected
    panel.step_cmd_edit.setPlainText("show cart")
    panel.step_cmd_edit.moveCursor(QtGui.QTextCursor.End)
    expected = cmd._parser.complete("show cart")
    qt_test.keyClick(panel.step_cmd_edit, qt.Key_Tab)
    assert panel.step_cmd_edit.toPlainText() == expected
    qt_test.qWait(400)
    flush_writes()
    assert history.read_bytes() == before, "Intermediate command edits were written to history"
    panel.step_cmd_edit.setPlainText(original)
    panel._sync_to_state(persist=False)
    panel.main_tabs.setCurrentWidget(panel.review_tab)


def check_shortcuts(panel, annotation, qt_test, qt, app, window=None):
    panel.auto_advance_cb.setChecked(False)
    panel.activateWindow()
    app.processEvents()
    cases = [(panel.dataset_table, qt.Key_1, "Good"), (panel.review.form.editors["notes"], qt.Key_2, "Bad")]
    if window is not None:
        cases += [(window.pymolwidget, qt.Key_1, "Good"), (window.lineedit, qt.Key_2, "Bad")]
    for target, key, value in cases:
        if target is panel.review.form.editors["notes"]:
            panel.review.form.scroll.ensureWidgetVisible(panel.review.form.optional_toggle)
            qt_test.mouseClick(panel.review.form.optional_toggle, qt.LeftButton)
            panel.review.form.scroll.ensureWidgetVisible(target)
            assert target.isVisible()
        target.window().activateWindow()
        target.window().raise_()
        target.setFocus()
        qt_test.qWait(60)
        before = annotation.STATE.annotation_generation
        qt_test.keyClick(target, key, qt.AltModifier)
        wait_for(app, lambda: annotation.STATE.annotation_generation > before)
        assert annotation.STATE.annotation_generation == before + 1
        assert annotation.STATE.row_annotation_store.get(annotation.STATE.current_entry_id(), "quality") == value
    panel._undo()
    wait_for(app, lambda: not panel._csv_task_id)
    assert annotation.STATE.row_annotation_store.get(annotation.STATE.current_entry_id(), "quality") == "Good"
    if window is not None:
        window.toggle_ext_window_dockable()
        qt_test.qWait(60)
        target = window.lineedit
        target.window().activateWindow()
        target.setFocus()
        before = annotation.STATE.annotation_generation
        qt_test.keyClick(target, qt.Key_2, qt.AltModifier)
        wait_for(app, lambda: annotation.STATE.annotation_generation > before)
        assert annotation.STATE.annotation_generation == before + 1
        window.toggle_ext_window_dockable()


def check_import_failure(panel, annotation, qt_test, qt, app):
    current_id = annotation.STATE.current_entry_id()
    current_object = annotation.STATE.current_object
    previous = panel.path_column_edit.text()
    panel.main_tabs.setCurrentIndex(1)
    panel.path_column_edit.setText("missing_path_column")
    qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
    wait_for(app, lambda: not panel._csv_task_id)
    assert panel.error_label.isVisible()
    assert "path_column" in panel.error_label.text()
    assert annotation.STATE.current_entry_id() == current_id
    assert annotation.STATE.current_object == current_object
    assert annotation.STATE.path_column == previous
    assert annotation._verify_current_object_enabled()
    panel.path_column_edit.setText(previous)
    panel.error_label.hide()
    panel.main_tabs.setCurrentWidget(panel.review_tab)
    panel._refresh_status()


def check_cancelled_import(panel, annotation, qt_test, qt, app):
    original = annotation._prepare_csv_load
    current_step = panel.step_cmd_edit.toPlainText().strip()
    entered, release = threading.Event(), threading.Event()
    calls = [0]
    def blocked(*args, **kwargs):
        prepared = original(*args, **kwargs)
        calls[0] += 1
        if calls[0] == 1:
            entered.set()
            if not release.wait(30):
                raise TimeoutError("Test did not release the cancelled import")
        return prepared
    panel.main_tabs.setCurrentIndex(1)
    try:
        with patch.object(annotation, "_prepare_csv_load", blocked):
            qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
            wait_for(app, entered.is_set)
            job_id = panel._csv_task_id
            started = time.perf_counter()
            qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
            cancelled_ms = (time.perf_counter() - started) * 1000
            assert cancelled_ms < 100
            assert job_id not in annotation._CSV_ASYNC_JOBS
            assert not panel._csv_task_id
            qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            generation = annotation.STATE.dataset_generation
            release.set()
            wait_for(app, lambda: annotation._csv_async_pool().activeThreadCount() == 0)
            app.processEvents()
            assert annotation.STATE.dataset_generation == generation
            assert annotation.STATE.step_cmd == current_step
            assert not annotation._CSV_ASYNC_JOBS
            assert annotation._verify_current_object_enabled()
            panel.column_picker.menu.hide()
            return cancelled_ms
    finally:
        release.set()


def check_closed_import(panel, annotation, qt_test, qt, app, window):
    original = annotation._prepare_csv_load
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        prepared = original(*args, **kwargs)
        entered.set()
        if not release.wait(30):
            raise TimeoutError("Test did not release the closed panel import")
        return prepared
    try:
        with patch.object(annotation, "_prepare_csv_load", blocked):
            panel._load_csv_dataset()
            wait_for(app, entered.is_set)
            generation = annotation.STATE.dataset_generation
            panel.close()
            assert not annotation._CSV_ASYNC_JOBS
            assert all(not shortcut.isEnabled() for shortcut in panel._qt_shortcuts.shortcuts)
            release.set()
            wait_for(app, lambda: annotation._csv_async_pool().activeThreadCount() == 0)
            app.processEvents()
            assert annotation.STATE.dataset_generation == generation
            if window is not None:
                before = annotation.STATE.annotation_generation
                window.activateWindow()
                window.pymolwidget.setFocus()
                qt_test.qWait(50)
                qt_test.keyClick(window.pymolwidget, qt.Key_1, qt.AltModifier)
                qt_test.qWait(50)
                assert annotation.STATE.annotation_generation == before
    finally:
        release.set()


def run(rows, columns, output, source_csv=None, source_config=None, full_gui=False, performance_only=False):
    assert os.name == "nt", "This is a Windows PyMOL acceptance check"
    sys.path.insert(0, str(ROOT))
    from pymol import cmd
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = None
    if full_gui:
        from pymol import invocation, gui
        invocation.parse_args(["pymol", "-k", "-q"])
        from pmg_qt.pymol_qt_gui import PyMOLQtGUI
        window = PyMOLQtGUI()
        gui.createlegacypmgapp = window.createlegacypmgapp
        window.add_lineedit()
        window.show()
        app.processEvents()
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-workflow-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        isolate_workspace_settings(directory, window)
        source_read_seconds = 0.0
        if source_csv is None:
            path = fixture(directory, rows, columns)
        else:
            path = directory / Path(source_csv).name
            started = time.perf_counter()
            shutil.copyfile(source_csv, path)
            source_read_seconds = time.perf_counter() - started
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                columns = len(next(reader))
                rows = sum(1 for _ in reader)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        annotation.STATE = annotation.AnnotatorState()
        panel = annotation.show_structure_annotator_panel()
        try:
            panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData("csv"))
            panel.csv_edit.setText(str(path))
            panel._csv_path_edited()
            wait_for(app, lambda: not panel._csv_task_id)
            if source_config is not None:
                panel.path_column_edit.setText(source_config["path_column"])
                panel.source_base_edit.setText(source_config.get("source_base") or str(Path(source_csv).parent))
            assert panel.path_column_edit.text(), panel.error_label.text()
            assert len(panel._column_headers) == columns
            panel.activateWindow()
            panel.raise_()
            QtTest.QTest.qWait(60)
            panel.path_column_edit.clear()
            panel.path_column_edit.setFocus()
            QtTest.QTest.keyClicks(panel.path_column_edit, "structure_f")
            assert panel.path_column_edit.text() == "structure_f", repr(panel.path_column_edit.text())
            app.processEvents()
            QtTest.QTest.keyClick(panel.path_column_edit, QtCore.Qt.Key_Return)
            assert panel.path_column_edit.text() == "structure_file", repr(panel.path_column_edit.text())
            panel.id_column_edit.setText((source_config or {}).get("id_column", "") if source_csv else "row_id")
            beats = []
            timer = QtCore.QTimer()
            timer.timeout.connect(lambda: beats.append(time.perf_counter()))
            timer.start(10)
            started = time.perf_counter()
            QtTest.QTest.mouseClick(panel.csv_load_btn, QtCore.Qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id, timeout=60)
            imported_seconds = time.perf_counter() - started
            import_gaps = [b - a for a, b in zip([started] + beats, beats + [time.perf_counter()])]
            assert annotation.STATE.files, panel.error_label.text() or panel.status_label.text()
            assert len(annotation.STATE.entries) == rows
            assert annotation.STATE.display_columns == []
            assert annotation._verify_current_object_enabled()
            assert panel._last_csv_completion_on_main_thread
            panel.column_picker.menu.hide()
            assert panel.main_tabs.currentWidget() is panel.review_tab
            view_before_preload = cmd.get_view()
            wait_for(app, lambda: len(annotation.STATE.managed_objects) >= 3)
            assert all(abs(a - b) < 1e-5 for a, b in zip(view_before_preload, cmd.get_view())), "Preloading changed the current camera"
            samples, calls = [], []
            original_load = cmd.load
            original_stat = os.stat
            main_thread = threading.get_ident()
            in_click = [False]
            def tracked_load(*args, **kwargs):
                if in_click[0]:
                    calls.append("cmd.load")
                return original_load(*args, **kwargs)
            def tracked_stat(*args, **kwargs):
                if in_click[0] and threading.get_ident() == main_thread:
                    calls.append("os.stat")
                return original_stat(*args, **kwargs)
            with patch.object(cmd, "load", tracked_load), patch.object(os, "stat", tracked_stat):
                for _ in range(min(20, rows - 1)):
                    index = (annotation.STATE.index + 1) % rows
                    name = annotation._object_name_for_index(annotation.STATE.files[index], index)
                    wait_for(app, lambda: annotation._object_exists(name))
                    started = time.perf_counter()
                    in_click[0] = True
                    panel._next()
                    in_click[0] = False
                    samples.append((time.perf_counter() - started) * 1000)
                    assert annotation.STATE.index == index
            assert not calls, calls
            full_checks = source_csv is None and not performance_only
            cancel_ms = None
            if full_checks:
                check_column_selection(panel, annotation, QtTest.QTest, QtCore.Qt, app)
                check_filters(panel, annotation, QtTest.QTest, QtCore.Qt, app, rows)
                check_completion(panel, annotation, QtTest.QTest, QtCore.Qt, app, directory)
                check_shortcuts(panel, annotation, QtTest.QTest, QtCore.Qt, app, window)
                check_import_failure(panel, annotation, QtTest.QTest, QtCore.Qt, app)
                cancel_ms = check_cancelled_import(panel, annotation, QtTest.QTest, QtCore.Qt, app)
            timer.stop()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            output.parent.mkdir(parents=True, exist_ok=True)
            screenshot = output.with_suffix(".png")
            panel.activateWindow()
            panel.raise_()
            app.processEvents()
            panel.grab().save(str(screenshot))
            if source_csv is not None:
                assert hashlib.sha256(Path(source_csv).read_bytes()).hexdigest() == digest
            if full_checks:
                check_closed_import(panel, annotation, QtTest.QTest, QtCore.Qt, app, window)
            return {"status": "passed", "platform": sys.platform, "pymol": cmd.get_version()[0],
                    "rows": rows, "columns": columns, "import_seconds": imported_seconds,
                    "source_csv": str(source_csv) if source_csv else "synthetic",
                    "source_csv_read_seconds": source_read_seconds, "full_pymol_window": full_gui,
                    "import_max_heartbeat_gap_ms": max(import_gaps) * 1000,
                    "heartbeat_ticks": len(beats), "next_median_ms": statistics.median(samples),
                    "next_p95_ms": sorted(samples)[max(0, int(len(samples)*.95)-1)],
                    "next_max_ms": max(samples), "next_load_or_stat_calls": calls,
                    "column_picker": "passed" if full_checks else "not_run",
                    "filter_conditions_empty_errors": "passed" if full_checks else "not_run",
                    "filter_enter_completion_then_submit": "passed" if full_checks else "not_run",
                    "native_command_completion": "passed" if full_checks else "not_run",
                    "shortcuts_panel_and_editor": "passed" if full_checks else "not_run",
                    "shortcuts_viewport_docked_floating_console": "passed" if full_gui and full_checks else "not_run",
                    "failed_import_preserves_scene": "passed" if full_checks else "not_run",
                    "cancel_ms": cancel_ms, "cancel_and_close_suppress_late_results": "passed" if full_checks else "not_run",
                    "screenshot": str(screenshot)}
        finally:
            panel.close()
            futures = list(annotation._materialization_futures.values())
            annotation._reset_bg_cache()
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=30)
            assert annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            app.processEvents()
            annotation._delete_managed_objects(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--columns", type=int, default=128)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-history", action="store_true")
    parser.add_argument("--full-gui", action="store_true")
    parser.add_argument("--performance-only", action="store_true")
    args = parser.parse_args()
    try:
        source_csv, source_config = None, None
        if args.from_history:
            history = json.loads((Path.home() / ".pymolplugins" / "structure_annotator_history.json").read_text(encoding="utf-8"))
            snapshot = history["snapshots"][0]
            source_csv, source_config = snapshot["csv_path"], snapshot["config"]
        result = run(args.rows, args.columns, args.output, source_csv, source_config, args.full_gui, args.performance_only)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), file=sys.__stdout__, flush=True)
    if args.full_gui:
        from pymol import cmd
        cmd.quit(0 if result["status"] == "passed" else 1)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
