"""Real Windows PyMOL acceptance on an existing score table; no mocked I/O/cmd.

Run from WSL: just test-review-overview '<Windows path to 01_scores.tsv>'
Requires Windows PyMOL's bundled Python and the real seed/step RNA review table.
Inputs stay read-only. A temporary review copy adds explicit references to each
seed's lowest-step structure; this is a test comparison choice, not a new score.
JSON, native screenshots, exported rows and raw sidecars are kept beside --output.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_workflow_smoke import wait_for
from annotator_source_review_smoke import open_table
from workspace_helpers import isolate_workspace_settings, resize_workspace


def run(args, result):
    global _APPLICATION
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    from pymol import cmd, gui, invocation
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI

    raw = args.table.read_bytes()
    original_hash = hashlib.sha256(raw).hexdigest()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines(), delimiter="\t"))
    assert rows and all(key in rows[0] for key in ("relative_path", "seed", "step", "worst_interface_pae_A"))
    baselines = {}
    for row in rows:
        seed = row["seed"]
        if seed not in baselines or int(row["step"]) < int(baselines[seed]["step"]):
            baselines[seed] = row

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _APPLICATION = app
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    result.update(platform=sys.platform, pymol=cmd.get_version()[0], source=str(args.table),
                  rows=len(rows), original_columns=len(rows[0]), source_sha256=original_hash)

    with tempfile.TemporaryDirectory(prefix="PymolPlugins-overview-") as temporary:
        directory = Path(temporary)
        os.environ.update(PYMOLPLUGINS_ANNOTATOR_HISTORY=str(directory / "history.json"),
            PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(directory / "mappings.json"),
            PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(directory / "cache"))
        isolate_workspace_settings(directory, window, mode="floating")
        table = directory / "review.tsv"
        for row in rows:
            row["candidate_file"] = str(args.table.parent / row["relative_path"])
            row["reference_file"] = str(args.table.parent / baselines[row["seed"]]["relative_path"])
        # One explicit negative case; all candidate structures and scores are real.
        rows[-1]["reference_file"] = str(directory / "deliberately_missing_reference.cif")
        with table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        review = {"path_column": "candidate_file", "reference_column": "reference_file",
                  "label_column": "label", "display_columns": ["seed", "step", "worst_interface_pae_A"],
                  "step_cmd": "hide everything, {object}\nshow cartoon, {object}\nzoom {object}"}
        (directory / "review.json").write_text(json.dumps(review), encoding="utf-8")
        from pymolplugins.host.plugin import pp_load
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        assert pp_load()["status"] == "ok"
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        panel = annotation.show_structure_annotator_panel()
        resize_workspace(panel, 1000, 720)
        app.processEvents()
        beats = []
        timer = QtCore.QTimer()
        timer.timeout.connect(lambda: beats.append(time.perf_counter()))
        timer.start(10)
        start = time.perf_counter()
        open_table(panel, table, app, QTest, QtCore.Qt)
        result["import_seconds"] = time.perf_counter() - start
        result["import_heartbeat_ticks"] = len(beats)
        timer.stop()
        assert len(annotation.STATE.all_entries) == len(rows)
        assert annotation.STATE.reference_column == "reference_file"
        assert panel._dataset_model._headers[4] == "Reference structure"
        assert cmd.count_atoms(annotation.STATE.current_object) > 100
        result["first_structure_atoms"] = cmd.count_atoms(annotation.STATE.current_object)

        # Real modal UI: choose a group and a numeric extra layer, inspect full data.
        modal_errors = []
        def configure_sample():
            dialog = app.activeModalWidget()
            try:
                dialog.group_combo.setCurrentIndex(dialog.group_combo.findData("seed"))
                dialog.metric_combo.setCurrentIndex(dialog.metric_combo.findData("worst_interface_pae_A"))
                dialog.per_stratum.setValue(2)
                result["overview_text"] = dialog.count_label.text()
                result["method"] = dialog.method_label.text()
                result["strata"] = [[dialog.strata_table.item(r, c).text() for c in range(4)]
                                    for r in range(dialog.strata_table.rowCount())]
                result["distribution"] = [[dialog.distribution_table.item(r, c).text() for c in range(8)]
                                           for r in range(dialog.distribution_table.rowCount())]
                result["groups"] = dialog.group_table.rowCount()
                dialog.grab().save(str(args.output.with_name(args.output.stem + "-overview.png")))
                button = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "Apply sample")
                QTest.mouseClick(button, QtCore.Qt.LeftButton)
            except Exception:
                modal_errors.append(traceback.format_exc())
                dialog.reject()
        QtCore.QTimer.singleShot(100, configure_sample)
        QTest.mouseClick(panel.overview_button, QtCore.Qt.LeftButton)
        assert not modal_errors, modal_errors
        wait_for(app, lambda: not panel._csv_task_id)
        assert not panel.error_label.isVisible(), panel.error_label.text()
        sample_ids = [entry.id for entry in annotation.STATE.entries]
        assert 1 < len(sample_ids) < len(rows)
        assert len(sample_ids) == sum(int(row[3]) for row in result["strata"])
        assert panel._dataset_model.rowCount() == len(sample_ids)
        assert annotation.STATE.navigation_session.count == len(sample_ids)
        assert annotation.STATE.filtered_count == len(rows)
        result["sample_rows"] = len(sample_ids)
        result["scope_text"] = panel.view_scope_label.text()
        result["sample_ids"] = sample_ids

        # Compare distinct real structures; the candidate's coordinates cannot move.
        QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: panel._pending_navigation is None and annotation.STATE.index == 1)
        candidate = annotation.STATE.current_object
        candidate_path = Path(annotation.STATE.current_entry().source_path)
        reference_path = Path(annotation.STATE.current_entry().metadata["reference_file"])
        protected = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (candidate_path, reference_path)}
        before = cmd.get_coordset(candidate).copy()
        QTest.mouseClick(panel.reference_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id)
        assert panel._reference_object, panel.error_label.text()
        assert cmd.count_atoms(panel._reference_object) > 100
        assert float(abs(before - cmd.get_coordset(candidate)).max()) == 0
        colors = set()
        cmd.iterate(panel._reference_object, "colors.add(color)", space={"colors": colors})
        assert colors == {cmd.get_color_index("orange")}, colors
        result["reference"] = {"candidate": str(candidate_path), "reference": str(reference_path),
            "atoms": cmd.count_atoms(panel._reference_object), "alignment": panel._reference_alignment,
            "candidate_coordinate_max_error": 0.0}
        window.pymolwidget.repaint()
        app.processEvents()
        frame = window.pymolwidget.grabFrameBuffer()
        colors = {frame.pixel(x, y) for x in range(0, frame.width(), max(1, frame.width() // 80))
                  for y in range(0, frame.height(), max(1, frame.height() // 80))}
        assert len(colors) > 8, "Rendered reference/candidate viewport is blank"
        result["viewport_colors"] = len(colors)
        result["screenshot_boundary"] = "Real QGL framebuffer after explicit repaint; not unattended redraw timing"
        frame.save(
            str(args.output.with_name(args.output.stem + "-scene.png")))

        # Compact form, real asynchronous save and next, then hide old reference.
        resize_workspace(panel, 760, 560)
        QTest.qWait(100)
        panel.review.form.scroll.ensureWidgetVisible(panel._value_buttons[0])
        QTest.mouseClick(panel._value_buttons[0], QtCore.Qt.LeftButton)
        assert panel.review.form.dirty
        saved_id = annotation.STATE.current_entry_id()
        assert panel.review.form.save_button.visibleRegion().isEmpty() is False
        panel.grab().save(str(args.output.with_name(args.output.stem + "-compact.png")))
        QTest.mouseClick(panel.review.form.save_button, QtCore.Qt.LeftButton)
        result["save_started_async"] = panel._csv_task_kind == "annotations"
        wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None
                 and annotation.STATE.current_entry_id() != saved_id)
        assert annotation.STATE.row_annotation_store.get(saved_id, "quality") == "Good"
        assert not panel._reference_object
        result["sample_save_and_next"] = "passed"

        # Same real pair through the existing batch entry; review samples cannot
        # silently restrict (or be mistaken for) the source-table batch scope.
        batch = panel._show_batch_processing()
        assert batch.config().b == "reference_file" and batch.config().mode == "paired"
        assert "NOT transferred" in batch.source_scope_label.text()
        batch.id_column.setCurrentText("relative_path")
        selected_row = next(row for row in rows if row["candidate_file"] == str(candidate_path))
        batch.ids.setText(selected_row["relative_path"])
        batch.output.setText(str(directory / "processed"))
        QTest.mouseClick(batch.preview_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: batch.future is None)
        assert batch.plan is not None, batch.error_box.toPlainText()
        assert len(batch.plan.items) == 1
        QTest.mouseClick(batch.run_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not batch.run_timer.isActive())
        assert batch.runner.completed == 1 and batch.runner.failed == 0, batch.runner.report()
        outputs = batch.plan.items[0]
        for label, path in (("candidate", outputs.output_a), ("reference", outputs.output_b)):
            name = "acceptance_batch_" + label
            cmd.load(path, name, quiet=1, zoom=0)
            assert cmd.count_atoms(name) > 100
            coordinates = cmd.get_coordset(name)
            assert coordinates is not None and len(coordinates) > 100
            shutil.copyfile(path, args.output.with_name(args.output.stem + "-batch-" + label + Path(path).suffix))
            cmd.delete(name)
        shutil.copyfile(batch.plan.report_path, args.output.with_name(args.output.stem + "-batch.json"))
        result["real_structure_batch_handoff"] = {"pairs": 1, "outputs": 2, "scope": batch.source_scope_label.text()}
        batch.close()
        panel.workspace_window.present()
        app.processEvents()

        # Cancel a real view worker before its commit; no sleeps or injected I/O.
        old_ids = [entry.id for entry in annotation.STATE.entries]
        old_object = annotation.STATE.current_object
        panel._apply_view(sampling={"enabled": False})
        assert panel._csv_task_id
        QTest.mouseClick(panel.cancel_task_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id)
        assert [entry.id for entry in annotation.STATE.entries] == old_ids
        assert annotation.STATE.current_object == old_object
        result["real_view_cancel_preserves_sample"] = "passed"

        # Existing Filter UI keeps full-data overview separate from filtered sampling.
        QTest.mouseClick(panel.filter_button, QtCore.Qt.LeftButton)
        panel.filter_mode_combo.setCurrentIndex(1)
        panel.filter_edit.setText("seed == 67101")
        QTest.mouseClick(panel.apply_view_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id)
        expected_filtered = sum(row["seed"] == "67101" for row in rows)
        assert annotation.STATE.filtered_count == expected_filtered
        assert len(annotation.STATE.entries) == 10
        result["filtered_sample"] = {"sample": 10, "filtered": expected_filtered, "all": len(rows)}

        def return_to_full():
            dialog = app.activeModalWidget()
            button = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "Return to full filtered set")
            QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtCore.QTimer.singleShot(100, return_to_full)
        QTest.mouseClick(panel.overview_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id)
        assert len(annotation.STATE.entries) == expected_filtered
        result["return_to_full_filtered"] = "passed"
        QTest.mouseClick(panel.filter_button, QtCore.Qt.LeftButton)
        panel.filter_edit.clear()
        QTest.mouseClick(panel.apply_view_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id)
        assert len(annotation.STATE.entries) == len(rows)
        assert panel._dataset_model.rowCount() == len(rows)
        result["clear_filter_returns_all"] = "passed"

        # Explicit missing reference error must not disturb the real candidate.
        panel.goto_spin.setValue(len(rows))
        QTest.mouseClick(panel.goto_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: panel._pending_navigation is None and annotation.STATE.index == len(rows) - 1)
        old_object = annotation.STATE.current_object
        QTest.mouseClick(panel.reference_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: not panel._csv_task_id)
        assert panel.error_label.isVisible(), "Missing reference error was hidden"
        assert annotation.STATE.current_object == old_object and cmd.count_atoms(old_object) > 100
        assert not panel._reference_object
        result["missing_reference_error"] = panel.error_label.text()
        panel.error_label.hide()

        # Configure a different reference column through the native choice dialog.
        def choose_reference():
            dialog = app.activeModalWidget()
            dialog.setTextValue("candidate_file")
            buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
            QTest.mouseClick(buttons.button(QtWidgets.QDialogButtonBox.Ok), QtCore.Qt.LeftButton)
        QtCore.QTimer.singleShot(100, choose_reference)
        panel._choose_reference_column()
        assert annotation.STATE.reference_column == "candidate_file"

        flush_writes()
        override = json.loads((directory / "review.override.json").read_text(encoding="utf-8"))
        assert override["reference_column"] == "candidate_file"
        shutil.copyfile(directory / "review.override.json", args.output.with_name(args.output.stem + "-review.override.json"))
        exported = args.output.with_name(args.output.stem + "-export.csv")
        annotation.sa_export_csv(str(exported))
        with exported.open(encoding="utf-8-sig", newline="") as handle:
            exported_rows = list(csv.DictReader(handle, delimiter=annotation.STATE.csv_delimiter))
        assert len(exported_rows) == len(rows)
        assert exported_rows[0]["reference_file"] == rows[0]["reference_file"]
        assert (directory / "review.json").read_text() == json.dumps(review)
        state_path = Path(annotation.STATE.state_path)
        shutil.copyfile(state_path, args.output.with_name(args.output.stem + "-state.json"))
        for sidecar in state_path.parent.glob("*.annotations.jsonl"):
            shutil.copyfile(sidecar, args.output.with_name(args.output.stem + "-annotations.jsonl"))
        open_table(panel, table, app, QTest, QtCore.Qt)
        assert annotation.STATE.reference_column == "candidate_file"
        assert not annotation.STATE.sampling.get("enabled")
        assert annotation.STATE.row_annotation_store.get(saved_id, "quality") == "Good"
        result["reimport_reference_and_saved_label"] = "passed"
        assert hashlib.sha256(args.table.read_bytes()).hexdigest() == original_hash
        for path, digest in protected.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        result["unchanged_source_hashes"] = 1 + len(protected)
        panel.close()
        assert annotation._csv_async_pool().waitForDone(30000)
        flush_writes()
        annotation._delete_managed_objects(False)
    result["status"] = "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"status": "running"}
    try:
        run(args, result)
    except Exception:
        result.update(status="failed", traceback=traceback.format_exc())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)
