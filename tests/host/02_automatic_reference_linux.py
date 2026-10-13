"""Real offscreen Qt/PyMOL check using two candidates from a review package.

python tests/host/02_automatic_reference_linux.py TABLE --package ZIP --output JSON
This checks objects and navigation, not rendered pixels or Windows installation.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from zipfile import ZipFile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from annotator_workflow_smoke import wait_for
from annotator_source_review_smoke import open_table
from workspace_helpers import isolate_workspace_settings


def run(args, result):
    global _APPLICATION, _WINDOW, _PANEL
    from pymol import cmd
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _APPLICATION = app
    app.setQuitOnLastWindowClosed(False)
    rows = list(csv.DictReader(args.table.open()))
    selected = []
    references = set()
    for row in rows:
        if row["reference_path"] not in references and row.get("binder_iptm_delta_vs_cluster_best"):
            if "reference" in row["structure_path"] or "optimization_start" in row["structure_path"]:
                continue
            selected.append(row)
            references.add(row["reference_path"])
        if len(selected) == 2:
            break
    assert len(selected) == 2
    paths = [{key: str((args.table.parent / row[key]).resolve())
              for key in ("structure_path", "reference_path")} for row in selected]
    protected = {Path(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                 for row in paths for p in row.values()}
    with tempfile.TemporaryDirectory(prefix="automatic-reference-") as temporary:
        directory = Path(temporary)
        with ZipFile(args.package) as package:
            package.extractall(directory / "installed")
        sys.path.insert(0, str(directory / "installed" / "pymol_plugins"))
        os.environ.update(PYMOLPLUGINS_ANNOTATOR_HISTORY=str(directory / "history.json"),
            PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(directory / "mappings.json"),
            PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(directory / "cache"))
        window = isolate_workspace_settings(directory, mode="docked")
        _WINDOW = window
        import pymolplugins
        import pymolplugins.annotation.plugin as annotation
        result.update(platform=sys.platform, pymol=cmd.get_version()[0],
                      plugin_version=pymolplugins.__version__, package=str(args.package),
                      evidence="Actual ZIP code, Qt widgets and PyMOL objects; no pixel/Windows check")
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        panel = annotation.show_structure_annotator_panel()
        _PANEL = panel
        table = directory / "scores.csv"
        with table.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["structure_path", "reference_path"])
            writer.writeheader()
            writer.writerows([paths[0], paths[0], paths[1], paths[0]])
        (directory / "review.json").write_text(json.dumps({
            "path_column": "structure_path", "steps": [
                {"type": "object", "name": "overlay", "path_column": "reference_path", "fixed": True,
                 "on_load_cmd": "hide everything, {overlay}\nshow cartoon, {overlay}\ncolor orange, {overlay}"},
                {"type": "object", "name": "candidate"}, {"type": "commands"}],
            "step_cmd": "hide everything, {object}\nshow cartoon, {object}\nzoom ({object}) or ({overlay})"}))
        events = []
        originals = {name: getattr(cmd, name) for name in ("load", "enable", "disable", "align")}
        def traced(name):
            def call(*values, **options):
                events.append((name, list(values)))
                return originals[name](*values, **options)
            return call
        with patch.object(cmd, "load", traced("load")), \
                patch.object(cmd, "enable", traced("enable")), \
                patch.object(cmd, "disable", traced("disable")), \
                patch.object(cmd, "align", traced("align")):
            open_table(panel, table, app, QTest, QtCore.Qt)
            wait_for(app, lambda: not panel._csv_task_id and len(cmd.get_names("objects", enabled_only=1)) == 2)
            loads = [event[1] for event in events if event[0] == "load"]
            assert Path(loads[0][0]) == Path(paths[0]["reference_path"])
            assert Path(loads[1][0]) == Path(paths[0]["structure_path"])
            first_overlay = loads[0][1]
            before = cmd.get_coordset(first_overlay).copy()
            result["first_load_order"] = [Path(item[0]).name for item in loads]
            events.clear()
            for index in (1, 2, 3):
                QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
                wait_for(app, lambda: annotation.STATE.index == index and not panel._csv_task_id
                         and panel._pending_navigation is None)
                QTest.qWait(50)
                assert cmd.count_atoms(annotation.STATE.current_object) > 100
                if index == 1:
                    assert not [e for e in events if e[0] in ("enable", "disable") and e[1][0] == first_overlay]
                    assert not [e for e in events if e[0] == "load" and e[1][1] == first_overlay]
                    result["same_object_zero_visibility_changes"] = True
                if index == 2:
                    assert first_overlay in cmd.get_names("objects")
                    assert first_overlay not in cmd.get_names("objects", enabled_only=1)
                if index == 3:
                    assert first_overlay in cmd.get_names("objects", enabled_only=1)
                    assert not [e for e in events if e[0] == "load" and e[1][1] == first_overlay]
                assert not [e for e in events if e[0] == "align"]
                events.clear()
            assert float(abs(before - cmd.get_coordset(first_overlay)).max()) == 0.0
            cmd.load(paths[0]["structure_path"], "coordinate_check", quiet=1, zoom=0)
            assert float(abs(cmd.get_coordset(annotation.STATE.current_object) - cmd.get_coordset("coordinate_check")).max()) == 0.0
            cmd.delete("coordinate_check")
            result.update(revisit_reuses_object=True, no_implicit_alignment=True)
            errors = []
            def edit_steps():
                dialog = app.activeModalWidget()
                try:
                    dialog.steps_list.setCurrentRow(1)
                    QTest.mouseClick(dialog.move_up_button, QtCore.Qt.LeftButton)
                    assert dialog.steps[0]["name"] == "candidate"
                    dialog.steps_list.setCurrentRow(1)
                    dialog.commands_edit.setPlainText("hide everything, {overlay}\nshow cartoon, {overlay}\ncolor magenta, {overlay}")
                    QTest.mouseClick(dialog.apply_button, QtCore.Qt.LeftButton)
                    if dialog.isVisible():
                        raise AssertionError(dialog.error_label.text())
                except Exception:
                    errors.append(traceback.format_exc())
                    dialog.reject()
            QtCore.QTimer.singleShot(100, edit_steps)
            panel._edit_review_steps()
            assert not errors, errors
            wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
            from pymolplugins.annotation.persistence import flush_writes
            flush_writes()
            override = json.loads((directory / "review.override.json").read_text())
            assert override["steps"][0]["name"] == "candidate"
            overlay = next(name for name in cmd.get_names("objects", enabled_only=1)
                           if name not in annotation.STATE.managed_objects)
            colors = set()
            cmd.iterate(overlay, "colors.add(color)", space={"colors": colors})
            assert colors == {cmd.get_color_index("magenta")}, colors
            panel.close()
            cmd.delete("all")  # Isolated test scene only.
            annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
            panel = annotation.show_structure_annotator_panel()
            _PANEL = panel
            events.clear()
            open_table(panel, table, app, QTest, QtCore.Qt)
            wait_for(app, lambda: not panel._csv_task_id and len(cmd.get_names("objects", enabled_only=1)) == 2)
            loads = [event[1] for event in events if event[0] == "load"]
            assert Path(loads[0][0]) == Path(paths[0]["structure_path"])
            assert Path(loads[1][0]) == Path(paths[0]["reference_path"])
            result["gui_reorder_edit_persist_reopen"] = "passed"
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
                   for path, digest in protected.items())
        result["source_files_unchanged"] = len(protected)
        from pymolplugins.annotation.persistence import flush_writes
        flush_writes()
        panel.close()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", type=Path)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"status": "failed"}
    try:
        run(args, result)
        result["status"] = "passed"
    except Exception:
        result["error"] = traceback.format_exc()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    sys.exit(0 if result["status"] == "passed" else 1)
