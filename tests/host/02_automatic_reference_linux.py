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
        window = isolate_workspace_settings(directory)
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
            writer.writerows(paths + [dict(paths[0], reference_path=""), paths[0]])
        (directory / "review.json").write_text(json.dumps({
            "path_column": "structure_path", "reference_column": "reference_path",
            "step_cmd": "hide everything, {object}\nshow cartoon, {object}\nzoom {object}"}))
        open_table(panel, table, app, QTest, QtCore.Qt)
        wait_for(app, lambda: bool(panel._reference_object) and not panel._csv_task_id)

        def check_pair(index):
            candidate, reference = annotation.STATE.current_object, panel._reference_object
            assert panel._reference_owner == candidate
            assert cmd.count_atoms(reference) > 100
            assert candidate in cmd.get_names("objects", enabled_only=1)
            assert reference in cmd.get_names("objects", enabled_only=1)
            cmd.load(paths[index]["reference_path"], "coordinate_check", quiet=1, zoom=0)
            error = float(abs(cmd.get_coordset(reference) - cmd.get_coordset("coordinate_check")).max())
            cmd.delete("coordinate_check")
            assert error == 0.0, error
            colors = set()
            cmd.iterate(reference, "colors.add(color)", space={"colors": colors})
            assert colors == {cmd.get_color_index("orange")}
            return dict(paths[index], candidate_atoms=cmd.count_atoms(candidate),
                        reference_atoms=cmd.count_atoms(reference), reference_coordinate_error=error)

        result["first_import"] = check_pair(0)
        old_reference = panel._reference_object
        QTest.mouseClick(panel.reference_button, QtCore.Qt.LeftButton)
        for _ in range(3):
            panel._refresh_status()
            QTest.qWait(30)
        assert not panel._reference_object and not panel._csv_task_id
        assert old_reference in cmd.get_names("objects")
        assert old_reference not in cmd.get_names("objects", enabled_only=1)
        result["manual_hide_stays_hidden"] = True
        QTest.mouseClick(panel.reference_button, QtCore.Qt.LeftButton)
        wait_for(app, lambda: bool(panel._reference_object) and not panel._csv_task_id)
        first_reference = panel._reference_object
        assert first_reference == old_reference
        QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: annotation.STATE.index == 1 and bool(panel._reference_object)
                 and panel._reference_owner == annotation.STATE.current_object and not panel._csv_task_id)
        assert old_reference in cmd.get_names("objects")
        assert old_reference not in cmd.get_names("objects", enabled_only=1)
        result["next_step"] = check_pair(1)
        old_reference = panel._reference_object
        QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: annotation.STATE.index == 2 and panel._pending_navigation is None)
        QTest.qWait(100)
        assert not panel._reference_object and not panel._csv_task_id
        assert old_reference in cmd.get_names("objects")
        assert old_reference not in cmd.get_names("objects", enabled_only=1)
        result["blank_reference_disables_previous"] = True
        QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
        wait_for(app, lambda: annotation.STATE.index == 3 and bool(panel._reference_object)
                 and panel._reference_owner == annotation.STATE.current_object and not panel._csv_task_id)
        assert panel._reference_object == first_reference
        assert old_reference not in cmd.get_names("objects", enabled_only=1)
        result["revisit_reuses_fixed_object"] = check_pair(0)
        result["retained_reference_objects"] = len([name for name in cmd.get_names("objects")
                                                  if name.startswith("sa_reference_")])
        assert result["retained_reference_objects"] == 2
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
