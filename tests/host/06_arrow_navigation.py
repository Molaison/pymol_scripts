"""Real Qt key events for object/group navigation and Annotator shortcuts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from workspace_helpers import isolate_workspace_settings

APPLICATION = None


def run(output):
    global APPLICATION
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    APPLICATION = app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.resize(1000, 720)
    window.show()
    window.activateWindow()
    app.processEvents()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pymol-arrow-") as temporary:
        directory = Path(temporary)
        isolate_workspace_settings(directory, window)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        from pymolplugins.host.plugin import pp_load
        from pymolplugins.navigation import commands as nav
        assert pp_load()["status"] == "ok"
        for i in range(3):
            cmd.fragment(("gly", "ala", "trp")[i], "model_" + str(i))
        cmd.select("user_selection", "model_0 and name CA")
        QTest.mouseClick(window.pymolwidget, QtCore.Qt.LeftButton, pos=QtCore.QPoint(180, 160))
        app.processEvents()
        window.pymolwidget._pymolProcess()
        QTest.qWait(100)
        initial_names = set(cmd.get_names("all"))
        initial_enabled = set(cmd.get_names("objects", enabled_only=1))
        selection_members = cmd.index("user_selection")
        previous_keys = {key: cmd.key_mappings.get(key) for key in nav.NAVIGATION_KEYS}
        previous_present = {key: key in cmd.key_mappings for key in nav.NAVIGATION_KEYS}

        def press(key, modifier=QtCore.Qt.NoModifier, widget=None):
            QTest.keyClick(widget or app.focusWidget() or window.pymolwidget, key, modifier)
            app.processEvents()
            window.pymolwidget._pymolProcess()
            QTest.qWait(60)

        cmd.do("pp_nav objects, model_*, page=2")
        assert nav._CONTROLLER and nav._CONTROLLER.keys_bound
        focus = type(app.focusWidget()).__name__
        press(QtCore.Qt.Key_Right)
        assert nav._CONTROLLER.session.cursor == 1, ("right", focus, nav._CONTROLLER.session.cursor)
        press(QtCore.Qt.Key_Left)
        assert nav._CONTROLLER.session.cursor == 0
        press(QtCore.Qt.Key_Right, QtCore.Qt.ControlModifier)
        assert nav._CONTROLLER.session.cursor == 2
        press(QtCore.Qt.Key_Left, QtCore.Qt.ControlModifier)
        assert nav._CONTROLLER.session.cursor == 0
        press(QtCore.Qt.Key_Left)
        assert nav._CONTROLLER.session.cursor == 2, "wrap"
        window.lineedit.setText("color cyan, model_0")
        window.lineedit.setFocus()
        cursor = window.lineedit.cursorPosition()
        press(QtCore.Qt.Key_Left)
        assert window.lineedit.cursorPosition() == cursor - 1
        assert nav._CONTROLLER.session.cursor == 2, "text editing changed object"
        window.lineedit.clear()
        cmd.do("pp_nav stop")
        assert nav._CONTROLLER is None
        assert set(cmd.get_names("all")) == initial_names
        assert set(cmd.get_names("objects", enabled_only=1)) == initial_enabled
        assert cmd.index("user_selection") == selection_members
        for key in previous_keys:
            assert (key in cmd.key_mappings) == previous_present[key]
            assert cmd.key_mappings.get(key) == previous_keys[key]

        cmd.pseudoatom("hidden_label", label="hidden")
        cmd.group("candidate_0", "model_0 hidden_label")
        cmd.group("candidate_1", "model_1")
        cmd.group("candidate_2", "model_2")
        cmd.disable("hidden_label")
        cmd.disable("candidate_1")
        before_groups = set(cmd.get_names("objects", enabled_only=1))
        cmd.do("pp_nav groups, candidate_*")
        assert nav._CONTROLLER.session.count == 3
        press(QtCore.Qt.Key_Right, widget=window.lineedit)
        assert nav._CONTROLLER.session.cursor == 1
        enabled = set(cmd.get_names("all", enabled_only=1))
        assert "candidate_1" in enabled and "candidate_0" not in enabled
        assert "hidden_label" not in enabled
        press(QtCore.Qt.Key_Left, widget=window.lineedit)
        assert nav._CONTROLLER.session.cursor == 0
        assert "hidden_label" not in cmd.get_names("all", enabled_only=1)
        window.repaint()
        window.pymolwidget.repaint()
        QTest.qWait(150)
        rect = window.frameGeometry()
        app.primaryScreen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(output.with_suffix(".png")))
        cmd.do("pp_nav stop")
        assert set(cmd.get_names("objects", enabled_only=1)) == before_groups
        cmd.do("pp_nav objects, model_*, keys=0")
        assert not nav._CONTROLLER.keys_bound
        cmd.do("pp_nav stop")
        for key in previous_keys:
            assert cmd.key_mappings.get(key) == previous_keys[key]

        import pymolplugins.annotation.plugin as a
        from pymolplugins.annotation.persistence import flush_writes
        cmd.group("candidates", "candidate_0 candidate_1 candidate_2")
        a.sa_load_groups("candidates", str(directory / "review.json"), restore_saved=0)
        panel = a.show_structure_annotator_panel()
        app.processEvents()
        panel.window().activateWindow()
        QTest.qWait(120)
        press(QtCore.Qt.Key_Right, QtCore.Qt.AltModifier, panel)
        assert a.STATE.index == 1, ("annotator right", a.STATE.index)
        press(QtCore.Qt.Key_Left, QtCore.Qt.AltModifier, panel)
        assert a.STATE.index == 0
        assert nav._CONTROLLER is None
        panel.close()
        flush_writes()
        assert all(not shortcut.isEnabled() for shortcut in panel._qt_shortcuts.shortcuts)
        return {"status": "passed", "pymol": cmd.get_version()[0],
                "viewport_click_focus": focus, "object_arrows": True,
                "group_arrows": True, "ctrl_page_and_wrap": True,
                "text_editing_preserved": True, "keys_0_opt_out": True,
                "stop_restores_keys_and_object_visibility": True, "hidden_member_preserved": True,
                "annotator_alt_arrows": True, "screenshot": str(output.with_suffix(".png"))}


if __name__ == "__main__":
    output = Path(sys.argv[1]).resolve()
    try:
        result = run(output)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)

