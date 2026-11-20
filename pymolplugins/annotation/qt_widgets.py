"""Focused Qt controls for CSV columns and PyMOL command editing."""

import ast
import re

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets
from .filter_values import FilterValueCatalog
from .filtering import COMPARISON_OPERATORS, TEXT_OPERATORS, column_expression
from .history_store import fuzzy_matches


def qt_enum(group, name):
    value = getattr(QtCore.Qt, name, None)
    return value if value is not None else getattr(getattr(QtCore.Qt, group), name)


class ScoresTable(QtWidgets.QTableView):
    """Use native row selection and preview on an unmodified click."""

    candidateClicked = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._wheel_fraction = 0.0
        self._press_modifiers = qt_enum("KeyboardModifier", "NoModifier")
        self.clicked.connect(self._candidate_clicked)

    def mousePressEvent(self, event):
        self._press_modifiers = event.modifiers()
        super().mousePressEvent(event)

    def _candidate_clicked(self, index):
        selection_modifiers = QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier
        if not self._press_modifiers & selection_modifiers:
            self.candidateClicked.emit(index)

    def scroll_shift_wheel(self, event):
        if not event.modifiers() & qt_enum("KeyboardModifier", "ShiftModifier"):
            return False
        scroll = self.horizontalScrollBar()
        pixel = event.pixelDelta()
        distance = pixel.x() or pixel.y()
        if not distance:
            angle = event.angleDelta()
            step = self.verticalHeader().defaultSectionSize()
            distance = (angle.x() or angle.y()) * step * QtWidgets.QApplication.wheelScrollLines() / 120
        self._wheel_fraction += distance
        movement = int(self._wheel_fraction)
        self._wheel_fraction -= movement
        scroll.setValue(scroll.value() - movement)
        event.accept()
        return True

    def wheelEvent(self, event):
        if not self.scroll_shift_wheel(event):
            super().wheelEvent(event)


class ColumnPicker(QtWidgets.QToolButton):
    """Search and stage column choices before applying one view change."""

    selectionChanged = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = []
        self._columns = []
        self.setText("Columns…")
        self.setToolTip("Choose which score columns to show")
        self.menu = QtWidgets.QMenu(self)
        content = QtWidgets.QWidget()
        content.setMinimumWidth(340)
        layout = QtWidgets.QVBoxLayout(content)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search column names")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.items = QtWidgets.QListWidget()
        self.items.setUniformItemSizes(True)
        self.items.setMinimumHeight(230)
        self.items.itemChanged.connect(self._count)
        layout.addWidget(self.items)
        self.count_label = QtWidgets.QLabel()
        layout.addWidget(self.count_label)
        row = QtWidgets.QHBoxLayout()
        self.all_button = QtWidgets.QPushButton("Select all")
        self.clear_button = QtWidgets.QPushButton("Clear all")
        self.apply_button = QtWidgets.QPushButton("Apply")
        self.all_button.clicked.connect(lambda: self._check_all(True))
        self.clear_button.clicked.connect(lambda: self._check_all(False))
        self.apply_button.clicked.connect(self._apply)
        for button in (self.all_button, self.clear_button, self.apply_button):
            row.addWidget(button)
        layout.addLayout(row)
        action = QtWidgets.QWidgetAction(self.menu)
        action.setDefaultWidget(content)
        self.menu.addAction(action)
        self.clicked.connect(lambda: self.open_menu())
        self._count()

    def set_columns(self, columns, selected=()):
        self._columns = list(dict.fromkeys(str(name) for name in columns))
        self._selected = [name for name in selected if name in self._columns]
        self.setText(f"Columns ({len(self._selected)})…")
        self.items.blockSignals(True)
        self.items.clear()
        for name in self._columns:
            item = QtWidgets.QListWidgetItem(name, self.items)
            item.setFlags(item.flags() | qt_enum("ItemFlag", "ItemIsUserCheckable"))
            item.setCheckState(qt_enum("CheckState", "Checked" if name in self._selected else "Unchecked"))
        self.items.blockSignals(False)
        self._filter(self.search.text())

    def open_menu(self, anchor=None):
        self.set_columns(self._columns, self._selected)
        anchor = anchor if anchor is not None else self
        self.menu.popup(anchor.mapToGlobal(QtCore.QPoint(0, anchor.height())))
        self.search.setFocus()

    def _filter(self, query):
        matching = set(fuzzy_matches(self._columns, query))
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(item.text() not in matching)
        self._count()

    def _check_all(self, checked):
        self.items.blockSignals(True)
        state = qt_enum("CheckState", "Checked" if checked else "Unchecked")
        for index in range(self.items.count()):
            self.items.item(index).setCheckState(state)
        self.items.blockSignals(False)
        self._count()

    def _choices(self):
        checked = qt_enum("CheckState", "Checked")
        names = [self.items.item(i).text() for i in range(self.items.count())
                 if self.items.item(i).checkState() == checked]
        return [name for name in self._selected if name in names] + [name for name in names if name not in self._selected]

    def _count(self, *args):
        visible = sum(not self.items.item(i).isHidden() for i in range(self.items.count()))
        self.count_label.setText(f"{len(self._choices())} selected · {visible} matching · {self.items.count()} columns")

    def _apply(self):
        self._selected = self._choices()
        self.setText(f"Columns ({len(self._selected)})…")
        self.menu.hide()
        self.selectionChanged.emit(list(self._selected))


