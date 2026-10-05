"""Annotation forms and compact candidate score guidance for the Qt workspace."""

import html

from pymol.Qt import QtCore, QtWidgets

from .qt_widgets import ColumnPicker, qt_enum
from .panel_style import ElidedLabel
from .review_config import decode_value, encode_value, validate_fields, validate_score_config


class ChoicePicker(ColumnPicker):
    advanceRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.search.setPlaceholderText("Search choices")
        self.setToolTip("Select choices; Ctrl+click a choice to save and go to the next candidate.")
        self._pressed_choice = None
        self._apply_next = False
        self.items.viewport().installEventFilter(self)
        self.apply_button.installEventFilter(self)
        self.apply_button.setToolTip("Apply choices; Ctrl+click to save and go to the next candidate.")

    def set_columns(self, columns, selected=()):
        super().set_columns(columns, selected)
        self.setText(", ".join(self._selected) or "Choose values…")

    def _count(self, *args):
        self.count_label.setText(f"{len(self._choices())} selected")

    def _apply(self, advance=False):
        advance = bool(advance or self._apply_next)
        self._apply_next = False
        super()._apply()
        self.setText(", ".join(self._selected) or "Choose values…")
        if advance:
            self.advanceRequested.emit()

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            advance = bool(event.modifiers() & QtCore.Qt.ControlModifier) and not bool(event.modifiers() & QtCore.Qt.ShiftModifier)
            if obj is self.apply_button:
                self._apply_next = advance
            elif obj is self.items.viewport():
                item = self.items.itemAt(event.pos())
                if item is not None:
                    self._pressed_choice = (item, advance)
                    return True
        if (obj is self.apply_button and event.type() == QtCore.QEvent.MouseButtonRelease
                and not self.apply_button.rect().contains(event.pos())):
            self._apply_next = False
        if (obj is self.items.viewport() and event.type() == QtCore.QEvent.MouseButtonRelease
                and event.button() == QtCore.Qt.LeftButton and self._pressed_choice is not None):
            item, advance = self._pressed_choice
            self._pressed_choice = None
            if item is self.items.itemAt(event.pos()):
                item.setCheckState(QtCore.Qt.Unchecked if item.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)
                if advance:
                    self._apply(advance=True)
            return True
        return super().eventFilter(obj, event)


class _AnnotationScrollArea(QtWidgets.QScrollArea):
    """Grow with the fields, leaving surplus space below the save controls."""

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(self.widget().sizeHint().height() + 2 * self.frameWidth())
        return hint

    def eventFilter(self, watched, event):
        if watched is self.widget() and event.type() == QtCore.QEvent.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)


