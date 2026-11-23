"""Install and exercise the reviewed ZIP in a real, isolated Windows Qt/OpenGL host.

Run with D:/Softwares/Pymol3/python.exe -I this_script.py --source <integration tree>
--output <evidence root>. Uses that tree's 21_hidden_desktop.py; never switches the
input desktop. Product modules are imported exclusively from the temporary install.
"""

import argparse
import csv
import ctypes
from ctypes import wintypes
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
from zipfile import ZipFile
from types import SimpleNamespace
from unittest.mock import patch

GIT = "C:/Users/ADMIN/scoop/apps/git/2.46.1/cmd/git.exe"
CHECKS = ("installation", "render", "collections", "csv", "annotation_versions", "batch_draft", "identity",
          "appearance", "ai_mapping", "batch_review", "lifecycle")


def dependencies(name):
    required = {"installation"} if name != "installation" else set()
    if name not in ("installation", "render"):
        required.add("render")
    if name in ("annotation_versions", "batch_draft", "identity", "appearance", "ai_mapping", "batch_review", "lifecycle"):
        required.add("csv")
    if name in ("batch_review", "lifecycle"):
        required.add("batch_draft")
    return required


def planned_checks(requested):
    selected = set(requested)
    while True:
        expanded = selected | set().union(*(dependencies(name) for name in selected))
        if expanded == selected:
            return tuple(name for name in CHECKS if name in selected)
        selected = expanded


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launch(args):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output.resolve() / ("01_" + stamp)
    output.mkdir(parents=True)
    source = args.source.resolve()
    metadata = {"source": str(source), "script": str(Path(__file__).resolve()),
                "script_sha256": digest(Path(__file__)), "runtime": sys.executable,
                "requested_checks": args.checks}
    for name, arguments in (("head", ["rev-parse", "HEAD"]), ("dirty", ["status", "--porcelain"])):
        metadata[name] = subprocess.check_output([GIT, "-C", str(source), *arguments], text=True,
                                                creationflags=subprocess.CREATE_NO_WINDOW).strip()
    if args.package:
        shutil.copyfile(args.package, output / args.package.name)
        metadata["reused_frozen_package"] = str(args.package.resolve())
    else:
        package = subprocess.run(["C:/Python313/python.exe", str(source / "scripts/package_plugin.py"),
                                  "--output-dir", str(output)], capture_output=True, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        (output / "02_package.log").write_text(package.stdout + package.stderr, encoding="utf-8")
        if package.returncode:
            write_json(output / "01_result.json", {"status": "failed", "stage": "packaging",
                                                   "exit_code": package.returncode, **metadata})
            return package.returncode
    archive, = output.glob("PymolPlugins-*.zip")
    metadata.update(package=str(archive), package_sha256=digest(archive))
    launcher = [sys.executable, "-I"]
    if args.launcher_wheel:
        wheel = args.launcher_wheel.resolve()
        dependencies = output / "00_launcher_dependencies"
        with ZipFile(wheel) as archive_file:
            archive_file.extractall(dependencies)
        metadata["launcher_wheel"] = {"path": str(wheel), "sha256": digest(wheel)}
        bootstrap = (
            "import os,sys,runpy; from pathlib import Path; p=Path(sys.argv.pop(1)); "
            "sys.path[:0]=[str(p),str(p/'win32'),str(p/'win32/lib'),str(p/'pywin32_system32')]; "
            "dlls=os.add_dll_directory(str(p/'pywin32_system32')); "
            "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')")
        launcher += ["-c", bootstrap, str(dependencies)]
    write_json(output / "02_candidate.json", metadata)
    process = subprocess.run([
        *launcher, str(source / "tests/host/21_hidden_desktop.py"),
        "--record", str(output / "03_desktop.json"), "--timeout", "180",
        str(Path(__file__).resolve()), "--child", "--source", str(source),
        "--output", str(output), "--package", str(archive),
        "--checks", *args.checks,
    ], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    (output / "03_launcher.log").write_text(process.stdout + process.stderr, encoding="utf-8")
    result_path = output / "01_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {
        "status": "failed", "error": "Hidden host exited without its result JSON"}
    desktop_path = output / "03_desktop.json"
    desktop = json.loads(desktop_path.read_text()) if desktop_path.exists() else {}
    result["desktop_launch"] = desktop
    if (process.returncode or desktop.get("input_before") != desktop.get("input_after")
            or not desktop or desktop.get("timed_out")):
        result["status"] = "failed"
    for name in planned_checks(args.checks):
        result.setdefault("checks", {}).setdefault(name, {
            "status": "skipped", "reason": "Host exited before this check"})
    if result.get("active_check"):
        result["checks"][result["active_check"]] = {
            "status": "failed", "reason": "Host exited during this check",
            "timed_out": desktop.get("timed_out", False)}
    write_json(result_path, result)
    print(json.dumps({"status": result["status"], "result": str(result_path),
                      "checks": {k: v["status"] for k, v in result.get("checks", {}).items()}}, indent=2))
    return 0 if result["status"] == "passed" else 1


def hidden_desktop():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetThreadDesktop.argtypes = [wintypes.DWORD]
    api.GetThreadDesktop.restype = wintypes.HANDLE
    api.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenInputDesktop.restype = wintypes.HANDLE
    api.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.CloseDesktop.argtypes = [wintypes.HANDLE]
    def name(handle):
        value, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        if not api.GetUserObjectInformationW(handle, 2, value, ctypes.sizeof(value), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value
    current = name(api.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId()))
    handle = api.OpenInputDesktop(0, False, 1)
    try:
        active = name(handle)
    finally:
        api.CloseDesktop(handle)
    assert current.startswith("PyMOLAcceptance_") and current != active, (current, active)
    return {"current": current, "input": active}


class Acceptance:
    def __init__(self, args):
        self.args, self.output = args, args.output.resolve()
        self.planned = planned_checks(args.checks)
        self.result = {"status": "running", "pid": os.getpid(), "checks": {}, "captures": [],
                       "requested_checks": args.checks, "planned_checks": self.planned,
                       "not_requested": [name for name in CHECKS if name not in self.planned],
                       "signal_errors": [], "package_sha256": digest(args.package),
                       "script_sha256": digest(Path(__file__)), "limitations": [
                           "Local fixture only; no remote servers, API generation or scientific cavity calculation.",
                           "Async completion gates test lifecycle boundaries, not calculation accuracy."]}
        self.gates = []
        self.panel = self.window = None

    def persist(self):
        write_json(self.output / "01_result.json", self.result)

    def setup(self):
        self.result["desktop"] = hidden_desktop()
        self.profile = self.output / "04_isolated_profile"
        self.profile.mkdir()
        for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            os.environ[key] = str(self.profile)
        for key, name in (("PYMOLPLUGINS_UI_SETTINGS", "ui.ini"),
                          ("PYMOLPLUGINS_ANNOTATOR_MAPPINGS", "mappings.json"),
                          ("PYMOLPLUGINS_ANNOTATOR_HISTORY", "history.json"),
                          ("PYMOLPLUGINS_STRUCTURE_CACHE_DIR", "cache")):
            os.environ[key] = str(self.profile / name)
        os.environ["QT_QPA_PLATFORM"] = "windows"
        from pymol import cmd, gui, invocation
        from pymol.Qt import QtCore, QtGui, QtWidgets
        from PyQt5.QtTest import QTest
        from pmg_qt import pymol_qt_gui
        self.cmd, self.QtCore, self.QtGui, self.QtWidgets, self.QTest = cmd, QtCore, QtGui, QtWidgets, QTest
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
        for scope in (QtCore.QSettings.UserScope, QtCore.QSettings.SystemScope):
            QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, scope, str(self.profile))
        settings = QtCore.QSettings(os.environ["PYMOLPLUGINS_UI_SETTINGS"], QtCore.QSettings.IniFormat)
        settings.setValue("updates/auto_check", False)
        settings.setValue("updates/auto_install", False)
        settings.setValue("structure_annotator/mode", "floating")
        settings.sync()
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        invocation.parse_args(["pymol", "-k", "-q"])
        self.window = pymol_qt_gui.PyMOLQtGUI()
        pymol_qt_gui.window = self.window
        gui.createlegacypmgapp = self.window.createlegacypmgapp
        self.window.add_lineedit()
        self.window.resize(1180, 820)
        self.window.show()
        self.app.processEvents()
        cmd.set("max_threads", 1)
        sys.excepthook = lambda typ, value, tb: self.result["signal_errors"].append(
            "".join(traceback.format_exception(typ, value, tb)))
        self.message_timer = QtCore.QTimer()
        def close_unexpected_messages():
            for widget in self.app.topLevelWidgets():
                if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible():
                    self.result["signal_errors"].append(
                        f"Unexpected message in {self.result.get('active_check')}: {widget.text()}")
                    widget.reject()
        self.message_timer.timeout.connect(close_unexpected_messages)
        self.message_timer.start(100)
        self.result.update(pymol=cmd.get_version()[0], qt=QtCore.qVersion(), python=sys.executable)

    def wait(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                raise TimeoutError("Expected Qt/native terminal state was not reached")
            time.sleep(0.01)
        self.app.processEvents()

    def click(self, button):
        assert button.isVisible() and button.isEnabled(), button.text()
        self.QTest.mouseClick(button, self.QtCore.Qt.LeftButton)
        self.app.processEvents()

    def capture(self, widget, name, gl=False):
        self.app.processEvents()
        base = widget.grab()
        assert not base.isNull()
        path = self.output / name
        assert base.save(str(path))
        record = {"path": str(path), "kind": "real Qt widget grab", "size": [base.width(), base.height()]}
        if gl:
            view = self.window.pymolwidget
            view._pymolProcess()
            view.repaint()
            self.app.processEvents()
            image = view.grabFrameBuffer()
            assert not image.isNull()
            colors = len({image.pixel(x, y) for x in range(0, image.width(), 4)
                          for y in range(0, image.height(), 4)})
            assert colors > 30, ("Empty OpenGL frame", colors)
            gl_path = path.with_name(path.stem + "_opengl.png")
            assert image.save(str(gl_path))
            record.update(opengl=str(gl_path), sampled_colors=colors)
            painter = self.QtGui.QPainter(base)
            painter.drawImage(self.QtCore.QRect(view.mapTo(widget, self.QtCore.QPoint()), view.size()), image)
            painter.end()
            assert base.save(str(path))
            record["kind"] = "real Qt window plus native OpenGL buffer at actual widget geometry"
        self.result["captures"].append(record)
        return digest(path)

    def installation(self):
        from pymol import plugins
        from pymol.plugins import legacysupport
        from pymol.plugins.installation import installPluginFromFile
        self.install = self.output / "05_plugin_install"
        self.install.mkdir()
        self.window.menudict["PluginQt"] = self.window.menudict["Plugin"]
        plugins.HAVE_QT = True
        plugins.preferences["instantsave"] = False
        plugins.set_startup_path([str(self.install)], autosave=False)
        notices = []
        def unexpected(*args, **kwargs):
            raise AssertionError(("Unexpected installer confirmation", args))
        messages = SimpleNamespace(showinfo=lambda *a, **k: notices.append(a),
                                   askyesno=unexpected, askokcancel=unexpected)
        with patch.object(legacysupport, "tkMessageBox", messages):
            installPluginFromFile(str(self.args.package), parent=self.window, plugdir=str(self.install))
        info = plugins.plugins["pymol_plugins"]
        assert info.loaded
        from pymolplugins.host import plugin, manifest
        loaded = plugin.pp_load()
        assert loaded["status"] == "ok", loaded
        self.host = plugin
        installed = (self.install / "pymol_plugins").resolve()
        for name, module in tuple(sys.modules.items()):
            if name == "pymolplugins" or name.startswith("pymolplugins."):
                if getattr(module, "__file__", None):
                    Path(module.__file__).resolve().relative_to(installed)
        self.menu = self.window.menudict["PluginQt|PymolPlugins"]
        labels = [a.text() for a in self.menu.actions()]
        assert {"Structure Collections…", "Compare or process structures…", "Review structures…"} <= set(labels), labels
        identities = {}
        for name in ("pp_collections", "pp_compare"):
            module, function = manifest.COMMAND_TARGETS[name]
            expected = getattr(importlib.import_module(module), function)
            assert plugin._unwrapped(plugin._registered_callback(name)) is plugin._unwrapped(expected)
            help_result = plugin.pp_help(name)
            assert help_result["matches"], help_result
            identities[name] = {"module": module, "function": function, "help": help_result}
        assert not any(name in self.cmd.keyword for name in (
            "collections_panel", "collections_open", "collections_preview", "collections_capture_view"))
        return {"installed": str(installed), "version": info.get_version(), "menus": labels,
                "commands": identities, "installer_notices": notices}

    def render(self):
        cmd = self.cmd
        self.inputs = self.output / "06_inputs"
        self.inputs.mkdir()
        cmd.fab("ACDEFG", "acceptance_user")
        cmd.alter("acceptance_user", "chain='A'")
        cmd.show_as("sticks", "acceptance_user")
        cmd.color("cyan", "acceptance_user")
        cmd.zoom("acceptance_user", animate=0)
        self.reference = self.inputs / "reference.pdb"
        cmd.save(str(self.reference), "acceptance_user")
        for i in range(3):
            cmd.create("fixture", "acceptance_user")
            cmd.translate([i * 2, 0, 0], "fixture")
            cmd.save(str(self.inputs / f"candidate_{i}.pdb"), "fixture")
            cmd.delete("fixture")
        self.table = self.inputs / "scores.csv"
        with self.table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["structure_file", "reference", "row_id", "score"])
            for i in range(3):
                writer.writerow([f"candidate_{i}.pdb", "reference.pdb", f"row-{i}", i])
        before = self.capture(self.window, "07_host_before.png", gl=True)
        cmd.color("orange", "acceptance_user")
        cmd.turn("y", 35)
        after = self.capture(self.window, "08_host_after.png", gl=True)
        assert before != after
        return {"renderer": cmd.get_renderer(), "frames_changed": True, "ray_jobs": 0}

    def collections(self):
        import numpy as np
        from pymolplugins.collections import core
        from pymolplugins.collections.commands import pp_collections
        from pymolplugins.annotation.panel_style import workspace_style, SCROLLBAR_STYLE
        cmd = self.cmd
        cmd.select("acceptance_selection", "acceptance_user and name CA")
        cmd.load(str(self.reference), "collection_fixture", zoom=0)
        coords = cmd.get_coords("collection_fixture", 1)
        cmd.delete("collection_fixture")
        config = {"scores": str(self.table), "root": str(self.inputs), "count": 1,
                  "metrics": [{"name": "score", "bins": 1}], "target": "chain A", "binder": "chain A"}
        document = {"version": 1, "config": config, "bounds": [coords.min(0).tolist(), coords.max(0).tolist()],
                    "samples": [{"id": "native fixture", "path": str(self.reference), "scores": {"score": 1},
                                 "stratum": [0], "image": "unused.png", "matrix": np.eye(4).tolist(),
                                 "coordinate_signature": core.coordinate_signature(coords)}]}
        path = self.inputs / "01_collection.json"
        core.write_json(path, document)
        panel = pp_collections(str(path))
        self.collection = panel
        assert panel.parentWidget() is self.window
        assert panel.styleSheet() == workspace_style() + SCROLLBAR_STYLE
        assert not panel.findChildren(self.QtWidgets.QTabWidget)
        self.app.processEvents()
        cmd.enable("acceptance_selection")
        enabled, view = set(cmd.get_names("all", enabled_only=1)), cmd.get_view()
        selection = cmd.index("acceptance_selection")
        item = panel.samples.item(0)
        rect = panel.samples.visualItemRect(item)
        self.QTest.mouseClick(panel.samples.viewport(), self.QtCore.Qt.LeftButton, pos=rect.center())
        self.QTest.mouseDClick(panel.samples.viewport(), self.QtCore.Qt.LeftButton, pos=rect.center())
        self.wait(lambda: panel.scene.object_name is not None)
        assert cmd.count_atoms(panel.scene.object_name) == len(coords)
        self.capture(panel, "09_collections.png")
        self.capture(self.window, "10_collection_scene.png", gl=True)
        panel.restore_scene()
        restored = set(cmd.get_names("all", enabled_only=1))
        assert restored == enabled, {"expected_enabled": sorted(enabled), "restored_enabled": sorted(restored)}
        assert cmd.index("acceptance_selection") == selection
        np.testing.assert_allclose(cmd.get_view(), view)
        panel.close()
        return {"native_parent": True, "shared_theme": True, "sample_atoms": len(coords), "restored_scene": True}

    def open_csv(self):
        panel = self.panel
        panel._show_import()
        panel.source_location.set_source(str(self.table))
        panel.source_path_edit.setText(str(self.table))
        panel.path_column_edit.setText("structure_file")
        self.click(panel.source_open_button)
        self.wait(lambda: not panel._csv_task_id and not panel._pending_csv_action and panel._pending_navigation is None)
        assert self.annotation.STATE.dataset is not None, panel.error_label.text()
        assert len(self.annotation.STATE.entries) == 3
        assert self.cmd.count_atoms(self.annotation.STATE.current_object) > 0
        self.wait(lambda: not panel._view_jobs.timer.isActive() and panel._view_jobs.job is None)

    def csv(self):
        from pymolplugins.annotation import plugin
        self.annotation = plugin
        plugin.STATE.preload_enabled = False
        next(a for a in self.menu.actions() if a.text() == "Review structures…").trigger()
        self.panel = plugin._panel
        self.panel.workspace_window.set_mode("floating")
        self.panel.workspace_window.resize(1050, 780)
        self.open_csv()
        self.capture(self.panel, "11_csv_review.png")
        return {"rows": 3, "source": plugin.STATE.csv_path, "current_object": plugin.STATE.current_object}

    def annotation_versions(self):
        panel, owner = self.panel, self.annotation
        state, review = owner.STATE, panel.review
        store, form = state.row_annotation_store, review.form
        entry_id, index, obj = state.current_entry_id(), state.index, state.current_object
        observed = state.loaded_source_versions[obj]
        assert store.versioned and observed.entry_id == entry_id and observed.token
        token = observed.token
        review.selection.select_entry(entry_id)
        review.refresh()
        before = store.annotations_for(entry_id)
        assert entry_id not in state.annotated_entry_ids and index not in state.annotated_indices
        advance, quick = panel.auto_advance_cb.isChecked(), panel.save_on_label_click_cb.isChecked()
        panel.auto_advance_cb.setChecked(False)
        panel.save_on_label_click_cb.setChecked(False)
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        saved, worker_threads, heartbeat = [], [], []
        native_save = owner.save_annotations
        gui_thread = threading.get_ident()

        def gated_save(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            entered.set()
            assert gate.wait(10), "Annotation save test gate expired"
            result = native_save(*args, **kwargs)
            saved.append(result)
            return result

        def binding():
            return (state.current_entry_id(), state.index, state.current_object,
                    state.loaded_source_versions[obj], store._source_versions.get(entry_id))

        expected = (entry_id, index, obj, observed, token)

        def finished():
            assert binding() == expected, "Pending save changed its loaded source binding"
            if not panel._csv_task_id:
                return review.pending is None
            assert panel._csv_task_kind == "annotations" and not form.isEnabled()
            assert not panel.next_btn.isEnabled() and not panel.dataset_table.isEnabled()
            page = panel.main_tabs.currentWidget()
            panel._request_navigation((index + 1) % len(state.entries))
            assert panel._show_batch_processing() is None
            assert panel.main_tabs.currentWidget() is page and panel._pending_navigation is None
            assert binding() == expected and review.targets() == (entry_id,)
            return False

        try:
            # Later is a workflow mark on the entry, not a quality label on source bytes.
            panel.mark_later_action.trigger()
            later = store.history(entry_id, owner.REVIEW_STATUS_LABEL)[-1]
            assert all(not change.get("source_version") for change in later["changes"])
            assert store.get(entry_id, owner.REVIEW_STATUS_LABEL) == owner.REVIEW_STATUS_LATER
            field = form.fields[0]
            assert field["type"] == "single", field
            editor = form.editors[field["name"]]
            form.scroll.ensureWidgetVisible(editor)
            if form.primary_buttons:
                self.click(form.primary_buttons[0])
            else:
                editor.setCurrentIndex(editor.findData(field["values"][0]))
            assert form.can_save(), form.required_missing()
            updates = form.updates()
            with patch.object(owner, "save_annotations", gated_save):
                self.click(form.save_button)
                self.wait(entered.is_set)
                assert not finished()
                self.QtCore.QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.wait(lambda: bool(heartbeat))
                assert not finished() and len(worker_threads) == 1
                assert worker_threads[0] != gui_thread
                gate.set()
                self.wait(finished)
            assert len(saved) == 1 and saved[0]["status"] == "saved", panel.error_label.text()
            sequence = saved[0]["seq"]
            events = [json.loads(line) for line in Path(store.path).read_text(encoding="utf-8").splitlines() if line]
            event, = [event for event in events if event["seq"] == sequence]
            assert event["op"] == "batch" and event["changes"]
            assert all(change["entry_id"] == entry_id and change["source_version"] == token
                       and change["value"] == updates[change["label"]] for change in event["changes"])
            assert entry_id in state.annotated_entry_ids and index in state.annotated_indices
            assert owner._reviewed(form.fields, store.annotations_for(entry_id))
            assert not store.get(entry_id, owner.REVIEW_STATUS_LABEL) and not form.dirty
            assert state.undo_history[-1]["batch_seq"] == sequence
            assert panel.undo_action.isEnabled()
            panel.undo_action.trigger()
            self.wait(lambda: not panel._csv_task_id and review.pending is None)
            assert store.annotations_for(entry_id) == before
            assert entry_id not in state.annotated_entry_ids and index not in state.annotated_indices
            assert not any(item.get("batch_seq") == sequence for item in state.undo_history)
            assert binding() == expected
            return {"entry_id": entry_id, "loaded_source_token": token, "saved_event": event,
                    "pending_navigation_and_batch_blocked": True, "qt_responsive": bool(heartbeat),
                    "completed_then_undone": True, "later_is_entry_level": True}
        finally:
            gate.set()
            self.wait(lambda: not panel._csv_task_id and review.pending is None)
            panel.auto_advance_cb.setChecked(advance)
            panel.save_on_label_click_cb.setChecked(quick)

    def batch_draft(self):
        panel, state = self.panel, self.annotation.STATE
        page = panel._show_batch_processing()
        self.batch = page
        assert page is panel.main_tabs.currentWidget()
        assert not panel.reference_track_button.isVisible()
        assert Path(page.a_edit.text()).resolve() == self.table.resolve()
        page.output.setText(str(self.output / "12_batch_output"))
        page.commands.setPlainText("color cyan, {A}\nshow sticks, {A}")
        page.save_b.setChecked(False)
        page.on_error.setCurrentIndex(page.on_error.findData("continue"))
        before = page.config()
        panel.preload_enabled_cb.setChecked(True)
        panel.preload_ahead_spin.setValue(1)
        panel.preload_interval_spin.setValue(100)
        panel._sync_to_state(persist=False)
        objects = set(state.managed_objects)
        self.annotation._kick_preload()
        self.annotation._process_preload_tick()
        assert set(state.managed_objects) == objects
        panel._back_to_review()
        assert panel.reference_track_button.isVisible()
        self.wait(lambda: set(state.managed_objects) != objects)
        panel._show_batch_processing()
        after = page.config()
        for key in ("output", "commands", "save_b", "on_error"):
            assert getattr(before, key) == getattr(after, key), key
        page.b_edit.setText(str(self.reference))
        page.command_section.set_expanded(True)
        self.capture(panel, "12_batch_draft.png")
        panel._back_to_review()
        return {"draft_preserved": ["output", "commands", "save_b", "on_error"],
                "preload_paused_in_batch": True, "preload_resumed_in_review": True}

    def reload_current_molecule(self):
        names = self.cmd.get_object_list("%" + self.annotation.STATE.current_object)
        assert names
        name = names[0]
        self.cmd.delete(name)
        self.cmd.load(str(self.inputs / "candidate_1.pdb"), name, zoom=0)
        return name

    def identity(self):
        panel, state = self.panel, self.annotation.STATE
        from pymolplugins.annotation.reference_track import validate_reference_track
        state.reference_track = validate_reference_track({"source": "file", "value": str(self.reference),
            "selection": "chain A", "row_selection": "chain A", "direction": "reference_to_row", "mode": "protein"})
        jobs = panel._view_jobs
        jobs.cancel()
        identity = panel._view_identity()
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        original = panel._reference_source
        def delayed(config):
            label, resolve = original(config)
            def compute():
                entered.set()
                assert gate.wait(10), "Reference test gate expired"
                return resolve()
            return label, compute
        with patch.object(panel, "_reference_source", delayed):
            jobs.start(identity, [(state.current_object, panel._reference_track)], title="Reference track")
            self.wait(entered.is_set)
            future = jobs.job[0]
            name = self.reload_current_molecule()
            assert panel._view_identity() != identity and not panel._view_is_current(identity)
            gate.set()
            self.wait(lambda: future.done() and jobs.job is None)
            assert panel._reference_track.name is None
        jobs.start(panel._view_identity(), [(state.current_object, panel._reference_track)], title="Reference track")
        self.wait(lambda: jobs.job is None and not jobs.timer.isActive())
        assert panel._reference_track.name in self.cmd.get_names("objects"), panel.quick_view_status.text()
        panel._reference_track.clear(self.cmd)
        identity = panel._view_identity()
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        drawn = []
        def compute():
            entered.set()
            assert gate.wait(10), "Cavity test gate expired"
            return "complete"
        def draw(value):
            drawn.append(value)
            self.cmd.pseudoatom("acceptance_cavity_boundary")
            return "Boundary draw", ""
        jobs.start(identity, [], title="Cavity lifecycle")
        jobs.start_cavity("Cavity lifecycle", compute, draw)
        self.wait(entered.is_set)
        future = jobs.cavity_job[0]
        self.reload_current_molecule()
        assert not panel._view_is_current(identity)
        gate.set()
        self.wait(lambda: future.done() and jobs.cavity_job is None)
        assert not drawn and "acceptance_cavity_boundary" not in self.cmd.get_names("objects")
        jobs.start(panel._view_identity(), [], title="Cavity lifecycle")
        jobs.start_cavity("Cavity lifecycle", lambda: "current", draw)
        self.wait(lambda: jobs.cavity_job is None)
        assert drawn == ["current"] and self.cmd.count_atoms("acceptance_cavity_boundary") == 1
        self.cmd.delete("acceptance_cavity_boundary")
        state.reference_track = {}
        return {"same_name": name, "reference_old_result_discarded": True,
                "cavity_old_result_discarded": True, "fresh_reference_and_cavity_callbacks": True}

    def appearance(self):
        from pymolplugins.host.show_presets import show_presets
        from pymolplugins.styles import registry
        source = self.cmd.get_object_list("%" + self.annotation.STATE.current_object)[0]
        dialog = show_presets(tab="color", source=source)
        self.appearance_dialog = dialog
        assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["Structure", "Color", "Lighting"]
        page = dialog.color_page
        self.cmd.alter("%" + source, "b=0")
        page.preset.setCurrentIndex(page.preset.findData("alphafold_plddt"))
        self.click(page.apply_button)
        assert page._owned_snapshots, page.status.text()
        colors = []
        self.cmd.iterate("%" + source, "colors.append(color)", space={"colors": colors})
        assert set(colors) == {self.cmd.get_color_index("pp_alphafold_plddt_orange")}, set(colors)
        own = page._owned_snapshots[-1]
        external = registry.pp_style_apply("professional_secondary_structure", "%" + source)
        before = []
        self.cmd.iterate("%" + source, "before.append(color)", space={"before": before})
        undo = next(b for b in page.findChildren(self.QtWidgets.QPushButton) if b.text() == "Undo last style")
        self.click(undo)
        after = []
        self.cmd.iterate("%" + source, "after.append(color)", space={"after": after})
        assert before == after
        assert registry._REGISTRY.snapshots[-1].snapshot_id == external
        assert own in page._owned_snapshots
        registry.pp_style_revert(external)
        self.click(undo)
        assert own not in page._owned_snapshots
        self.capture(dialog, "13_appearance.png")
        dialog.close()
        return {"separate_color_lighting": True, "plddt_zero_is_orange": True,
                "page_undo_preserves_external_top_style": True}

    def ai_mapping(self):
        from pymolplugins.annotation.ai_widgets import show_ai_commands
        from pymolplugins.annotation.mapping_widgets import MappingProfileDialog
        errors = []
        def exercise_modal():
            try:
                self.exercise_ai_dialog()
            except Exception:
                errors.append(traceback.format_exc())
            finally:
                self.panel.ai_dialog.reject()
        self.QtCore.QTimer.singleShot(150, exercise_modal)
        show_ai_commands(self.panel, preset=True)
        assert not errors, "\n".join(errors)
        self.exercise_mapping_dialog(MappingProfileDialog)
        return {"preset_first": True, "collapsed_editable_drafts": True, "AI_requests": 0,
                "rewrite_and_root_order": True, "mapping_saved_in_isolated_profile": True}

    def exercise_ai_dialog(self):
        dialog = self.panel.ai_dialog
        assert not dialog.generation_controls.isVisible() and dialog.generation_toggle.isVisible()
        assert dialog.triggers.count() >= 2
        for i in range(2):
            dialog.triggers.item(i).setCheckState(self.QtCore.Qt.Checked)
        self.click(dialog.preset_button)
        assert len(dialog.preview_sections) == 2, dialog.status.text()
        sections = list(dialog.preview_sections.values())
        assert sections[0]["toggle"].isChecked() and not sections[1]["toggle"].isChecked()
        self.click(sections[1]["toggle"])
        editor = sections[1]["editor"]
        assert editor.isVisible()
        original = editor.toPlainText()
        editor.setFocus()
        self.QTest.keyClick(editor, self.QtCore.Qt.Key_End, self.QtCore.Qt.ControlModifier)
        self.QTest.keyClick(editor, self.QtCore.Qt.Key_Return)
        self.QTest.keyClicks(editor, "# acceptance draft")
        assert editor.toPlainText() != original
        self.click(dialog.generation_toggle)
        assert dialog.generation_controls.isVisible()
        self.click(dialog.generation_toggle)
        assert dialog.process is None
        self.capture(dialog, "14_presets_drafts.png")

    def exercise_mapping_dialog(self, MappingProfileDialog):
        mapping = MappingProfileDialog(self.panel)
        self.mapping_dialog = mapping
        mapping.show()
        self.click(mapping.new_button)
        mapping.name_edit.setText("acceptance-only")
        mapping.rewrite_section["toggle"].setChecked(True)
        add_rewrite = next(b for b in mapping.rewrite_section["content"].findChildren(self.QtWidgets.QPushButton)
                           if b.text() == "Add rewrite")
        self.click(add_rewrite)
        self.click(add_rewrite)
        mapping.rewrites.item(0, 1).setText("first/")
        mapping.rewrites.item(1, 1).setText("second/")
        mapping.rewrites.selectRow(1)
        up = next(b for b in mapping.rewrite_section["content"].findChildren(self.QtWidgets.QPushButton)
                  if b.text() == "Move up")
        self.click(up)
        assert [v["pattern"] for v in mapping._rewrite_values()] == ["second/", "first/"]
        mapping.root_section["toggle"].setChecked(True)
        add_root = next(b for b in mapping.root_section["content"].findChildren(self.QtWidgets.QPushButton)
                       if b.text() == "Add root")
        mapping.profile_scroll.ensureWidgetVisible(add_root)
        self.click(add_root)
        self.click(add_root)
        for i, prefix in enumerate(("/first", "/second")):
            mapping.roots.item(i, 0).setText(prefix)
            mapping.roots.item(i, 1).setText(str(self.inputs))
        mapping.roots.selectRow(1)
        up = next(b for b in mapping.root_section["content"].findChildren(self.QtWidgets.QPushButton)
                  if b.text() == "Move up")
        mapping.profile_scroll.ensureWidgetVisible(up)
        self.click(up)
        assert [v["source_prefix"] for v in mapping._root_values()] == ["/second", "/first"]
        self.capture(mapping, "15_mapping_order.png")
        self.click(mapping.save_button)
        assert mapping.saved_name == "acceptance-only", mapping.error_label.text()

    def batch_review(self):
        panel = self.panel
        page = panel._show_batch_processing()
        page.workflow.setCurrentIndex(page.workflow.findData("review"))
        page.b_edit.setText(str(self.reference))
        ready = []
        page.review_ready.connect(ready.append)
        self.click(page.preview_button)
        self.wait(lambda: page.future is None)
        assert page.plan is not None and page.run_button.isEnabled(), page.error_box.toPlainText()
        folder = self.output / "16_created_review"
        folder.mkdir()
        with patch.object(self.QtWidgets.QFileDialog, "getExistingDirectory", return_value=str(folder)):
            self.click(page.run_button)
        self.wait(lambda: bool(ready) and not panel._csv_task_id and not panel._pending_csv_action
                  and panel._pending_navigation is None)
        assert Path(ready[0]).is_file()
        assert Path(self.annotation.STATE.csv_path).resolve() == Path(ready[0]).resolve()
        assert panel.main_tabs.currentIndex() == 0 and self.cmd.count_atoms(self.annotation.STATE.current_object) > 0
        self.capture(panel, "16_review_ready.png")
        self.capture(self.window, "17_review_scene.png", gl=True)
        return {"review_ready": str(ready[0]), "opened_in_review": True}

    def lifecycle(self):
        self.open_csv()
        panel, state = self.panel, self.annotation.STATE
        page = panel._show_batch_processing()
        page.workflow.setCurrentIndex(page.workflow.findData("process"))
        page.b_edit.setText(str(self.reference))
        page.output.setText(str(self.output / "18_cancelled_batch"))
        page.commands.setPlainText("color cyan, {A}\nshow sticks, {A}")
        self.click(page.preview_button)
        self.wait(lambda: page.future is None)
        assert page.plan is not None, page.error_box.toPlainText()
        page.run_timer.setInterval(100)
        self.click(page.run_button)
        assert page.busy and page.runner.running
        old_index, owner = state.index, state.current_object
        panel.main_tabs.setCurrentIndex(0)
        assert panel.main_tabs.currentWidget() is page
        panel._request_navigation((old_index + 1) % 3)
        assert state.index == old_index and state.current_object == owner
        assert not panel.back_button.isEnabled() and not panel.settings_button.isEnabled()
        self.wait(lambda: bool(page.runner.owned))
        self.capture(panel, "18_busy_batch.png")
        panel.close()
        assert not panel._closed and panel._batch_close_pending and page.runner.cancel_requested
        self.wait(lambda: panel._closed and not page.runner.running)
        assert not page.runner.owned
        assert not page.run_timer.isActive() and not page.prepare_timer.isActive()
        assert self.cmd.count_atoms("acceptance_user") > 0
        return {"busy_navigation_blocked": True, "close_waited_for_owned_scene_cleanup": True,
                "batch_status": page.runner.status, "user_object_preserved": True}

    def run(self):
        self.persist()
        try:
            self.setup()
            for name in self.planned:
                required = dependencies(name)
                missing = [key for key in required if self.result["checks"].get(key, {}).get("status") != "passed"]
                if missing:
                    self.result["checks"][name] = {"status": "skipped", "dependencies": missing}
                    continue
                count = len(self.result["signal_errors"])
                self.result["active_check"] = name
                self.persist()
                try:
                    evidence = getattr(self, name)()
                    assert len(self.result["signal_errors"]) == count, self.result["signal_errors"][count:]
                    self.result["checks"][name] = {"status": "passed", "evidence": evidence}
                except Exception:
                    self.result["checks"][name] = {"status": "failed", "traceback": traceback.format_exc()}
                    for gate in self.gates:
                        gate.set()
                    for attr in ("collection", "appearance_dialog", "mapping_dialog"):
                        dialog = getattr(self, attr, None)
                        if dialog is not None:
                            dialog.hide()
                    if self.panel is not None and not self.panel._closed:
                        self.panel._view_jobs.cancel()
                        if not self.panel._batch_is_busy():
                            self.panel._back_to_review()
                self.result.pop("active_check", None)
                self.persist()
        except Exception:
            self.result["bootstrap_error"] = traceback.format_exc()
        finally:
            for gate in self.gates:
                gate.set()
            try:
                if self.panel is not None and not self.panel._closed:
                    self.panel.close()
                    self.wait(lambda: self.panel._closed)
                collection = getattr(self, "collection", None)
                if collection is not None:
                    collection.task.executor.shutdown(wait=True)
                if hasattr(self, "annotation"):
                    self.annotation._csv_async_pool().waitForDone(10000)
                    if self.annotation._bg_executor is not None:
                        self.annotation._bg_executor.shutdown(wait=True)
                    from pymolplugins.annotation.persistence import flush_writes
                    flush_writes()
            except Exception:
                self.result["cleanup_error"] = traceback.format_exc()
            passed = (len(self.result["checks"]) == len(self.planned)
                      and all(row["status"] == "passed" for row in self.result["checks"].values())
                      and not self.result["signal_errors"] and "cleanup_error" not in self.result
                      and "bootstrap_error" not in self.result)
            self.result["status"] = "passed" if passed else "failed"
            self.persist()
            if hasattr(self, "cmd"):
                self.cmd.quit(0 if passed else 1)
        return 0 if self.result["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--launcher-wheel", type=Path,
                        help="Task-local pywin32 wheel matching the PyMOL Python; never installed globally")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--package", type=Path, help="Reuse this frozen ZIP instead of rebuilding the candidate")
    parser.add_argument("--checks", nargs="+", choices=CHECKS, default=list(CHECKS),
                        help="Run these checks and their setup dependencies; default: the full contract")
    args = parser.parse_args()
    assert os.name == "nt", "This acceptance requires Windows PyMOL"
    return Acceptance(args).run() if args.child else launch(args)


if __name__ == "__main__":
    raise SystemExit(main())
