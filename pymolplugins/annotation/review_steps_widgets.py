"""Compact editor for the linear review step list."""

import copy

from pymol.Qt import QtWidgets

from .review_steps import validate_steps


class ReviewStepsDialog(QtWidgets.QDialog):
    def __init__(self, steps, columns, step_cmd, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review steps")
        self.resize(680, 480)
        self.steps = copy.deepcopy(steps)
        self.columns = tuple(columns)
        self.step_cmd = step_cmd
        self._selected = -1
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        self.steps_list = QtWidgets.QListWidget()
        self.steps_list.setObjectName("reviewStepsList")
        layout.addWidget(self.steps_list)
        actions = QtWidgets.QHBoxLayout()
        for name, title, callback in (
            ("add_object_button", "Add object", lambda: self._add("object")),
            ("add_commands_button", "Add commands", lambda: self._add("commands")),
            ("move_up_button", "Move up", lambda: self._move(-1)),
            ("move_down_button", "Move down", lambda: self._move(1)),
            ("remove_button", "Remove", self._remove),
        ):
            button = QtWidgets.QPushButton(title)
            button.setObjectName(name)
            button.clicked.connect(callback)
            setattr(self, name, button)
            actions.addWidget(button)
        layout.addLayout(actions)
        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setObjectName("stepObjectName")
        self.path_column_combo = QtWidgets.QComboBox()
        self.path_column_combo.setObjectName("stepPathColumn")
        self.path_column_combo.setEditable(True)
        self.path_column_combo.addItems(list(columns))
        self.fixed_checkbox = QtWidgets.QCheckBox("Keep this object loaded and reuse it for the same path")
        self.fixed_checkbox.setObjectName("stepFixed")
        self.use_step_cmd = QtWidgets.QCheckBox("Use the existing 'When a structure opens' commands")
        self.use_step_cmd.setObjectName("stepUseOpenCommands")
        self.commands_edit = QtWidgets.QPlainTextEdit()
        self.commands_edit.setObjectName("stepCommands")
        self.commands_edit.setPlaceholderText("Object: run once after loading. Commands: run at this position.")
        form.addRow("Object name", self.name_edit)
        form.addRow("Path column", self.path_column_combo)
        form.addRow(self.fixed_checkbox)
        form.addRow(self.use_step_cmd)
        form.addRow("Commands", self.commands_edit)
        layout.addLayout(form)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QtWidgets.QDialogButtonBox.Save)
        self.apply_button.setObjectName("saveReviewSteps")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.steps_list.currentRowChanged.connect(self._select)
        self.name_edit.textEdited.connect(self._store)
        self.path_column_combo.currentTextChanged.connect(self._store)
        self.fixed_checkbox.toggled.connect(self._store)
        self.use_step_cmd.toggled.connect(self._store)
        self.commands_edit.textChanged.connect(self._store)
        self._render(0)

    def _title(self, item):
        if item["type"] == "commands":
            return "Commands · " + ("When a structure opens" if "commands" not in item else "custom")
        return "Object · " + item["name"] + (" · fixed" if item.get("fixed") else "")

    def _render(self, index):
        self.steps_list.blockSignals(True)
        self.steps_list.clear()
        self.steps_list.addItems([self._title(item) for item in self.steps])
        self.steps_list.setCurrentRow(index if self.steps else -1)
        self.steps_list.blockSignals(False)
        self._select(self.steps_list.currentRow())

    def _select(self, index):
        self._selected = index
        self._loading = True
        item = self.steps[index] if index >= 0 else {"type": "commands", "commands": ""}
        is_object = item["type"] == "object"
        self.name_edit.setText(item.get("name", ""))
        self.path_column_combo.setCurrentText(item.get("path_column", ""))
        self.fixed_checkbox.setChecked(item.get("fixed", False))
        self.use_step_cmd.setChecked(not is_object and "commands" not in item)
        self.commands_edit.setPlainText(item.get("on_load_cmd", "") if is_object else item.get("commands", self.step_cmd))
        self.name_edit.setEnabled(is_object)
        self.path_column_combo.setEnabled(is_object and item.get("name") != "candidate")
        self.fixed_checkbox.setEnabled(is_object and item.get("name") != "candidate")
        self.use_step_cmd.setVisible(not is_object)
        self.commands_edit.setEnabled(is_object or "commands" in item)
        self._loading = False

    def _store(self, *args):
        if self._loading or self._selected < 0:
            return
        item = self.steps[self._selected]
        if item["type"] == "object":
            name = self.name_edit.text().strip()
            item.update(name=name, on_load_cmd=self.commands_edit.toPlainText())
            if name == "candidate":
                item.pop("path_column", None)
                item.pop("fixed", None)
            else:
                item.update(path_column=self.path_column_combo.currentText().strip(), fixed=self.fixed_checkbox.isChecked())
            self.path_column_combo.setEnabled(name != "candidate")
            self.fixed_checkbox.setEnabled(name != "candidate")
        elif self.use_step_cmd.isChecked():
            item.pop("commands", None)
            self.commands_edit.setEnabled(False)
        else:
            item["commands"] = self.commands_edit.toPlainText()
            self.commands_edit.setEnabled(True)
        self.steps_list.item(self._selected).setText(self._title(item))

    def _add(self, kind):
        if kind == "object":
            names = {item.get("name") for item in self.steps}
            number = 1
            while "context%d" % number in names:
                number += 1
            item = {"type": "object", "name": "context%d" % number,
                    "path_column": self.columns[0] if self.columns else "", "fixed": False}
        else:
            item = {"type": "commands", "commands": ""}
        self.steps.append(item)
        self._render(len(self.steps) - 1)

    def _move(self, offset):
        source, target = self._selected, self._selected + offset
        if 0 <= source < len(self.steps) and 0 <= target < len(self.steps):
            self.steps[source], self.steps[target] = self.steps[target], self.steps[source]
            self._render(target)

    def _remove(self):
        if self._selected >= 0:
            index = self._selected
            del self.steps[index]
            self._render(min(index, len(self.steps) - 1))

    def _accept(self):
        try:
            self.steps = validate_steps(self.steps, self.step_cmd, self.columns)
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()
