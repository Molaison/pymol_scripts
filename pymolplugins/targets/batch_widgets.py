"""File/CSV batch processing dialog with main-thread PyMOL execution."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import traceback

from pymol.Qt import QtCore, QtGui, QtWidgets

from pymolplugins.annotation.mapping_store import read_mapping_store_readonly
from pymolplugins.annotation.panel_style import apply_style
from pymolplugins.annotation.qt_widgets import CollapsibleSection
from pymolplugins.annotation.sources.csv_source import read_csv_header
from pymolplugins.host.workspace import WindowModeButton, show_workspace, workspace_closed
from .batch_runner import BatchRunner
from .batch_sources import BatchConfig, native_path, prepare_batch


_DIALOG = None
_PRESETS = {
    "Align": "align {A}, {B}",
    "Superpose": "super {A}, {B}",
    "RMSD without fitting": "rms_cur {A}, {B}",
}


class PairTableModel(QtCore.QAbstractTableModel):
    headers = ("ID", "A", "B", "Status", "Last step", "Error")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            return self.headers[section] if orientation == QtCore.Qt.Horizontal else section + 1

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == QtCore.Qt.ToolTipRole and index.column() == 4:
            return "\n".join(row.get("commands", row["steps"]))
        if role == QtCore.Qt.ToolTipRole and index.column() in (1, 2):
            return row["A" if index.column() == 1 else "B"] + "\n" + "\n".join(row.get("planned_outputs", []))
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            values = (row["id"], row["A"], row["B"], row["status"],
                      row.get("failed_step") or (row["steps"][-1] if row["steps"] else ""), row.get("error", ""))
            return str(values[index.column()])
        if role == QtCore.Qt.ForegroundRole and row["status"] == "failed":
            return QtGui.QBrush(QtGui.QColor("#982e36"))

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def refresh_rows(self, start, end):
        if self.rows:
            self.dataChanged.emit(self.index(max(0, start), 0), self.index(min(end, len(self.rows) - 1), 5))


def _line_with_browse(dialog, callback):
    line = QtWidgets.QLineEdit()
    button = QtWidgets.QToolButton()
    button.setIcon(dialog.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon))
    button.setToolTip("Browse")
    button.setProperty("role", "icon")
    button.setFixedSize(34, 34)
    button.clicked.connect(callback)
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(line, 1)
    layout.addWidget(button)
    return line, row


def _columns(dialog, form):
    dialog.column_box = QtWidgets.QWidget()
    columns = QtWidgets.QHBoxLayout(dialog.column_box)
    columns.setContentsMargins(0, 0, 0, 0)
    dialog.a_column = QtWidgets.QComboBox()
    dialog.id_column = QtWidgets.QComboBox()
    for title, widget in (("A column", dialog.a_column), ("ID column", dialog.id_column)):
        widget.setEditable(True)
        widget.setMinimumContentsLength(10)
        widget.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        columns.addWidget(QtWidgets.QLabel(title))
        columns.addWidget(widget, 1)
    dialog.headers_button = QtWidgets.QToolButton()
    dialog.headers_button.setIcon(dialog.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
    dialog.headers_button.setToolTip("Read CSV columns")
    dialog.headers_button.setProperty("role", "icon")
    dialog.headers_button.setFixedSize(34, 34)
    dialog.headers_button.clicked.connect(dialog.read_headers)
    columns.addWidget(dialog.headers_button)
    form.addRow(dialog.column_box)


def _path_options(dialog, form):
    dialog.location_section = CollapsibleSection("File locations")
    dialog.location_toggle = dialog.location_section.toggle
    dialog.location_box = dialog.location_section.content
    advanced = QtWidgets.QFormLayout(dialog.location_box)
    advanced.setContentsMargins(0, 8, 0, 0)
    advanced.setSpacing(14)
    dialog.profile = QtWidgets.QComboBox()
    dialog.profile.setEditable(True)
    dialog.profile.addItem("custom")
    for profile in read_mapping_store_readonly()["profiles"]:
        if profile["name"] != "custom":
            dialog.profile.addItem(profile["name"])
    dialog.base = QtWidgets.QLineEdit()
    dialog.target_format = QtWidgets.QComboBox()
    dialog.target_format.addItems(["custom", "windows", "posix", "wsl"])
    dialog.distribution = QtWidgets.QLineEdit()
    dialog.encoding = QtWidgets.QLineEdit("utf-8-sig")
    dialog.delimiter = QtWidgets.QComboBox()
    dialog.delimiter.addItems(["auto", "comma", "tab", "semicolon", "pipe"])
    for title, widget in (("Server profile", dialog.profile), ("Base folder", dialog.base),
                          ("This computer's format", dialog.target_format),
                          ("WSL distribution", dialog.distribution),
                          ("CSV encoding", dialog.encoding), ("Separator", dialog.delimiter)):
        advanced.addRow(title, widget)
    form.addRow(dialog.location_section)


def _input_page(dialog):
    page = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(page)
    form.setContentsMargins(0, 14, 8, 8)
    form.setSpacing(14)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
    selectors = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(selectors)
    row.setContentsMargins(0, 0, 0, 0)
    dialog.source = QtWidgets.QComboBox()
    dialog.source.addItem("Folder / file patterns", "files")
    dialog.source.addItem("CSV table", "csv")
    dialog.mode = QtWidgets.QComboBox()
    dialog.mode.addItem("All against reference", "reference")
    dialog.mode.addItem("A / B by ID", "paired")
    row.addWidget(dialog.source, 1)
    row.addWidget(dialog.mode, 1)
    form.addRow("Input / pairing", selectors)
    _pattern_options(dialog, form)
    dialog.a_edit, a_row = _line_with_browse(dialog, dialog.browse_a)
    dialog.a_label = QtWidgets.QLabel("A files")
    form.addRow(dialog.a_label, a_row)
    _columns(dialog, form)
    dialog.b_edit, dialog.b_file_row = _line_with_browse(dialog, dialog.browse_b)
    dialog.b_column = QtWidgets.QComboBox()
    dialog.b_column.setEditable(True)
    dialog.b_stack = QtWidgets.QStackedWidget()
    dialog.b_stack.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    dialog.b_stack.addWidget(dialog.b_file_row)
    dialog.b_stack.addWidget(dialog.b_column)
    dialog.b_label = QtWidgets.QLabel("Reference file")
    form.addRow(dialog.b_label, dialog.b_stack)
    dialog.ids = QtWidgets.QLineEdit()
    dialog.ids.setPlaceholderText("1-20,25")
    dialog.ids.setToolTip("Empty selects all IDs. Comma-separated IDs and numeric ranges are accepted.")
    form.addRow("IDs", dialog.ids)
    dialog.output, output_row = _line_with_browse(dialog, dialog.browse_output)
    form.addRow("Output directory", output_row)
    dialog.save_b = QtWidgets.QCheckBox("Also save processed B")
    dialog.on_error = QtWidgets.QComboBox()
    dialog.on_error.addItem("Stop on error", "stop")
    dialog.on_error.addItem("Continue with next pair", "continue")
    form.addRow(dialog.save_b, dialog.on_error)
    _path_options(dialog, form)
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    area.setWidget(page)
    return area


def _pattern_options(dialog, form):
    dialog.match_box = QtWidgets.QWidget()
    layout = QtWidgets.QFormLayout(dialog.match_box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(14)
    dialog.match = QtWidgets.QComboBox()
    for label, value in (("ID template", "template"), ("Wildcard", "glob"), ("Regular expression", "regex")):
        dialog.match.addItem(label, value)
    dialog.match.setToolTip("Template: {id}. Wildcard: one * captures the ID; {id} also allows other wildcards.")
    layout.addRow("Filename matching", dialog.match)
    dialog.regex_box = QtWidgets.QWidget()
    fields = QtWidgets.QFormLayout(dialog.regex_box)
    fields.setContentsMargins(0, 0, 0, 0)
    fields.setSpacing(14)
    dialog.a_pattern, dialog.b_pattern = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
    for title, edit in (("A expression", dialog.a_pattern), ("B expression", dialog.b_pattern)):
        edit.setToolTip("Full filename regex, with (?P<id>...) or exactly one capture group. A/B inputs are directories.")
        fields.addRow(title, edit)
    layout.addRow(dialog.regex_box)
    form.addRow(dialog.match_box)


def _command_page(dialog):
    page = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(page)
    layout.setContentsMargins(0, 14, 0, 0)
    layout.setSpacing(14)
    dialog.preset = QtWidgets.QComboBox()
    dialog.preset.addItems(list(_PRESETS) + ["Custom"])
    dialog.commands = QtWidgets.QPlainTextEdit(_PRESETS["Align"])
    dialog.commands.setObjectName("commandEditor")
    dialog.commands.setToolTip("One PyMOL command per line. Fields: {A}, {B}, {id}, {index}, "
                               "{A_source}, {B_source}, {output_A}, {output_B}, {output_dir}.")
    dialog.commands.setMinimumHeight(100)
    layout.addWidget(dialog.preset)
    layout.addWidget(dialog.commands, 1)
    dialog.preset.activated.connect(lambda index: dialog.commands.setPlainText(
        _PRESETS[dialog.preset.itemText(index)]) if dialog.preset.itemText(index) in _PRESETS else None)
    return page


def _footer(dialog, layout):
    dialog.summary = QtWidgets.QLabel("Not prepared")
    dialog.summary.setProperty("role", "muted")
    dialog.summary.setWordWrap(True)
    dialog.progress = QtWidgets.QProgressBar()
    dialog.progress.setRange(0, 1)
    dialog.progress.setValue(0)
    dialog.progress.setTextVisible(False)
    dialog.progress.setFixedHeight(3)
    layout.addWidget(dialog.summary)
    layout.addWidget(dialog.progress)
    dialog.error_box = QtWidgets.QPlainTextEdit()
    dialog.error_box.setProperty("role", "error")
    dialog.error_box.setReadOnly(True)
    dialog.error_box.setMaximumHeight(80)
    dialog.error_box.hide()
    layout.addWidget(dialog.error_box)
    row = QtWidgets.QHBoxLayout()
    for name, title, icon, callback in (
        ("preview_button", "Preview pairs", QtWidgets.QStyle.SP_FileDialogDetailedView, dialog.preview),
        ("stop_button", "Stop", QtWidgets.QStyle.SP_MediaStop, dialog.stop),
        ("export_button", "Export results", QtWidgets.QStyle.SP_DialogSaveButton, dialog.export_results),
        ("run_button", "Run batch", QtWidgets.QStyle.SP_MediaPlay, dialog.run_batch),
    ):
        button = QtWidgets.QPushButton(dialog.style().standardIcon(icon), title)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        setattr(dialog, name, button)
        if name == "run_button":
            row.addStretch()
            button.setProperty("role", "primary")
        row.addWidget(button)
    layout.addLayout(row)
    dialog.run_button.setEnabled(False)
    dialog.stop_button.setEnabled(False)
    dialog.export_button.setEnabled(False)


class BatchDialog(QtWidgets.QWidget):
    def __init__(self, command_api, execute, initial=None, parent=None):
        super().__init__(parent)
        self._closed = False
        apply_style(self, "batchWorkspace")
        self.cmd, self.execute = command_api, execute
        self.plan, self.runner, self.future = None, None, None
        self.cancel_event, self.future_kind = None, ""
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pymol-batch-input")
        self.setWindowTitle("Batch processing")
        self.resize(1000, 720)
        self.setMinimumSize(700, 560)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(14)
        self.source_scope_label = QtWidgets.QLabel()
        self.source_scope_label.setWordWrap(True)
        self.source_scope_label.setProperty("role", "muted")
        self.source_scope_label.hide()
        layout.addWidget(self.source_scope_label)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.tabs = QtWidgets.QTabWidget()
        self.window_mode_button = WindowModeButton(self)
        self.tabs.setCornerWidget(self.window_mode_button)
        self.tabs.addTab(_input_page(self), "Inputs")
        self.tabs.addTab(_command_page(self), "Commands per pair")
        self.table = QtWidgets.QTableView()
        self.table.setMinimumHeight(112)
        self.model = PairTableModel(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((65, 220, 220, 100, 160, 180)):
            self.table.setColumnWidth(index, width)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([400, 175])
        layout.addWidget(splitter, 1)
        _footer(self, layout)
        self.prepare_timer = QtCore.QTimer(self)
        self.prepare_timer.setInterval(30)
        self.prepare_timer.timeout.connect(self._poll_preparation)
        self.run_timer = QtCore.QTimer(self)
        self.run_timer.setInterval(0)
        self.run_timer.timeout.connect(self._advance)
        self._set_initial(initial or BatchConfig())
        self.source.currentIndexChanged.connect(self._mode_changed)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.match.currentIndexChanged.connect(self._mode_changed)
        for edit in self.findChildren(QtWidgets.QLineEdit):
            edit.textChanged.connect(self.invalidate)
        for combo in (self.source, self.mode, self.match, self.a_column, self.id_column, self.b_column,
                      self.on_error, self.profile, self.target_format, self.delimiter):
            combo.currentTextChanged.connect(self.invalidate)
        self.save_b.toggled.connect(self.invalidate)
        self.commands.textChanged.connect(self._commands_changed)
        self._mode_changed()
        self._commands_changed()

    def _commands_changed(self):
        text = self.commands.toPlainText().strip()
        self.preset.setCurrentText(next((name for name, value in _PRESETS.items() if value == text), "Custom"))
        self.invalidate()

    def _set_initial(self, config):
        self.source.setCurrentIndex(self.source.findData(config.source))
        self.mode.setCurrentIndex(self.mode.findData(config.mode))
        self.match.setCurrentIndex(self.match.findData(config.match))
        for widget, value in ((self.a_edit, config.a), (self.b_edit, config.b),
                              (self.output, config.output), (self.ids, config.ids),
                              (self.base, config.source_base), (self.encoding, config.encoding),
                              (self.distribution, config.wsl_distribution),
                              (self.a_pattern, config.a_pattern), (self.b_pattern, config.b_pattern)):
            widget.setText(value)
        self.a_column.setCurrentText(config.a_column)
        self.id_column.setCurrentText(config.id_column)
        self.profile.setCurrentText(config.mapping_profile)
        self.target_format.setCurrentText(config.target_format)
        self.delimiter.setCurrentText(config.delimiter)
        self.commands.setPlainText(config.commands)
        self.save_b.setChecked(config.save_b)
        self.on_error.setCurrentIndex(self.on_error.findData(config.on_error))
        if config.source == "csv" and config.mode == "paired":
            self.b_column.setCurrentText(config.b)

    def config(self):
        csv = self.source.currentData() == "csv"
        paired = self.mode.currentData() == "paired"
        return BatchConfig(a=self.a_edit.text().strip(),
                           b=self.b_column.currentText() if csv and paired else self.b_edit.text().strip(),
                           source=self.source.currentData(), mode=self.mode.currentData(),
                           match=self.match.currentData(), a_pattern=self.a_pattern.text(), b_pattern=self.b_pattern.text(),
                           commands=self.commands.toPlainText(), output=self.output.text().strip(),
                           ids=self.ids.text().strip(), a_column=self.a_column.currentText(),
                           id_column=self.id_column.currentText(), save_b=self.save_b.isChecked(),
                           on_error=self.on_error.currentData(), mapping_profile=self.profile.currentText(),
                           source_base=self.base.text().strip(), target_format=self.target_format.currentText(),
                           wsl_distribution=self.distribution.text().strip(), encoding=self.encoding.text(),
                           delimiter=self.delimiter.currentText())

    def _mode_changed(self, *args):
        csv, paired = self.source.currentData() == "csv", self.mode.currentData() == "paired"
        regex = paired and not csv and self.match.currentData() == "regex"
        self.match_box.setVisible(paired and not csv)
        self.regex_box.setVisible(regex)
        self.a_label.setText("CSV file" if csv else "A directory" if regex else "A pattern" if paired else "A files")
        self.b_label.setText("B column" if csv and paired else "B directory" if regex else "B pattern" if paired else "Reference file")
        self.column_box.setVisible(csv)
        self.b_stack.setCurrentIndex(1 if csv and paired else 0)
        marker = "*" if self.match.currentData() == "glob" else "{id}"
        self.a_edit.setPlaceholderText("scores.csv" if csv else "" if regex else "A_%s.pdb" % marker if paired else "*.pdb")
        self.b_edit.setPlaceholderText("" if regex else "B_%s.pdb" % marker if paired else "reference.pdb")
        self.location_section.setVisible(csv)
        self.location_section.set_expanded(self.location_toggle.isChecked())
        self.invalidate()

    def invalidate(self, *args):
        if self.runner is not None and self.runner.running:
            return
        self.plan = None
        self.run_button.setEnabled(False)
        self.summary.setText("Not prepared")

    def _busy(self, busy):
        for index in range(self.tabs.count()):
            self.tabs.widget(index).setEnabled(not busy)
        self.preview_button.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self.plan is not None and self.runner is None)
        self.stop_button.setEnabled(busy)

    def _error(self, exc):
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        self.error_box.setPlainText(str(exc))
        self.error_box.show()

    def _prepare(self, kind, function, *args, **kwargs):
        self.error_box.hide()
        self.future_kind = kind
        self.future = self.pool.submit(function, *args, **kwargs)
        self._busy(True)
        self.summary.setText("Reading columns" if kind == "headers" else "Preparing pairs")
        self.prepare_timer.start()

    def read_headers(self):
        config = self.config()
        self.cancel_event = threading.Event()
        self._prepare("headers", read_csv_header, native_path(config.a),
                      encoding=config.encoding, delimiter=config.delimiter)

    def preview(self):
        self.runner = None
        self.plan = None
        self.model.set_rows([])
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.export_button.setEnabled(False)
        self.cancel_event = threading.Event()
        self._prepare("pairs", prepare_batch, self.config(), self.cancel_event)

    def _poll_preparation(self):
        if not self.future.done():
            return
        self.prepare_timer.stop()
        future, self.future = self.future, None
        try:
            result = future.result()
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.summary.setText("Cancelled")
            elif self.future_kind == "headers":
                for combo in (self.a_column, self.id_column, self.b_column):
                    selected = combo.currentText()
                    combo.clear()
                    combo.addItem("")
                    combo.addItems(list(result["columns"]))
                    combo.setCurrentText(selected)
                self.summary.setText("%d columns" % len(result["columns"]))
            else:
                self.plan = result
                rows = [dict(id=item.pair.identifier, A=item.pair.left, B=item.pair.right,
                             status="ready", steps=[command for _, command in item.commands], error="",
                             planned_outputs=[path for path in (item.output_a, item.output_b) if path])
                        for item in result.items]
                self.model.set_rows(rows)
                self.summary.setText("%d pairs ready" % len(rows))
        except InterruptedError:
            self.summary.setText("Cancelled")
        except Exception as exc:
            self._error(exc)
            self.summary.setText("Preparation failed")
        self._busy(False)

    def run_batch(self):
        if self.plan is None:
            return
        self.error_box.hide()
        self.runner = BatchRunner(self.plan, self.cmd, self.execute)
        try:
            self.runner.start()
        except Exception as exc:
            self.runner.abort(exc)
            self._error(exc)
            self.summary.setText("Batch did not start")
            self.runner = None
            return
        self.model.set_rows(self.runner.results)
        self.progress.setRange(0, len(self.plan.items))
        self.progress.setValue(0)
        self.export_button.setEnabled(False)
        self._busy(True)
        self.run_timer.start()

    def _advance(self):
        before = self.runner.index
        try:
            running = self.runner.advance()
        except Exception as exc:
            self.runner.abort(exc)
            self.run_timer.stop()
            self._busy(False)
            self.export_button.setEnabled(True)
            self._error(exc)
            self.summary.setText("Batch/report failure")
            return
        self.model.refresh_rows(before, self.runner.index)
        self.progress.setValue(self.runner.completed + self.runner.failed)
        self.summary.setText("%s: %d / %d completed, %d failed" %
                             (self.runner.status, self.runner.completed, len(self.plan.items), self.runner.failed))
        if not running:
            report = self.runner.report()
            self.run_timer.stop()
            self._busy(False)
            self.export_button.setEnabled(True)
            errors = [row for row in report["results"] if row["status"] == "failed"]
            if report["error"]:
                self.error_box.setPlainText(report["error"])
                self.error_box.show()
            elif errors:
                self.error_box.setPlainText("ID %s: %s\n%s" %
                                           (errors[0]["id"], errors[0]["failed_step"], errors[0]["error"]))
                self.error_box.show()

    def stop(self):
        if self.future is not None:
            if self.cancel_event is None:
                self.cancel_event = threading.Event()
            self.cancel_event.set()
            self.summary.setText("Stopping preparation")
        elif self.runner is not None and self.runner.running:
            self.runner.cancel()
            self.summary.setText("Stopping after current step")

    def browse_a(self):
        if self.source.currentData() == "csv":
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "CSV table", "", "Tables (*.csv *.tsv);;All files (*)")
        else:
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "A structure directory")
        if path:
            if (self.source.currentData() == "files" and self.mode.currentData() == "paired"
                    and self.match.currentData() != "regex"):
                path = str(Path(path) / ("A_*.pdb" if self.match.currentData() == "glob" else "A_{id}.pdb"))
            self.a_edit.setText(path)
            if self.source.currentData() == "csv":
                self.read_headers()

    def browse_b(self):
        if self.mode.currentData() == "paired":
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "B structure directory")
            if path and self.match.currentData() != "regex":
                path = str(Path(path) / ("B_*.pdb" if self.match.currentData() == "glob" else "B_{id}.pdb"))
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Reference structure", "", "Structures (*.pdb *.cif *.mmcif *.pdb.gz *.cif.gz);;All files (*)")
        if path:
            self.b_edit.setText(path)

    def browse_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory")
        if path:
            self.output.setText(path)

    def export_results(self):
        if self.runner is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export results", "", "JSON (*.json)")
        if path:
            try:
                with open(native_path(path), "x", encoding="utf-8") as handle:
                    json.dump(self.runner.report(), handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
            except Exception as exc:
                self._error(exc)

    def closeEvent(self, event):
        global _DIALOG
        if self._closed:
            super().closeEvent(event)
            return
        if self.runner is not None and self.runner.running:
            self.stop()
            event.ignore()
            return
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.prepare_timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self._closed = True
        if _DIALOG is self:
            _DIALOG = None
        super().closeEvent(event)
        workspace_closed(self)


def show_batch_dialog(initial=None, mode=None):
    """Open the batch tool without loading data or modifying the current scene."""
    global _DIALOG
    from .commands import _execute_checked, _require_cmd
    if _DIALOG is None:
        _DIALOG = BatchDialog(_require_cmd(), _execute_checked, initial)
        try:
            return show_workspace(_DIALOG, "batch_processing", mode)
        except Exception:
            _DIALOG.close()
            raise
    return show_workspace(_DIALOG, "batch_processing", mode)
