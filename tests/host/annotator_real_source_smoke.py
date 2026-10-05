"""Read-only Windows GUI acceptance for an explicitly supplied real SSH table."""

import argparse
import base64
from contextlib import contextmanager
import csv
import faulthandler
import hashlib
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


def timed_file_hash(location, directory, name):
    from pymolplugins.remote import ssh as ssh_transport
    from pymolplugins.remote.ssh import SshClient, ssh_progress

    path = directory / name
    client = SshClient(location)
    progress = []
    started = time.perf_counter()
    with ssh_progress(lambda message: progress.append((time.perf_counter(), message))):
        metadata = client.download(path)
    elapsed = time.perf_counter() - started
    key = (client.location.profile, client.location.config, tuple(client.command()))
    session = ssh_transport._sessions[key]
    first_response_progress_seconds = next((stamp - started for stamp, message in progress
                         if "Connecting / authenticating" not in message), None)
    wire_bytes = int(session.wire_bytes)
    transferred = int(session.transferred)
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seconds": elapsed, "first_response_progress_seconds": first_response_progress_seconds,
            "timing_note": "Progress is sampled; first response progress is not exact authentication duration.",
            "bytes": transferred, "wire_bytes": wire_bytes,
            "content_to_wire_ratio": transferred / wire_bytes if wire_bytes else None,
            "metadata": metadata,
            "progress": [message for _stamp, message in progress]}


def file_hash(location, directory, name):
    return timed_file_hash(location, directory, name)["sha256"]


def review_hashes(location):
    from pymolplugins.remote.ssh import SshClient
    parent = posixpath.dirname(location.path)
    values = SshClient(location).read_small_files([posixpath.join(parent, name) for name in ("review.json", "review.override.json")])
    return [hashlib.sha256(base64.b64decode(value["content"])).hexdigest() if value["status"] == "available" else value["status"] for value in values]


