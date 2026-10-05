"""Real Windows PyMOL acceptance: SSH profile imports, fuzzy completion, Ctrl+click."""

import argparse
import json
import os
from pathlib import Path
import posixpath
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_workflow_smoke import wait_for
from workspace_helpers import isolate_workspace_settings

_APPLICATION = None
_WINDOW = None


def idle(panel, app):
    wait_for(app, lambda: not panel._csv_task_id and not panel._pending_csv_action and panel._pending_navigation is None, timeout=90)


def choose(picker, value, app, qt_test, qt, *, control=False):
    if not picker.menu.isVisible():
        picker.open_menu()
    picker.search.clear()
    wait_for(app, picker.menu.isVisible)
    item = next(picker.items.item(index) for index in range(picker.items.count()) if picker.items.item(index).text() == value)
    qt_test.mouseClick(picker.items.viewport(), qt.LeftButton,
                       qt.ControlModifier if control else qt.NoModifier,
                       pos=picker.items.visualItemRect(item).center())


def check_fuzzy(panel, annotation, app, qt_test, qt):
    panel._show_import()
    panel.path_column_edit.setFocus()
    panel.path_column_edit.selectAll()
    qt_test.keyClicks(panel.path_column_edit, "strfil")
    popup = panel._column_bindings["path"].completer.popup()
    wait_for(app, popup.isVisible)
    assert panel.path_column_edit.text() == "strfil", "Completion rewrote unaccepted text"
    qt_test.keyClick(popup, qt.Key_Tab)
    assert panel.path_column_edit.text() == "structure_file"
    panel._back_to_review()
    panel._show_filters()
    wait_for(app, panel.filter_dialog.isActiveWindow)
    panel.filter_mode_combo.setCurrentIndex(1)
    binding = panel._column_bindings["filter"]
    for key in (qt.Key_Return, qt.Key_Enter, qt.Key_Tab):
        panel.filter_edit.clear()
        panel.filter_edit.setFocus()
        qt_test.keyClicks(panel.filter_edit, "acipm")
        wait_for(app, binding.completer.popup().isVisible)
        qt_test.keyClick(binding.completer.popup(), key)
        assert panel.filter_edit.text() == "AC.iptm"
        assert panel.filter_dialog.isVisible() and not panel._csv_task_id
    panel.filter_edit.setText("AC.iptm >= 0 and `iface qty")
    panel.filter_edit.setCursorPosition(len(panel.filter_edit.text()))
    binding.refresh()
    wait_for(app, binding.completer.popup().isVisible)
    qt_test.keyClick(binding.completer.popup(), qt.Key_Return)
    assert panel.filter_edit.text() == "AC.iptm >= 0 and `interface quality`"
    panel.filter_edit.setText("AC.iptm >= 0")
    qt_test.keyClick(panel.filter_edit, qt.Key_Return)
    idle(panel, app)
    assert len(annotation.STATE.entries) == 8
    panel.column_picker.open_menu()
    qt_test.keyClicks(panel.column_picker.search, "acipm")
    visible = [panel.column_picker.items.item(index).text() for index in range(panel.column_picker.items.count())
               if not panel.column_picker.items.item(index).isHidden()]
    assert visible == ["AC.iptm"], visible
    panel.column_picker.menu.hide()
    return "whole fields, quoted tokens, picker search and popup Tab/Return/keypad Enter passed"


