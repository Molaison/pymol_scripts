"""Install a ZIP using Windows PyMOL's real installer, cmd API, and Qt menus.

Run with PyMOL's bundled python.exe -I from a Windows-local working directory.
The temporary GUI container replaces the desktop window; installation dialogs
are captured, and user plugin preferences are not saved.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_helpers import isolate_workspace_settings


def _unexpected_prompt(*args, **kwargs):
    raise RuntimeError(f"Unexpected installer confirmation: {args!r}")


def _check_onboarding(annotation, directory, menu, output):
    from pymol import cmd
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    from annotator_workflow_smoke import fixture, wait_for
    from workspace_helpers import resize_workspace
    from pymolplugins.annotation.persistence import flush_writes

    app = QtWidgets.QApplication.instance()
    action = next(action for action in menu.actions() if action.text() == "PymolPlugins: Structure Annotator")
    action.trigger()
    app.processEvents()
    panel = annotation._panel
    panel.workspace_window.set_mode("floating")
    resize_workspace(panel, 760, 560)
    panel.window().activateWindow()
    app.processEvents()
    assert annotation.STATE.dataset is None
    assert panel.main_tabs.currentIndex() == 1
    assert not panel.source_open_button.isEnabled()
    assert panel.quick_start_button.isVisible() and panel.quick_start_button.accessibleName() == "Quick start"
    assert not panel.resume_button.isVisible() and not panel.history_button.isVisible()
    assert [label.text() for label in panel.source_tab.findChildren(QtWidgets.QLabel) if label.isVisible()] == ["Open from", "Path"]

    empty_config = directory / "empty-ssh-config"
    empty_config.write_text("# No saved servers on this new computer\n")
    controls = panel.source_location
    controls.config.setText(str(empty_config))
    controls._reload_profiles()
    assert controls.connection.count() == 1
    assert controls.settings_button.text() == "Set up SSH…"
    assert not controls.connection.itemIcon(0).isNull()
    assert "WSL config is not selected automatically" in controls.config.toolTip()
    settings_checked = []
    dialog_geometry = {"connections_before": [controls.settings_dialog.width(), controls.settings_dialog.height(),
                                              int(controls.settings_dialog.windowState())]}
    def inspect_connections():
        try:
            dialog_geometry["connections_shown"] = [controls.settings_dialog.width(), controls.settings_dialog.height(),
                int(controls.settings_dialog.windowState()), controls.settings_dialog.minimumWidth(),
                controls.settings_dialog.minimumHeight(), controls.settings_dialog.sizeHint().width(),
                controls.settings_dialog.sizeHint().height()]
            assert controls.settings_dialog.width() <= 760 and controls.settings_dialog.height() <= 560
            assert controls.config.isVisible() and not controls.program.isVisible()
            controls.settings_dialog.grab().save(str(output.with_name(output.stem + "-connections.png")))
            QtTest.QTest.mouseClick(controls.advanced_section.toggle, QtCore.Qt.LeftButton)
            assert controls.program.isVisible()
            settings_checked.append(True)
        except Exception:
            settings_checked.append(traceback.format_exc())
        finally:
            controls.settings_dialog.reject()
    QtCore.QTimer.singleShot(150, inspect_connections)
    QtTest.QTest.mouseClick(controls.settings_button, QtCore.Qt.LeftButton)
    assert settings_checked == [True], settings_checked
    controls.connection.setCurrentText("my-lab")
    assert controls.settings_button.text() == "Connections…"
    assert "Remote" in panel.source_path_edit.placeholderText()
    controls.connection.setCurrentIndex(0)
    help_text = controls.quick_start_text.toPlainText()
    for phrase in ("Structure column", "Save and next", "ssh my-lab", "Python 3", "saved locally"):
        assert phrase in help_text, phrase
    help_opened = []
    def close_help():
        dialog_geometry["help_shown"] = [controls.quick_start_dialog.width(), controls.quick_start_dialog.height(),
            int(controls.quick_start_dialog.windowState()), controls.quick_start_dialog.minimumWidth(),
            controls.quick_start_dialog.minimumHeight()]
        help_opened.append(controls.quick_start_dialog.isVisible())
        controls.quick_start_dialog.grab().save(str(output.with_name(output.stem + "-help.png")))
        controls.quick_start_dialog.reject()
    QtCore.QTimer.singleShot(150, close_help)
    QtTest.QTest.mouseClick(panel.quick_start_button, QtCore.Qt.LeftButton)
    assert help_opened == [True]
    assert controls.quick_start_dialog.width() <= 760 and controls.quick_start_dialog.height() <= 560
    assert not empty_config.read_text().strip().startswith("Host")
    for button in (panel.quick_start_button, panel.source_open_button):
        assert button.visibleRegion().boundingRect().contains(button.rect()), button.text()
    panel.grab().save(str(output.with_name(output.stem + "-open.png")))

    source = directory / "first review"
    source.mkdir()
    table = fixture(source, 3, 8)
    originals = {path: path.read_bytes() for path in source.iterdir()}
    panel.source_path_edit.setText(str(table))
    QtTest.QTest.mouseClick(panel.source_open_button, QtCore.Qt.LeftButton)
    wait_for(app, lambda: not panel._csv_task_id and not panel._pending_csv_action)
    if annotation.STATE.dataset is None and panel.path_column_edit.isVisible():
        panel.path_column_edit.setText("structure_file")
        QtTest.QTest.mouseClick(panel.source_open_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id and not panel._pending_csv_action)
    assert not panel.error_label.isVisible(), panel.error_label.text()
    assert len(annotation.STATE.entries) == 3
    assert panel.main_tabs.currentIndex() == 0
    first = annotation.STATE.current_entry_id()
    assert panel.resume_button.isEnabled(), "A saved source should offer Continue last review"
    panel._continue_last_review()
    wait_for(app, lambda: annotation.STATE.dataset is not None and not panel._csv_task_id and panel._pending_navigation is None)
    assert annotation.STATE.current_entry_id() == first
    assert cmd.count_atoms(annotation.STATE.current_object) == 3
    assert not panel.view_scope_label.isVisible(), "Unfiltered counts should not repeat the full-set size"
    panel._mark_current_for_later()
    assert annotation.STATE.row_annotation_store.get(first, "__pymol_review_status__") == "later"
    assert panel.review_status_label.isVisible() and panel.review_status_label.text() == "Later"
    assert not panel.status_label.isVisible(), "The Later badge already confirms the action"
    panel._next_unannotated()
    wait_for(app, lambda: not panel._pending_navigation)
    assert annotation.STATE.current_entry_id() != first
    panel._request_navigation(0)
    wait_for(app, lambda: not panel._pending_navigation)
    panel._clear_current_review_status()
    assert annotation.STATE.row_annotation_store.get(first, "__pymol_review_status__", "") == ""
    assert not panel.review_status_label.isVisible()
    assert panel.review.form.primary_buttons, "No initial label choice"
    QtTest.QTest.mouseClick(panel.review.form.primary_buttons[0], QtCore.Qt.LeftButton)
    assert annotation.STATE.index == 0
    assert not annotation.STATE.row_annotation_store.annotations_for(first)
    QtTest.QTest.mouseClick(panel.review.form.save_button, QtCore.Qt.LeftButton)
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert annotation.STATE.row_annotation_store.annotations_for(first)
    assert annotation.STATE.index == 1
    panel._mark_current_for_later()
    handoff = directory / "review handoff.json"
    with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(handoff), "JSON Files (*.json)")):
        panel._export_review_handoff()
    handoff_document = json.loads(handoff.read_text(encoding="utf-8"))
    assert handoff_document["schema"] == "pymolplugins.review-handoff/1"
    assert handoff_document["counts"]["later"] == 1
    assert all("__pymol_review_status__" not in item["annotations"] for item in handoff_document["entries"])
    panel._clear_current_review_status()
    panel._columns_selected(["AC.iptm", "score_0"])
    panel.review.scores.apply_config({"columns": ["AC.iptm", "score_0"],
                                     "rules": {"AC.iptm": {"direction": "higher", "good": 0.01}}})
    wait_for(app, lambda: panel.review.scores.future is None)
    assert not panel.score_footer.hint.isVisible()
    assert panel.score_footer.cards["AC.iptm"][1][2].text() == "Target met"
    assert panel.score_footer.help_button.isVisible()
    score_help = []
    def close_score_help():
        dialog = app.activeModalWidget()
        score_help.append(isinstance(dialog, QtWidgets.QMessageBox) and "filtered set" in dialog.text())
        if dialog is not None:
            dialog.accept()
    QtCore.QTimer.singleShot(150, close_score_help)
    QtTest.QTest.mouseClick(panel.score_footer.help_button, QtCore.Qt.LeftButton)
    assert score_help == [True]
    flush_writes()
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert panel.review.form.save_button.visibleRegion().boundingRect().contains(panel.review.form.save_button.rect())
    panel.grab().save(str(output.with_name(output.stem + "-review.png")))
    panel.filter_edit.setText("model 1")
    panel._apply_filters()
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert len(annotation.STATE.entries) == 1
    assert panel.view_scope_label.isVisible() and panel.view_scope_label.text() == "Filtered 1 / 3"
    panel._clear_filters()
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert len(annotation.STATE.entries) == 3 and not panel.view_scope_label.isVisible()
    assert annotation.STATE.filter_presets == [], "Old bundles must work without presets"
    from annotator_source_review_smoke import open_table
    grouped = directory / "grouped review"
    grouped.mkdir()
    grouped_table = fixture(grouped, 3, 8)
    definitions = [{"name": "High", "expression": "AC.iptm >= 0.01", "sort_column": "AC.iptm", "sort_descending": True},
                   {"name": "Low", "expression": "AC.iptm < 0.02"},
                   {"name": "Missing", "expression": "AC.iptm is None"}]
    generated_review = grouped / "review.json"
    generated_review.write_text(json.dumps({"path_column": "structure_file", "display_columns": ["AC.iptm", "score_0"],
                                            "filter_presets": definitions}), encoding="utf-8")
    generated_files = {path: path.read_bytes() for path in grouped.iterdir()}
    open_table(panel, grouped_table, app, QtTest.QTest, QtCore.Qt)
    assert [p["name"] for p in annotation.STATE.filter_presets] == ["High", "Low", "Missing"]
    panel.window().activateWindow()
    wait_for(app, panel.window().isActiveWindow)
    app.processEvents()
    option = QtWidgets.QStyleOptionToolButton()
    panel.filter_button.initStyleOption(option)
    arrow = panel.filter_button.style().subControlRect(QtWidgets.QStyle.CC_ToolButton, option,
            QtWidgets.QStyle.SC_ToolButtonMenu, panel.filter_button)
    menu_selection = []
    def choose_high_group():
        try:
            assert panel.filter_menu.isVisible(), "The menu arrow did not open the group menu"
            action = next(a for a in panel.filter_menu.actions() if a.text() == "High")
            panel.filter_menu.grab().save(str(output.with_name(output.stem + "-group-menu.png")))
            QtTest.QTest.mouseClick(panel.filter_menu, QtCore.Qt.LeftButton, pos=panel.filter_menu.actionGeometry(action).center())
            menu_selection.append(True)
        except Exception:
            menu_selection.append(traceback.format_exc())
        finally:
            panel.filter_menu.close()
    QtCore.QTimer.singleShot(150, choose_high_group)
    QtTest.QTest.mouseClick(panel.filter_button, QtCore.Qt.LeftButton, pos=arrow.center())
    wait_for(app, lambda: bool(menu_selection))
    assert menu_selection == [True], menu_selection
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert [float(e.metadata["AC.iptm"]) for e in annotation.STATE.entries] == [0.02, 0.01]
    high_ids = {e.id for e in annotation.STATE.entries}
    panel._apply_filter_preset("Low")
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert len(annotation.STATE.entries) == 2 and len(high_ids & {e.id for e in annotation.STATE.entries}) == 1
    panel._apply_filter_preset("Missing")
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert not annotation.STATE.entries and panel.empty_view.isVisible()
    panel._clear_filters()
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert len(annotation.STATE.entries) == 3
    QtTest.QTest.mouseClick(panel.filter_button, QtCore.Qt.LeftButton, pos=QtCore.QPoint(10, panel.filter_button.height() // 2))
    wait_for(app, panel.filter_dialog.isActiveWindow)
    panel.filter_preset_combo.setFocus()
    QtTest.QTest.keyClick(panel.filter_preset_combo, QtCore.Qt.Key_Home)
    QtTest.QTest.keyClick(panel.filter_preset_combo, QtCore.Qt.Key_Down)
    assert panel.filter_preset_combo.currentData() == "High"
    panel.filter_edit.setText("missing_score > 0")
    QtTest.QTest.mouseClick(panel.apply_view_btn, QtCore.Qt.LeftButton)
    assert panel.filter_dialog.isVisible() and panel.filter_error_label.isVisible()
    assert len(annotation.STATE.entries) == 3
    panel.filter_edit.setText("AC.iptm >= 0.02")
    def accept_filter_name():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QtWidgets.QInputDialog):
            dialog.setTextValue("High")
            dialog.accept()
    QtCore.QTimer.singleShot(100, accept_filter_name)
    QtTest.QTest.mouseClick(panel.filter_save_button, QtCore.Qt.LeftButton)
    flush_writes()
    assert not panel.filter_error_label.isVisible(), panel.filter_error_label.text()
    override = json.loads((grouped / "review.override.json").read_text())
    assert override["filter_presets"][0]["expression"] == "AC.iptm >= 0.02"
    panel.filter_dialog.grab().save(str(output.with_name(output.stem + "-filters.png")))
    QtTest.QTest.mouseClick(panel.apply_view_btn, QtCore.Qt.LeftButton)
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert len(annotation.STATE.entries) == 1
    assert annotation.STATE.row_annotation_store.snapshot() == {}, "Groups must not assign annotations"
    panel._show_filters()
    panel.filter_preset_combo.setCurrentIndex(panel.filter_preset_combo.findData("Missing"))
    panel._select_filter_preset()
    def confirm_delete():
        box = app.activeModalWidget()
        if isinstance(box, QtWidgets.QMessageBox):
            box.button(QtWidgets.QMessageBox.Yes).click()
    QtCore.QTimer.singleShot(100, confirm_delete)
    QtTest.QTest.mouseClick(panel.filter_delete_button, QtCore.Qt.LeftButton)
    assert [p["name"] for p in annotation.STATE.filter_presets] == ["High", "Low"]
    panel.filter_edit.setText('contains(structure_file, "model 1")')
    def name_manual_group():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QtWidgets.QInputDialog):
            dialog.setTextValue("Manual")
            dialog.accept()
    QtCore.QTimer.singleShot(100, name_manual_group)
    QtTest.QTest.mouseClick(panel.filter_save_button, QtCore.Qt.LeftButton)
    assert [p["name"] for p in annotation.STATE.filter_presets] == ["High", "Low", "Manual"]
    panel.filter_dialog.reject()
    flush_writes()
    saved_groups = list(annotation.STATE.filter_presets)
    panel.close()
    flush_writes()
    annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
    panel = annotation.show_structure_annotator_panel()
    open_table(panel, grouped_table, app, QtTest.QTest, QtCore.Qt)
    assert annotation.STATE.filter_presets == saved_groups
    panel._apply_filter_preset("High")
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert len(annotation.STATE.entries) == 1 and cmd.count_atoms(annotation.STATE.current_object) == 3
    panel.grab().save(str(output.with_name(output.stem + "-group.png")))
    open_table(panel, table, app, QtTest.QTest, QtCore.Qt)
    assert annotation.STATE.filter_presets == [], "Named groups leaked from another source"
    assert all(path.read_bytes() == data for path, data in generated_files.items())
    panel.close()
    assert annotation._csv_async_pool().waitForDone(30000)
    if annotation._bg_executor is not None:
        annotation._bg_executor.shutdown(wait=True)
    flush_writes()
    annotation._delete_managed_objects(False)
    return {"fresh_settings": True, "empty_ssh_setup_action": True,
            "open_page_visible_labels": ["Open from", "Path"], "ssh_advanced_disclosure": True,
            "dialog_geometry": dialog_geometry,
            "contextual_score_help": True, "later_badge": True, "conditional_filter_counts": True,
            "in_app_quick_start_opened": True, "first_csv_rows": 3,
            "first_structure_atoms": 3, "label_click_is_draft": True,
            "first_save_and_next": True, "compact_window": [760, 560],
            "later_route": True, "handoff_export": True, "resume_action_available": True,
            "filter_groups": {"loaded": 3, "high_rows": 2, "overlap_rows": 1, "empty_group_rows": 0,
                              "edited_rows": 1, "override_reopen": True, "create": True, "delete": True,
                              "invalid_edit_preserves_view": True, "no_annotation_writes": True,
                              "old_bundle_and_source_isolation": True, "generated_files_unchanged": len(generated_files)},
            "source_files_unchanged": len(originals),
            "evidence_boundary": "Automated native first-use flow; not a timed study with a new human user."}


def _check_install(package, directory, parent, menu, *, onboarding=False, output=None):
    from pymol import cmd, plugins
    from pymol.plugins.installation import installPluginFromFile

    installPluginFromFile(str(package), parent=parent, plugdir=str(directory))
    info = plugins.plugins["pymol_plugins"]
    assert info.loaded, "Plugin Manager did not finish loading the installed bundle"
    runtime = importlib.import_module("pymolplugins.host.plugin")
    loaded = runtime.pp_load()
    assert loaded["status"] == "ok", loaded
    installed = directory / "pymol_plugins"
    assert Path(runtime.REPOSITORY_ROOT).resolve() == installed.resolve()
    for name, module in list(sys.modules.items()):
        if name == "pymolplugins" or name.startswith("pymolplugins."):
            Path(module.__file__).resolve().relative_to(installed.resolve())
    labels = [action.text() for action in menu.actions()]
    assert len(labels) == 6 and "PymolPlugins: Structure Annotator" in labels, labels
    assert "PymolPlugins: Batch Processing" in labels, labels
    annotation = importlib.import_module("pymolplugins.annotation.plugin")
    assert callable(annotation.show_structure_annotator_panel)
    assert Path(annotation.__file__).resolve().is_relative_to(installed.resolve())
    if onboarding:
        result = _check_onboarding(annotation, directory, menu, output)
        return {"status": "passed", "plugin_version": info.get_version(),
                "pymol_version": cmd.get_version()[0], "installed_entry": info.filename,
                "onboarding": result}
    batch = importlib.import_module("pymolplugins.targets.batch_widgets")
    batch_action = next(action for action in menu.actions() if action.text() == "PymolPlugins: Batch Processing")
    batch_action.trigger()
    from pymol.Qt import QtGui, QtWidgets
    QtWidgets.QApplication.processEvents()
    assert batch._DIALOG.isVisible() and batch._DIALOG.save_b.isChecked()
    assert batch._DIALOG.match.count() == 3
    shared_style = importlib.import_module("pymolplugins.annotation.panel_style")
    assert batch._DIALOG.styleSheet() == shared_style.STYLE
    assert batch._DIALOG.objectName() == "batchWorkspace"
    assert QtGui.QFontInfo(batch._DIALOG.source.font()).family() == "Segoe UI"
    assert QtGui.QFontInfo(batch._DIALOG.commands.font()).family() == "Consolas"
    assert batch._DIALOG.run_button.property("role") == "primary"
    assert Path(batch.__file__).resolve().is_relative_to(installed.resolve())
    host = batch._DIALOG.workspace_window
    assert host.parentWidget() is parent and not host.isFloating()
    host.set_mode("floating")
    assert host.isFloating() and batch._DIALOG.window_mode_button.isEnabled()
    host.set_mode("docked")
    annotation_action = next(action for action in menu.actions() if action.text() == "PymolPlugins: Structure Annotator")
    annotation_action.trigger()
    QtWidgets.QApplication.processEvents()
    annotation_host = annotation._panel.workspace_window
    assert annotation_host in parent.tabifiedDockWidgets(host)
    assert annotation._panel.styleSheet() == shared_style.STYLE
    annotation_host.set_mode("floating")
    assert annotation_host.isFloating()
    ssh = importlib.import_module("pymolplugins.remote.ssh")
    ssh_source = importlib.import_module("pymolplugins.annotation.sources.ssh_source")
    for module in (ssh, ssh_source):
        Path(module.__file__).resolve().relative_to(installed.resolve())
    assert ssh._REMOTE_SCRIPT == Path(ssh.__file__).with_name("ssh_worker.py").read_text(encoding="utf-8")
    annotation._panel.source_location.connection.setCurrentIndex(1)
    assert annotation._panel.source_location.remote
    assert annotation._panel.source_location.connection.isVisible()
    assert annotation._panel.source_location.profile is annotation._panel.source_location.connection
    annotation._panel.close()
    batch._DIALOG.close()
    return {
        "status": "passed",
        "os": os.name,
        "platform": sys.platform,
        "python": sys.version,
        "pymol_version": cmd.get_version()[0],
        "plugin_version": info.get_version(),
        "installed_entry": info.filename,
        "canonical_modules": len(loaded["loaded"]),
        "canonical_commands": loaded["command_audit"]["declared_canonical_count"],
        "compat_commands": loaded["command_audit"]["declared_compat_count"],
        "menus": labels,
        "structure_annotator_import": "passed",
        "batch_processing_menu_dialog": "passed",
        "batch_processing_shared_theme": "passed",
        "installed_workspace_modes_and_tabification": "passed",
        "installed_ssh_modules_and_profile_control": "passed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--onboarding-only", action="store_true")
    args = parser.parse_args()
    package_sha256 = hashlib.sha256(args.package.read_bytes()).hexdigest()
    assert os.name == "nt", "Run this check with Windows PyMOL's Python"
    assert not os.getcwd().startswith("\\\\"), "Use a Windows-local working directory"
    from pymol import plugins
    from pymol.Qt import QtWidgets
    from pymol.plugins import legacysupport
    from pmg_qt.mimic_pmg_tk import PmwMenuBar

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if args.onboarding_only:
        from pymol import invocation, gui as pymol_gui
        from pmg_qt.pymol_qt_gui import PyMOLQtGUI
        invocation.parse_args(["pymol", "-k", "-q"])
        parent = PyMOLQtGUI()
        pymol_gui.createlegacypmgapp = parent.createlegacypmgapp
        parent.add_lineedit()
    else:
        parent = QtWidgets.QMainWindow()
    parent.resize(1440, 900)
    parent.show()
    menu = QtWidgets.QMenu("Plugin", parent)
    gui = SimpleNamespace(menuBar=PmwMenuBar({"PluginQt": menu}))
    dialogs = []
    messagebox = SimpleNamespace(
        showinfo=lambda title, message, **kwargs: dialogs.append([title, message]),
        askyesno=_unexpected_prompt, askokcancel=_unexpected_prompt,
    )
    plugins.preferences["instantsave"] = False
    plugins.preferences["verbose"] = True
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-install-") as temporary:
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(Path(temporary) / "mappings.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(Path(temporary) / "history.json")
        if args.onboarding_only:
            os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(Path(temporary) / "cache")
        isolate_workspace_settings(temporary, parent, mode=None)
        plugins.set_startup_path([temporary], autosave=False)
        with patch.object(plugins, "get_pmgapp", return_value=gui), \
                patch.object(plugins, "HAVE_QT", True), \
                patch.object(legacysupport, "tkMessageBox", messagebox):
            try:
                result = _check_install(args.package.resolve(), Path(temporary), parent, menu,
                                        onboarding=args.onboarding_only, output=args.output)
            except Exception:
                result = {"status": "failed", "traceback": traceback.format_exc()}
        assert dialogs == [["Success", 'Plugin "pymol_plugins" has been installed.']], dialogs
        result["dialogs"] = dialogs
        result["package_sha256"] = package_sha256
        result["package_path"] = str(args.package)
    if not args.onboarding_only:
        parent.close()
        app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
