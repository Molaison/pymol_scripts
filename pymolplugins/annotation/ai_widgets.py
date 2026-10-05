"""AI command preparation in the existing Automation page, never execution."""

import json
import os
from pathlib import Path, PureWindowsPath
import sys
from urllib.parse import urlsplit

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets

from .ai_commands import build_prompt, output_schema, parse_draft
from .command_templates import VIEW_PRESETS, viewing_preset
from .panel_style import STYLE
from ..host.workspace import window_settings
from ..remote.client import _strict_json_loads


_SESSION_API_KEY = ""


def custom_provider_process():
    executable = Path(sys.executable)
    if os.name == "nt" and executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    if not executable.is_file():
        raise ValueError("Cannot find the Python interpreter beside PyMOL")
    return str(executable), [str(Path(__file__).with_name("ai_provider.py"))]


def connection_settings():
    settings = window_settings()
    settings.beginGroup("ai_commands")
    connection = {name: str(settings.value(name, default)) for name, default in
                  (("mode", "codex"), ("base_url", ""), ("model", ""), ("api", "responses"))}
    key = _SESSION_API_KEY
    protected = settings.value("protected_key", "")
    if protected:
        from .ai_provider import unprotect_api_key
        key = unprotect_api_key(str(protected))
    connection["api_key"] = key
    return connection


class AIConnectionDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("AI connection")
        self.setStyleSheet(STYLE)
        self.process = None
        self.saved = False
        self.connection = connection_settings()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Codex (Debian WSL)", "codex")
        self.mode.addItem("Custom API", "custom")
        self.mode.setCurrentIndex(self.mode.findData(self.connection["mode"]))
        layout.addWidget(self.mode)
        self.custom = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.custom)
        form.setContentsMargins(0, 0, 0, 0)
        self.base_url = QtWidgets.QLineEdit(self.connection["base_url"])
        self.base_url.setMinimumWidth(330)
        self.base_url.setPlaceholderText("https://api.example.org/v1")
        self.api = QtWidgets.QComboBox()
        self.api.addItem("Responses", "responses")
        self.api.addItem("Chat Completions", "chat_completions")
        self.api.setCurrentIndex(self.api.findData(self.connection["api"]))
        self.key = QtWidgets.QLineEdit(self.connection["api_key"])
        self.key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key.setAccessibleName("API key")
        self.base_url.textEdited.connect(lambda: self.key.clear())
        self.remember = QtWidgets.QCheckBox("Remember key on this computer")
        self.remember.setEnabled(os.name == "nt")
        settings = window_settings()
        self.remember.setChecked(bool(settings.value("ai_commands/protected_key", "")))
        self.remember.setToolTip("Encrypted for your Windows account. Otherwise kept only until PyMOL exits.")
        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)
        self.model.addItem(self.connection["model"])
        self.model.lineEdit().setPlaceholderText("Model ID")
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.models_button = QtWidgets.QPushButton("Refresh")
        self.models_button.setAutoDefault(False)
        self.models_button.clicked.connect(self.load_models)
        model_row.addWidget(self.models_button)
        form.addRow("API URL", self.base_url)
        form.addRow("API format", self.api)
        form.addRow("API key", self.key)
        form.addRow(self.remember)
        form.addRow("Model", model_row)
        layout.addWidget(self.custom)
        self.custom.setVisible(self.mode.currentData() == "custom")
        self.mode.currentIndexChanged.connect(lambda: self.custom.setVisible(self.mode.currentData() == "custom"))
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty("role", "error")
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"mode": self.mode.currentData(), "base_url": self.base_url.text().strip(),
                "api": self.api.currentData(), "model": self.model.currentText().strip(),
                "api_key": self.key.text().strip()}

    def show_error(self, message):
        self.error.setText(str(message))
        self.error.show()

    def load_models(self):
        if self.process is not None:
            return
        self.error.hide()
        process = self.process = QtCore.QProcess(self)
        connection = self.values()
        process.started.connect(lambda: process.write((json.dumps({"action": "models", "connection": connection}) + "\n").encode()))
        process.finished.connect(lambda code, _status: self.models_finished(process, code))
        process.errorOccurred.connect(lambda error: self.models_finished(process, -1)
                                     if error == QtCore.QProcess.FailedToStart else None)
        self.models_button.setEnabled(False)
        try:
            program, arguments = custom_provider_process()
            process.start(program, arguments)
        except Exception as exc:
            self.models_finished(process, -1)
            self.show_error(exc)

    def models_finished(self, process, code):
        if process is not self.process:
            process.deleteLater()
            return
        self.process = None
        self.models_button.setEnabled(True)
        try:
            if code != 0:
                raise ValueError("Cannot read models from this API")
            result = _strict_json_loads(bytes(process.readAllStandardOutput()).decode())
            if not result.get("ok"):
                raise ValueError(result.get("error", "Cannot read model list"))
            chosen = self.model.currentText()
            self.model.clear()
            self.model.addItems(result["models"])
            self.model.setCurrentText(chosen)
        except Exception as exc:
            self.show_error(exc)
        process.deleteLater()

    def save(self):
        global _SESSION_API_KEY
        try:
            connection = self.values()
            if connection["mode"] == "custom":
                url = urlsplit(connection["base_url"])
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError("Enter an HTTP(S) API URL without credentials or query parameters")
                if not connection["model"]:
                    raise ValueError("Choose or enter a model ID")
                if not connection["api_key"]:
                    raise ValueError("Enter an API key")
            protected = ""
            if self.remember.isChecked() and connection["api_key"]:
                from .ai_provider import protect_api_key
                protected = protect_api_key(connection["api_key"])
            settings = window_settings()
            settings.beginGroup("ai_commands")
            for name in ("mode", "base_url", "api", "model"):
                settings.setValue(name, connection[name])
            settings.setValue("protected_key", protected)
            settings.sync()
            if settings.status() != QtCore.QSettings.NoError:
                raise OSError("Cannot save AI connection settings")
            _SESSION_API_KEY = connection["api_key"]
            self.saved = True
            self.accept()
        except Exception as exc:
            self.show_error(exc)

    def done(self, result):
        if self.process is not None:
            self.process.write(b"cancel\n")
            self.process.closeWriteChannel()
        super().done(result)