def run(args):
    global _APPLICATION, _WINDOW
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    from pymolplugins.remote.ssh import SshClient, SshLocation, close_ssh_sessions, default_ssh_program
    from pymolplugins.remote import ssh as ssh_transport
    from pymolplugins.annotation.review_file import read_review_file
    from pymolplugins.annotation.sources.csv_source import read_csv_header
    _APPLICATION = app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    _WINDOW = window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-real-source-") as temporary:
        directory = Path(temporary)
        for key, name in (("PYMOLPLUGINS_ANNOTATOR_HISTORY", "history.json"),
                          ("PYMOLPLUGINS_ANNOTATOR_MAPPINGS", "mappings.json"),
                          ("PYMOLPLUGINS_STRUCTURE_CACHE_DIR", "cache"),
                          ("PYMOLPLUGINS_SSH_STATE_ROOT", "ssh-state")):
            os.environ[key] = str(directory / name)
        isolate_workspace_settings(directory, window)
        location = SshLocation(args.profile, args.table)
        table_transfer = timed_file_hash(location, directory, "table.csv")
        table_hash = table_transfer["sha256"]
        assert table_transfer["first_response_progress_seconds"] is not None
        assert table_transfer["bytes"] == table_transfer["metadata"]["size"]
        assert table_transfer["wire_bytes"] > 0
        assert any("Connecting / authenticating" in value for value in table_transfer["progress"])
        assert any("MiB" in value for value in table_transfer["progress"])
        header = read_csv_header(location.uri)
        review = read_review_file(location.uri)
        path_column = review.config.get("path_column", args.path_column)
        with (directory / "table.csv").open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=header["delimiter"])
            columns = reader.fieldnames
            rows = list(reader)
        assert tuple(columns or ()) == tuple(header["columns"])
        first = location.resolve(rows[0][path_column])
        first_transfer = timed_file_hash(first, directory, "first.cif")
        first_hash = first_transfer["sha256"]
        assert first_transfer["bytes"] == first_transfer["metadata"]["size"]
        assert first_transfer["wire_bytes"] > 0
        assert not any("Connecting / authenticating" in value for value in first_transfer["progress"])
        before_review = review_hashes(location)
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        annotation.STATE = annotation.AnnotatorState()
        panel = annotation.show_structure_annotator_panel(mode="floating")
        qt_test, qt = QtTest.QTest, QtCore.Qt
        clipboard = app.clipboard()
        previous_clipboard = clipboard.text()
        timer = QtCore.QTimer()
        beats, calls, responses, operation_metrics = [], [], [], []
        timer.timeout.connect(lambda: beats.append(time.perf_counter()))
        original_response = SshClient._response
        main_thread = threading.get_ident()

        @contextmanager
        def tracked(client, *values, **kwargs):
            calls.append(threading.get_ident())
            response = original_response(client, *values, **kwargs)
            responses.append((values[0]["operation"], response))
            started = time.perf_counter()
            with response as value:
                yield value
            key = (client.location.profile, client.location.config, tuple(client.command()))
            session = ssh_transport._sessions[key]
            transferred = int(session.transferred)
            wire_bytes = int(session.wire_bytes)
            operation_metrics.append({"operation": values[0]["operation"],
                "seconds": time.perf_counter() - started,
                "bytes": transferred, "wire_bytes": wire_bytes,
                "content_to_wire_ratio": transferred / wire_bytes if wire_bytes else None,
                "used_output": kwargs.get("output") is not None})

        try:
            panel.window().activateWindow()
            panel.window().raise_()
            connection = panel.source_location.connection
            connection.lineEdit().setFocus()
            connection.lineEdit().selectAll()
            qt_test.keyClicks(connection.lineEdit(), args.profile)
            popup = panel.source_location.completion.completer.popup()
            wait_for(app, popup.isVisible)
            qt_test.keyClick(popup, qt.Key_Return)
            assert connection.currentText() == args.profile, "Completion selected the wrong case-sensitive SSH alias"
            assert panel.source_type_combo.currentData() == "auto"
            panel.source_path_edit.setFocus()
            clipboard.setText(args.table)
            qt_test.keyClick(panel.source_path_edit, qt.Key_V, qt.ControlModifier)
            assert panel.source_path_edit.text() == args.table
            assert panel._source_mode() == "csv", "Pasted CSV was treated as a folder"
            assert panel.csv_edit is panel.dir_edit is panel.source_path_edit
            assert panel.csv_load_btn is panel.load_btn is panel.source_open_button
            assert not panel.source_options_section.content.isVisible()
            visible_inputs = [widget for widget in panel.source_tab.findChildren(QtWidgets.QLineEdit) if widget.isVisible()]
            assert set(visible_inputs) == {connection.lineEdit(), panel.source_path_edit}, len(visible_inputs)
            panel.grab().save(str(args.output.with_name(args.output.stem + "-open.png")))
            timer.start(10)
            started = time.perf_counter()
            with patch.object(SshClient, "_response", tracked):
                qt_test.mouseClick(panel.source_open_button, qt.LeftButton)
                click_ms = (time.perf_counter() - started) * 1000
                def complete():
                    if not panel._csv_task_id and panel._pending_csv_action and time.perf_counter() - started > 3:
                        raise RuntimeError("Open remained queued without an active import job")
                    return not panel._csv_task_id and not panel._pending_csv_action
                wait_for(app, complete, timeout=35)
            import_seconds = time.perf_counter() - started
            assert not panel.error_label.isVisible(), panel.error_label.toolTip()
            assert annotation.STATE.csv_path == location.uri
            assert len(annotation.STATE.entries) == len(rows)
            assert annotation.STATE.path_column == path_column
            assert annotation.STATE.current_entry().source_path == rows[0][path_column]
            atoms = cmd.count_atoms(annotation.STATE.current_object)
            assert atoms > 0
            assert calls and all(thread != main_thread for thread in calls)
            assert click_ms < 250
            assert panel._last_ssh_progress_message.startswith("SSH "), panel._last_ssh_progress_message
            assert panel._last_ssh_progress_on_main_thread is True
            navigation = {"rows": len(rows), "tested": False}
            if len(rows) > 1:
                next_object = annotation._object_name_for_index(annotation.STATE.files[1], 1)
                wait_for(app, lambda: annotation._object_exists(next_object), timeout=35)
                with patch.object(cmd, "load", side_effect=AssertionError("Cached consecutive navigation loaded a structure")):
                    navigation_started = time.perf_counter()
                    qt_test.mouseClick(panel.next_btn, qt.LeftButton)
                    navigation["milliseconds"] = (time.perf_counter() - navigation_started) * 1000
                assert annotation.STATE.index == 1
                navigation["tested"] = True
            flush_writes()
            assert annotation.STATE.state_path.startswith(str(directory / "ssh-state"))
            assert file_hash(location, directory, "table-after.csv") == table_hash
            assert file_hash(first, directory, "first-after.cif") == first_hash
            assert review_hashes(location) == before_review
            connections = [session for session in ssh_transport._sessions.values()
                           if session.client.location.profile == args.profile and not session.closed]
            assert len(connections) == 1, "The import authenticated more than once"
            panel.grab().save(str(args.output.with_suffix(".png")))
            result = {"status": "passed", "platform": sys.platform, "pymol": cmd.get_version()[0],
                      "host_python": sys.executable,
                      "ssh_login_command_prefix": SshClient(location).command()[:-1],
                      "source": args.table, "profile": args.profile, "ssh_program": default_ssh_program(),
                      "rows": len(rows), "columns": len(columns), "path_column": path_column,
                      "delimiter": header["delimiter"],
                      "first_structure_atoms": atoms, "import_click_ms": click_ms,
                      "import_seconds": import_seconds, "heartbeat_ticks": len(beats),
                      "ssh_operations_off_main_thread": len(calls), "one_connection_path_open": "passed",
                      "ssh_connections": len(connections),
                      "cold_first_download": table_transfer,
                      "warm_structure_download": first_transfer,
                      "import_operations": operation_metrics,
                      "progress": panel._last_ssh_progress_message,
                      "ssh_progress_on_main_thread": panel._last_ssh_progress_on_main_thread,
                      "consecutive_navigation_cache": navigation,
                      "exact_profile_case": "passed", "csv_auto_detection": "passed",
                      "source_hashes_unchanged": {"table": table_hash, "structure": first_hash, "review": before_review}}
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
        except Exception:
            state = {"connection": panel.source_location.connection.currentText(),
                     "path": panel.source_path_edit.text(), "kind": panel._source_mode(),
                     "task_id": panel._csv_task_id, "task_kind": panel._csv_task_kind,
                     "pending": panel._pending_csv_action, "status": panel.status_label.text(),
                     "error": panel.error_label.toolTip(), "header_path": panel._header_csv_path,
                     "source_widgets_path": panel._source_widgets_csv_path,
                     "arguments": panel._csv_arguments(), "active_source": annotation.STATE.csv_path}
            state["ssh_processes"] = []
            for operation, response in responses:
                frame = response.gen.gi_frame
                if frame is None:
                    continue
                local = frame.f_locals
                session = local.get("session")
                process = session.process if session is not None else local.get("process")
                if process is None:
                    continue
                diagnostics = session.diagnostics if session is not None else local.get("diagnostics")
                diagnostic_text = ""
                if diagnostics is not None and not diagnostics.closed:
                    offset = diagnostics.tell()
                    diagnostics.seek(0)
                    diagnostic_text = diagnostics.read().decode("utf-8", errors="replace")
                    diagnostics.seek(offset)
                state["ssh_processes"].append({"operation": operation, "pid": process.pid,
                    "exit_code": process.poll(), "stdin_closed": process.stdin.closed,
                    "output_bytes": os.fstat(local["output"].fileno()).st_size,
                    "diagnostics": diagnostic_text})
            args.output.with_suffix(".state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            panel.grab().save(str(args.output.with_name(args.output.stem + "-error.png")))
            with args.output.with_suffix(".threads.log").open("w") as handle:
                faulthandler.dump_traceback(file=handle, all_threads=True)
            raise
        finally:
            clipboard.setText(previous_clipboard)
            timer.stop()
            panel.close()
            assert annotation._csv_async_pool().waitForDone(30000)
            if annotation._bg_executor is not None:
                annotation._bg_executor.shutdown(wait=True)
            close_ssh_sessions()
            flush_writes()
            annotation._delete_managed_objects(False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--path-column", default="relative_path")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)
