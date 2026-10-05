"""Focused Qt controls for CSV columns and PyMOL command editing."""

import re

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets
from .history_store import fuzzy_matches


def qt_enum(group, name):
    value = getattr(QtCore.Qt, name, None)
    return value if value is not None else getattr(getattr(QtCore.Qt, group), name)


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


class ColumnCompletion(QtCore.QObject):
    """Complete exact CSV headers, optionally within a filter expression."""

    def __init__(self, widget, *, expression=False):
        super().__init__(widget)
        self.widget, self.expression = widget, expression
        self.enabled = True
        self.columns = []
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

    def _token(self):
        text = self.widget.text()[:self.widget.cursorPosition()]
        if text.count('"') % 2 or text.count("'") % 2:
            return None
        return re.search(r"`[^`]*$|[\w.]+$", text)

    def refresh(self, *args):
        if not self.enabled:
            self.completer.popup().hide()
            return
        if self.expression:
            match = self._token()
            if match is None:
                self.completer.popup().hide()
                return
            query = match.group().lstrip("`")
            values = [name if re.fullmatch(r"[^\W\d]\w*(?:\.\w+)*", name) else f"`{name}`"
                      for name in fuzzy_matches(self.columns, query)]
        else:
            query = self.widget.text()
            values = fuzzy_matches(self.columns, query)
        self.model.setStringList(values)
        if values:
            self.completer.setCompletionPrefix("")
            self.completer.complete()
            self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))
        else:
            self.completer.popup().hide()

    def _accept(self, value):
        if not self.expression:
            self.widget.setText(value)
            self.widget.setCursorPosition(len(value))
            self.completer.popup().hide()
            return
        match = self._token()
        if match is not None:
            end = self.widget.cursorPosition()
            self.widget.setText(self.widget.text()[:match.start()] + value + self.widget.text()[end:])
            self.widget.setCursorPosition(match.start() + len(value))
        self.completer.popup().hide()

    def eventFilter(self, obj, event):
        if self.enabled and event.type() == QtCore.QEvent.KeyPress and self.completer.popup().isVisible():
            if event.key() in (qt_enum("Key", "Key_Tab"), qt_enum("Key", "Key_Return"), qt_enum("Key", "Key_Enter")):
                index = self.completer.popup().currentIndex()
                if not index.isValid():
                    index = self.model.index(0, 0)
                self._accept(str(index.data()))
                self.completer.popup().hide()
                return True
        return super().eventFilter(obj, event)


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
