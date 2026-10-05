"""Check Structure Annotator footer placement in maximized Windows PyMOL."""

import argparse
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_layout_smoke import click, menu_action, visible
from annotator_workflow_smoke import fixture, wait_for
from workspace_helpers import isolate_workspace_settings, resize_workspace


def rect_values(rect):
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def native_rect_to_list(rect):
    return [int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)]


def native_taskbar_info(window):
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    class APPBARDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uCallbackMessage", wintypes.UINT),
            ("uEdge", wintypes.UINT),
            ("rc", wintypes.RECT),
            ("lParam", wintypes.LPARAM),
        ]

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
    shell32.SHAppBarMessage.restype = ctypes.c_size_t
    monitor_default_to_nearest = 2
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    appbar = APPBARDATA()
    appbar.cbSize = ctypes.sizeof(APPBARDATA)
    state = shell32.SHAppBarMessage(4, ctypes.byref(appbar))
    info = {
        "available": True,
        "auto_hide": bool(state & 1),
        "always_on_top": bool(state & 2),
        "appbar_state": int(state),
        "shell_tray_hwnd": int(tray or 0),
    }
    if not tray:
        return info
    rect = wintypes.RECT()
    assert user32.GetWindowRect(tray, ctypes.byref(rect)), "GetWindowRect(Shell_TrayWnd) failed"
    monitor = user32.MonitorFromWindow(tray, monitor_default_to_nearest)
    window_monitor = user32.MonitorFromWindow(wintypes.HWND(int(window.winId())), monitor_default_to_nearest)
    monitor_info = MONITORINFO()
    monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
    assert user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)), "GetMonitorInfoW(taskbar) failed"
    info.update({
        "visible": bool(user32.IsWindowVisible(tray)),
        "native_rect": native_rect_to_list(rect),
        "native_monitor": native_rect_to_list(monitor_info.rcMonitor),
        "native_work": native_rect_to_list(monitor_info.rcWork),
        "on_window_monitor": int(monitor) == int(window_monitor),
    })
    if not info["on_window_monitor"]:
        return info
    qt_screen = window.screen()
    qt_monitor = qt_screen.geometry()
    native_monitor = monitor_info.rcMonitor
    scale_x = (native_monitor.right - native_monitor.left) / qt_monitor.width()
    scale_y = (native_monitor.bottom - native_monitor.top) / qt_monitor.height()

    def to_qt(value, native_origin, qt_origin, scale):
        return round(qt_origin + (value - native_origin) / scale)

    qt_left = to_qt(rect.left, native_monitor.left, qt_monitor.x(), scale_x)
    qt_top = to_qt(rect.top, native_monitor.top, qt_monitor.y(), scale_y)
    qt_right = to_qt(rect.right, native_monitor.left, qt_monitor.x(), scale_x)
    qt_bottom = to_qt(rect.bottom, native_monitor.top, qt_monitor.y(), scale_y)
    info.update({
        "qt_rect": [qt_left, qt_top, qt_right - qt_left, qt_bottom - qt_top],
        "qt_monitor": rect_values(qt_monitor),
        "dpi_scale": [scale_x, scale_y],
        "qt_dpr": qt_screen.devicePixelRatio(),
    })
    return info


def widget_rect(widget):
    from pymol.Qt import QtCore

    return QtCore.QRect(widget.mapToGlobal(QtCore.QPoint(0, 0)), widget.size())


def widget_snapshot(widget, available, taskbar_rect=None):
    from pymol.Qt import QtCore

    global_rect = widget_rect(widget)
    local_rect = QtCore.QRect(widget.mapTo(widget.window(), QtCore.QPoint(0, 0)), widget.size())
    data = {
        "global": rect_values(global_rect),
        "local": rect_values(local_rect),
        "visible": widget.isVisible(),
        "enabled": widget.isEnabled(),
        "text": widget.text(),
        "ancestor_contains": widget.visibleRegion().boundingRect().contains(widget.rect()),
        "window_contains": widget.window().rect().contains(local_rect),
        "available_contains": available.contains(global_rect),
        "available_bottom_gap": available.bottom() - global_rect.bottom(),
    }
    if taskbar_rect is not None:
        data["taskbar_intersects"] = global_rect.intersects(taskbar_rect)
    return data