def check_ctrl_click(panel, annotation, app, qt_test, qt):
    from pymol.Qt import QtCore
    fields = [{"name": "quality", "type": "multiple", "required": True, "values": ["Good", "Check", "Bad"]},
              {"name": "tags", "type": "multiple", "required": False, "values": ["Helix", "Loop"]}]
    annotation.STATE.annotation_fields = fields
    annotation.STATE.label_name, annotation.STATE.values_text = "quality", "Good,Check,Bad"
    panel._sync_from_state()
    panel._rebuild_value_controls(sync_to_state=False)
    panel.auto_advance_cb.setChecked(False)
    panel.save_on_label_click_cb.setChecked(False)
    panel._request_navigation(0)
    idle(panel, app)
    first = annotation.STATE.current_entry_id()
    picker = panel.review.form.editors["quality"]
    choose(picker, "Good", app, qt_test, qt)
    choose(picker, "Check", app, qt_test, qt)
    assert not panel._csv_task_id and annotation.STATE.index == 0
    assert annotation.STATE.row_annotation_store.annotations_for(first) == {}
    choose(picker, "Bad", app, qt_test, qt, control=True)
    idle(panel, app)
    assert annotation.STATE.index == 1, panel.error_label.text()
    assert json.loads(annotation.STATE.row_annotation_store.annotations_for(first)["quality"]) == ["Good", "Check", "Bad"]
    assert len(annotation.STATE.undo_history) == 1, "Ctrl+click saved more than once"

    second = annotation.STATE.current_entry_id()
    panel.review.form.optional_toggle.setChecked(True)
    choose(panel.review.form.editors["tags"], "Helix", app, qt_test, qt, control=True)
    assert panel.error_label.isVisible() and annotation.STATE.index == 1
    assert panel.review.form.values["tags"] == ["Helix"]
    assert annotation.STATE.row_annotation_store.annotations_for(second) == {}
    choose(panel.review.form.editors["quality"], "Good", app, qt_test, qt)
    qt_test.mouseClick(panel.review.form.editors["quality"].apply_button, qt.LeftButton)
    assert not panel._csv_task_id
    choose(panel.review.form.editors["tags"], "Loop", app, qt_test, qt, control=True)
    idle(panel, app)
    assert annotation.STATE.index == 2
    assert json.loads(annotation.STATE.row_annotation_store.annotations_for(second)["tags"]) == ["Helix", "Loop"]

    third = annotation.STATE.current_entry_id()
    store = annotation.STATE.row_annotation_store
    choose(panel.review.form.editors["quality"], "Good", app, qt_test, qt)
    with patch.object(store, "set_many", side_effect=OSError("injected write failure")):
        choose(panel.review.form.editors["quality"], "Check", app, qt_test, qt, control=True)
        idle(panel, app)
    assert annotation.STATE.index == 2 and "injected write failure" in panel.error_label.text()
    assert panel.review.form.values["quality"] == ["Good", "Check"]
    assert store.annotations_for(third) == {} and len(annotation.STATE.undo_history) == 2
    picker = panel.review.form.editors["quality"]
    picker.open_menu()
    qt_test.mouseClick(picker.apply_button, qt.LeftButton, qt.ControlModifier)
    idle(panel, app)
    assert annotation.STATE.index == 3 and len(annotation.STATE.undo_history) == 3
    assert json.loads(store.annotations_for(third)["quality"]) == ["Good", "Check"]

    selection = QtCore.QItemSelection(panel._dataset_model.index(4, 0), panel._dataset_model.index(5, 0))
    panel.dataset_table.selectionModel().select(selection, QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
    assert len(panel.review.targets()) == 2
    index = annotation.STATE.index
    choose(panel.review.form.editors["quality"], "Good", app, qt_test, qt, control=True)
    assert not panel._csv_task_id and annotation.STATE.index == index
    qt_test.mouseClick(panel.review.form.save_button, qt.LeftButton)
    idle(panel, app)
    assert annotation.STATE.index == index and len(annotation.STATE.undo_history) == 4
    panel.review.selection.clear()
    annotation.STATE.annotation_fields = [{"name": "quality", "type": "single", "required": True, "values": ["Good", "Bad"]}]
    annotation.STATE.values_text = "Good,Bad"
    panel._sync_from_state()
    panel._rebuild_value_controls(sync_to_state=False)
    panel._request_navigation(6)
    idle(panel, app)
    fresh = annotation.STATE.current_entry_id()
    qt_test.mouseClick(panel.review.form.primary_buttons[0], qt.LeftButton, qt.ControlModifier | qt.ShiftModifier)
    assert annotation.STATE.index == 6 and not panel._csv_task_id
    assert store.annotations_for(fresh) == {}
    qt_test.mouseClick(panel.review.form.primary_buttons[0], qt.LeftButton, qt.ControlModifier)
    idle(panel, app)
    assert annotation.STATE.index == 7 and store.annotations_for(fresh)["quality"] == "Good"
    return "default Ctrl+click, single/multiple/secondary fields, Shift, required fields, failure/retry and explicit batches passed"


def check_remote_history(panel, annotation, location, app, qt_test, qt):
    panel._show_import()
    panel.csv_edit.setFocus()
    panel.csv_edit.selectAll()
    qt_test.keyClicks(panel.csv_edit, "wdscr")
    binding = next(value for value in panel._history_bindings if value.field == "csv_path")
    wait_for(app, binding.completer.popup().isVisible)
    assert panel.csv_edit.text() == "wdscr"
    qt_test.keyClick(binding.completer.popup(), qt.Key_Return)
    assert panel.csv_edit.text() == location.path
    qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
    idle(panel, app)
    assert annotation.STATE.csv_path == location.uri, panel.error_label.text()
    return "remote source history accepted once and retained the SSH profile"


def check_remote_browser(panel, location, app, qt_test, qt):
    from pymolplugins.annotation.ssh_widgets import RemotePathDialog
    browser = RemotePathDialog(location.at(posixpath.dirname(location.path)), directory=False, parent=panel)
    browser.show()
    assert not browser.path.isEnabled(), "A pending folder response could overwrite an edited path"
    wait_for(app, lambda: browser.future is None and browser.items.isEnabled())
    assert browser.path.isEnabled()
    assert browser._last_progress_message.startswith("SSH "), browser._last_progress_message
    assert browser._progress_thread_id != threading.get_ident()
    assert browser._last_progress_on_main_thread is True

    original = browser.current_path
    qt_test.keyClicks(browser.search, "mdlpd")
    assert browser.items.count() >= 3
    first = browser.items.currentItem().text()
    qt_test.keyClick(browser.search, qt.Key_Down)
    assert browser.items.currentRow() == 1
    qt_test.keyClick(browser.search, qt.Key_Up)
    assert browser.items.currentItem().text() == first

    qt_test.mouseClick(browser.up, qt.LeftButton)
    wait_for(app, lambda: browser.future is None and browser.items.isEnabled())
    assert browser.current_path == posixpath.dirname(original)
    assert browser.search.text() == ""
    qt_test.keyClicks(browser.search, "rmstr")
    assert browser.items.count() == 1 and browser.items.currentItem().text() == "remote structures/"
    qt_test.keyClick(browser.search, qt.Key_Return)
    wait_for(app, lambda: browser.future is None and browser.items.isEnabled())
    assert browser.current_path == original
    assert browser.path.text() == original and browser.search.text() == ""

    qt_test.keyClicks(browser.search, "wdscr")
    browser.path.setText(posixpath.join(original, "missing folder"))
    qt_test.mouseClick(browser.go, qt.LeftButton)
    wait_for(app, lambda: browser.future is None)
    assert browser.current_path == original and browser.path.text() == original
    assert browser.search.text() == "wdscr", "A failed navigation cleared the active filter"
    assert browser.items.isEnabled() and browser.buttons.button(browser.buttons.Open).isEnabled()
    assert browser.items.count() == 1 and browser.items.currentItem().text() == "wide scores.csv"
    qt_test.keyClick(browser.search, qt.Key_Return)
    assert browser.selected_path == location.path and browser.result() == browser.Accepted
    return {"selection": "ranked fuzzy keyboard choice and directory navigation passed",
            "failed_navigation_preserved_path": True,
            "progress": browser._last_progress_message,
            "worker_thread": browser._progress_thread_id}


def check_direct_cache(annotation, app):
    from pymolplugins.annotation.cache import PersistentStructureCache
    from pymolplugins.annotation.materializer import SourceChangedError
    from pymolplugins.remote.ssh import SshClient

    entry = next(value for value in annotation.STATE.entries
                 if value.resolved_path.startswith("ssh://") and not value.resolved_path.lower().endswith(".gz"))
    previous_cache = annotation._structure_cache
    previous_copy = annotation._copy_materializer
    previous_preload = annotation.STATE.preload_enabled
    main_thread = threading.get_ident()
    download_threads = []
    original_download = SshClient.download

    class NoCopy:
        def materialize(self, *args, **kwargs):
            raise AssertionError("Non-gzip SSH materialization copied a completed download")

    def tracked_download(client, *args, **kwargs):
        download_threads.append(threading.get_ident())
        return original_download(client, *args, **kwargs)

    annotation.STATE.preload_enabled = False
    annotation._reset_bg_cache()
    try:
        root = Path(os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"]) / "direct-writer"
        annotation._structure_cache = PersistentStructureCache(str(root))
        annotation._copy_materializer = NoCopy()
        with patch.object(SshClient, "download", tracked_download):
            first_future = annotation._get_bg_executor().submit(annotation._materialize_ssh_entry, entry)
            wait_for(app, first_future.done, timeout=30)
            first = first_future.result()
            second_future = annotation._get_bg_executor().submit(annotation._materialize_ssh_entry, entry)
            wait_for(app, second_future.done, timeout=30)
            second = second_future.result()
        assert not first.cache_hit and second.cache_hit
        assert first.path == second.path and len(download_threads) == 1
        assert all(value != main_thread for value in download_threads)

        changed_root = Path(os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"]) / "identity-change"
        changed_cache = PersistentStructureCache(str(changed_root))
        annotation._structure_cache = changed_cache

        def changed_download(client, *args, **kwargs):
            value = dict(original_download(client, *args, **kwargs))
            value["ctime_ns"] += 1
            return value

        with patch.object(SshClient, "download", changed_download):
            changed_future = annotation._get_bg_executor().submit(annotation._materialize_ssh_entry, entry)
            wait_for(app, changed_future.done, timeout=30)
            try:
                changed_future.result()
            except SourceChangedError:
                pass
            else:
                raise AssertionError("Changed remote identity was published under the observed cache key")
        assert not any(path.is_file() for path in (changed_root / "objects").rglob("*"))
        return {"cold_cache_hit": first.cache_hit, "warm_cache_hit": second.cache_hit,
                "download_threads_off_main": len(download_threads),
                "identity_mismatch_not_published": True}
    finally:
        annotation._structure_cache = previous_cache
        annotation._copy_materializer = previous_copy
        annotation.STATE.preload_enabled = previous_preload


def check_navigation_cancel(panel, annotation, app, qt_test, qt):
    from pymolplugins.remote.ssh import SshClient
    from pymolplugins.remote import ssh as ssh_transport

    previous_preload = annotation.STATE.preload_enabled
    annotation.STATE.preload_enabled = False
    annotation._reset_bg_cache()
    current = annotation.STATE.index
    target = (current + 1) % len(annotation.STATE.entries)
    entry = annotation.STATE.entries[target]
    object_name = annotation._object_name_for_index(annotation.STATE.files[target], target)
    if annotation._object_exists(object_name):
        annotation.cmd.delete(object_name)
    started = threading.Event()
    original = SshClient.command

    def slow_command(client):
        command = original(client)
        command[-1] = "sleep 5; " + command[-1]
        started.set()
        return command

    panel._last_ssh_progress_message = ""
    try:
        with patch.object(SshClient, "command", slow_command):
            panel._request_navigation(target)
            wait_for(app, started.is_set)
            wait_for(app, lambda: "Connecting / authenticating" in panel._last_ssh_progress_message)
            assert panel._pending_navigation is not None
            assert panel.cancel_task_button.isVisible()
            assert panel._last_ssh_progress_on_main_thread is True
            assert any(session.command[-1].startswith("sleep 5;") and session.process is not None
                       and session.process.poll() is None for session in list(ssh_transport._sessions.values()))
            begin = time.perf_counter()
            qt_test.mouseClick(panel.cancel_task_button, qt.LeftButton)
            cancel_ms = (time.perf_counter() - begin) * 1000
            wait_for(app, lambda: panel._pending_navigation is None)
            wait_for(app, lambda: not any(
                session.command[-1].startswith("sleep 5;")
                and session.process is not None and session.process.poll() is None
                for session in list(ssh_transport._sessions.values())
            ), timeout=10)
            assert annotation.STATE.index == current
            assert cancel_ms < 250
        return {"progress": panel._last_ssh_progress_message, "cancel_ms": cancel_ms,
                "current_candidate_preserved": True}
    finally:
        annotation.STATE.preload_enabled = previous_preload


def check_failures(panel, annotation, app, qt_test, qt):
    from pymolplugins.remote.ssh import SshClient
    from pymolplugins.remote import ssh as ssh_transport
    source, current = annotation.STATE.dataset.source_path, annotation.STATE.current_object
    panel._show_import()
    panel.source_location.profile.setCurrentText("pymol-unreachable")
    qt_test.mouseClick(panel.load_btn, qt.LeftButton)
    idle(panel, app)
    assert "SSH" in panel.error_label.text(), panel.error_label.text()
    assert annotation.STATE.dataset.source_path == source and annotation.STATE.current_object == current
    panel.source_location.profile.setCurrentText("pymol-ssh-test")
    started = threading.Event()
    original = SshClient.command

    def slow_command(client):
        command = original(client)
        command[-1] = "sleep 5; " + command[-1]
        started.set()
        return command

    panel._last_ssh_progress_message = ""
    with patch.object(SshClient, "command", slow_command):
        qt_test.mouseClick(panel.load_btn, qt.LeftButton)
        wait_for(app, started.is_set)
        wait_for(app, lambda: "Connecting / authenticating" in panel._last_ssh_progress_message)
        assert "Cancel is available" in panel._last_ssh_progress_message
        assert panel._last_ssh_progress_on_main_thread is True
        assert panel.cancel_task_button.isVisible()
        wait_for(app, lambda: any(session.command[-1].startswith("sleep 5;") and session.process is not None
                                 and session.process.poll() is None for session in list(ssh_transport._sessions.values())))
        begin = time.perf_counter()
        qt_test.mouseClick(panel.cancel_task_button, qt.LeftButton)
        elapsed = (time.perf_counter() - begin) * 1000
        assert elapsed < 250
        assert annotation._csv_async_pool().waitForDone(2000), "Cancelled SSH login process kept the worker occupied"
        app.processEvents()
    assert annotation.STATE.dataset.source_path == source and annotation.STATE.current_object == current
    return {"connection_failure_preserves_scene": True, "cancel_ms": elapsed,
            "progress": panel._last_ssh_progress_message,
            "cancellation_evidence": "real SSH with an injected remote sleep"}


def run(args):
    global _APPLICATION, _WINDOW
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    from pymolplugins.remote.ssh import SshClient, SshLocation, close_ssh_sessions
    _APPLICATION = app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    _WINDOW = window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-ssh-ui-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        os.environ["PYMOLPLUGINS_SSH_STATE_ROOT"] = str(directory / "ssh states")
        isolate_workspace_settings(directory, window)
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        annotation.STATE = annotation.AnnotatorState()
        panel = annotation.show_structure_annotator_panel(mode="floating")
        qt_test, qt = QtTest.QTest, QtCore.Qt
        panel.window().activateWindow()
        panel.window().raise_()
        location = SshLocation("pymol-ssh-test", args.remote_table, str(args.ssh_config))
        calls, beats = [], []
        main_thread = threading.get_ident()
        original_response = SshClient._response

        def tracked(client, *values, **kwargs):
            calls.append(threading.get_ident())
            return original_response(client, *values, **kwargs)

        timer = QtCore.QTimer()
        timer.timeout.connect(lambda: beats.append(time.perf_counter()))
        timer.start(10)
        try:
            panel.source_location.config.setText(str(args.ssh_config))
            panel.source_location._reload_profiles()
            assert panel.source_location.profile.findText("pymol-ssh-test") >= 0
            panel.source_location.connection.setCurrentIndex(1)
            panel.source_location.profile.setCurrentText("pymol-ssh-test")
            panel.source_mode_combo.setCurrentIndex(1)
            panel.csv_edit.setText(args.remote_table)
            begin = time.perf_counter()
            with patch.object(SshClient, "_response", tracked):
                qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
                click_ms = (time.perf_counter() - begin) * 1000
                idle(panel, app)
            assert annotation.STATE.csv_path == location.uri, panel.error_label.text()
            assert len(annotation.STATE.entries) == 8
            assert annotation.STATE.entries[0].source_path == "model 0.pdb"
            assert annotation.STATE.entries[1].source_path == posixpath.join(posixpath.dirname(args.remote_table), "model 1.pdb")
            assert cmd.count_atoms(annotation.STATE.current_object) == 3
            assert all(thread != main_thread for thread in calls), "SSH ran on the Qt thread"
            assert click_ms < 250
            wait_for(app, lambda: annotation._object_exists(annotation._object_name_for_index(annotation.STATE.files[1], 1)))
            with patch.object(cmd, "load", side_effect=AssertionError("Warm Next loaded a structure")):
                warm = time.perf_counter()
                qt_test.mouseClick(panel.next_btn, qt.LeftButton)
                warm_ms = (time.perf_counter() - warm) * 1000
            assert annotation.STATE.index == 1
            assert panel._last_ssh_progress_message.startswith("SSH "), panel._last_ssh_progress_message
            assert panel._last_ssh_progress_on_main_thread is True
            results = {"status": "passed", "platform": sys.platform, "pymol": cmd.get_version()[0],
                       "host_python": sys.executable,
                       "ssh_login_command_prefix": SshClient(location).command()[:-1],
                       "ssh_csv_rows": 8, "ssh_calls_off_main_thread": len(calls),
                       "import_click_ms": click_ms, "warm_next_ms": warm_ms,
                       "heartbeat_ticks": len(beats),
                       "progress": panel._last_ssh_progress_message,
                       "ssh_progress_on_main_thread": panel._last_ssh_progress_on_main_thread,
                       "ssh_ux_only": bool(args.ssh_ux_only)}
            results["direct_cache"] = check_direct_cache(annotation, app)
            if not args.ssh_ux_only:
                results["fuzzy_completion"] = check_fuzzy(panel, annotation, app, qt_test, qt)
                flush_writes()
                results["remote_history"] = check_remote_history(panel, annotation, location, app, qt_test, qt)
                results["ctrl_click"] = check_ctrl_click(panel, annotation, app, qt_test, qt)
            results["remote_browser"] = check_remote_browser(panel, location, app, qt_test, qt)
            flush_writes()
            assert annotation.STATE.row_annotation_store.path.startswith(str(directory / "ssh states"))
            panel._show_import()
            panel.source_mode_combo.setCurrentIndex(0)
            panel.dir_edit.setText(posixpath.dirname(args.remote_table))
            qt_test.mouseClick(panel.load_btn, qt.LeftButton)
            idle(panel, app)
            assert len(annotation.STATE.entries) == 3, panel.error_label.text()
            assert any(entry.source_path.endswith(".pdb.gz") for entry in annotation.STATE.entries)
            results["ssh_directory_structures"] = 3
            results["navigation_cancel"] = check_navigation_cancel(panel, annotation, app, qt_test, qt)
            results["failures"] = check_failures(panel, annotation, app, qt_test, qt)
            panel._back_to_review()
            panel.grab().save(str(args.output.with_suffix(".png")))
            args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            return results
        finally:
            timer.stop()
            panel.close()
            assert annotation._csv_async_pool().waitForDone(30000)
            if annotation._bg_executor is not None:
                annotation._bg_executor.shutdown(wait=True)
            close_ssh_sessions()
            flush_writes()
            annotation._delete_managed_objects(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ssh-config", required=True, type=Path)
    parser.add_argument("--remote-table", required=True)
    parser.add_argument("--ssh-ux-only", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
