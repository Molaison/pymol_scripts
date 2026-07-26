"""Windows bundled-Qt performance gate for the Structure Annotator Scores table."""

from __future__ import annotations

import argparse
import datetime
import gc
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
import traceback
from typing import Dict, List

import psutil


SCHEMA = "pymolplugins.annotator-qt-performance/1.0"
ROWS = 50_000
DISPLAY_COLUMNS = ("score", "iptm", "plddt", "cohort")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _repository_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _rss() -> int:
    gc.collect()
    return int(psutil.Process().memory_info().rss)


def _percentile(values: List[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction) - 1))
    return ordered[index]


def _user_role(qt):
    value = getattr(qt, "UserRole", None)
    if value is not None:
        return value
    return qt.ItemDataRole.UserRole


def run_benchmark(repository: str) -> Dict[str, object]:
    repository = os.path.abspath(repository)
    if repository not in sys.path:
        sys.path.insert(0, repository)

    from pymol import Qt as PymolQt
    from pymol.Qt import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    scratch = tempfile.mkdtemp(prefix="PymolPluginsQtPerf-")
    previous_history = os.environ.get("PYMOLPLUGINS_ANNOTATOR_HISTORY")
    previous_mappings = os.environ.get("PYMOLPLUGINS_ANNOTATOR_MAPPINGS")
    os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = os.path.join(
        scratch, "history.json"
    )
    os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = os.path.join(
        scratch, "mappings.json"
    )
    import pymolplugins.annotation.plugin as annotation

    panel = None
    legacy = None
    try:
        annotation.STATE.files = []
        annotation.STATE.entries = []
        annotation.STATE.all_entries = []
        annotation.STATE.navigation_session = None
        panel = annotation.StructureAnnotatorPanel()
        panel.hide()
        app.processEvents()

        entries = [
            annotation.AnnotationEntry(
                id=f"perf-{index:05d}",
                ordinal=index,
                row_number=index + 2,
                display_name=f"design-{index:05d}.pdb",
                source_path=f"C:\\synthetic\\design-{index:05d}.pdb",
                source_kind="csv",
                metadata={
                    "score": f"{index / ROWS:.6f}",
                    "iptm": f"{(index % 1000) / 1000:.3f}",
                    "plddt": str(50 + index % 50),
                    "cohort": "even" if index % 2 == 0 else "odd",
                },
                availability="available",
            )
            for index in range(ROWS)
        ]
        annotation.STATE.source_mode = "csv"
        annotation.STATE.csv_path = os.path.join(scratch, "synthetic.csv")
        annotation.STATE.display_columns = list(DISPLAY_COLUMNS)
        annotation.STATE.all_entries = entries
        annotation.STATE.entries = entries
        annotation.STATE.files = [entry.source_path for entry in entries]
        annotation.STATE.dataset_generation += 1
        annotation.STATE.navigation_session = annotation._new_navigation_session(entries)
        annotation.STATE.navigation_generation = annotation.STATE.dataset_generation
        annotation.STATE.row_annotation_store = None
        annotation.STATE.index = 0

        data_rss = _rss()
        started = time.perf_counter()
        panel._refresh_dataset_table()
        app.processEvents()
        model_seconds = time.perf_counter() - started
        model_rss = _rss()
        model_extra = max(0, model_rss - data_rss)
        model_row_count = panel._dataset_model.rowCount()
        model_column_count = panel._dataset_model.columnCount()

        timings = []
        indices = [ROWS - 1 - ((index * 233) % ROWS) for index in range(200)]
        for row in indices:
            annotation.STATE.navigation_session.goto(row)
            annotation.STATE.index = row
            started = time.perf_counter()
            panel._refresh_dataset_table()
            app.processEvents()
            timings.append((time.perf_counter() - started) * 1000.0)
        refresh_p95 = _percentile(timings, 0.95)
        refresh_max = max(timings)
        refresh_median = statistics.median(timings)

        panel._dataset_model.clear_rows()
        panel._entry_id_to_active_index = {}
        panel._entries_by_id = {}
        app.processEvents()
        legacy_baseline = _rss()
        legacy = QtWidgets.QTableWidget(0, 8)
        legacy.setUpdatesEnabled(False)
        legacy.setRowCount(ROWS)
        legacy.setColumnCount(8)
        role = _user_role(QtCore.Qt)
        started = time.perf_counter()
        for row, entry in enumerate(entries):
            values = (
                entry.row_number,
                entry.status,
                entry.display_name,
                "",
                entry.metadata["score"],
                entry.metadata["iptm"],
                entry.metadata["plddt"],
                entry.metadata["cohort"],
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(role, entry.id)
                legacy.setItem(row, column, item)
        legacy_seconds = time.perf_counter() - started
        legacy_rss = _rss()
        legacy_extra = max(1, legacy_rss - legacy_baseline)
        reduction = (1.0 - model_extra / legacy_extra) * 100.0

        passed = (
            reduction >= 70.0
            and refresh_p95 < 16.0
            and refresh_max < 50.0
            and model_row_count == ROWS
            and model_column_count == 8
            and isinstance(panel.dataset_table, QtWidgets.QTableView)
            and not isinstance(panel.dataset_table, QtWidgets.QTableWidget)
        )
        return {
            "schema": SCHEMA,
            "status": "pass" if passed else "fail",
            "completed_at_utc": _utc_now(),
            "repository": repository,
            "python": sys.version,
            "platform": platform.platform(),
            "qt_binding": str(PymolQt.PYQT_NAME),
            "rows": ROWS,
            "columns": 8,
            "model_population_seconds": model_seconds,
            "model_row_count": model_row_count,
            "model_column_count": model_column_count,
            "legacy_population_seconds": legacy_seconds,
            "model_extra_rss_bytes": model_extra,
            "legacy_extra_rss_bytes": legacy_extra,
            "rss_reduction_percent": reduction,
            "cursor_refresh_samples": len(timings),
            "cursor_refresh_median_ms": refresh_median,
            "cursor_refresh_p95_ms": refresh_p95,
            "cursor_refresh_max_ms": refresh_max,
        }
    finally:
        if legacy is not None:
            legacy.deleteLater()
        if panel is not None:
            panel.close()
            panel.deleteLater()
        app.processEvents()
        if previous_history is None:
            os.environ.pop("PYMOLPLUGINS_ANNOTATOR_HISTORY", None)
        else:
            os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = previous_history
        if previous_mappings is None:
            os.environ.pop("PYMOLPLUGINS_ANNOTATOR_MAPPINGS", None)
        else:
            os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = previous_mappings
        shutil.rmtree(scratch, ignore_errors=True)


def _write_result(path: str, result: Dict[str, object]) -> None:
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
        result = run_benchmark(args.repository)
    except Exception as exc:
        result = {
            "schema": SCHEMA,
            "status": "fail",
            "completed_at_utc": _utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    _write_result(args.output, result)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
