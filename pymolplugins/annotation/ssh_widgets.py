"""OpenSSH profile selection and an asynchronous remote file browser."""

from concurrent.futures import ThreadPoolExecutor
import os
import posixpath
import threading
import traceback
from html import escape

from pymol.Qt import QtCore, QtWidgets

from pymolplugins.remote.ssh import (
    SshClient, SshLocation, default_ssh_program, is_ssh_path, parse_ssh_path,
    ssh_profiles, ssh_progress,
)
from .history_store import fuzzy_matches
from .qt_widgets import CollapsibleSection, ColumnCompletion


class SourceLocationControls(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, parent):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Open from"))
        self.connection = QtWidgets.QComboBox()
        self.connection.setEditable(True)
        self.connection.setAccessibleName("Open from")
        self.connection.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.connection.addItem("This computer")
        self.connection.lineEdit().setPlaceholderText("This computer or an SSH server")
        self.profile = self.connection
        row.addWidget(self.connection, 1)
        self.settings_button = QtWidgets.QPushButton("Connections…")
        self.settings_button.setAutoDefault(False)
        self.settings_button.clicked.connect(self._configure)
        row.addWidget(self.settings_button)
        layout.addLayout(row)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty("role", "error")
        layout.addWidget(self.error)
        self._build_settings()
        self._build_quick_start()
        self.completion = ColumnCompletion(self.connection.lineEdit())
        self._reload_profiles()
        self.connection.currentTextChanged.connect(self._changed)

    def _build_settings(self):
        self.settings_dialog = QtWidgets.QDialog(self)
        self.settings_dialog.setWindowTitle("SSH connections")
        self.settings_dialog.resize(620, 240)
        form = QtWidgets.QFormLayout(self.settings_dialog)
        form.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.config = QtWidgets.QLineEdit()
        self.config.setMinimumWidth(360)
        self.config.setPlaceholderText(os.path.expanduser("~/.ssh/config"))
        self.config.setToolTip("SSH config on the computer running PyMOL. A WSL config is not selected automatically.")
        self.program = QtWidgets.QLineEdit()
        self.program.setPlaceholderText("Use the configured SSH login program")
        self.program.setToolTip("Leave blank to use the detected login program, including your existing OTP wrapper.")
        self.advanced_section = CollapsibleSection("Advanced")
        advanced_form = QtWidgets.QFormLayout(self.advanced_section.content)
        for title, editor, filters in (
            ("SSH config", self.config, "SSH config files (*);;All files (*)"),
            ("SSH program", self.program, "SSH programs (*.exe *.py);;All files (*)"),
        ):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(editor, 1)
            button = QtWidgets.QPushButton("Browse…")
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, widget=editor, selected_filter=filters: self._browse_setting(widget, selected_filter))
            row.addWidget(button)
            (advanced_form if editor is self.program else form).addRow(title, row)
        form.addRow(self.advanced_section)
        self.setup_help_button = QtWidgets.QPushButton("First SSH connection…")
        self.setup_help_button.setAutoDefault(False)
        self.setup_help_button.setProperty("role", "quiet")
        self.setup_help_button.clicked.connect(self.show_quick_start)
        form.addRow(self.setup_help_button)
        self.dialog_error = QtWidgets.QLabel()
        self.dialog_error.setWordWrap(True)
        self.dialog_error.setProperty("role", "error")
        form.addRow(self.dialog_error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_settings)
        buttons.rejected.connect(self.settings_dialog.reject)
        form.addRow(buttons)

    def _build_quick_start(self):
        self.quick_start_dialog = QtWidgets.QDialog(self)
        self.quick_start_dialog.setWindowTitle("Quick start · Open and review structures")
        self.quick_start_dialog.resize(610, 470)
        layout = QtWidgets.QVBoxLayout(self.quick_start_dialog)
        layout.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.quick_start_text = QtWidgets.QTextBrowser()
        self.quick_start_text.setMinimumSize(560, 360)
        self.quick_start_text.setOpenExternalLinks(False)
        config = escape(os.path.expanduser("~/.ssh/config"))
        self.quick_start_text.setHtml(
            "<h3>Open your data in three steps</h3>"
            "<p><b>1. Choose a location.</b> In <b>Open from</b>, use <b>This computer</b> "
            "for local files, or choose your saved SSH server name.</p>"
            "<p><b>2. Choose a folder or table.</b> Paste its path, or use <b>Browse…</b>. "
            "For SSH, use the server's path, such as <code>/data/review/scores.tsv</code> "
            "or <code>~/review/structures</code>, not a Windows drive path.</p>"
            "<p><b>3. Click Open.</b> Folder and CSV/TSV inputs are detected automatically. "
            "If asked for a <b>Structure column</b>, choose the column containing PDB/CIF "
            "file paths, not a score or candidate name. Leave Import options alone unless needed.</p>"
            "<h3>Review and save</h3>"
            "<p>Inspect the structure, choose labels, then use <b>Save and next</b>. "
            "Ordinary label clicks are drafts; <b>Previous / Next</b> navigate. "
            "Use <b>Data → Recent configurations…</b> to reopen your work. "
            "SSH annotations are saved locally; remote files are not changed.</p>"
            "<h3>No SSH server listed?</h3>"
            "<p>Ask your administrator for the server address, username and login method. "
            "A saved SSH name is a <code>Host</code> entry in <code>" + config + "</code>. "
            "For example (replace both values):</p>"
            "<pre>Host my-lab\n    HostName server.example.org\n    User your-username</pre>"
            "<p>First confirm <code>ssh my-lab</code> works on the computer running PyMOL, "
            "including host trust and key/OTP setup. Then reopen <b>Connections…</b> and click "
            "<b>OK</b> to refresh the list. You can choose an existing config or login program "
            "there. Leave the program blank for automatic selection.</p>"
            "<p>The server needs <b>Python 3</b>. No SSHFS mount or WSL worker is required. "
            "The plugin does not create accounts or manage passwords.</p>"
            "<h3>If opening takes time</h3>"
            "<p><b>Connecting / authenticating</b>: the existing login program or OTP may be waiting. "
            "You can cancel. <b>File not found</b>: check the path on the selected server. "
            "<b>Permission denied</b>: confirm the same SSH login works outside PyMOL. "
            "Failures keep the current dataset and structure.</p>"
        )
        layout.addWidget(self.quick_start_text)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.quick_start_dialog.reject)
        layout.addWidget(buttons)

    def show_quick_start(self):
        self.quick_start_text.verticalScrollBar().setValue(0)
        self.quick_start_dialog.exec_()

    def _update_connection_action(self):
        self.settings_button.setText("Connections…" if self.remote or self.connection.count() > 1 else "Set up SSH…")

    def _browse_setting(self, editor, filters):
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(self.settings_dialog, "Select file", editor.text() or os.path.expanduser("~/.ssh"), filters)
        if selected:
            editor.setText(selected)

    def _configure(self):
        before = self.config.text(), self.program.text()
        program = self.program.text().strip() or default_ssh_program()
        self.program.setPlaceholderText(program or "SSH is not installed")
        self.dialog_error.hide()
        if self.settings_dialog.exec_() != QtWidgets.QDialog.Accepted:
            self.config.setText(before[0])
            self.program.setText(before[1])

    def _accept_settings(self):
        try:
            ssh_profiles(self.config.text().strip())
            SshClient(SshLocation("connection-check", "~", self.config.text().strip(), self.program.text().strip()))
        except (OSError, ValueError, RuntimeError) as exc:
            traceback.print_exc()
            self.dialog_error.setText(str(exc))
            self.dialog_error.show()
            return
        if self._reload_profiles():
            self.settings_dialog.accept()

    @property
    def remote(self):
        return self.connection.currentText().strip().casefold() != "this computer"

    def _changed(self, *args):
        self._update_connection_action()
        self.changed.emit()

    def _reload_profiles(self):
        chosen = self.connection.currentText()
        try:
            profiles = ssh_profiles(self.config.text().strip())
        except (OSError, ValueError) as exc:
            traceback.print_exc()
            self.error.setText(str(exc))
            self.error.show()
            return False
        self.error.hide()
        self.connection.blockSignals(True)
        try:
            self.connection.clear()
            self.connection.addItem(self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon), "This computer")
            for profile in profiles:
                self.connection.addItem(self.style().standardIcon(QtWidgets.QStyle.SP_DriveNetIcon), profile)
            self.connection.setCurrentText(chosen or "This computer")
            self.completion.set_columns(["This computer"] + profiles)
        finally:
            self.connection.blockSignals(False)
        self._update_connection_action()
        self.changed.emit()
        return True

    def source(self, path):
        if is_ssh_path(path):
            return parse_ssh_path(path).uri
        if not self.remote or not path:
            return path
        return SshLocation(self.connection.currentText().strip(), path, self.config.text().strip(), self.program.text().strip()).uri

    def set_source(self, source):
        """Restore connection choices and return only the path for the input."""
        self.blockSignals(True)
        try:
            if is_ssh_path(source):
                remote = parse_ssh_path(source)
                self.config.setText(remote.config)
                self.program.setText(remote.program)
                self._reload_profiles()
                self.connection.setCurrentText(remote.profile)
                return remote.path
            self.connection.setCurrentIndex(0)
            return source
        finally:
            self.blockSignals(False)

    def browse(self, path, *, directory=False, initial_directory=False):
        remote = parse_ssh_path(self.source(path or "~"))
        start = remote.path if directory or initial_directory else posixpath.dirname(remote.path) or "~"
        dialog = RemotePathDialog(remote.at(start), directory=directory, parent=self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            return dialog.selected_path
        return ""


class RemotePathDialog(QtWidgets.QDialog):
    def __init__(self, location, *, directory, parent=None):
        super().__init__(parent)
        self.location, self.directory = location, directory
        self.selected_path, self.current_path = "", location.path
        self.setWindowTitle("SSH · " + location.profile)
        self.resize(620, 480)
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit(location.path)
        self.up = QtWidgets.QPushButton("Up")
        self.go = QtWidgets.QPushButton("Open folder")
        row.addWidget(self.up)
        row.addWidget(self.path, 1)
        row.addWidget(self.go)
        layout.addLayout(row)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search files and folders")
        layout.addWidget(self.search)
        self.items = QtWidgets.QListWidget()
        layout.addWidget(self.items, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Open).setText("Choose folder" if directory else "Choose file")
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self._choose)
        self.buttons.rejected.connect(self.reject)
        self.go.clicked.connect(self._load)
        self.path.returnPressed.connect(self._load)
        self.up.clicked.connect(self._up)
        self.search.textChanged.connect(self._filter)
        self.search.installEventFilter(self)
        self.items.itemDoubleClicked.connect(self._activate)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ssh-browser")
        self.cancel = threading.Event()
        self.future = None
        self._entries = []
        self._has_listing = False
        self._progress_lock = threading.Lock()
        self._progress_message = ""
        self._displayed_progress = ""
        self._last_progress_message = ""
        self._progress_thread_id = None
        self._last_progress_on_main_thread = None
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._poll)
        self.finished.connect(self._close)
        self._load()

    def _load(self):
        if self.future is not None:
            return
        self.cancel.clear()
        with self._progress_lock:
            self._progress_message = ""
        self._displayed_progress = ""
        self.status.setText("Reading remote folder…")
        for control in (self.path, self.up, self.go):
            control.setEnabled(False)
        self.items.setEnabled(False)
        self.buttons.button(QtWidgets.QDialogButtonBox.Open).setEnabled(False)
        self.future = self.pool.submit(self._read_directory, self.path.text())
        self.timer.start()

    def _record_progress(self, message):
        with self._progress_lock:
            self._progress_message = message
            self._last_progress_message = message
            self._progress_thread_id = threading.get_ident()

    def _read_directory(self, path):
        with ssh_progress(self._record_progress):
            return SshClient(self.location).list_directory(
                path, cancel_event=self.cancel
            )

    def _poll(self):
        with self._progress_lock:
            progress = self._progress_message
        if progress and progress != self._displayed_progress:
            self._displayed_progress = progress
            self.status.setText(progress)
            app = QtCore.QCoreApplication.instance()
            self._last_progress_on_main_thread = bool(
                app is not None and QtCore.QThread.currentThread() == app.thread()
            )
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        self.timer.stop()
        for control in (self.path, self.up, self.go):
            control.setEnabled(True)
        try:
            listing = future.result()
        except Exception as exc:
            traceback.print_exc()
            self.path.setText(self.current_path)
            self.items.setEnabled(self._has_listing)
            self.buttons.button(QtWidgets.QDialogButtonBox.Open).setEnabled(self._has_listing)
            self.status.setText(str(exc))
            return
        changed_directory = listing["path"] != self.current_path
        self.current_path = listing["path"]
        self.path.setText(self.current_path)
        self._entries = [
            value for value in listing["entries"]
            if value.get("directory") or (not self.directory and value.get("file"))
        ]
        self._has_listing = True
        if changed_directory:
            self.search.clear()
        self._filter()
        self.items.setEnabled(True)
        self.buttons.button(QtWidgets.QDialogButtonBox.Open).setEnabled(True)
        self.status.setText(f"{len(self._entries):,} files and folders")

    def _filter(self, *args):
        by_name = {
            value["name"] + ("/" if value.get("directory") else ""): value
            for value in self._entries
        }
        self.items.clear()
        for name in fuzzy_matches(by_name, self.search.text()):
            item = QtWidgets.QListWidgetItem(name, self.items)
            item.setData(QtCore.Qt.UserRole, by_name[name])
        if self.items.count():
            self.items.setCurrentRow(0)

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                offset = 1 if key == QtCore.Qt.Key_Down else -1
                row = max(0, min(self.items.count() - 1, self.items.currentRow() + offset))
                if self.items.count():
                    self.items.setCurrentRow(row)
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                item = self.items.currentItem()
                if item is not None:
                    self._activate(item)
                return True
        return super().eventFilter(obj, event)

    def _up(self):
        self.path.setText(posixpath.dirname(self.current_path) or "/")
        self._load()

    def _activate(self, item):
        value = item.data(QtCore.Qt.UserRole)
        if value["directory"]:
            self.path.setText(value["path"])
            self._load()
        elif not self.directory:
            self.selected_path = value["path"]
            self.accept()

    def _choose(self):
        item = self.items.currentItem()
        if self.directory:
            self.selected_path = item.data(QtCore.Qt.UserRole)["path"] if item else self.current_path
            self.accept()
        elif item:
            self._activate(item)

    def _close(self, *args):
        self.cancel.set()
        self.timer.stop()
        if self.future is not None:
            self.future.cancel()
        self.pool.shutdown(wait=False)
