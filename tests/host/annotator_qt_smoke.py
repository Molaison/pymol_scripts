"""Visible Host-Qt smoke for Structure Annotator fuzzy completion.

Run this with the Python executable bundled with Windows PyMOL, not WSL
Python. The test creates a real QApplication and visible panel, types through
QtTest, accepts one completion by mouse and one by keyboard, then exits.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
import sys
import tempfile
import traceback
from typing import Any, Dict


SCHEMA = "pymolplugins.annotator-qt-smoke/1.0"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _repository_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _qt_test_module(pyqt_name: str):
    if pyqt_name == "PyQt5":
        from PyQt5 import QtTest
    elif pyqt_name == "PyQt6":
        from PyQt6 import QtTest
    elif pyqt_name == "PySide2":
        from PySide2 import QtTest
    elif pyqt_name == "PySide6":
        from PySide6 import QtTest
    else:
        raise RuntimeError(f"unsupported pymol.Qt binding: {pyqt_name}")
    return QtTest


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _qt_enum(qt, group: str, name: str):
    value = getattr(qt, name, None)
    if value is not None:
        return value
    return getattr(getattr(qt, group), name)


def _seed_config() -> Dict[str, object]:
    return {
        "pattern": r"^.+\.pdb$",
        "label_name": "review",
        "values_text": "Keep,Reject",
        "step_cmd": "show cartoon, {object}",
        "bucket_cmds": {
            "Keep": "hide everything, {object}",
            "Reject": "color cyan, {object}",
        },
        "auto_advance": True,
        "keep_previous": False,
        "prefer_gz": True,
        "skip_annotated": False,
        "preload_enabled": False,
        "preload_ahead": 2,
        "preload_interval_ms": 250,
        "background_decompress_gz": False,
        "restore_last_dataset": False,
    }


def run_qt_smoke(repository: str) -> Dict[str, Any]:
    trace_enabled = os.environ.get("PP_QT_SMOKE_TRACE", "").strip() == "1"

    def progress(message: str) -> None:
        if trace_enabled:
            print(f"QT_SMOKE_TRACE {message}", flush=True)

    repository = os.path.abspath(repository)
    if repository not in sys.path:
        sys.path.insert(0, repository)

    from pymol import Qt as PymolQt
    from pymol.Qt import QtCore, QtWidgets

    qt_test = _qt_test_module(str(PymolQt.PYQT_NAME))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)

    history_root = tempfile.mkdtemp(prefix="PymolPluginsQtHistory-")
    previous_history = os.environ.get("PYMOLPLUGINS_ANNOTATOR_HISTORY")
    previous_mappings = os.environ.get("PYMOLPLUGINS_ANNOTATOR_MAPPINGS")
    os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = os.path.join(
        history_root, "history.json"
    )
    os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = os.path.join(
        history_root, "path_mappings.json"
    )
    panel = None
    try:
        from pymolplugins.annotation.history_store import record_config_history

        dataset_hint = os.path.join(history_root, "last dataset")
        record_config_history(dataset_hint, _seed_config())
        import pymolplugins.annotation.plugin as annotation

        panel = annotation.StructureAnnotatorPanel()
        progress("panel-created")
        panel.show()
        app.processEvents()
        progress("panel-shown")
        _require(
            panel.dir_edit.text() == dataset_hint,
            "latest global snapshot did not restore the dataset path",
        )
        _require(
            panel.label_edit.text() == "review"
            and panel.step_cmd_edit.toPlainText() == "show cartoon, {object}",
            "latest global snapshot did not restore text configuration",
        )
        _require(
            not panel.restore_last_cb.isChecked(),
            "restore-last opt-out was not restored from global history",
        )
        _require(
            panel.dataset_table.model().columnCount() >= 3
            and panel.metadata_table.columnCount() == 2,
            "CSV metadata widgets were not constructed",
        )
        _require(
            isinstance(panel.dataset_table, QtWidgets.QTableView)
            and not isinstance(panel.dataset_table, QtWidgets.QTableWidget),
            "Scores table is not the lazy QTableView implementation",
        )
        preview_navigation_calls = []
        original_goto = annotation.sa_goto
        try:
            annotation.sa_goto = lambda position: preview_navigation_calls.append(position)
            panel._populate_preview_table(
                {
                    "csv_path": os.path.join(history_root, "preview.csv"),
                    "display_columns": ["score"],
                    "rows": [
                        {
                            "entry_id": "preview-entry",
                            "row_number": 2,
                            "display_name": "preview.pdb",
                            "raw_path": "/remote/preview.pdb",
                            "resolved_path": "K:\\preview.pdb",
                            "status": "available",
                            "provider": "sshfs-win",
                            "matched_rule": "x_k",
                            "error": None,
                            "metadata": {"score": "0.9"},
                        }
                    ],
                }
            )
            panel._dataset_row_clicked(0, 0)
            _require(
                panel._table_mode == "preview"
                and not preview_navigation_calls
                and "before navigating" in panel.status_label.text(),
                "preview table click navigated or was not source-isolated",
            )
        finally:
            annotation.sa_goto = original_goto
        active_entries = [
            annotation.AnnotationEntry(
                id=f"model-entry-{index}",
                ordinal=index,
                row_number=index + 2,
                display_name=f"model-{index}.pdb",
                source_path=f"C:\\models\\model-{index}.pdb",
                source_kind="csv",
                metadata={"score": str(index / 10)},
                availability="available",
            )
            for index in range(2)
        ]
        panel._dataset_model.set_active(
            active_entries,
            ["score"],
            "csv",
            "review",
            {"model-entry-0": "Keep"},
        )
        changed_cells = []
        panel._dataset_model.dataChanged.connect(
            lambda top, bottom, *roles: changed_cells.append(
                (top.row(), top.column(), bottom.row(), bottom.column())
            )
        )
        changed_ids = panel._dataset_model.update_annotations(
            {"model-entry-0": "Reject"}
        )
        _require(
            changed_ids == ("model-entry-0",)
            and changed_cells == [(0, 3, 0, 3)],
            f"annotation update was not cell-targeted: {changed_ids}, {changed_cells}",
        )
        state_before_model_refresh = annotation._runtime_snapshot()
        try:
            first_generation_entry = active_entries[0]
            annotation.STATE.source_mode = "csv"
            annotation.STATE.csv_path = os.path.join(history_root, "same.csv")
            annotation.STATE.display_columns = ["score"]
            annotation.STATE.all_entries = list(active_entries)
            annotation.STATE.entries = list(active_entries)
            annotation.STATE.files = [
                entry.source_path for entry in active_entries
            ]
            annotation.STATE.dataset_generation += 1
            annotation.STATE.navigation_session = annotation._new_navigation_session(
                annotation.STATE.entries
            )
            annotation.STATE.navigation_generation = (
                annotation.STATE.dataset_generation
            )
            annotation.STATE.row_annotation_store = None
            panel._refresh_dataset_table()
            display_role = _qt_enum(QtCore.Qt, "ItemDataRole", "DisplayRole")
            before_score = panel._dataset_model.data(
                panel._dataset_model.index(0, 4), display_role
            )
            active_click_navigation_calls = []
            original_goto = annotation.sa_goto
            try:
                annotation.sa_goto = (
                    lambda position: active_click_navigation_calls.append(position)
                )
                panel._dataset_row_clicked(1, 0)
            finally:
                annotation.sa_goto = original_goto
            _require(
                active_click_navigation_calls == [2],
                "active table row did not use the entry-to-navigation map",
            )

            annotation.STATE.all_entries = list(active_entries)
            annotation.STATE.entries = [active_entries[0]]
            annotation.STATE.files = [active_entries[0].source_path]
            annotation.STATE.dataset_generation += 1
            annotation.STATE.navigation_session = annotation._new_navigation_session(
                annotation.STATE.entries
            )
            annotation.STATE.navigation_generation = (
                annotation.STATE.dataset_generation
            )
            panel._refresh_dataset_table()
            filtered_click_navigation_calls = []
            original_goto = annotation.sa_goto
            try:
                annotation.sa_goto = (
                    lambda position: filtered_click_navigation_calls.append(position)
                )
                panel._dataset_row_clicked(1, 0)
            finally:
                annotation.sa_goto = original_goto
            _require(
                not filtered_click_navigation_calls
                and "excluded by the current filter" in panel.status_label.text(),
                "filtered table row navigated or lacked its inactive explanation",
            )

            missing_entry = annotation.AnnotationEntry(
                id="model-entry-missing",
                ordinal=1,
                row_number=3,
                display_name="missing.pdb",
                source_path="C:\\models\\missing.pdb",
                source_kind="csv",
                metadata={"score": ""},
                availability="missing",
            )
            annotation.STATE.all_entries = [active_entries[0], missing_entry]
            annotation.STATE.entries = [active_entries[0]]
            annotation.STATE.files = [active_entries[0].source_path]
            annotation.STATE.dataset_generation += 1
            annotation.STATE.navigation_session = annotation._new_navigation_session(
                annotation.STATE.entries
            )
            annotation.STATE.navigation_generation = (
                annotation.STATE.dataset_generation
            )
            panel._refresh_dataset_table()
            missing_click_navigation_calls = []
            original_goto = annotation.sa_goto
            try:
                annotation.sa_goto = (
                    lambda position: missing_click_navigation_calls.append(position)
                )
                panel._dataset_row_clicked(1, 0)
            finally:
                annotation.sa_goto = original_goto
            _require(
                not missing_click_navigation_calls
                and "not active: missing" in panel.status_label.text(),
                "missing table row navigated or lacked its failure explanation",
            )

            annotation.STATE.all_entries = list(active_entries)
            annotation.STATE.entries = list(active_entries)
            annotation.STATE.files = [
                entry.source_path for entry in active_entries
            ]
            annotation.STATE.dataset_generation += 1
            annotation.STATE.navigation_session = annotation._new_navigation_session(
                annotation.STATE.entries
            )
            annotation.STATE.navigation_generation = (
                annotation.STATE.dataset_generation
            )
            panel._refresh_dataset_table()
            stale_click_navigation_calls = []
            annotation.STATE.all_entries = list(reversed(active_entries))
            annotation.STATE.entries = list(reversed(active_entries))
            annotation.STATE.files = [
                entry.source_path for entry in annotation.STATE.entries
            ]
            annotation.STATE.dataset_generation += 1
            annotation.STATE.navigation_session = annotation._new_navigation_session(
                annotation.STATE.entries
            )
            annotation.STATE.navigation_generation = (
                annotation.STATE.dataset_generation
            )
            original_goto = annotation.sa_goto
            try:
                annotation.sa_goto = (
                    lambda position: stale_click_navigation_calls.append(position)
                )
                panel._dataset_row_clicked(0, 0)
            finally:
                annotation.sa_goto = original_goto
            _require(
                not stale_click_navigation_calls
                and panel._dataset_model.entry_id_at(0) == "model-entry-1"
                and "table was refreshed" in panel.status_label.text(),
                "stale same-source click used an old entry-to-index map",
            )
            modified_entry = annotation.AnnotationEntry(
                id=first_generation_entry.id,
                ordinal=0,
                row_number=2,
                display_name=first_generation_entry.display_name,
                source_path=first_generation_entry.source_path,
                source_kind="csv",
                metadata={"score": "9.9"},
                availability="missing",
            )
            annotation.STATE.all_entries = [modified_entry]
            annotation.STATE.entries = [modified_entry]
            annotation.STATE.dataset_generation += 1
            annotation.STATE.navigation_session = annotation._new_navigation_session(
                annotation.STATE.entries
            )
            annotation.STATE.navigation_generation = (
                annotation.STATE.dataset_generation
            )
            panel._refresh_dataset_table()
            after_score = panel._dataset_model.data(
                panel._dataset_model.index(0, 4), display_role
            )
            after_status = panel._dataset_model.data(
                panel._dataset_model.index(0, 1), display_role
            )
            _require(
                before_score == "0.0"
                and after_score == "9.9"
                and after_status == "missing",
                "same-source dataset generation retained stale score/status cells",
            )
        finally:
            annotation._restore_runtime_snapshot(state_before_model_refresh)
        progress("panel-contract-checked")
        panel.main_tabs.setCurrentIndex(1)
        app.processEvents()

        step_edit = panel.step_cmd_edit
        step_edit.clear()
        step_edit.setFocus()
        qt_test.QTest.keyClicks(step_edit, "cartn")
        app.processEvents()
        progress("step-typed")
        step_binding = panel._history_bindings[-1]
        step_suggestions = list(step_binding.model.stringList())
        step_popup = bool(step_binding.completer.popup().isVisible())
        _require(step_popup, "multiline fuzzy popup did not open while typing")
        _require(
            step_suggestions
            and step_suggestions[0] == "show cartoon, {object}",
            f"unexpected multiline fuzzy suggestions: {step_suggestions}",
        )
        index = step_binding.model.index(0, 0)
        rectangle = step_binding.completer.popup().visualRect(index)
        qt_test.QTest.mouseClick(
            step_binding.completer.popup().viewport(),
            _qt_enum(QtCore.Qt, "MouseButton", "LeftButton"),
            pos=rectangle.center(),
        )
        app.processEvents()
        progress("step-accepted")
        _require(
            step_edit.toPlainText() == "show cartoon, {object}",
            "mouse completion did not replace the full multiline field",
        )

        bucket_edit = next(iter(panel._bucket_inputs.values()))
        bucket_edit.clear()
        bucket_edit.setFocus()
        qt_test.QTest.keyClicks(bucket_edit, "evrythng")
        app.processEvents()
        progress("bucket-typed")
        bucket_binding = panel._bucket_history_bindings[0]
        bucket_suggestions = list(bucket_binding.model.stringList())
        bucket_popup = bool(bucket_binding.completer.popup().isVisible())
        _require(bucket_popup, "bucket fuzzy popup did not open while typing")
        _require(
            bucket_suggestions
            and bucket_suggestions[0] == "hide everything, {object}",
            f"unexpected bucket fuzzy suggestions: {bucket_suggestions}",
        )
        qt_test.QTest.keyClick(
            bucket_edit, _qt_enum(QtCore.Qt, "Key", "Key_Down")
        )
        qt_test.QTest.keyClick(
            bucket_edit, _qt_enum(QtCore.Qt, "Key", "Key_Return")
        )
        app.processEvents()
        progress("bucket-accepted")
        _require(
            bucket_edit.text() == "hide everything, {object}",
            "keyboard completion did not replace the full bucket field",
        )

        return {
            "status": "pass",
            "schema": SCHEMA,
            "completed_at_utc": _utc_now(),
            "repository": repository,
            "python": sys.version,
            "platform": platform.platform(),
            "qt_binding": str(PymolQt.PYQT_NAME),
            "panel_visible": bool(panel.isVisible()),
            "restored_dataset_path": panel.dir_edit.text(),
            "restored_label": panel.label_edit.text(),
            "restore_last_checked": bool(panel.restore_last_cb.isChecked()),
            "static_binding_count": len(panel._history_bindings),
            "bucket_binding_count": len(panel._bucket_history_bindings),
            "step_popup_visible": step_popup,
            "step_suggestions": step_suggestions,
            "step_mouse_accepted": step_edit.toPlainText(),
            "bucket_popup_visible": bucket_popup,
            "bucket_suggestions": bucket_suggestions,
            "bucket_keyboard_accepted": bucket_edit.text(),
            "preview_click_navigation_calls": preview_navigation_calls,
            "scores_table_class": type(panel.dataset_table).__name__,
            "annotation_changed_cells": changed_cells,
            "active_click_navigation_calls": active_click_navigation_calls,
            "filtered_click_navigation_calls": filtered_click_navigation_calls,
            "missing_click_navigation_calls": missing_click_navigation_calls,
            "same_source_reload_values": [before_score, after_score, after_status],
            "stale_click_navigation_calls": stale_click_navigation_calls,
        }
    finally:
        progress("cleanup-begin")
        if panel is not None:
            panel.close()
            panel.deleteLater()
            app.processEvents()
        progress("cleanup-panel-done")
        if previous_history is None:
            os.environ.pop("PYMOLPLUGINS_ANNOTATOR_HISTORY", None)
        else:
            os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = previous_history
        if previous_mappings is None:
            os.environ.pop("PYMOLPLUGINS_ANNOTATOR_MAPPINGS", None)
        else:
            os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = previous_mappings
        shutil.rmtree(history_root, ignore_errors=True)
        progress("cleanup-complete")


def _write_result(path: str, result: Dict[str, Any]) -> None:
    if not path:
        return
    destination = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=_repository_root())
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    try:
        result = run_qt_smoke(args.repository)
    except Exception as exc:
        result = {
            "status": "fail",
            "schema": SCHEMA,
            "completed_at_utc": _utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    _write_result(args.output, result)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