def field_bottom(panel):
    widgets = list(panel._value_buttons)
    form = panel.review.form
    if form.optional_toggle.isVisible():
        widgets.append(form.optional_toggle)
    widgets.extend(editor for editor in form.editors.values() if editor.isVisible())
    return max(widget_rect(widget).bottom() for widget in widgets if widget.isVisible())


def collect_geometry(window, panel=None):
    from pymol.Qt import QtCore

    screen = window.screen()
    available = screen.availableGeometry()
    taskbar = native_taskbar_info(window)
    taskbar_rect = None
    if taskbar.get("visible") and taskbar.get("qt_rect"):
        taskbar_rect = QtCore.QRect(*taskbar["qt_rect"])
    data = {
        "screen": {
            "name": screen.name(),
            "geometry": rect_values(screen.geometry()),
            "available": rect_values(available),
            "device_pixel_ratio": screen.devicePixelRatio(),
        },
        "taskbar": taskbar,
        "window": {
            "client": rect_values(window.geometry()),
            "frame": rect_values(window.frameGeometry()),
            "normal": rect_values(window.normalGeometry()),
            "rect": rect_values(window.rect()),
            "maximized": window.isMaximized(),
            "fullscreen": window.isFullScreen(),
        },
    }
    if panel is not None:
        form = panel.review.form
        save = widget_snapshot(form.save_button, available, taskbar_rect)
        hint = widget_snapshot(form.hint_label, available, taskbar_rect)
        footer = QtCore.QRect(widget_rect(form.hint_label)).united(widget_rect(form.save_button))
        data["host"] = {
            "mode": panel.workspace_window.mode,
            "client": rect_values(panel.workspace_window.geometry()),
            "frame": rect_values(panel.workspace_window.frameGeometry()),
            "content": rect_values(panel.geometry()),
        }
        data["footer"] = {
            "hint_label": hint,
            "save_button": save,
            "status_label": widget_snapshot(panel.status_label, available, taskbar_rect) if panel.status_label.isVisible() else None,
            "union": rect_values(footer),
            "field_bottom": field_bottom(panel),
            "save_gap_from_fields": save["global"][1] - field_bottom(panel),
        }
    return data


def assert_footer_unobscured(geometry):
    footer = geometry["footer"]
    for name in ("save_button", "hint_label"):
        item = footer[name]
        assert item["visible"], f"Hidden footer widget: {name}"
        assert item["ancestor_contains"], f"Footer widget clipped by ancestor: {name}"
        assert item["window_contains"], f"Footer widget outside window: {name} {item['local']}"
        assert item["available_contains"], f"Footer widget outside available screen: {name} {item['global']}"
        assert not item.get("taskbar_intersects", False), f"Footer widget intersects visible taskbar: {name} {item['global']}"
    if footer["status_label"] is not None:
        assert not footer["status_label"].get("taskbar_intersects", False), footer["status_label"]
    if geometry["window"]["maximized"]:
        assert 0 <= footer["save_gap_from_fields"] <= 96, footer


def capture_window(window, output, name):
    path = output.with_name(output.stem + "-" + name + ".png")
    if hasattr(window, "pymolwidget"):
        window.pymolwidget.repaint()
    screen = window.screen()
    assert screen.grabWindow(int(window.winId())).save(str(path)), str(path)
    return str(path)


def apply_window_state(app, window, output, result, name, maximized, panel=None):
    from PyQt5.QtTest import QTest

    if maximized:
        window.showMaximized()
    else:
        window.showNormal()
    window.raise_()
    window.activateWindow()
    error = None
    try:
        wait_for(app, lambda: window.isVisible() and window.isMaximized() == maximized, timeout=8)
        QTest.qWait(250)
        app.processEvents()
    except Exception as exc:
        error = exc
    geometry = collect_geometry(window, panel)
    geometry["screenshot"] = capture_window(window, output, name)
    result.setdefault("geometry", {})[name] = geometry
    if error is not None:
        raise error
    if panel is not None:
            assert_footer_unobscured(geometry)
    return geometry