_NUMBER_FRAGMENT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d*)?"
_FILTER_FRAGMENT = re.compile(rf"{_NUMBER_FRAGMENT}|[\w.]+|[!<>=]=?|[+-]")
_FILTER_TOKEN = re.compile(
    r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\x60[^\x60]*\x60)|(["'\x60].*)|("""
    + _NUMBER_FRAGMENT + r"""|[\w.]+|[!<>=]=?|\S)"""
)
_COLUMN_ARGUMENT = "<column>"
_TEXT_ARGUMENT = "text"
_TEXT_ARGUMENT_PREFIX = ', "'
_TEXT_ARGUMENT_TAIL = _TEXT_ARGUMENT_PREFIX + _TEXT_ARGUMENT + '")'
_TEXT_FUNCTION_OPENINGS = tuple(name + "(" for name in TEXT_OPERATORS)
_TEXT_FUNCTION_SNIPPETS = {
    opening + _COLUMN_ARGUMENT + _TEXT_ARGUMENT_TAIL: opening for opening in _TEXT_FUNCTION_OPENINGS
}


def _value_position(tokens, columns):
    """Identify the column that owns the comparison or text argument."""
    previous = tokens[-1].group() if tokens else ""
    comparison = len(tokens) > 1 and previous in COMPARISON_OPERATORS
    function = (len(tokens) >= 4 and previous == "," and tokens[-3].group() == "("
                and tokens[-4].group() in TEXT_OPERATORS)
    name = tokens[-2].group().strip("\x60") if comparison or function else ""
    return (name, function) if name in columns else ("", False)


def _filter_completions(text, columns, catalog):
    """Return the active token and suggestions for the syntactic position."""
    tokens = list(_FILTER_TOKEN.finditer(text))
    last = tokens[-1] if tokens else None
    start, fragment = len(text), ""
    if last and last.end() == len(text) and (
        last.group(2) or _FILTER_FRAGMENT.fullmatch(last.group())
    ):
        start, fragment = last.start(), last.group()
        tokens.pop()
    field, as_text = _value_position(tokens, columns)
    if fragment.startswith(("'", '"')):
        try:
            query = ast.literal_eval(fragment + fragment[0])
        except (ValueError, SyntaxError):
            query = fragment[1:]
        return start, catalog.suggestions(field, query, text=True) if field else []
    literals = catalog.suggestions(field, fragment, text=as_text) if field else []
    previous = tokens[-1].group() if tokens else ""
    if previous == "is":
        options, headers = ("None", "not None"), ()
    elif previous == "not" and len(tokens) > 1 and tokens[-2].group() == "is":
        options, headers = ("None",), ()
    elif previous in COMPARISON_OPERATORS or previous == ",":
        options, headers = ("True", "False", "None"), columns
    elif previous == "(" and len(tokens) > 1 and tokens[-2].group() in TEXT_OPERATORS:
        options, headers = (), columns
    elif previous in ("", "(", "and", "or", "not"):
        options, headers = ("not", "(") + tuple(_TEXT_FUNCTION_SNIPPETS), columns
    else:
        options, headers = ("and", "or", "is None", "is not None") + COMPARISON_OPERATORS, ()
    choices = [item for item in options if item.casefold().startswith(fragment.casefold())]
    if not fragment or not fragment[0].isdigit():
        choices.extend(column_expression(name) for name in fuzzy_matches(headers, fragment.lstrip("`")))
    return start, list(dict.fromkeys(literals + choices))


class ColumnCompletion(QtCore.QObject):
    """Complete exact CSV headers, optionally within a filter expression."""

    def __init__(self, widget, *, expression=False, choices=None):
        super().__init__(widget)
        self.widget, self.expression = widget, expression
        self.enabled = True
        self.columns = []
        self.value_catalog = FilterValueCatalog() if expression else None
        self.choices = choices
        self.model = QtCore.QStringListModel([], widget)
        self.completer = QtWidgets.QCompleter(self.model, widget)
        self.completer.setCaseSensitivity(qt_enum("CaseSensitivity", "CaseInsensitive"))
        self.completer.setMaxVisibleItems(12)
        # QLineEdit's native highlighted handler can rewrite the input while
        # typing. Own insertion explicitly, for both whole fields and tokens.
        widget.setCompleter(None)
        if isinstance(widget.parent(), QtWidgets.QComboBox):
            widget.parent().setCompleter(None)
        self.completer.setWidget(widget)
        self.completer.activated[str].connect(self._accept)
        widget.textEdited.connect(self.refresh)
        widget.installEventFilter(self)
        # QCompleter otherwise hides its popup, then forwards Enter to the
        # line edit where returnPressed would submit the unfinished filter.
        self.completer.popup().installEventFilter(self)

    def set_columns(self, columns):
        self.columns = list(dict.fromkeys(columns))
        self.model.setStringList(self.columns)

    def set_value_dataset(self, dataset):
        if self.value_catalog.dataset is not dataset:
            self.value_catalog = FilterValueCatalog(dataset)

    def _prefix_end(self):
        if self.widget.selectedText() in (_COLUMN_ARGUMENT, _TEXT_ARGUMENT):
            return self.widget.selectionStart()
        return self.widget.cursorPosition()

    def refresh(self, *args):
        if not self.enabled:
            self.completer.popup().hide()
            return
        if self.expression:
            _, values = _filter_completions(
                self.widget.text()[:self._prefix_end()], self.columns, self.value_catalog
            )
        elif self.choices is not None:
            values = self.choices(self.widget.text())
        else:
            query = self.widget.text()
            values = fuzzy_matches(self.columns, query)
        self.model.setStringList(values)
        if values:
            self.completer.setCompletionPrefix("")
            self.completer.complete()
            index = QtCore.QModelIndex() if self.expression else self.completer.completionModel().index(0, 0)
            self.completer.popup().setCurrentIndex(index)
        else:
            self.completer.popup().hide()

    def _accept(self, value):
        self.completer.popup().hide()
        if not self.expression:
            self.widget.setText(value)
            self.widget.setCursorPosition(len(value))
            return
        text, end = self.widget.text(), self.widget.cursorPosition()
        prefix_end = self._prefix_end()
        start, _ = _filter_completions(text[:prefix_end], self.columns, self.value_catalog)
        if text[start:prefix_end].startswith(("'", '"')):
            literal = _FILTER_TOKEN.match(text, start)
            if literal and literal.group(1):
                end = literal.end()
        elif start < prefix_end:
            remaining = re.match(r"[\w.]*", text[end:])
            end += len(remaining.group())
        opening = _TEXT_FUNCTION_SNIPPETS.get(value)
        if opening:
            if text[end:].lstrip().startswith("("):
                name = opening[:-1]
                self.widget.setText(text[:start] + name + text[end:])
                self.widget.setCursorPosition(start + len(name) + text[end:].index("(") + 1)
            else:
                self.widget.setText(text[:start] + value + text[end:])
                self.widget.setSelection(start + len(opening), len(_COLUMN_ARGUMENT))
            self.refresh()
            return
        if value in ("and", "or", "not", *COMPARISON_OPERATORS) and not text[end:end + 1].isspace():
            value += " "
        self.widget.setText(text[:start] + value + text[end:])
        position = start + len(value)
        if text[end:].startswith(_TEXT_ARGUMENT_TAIL) and text[:start].rstrip().endswith(_TEXT_FUNCTION_OPENINGS):
            self.widget.setSelection(position + len(_TEXT_ARGUMENT_PREFIX), len(_TEXT_ARGUMENT))
            self.refresh()
        else:
            self.widget.setCursorPosition(position)

    def eventFilter(self, obj, event):
        if self.enabled and event.type() == QtCore.QEvent.KeyPress and self.completer.popup().isVisible():
            enter = event.key() in (qt_enum("Key", "Key_Return"), qt_enum("Key", "Key_Enter"))
            if enter and self.expression:
                self.completer.popup().hide()
                self.widget.returnPressed.emit()
                return True
            if enter and self.choices is not None:
                self.completer.popup().hide()
                return False
            if enter or event.key() == qt_enum("Key", "Key_Tab"):
                index = self.completer.popup().currentIndex()
                if not index.isValid():
                    index = self.model.index(0, 0)
                self._accept(str(index.data()))
                return True
        return super().eventFilter(obj, event)


def filter_condition_dialog(parent, columns, catalog):
    """Build one filter condition from an exact column, operator and value."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Add filter condition")
    form = QtWidgets.QFormLayout(dialog)
    column = QtWidgets.QComboBox()
    column.addItems(columns)
    operation = QtWidgets.QComboBox()
    for title, expression in (
        ("Contains text", "contains"), ("Does not contain text", "not contains"),
        ("Starts with", "startswith"), ("Ends with", "endswith"),
        ("Is empty", "is None"), ("Is not empty", "is not None"),
    ):
        operation.addItem(title, expression)
    for comparison in COMPARISON_OPERATORS:
        operation.addItem(comparison, comparison)
    value = QtWidgets.QLineEdit()

    def value_choices(query):
        text = operation.currentData() in TEXT_OPERATORS or operation.currentData() == "not contains"
        return catalog.suggestions(column.currentText(), query, text=text, literal=not text)

    ColumnCompletion(value, choices=value_choices)
    operation.currentIndexChanged.connect(
        lambda: value.setEnabled(operation.currentData() not in ("is None", "is not None"))
    )
    form.addRow("Column", column)
    form.addRow("Condition", operation)
    form.addRow("Value", value)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return ""
    name, op, raw = column_expression(column.currentText()), operation.currentData(), value.text()
    if op in TEXT_OPERATORS or op == "not contains":
        return f"{op}({name}, {raw!r})"
    if op in ("is None", "is not None"):
        return f"{name} {op}"
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        parsed = raw
    literal = parsed if isinstance(parsed, (bool, int, float, str)) else raw
    return f"{name} {op} {literal!r}"


class CommandCompletion(QtCore.QObject):
    """Use the same Tab completion entry point as PyMOL's command line."""

    def __init__(self, widget, *, multiline=False):
        super().__init__(widget)
        self.widget, self.multiline = widget, multiline
        widget.installEventFilter(self)

    def complete(self):
        if self.multiline:
            cursor = self.widget.textCursor()
            fragment = cursor.block().text()[:cursor.positionInBlock()]
        else:
            cursor = None
            fragment = self.widget.text()[:self.widget.cursorPosition()]
        prefix, separator, command = fragment.rpartition(";")
        if not separator:
            prefix, command = "", fragment
        leading = command[:len(command) - len(command.lstrip())]
        command = command.lstrip()
        placeholder = re.search(r"\{\w*$", command)
        if placeholder:
            candidates = ["{object}", "{selection}", "{file}", "{index}", "{count}", "{entry_id}", "{row_number}", "{label}", "{value}"]
            matches = [value for value in candidates if value.startswith(placeholder.group())]
            completed = command[:placeholder.start()] + matches[0] if len(matches) == 1 else command
        else:
            completed = cmd._parser.complete(command)
        if not completed:
            return
        replacement = prefix + separator + leading + completed
        if self.multiline:
            cursor.setPosition(cursor.block().position(), QtGui.QTextCursor.KeepAnchor)
            cursor.insertText(replacement)
            self.widget.setTextCursor(cursor)
        else:
            position = self.widget.cursorPosition()
            self.widget.setText(replacement + self.widget.text()[position:])
            self.widget.setCursorPosition(len(replacement))

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if key == qt_enum("Key", "Key_Tab") and not modifiers:
                self.complete()
                return True
            if key == qt_enum("Key", "Key_Space") and modifiers & qt_enum("KeyboardModifier", "ControlModifier"):
                self.complete()
                return True
        return super().eventFilter(obj, event)

    def detach(self):
        self.widget.removeEventFilter(self)


class AnnotationShortcuts:
    """One application-wide Qt route, independent of console docking."""

    def __init__(self, panel):
        shortcut_class = getattr(QtWidgets, "QShortcut", None) or QtGui.QShortcut
        callbacks = {"Alt+Right": panel._next, "Alt+Left": panel._prev, "Alt+Z": panel._undo}
        callbacks.update({f"Alt+{i}": lambda digit=i: panel._annotate_digit(digit) for i in range(1, 10)})
        self.shortcuts = []
        for key, callback in callbacks.items():
            shortcut = shortcut_class(QtGui.QKeySequence(key), panel)
            shortcut.setContext(qt_enum("ShortcutContext", "ApplicationShortcut"))
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def set_enabled(self, enabled):
        for shortcut in self.shortcuts:
            shortcut.setEnabled(bool(enabled))


class CollapsibleSection(QtWidgets.QWidget):
    def __init__(self, title, parent=None, expanded=False):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(qt_enum("ToolButtonStyle", "ToolButtonTextBesideIcon"))
        self.content = QtWidgets.QWidget()
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)
        self.toggle.setChecked(bool(expanded))
        self.set_expanded(bool(expanded))

    def set_expanded(self, expanded):
        self.content.setVisible(bool(expanded))
        self.toggle.setArrowType(qt_enum("ArrowType", "DownArrow" if expanded else "RightArrow"))
