"""Check the real wizard on a hidden Windows desktop without touching daily settings.

Run: D:/Softwares/Pymol3.1/python.exe -I -u tests/host/19_getting_started.py
Evidence: output/20_wizard/. Input: PyMOL's built-in glycine fragment.
"""

# %% Launch only on a separate desktop, following the existing Host harness.
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--child", action="store_true")
parser.add_argument("--output", type=Path, default=ROOT / "output/20_wizard")
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)

if not args.child:
    import win32event
    import win32process

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]

    def input_desktop():
        handle = user32.OpenInputDesktop(0, False, 1)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
            if not user32.GetUserObjectInformationW(
                handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return name.value
        finally:
            user32.CloseDesktop(handle)

    desktop_name = f"PyMOLWizard_{os.getpid()}"
    launch = {"input_before": input_desktop(), "desktop": desktop_name}
    desktop = user32.CreateDesktopW(desktop_name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\" + desktop_name
        process, thread, pid, _ = win32process.CreateProcess(
            sys.executable,
            subprocess.list2cmdline([sys.executable, "-I", "-u", str(Path(__file__).resolve()),
                                    *sys.argv[1:], "--child"]),
            None, None, False, 0x08000000, None, str(ROOT), startup,
        )
        launch["pid"] = pid
        try:
            deadline = time.monotonic() + 120
            while win32event.WaitForSingleObject(process, 1000) == 258:
                if time.monotonic() >= deadline:
                    win32process.TerminateProcess(process, 124)
                    win32event.WaitForSingleObject(process, 5000)
                    launch["timed_out"] = True
                    break
            launch["exit_code"] = win32process.GetExitCodeProcess(process)
        finally:
            thread.Close()
            process.Close()
        launch["input_after"] = input_desktop()
        assert launch["input_before"] == launch["input_after"], launch
    finally:
        user32.CloseDesktop(desktop)
        (output / "02_desktop.json").write_text(json.dumps(launch, indent=2), encoding="utf-8")
    print(json.dumps(launch), flush=True)
    result_path = output / "01_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    if launch["exit_code"] or result.get("status") != "passed":
        raise SystemExit("Host acceptance failed: " + str(result_path))
    raise SystemExit(0)

# %% Create the actual PyMOL Qt host and qualify its renderer.
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
from pymol import cmd, gui, invocation
from pymol.Qt import QtCore, QtWidgets
from PyQt5.QtTest import QTest
from pmg_qt.pymol_qt_gui import PyMOLQtGUI
from workspace_helpers import isolate_workspace_settings

report = {"status": "running", "checks": {}}
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
temporary = tempfile.TemporaryDirectory(prefix="pymolplugins-wizard-")
invocation.parse_args(["pymol", "-k", "-q"])
window = PyMOLQtGUI()
gui.createlegacypmgapp = window.createlegacypmgapp
window.add_lineedit()
window.resize(1100, 780)
isolate_workspace_settings(Path(temporary.name), window)
from pymolplugins.host.workspace import window_settings
settings = window_settings()
settings.setValue("updates/auto_check", False)
settings.sync()
window.show()
app.processEvents()


def framebuffer(filename):
    window.pymolwidget._pymolProcess()
    window.pymolwidget.repaint()
    app.processEvents()
    image = window.pymolwidget.grabFrameBuffer()
    colors = {image.pixel(x, y) for x in range(0, image.width(), 4)
              for y in range(0, image.height(), 4)}
    assert len(colors) > 30, ("Empty render", len(colors))
    assert image.save(str(output / filename))
    return (output / filename).read_bytes()


def click(button):
    assert button.isVisible() and button.isEnabled(), button.text()
    QTest.mouseClick(button, QtCore.Qt.LeftButton)
    app.processEvents()


try:
    cmd.fragment("gly", "wizard_reference")
    cmd.show_as("spheres", "wizard_reference")
    cmd.zoom("wizard_reference")
    first = framebuffer("03_render_before.png")
    cmd.color("cyan", "wizard_reference")
    cmd.turn("y", 35)
    assert framebuffer("04_render_after.png") != first
    report["checks"]["real_render"] = "passed"
    baseline = (cmd.get_names("all"), cmd.get_coords("wizard_reference").copy(), cmd.get_view())

    # %% Native menu, PyMOL parser, all pages and clipboard action.
    from pymolplugins.host import plugin, wizard
    from pymol import plugins
    # Same Qt menu setup as initializePlugins, without autoloading daily plugins.
    window.menudict["PluginQt"] = window.menudict["Plugin"]
    plugins.HAVE_QT = True
    assert wizard._wizard is None
    plugin.__init_plugin__()
    app.processEvents()
    assert wizard._wizard is None, "The guide must not auto-open."
    menu_action = next(action for action in window.findChildren(QtWidgets.QAction)
                       if action.text() == "PymolPlugins: Getting Started")
    menu_action.trigger()
    app.processEvents()
    guide = wizard._wizard
    assert guide.isVisible() and not guide.isModal() and guide.currentId() == 0
    report["checks"]["menu_and_on_demand"] = "passed"
    for index, topic in enumerate(wizard._TOPICS):
        assert guide.currentId() == index
        assert guide.currentPage().body.toPlainText()
        assert guide.grab().save(str(output / f"{index + 5:02d}_{topic}.png"))
        if index < 4:
            click(guide.button(guide.NextButton))
    click(guide.button(guide.BackButton))
    assert guide.currentId() == 3
    for index in range(guide.tool_choice.count()):
        guide.tool_choice.setCurrentIndex(index)
        app.processEvents()
        assert guide.example in guide.tool_help.toPlainText()
    clipboard = app.clipboard()
    saved_clipboard = QtCore.QMimeData()
    current_clipboard = clipboard.mimeData()
    if current_clipboard is not None:
        for format_name in current_clipboard.formats():
            saved_clipboard.setData(format_name, current_clipboard.data(format_name))
    try:
        click(guide.copy_button)
        assert clipboard.text() == guide.example
    finally:
        clipboard.setMimeData(saved_clipboard)
    report["checks"]["pages_back_and_examples"] = "passed"
    cmd.do("pp_wizard data")
    app.processEvents()
    assert wizard._wizard is guide and guide.currentId() == 1
    click(guide.button(guide.BackButton))
    assert guide.currentId() == 0
    assert plugin.pp_help("pp_wizard")["matches"][0]["mutates"] is False
    report["checks"]["console_and_help"] = "passed"

    # %% Actual workspace handoffs, existing SSH details, and ? re-entry.
    guide.show_topic("data")
    click(guide.pages[1].action_buttons["data"])
    from pymolplugins.annotation import plugin as annotation
    panel = annotation._panel
    assert panel.isVisible() and panel.main_tabs.currentIndex() == 1
    assert not guide.pages[1].feedback.isVisible()
    ssh_dialog = panel.source_location.quick_start_dialog
    ssh_seen = []

    def close_ssh_help():
        ssh_seen.append(ssh_dialog.isVisible())
        ssh_dialog.reject()

    QtCore.QTimer.singleShot(150, close_ssh_help)
    click(guide.pages[1].action_buttons["ssh"])
    assert ssh_seen == [True]
    click(panel.quick_start_button)
    assert wizard._wizard is guide and guide.currentId() == 2 and guide.isVisible()
    click(guide.pages[2].action_buttons["review"])
    assert panel.isVisible() and not guide.pages[2].feedback.isVisible()
    guide.show_topic("finish")
    click(guide.pages[4].action_buttons["batch"])
    from pymolplugins.targets import batch_widgets
    assert batch_widgets._DIALOG.isVisible() and not guide.pages[4].feedback.isVisible()
    report["checks"]["data_review_ssh_batch_and_question_button"] = "passed"

    # %% Compact layout, finish/reopen, and preservation of the existing molecule.
    guide.show_topic("tools")
    guide.resize(620, 530)
    app.processEvents()
    for control in (guide.tool_choice, guide.copy_button, guide.button(guide.NextButton),
                    guide.button(guide.BackButton), guide.button(guide.CancelButton)):
        assert control.visibleRegion().boundingRect().contains(control.rect()), control
    assert guide.grab().save(str(output / "10_compact.png"))
    guide.show_topic("finish")
    click(guide.button(guide.FinishButton))
    assert not guide.isVisible()
    cmd.do("pp_wizard")
    app.processEvents()
    assert wizard._wizard is guide and guide.isVisible() and guide.currentId() == 0
    click(guide.button(guide.CancelButton))
    assert not guide.isVisible()
    assert cmd.get_names("all") == baseline[0]
    assert (cmd.get_coords("wizard_reference") == baseline[1]).all()
    assert cmd.get_view() == baseline[2]
    report["checks"]["compact_finish_reopen_scene_preserved"] = "passed"
    report.update(status="passed", pymol_version=list(cmd.get_version()),
                  qt_version=QtCore.QT_VERSION_STR, wizard_module=wizard.__file__,
                  page_count=len(guide.pageIds()), command_count=plugin._command_audit()["declared_canonical_count"])
except Exception:
    report.update(status="failed", error=traceback.format_exc())
    traceback.print_exc()
finally:
    (output / "01_result.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    cmd.quit()
