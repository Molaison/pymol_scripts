"""Profile editing and path diagnostics, separate from the active review."""

import traceback

from pymol.Qt import QtCore, QtWidgets

from .mapping_store import read_mapping_store_readonly, remove_mapping_profile, upsert_mapping_profile, validate_mapping_profile
from .path_resolver import PathResolver
from .panel_style import StatusLabel


def format_combo():
    combo = QtWidgets.QComboBox()
    for label, value in (("Custom", "custom"), ("Windows", "windows"), ("Linux / POSIX", "posix"), ("WSL (Linux)", "wsl")):
        combo.addItem(label, value)
    return combo


def set_format(combo, value):
    combo.setCurrentIndex(max(0, combo.findData(value)))


def profiles():
    values = read_mapping_store_readonly()["profiles"]
    if not any(item["name"].casefold() == "custom" for item in values):
        values.insert(0, dict(name="custom", description="Use paths as written", rules=[], rewrites=[],
                              source_format="custom", target_format="custom", wsl_distribution=""))
    return values


def _button(text, callback):
    button = QtWidgets.QPushButton(text)
    button.setAutoDefault(False)
    button.clicked.connect(callback)
    return button


def _row(*widgets):
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    for child in widgets:
        layout.addWidget(child)
    return widget


def _table(headers):
    table = QtWidgets.QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setWordWrap(False)
    table.setTextElideMode(QtCore.Qt.ElideMiddle)
    table.verticalHeader().setDefaultSectionSize(36)
    table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    return table


def _cell(table, row, column, value):
    if type(value) is bool:
        widget = QtWidgets.QCheckBox()
        widget.setChecked(value)
        table.setCellWidget(row, column, widget)
    else:
        item = QtWidgets.QTableWidgetItem(str(value))
        item.setToolTip(str(value))
        table.setItem(row, column, item)


def _text(table, row, column):
    item = table.item(row, column)
    return item.text() if item is not None else ""


def _profile_fields(dialog, layout):
    dialog.profile_combo = QtWidgets.QComboBox()
    dialog.new_button = _button("New", dialog._new)
    dialog.copy_button = _button("Duplicate", dialog._duplicate)
    dialog.delete_button = _button("Delete profile", dialog._delete)
    for button in (dialog.new_button, dialog.copy_button, dialog.delete_button):
        button.setProperty("role", "quiet")
        button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
    dialog.profile_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    layout.addWidget(_row(dialog.profile_combo, dialog.new_button, dialog.copy_button, dialog.delete_button))
    form = QtWidgets.QFormLayout()
    dialog.name_edit, dialog.description_edit = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
    dialog.source_format, dialog.target_format = format_combo(), format_combo()
    dialog.distribution_edit = QtWidgets.QLineEdit()
    dialog.distribution_edit.setPlaceholderText("For example Debian or Ubuntu")
    dialog.distribution_row = _row(QtWidgets.QLabel("WSL distribution:"), dialog.distribution_edit)
    dialog.source_format.currentIndexChanged.connect(dialog._format_changed)
    dialog.target_format.currentIndexChanged.connect(dialog._format_changed)
    form.addRow("Profile name:", dialog.name_edit)
    form.addRow("Description:", dialog.description_edit)
    form.addRow("Server path format:", dialog.source_format)
    form.addRow("This computer's default:", dialog.target_format)
    form.addRow(dialog.distribution_row)
    layout.addLayout(form)


def _profile_rule_tables(dialog, layout):
    dialog.rule_tabs = QtWidgets.QTabWidget()
    dialog.rule_tabs.setFixedHeight(300)
    rewrite_page, root_page = QtWidgets.QWidget(), QtWidgets.QWidget()
    rewrite_layout, root_layout = QtWidgets.QVBoxLayout(rewrite_page), QtWidgets.QVBoxLayout(root_page)
    dialog.rule_tabs.addTab(rewrite_page, "Text rewrites")
    dialog.rule_tabs.addTab(root_page, "Root mappings")
    layout.addWidget(dialog.rule_tabs)
    dialog.rewrites = _table(["Operation", "Prefix / regex", "Replacement / added prefix", "Enabled"])
    dialog.rewrites.horizontalHeaderItem(2).setToolTip(r"Regex replacements support \1 and \g<name>.")
    for column, width in enumerate((170, 275, 330)):
        dialog.rewrites.setColumnWidth(column, width)
    rewrite_layout.addWidget(dialog.rewrites, 1)
    rewrite_layout.addWidget(_row(_button("Add rewrite", dialog._add_rewrite), _button("Remove rewrite", lambda: dialog._remove(dialog.rewrites)),
                          _button("Move up", lambda: dialog._move(-1)), _button("Move down", lambda: dialog._move(1))))
    dialog.roots = _table(["Server root", "Computer root", "Priority", "Enabled", "Case sensitive"])
    dialog.roots.horizontalHeaderItem(1).setToolTip("For example K: or K:\\ for a drive root, or a full local/UNC folder.")
    dialog.roots.horizontalHeaderItem(2).setToolTip("The longest matching root wins, followed by priority.")
    for column, width in enumerate((250, 340, 80, 80)):
        dialog.roots.setColumnWidth(column, width)
    root_layout.addWidget(dialog.roots, 1)
    root_layout.addWidget(_row(_button("Add root", dialog._add_root), _button("Remove root", lambda: dialog._remove(dialog.roots))))


