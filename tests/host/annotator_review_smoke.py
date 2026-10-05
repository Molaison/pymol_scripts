"""Real Qt/PyMOL acceptance for multi-row review fields and score guidance."""

import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_helpers import isolate_workspace_settings
from annotator_workflow_smoke import fixture, wait_for


def click_row(panel, row, qt_test, qt, modifiers=None):
    index = panel._dataset_model.index(row, 2)
    panel.dataset_table.scrollTo(index)
    rectangle = panel.dataset_table.visualRect(index)
    qt_test.mouseClick(panel.dataset_table.viewport(), qt.LeftButton, qt.NoModifier if modifiers is None else modifiers, rectangle.center())


def choose_multiple(widget, values, qt_test, qt):
    widget.open_menu()
    for index in range(widget.items.count()):
        item = widget.items.item(index)
        item.setCheckState(qt.Checked if item.text() in values else qt.Unchecked)
    qt_test.mouseClick(widget.apply_button, qt.LeftButton)


def run(output, rows):
    assert os.name == "nt", "Use Windows PyMOL's bundled Python"
    assert not os.getcwd().startswith("\\\\"), "Use a Windows-local working directory"
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
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-review-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        isolate_workspace_settings(directory, window)
        path = fixture(directory, rows, 16)
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        from pymolplugins.annotation.state_store import read_dataset_state
        annotation.STATE = annotation.AnnotatorState()
        annotation.STATE.annotation_fields = [
            {"name": "quality", "type": "single", "required": True, "values": ["Good", "Bad"]},
            {"name": "reason", "type": "multiple", "required": True, "values": ["interface", "shape"]},
            {"name": "notes", "type": "text", "required": False, "values": []},
        ]
        panel = annotation.show_structure_annotator_panel()
        qt_test, qt = QtTest.QTest, QtCore.Qt
        try:
            panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData("csv"))
            panel.csv_edit.setText(str(path))
            panel._csv_path_edited()
            wait_for(app, lambda: not panel._csv_task_id)
            panel.id_column_edit.setText("row_id")
            qt_test.mouseClick(panel.csv_load_btn, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            assert len(annotation.STATE.entries) == rows, panel.error_label.text()
            panel.column_picker.menu.hide()
            panel.activateWindow()
            panel.raise_()
            qt_test.qWait(80)
            panel._columns_selected(["AC.iptm", "score_0", "full_pass"])
            wait_for(app, lambda: panel.review.scores.summary is not None and panel.review.scores.future is None)
            assert set(panel.score_footer.cards) == {"AC.iptm", "score_0"}
            panel.review.scores.apply_config({"columns": None, "rules": {"AC.iptm": {"direction": "higher", "good": .8, "bad": .5}}})
            wait_for(app, lambda: panel.review.scores.future is None)
            assert panel.review.scores.summary.cards(annotation.STATE.current_entry())[0]["absolute_tone"] == "bad"

            panel.auto_advance_cb.setChecked(False)
            panel.review.form.scroll.ensureWidgetVisible(panel._value_buttons[0])
            qt_test.mouseClick(panel._value_buttons[0], qt.LeftButton)
            assert not panel._csv_task_id, "A label click saved instead of staging a choice"
            assert not panel.review.form.save_button.isEnabled(), "Missing required reason was accepted"
            assert panel.review.form.values["quality"] == "Good"
            assert annotation.STATE.row_annotation_store.snapshot() == {}
            assert annotation.STATE.index == 0
            panel.review.form.scroll.ensureWidgetVisible(panel.review.form.editors["reason"])
            choose_multiple(panel.review.form.editors["reason"], ["interface", "shape"], qt_test, qt)
            qt_test.mouseClick(panel.review.form.save_button, qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            first_id = annotation.STATE.current_entry_id()
            store = annotation.STATE.row_annotation_store
            assert store.get(first_id, "quality") == "Good", panel.error_label.text()
            assert json.loads(store.get(first_id, "reason")) == ["interface", "shape"]
            assert store.get(first_id, "notes") is None
            assert annotation.STATE.annotated_indices == {0}

            click_row(panel, 0, qt_test, qt)
            click_row(panel, 2, qt_test, qt, qt.ShiftModifier)
            assert len(panel.review.selection.ids) == 3
            assert len(panel.dataset_table.selectionModel().selectedRows()) == 3
            assert annotation.STATE.index == 0
            click_row(panel, 4, qt_test, qt, qt.ControlModifier)
            assert len(panel.review.selection.ids) == 4
            click_row(panel, 4, qt_test, qt, qt.ControlModifier)
            assert len(panel.review.selection.ids) == 3
            assert len(panel.dataset_table.selectionModel().selectedRows()) == 3
            selected = tuple(panel.review.targets())
            assert len(selected) == 3
            panel.review.form.scroll.ensureWidgetVisible(panel._value_buttons[1])
            qt_test.mouseClick(panel._value_buttons[1], qt.LeftButton, qt.ShiftModifier)
            assert not panel._csv_task_id, "Shift-click saved before the user submitted the batch"
            panel.review.form.scroll.ensureWidgetVisible(panel.review.form.editors["reason"])
            choose_multiple(panel.review.form.editors["reason"], ["interface"], qt_test, qt)
            panel.review.form.scroll.ensureWidgetVisible(panel.review.form.optional_toggle)
            qt_test.mouseClick(panel.review.form.optional_toggle, qt.LeftButton)
            panel.review.form.scroll.ensureWidgetVisible(panel.review.form.editors["notes"])
            panel.review.form.editors["notes"].setFocus()
            qt_test.keyClicks(panel.review.form.editors["notes"], "batch note")
            output.parent.mkdir(parents=True, exist_ok=True)
            panel.grab().save(str(output.with_name(output.stem + "-batch.png")))
            original_load = cmd.load
            loads = []
            wait_for(app, lambda: not annotation._preload_queue)
            with patch.object(cmd, "load", lambda *args, **kwargs: (loads.append(args), original_load(*args, **kwargs))[1]):
                qt_test.mouseClick(panel.review.form.save_button, qt.LeftButton)
                wait_for(app, lambda: not panel._csv_task_id)
            assert not loads, "Batch annotation loaded unpreviewed structures"
            for entry_id in selected:
                assert store.get(entry_id, "quality") == "Bad"
                assert json.loads(store.get(entry_id, "reason")) == ["interface"]
                assert store.get(entry_id, "notes") == "batch note"
            assert len(panel.review.selection.ids) == 3
            panel._undo()
            wait_for(app, lambda: not panel._csv_task_id)
            assert store.get(first_id, "quality") == "Good"
            assert json.loads(store.get(first_id, "reason")) == ["interface", "shape"]
            for entry_id in selected:
                if entry_id != first_id:
                    assert store.annotations_for(entry_id) == {}

            reference_entry = annotation.STATE.all_entries[80]
            before_percentile = panel.review.scores.summary.cards(reference_entry)[0]["percentile"]
            panel.filter_mode_combo.setCurrentIndex(1)
            panel.filter_edit.setText("AC.iptm >= 0.8")
            panel._apply_view()
            wait_for(app, lambda: not panel._csv_task_id)
            wait_for(app, lambda: panel.review.scores.future is None and panel.review.scores.summary is not None)
            after_card = panel.review.scores.summary.cards(annotation.STATE.current_entry())[0]
            assert panel.review.scores.summary.count == sum(index % 100 >= 80 for index in range(rows))
            assert after_card["percentile"] != before_percentile
            assert after_card["absolute_tone"] == "good" and after_card["relative_tone"] == "bad"
            assert annotation.STATE.current_entry().display_name in panel.score_footer.title.text()
            panel.details_cb.setChecked(True)
            names = [panel.metadata_table.item(row, 0).text() for row in range(panel.metadata_table.rowCount())]
            assert "AC.iptm" in names and "score_11" not in names
            panel.details_cb.setChecked(False)
            panel._columns_selected(["score_0"])
            wait_for(app, lambda: panel.review.scores.future is None)
            assert set(panel.score_footer.cards) == {"score_0"}

            panel._save_ui_state()
            flush_writes()
            saved = read_dataset_state(str(path), source_mode="csv", csv_path=str(path))
            assert saved["config"]["annotation_fields"] == annotation.STATE.annotation_fields
            assert saved["config"]["score_guidance"] == annotation.STATE.score_guidance
            exported = directory / "reviewed.csv"
            assert annotation.sa_export_csv(str(exported))
            with exported.open(encoding="utf-8-sig", newline="") as handle:
                exported_rows = list(csv.DictReader(handle))
            assert exported_rows[0]["pymol_annotation_quality"] == "Good"
            assert json.loads(exported_rows[0]["pymol_annotation_reason"]) == ["interface", "shape"]

            timings, io_calls = [], []
            for _ in range(10):
                next_index = (annotation.STATE.index + 1) % len(annotation.STATE.entries)
                target = annotation._object_name_for_index(annotation.STATE.files[next_index], next_index)
                wait_for(app, lambda: annotation._object_exists(target))
                with patch.object(cmd, "load", side_effect=AssertionError("Warm navigation loaded a file")):
                    started = time.perf_counter()
                    panel._next()
                    timings.append((time.perf_counter() - started) * 1000)
                assert annotation.STATE.current_entry().display_name in panel.score_footer.title.text()
            assert max(timings) < 50, timings
            panel._columns_selected(["AC.iptm", "score_0"])
            wait_for(app, lambda: panel.review.scores.future is None)
            panel._request_navigation(0)
            wait_for(app, lambda: panel._pending_navigation is None)
            output.parent.mkdir(parents=True, exist_ok=True)
            panel.grab().save(str(output.with_suffix(".png")))
            return {"status": "passed", "pymol": cmd.get_version()[0], "rows": rows,
                    "shift_ctrl_selection": "passed", "required_optional_multiple_fields": "passed",
                    "batch_save_undo": "passed", "batch_structure_loads": loads,
                    "filtered_score_cohort": panel.review.scores.summary.count,
                    "percentile_before_filter": before_percentile, "percentile_after_filter": after_card["percentile"],
                    "detail_column_filter": "passed", "config_roundtrip_export": "passed",
                    "next_median_ms": statistics.median(timings), "next_max_ms": max(timings)}
        finally:
            if panel._csv_task_kind in {"annotations", "annotation_undo"}:
                wait_for(app, lambda: not panel._csv_task_id)
            panel.close()
            futures = list(annotation._materialization_futures.values())
            annotation._reset_bg_cache()
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=30)
            annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._delete_managed_objects(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output, args.rows)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