def switch(content, mode):
    from PyQt5.QtTest import QTest

    button = content.window_mode_button
    menu_action(button, button.actions_by_mode[mode])
    QTest.qWait(100)
    assert content.workspace_window.mode == mode
    assert [key for key, action in button.actions_by_mode.items() if action.isChecked()] == [mode]


def capture_workspace(panel, output, result, name):
    geometry = collect_geometry(panel.workspace_window, panel)
    geometry["screenshot"] = capture_window(panel.workspace_window, output, name)
    result.setdefault("geometry", {})[name] = geometry
    assert_footer_unobscured(geometry)
    return geometry


def assert_draft(panel, annotation, entry_id):
    form = panel.review.form
    assert annotation.STATE.current_entry_id() == entry_id
    assert form.values["quality"] == "Good"
    assert form.dirty == {"quality"}
    assert annotation.STATE.row_annotation_store.get(entry_id, "quality") is None


def check_note_toggle(panel, output, result):
    from PyQt5.QtTest import QTest

    form = panel.review.form
    closed_height = form.scroll.height()
    closed_save_y = form.save_button.y()
    click(form.optional_toggle)
    QTest.qWait(100)
    visible(form.editors["notes"])
    assert form.scroll.verticalScrollBar().maximum() == 0
    assert form.scroll.height() > closed_height
    assert form.save_button.y() > closed_save_y
    capture_workspace(panel, output, result, "maximized-notes-expanded")
    click(form.optional_toggle)
    QTest.qWait(100)
    assert form.scroll.height() == closed_height
    assert form.save_button.y() == closed_save_y
    result["notes_expand_collapse_without_resize"] = "passed"


def check_floating_short_form(panel, annotation, app, output, result, entry_id):
    from PyQt5.QtTest import QTest

    form = panel.review.form
    switch(panel, "floating")
    if not form.optional_toggle.isChecked():
        click(form.optional_toggle)
    notes = form.editors["notes"]
    for label, size in (("floating-1000x720", (1000, 720)), ("floating-760x560", (760, 560))):
        resize_workspace(panel, *size)
        QTest.qWait(100)
        assert (panel.width(), panel.height()) == size, (label, panel.size())
        form.scroll.ensureWidgetVisible(panel._value_buttons[0])
        form.scroll.ensureWidgetVisible(notes)
        app.processEvents()
        visible(panel._value_buttons[0])
        visible(notes)
        visible(form.hint_label)
        visible(form.save_button)
        geometry = capture_workspace(panel, output, result, label)
        geometry["editor_scroll_maximum"] = form.scroll.verticalScrollBar().maximum()
        # The compact window can legitimately need scrolling for the score cards.
        # The roomy window must accommodate the two fields without scrolling.
        if size == (1000, 720):
            assert geometry["editor_scroll_maximum"] == 0, geometry
    notes.setFocus()
    notes.clear()
    QTest.keyClicks(notes, "visible note")
    app.processEvents()
    assert form.values["quality"] == "Good"
    assert form.values["notes"] == "visible note"
    assert form.dirty == {"quality", "notes"}
    capture_workspace(panel, output, result, "floating-notes-draft")
    click(form.save_button)
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    from pymolplugins.annotation.persistence import flush_writes
    flush_writes()
    assert annotation.STATE.row_annotation_store.get(entry_id, "quality") == "Good"
    assert annotation.STATE.row_annotation_store.get(entry_id, "notes") == "visible note"
    result["floating_short_form"] = {
        "entry_id": entry_id,
        "quality": "Good",
        "notes": "visible note",
        "sizes": [[1000, 720], [760, 560]],
    }


