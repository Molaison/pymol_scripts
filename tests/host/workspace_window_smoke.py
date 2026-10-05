"""Exercise docked/floating workspaces in the actual Windows PyMOL desktop."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_layout_smoke import click, menu_action, visible
from annotator_workflow_smoke import fixture, wait_for
from batch_processing_smoke import configure, fixtures, output_error, prepare, visible_table
from workspace_helpers import isolate_workspace_settings


def switch(content, mode):
    from PyQt5.QtTest import QTest
    button = content.window_mode_button
    menu_action(button, button.actions_by_mode[mode])
    QTest.qWait(100)
    assert content.workspace_window.mode == mode
    assert [key for key, action in button.actions_by_mode.items() if action.isChecked()] == [mode]


def native_float_button(host):
    from pymol.Qt import QtWidgets
    from PyQt5.QtTest import QTest
    button = host.findChild(QtWidgets.QAbstractButton, "qt_dockwidget_floatbutton")
    assert button is not None, "Native dock float control is missing"
    click(button)
    QTest.qWait(100)


def snapshot(window, output, name):
    path = output.with_name(output.stem + "-" + name + ".png")
    if hasattr(window, "pymolwidget"):
        window.pymolwidget.repaint()
    assert window.screen().grabWindow(int(window.winId())).save(str(path)), str(path)


def check_geometry(window, panel, batch, output):
    from pymol import cmd
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    geometry = {}
    cmd.show("spheres", "user_structure")
    cmd.set("sphere_scale", 0.45, "user_structure")
    cmd.zoom("user_structure", buffer=8)
    for width, height in ((1440, 900), (1280, 720)):
        window.resize(width, height)
        window.move(10, 10)
        panel.workspace_window.present()
        QTest.qWait(150)
        assert (window.width(), window.height()) == (width, height), (
            "Embedded minimum sizes enlarged PyMOL", window.size(), window.minimumSizeHint())
        assert window.centralWidget() is window.pymolwidget
        assert window.pymolwidget.width() >= 320 and window.pymolwidget.height() >= 180, (
            window.pymolwidget.size(), panel.size(), batch.size())
        visible(window.pymolwidget)
        visible(panel.window_mode_button)
        visible(panel.review.form.save_button)
        visible(panel.next_btn)
        visible_table(panel.dataset_table)
        snapshot(window, output, "annotation-%d" % width)
        batch.workspace_window.present()
        QTest.qWait(100)
        for button in (batch.window_mode_button, batch.preview_button, batch.run_button, batch.stop_button):
            visible(button)
        visible_table(batch.table)
        snapshot(window, output, "batch-%d" % width)
        geometry[str(width)] = dict(main=[window.width(), window.height()],
                                   viewport=[window.pymolwidget.width(), window.pymolwidget.height()],
                                   annotation=[panel.width(), panel.height()], batch=[batch.width(), batch.height()])
    cmd.show("spheres", "user_structure")
    cmd.zoom("user_structure", buffer=8)
    QTest.qWait(150)
    window.pymolwidget.repaint()
    before = window.pymolwidget.grabFrameBuffer().copy()
    assert before.save(str(output.with_name(output.stem + "-viewport-before.png")))
    colors = {before.pixel(x, y) for x in range(0, before.width(), max(1, before.width() // 80))
              for y in range(0, before.height(), max(1, before.height() // 80))}
    assert len(colors) > 8, "The embedded workspace left a blank viewport"
    view_before = cmd.get_view()
    cmd.turn("y", 25)
    QTest.qWait(150)
    window.pymolwidget.repaint()
    after = window.pymolwidget.grabFrameBuffer()
    assert after.save(str(output.with_name(output.stem + "-viewport.png")))
    changed = sum(before.pixel(x, y) != after.pixel(x, y)
                  for x in range(0, before.width(), max(1, before.width() // 80))
                  for y in range(0, before.height(), max(1, before.height() // 80)))
    assert changed > 8, dict(changed_samples=changed, colors=len(colors),
                             view_before=view_before, view_after=cmd.get_view(),
                             timer_active=window.pymolwidget._timer.isActive(),
                             enabled_objects=cmd.get_names("objects", enabled_only=1))
    geometry.update(viewport_colors=len(colors), changed_viewport_samples=changed,
                    framebuffer_capture="explicit native repaint")
    window.resize(1440, 900)
    QTest.qWait(100)
    return geometry


def check_annotation(annotation, panel, app, window, output):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    host, form = panel.workspace_window, panel.review.form
    entry_id = annotation.STATE.current_entry_id()
    click(panel._value_buttons[0])
    state = (dict(form.values), set(form.dirty), annotation.STATE.index,
             annotation.STATE.current_object, panel.review.targets())
    native_float_button(host)
    assert host.mode == "floating" and panel.window_mode_button.actions_by_mode["floating"].isChecked()
    host.resize(930, 690)
    host.move(180, 120)
    QTest.qWait(100)
    size, position = host.size(), host.pos()
    snapshot(host, output, "annotation-floating")
    switch(panel, "docked")
    assert host.mode == "docked" and panel.window_mode_button.actions_by_mode["docked"].isChecked()
    switch(panel, "floating")
    assert host.size() == size and host.pos() == position, (host.size(), size, host.pos(), position)
    switch(panel, "docked")
    assert annotation.show_structure_annotator_panel() is panel
    assert panel.review.form is form
    assert state == (dict(form.values), set(form.dirty), annotation.STATE.index,
                     annotation.STATE.current_object, panel.review.targets())
    entered, release = threading.Event(), threading.Event()
    save = annotation.save_annotations
    def held_save(**kwargs):
        entered.set()
        if not release.wait(20):
            raise TimeoutError("Annotation close-protection fixture timed out")
        return save(**kwargs)
    with patch.object(annotation, "save_annotations", side_effect=held_save):
        try:
            click(form.save_button)
            wait_for(app, entered.is_set)
            task = panel._csv_task_id
            switch(panel, "floating")
            assert task == panel._csv_task_id and not panel._closed
            assert host.close() is False and not panel._closed
            assert "Saving annotations" in panel.status_label.text()
            switch(panel, "docked")
        finally:
            release.set()
        wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert annotation.STATE.row_annotation_store.get(entry_id, "quality") == "Good"
    panel.auto_advance_cb.setChecked(True)
    window.activateWindow()
    window.pymolwidget.setFocus()
    QTest.qWait(80)
    QTest.keyClick(window.pymolwidget, QtCore.Qt.Key_2, QtCore.Qt.AltModifier)
    wait_for(app, lambda: not panel._csv_task_id and annotation.STATE.index == 1)
    assert annotation.STATE.row_annotation_store.get(entry_id, "quality") == "Bad"
    return dict(drafts_and_identity="passed", native_and_menu_switches="passed",
                saving_switch_and_close_veto="passed", docked_viewport_shortcuts="passed",
                floating_geometry_restored="passed")


def check_batch(batch_widgets, dialog, app, root, all_files, panel):
    from PyQt5.QtTest import QTest
    configure(dialog, all_files, all_files / "ref.pdb", root / "batch-output")
    entered, release = threading.Event(), threading.Event()
    prepare_batch = batch_widgets.prepare_batch
    def held_prepare(*args, **kwargs):
        entered.set()
        if not release.wait(20):
            raise TimeoutError("Batch preparation fixture timed out")
        return prepare_batch(*args, **kwargs)
    with patch.object(batch_widgets, "prepare_batch", side_effect=held_prepare):
        try:
            click(dialog.preview_button)
            wait_for(app, entered.is_set)
            future = dialog.future
            switch(dialog, "floating")
            switch(dialog, "docked")
            assert dialog.future is future and not dialog.cancel_event.is_set()
        finally:
            release.set()
        wait_for(app, lambda: dialog.future is None)
    assert dialog.plan is not None and dialog.run_button.isEnabled(), dialog.error_box.toPlainText()
    plan = dialog.plan
    panel.workspace_window.present()
    QTest.qWait(80)
    assert batch_widgets.show_batch_dialog() is dialog and dialog.plan is plan
    dialog.run_timer.setInterval(180)
    click(dialog.run_button)
    runner = dialog.runner
    assert runner.running
    switch(dialog, "floating")
    switch(dialog, "docked")
    assert dialog.runner is runner and runner.running
    wait_for(app, lambda: not runner.running)
    report = runner.report()
    assert report["status"] == "completed", report
    from pymol import cmd
    error = output_error(report, cmd)
    assert len(report["results"]) == 3 and sum(len(row["outputs"]) for row in report["results"]) == 6
    configure(dialog, all_files, all_files / "ref.pdb", root / "cancel-output")
    prepare(dialog, app, ["1", "2", "3"])
    click(dialog.run_button)
    host = dialog.workspace_window
    assert host.close() is False and not dialog._closed
    wait_for(app, lambda: not dialog.runner.running)
    assert dialog.runner.status == "cancelled"
    return dict(preparing_switch="passed", preview_and_singleton="passed", running_switch="passed",
                running_close_veto="passed", pairs=3, saved_outputs=6, max_coordinate_error=error)


def check_reopen(annotation, batch_widgets, panel, batch, app, window):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    switch(batch, "floating")
    host = batch.workspace_window
    host.resize(960, 700)
    host.move(145, 105)
    QTest.qWait(100)
    size, position = host.size(), host.pos()
    assert batch.close() and batch._closed and batch_widgets._DIALOG is None
    assert batch.workspace_window is None
    QTest.qWait(80)
    reopened = batch_widgets.show_batch_dialog()
    QTest.qWait(100)
    assert reopened is not batch and reopened.workspace_window.mode == "floating"
    assert reopened.workspace_window.size() == size and reopened.workspace_window.pos() == position, dict(
        expected_size=[size.width(), size.height()], expected_position=[position.x(), position.y()],
        actual_geometry=reopened.workspace_window.geometry().getRect(),
        actual_position=[reopened.workspace_window.x(), reopened.workspace_window.y()],
        minimum=reopened.workspace_window.minimumSize().width(), content=reopened.size())
    reopened.workspace_window.move(-10000, -10000)
    QTest.qWait(80)
    reopened.close()
    QTest.qWait(80)
    reopened = batch_widgets.show_batch_dialog()
    QTest.qWait(100)
    assert any(screen.availableGeometry().contains(reopened.workspace_window.frameGeometry())
               for screen in app.screens()), reopened.workspace_window.frameGeometry()
    panel.workspace_window.present()
    window.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel.workspace_window, QtCore.Qt.Horizontal)
    window.resizeDocks([panel.workspace_window], [720], QtCore.Qt.Horizontal)
    QTest.qWait(100)
    dock_width = panel.workspace_window.width()
    close_button = panel.workspace_window.findChild(QtWidgets.QAbstractButton, "qt_dockwidget_closebutton")
    assert close_button is not None
    click(close_button)
    assert panel._closed and annotation._panel is None
    QTest.qWait(80)
    second = annotation.show_structure_annotator_panel()
    QTest.qWait(100)
    assert second is not panel and second.workspace_window.mode == "docked"
    assert window.dockWidgetArea(second.workspace_window) == QtCore.Qt.RightDockWidgetArea
    assert abs(second.workspace_window.width() - dock_width) <= 2, (second.workspace_window.width(), dock_width)
    reopened.close()
    second.close()
    app.processEvents()
    from pymolplugins.host.workspace import PanelWindow
    assert not [dock for dock in window.findChildren(PanelWindow) if not dock._closed]
    return dict(mode_and_float_geometry="passed", dock_side_and_width="passed",
                offscreen_geometry_recovered="passed", no_duplicate_hosts="passed")


def check_settings_errors(annotation, batch_widgets, window, root):
    from pymol import gui
    from pymol.Qt import QtCore, QtWidgets
    from pymolplugins.host.workspace import PanelWindow, window_settings
    with patch.object(gui, "get_qtwindow", return_value=None):
        try:
            batch_widgets.show_batch_dialog()
        except RuntimeError as exc:
            assert "main window" in str(exc)
        else:
            raise AssertionError("Missing desktop silently opened a floating window")
    assert batch_widgets._DIALOG is None
    settings = window_settings()
    settings.setValue("structure_annotator/mode", "invalid")
    settings.sync()
    try:
        annotation.show_structure_annotator_panel()
    except ValueError as exc:
        assert "Invalid workspace mode" in str(exc)
    else:
        raise AssertionError("Invalid saved mode was silently replaced")
    assert annotation._panel is None
    assert settings.value("structure_annotator/mode") == "invalid"
    settings.setValue("structure_annotator/mode", "docked")
    settings.sync()
    dialog = batch_widgets.show_batch_dialog()
    host = dialog.workspace_window
    real_settings = host.settings
    bad_settings = QtCore.QSettings(str(root), QtCore.QSettings.IniFormat)
    try:
        host.settings = bad_settings
        with patch.object(QtWidgets.QMessageBox, "critical") as critical:
            host._persist()
            critical.assert_called_once()
            assert "Cannot read or save window settings" in critical.call_args.args[2]
    finally:
        host.settings = real_settings
        dialog.close()
    assert not [dock for dock in window.findChildren(PanelWindow) if not dock._closed]
    return dict(missing_main_window_is_explicit="passed", invalid_preference_preserved="passed",
                settings_write_error_visible="passed")


def viewport_probe(window, output):
    from pymol import cmd
    from PyQt5.QtTest import QTest
    viewport = window.pymolwidget
    cmd.show("spheres", "user_structure")
    cmd.zoom("user_structure")
    QTest.qWait(300)
    before = viewport.grabFrameBuffer().copy()
    cmd.turn("y", 25)
    QTest.qWait(300)
    automatic = viewport.grabFrameBuffer().copy()
    viewport.repaint()
    QTest.qWait(100)
    painted = viewport.grabFrameBuffer().copy()
    def changed(image):
        return sum(before.pixel(x, y) != image.pixel(x, y)
                   for x in range(0, before.width(), max(1, before.width() // 80))
                   for y in range(0, before.height(), max(1, before.height() // 80)))
    for name, image in (("before", before), ("automatic", automatic), ("repaint", painted)):
        assert image.save(str(output.with_name(output.stem + "-" + name + ".png")))
    return dict(status="passed", diagnostic_only=True, without_plugin_docks=True,
                automatic_changed=changed(automatic), repaint_changed=changed(painted),
                updates_enabled=viewport.updatesEnabled(), timer_active=viewport._timer.isActive(),
                visible=viewport.isVisible(), defer_updates=cmd.get_setting_int("defer_updates"))


def run(output, viewport_only=False):
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, gui, invocation
    from pymol.Qt import QtCore, QtWidgets
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    original_style = app.styleSheet(), app.font().toString()
    original_viewport_minimum = window.pymolwidget.minimumSize()
    original_corners = {corner: window.corner(corner)
                        for corner in (QtCore.Qt.BottomLeftCorner, QtCore.Qt.BottomRightCorner)}
    native_docks = window.findChildren(QtWidgets.QDockWidget)
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-window-modes-") as temporary:
        root = Path(temporary)
        os.environ.update(PYMOLPLUGINS_ANNOTATOR_HISTORY=str(root / "history.json"),
                          PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(root / "mappings.json"),
                          PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(root / "cache"))
        isolate_workspace_settings(root, window, mode=None)
        all_files, pairs, table = fixtures(root)
        if viewport_only:
            cmd.load(str(all_files / "ref.pdb"), "user_structure")
            return viewport_probe(window, output)
        csv_path = fixture(root, 8, 8)
        sources = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in [*all_files.iterdir(), *pairs.iterdir(), table, csv_path]}
        from pymolplugins.host.plugin import pp_load
        from pymolplugins.targets import batch_widgets
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        assert pp_load()["status"] == "ok"
        annotation.STATE = annotation.AnnotatorState()
        annotation.STATE.preload_enabled = False
        annotation.STATE.auto_advance = False
        annotation.STATE.annotation_fields = [
            dict(name="quality", type="single", required=True, values=["Good", "Bad"])]
        assert annotation.sa_load_csv(str(csv_path), path_column="structure_file", id_column="row_id")
        cmd.load(str(all_files / "ref.pdb"), "user_structure")
        cmd.show("spheres", "user_structure")
        cmd.color("teal", "user_structure")
        panel = annotation.show_structure_annotator_panel()
        app.processEvents()
        try:
            assert panel.workspace_window.mode == "docked"
            assert window.dockWidgetArea(panel.workspace_window) == QtCore.Qt.LeftDockWidgetArea
            annotations = check_annotation(annotation, panel, app, window, output)
            batch = batch_widgets.show_batch_dialog()
            app.processEvents()
            assert panel.workspace_window in window.tabifiedDockWidgets(batch.workspace_window)
            assert panel.styleSheet() == batch.styleSheet()
            geometry = check_geometry(window, panel, batch, output)
            batch.workspace_window.present()
            processing = check_batch(batch_widgets, batch, app, root, all_files, panel)
            reopen = check_reopen(annotation, batch_widgets, panel, batch, app, window)
            settings_errors = check_settings_errors(annotation, batch_widgets, window, root)
            assert original_style == (app.styleSheet(), app.font().toString())
            assert all(dock.parentWidget() is window for dock in native_docks)
            assert window.centralWidget() is window.pymolwidget
            assert window.pymolwidget.minimumSize() == original_viewport_minimum
            assert original_corners == {corner: window.corner(corner) for corner in original_corners}
            assert sources == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
            return dict(status="passed", pymol=cmd.get_version()[0], annotations=annotations,
                        batch=processing, geometry=geometry, reopen=reopen,
                        settings_errors=settings_errors,
                        native_layout_and_style_preserved="passed", unchanged_sources=len(sources))
        except Exception:
            snapshot(window, output, "failed")
            raise
        finally:
            if batch_widgets._DIALOG is not None:
                batch = batch_widgets._DIALOG
                batch.stop()
                wait_for(app, lambda: batch.runner is None or not batch.runner.running)
                batch.close()
            if annotation._panel is not None:
                wait_for(app, lambda: not annotation._panel._csv_task_id)
                annotation._panel.close()
            assert annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._reset_bg_cache()
            annotation._delete_managed_objects(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--viewport-probe", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.output, args.viewport_probe)
    except Exception:
        result = dict(status="failed", traceback=traceback.format_exc())
    result["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
