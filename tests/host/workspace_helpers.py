"""Isolate native window settings and size the hosted content in Qt checks."""

import os
from pathlib import Path


def isolate_workspace_settings(directory, window=None, mode="floating"):
    from pymol.Qt import QtCore, QtWidgets
    from pmg_qt import pymol_qt_gui

    path = Path(directory) / "workspace-windows.ini"
    os.environ["PYMOLPLUGINS_UI_SETTINGS"] = str(path)
    if mode is not None:
        settings = QtCore.QSettings(str(path), QtCore.QSettings.IniFormat)
        for key in ("structure_annotator", "batch_processing"):
            settings.setValue(key + "/mode", mode)
        settings.sync()
        assert settings.status() == QtCore.QSettings.NoError
    if window is None:
        # Widget-only checks need an explicit desktop fixture, not a runtime fallback.
        window = QtWidgets.QMainWindow()
        window.show()
    pymol_qt_gui.window = window
    # Keep the QApplication wrapper alive until the harness writes final JSON.
    pymol_qt_gui._acceptance_app = QtWidgets.QApplication.instance()
    return window


def resize_workspace(content, width, height):
    host = content.workspace_window
    assert host.isFloating(), "Resize the real main window for embedded layout checks"
    host.resize(width + host.width() - content.width(),
                height + host.height() - content.height())
