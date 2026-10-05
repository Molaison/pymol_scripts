"""Native PyMOL hosts for persistent, dockable plugin workspaces."""

import os
import traceback

from pymol.Qt import QtCore, QtGui, QtWidgets


_MODES = {"docked": "Embedded in PyMOL", "floating": "Floating window"}


def window_settings():
    """Return user settings, or the explicit INI file used by an isolated host."""
    path = os.environ.get("PYMOLPLUGINS_UI_SETTINGS")
    if path:
        return QtCore.QSettings(path, QtCore.QSettings.IniFormat)
    return QtCore.QSettings(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                            "PymolPlugins", "Workspaces")


def _check_settings(settings):
    if settings.status() != QtCore.QSettings.NoError:
        raise OSError("Cannot read or save window settings: " + settings.fileName())


def _reserve_viewport(main_window):
    docks = [dock for dock in main_window.findChildren(PanelWindow)
             if not dock._closed and not dock.isFloating()]
    corners = getattr(main_window, "_pymolplugins_dock_corners", {})
    for corner, area in ((QtCore.Qt.BottomLeftCorner, QtCore.Qt.LeftDockWidgetArea),
                         (QtCore.Qt.BottomRightCorner, QtCore.Qt.RightDockWidgetArea)):
        if any(main_window.dockWidgetArea(dock) == area for dock in docks):
            if corner not in corners:
                corners[corner] = main_window.corner(corner)
            main_window.setCorner(corner, area)
        elif corner in corners:
            main_window.setCorner(corner, corners.pop(corner))
    main_window._pymolplugins_dock_corners = corners
    viewport = main_window.centralWidget()
    if viewport is None:
        return
    previous = getattr(main_window, "_pymolplugins_viewport_minimum", None)
    if docks:
        if previous is None:
            previous = viewport.minimumSize()
            main_window._pymolplugins_viewport_minimum = previous
        viewport.setMinimumSize(previous.expandedTo(QtCore.QSize(320, 180)))
    elif previous is not None:
        viewport.setMinimumSize(previous)
        del main_window._pymolplugins_viewport_minimum


class WindowModeButton(QtWidgets.QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.host = None
        self.setProperty("role", "icon")
        self.setFixedSize(34, 34)
        self.setAccessibleName("Window mode")
        self.setToolTip("Window mode")
        self.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_TitleBarNormalButton))
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(self)
        action_group_class = getattr(QtWidgets, "QActionGroup", None) or QtGui.QActionGroup
        self.mode_group = action_group_class(self)
        self.mode_group.setExclusive(True)
        self.actions_by_mode = {}
        for mode, title in _MODES.items():
            action = menu.addAction(title)
            action.setCheckable(True)
            self.mode_group.addAction(action)
            action.triggered.connect(lambda _checked=False, value=mode: self.host.set_mode(value))
            self.actions_by_mode[mode] = action
        self.setMenu(menu)
        self.setEnabled(False)

    def bind(self, host):
        self.host = host
        host.topLevelChanged.connect(self.refresh)
        self.setEnabled(True)
        self.refresh()

    def refresh(self, *args):
        for mode, action in self.actions_by_mode.items():
            action.setChecked(mode == self.host.mode)
        self.setToolTip("Window mode: " + _MODES[self.host.mode])

    def unbind(self):
        self.host.topLevelChanged.disconnect(self.refresh)
        self.host = None
        self.setEnabled(False)


