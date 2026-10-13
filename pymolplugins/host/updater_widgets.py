"""Nonblocking update controls; network and package writes run outside Qt."""

import threading
import traceback

from pymol.Qt import QtCore, QtWidgets

from pymolplugins import __version__
from . import updater
from .workspace import window_settings, _check_settings


_controller = None


class UpdateController(QtCore.QObject):
    completed = QtCore.Signal(str, object, object)

    def __init__(self):
        super().__init__()
        self.settings = window_settings()
        self.dialog = None
        self.busy = False
        self.restart_required = False
        self.release = None
        self.status = "Installed: " + __version__
        self.completed.connect(self._finished)

    def option(self, name, default):
        return self.settings.value("updates/" + name, default, type=bool)

    def _save_option(self, name, checked):
        self.settings.setValue("updates/" + name, bool(checked))
        self.settings.sync()
        try:
            _check_settings(self.settings)
        except OSError as exc:
            self.status = str(exc)
            self._refresh()

    def show(self):
        if self.dialog is None:
            dialog = QtWidgets.QDialog()
            dialog.setWindowTitle("PymolPlugins Updates")
            dialog.setMinimumWidth(420)
            layout = QtWidgets.QVBoxLayout(dialog)
            self.status_label = QtWidgets.QLabel(self.status)
            self.status_label.setWordWrap(True)
            self.status_label.setTextFormat(QtCore.Qt.PlainText)
            layout.addWidget(self.status_label)
            for name, text, default in (
                ("auto_check", "Check for updates when PyMOL starts", True),
                ("auto_install", "Automatically install stable updates (restart required)", False),
            ):
                control = QtWidgets.QCheckBox(text)
                control.setChecked(self.option(name, default))
                control.toggled.connect(lambda checked, key=name: self._save_option(key, checked))
                layout.addWidget(control)
            actions = QtWidgets.QHBoxLayout()
            self.check_button = QtWidgets.QPushButton("Check now")
            self.install_button = QtWidgets.QPushButton("Download and install")
            self.check_button.clicked.connect(self.check)
            self.install_button.clicked.connect(self.install)
            actions.addWidget(self.check_button)
            actions.addWidget(self.install_button)
            layout.addLayout(actions)
            self.dialog = dialog
        self._refresh()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _refresh(self):
        if self.dialog is not None:
            self.status_label.setText(self.status)
            self.check_button.setEnabled(not self.busy and not self.restart_required)
            self.install_button.setEnabled(bool(self.release and self.release["newer"])
                                           and not self.busy and not self.restart_required)

    def _run(self, kind, function, *args):
        if self.busy or self.restart_required:
            return
        self.busy = True
        self.status = ("Checking the latest stable GitHub release…" if kind == "check" else
                       "Downloading and verifying the update, then installing…")
        self._refresh()
        def work():
            result, error = None, None
            try:
                result = function(*args)
            except Exception as exc:
                error = str(exc)
                traceback.print_exc()
            self.completed.emit(kind, result, error)
        threading.Thread(target=work, name="pymolplugins-update", daemon=True).start()

    def check(self):
        self._run("check", updater.check_release, __version__)

    def install(self):
        if not self.release or not self.release["newer"]:
            return
        from pymol.plugins import plugins
        info = plugins.get("pymol_plugins")
        registered = info.filename if info is not None else ""
        self._run("install", updater.install_release, self.release, updater.__file__, registered)

    def _finished(self, kind, result, error):
        self.busy = False
        if error:
            self.status = "Update %s failed: %s" % (kind, error)
            print("PymolPlugins: " + self.status)
            if kind == "install":
                self.show()
        elif kind == "check":
            self.release = result
            if result["newer"]:
                self.status = "Version %s is available (installed: %s)." % (result["version"], __version__)
                if self.option("auto_install", False):
                    self.install()
                    return
                self.show()
            else:
                self.status = "Up to date: " + __version__
        else:
            self.restart_required = True
            self.status = ("Installed %s. Save your work and restart PyMOL to use it. "
                           "The running session has not been reloaded.\nPrevious package: %s" %
                           (result["version"], result["backup"]))
            self.show()
        self._refresh()


def controller():
    global _controller
    if _controller is None:
        _controller = UpdateController()
    return _controller


def show_updates():
    controller().show()


def start_update_check():
    if QtWidgets.QApplication.instance() is None:
        return
    instance = controller()
    if instance.option("auto_check", True):
        QtCore.QTimer.singleShot(1000, instance.check)
