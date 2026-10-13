"""Import the actual complete review CSV, isolating only annotation writes.

python tests/host/04_full_csv_steps_linux.py TABLE --package ZIP --output JSON
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from annotator_workflow_smoke import wait_for
from workspace_helpers import isolate_workspace_settings


def run(args, result):
    global _APP, _WINDOW, _PANEL
    from pymol import cmd
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    _APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    source_bytes = args.table.read_bytes()
    rows = list(csv.DictReader(source_bytes.decode("utf-8-sig").splitlines()))
    review_file = args.table.parent / "review.json"
    review_bytes = review_file.read_bytes()
    result.update(source=str(args.table), rows=len(rows), table_sha256=hashlib.sha256(source_bytes).hexdigest())
    with tempfile.TemporaryDirectory(prefix="pymol-full-csv-") as temporary:
        directory = Path(temporary)
        with ZipFile(args.package) as archive:
            archive.extractall(directory / "installed")
        sys.path.insert(0, str(directory / "installed/pymol_plugins"))
        os.environ.update(PYMOLPLUGINS_ANNOTATOR_HISTORY=str(directory / "history.json"),
            PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(directory / "mappings.json"),
            PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(directory / "cache"))
        _WINDOW = isolate_workspace_settings(directory)
        import pymolplugins
        import pymolplugins.annotation.plugin as annotation
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        _PANEL = panel = annotation.show_structure_annotator_panel()
        result.update(plugin_version=pymolplugins.__version__, pymol=cmd.get_version()[0])
        events = []
        originals = {name: getattr(cmd, name) for name in ("load", "enable", "disable", "align", "do")}
        def traced(name):
            def call(*values, **options):
                events.append((name, list(values)))
                return originals[name](*values, **options)
            return call
        with patch.object(annotation, "_write_persisted_dataset_state", return_value=None), \
                patch.object(cmd, "load", traced("load")), \
                patch.object(cmd, "enable", traced("enable")), \
                patch.object(cmd, "disable", traced("disable")), \
                patch.object(cmd, "align", traced("align")), \
                patch.object(cmd, "do", traced("do")):
            panel._show_import()
            panel.source_mode_combo.setCurrentIndex(panel.source_mode_combo.findData("csv"))
            panel.csv_edit.setText(str(args.table))
            start = time.monotonic()
            QTest.mouseClick(panel.csv_load_btn, QtCore.Qt.LeftButton)
            try:
                wait_for(_APP, lambda: annotation.STATE.dataset is not None
                         and not panel._csv_task_id and not panel._pending_csv_action, timeout=90)
                wait_for(_APP, lambda: len(cmd.get_names("objects", enabled_only=1)) >= 2, timeout=30)
            finally:
                result["ui_state"] = {"status": panel.status_label.text(), "error": panel.error_label.text(),
                    "task": panel._csv_task_kind, "index": annotation.STATE.index,
                    "reference_column": annotation.STATE.reference_column,
                    "objects": cmd.get_names("objects"), "enabled": cmd.get_names("objects", enabled_only=1)}
            result["import_seconds"] = time.monotonic() - start
            assert len(annotation.STATE.all_entries) == len(rows)
            entry = annotation.STATE.current_entry()
            result["first_row"] = dict(entry.metadata)
            result["active_rows"] = len(annotation.STATE.entries)
            result["first_events"] = list(events)
            references = [name for name in cmd.get_names("objects") if name not in annotation.STATE.managed_objects]
            assert len(references) == 1, references
            reference = references[0]
            before = cmd.get_coordset(reference).copy()
            events.clear()
            QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
            wait_for(_APP, lambda: annotation.STATE.index == 1 and not panel._csv_task_id
                     and panel._pending_navigation is None, timeout=30)
            QTest.qWait(200)
            result["next_events"] = list(events)
            result["same_reference_retained"] = reference in cmd.get_names("objects")
            result["reference_coordinate_error"] = float(abs(before - cmd.get_coordset(reference)).max())
            result["reference_visibility_calls"] = [event for event in events
                if event[0] in ("enable", "disable") and event[1][0] == reference]
            if args.configured:
                assert result["first_row"]["row_type"] == "candidate"
                assert result["active_rows"] == sum(row["row_type"] == "candidate" for row in rows)
                loads = [event[1] for event in result["first_events"] if event[0] == "load"]
                assert Path(loads[0][0]).name == Path(result["first_row"]["reference_path"]).name
                assert Path(loads[1][0]).name == Path(result["first_row"]["structure_path"]).name
                assert result["reference_visibility_calls"] == [], result["reference_visibility_calls"]
                assert not any(event[0] == "load" and event[1][1] == reference for event in events)
                assert result["reference_coordinate_error"] == 0.0
                result["configured_order_and_fixed_reuse"] = "passed"
            assert args.table.read_bytes() == source_bytes
            assert review_file.read_bytes() == review_bytes
            result["input_table_and_config_unchanged"] = True
            panel.close()
            from pymolplugins.annotation.persistence import flush_writes
            flush_writes()
            _WINDOW.close()
            _APP.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", type=Path)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--configured", action="store_true")
    args = parser.parse_args()
    result = {"status": "failed"}
    try:
        run(args, result)
        result["status"] = "passed"
    except Exception:
        result["error"] = traceback.format_exc()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    sys.exit(0 if result["status"] == "passed" else 1)