class PanelWindow(QtWidgets.QDockWidget):
    def __init__(self, content, key, main_window, mode=None):
        self._restoring, self._closed, self._closing = True, False, False
        self._content_closed = False
        self.content, self.main_window, self.key = content, main_window, key
        self.settings = window_settings()
        self.settings.beginGroup(key)
        saved_mode = self.settings.value("mode", "docked")
        selected_mode = saved_mode if mode is None else mode
        if selected_mode not in _MODES:
            raise ValueError("Invalid workspace mode: %r" % selected_mode)
        area = int(self.settings.value("dock_area", int(QtCore.Qt.LeftDockWidgetArea)))
        if area not in (int(QtCore.Qt.LeftDockWidgetArea), int(QtCore.Qt.RightDockWidgetArea)):
            raise ValueError("Invalid workspace dock area: %r" % area)
        self._dock_area = QtCore.Qt.DockWidgetArea(area)
        self._dock_width = int(self.settings.value("dock_width", content.minimumWidth()))
        if self._dock_width <= 0:
            raise ValueError("Workspace dock width must be positive")
        self._floating_geometry = self.settings.value("floating_geometry", QtCore.QByteArray())
        self._floating_position = self.settings.value("floating_position", None)
        _check_settings(self.settings)
        super().__init__(content.windowTitle(), main_window)
        self._floating_minimum = content.minimumSize()
        self._floating_size = content.size()
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._persist)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._persist)
        self.setObjectName("PymolPlugins_" + key)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.setWidget(content)
        content.workspace_window = self
        self.main_window.addDockWidget(self._dock_area, self, QtCore.Qt.Horizontal)
        self.topLevelChanged.connect(self._native_mode_changed)
        self.dockLocationChanged.connect(self._dock_location_changed)
        content.window_mode_button.bind(self)
        self._apply_mode(selected_mode)
        self._restoring = False

    @property
    def mode(self):
        return "floating" if self.isFloating() else "docked"

    def _tabify(self):
        for other in self.main_window.findChildren(PanelWindow):
            if (other is not self and not other._closed and not other.isFloating()
                    and self.main_window.dockWidgetArea(other) == self._dock_area):
                self.main_window.tabifyDockWidget(other, self)
                break

    def _minimum_size(self):
        size = self._floating_minimum if self.isFloating() else QtCore.QSize(640, 440)
        self.content.setMinimumSize(size)
        _reserve_viewport(self.main_window)

    def _apply_mode(self, mode):
        self.setFloating(mode == "floating")
        self._minimum_size()
        if self.isFloating():
            self._restore_floating_geometry()
        else:
            self._tabify()
            self.main_window.resizeDocks([self], [self._dock_width], QtCore.Qt.Horizontal)
        self.content.window_mode_button.refresh()

    def _restore_floating_geometry(self):
        if not self._floating_geometry:
            self.resize(self._floating_size)
            return
        if not self.restoreGeometry(self._floating_geometry):
            raise ValueError("Invalid floating window geometry for " + self.key)
        if self._floating_position is None:
            return
        frame = self.frameGeometry()
        bounds = QtCore.QRect(self._floating_position, frame.size())
        # Windows can change dock frame margins; keep an available saved position exact.
        if any(screen.availableGeometry().contains(bounds) for screen in QtWidgets.QApplication.screens()):
            self.move(self._floating_position)
            return
        available = self.windowHandle().screen().availableGeometry()
        # Qt can restore the client area while leaving the native title bar off-screen.
        x = max(available.left(), min(frame.x(), available.right() - frame.width() + 1))
        y = max(available.top(), min(frame.y(), available.bottom() - frame.height() + 1))
        self.move(x, y)

    def set_mode(self, mode):
        if mode not in _MODES:
            raise ValueError("Invalid workspace mode: %r" % mode)
        if mode != self.mode:
            self._capture_geometry()
            self._restoring = True
            try:
                self._apply_mode(mode)
            finally:
                self._restoring = False
        self.present()
        self._persist(capture=False)

    def present(self):
        self.content.show()
        self.show()
        self.raise_()
        (self if self.isFloating() else self.main_window).activateWindow()

    def _native_mode_changed(self, floating):
        if not self._restoring and not self._closed:
            self._minimum_size()
            self._save_timer.start(250)

    def _dock_location_changed(self, area):
        if area in (QtCore.Qt.LeftDockWidgetArea, QtCore.Qt.RightDockWidgetArea):
            self._dock_area = area
            if not self._restoring and not self._closed:
                _reserve_viewport(self.main_window)
                self._save_timer.start(250)

    def _capture_geometry(self):
        if self.isFloating():
            self._floating_geometry = self.saveGeometry()
            self._floating_position = self.pos()
        elif self.isVisible():
            self._dock_width = self.width()

    def _geometry_changed(self):
        if not self._restoring and not self._closed:
            self._capture_geometry()
            self._save_timer.start(250)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._geometry_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._geometry_changed()

    def save_preferences(self, capture=True):
        self._save_timer.stop()
        if capture:
            self._capture_geometry()
        self.settings.setValue("mode", self.mode)
        self.settings.setValue("dock_area", int(self._dock_area))
        self.settings.setValue("dock_width", self._dock_width)
        if self._floating_geometry:
            self.settings.setValue("floating_geometry", self._floating_geometry)
            self.settings.setValue("floating_position", self._floating_position)
        self.settings.sync()
        _check_settings(self.settings)

    def _persist(self, capture=True):
        if self._closed:
            return
        try:
            self.save_preferences(capture)
        except Exception as exc:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Window settings", str(exc))

    def closeEvent(self, event):
        if not self._content_closed:
            self._closing = True
            try:
                accepted = self.content.close()
            finally:
                self._closing = False
            if not accepted:
                event.ignore()
                return
        self._persist()
        self._closed = True
        self.main_window.removeDockWidget(self)
        _reserve_viewport(self.main_window)
        self.content.window_mode_button.unbind()
        self.content.workspace_window = None
        # Preserve QWidget.close() semantics for callers still holding the content.
        self.content.setParent(None)
        self.content = None
        super().closeEvent(event)
        self.deleteLater()


def show_workspace(content, key, mode=None):
    """Show one content instance in the actual PyMOL desktop window."""
    from pymol.gui import get_qtwindow

    host = getattr(content, "workspace_window", None)
    if host is None:
        main_window = get_qtwindow()
        if not isinstance(main_window, QtWidgets.QMainWindow):
            raise RuntimeError("Open the PyMOL Qt main window before opening this workspace.")
        host = PanelWindow(content, key, main_window, mode)
    elif mode is not None:
        host.set_mode(mode)
    host.present()
    return content


def workspace_closed(content):
    """Finish the host only after its content's close guards accepted closure."""
    host = getattr(content, "workspace_window", None)
    if host is not None:
        host._content_closed = True
        if not host._closing:
            host.close()
