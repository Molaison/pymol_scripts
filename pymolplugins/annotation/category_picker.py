"""Checkbox popup for the categories grouped under a Scores header's Other."""

from pymol.Qt import QtCore, QtWidgets

from .column_profiles import format_proportion


class CategoryChecklist(QtCore.QAbstractListModel):
    def __init__(self, counts, selected, total, parent):
        super().__init__(parent)
        self.counts = counts
        self.visible = counts
        self.selected = set(selected)
        self.total = total

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.visible)

    def flags(self, index):
        return super().flags(index) | QtCore.Qt.ItemIsUserCheckable if index.isValid() else super().flags(index)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        label, count = self.visible[index.row()]
        if role == QtCore.Qt.DisplayRole:
            return f"{label}    {count:,} ({format_proportion(count, self.total)})"
        if role == QtCore.Qt.ToolTipRole:
            return label
        if role == QtCore.Qt.CheckStateRole:
            return QtCore.Qt.Checked if label in self.selected else QtCore.Qt.Unchecked
        return None

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if not index.isValid() or role != QtCore.Qt.CheckStateRole:
            return False
        label = self.visible[index.row()][0]
        if value == QtCore.Qt.Checked:
            self.selected.add(label)
        else:
            self.selected.discard(label)
        self.dataChanged.emit(index, index, [role])
        return True

    def search(self, query):
        self.beginResetModel()
        needle = query.casefold()
        self.visible = tuple(row for row in self.counts if needle in row[0].casefold())
        self.endResetModel()

    def set_all(self, checked):
        self.selected = {label for label, _ in self.counts} if checked else set()
        if self.visible:
            self.dataChanged.emit(self.index(0), self.index(len(self.visible) - 1), [QtCore.Qt.CheckStateRole])


class OtherCategoryPicker(QtWidgets.QMenu):
    applied = QtCore.Signal(object, bool)

    def __init__(self, parent, future, explicit, remainder, total):
        super().__init__(parent)
        self.future, self.explicit, self.remainder, self.total = future, explicit, remainder, total
        self.model = None
        self.use_remainder = False
        content = QtWidgets.QWidget()
        content.setMinimumWidth(330)
        layout = QtWidgets.QVBoxLayout(content)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Find category")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.list = QtWidgets.QListView()
        self.list.setUniformItemSizes(True)
        self.list.setMinimumHeight(190)
        layout.addWidget(self.list)
        self.status = QtWidgets.QLabel("Loading categories…")
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.select_all = QtWidgets.QPushButton("Select all")
        self.clear_all = QtWidgets.QPushButton("Clear all")
        self.apply_button = QtWidgets.QPushButton("Apply")
        for button in (self.select_all, self.clear_all, self.apply_button):
            button.setEnabled(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        action = QtWidgets.QWidgetAction(self)
        action.setDefaultWidget(content)
        self.addAction(action)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._collect)
        self.timer.start(30)
        self.aboutToHide.connect(self._cancel)

    def _collect(self):
        if not self.future.done():
            return
        self.timer.stop()
        try:
            counts = self.future.result()
        except Exception as exc:
            self.status.setText(f"Could not read categories: {exc}")
            return
        selected = {label for label, _ in counts
                    if label in self.explicit or (self.remainder is not None and label not in self.remainder)}
        self.use_remainder = self.remainder is not None and bool(counts) and len(selected) == len(counts)
        self.model = CategoryChecklist(counts, selected, self.total, self.list)
        self.list.setModel(self.model)
        self.search.textChanged.connect(self.model.search)
        self.model.search(self.search.text())
        self.model.dataChanged.connect(self._individual_change)
        self.select_all.clicked.connect(lambda: self._select_all(True))
        self.clear_all.clicked.connect(lambda: self._select_all(False))
        self.apply_button.clicked.connect(self._apply)
        for button in (self.select_all, self.clear_all, self.apply_button):
            button.setEnabled(True)
        self._update_count()

    def _update_count(self):
        self.status.setText(f"{len(self.model.selected):,} selected · {len(self.model.counts):,} categories")

    def _individual_change(self):
        self.use_remainder = False
        self._update_count()

    def _select_all(self, checked):
        self.model.set_all(checked)
        self.use_remainder = checked and bool(self.model.counts)
        self._update_count()

    def _cancel(self):
        self.timer.stop()
        self.future.cancel()

    def _apply(self):
        checked = tuple(label for label, _ in self.model.counts if label in self.model.selected)
        self.close()
        self.applied.emit(checked, self.use_remainder)