def _profile_sample(dialog, layout):
    dialog.sample_edit, dialog.base_edit = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
    dialog.sample_edit.setPlaceholderText("Paste one path exactly as it appears in the CSV")
    dialog.base_edit.setPlaceholderText("Base directory for a relative sample")
    dialog.test_button = _button("Test path", dialog._test)
    layout.addWidget(_row(dialog.sample_edit, dialog.test_button))
    layout.addWidget(dialog.base_edit)
    dialog.result_label = StatusLabel()
    dialog.result_label.setText("")
    dialog.result_label.setWordWrap(True)
    dialog.result_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
    dialog.result_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    layout.addWidget(dialog.result_label)


class MappingProfileDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, selected="custom", sample="", source_base=""):
        super().__init__(parent)
        self.setWindowTitle("Server path profiles")
        self.resize(980, 740)
        self.saved_name = ""
        self._profiles = profiles()
        self._build()
        self.profile_combo.addItems([item["name"] for item in self._profiles])
        self.profile_combo.currentIndexChanged.connect(self._select)
        self.profile_combo.setCurrentText(selected)
        self._select(self.profile_combo.currentIndex())
        self.sample_edit.setText(sample)
        self.base_edit.setText(source_base)

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        _profile_fields(self, content_layout)
        _profile_rule_tables(self, content_layout)
        _profile_sample(self, content_layout)
        content_layout.addStretch()
        self.profile_scroll = QtWidgets.QScrollArea()
        self.profile_scroll.setWidgetResizable(True)
        self.profile_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.profile_scroll.setWidget(content)
        layout.addWidget(self.profile_scroll, 1)
        self.error_label = StatusLabel()
        self.error_label.setText("")
        self.error_label.setStyleSheet("color: #a32020")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        footer.addWidget(_button("Cancel", self.reject))
        self.save_button = _button("Save profile", self._save)
        self.save_button.setProperty("role", "primary")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self._format_changed()

    def _format_changed(self, *args):
        self.distribution_row.setVisible("wsl" in (self.source_format.currentData(), self.target_format.currentData()))

    def _select(self, index):
        if index < 0:
            return
        value = self._profiles[index]
        self.name_edit.setText(value["name"])
        self.description_edit.setText(value.get("description", ""))
        set_format(self.source_format, value.get("source_format", "custom"))
        set_format(self.target_format, value.get("target_format", "windows"))
        self.distribution_edit.setText(value.get("wsl_distribution", ""))
        self.rewrites.setRowCount(0)
        for rule in value.get("rewrites", []):
            self._add_rewrite(rule=rule)
        self.roots.setRowCount(0)
        for rule in value["rules"]:
            self._add_root(rule=rule)
        self.delete_button.setEnabled(value["name"].casefold() != "custom")
        self.error_label.setText("")
        self.result_label.setText("")

    def _new(self):
        self._select(0)
        self.name_edit.clear()
        self.description_edit.clear()
        self.roots.setRowCount(0)
        self.rewrites.setRowCount(0)
        set_format(self.source_format, "custom")
        set_format(self.target_format, "custom")
        self.distribution_edit.clear()
        self.delete_button.setEnabled(False)
        self.name_edit.setFocus()

    def _duplicate(self):
        self.name_edit.setText(self.name_edit.text() + " copy")
        self.delete_button.setEnabled(False)
        self.name_edit.setFocus()

    def _add_rewrite(self, checked=False, *, rule=None):
        rule = rule or dict(kind="strip_prefix", pattern="", replacement="", enabled=True)
        row = self.rewrites.rowCount()
        self.rewrites.insertRow(row)
        combo = QtWidgets.QComboBox()
        for label, value in (("Remove prefix", "strip_prefix"), ("Add prefix", "prepend"), ("Regex replace", "regex")):
            combo.addItem(label, value)
        combo.setCurrentIndex(combo.findData(rule["kind"]))
        self.rewrites.setCellWidget(row, 0, combo)
        for column, key in enumerate(("pattern", "replacement", "enabled"), 1):
            _cell(self.rewrites, row, column, rule[key])

    def _add_root(self, checked=False, *, rule=None):
        rule = rule or dict(source_prefix="", target_prefix="", priority=0, provider="mapped", enabled=True, case_sensitive=True, require_exists=True)
        row = self.roots.rowCount()
        self.roots.insertRow(row)
        for column, key in enumerate(("source_prefix", "target_prefix", "priority", "enabled", "case_sensitive")):
            _cell(self.roots, row, column, rule[key])
        self.roots.item(row, 0).setData(QtCore.Qt.UserRole, dict(rule))

    @staticmethod
    def _remove(table):
        for row in sorted({index.row() for index in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

    def _rewrite_values(self):
        return [dict(kind=self.rewrites.cellWidget(row, 0).currentData(), pattern=_text(self.rewrites, row, 1),
                     replacement=_text(self.rewrites, row, 2), enabled=self.rewrites.cellWidget(row, 3).isChecked())
                for row in range(self.rewrites.rowCount())]

    def _move(self, offset):
        row = self.rewrites.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.rewrites.rowCount():
            return
        values = self._rewrite_values()
        values[row], values[target] = values[target], values[row]
        self.rewrites.setRowCount(0)
        for rule in values:
            self._add_rewrite(rule=rule)
        self.rewrites.selectRow(target)

    def draft(self):
        rules = []
        for row in range(self.roots.rowCount()):
            rule = dict(self.roots.item(row, 0).data(QtCore.Qt.UserRole))
            rule.update({key: _text(self.roots, row, column) for column, key in enumerate(("source_prefix", "target_prefix", "priority"))})
            rule["priority"] = int(rule["priority"])
            rule.update({key: self.roots.cellWidget(row, column).isChecked() for column, key in enumerate(("enabled", "case_sensitive"), 3)})
            rules.append(rule)
        return validate_mapping_profile(dict(name=self.name_edit.text().strip(), description=self.description_edit.text().strip(), rules=rules,
                                             source_format=self.source_format.currentData(), target_format=self.target_format.currentData(),
                                             wsl_distribution=self.distribution_edit.text().strip(), rewrites=self._rewrite_values()))

    def _test(self):
        try:
            draft = self.draft()
            result = PathResolver.from_profile(draft).resolve(self.sample_edit.text(), source_base=self.base_edit.text(), require_exists=False)
            self.result_label.setText("Raw: %s\nAfter rewrites: %s\nComputer path: %s" % (result.raw_path, result.rewritten_path, result.resolved_path or result.error))
            self.error_label.setText(result.error)
            QtCore.QTimer.singleShot(0, lambda: self.profile_scroll.ensureWidgetVisible(self.result_label))
        except Exception as exc:
            traceback.print_exc()
            self.error_label.setText(str(exc))

    def _save(self):
        try:
            saved = upsert_mapping_profile(self.draft())
            self.saved_name = saved["name"]
            self.accept()
        except Exception as exc:
            traceback.print_exc()
            self.error_label.setText(str(exc))

    def _delete(self):
        try:
            remove_mapping_profile(self.profile_combo.currentText())
            self.saved_name = "custom"
            self.accept()
        except Exception as exc:
            traceback.print_exc()
            self.error_label.setText(str(exc))


class MappingPreviewDialog(QtWidgets.QDialog):
    def __init__(self, payload, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Path mapping preview")
        self.resize(1050, 470)
        layout = QtWidgets.QVBoxLayout(self)
        counts = ", ".join("%s: %s" % item for item in payload.get("status_counts", {}).items())
        layout.addWidget(QtWidgets.QLabel("%s of %s rows. %s" %
                                          (len(payload.get("rows", [])), payload.get("row_count", 0), counts)))
        self.table = _table(["Row", "Raw CSV path", "Computer path", "Status", "Details"])
        for index, value in enumerate(payload.get("rows", [])):
            self.table.insertRow(index)
            for column, key in enumerate(("row_number", "raw_path", "resolved_path", "status", "error")):
                _cell(self.table, index, column, value.get(key) or "")
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setColumnWidth(1, 330)
        self.table.setColumnWidth(2, 330)
        layout.addWidget(self.table, 1)
        layout.addWidget(_button("Close", self.close))
