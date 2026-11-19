"""Read-only bundle report in the existing background-task workflow."""

import json
from pymol.Qt import QtWidgets


class ReviewCheckDialog(QtWidgets.QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Review bundle check")
        self.resize(820, 500)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel(
            f"{report['rows']:,} candidates · {report['paths_checked']:,} paths checked · "
            f"{report['errors']} errors · {report['warnings']} warnings")
        layout.addWidget(self.summary)
        table = QtWidgets.QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Severity", "Candidate / field", "Finding"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        shown = report["issues"][:1000]
        table.setRowCount(len(shown))
        for row, issue in enumerate(shown):
            for column, key in enumerate(("severity", "location", "message")):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(issue[key]))
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(1, 260)
        layout.addWidget(table, 1)
        detail = "\n".join(f"{item['name']}: {item['matches']:,} / {item['total']:,}"
                           for item in report["filters"])
        if len(report["issues"]) > len(shown):
            detail += "\nShowing first 1,000 findings; Export includes all findings."
        layout.addWidget(QtWidgets.QLabel(detail))
        layout.addWidget(QtWidgets.QLabel("Read-only check. Commands were not executed."))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        export = buttons.addButton("Export JSON…", QtWidgets.QDialogButtonBox.ActionRole)
        export.clicked.connect(self.export_report)
        layout.addWidget(buttons)

    def export_report(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export bundle check", "review-check.json", "JSON (*.json)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(self.report, handle, ensure_ascii=False, indent=2)
            except OSError as exc:
                import traceback
                traceback.print_exc()
                QtWidgets.QMessageBox.warning(self, "Export failed", str(exc))