def provider_process():
    """Return a launch path for the interpreter that actually owns the file."""
    script = Path(__file__).with_name("ai_provider.py")
    if os.name != "nt":
        return sys.executable, [str(script)]
    path = PureWindowsPath(script)
    if path.drive.lower() in {r"\\wsl.localhost\debian", r"\\wsl$\debian"}:
        linux_path = "/" + "/".join(path.parts[1:])
    elif len(path.drive) == 2 and path.drive[1] == ":":
        linux_path = "/mnt/" + path.drive[0].lower() + "/" + "/".join(path.parts[1:])
    else:
        raise ValueError("AI helper must be installed on a Windows drive or Debian WSL path")
    return "wsl.exe", ["--distribution", "Debian", "--exec", "python3", linux_path]


def command_editors(panel):
    return {"on_open": panel.step_cmd_edit,
            **{"after_label/" + value: edit for value, edit in panel._bucket_inputs.items()}}


def current_context(panel):
    from . import plugin
    state = plugin.STATE
    entry = state.current_entry()
    return {"object": state.current_object, "selection": state.current_object,
            "index": str(state.index), "count": str(len(state.files)),
            "file": state.current_file(),
            "entry_id": state.current_entry_id(),
            "row_number": str(entry.row_number or "") if entry else "",
            "label": plugin._annotation_fields()[0]["name"],
            "reference_object": getattr(panel, "_reference_object", "")}