def run_host(output, result, root):
    assert os.name == "nt" and not os.getcwd().startswith("\\\\"), "Use Windows PyMOL from a local directory"
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, gui, invocation
    from pymol.Qt import QtCore, QtWidgets
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    from pmg_qt import pymol_qt_gui

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pymol_qt_gui._acceptance_app = app
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    result.update({"status": "running", "platform": sys.platform})
    annotation = None
    panel = None
    try:
        apply_window_state(app, window, output, result, "baseline-normal", False)
        apply_window_state(app, window, output, result, "baseline-maximized", True)
        os.environ.update(
            PYMOLPLUGINS_ANNOTATOR_HISTORY=str(root / "history.json"),
            PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(root / "mappings.json"),
            PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(root / "cache"),
        )
        isolate_workspace_settings(root, window, mode="docked")
        csv_path = fixture(root, 2, 4)
        from pymolplugins.host.plugin import pp_load
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes

        assert pp_load()["status"] == "ok"
        annotation.STATE = annotation.AnnotatorState()
        annotation.STATE.preload_enabled = False
        annotation.STATE.auto_advance = True
        annotation.STATE.annotation_fields = [
            {"name": "quality", "type": "single", "required": True, "values": ["Good", "Bad"]},
            {"name": "notes", "type": "text", "required": False, "values": []},
        ]
        assert annotation.sa_load_csv(str(csv_path), path_column="structure_file", id_column="row_id")
        panel = annotation.show_structure_annotator_panel()
        app.processEvents()
        wait_for(app, lambda: panel.review is not None and panel.review.form is not None and not panel._csv_task_id)
        assert panel.workspace_window.mode == "docked"
        assert window.dockWidgetArea(panel.workspace_window) == QtCore.Qt.LeftDockWidgetArea
        assert panel.review.form.save_button.text() == "Save and next"

        first_id = annotation.STATE.current_entry_id()
        apply_window_state(app, window, output, result, "annotator-normal-empty", False, panel)
        panel.review.form.scroll.ensureWidgetVisible(panel._value_buttons[0])
        click(panel._value_buttons[0])
        assert panel.review.form.values["quality"] == "Good"
        assert panel.review.form.dirty == {"quality"}
        apply_window_state(app, window, output, result, "annotator-maximized-draft", True, panel)
        check_note_toggle(panel, output, result)
        click(panel.review.form.save_button)
        wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None and annotation.STATE.index == 1)
        flush_writes()
        assert annotation.STATE.row_annotation_store.get(first_id, "quality") == "Good"
        assert annotation.STATE.index == 1
        result["save_and_next"] = {"entry_id": first_id, "quality": "Good", "cursor": annotation.STATE.index}

        second_id = annotation.STATE.current_entry_id()
        panel.review.form.scroll.ensureWidgetVisible(panel._value_buttons[0])
        click(panel._value_buttons[0])
        assert_draft(panel, annotation, second_id)
        apply_window_state(app, window, output, result, "annotator-normal-restored-draft", False, panel)
        assert_draft(panel, annotation, second_id)
        apply_window_state(app, window, output, result, "annotator-maximized-restored-draft", True, panel)
        assert_draft(panel, annotation, second_id)
        result["restore_keeps_draft"] = {"entry_id": second_id, "quality": "Good", "cursor": annotation.STATE.index}
        check_floating_short_form(panel, annotation, app, output, result, second_id)
        result.update({"status": "passed", "pymol": cmd.get_version()[0], "rows": 2})
        return result
    finally:
        if annotation is not None:
            from pymolplugins.annotation.persistence import flush_writes
            if panel is not None and panel._csv_task_id:
                wait_for(app, lambda: not panel._csv_task_id)
            if panel is not None:
                panel.close()
            futures = list(annotation._materialization_futures.values())
            annotation._reset_bg_cache()
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=30)
            assert annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._delete_managed_objects(False)


def run(output, result):
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-maximized-") as temporary:
        return run_host(output, result, Path(temporary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"status": "running", "geometry": {}}
    try:
        result = run(args.output, result)
    except Exception:
        result["status"] = "failed"
        result["traceback"] = traceback.format_exc()
    result["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
