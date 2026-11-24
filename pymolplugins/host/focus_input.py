"""Dispatch a settled focus tap without PyMOL's native double-click delay."""

from weakref import ref

from pymol.Qt import QtCore, QtWidgets
from pymol.gui import get_qtwindow


_EVENT = getattr(QtCore.QEvent, "Type", QtCore.QEvent)
_BUTTON = getattr(QtCore.Qt, "MouseButton", QtCore.Qt)
_MODIFIER = getattr(QtCore.Qt, "KeyboardModifier", QtCore.Qt)
_LEFT = _BUTTON.LeftButton
_NONE = _MODIFIER.NoModifier
# P_GLUT_SINGLE_LEFT in PyMOL's layer0/os_gl_glut.h.
_SINGLE_LEFT = 100


class FocusClickInput(QtCore.QObject):
    def __init__(self, wizard, viewport):
        super().__init__(viewport)
        self.wizard = ref(wizard)
        self.viewport = viewport
        self.press = None
        self.ignore_release = False
        viewport.installEventFilter(self)

    @classmethod
    def attach(cls, wizard):
        window = get_qtwindow()
        viewport = getattr(window, "pymolwidget", None)
        if viewport is not None and callable(getattr(viewport, "_event_x_y_mod", None)):
            return cls(wizard, viewport)
        return None

    def close(self):
        self.viewport.removeEventFilter(self)
        self.deleteLater()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (_EVENT.MouseButtonPress, _EVENT.MouseButtonRelease,
                        _EVENT.MouseButtonDblClick, _EVENT.MouseMove):
            return False
        if kind == _EVENT.MouseMove and self.press is None:
            return False
        wizard = self.wizard()
        if wizard is None or wizard.closed or wizard.cmd.get_wizard() is not wizard:
            self.press = None
            self.ignore_release = False
            return False
        if self.press is not None and kind == _EVENT.MouseMove:
            point, coordinates = self.press
            moved = (event.pos() - point).manhattanLength() >= QtWidgets.QApplication.startDragDistance()
            if moved or event.modifiers() != _NONE:
                self._send_press(coordinates)
                return False
            return True
        if kind == _EVENT.MouseButtonRelease and event.button() == _LEFT:
            if self.ignore_release:
                self.ignore_release = False
                return True
            if self.press is not None:
                _, coordinates = self.press
                self.press = None
                if event.modifiers() != _NONE:
                    self._send_press(coordinates)
                    return False
                position = self.viewport._event_x_y_mod(event)
                self.viewport.pymol.button(_SINGLE_LEFT, 0, *position)
                self.viewport.pymol.button(_SINGLE_LEFT, 1, *position)
                return True
        if kind not in (_EVENT.MouseButtonPress, _EVENT.MouseButtonDblClick):
            return False
        if event.button() != _LEFT or event.modifiers() != _NONE or event.buttons() != _LEFT:
            if self.press is not None:
                self._send_press(self.press[1])
            return False
        width = wizard.cmd.get_setting_int("internal_gui_width") if wizard.cmd.get_setting_int("internal_gui") else 0
        if width and event.pos().x() >= self.viewport.width() - width:
            return False
        if kind == _EVENT.MouseButtonDblClick:
            self.press = None
            self.ignore_release = True
            return True
        self.ignore_release = False
        self.press = (event.pos(), self.viewport._event_x_y_mod(event))
        return True

    def _send_press(self, coordinates):
        self.press = None
        self.viewport.pymol.button(self.viewport._buttonMap[_LEFT], 0, *coordinates)
