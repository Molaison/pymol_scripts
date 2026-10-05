"""Explicit browsing and selection of saved annotation configurations."""

import copy
import json

from pymol.Qt import QtWidgets

from .history_store import configuration_snapshots


class HistoryRestoreDialog(QtWidgets.QDialog):
    """Return one chosen snapshot; browsing never loads data or changes settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration history")
        self.resize(900, 580)
        self.snapshot = None
        self.load_dataset = False
        self.snapshots = configuration_snapshots()
        self._search_text = [json.dumps(item, ensure_ascii=False).casefold() for item in self.snapshots]
        self._visible = []
        layout = QtWidgets.QVBoxLayout(self)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search paths, dates, annotation fields or commands")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        self.entries_table = QtWidgets.QTableWidget(0, 3)
        self.entries_table.setHorizontalHeaderLabels(["Last used (UTC)", "Source", "Path"])
        self.entries_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.entries_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.entries_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.entries_table.verticalHeader().hide()
        self.entries_table.horizontalHeader().setStretchLastSection(True)
        self.entries_table.setColumnWidth(0, 165)
        self.entries_table.setColumnWidth(1, 85)
        layout.addWidget(self.entries_table, 3)
        self.detail_text = QtWidgets.QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        layout.addWidget(self.detail_text, 2)
        self.count_label = QtWidgets.QLabel()
        layout.addWidget(self.count_label)
        actions = QtWidgets.QHBoxLayout()
        self.restore_button = self._button("Restore configuration", lambda: self._accept(False))
        self.restore_button.setToolTip("Apply settings and fill import inputs without loading structures")
        self.load_button = self._button("Restore and load", lambda: self._accept(True))
        self.cancel_button = self._button("Cancel", self.reject)
        for button in (self.restore_button, self.load_button, self.cancel_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search_edit.textChanged.connect(self._filter)
        self.entries_table.itemSelectionChanged.connect(self._selection_changed)
        self._filter()

    @staticmethod
    def _button(text, callback):
        button = QtWidgets.QPushButton(text)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        return button

    def _filter(self, *args):
        tokens = self.search_edit.text().casefold().split()
        self._visible = [i for i, text in enumerate(self._search_text) if all(token in text for token in tokens)]
        self.entries_table.clearSelection()
        self.entries_table.setRowCount(len(self._visible))
        for row, index in enumerate(self._visible):
            snapshot = self.snapshots[index]
            stamp = snapshot["recorded_at_utc"].replace("T", " ")[:16]
            csv_mode = snapshot["source_mode"] == "csv"
            values = (stamp, "CSV" if csv_mode else "Directory", snapshot["csv_path"] if csv_mode else snapshot["dataset_dir"])
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                self.entries_table.setItem(row, column, item)
        self.count_label.setText(f"{len(self._visible)} saved configurations. Nothing is restored until you choose an action.")
        if self._visible:
            self.entries_table.selectRow(0)
        self._selection_changed()

    def _selected_snapshot(self):
        rows = self.entries_table.selectionModel().selectedRows()
        return self.snapshots[self._visible[rows[0].row()]] if rows else None

    def _selection_changed(self):
        snapshot = self._selected_snapshot()
        self.restore_button.setEnabled(snapshot is not None)
        self.load_button.setEnabled(snapshot is not None)
        if snapshot is None:
            self.detail_text.setPlainText("No matching configuration.")
            return
        config = snapshot["config"]
        path = snapshot["csv_path"] if snapshot["source_mode"] == "csv" else snapshot["dataset_dir"]
        fields = config.get("annotation_fields") or [{"name": config.get("label_name", "quality"), "required": True}]
        lines = [f"Source: {path}", "", "Annotation fields: " + ", ".join(
            str(field["name"]) + (" (required)" if field.get("required") else " (optional)") for field in fields
        )]
        if snapshot["source_mode"] == "csv":
            lines.extend([f"Path column: {config.get('path_column', 'structure_path')}",
                          "Score columns: " + ", ".join(config.get("display_columns", [])),
                          f"Filter: {config.get('filter_text') or '(none)'}"])
        else:
            lines.append(f"Filename pattern: {config.get('pattern', '')}")
        lines.extend(["", "On step:", config.get("step_cmd", "") or "(none)"])
        self.detail_text.setPlainText("\n".join(lines))

    def _accept(self, load_dataset):
        snapshot = self._selected_snapshot()
        if snapshot is not None:
            self.snapshot = copy.deepcopy(snapshot)
            self.load_dataset = bool(load_dataset)
            self.accept()
