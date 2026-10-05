"""Real Windows profile editing, preview isolation and command-failure recovery."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_helpers import isolate_workspace_settings
from annotator_workflow_smoke import fixture, wait_for
from annotator_layout_smoke import combo_text_bounds, visible


def check_profile_layout(dialog, app, output, qt_test, qt_core):
    geometry = {}
    for label, size in (("normal", (980, 740)), ("small", (760, 560))):
        dialog.resize(*size)
        qt_test.qWait(80)
        assert (dialog.width(), dialog.height()) == size, "Profile size hints enlarged the window"
        dialog.profile_scroll.ensureWidgetVisible(dialog.source_format)
        app.processEvents()
        source = combo_text_bounds(dialog.source_format)
        target = combo_text_bounds(dialog.target_format)
        tables = {}
        for index, name, table in ((0, "rewrites", dialog.rewrites), (1, "roots", dialog.roots)):
            dialog.profile_scroll.ensureWidgetVisible(dialog.rule_tabs)
            tabs = dialog.rule_tabs.tabBar()
            qt_test.mouseClick(tabs, qt_core.Qt.LeftButton, pos=tabs.tabRect(index).center())
            app.processEvents()
            assert dialog.rule_tabs.currentIndex() == index
            assert table.rowCount() > 0
            bounds = visible(table)
            assert dialog.grab().save(str(output.with_name(output.stem + "-profile-" + label + "-" + name + ".png")))
            assert table.viewport().height() >= 2 * table.rowHeight(0), (
                f"{label} {name}: viewport {table.viewport().height()}, row {table.rowHeight(0)}, "
                f"table {table.height()}, header {table.horizontalHeader().height()}, tabs {tabs.height()}")
            combos = [combo_text_bounds(table.cellWidget(row, 0)) for row in range(table.rowCount())] if index == 0 else []
            tables[name] = {"bounds": bounds, "row_height": table.rowHeight(0), "combos": combos}
        geometry[label] = {"size": size, "save": visible(dialog.save_button), "source": source,
                           "target": target, "tables": tables}
        dialog.profile_scroll.ensureWidgetVisible(dialog.test_button)
        app.processEvents()
        visible(dialog.test_button)
        qt_test.mouseClick(dialog.test_button, qt_core.Qt.LeftButton)
        qt_test.qWait(50)
        geometry[label]["result"] = visible(dialog.result_label)
        assert dialog.grab().save(str(output.with_name(output.stem + "-profile-" + label + "-result.png")))
    return geometry


def edit_profile(panel, app, action, qt_test, qt_core):
    errors = []
    def edit():
        dialog = app.activeModalWidget()
        try:
            from pymolplugins.annotation.mapping_widgets import MappingProfileDialog
            assert isinstance(dialog, MappingProfileDialog)
            action(dialog)
        except Exception as exc:
            errors.append(exc)
            if dialog is not None:
                dialog.reject()
    qt_core.QTimer.singleShot(0, edit)
    qt_test.mouseClick(panel.mapping_edit_btn, qt_core.Qt.LeftButton)
    if errors:
        raise errors[0]


def check_profiles(panel, app, directory, output, qt_test, qt_core):
    from pymolplugins.annotation.mapping_widgets import set_format
    from pymolplugins.annotation.mapping_store import get_mapping_profile_readonly
    mapping_path = directory / "mappings.json"
    if not panel.mapping_section.toggle.isChecked():
        qt_test.mouseClick(panel.mapping_section.toggle, qt_core.Qt.LeftButton)
    app.processEvents()
    panel.main_tabs.currentWidget().ensureWidgetVisible(panel.mapping_edit_btn)
    assert panel.mapping_edit_btn.isVisible()
    before = mapping_path.read_bytes() if mapping_path.exists() else None
    geometry = {}
    def create(dialog):
        qt_test.mouseClick(dialog.new_button, qt_core.Qt.LeftButton)
        dialog.name_edit.setText("lab server")
        set_format(dialog.source_format, "posix")
        set_format(dialog.target_format, "windows")
        dialog._add_rewrite(rule=dict(kind="strip_prefix", pattern="artifact:", replacement="", enabled=True))
        dialog._add_rewrite(rule=dict(kind="regex", pattern=r"^job-(\d+):", replacement=r"/remote/run\1/", enabled=True))
        dialog._add_root(rule=dict(source_prefix="/remote/run7", target_prefix=str(directory), priority=10,
                                   provider="mapped", enabled=True, case_sensitive=True, require_exists=True))
        dialog.sample_edit.setText("artifact:job-7:model 0.pdb")
        qt_test.mouseClick(dialog.test_button, qt_core.Qt.LeftButton)
        assert not dialog.error_label.text(), dialog.error_label.text()
        assert str(directory / "model 0.pdb") in dialog.result_label.text()
        assert (mapping_path.read_bytes() if mapping_path.exists() else None) == before
        qt_test.qWait(50)
        assert dialog.grab().save(str(output.with_name(output.stem + "-profile-result.png")))
        visible(dialog.result_label)
        geometry.update(check_profile_layout(dialog, app, output, qt_test, qt_core))
        qt_test.mouseClick(dialog.save_button, qt_core.Qt.LeftButton)
        assert dialog.saved_name == "lab server"
    edit_profile(panel, app, create, qt_test, qt_core)
    assert panel.mapping_profile_combo.currentText() == "lab server"
    assert panel.target_format_combo.currentData() == "windows"
    assert len(get_mapping_profile_readonly("lab server")["rewrites"]) == 2
    saved = mapping_path.read_bytes()
    def invalid(dialog):
        dialog._add_rewrite(rule=dict(kind="regex", pattern="[", replacement="", enabled=True))
        qt_test.mouseClick(dialog.save_button, qt_core.Qt.LeftButton)
        assert dialog.error_label.text() and not dialog.saved_name
        assert mapping_path.read_bytes() == saved
        dialog.reject()
    edit_profile(panel, app, invalid, qt_test, qt_core)
    def duplicate(dialog):
        qt_test.mouseClick(dialog.copy_button, qt_core.Qt.LeftButton)
        qt_test.mouseClick(dialog.save_button, qt_core.Qt.LeftButton)
        assert dialog.saved_name == "lab server copy"
    edit_profile(panel, app, duplicate, qt_test, qt_core)
    assert get_mapping_profile_readonly("lab server copy") is not None
    edit_profile(panel, app, lambda dialog: qt_test.mouseClick(dialog.delete_button, qt_core.Qt.LeftButton), qt_test, qt_core)
    assert get_mapping_profile_readonly("lab server copy") is None
    panel.mapping_profile_combo.setCurrentText("lab server")
    return geometry


def check_preview(panel, annotation, app, qt_test, qt_core):
    from pymol import cmd
    from pymolplugins.annotation.persistence import flush_writes
    flush_writes()
    selected = panel.dataset_table.selectionModel()
    selected.select(panel._dataset_model.index(1, 0), qt_core.QItemSelectionModel.Select | qt_core.QItemSelectionModel.Rows)
    before = (annotation.STATE.current_entry_id(), annotation.STATE.current_object,
              tuple(panel._dataset_model.entry_id_at(row) for row in range(panel._dataset_model.rowCount())),
              set(panel.review.selection.ids), set(cmd.get_names()))
    panel._show_import()
    panel.main_tabs.currentWidget().ensureWidgetVisible(panel.csv_preview_btn)
    assert panel.csv_preview_btn.isVisible()
    qt_test.mouseClick(panel.csv_preview_btn, qt_core.Qt.LeftButton)
    wait_for(app, lambda: not panel._csv_task_id)
    assert not panel.error_label.isVisible(), panel.error_label.text()
    dialog = panel._mapping_preview_dialog
    assert dialog.isVisible()
    assert dialog.table.rowCount() == 12
    assert dialog.table.item(0, 1).text() == "artifact:job-7:model 0.pdb"
    assert (annotation.STATE.current_entry_id(), annotation.STATE.current_object,
            tuple(panel._dataset_model.entry_id_at(row) for row in range(panel._dataset_model.rowCount())),
            set(panel.review.selection.ids), set(cmd.get_names())) == before
    assert panel._table_mode == "active"
    dialog.close()
    panel.main_tabs.setCurrentWidget(panel.review_tab)
    panel.review.selection.clear()
    panel._refresh_status()


def check_commands(panel, annotation, app, qt_test, qt_core):
    from pymol import cmd
    observed = []
    def require_selection(selection):
        observed.append(selection)
        cmd.color("cyan", selection)
    cmd.extend("test_required_selection", require_selection)
    applied = annotation.STATE.step_cmd
    panel.step_cmd_edit.setPlainText("test_required_selection")
    before = annotation.STATE.index
    panel._next()
    wait_for(app, lambda: panel._pending_navigation is None)
    assert annotation.STATE.index != before
    assert annotation.STATE.step_cmd == applied and not observed
    assert not panel.error_label.isVisible(), panel.error_label.text()
    panel.step_cmd_edit.setPlainText("zoom {object}\ntest_required_selection {obj")
    assert panel._apply_commands() is False
    assert annotation.STATE.step_cmd == applied
    assert "Command line 2" in panel.error_label.text()
    panel.step_cmd_edit.setPlainText("turn y, 15\ntest_required_selection")
    assert panel._apply_commands()
    for preload in (True, False):
        annotation.STATE.preload_enabled = preload
        panel.preload_enabled_cb.setChecked(preload)
        annotation._evict_unneeded_objects()
        before = (annotation.STATE.index, annotation.STATE.current_object, set(cmd.get_names("objects", enabled_only=1)))
        before_view = cmd.get_view()
        panel._next()
        wait_for(app, lambda: panel._pending_navigation is None)
        assert (annotation.STATE.index, annotation.STATE.current_object, set(cmd.get_names("objects", enabled_only=1))) == before
        assert annotation._object_exists(before[1])
        assert "test_required_selection" in panel.error_label.text()
        assert "failed" in panel.error_label.text()
        assert max(abs(a - b) for a, b in zip(before_view, cmd.get_view())) < 1e-5
    panel.step_cmd_edit.setPlainText('zoom {object}\nutil.cbc {object}\nutil.cba(33,"{object}",_self=cmd)\ntest_required_selection {selection}')
    assert panel._apply_commands()
    panel._next()
    wait_for(app, lambda: panel._pending_navigation is None)
    assert observed[-1] == annotation.STATE.current_object
    assert not panel.error_label.isVisible(), panel.error_label.text()
    assert not [name for name in cmd.get_names("all") if name.startswith("ppfe_ack_")]
    assert annotation._verify_current_object_enabled()
    return {"unapplied_commands": "passed", "incomplete_placeholder": "passed", "missing_selection_warm_and_cold": "passed",
            "explicit_current_selection": "passed", "camera_restoration": "passed", "python_style_commands": "passed"}


def run(output):
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5 import QtTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-mapping-") as temporary:
        directory = Path(temporary)
        os.environ.update(PYMOLPLUGINS_ANNOTATOR_HISTORY=str(directory / "history.json"),
                          PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(directory / "mappings.json"),
                          PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(directory / "cache"))
        isolate_workspace_settings(directory, window)
        path = fixture(directory, 12, 8)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        for row in rows[1:]:
            row[0] = "artifact:job-7:" + row[0]
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
        before_csv = path.read_bytes()
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        annotation.STATE = annotation.AnnotatorState()
        panel = annotation.show_structure_annotator_panel()
        try:
            assert panel.mapping_profile_combo.currentText() == "custom"
            assert panel.target_format_combo.currentData() == "custom"
            assert not panel.worker_access_group.isVisible()
            panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData("csv"))
            panel.csv_edit.setText(str(path))
            panel._csv_path_edited()
            wait_for(app, lambda: not panel._csv_task_id)
            panel.access_method_combo.setCurrentIndex(panel.access_method_combo.findData("worker"))
            panel._preview_csv()
            assert not panel._csv_task_id and "server root" in panel.error_label.text()
            panel.access_method_combo.setCurrentIndex(panel.access_method_combo.findData("direct"))
            panel.error_label.hide()
            profile_geometry = check_profiles(panel, app, directory, output, QtTest.QTest, QtCore)
            panel.target_format_combo.setCurrentIndex(panel.target_format_combo.findData("custom"))
            app.processEvents()
            panel.grab().save(str(output.with_name(output.stem + "-import.png")))
            started = time.perf_counter()
            QtTest.QTest.mouseClick(panel.csv_load_btn, QtCore.Qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            assert len(annotation.STATE.entries) == 12, panel.error_label.text()
            import_seconds = time.perf_counter() - started
            panel.column_picker.menu.hide()
            wait_for(app, lambda: len(annotation.STATE.managed_objects) >= 3)
            assert annotation.STATE.mapping_profile == "lab server"
            assert annotation.STATE.mapping_target_format == "custom"
            assert all(entry.resolved_path == str(directory / ("model %d.pdb" % (i % 3))) for i, entry in enumerate(annotation.STATE.entries))
            check_preview(panel, annotation, app, QtTest.QTest, QtCore)
            checks = check_commands(panel, annotation, app, QtTest.QTest, QtCore)
            app.processEvents()
            panel.grab().save(str(output.with_suffix(".png")))
            assert path.read_bytes() == before_csv
            panel.close()
            flush_writes()
            panel = annotation.show_structure_annotator_panel()
            assert panel.mapping_profile_combo.currentText() == "lab server"
            assert panel.target_format_combo.currentData() == "custom"
            assert panel.step_cmd_edit.toPlainText() == annotation.STATE.step_cmd
            return dict(status="passed", pymol=cmd.get_version()[0], full_pymol_window=True, import_seconds=import_seconds,
                        profile_create_test_save_duplicate_delete="passed", invalid_regex_preserves_store="passed",
                        preview_preserves_dataset_scene_and_multiselection="passed", profile_restoration="passed",
                        computer_format_override_restoration="passed", source_csv_unchanged=True,
                        profile_geometry=profile_geometry, **checks)
        finally:
            panel.close()
            futures = list(annotation._materialization_futures.values())
            annotation._reset_bg_cache()
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=30)
            assert annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._delete_managed_objects(False)
            app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output)
    except Exception as exc:
        result = dict(status="failed", error=str(exc), traceback=traceback.format_exc())
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