class AnnotationForm(QtWidgets.QWidget):
    changed = QtCore.Signal()
    primaryChosen = QtCore.Signal(str)
    saveRequested = QtCore.Signal()
    advanceRequested = QtCore.Signal()
    configureRequested = QtCore.Signal()

    def __init__(self, fields, parent=None):
        super().__init__(parent)
        self.fields = validate_fields(fields)
        self.editors, self.values, self.primary_buttons = {}, {}, []
        self.dirty = set()
        self.required_filled = {}
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.target_label = QtWidgets.QLabel("Current candidate")
        self.target_label.setParent(self)
        self.target_label.hide()
        content = QtWidgets.QWidget()
        form = self.content_layout = QtWidgets.QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        self.optional_content = QtWidgets.QWidget()
        optional = QtWidgets.QVBoxLayout(self.optional_content)
        optional.setContentsMargins(0, 0, 0, 0)
        optional.setSpacing(10)
        for index, field in enumerate(self.fields):
            editor = self._editor(field, index == 0)
            self.editors[field["name"]] = editor
            section = form if field["required"] or index == 0 else optional
            title = field["name"].replace("_", " ").capitalize()
            label = ElidedLabel(title + (" *" if field["required"] else ""))
            label.setToolTip(title + (" (required)" if field["required"] else ""))
            label.setBuddy(editor)
            label.setProperty("role", "muted")
            section.addWidget(label)
            section.addWidget(editor)
        optional_fields = [field for index, field in enumerate(self.fields) if not field["required"] and index != 0]
        self.optional_toggle = QtWidgets.QPushButton("+ Add note" if len(optional_fields) == 1 and optional_fields[0]["type"] == "text" else "+ More fields")
        self.optional_toggle.setAutoDefault(False)
        self.optional_toggle.setCheckable(True)
        self.optional_toggle.setProperty("role", "quiet")
        self.optional_toggle.toggled.connect(self.optional_content.setVisible)
        self.optional_toggle.setVisible(bool(optional_fields))
        self.optional_content.hide()
        form.addWidget(self.optional_toggle, 0, QtCore.Qt.AlignLeft)
        form.addWidget(self.optional_content)
        form.addStretch()
        scroll = self.scroll = _AnnotationScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)
        scroll.setMinimumHeight(80)
        scroll.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
        layout.addWidget(scroll)
        row = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save and next")
        self.save_button.setProperty("role", "primary")
        self.save_button.setMinimumWidth(170)
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self.saveRequested)
        self.fields_button = QtWidgets.QPushButton("Annotation fields…")
        self.fields_button.setAutoDefault(False)
        self.fields_button.clicked.connect(self.configureRequested)
        self.fields_button.setParent(self)
        self.fields_button.hide()
        self.hint_label = ElidedLabel()
        self.hint_label.setProperty("role", "muted")
        row.addWidget(self.hint_label, 1)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        # Keep saving beside the fields instead of at an auto-hidden taskbar edge.
        layout.addStretch(1)

    def _editor(self, field, primary):
        name, kind = field["name"], field["type"]
        self.values[name] = [] if kind == "multiple" else ""
        if kind == "text":
            editor = QtWidgets.QLineEdit()
            editor.setPlaceholderText("Enter " + name.replace("_", " "))
            editor.textEdited.connect(lambda value, key=name: self._change(key, value))
        elif kind == "multiple":
            editor = ChoicePicker()
            editor.set_columns(field["values"])
            editor.selectionChanged.connect(lambda value, key=name: self._change(key, value))
            editor.advanceRequested.connect(self.advanceRequested)
        elif primary and len(field["values"]) <= 4 and max(map(len, field["values"])) <= 24:
            editor = QtWidgets.QWidget()
            row = QtWidgets.QGridLayout(editor)
            row.setContentsMargins(0, 0, 0, 0)
            for index, value in enumerate(field["values"], 1):
                button = QtWidgets.QPushButton(value)
                button.setProperty("role", "choice")
                button.setToolTip(f"Alt+{index}: save {value}. Ctrl+click: save and next.")
                button.setAutoDefault(False)
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, value=value: self.primaryChosen.emit(value))
                row.addWidget(button, (index - 1) // 2, (index - 1) % 2)
                self.primary_buttons.append(button)
        else:
            editor = QtWidgets.QComboBox()
            editor.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            editor.setMinimumContentsLength(12)
            editor.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            editor.addItem("Choose…", "")
            for value in field["values"]:
                editor.addItem(value, value)
            editor.currentIndexChanged.connect(lambda _index, key=name, widget=editor: self._change(key, widget.currentData() or ""))
            if primary:
                editor.activated.connect(lambda _index, widget=editor: self.primaryChosen.emit(widget.currentData() or ""))
        return editor

    def _change(self, name, value):
        if self._loading:
            return
        self.values[name] = value
        self.dirty.add(name)
        self.changed.emit()

    def set_value(self, field, value, dirty=False):
        name = field["name"]
        editor = self.editors[name]
        self._loading = True
        try:
            self.values[name] = value
            if field["type"] == "text":
                editor.setText(str(value))
            elif field["type"] == "multiple":
                editor.set_columns(field["values"], value)
            elif isinstance(editor, QtWidgets.QComboBox):
                index = editor.findData(value)
                if index < 0 and value:
                    editor.addItem(str(value) + " (saved)", value)
                    index = editor.count() - 1
                editor.setCurrentIndex(max(0, index))
            else:
                for button, choice in zip(self.primary_buttons, field["values"]):
                    button.setChecked(value == choice)
        finally:
            self._loading = False
        if dirty:
            self.dirty.add(name)
            self.changed.emit()

    def load_annotations(self, values, mixed=(), drafts=None, required_filled=None):
        self.dirty.clear()
        self.required_filled = dict(required_filled or {})
        saved = any(decode_value(field, values.get(field["name"])) for field in self.fields)
        self.hint_label.setText("Mixed values" if mixed else "Saved" if saved else "")
        optional_visible = False
        for index, field in enumerate(self.fields):
            self.set_value(field, decode_value(field, values.get(field["name"])))
            editor = self.editors[field["name"]]
            if isinstance(editor, QtWidgets.QLineEdit):
                editor.setPlaceholderText("Mixed values" if field["name"] in mixed else "Enter " + field["name"].replace("_", " "))
            if index and not field["required"] and (self.values[field["name"]] or field["name"] in mixed):
                optional_visible = True
        for field in self.fields:
            if drafts and field["name"] in drafts:
                self.set_value(field, decode_value(field, drafts[field["name"]]), dirty=True)
                if field is not self.fields[0] and not field["required"]:
                    optional_visible = True
        self.optional_toggle.setChecked(optional_visible)
        if self.dirty:
            self.hint_label.setText("Not saved")

    def can_save(self):
        return bool(self.dirty) and not self.required_missing()

    def required_missing(self):
        missing = []
        for field in self.fields:
            name = field["name"]
            if field["required"]:
                value = self.values[name]
                present = bool(value) if isinstance(value, list) else bool(value.strip())
                filled = present if name in self.dirty else self.required_filled.get(name, present)
                if not filled:
                    missing.append(name)
        return missing

    def updates(self):
        return {field["name"]: encode_value(field, self.values[field["name"]])
                for field in self.fields if field["name"] in self.dirty}


class AnnotationFieldsDialog(QtWidgets.QDialog):
    def __init__(self, fields, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Annotation fields")
        self.resize(680, 390)
        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field", "Type", "Required", "Choices"])
        self.table.horizontalHeaderItem(3).setToolTip("Separate choices with semicolons.")
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(36)
        for column, width in enumerate((170, 175, 100, 180)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        for field in fields:
            self.add_field(field)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add field")
        remove = QtWidgets.QPushButton("Remove selected")
        add.clicked.connect(lambda: self.add_field({"name": f"field_{self.table.rowCount()+1}", "type": "single", "values": ["Yes", "No"], "required": False}))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.result_fields = None

    def add_field(self, field):
        index = self.table.rowCount()
        self.table.insertRow(index)
        self.table.setItem(index, 0, QtWidgets.QTableWidgetItem(field["name"]))
        kind = QtWidgets.QComboBox()
        for label, value in [("Single choice", "single"), ("Multiple choices", "multiple"), ("Text", "text")]:
            kind.addItem(label, value)
        kind.setCurrentIndex(kind.findData(field["type"]))
        self.table.setCellWidget(index, 1, kind)
        required = QtWidgets.QCheckBox()
        required.setChecked(field["required"])
        self.table.setCellWidget(index, 2, required)
        self.table.setItem(index, 3, QtWidgets.QTableWidgetItem(";".join(field.get("values", []))))

    def _accept(self):
        try:
            fields = []
            for row in range(self.table.rowCount()):
                kind = self.table.cellWidget(row, 1).currentData()
                choices = self.table.item(row, 3).text()
                fields.append({"name": self.table.item(row, 0).text(), "type": kind,
                               "required": self.table.cellWidget(row, 2).isChecked(),
                               "values": [value.strip() for value in choices.split(";") if value.strip()] if kind != "text" else []})
            if not fields:
                raise ValueError("Keep at least one annotation field")
            self.result_fields = validate_fields(fields)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class ScoreGuidanceDialog(QtWidgets.QDialog):
    def __init__(self, columns, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Candidate score guidance")
        self.resize(920, 420)
        config = validate_score_config(config or {})
        layout = QtWidgets.QVBoxLayout(self)
        self.automatic = QtWidgets.QCheckBox("Automatically show up to six selected numeric columns")
        self.automatic.setChecked(config["columns"] is None)
        layout.addWidget(self.automatic)
        self.table = QtWidgets.QTableWidget(len(columns), 6)
        self.table.setHorizontalHeaderLabels(["Show", "Column", "Better direction", "Good threshold", "Bad threshold", "CSV pass column"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(36)
        for column, width in enumerate((60, 160, 190, 140, 140, 160)):
            self.table.setColumnWidth(column, width)
        for row, name in enumerate(columns):
            rule = config["rules"].get(name, {})
            shown = QtWidgets.QCheckBox()
            shown.setChecked(name in (config["columns"] or []))
            self.table.setCellWidget(row, 0, shown)
            item = QtWidgets.QTableWidgetItem(name)
            item.setFlags(item.flags() & ~qt_enum("ItemFlag", "ItemIsEditable"))
            self.table.setItem(row, 1, item)
            direction = QtWidgets.QComboBox()
            for label, value in [("Numeric rank only", "neutral"), ("Higher is better", "higher"), ("Lower is better", "lower")]:
                direction.addItem(label, value)
            direction.setCurrentIndex(direction.findData(rule.get("direction", "neutral")))
            self.table.setCellWidget(row, 2, direction)
            for column, key in [(3, "good"), (4, "bad")]:
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem("" if rule.get(key) is None else str(rule[key])))
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(rule.get("pass_column", "")))
        layout.addWidget(self.table)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.result_config = None

    def _accept(self):
        try:
            columns, rules = [], {}
            for row in range(self.table.rowCount()):
                name = self.table.item(row, 1).text()
                if self.table.cellWidget(row, 0).isChecked():
                    columns.append(name)
                rule = {"direction": self.table.cellWidget(row, 2).currentData(), "pass_column": self.table.item(row, 5).text().strip()}
                for column, key in [(3, "good"), (4, "bad")]:
                    value = self.table.item(row, column).text().strip()
                    rule[key] = float(value) if value else None
                rules[name] = rule
            self.result_config = validate_score_config({"columns": None if self.automatic.isChecked() else columns, "rules": rules})
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class ScoreFooter(QtWidgets.QWidget):
    configureRequested = QtCore.Signal()
    COLORS = {"good": ("#dcefdc", "#195724"), "bad": ("#fbe1de", "#8d2424"),
              "middle": ("#fff0ce", "#71520b"), "neutral": ("#eceff2", "#46515c")}

    def __init__(self, parent=None, column_picker=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("Candidate scores")
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.title.setTextFormat(qt_enum("TextFormat", "PlainText"))
        self.title.setParent(self)
        self.title.hide()
        self.column_picker = column_picker
        if column_picker is not None:
            column_picker.setProperty("role", "quiet")
            header.addWidget(column_picker)
        header.addStretch()
        self.help_button = QtWidgets.QToolButton()
        self.help_button.setText("?")
        self.help_button.setProperty("role", "icon")
        self.help_button.setAccessibleName("About scores")
        self.help_button.setToolTip("About scores")
        self.help_button.clicked.connect(self._show_help)
        header.addWidget(self.help_button)
        layout.addLayout(header)
        self.hint = QtWidgets.QLabel()
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.grid = QtWidgets.QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.grid)
        self.cards = {}
        self._grid_columns = 2

    def _show_help(self):
        QtWidgets.QMessageBox.information(self, "About scores",
            "Relative rank compares the current filtered set, not just a sample; it is not pass/fail.\n\n"
            "Target and CSV pass/fail badges use configured rules or table values.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 3 if self.width() >= 400 else 2
        if columns != self._grid_columns:
            self._grid_columns = columns
            for index, (frame, _labels) in enumerate(self.cards.values()):
                self.grid.addWidget(frame, index // columns, index % columns)

    def show_scores(self, candidate, cards, cohort_count, selected_count=1, loading=False):
        self.title.setText(f"Candidate: {candidate}" + (f" · {selected_count} rows selected for annotation" if selected_count > 1 else ""))
        self.title.setToolTip(str(candidate))
        names = tuple(card["column"] for card in cards)
        if names != tuple(self.cards):
            while self.grid.count():
                self.grid.takeAt(0).widget().deleteLater()
            self.cards = {}
            for index, name in enumerate(names):
                frame = QtWidgets.QFrame()
                box = QtWidgets.QVBoxLayout(frame)
                box.setContentsMargins(2, 3, 12, 3)
                labels = [ElidedLabel() for _ in range(4)]
                labels[0].setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
                labels[1].setStyleSheet("font-size: 13pt; font-weight: 600;")
                labels[0].setProperty("role", "muted")
                for label in labels:
                    label.setTextFormat(qt_enum("TextFormat", "PlainText"))
                    box.addWidget(label)
                self.grid.addWidget(frame, index // self._grid_columns, index % self._grid_columns)
                self.cards[name] = (frame, labels)
        if loading:
            self.hint.setText("Updating scores…")
        else:
            self.hint.setText("")
        self.hint.setVisible(loading)
        self.help_button.setVisible(bool(cards))
        if self.column_picker is not None:
            self.column_picker.setText("Scores…" if cards else "+ Add scores")
        for card in cards:
            frame, labels = self.cards[card["column"]]
            for label, text in zip(labels, (card["column"], card["value"], card["absolute"], card["relative"].split(" · ")[0])):
                if label.text() != text:
                    label.setText(text)
            for label, key in ((labels[2], "absolute_tone"), (labels[3], "relative_tone")):
                background, foreground = self.COLORS[card[key]]
                style = (f"color: {foreground}; background: {background}; padding: 2px 4px; border-radius: 3px;"
                         if label is labels[2] else "color: #46515c;")
                if label.styleSheet() != style:
                    label.setStyleSheet(style)
            labels[2].setVisible(card["absolute"] != "No absolute rule")
            direction = card["direction"]
            frame.setToolTip("<qt>" + html.escape(f"{card['column']} = {card['raw']}\n{card['absolute']}\n{card['relative']}\nDirection: {direction}").replace("\n", "<br>") + "</qt>")