class AICommandDialog(QtWidgets.QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.process = None
        self.response = None
        self.request_steps = {}
        self.preview_editors = {}
        self.draft_context = None
        self.draft_steps = {}
        self.setWindowTitle("Command drafts")
        self.setStyleSheet(STYLE)
        self.resize(720, 700)
        self.setMinimumSize(520, 480)
        self._build()
        self.deadline = QtCore.QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.cancel("Timed out — editors unchanged"))

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        presets = QtWidgets.QHBoxLayout()
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItem("Choose a viewing preset…", "")
        for key, title in VIEW_PRESETS.items():
            self.preset_combo.addItem(title, key)
        self.preset_button = QtWidgets.QPushButton("Use preset")
        self.preset_button.setAutoDefault(False)
        self.preset_button.clicked.connect(self.use_preset)
        presets.addWidget(self.preset_combo, 1)
        presets.addWidget(self.preset_button)
        layout.addLayout(presets)
        self.triggers = QtWidgets.QListWidget()
        self.triggers.setMaximumHeight(110)
        for key in command_editors(self.panel):
            title = "When a structure opens" if key == "on_open" else "After saving label: " + key[12:]
            item = QtWidgets.QListWidgetItem(title, self.triggers)
            item.setData(QtCore.Qt.UserRole, key)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if key == "on_open" else QtCore.Qt.Unchecked)
        layout.addWidget(self.triggers)
        connection_row = QtWidgets.QHBoxLayout()
        self.connection_label = QtWidgets.QLabel()
        self.connection_label.setTextFormat(QtCore.Qt.PlainText)
        self.connection_button = QtWidgets.QPushButton("AI settings…")
        self.connection_button.setAutoDefault(False)
        self.connection_button.clicked.connect(self.configure_connection)
        connection_row.addWidget(self.connection_label, 1)
        connection_row.addWidget(self.connection_button)
        layout.addLayout(connection_row)
        self.refresh_connection()
        self.request_edit = QtWidgets.QPlainTextEdit()
        self.request_edit.setPlaceholderText("Example: On open show cartoon, color chains, and zoom. After Keep, color the candidate cyan.")
        self.request_edit.setMaximumHeight(100)
        layout.addWidget(self.request_edit)
        buttons = QtWidgets.QHBoxLayout()
        self.generate_button = QtWidgets.QPushButton("Generate draft")
        self.generate_button.setProperty("role", "primary")
        self.generate_button.clicked.connect(self.generate)
        self.cancel_button = QtWidgets.QPushButton("Cancel generation")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda: self.cancel())
        buttons.addWidget(self.generate_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QtWidgets.QLabel()
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.previews = QtWidgets.QTabWidget()
        layout.addWidget(self.previews, 1)
        footer = QtWidgets.QHBoxLayout()
        self.insert_button = QtWidgets.QPushButton("Insert draft…")
        self.insert_button.setToolTip("Replace selected command drafts only. Use Apply commands in Settings to enable them.")
        self.insert_button.setEnabled(False)
        self.insert_button.clicked.connect(self.insert)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        footer.addWidget(self.insert_button)
        footer.addWidget(close)
        layout.addLayout(footer)

    def refresh_connection(self):
        connection = connection_settings()
        self.connection_label.setText("Codex (Debian WSL)" if connection["mode"] == "codex"
                                      else connection["model"] or "Custom API")
        self.connection_label.setToolTip(connection["base_url"] if connection["mode"] == "custom"
                                        else "Use the provider and model from your existing Codex configuration")

    def configure_connection(self):
        self.connection_dialog = AIConnectionDialog(self)
        if self.connection_dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.refresh_connection()

    def selected_steps(self):
        editors = command_editors(self.panel)
        return {self.triggers.item(i).data(QtCore.Qt.UserRole):
                editors[self.triggers.item(i).data(QtCore.Qt.UserRole)].toPlainText()
                for i in range(self.triggers.count())
                if self.triggers.item(i).checkState() == QtCore.Qt.Checked}

    def show_drafts(self, drafts):
        self.previews.clear()
        for edit in self.preview_editors.values():
            edit.deleteLater()
        self.preview_editors = {}
        self.draft_context = dict(self.context)
        self.draft_steps = dict(self.request_steps)
        for key, text in drafts.items():
            edit = QtWidgets.QPlainTextEdit(text)
            edit.setObjectName("commandEditor")
            self.preview_editors[key] = edit
            self.previews.addTab(edit, "On open" if key == "on_open" else key[12:])
        self.insert_button.setEnabled(True)

    def use_preset(self):
        from .ai_commands import SCHEMA
        try:
            self.context = current_context(self.panel)
            self.request_steps = self.selected_steps()
            if not self.request_steps:
                raise ValueError("Select a trigger first")
            commands, note = viewing_preset(self.preset_combo.currentData(), self.context["object"], cmd)
            document = {"schema": SCHEMA, "steps": [
                {"step_id": key, "commands": commands} for key in self.request_steps]}
            drafts = parse_draft(json.dumps(document), self.request_steps, self.context, cmd)
            self.show_drafts(drafts)
            self.feedback(note or "Preset ready")
        except Exception as exc:
            self.feedback(str(exc), True)

    def feedback(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: " + ("#982e36" if error else "#2854ab") + ";")

    def generate(self):
        if self.process is not None:
            return
        steps = self.selected_steps()
        request = self.request_edit.toPlainText().strip()
        if not steps or not request:
            self.feedback("Choose at least one trigger and describe the requested commands", True)
            return
        self.response = None
        self.insert_button.setEnabled(False)
        try:
            self.context = current_context(self.panel)
            if not self.context["object"] or not cmd.count_atoms(self.context["object"]):
                raise ValueError("Open a candidate structure first so commands can be checked")
            self.request_steps = steps
            self.request_payload = {
                "instructions": build_prompt(self.request_steps, cmd.get_names("objects"), self.context),
                "input": request, "schema": output_schema(self.request_steps)}
            connection = connection_settings()
            program, arguments = custom_provider_process() if connection["mode"] == "custom" else provider_process()
            process = QtCore.QProcess(self)
            self.process = process
            # Credentials are sent only over stdin, never stored in request_payload/logs.
            payload = dict(self.request_payload, connection=connection if connection["mode"] == "custom" else {"mode": "codex"})
            process.started.connect(lambda: process.write((json.dumps(payload) + "\n").encode("utf-8"))
                                    if process is self.process else process.closeWriteChannel())
            process.finished.connect(lambda code, status: self.finished(process, code))
            process.errorOccurred.connect(lambda error: self.process_error(process, error))
            self.generate_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.triggers.setEnabled(False)
            for widget in (self.preset_combo, self.preset_button, self.connection_button, self.request_edit):
                widget.setEnabled(False)
            self.feedback("Generating · " + self.connection_label.text())
            self.deadline.start(135000)
            process.start(program, arguments)
        except Exception as exc:
            self._idle()
            self.feedback(str(exc), True)

    def process_error(self, process, error):
        if process is self.process and error == QtCore.QProcess.FailedToStart:
            self.cancel("Cannot start the selected AI connection helper")

    def _idle(self):
        self.deadline.stop()
        self.generate_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.triggers.setEnabled(True)
        for widget in (self.preset_combo, self.preset_button, self.connection_button, self.request_edit):
            widget.setEnabled(True)
        self.insert_button.setEnabled(bool(self.preview_editors))

    def finished(self, process, code):
        if process is not self.process:
            process.deleteLater()
            return
        self.process = None
        self._idle()
        try:
            if code != 0:
                raise ValueError("AI helper exited unsuccessfully; check the selected connection")
            response = _strict_json_loads(bytes(process.readAllStandardOutput()).decode("utf-8"))
            if response.get("ok") is not True:
                raise ValueError(response.get("error", "AI request failed"))
            self.receive_response(response)
        except Exception as exc:
            self.feedback("Failed — " + str(exc), True)
        process.deleteLater()

    def receive_response(self, response):
        """One commit boundary for received model text; invalid text changes no editor."""
        self.insert_button.setEnabled(False)
        try:
            drafts = parse_draft(response["text"], self.request_steps, self.context, cmd)
            self.response = response
            self.show_drafts(drafts)
            identity = " / ".join(str(response[key]) for key in ("provider", "model") if response.get(key))
            self.feedback("Draft ready" + (" · " + identity if identity else ""))
        except Exception as exc:
            self.insert_button.setEnabled(bool(self.preview_editors))
            self.feedback("Failed — " + str(exc), True)

    def cancel(self, message="Cancelled — existing editors unchanged"):
        process, self.process = self.process, None
        if process is not None:
            process.write(b"cancel\n")
            process.closeWriteChannel()
        self._idle()
        self.feedback(message)

    def insert(self):
        from .ai_commands import SCHEMA
        try:
            editors = command_editors(self.panel)
            if current_context(self.panel) != self.draft_context or any(
                    key not in editors or editors[key].toPlainText() != text
                    for key, text in self.draft_steps.items()):
                raise ValueError("Structure or editors changed while generating; generate a fresh draft")
            document = {"schema": SCHEMA, "steps": [
                {"step_id": key, "commands": [line for line in edit.toPlainText().splitlines()
                                              if line.strip() and not line.lstrip().startswith("#")]}
                for key, edit in self.preview_editors.items()]}
            drafts = parse_draft(json.dumps(document), self.draft_steps, self.draft_context, cmd)
        except Exception as exc:
            self.feedback("Not inserted — " + str(exc), True)
            return
        answer = QtWidgets.QMessageBox.question(self, "Replace selected command drafts?",
            "Replace drafts for %d selected triggers? Other editors are unchanged.\n"
            "Nothing will execute. You must still use Apply commands in Settings." % len(drafts),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        for key, text in drafts.items():
            editors[key].setPlainText(text)
        self.accept()

    def reject(self):
        self.cancel()
        super().reject()


def show_ai_commands(panel, preset=False):
    panel.ai_dialog = AICommandDialog(panel)
    if preset:
        panel.ai_dialog.preset_combo.setCurrentIndex(1)
        panel.ai_dialog.preset_combo.setFocus()
    panel.ai_dialog.exec_()
