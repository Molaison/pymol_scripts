"""Route arrow keys according to which part of the PyMOL window was clicked."""
import sys


def install_qt_navigation_keys():
    """Install one inert-unless-bound dispatcher on the existing PyMOL window."""
    host_module = sys.modules.get("pmg_qt.pymol_qt_gui")
    window = getattr(host_module, "window", None)
    if window is None or getattr(window, "_pp_navigation_keys", None) is not None:
        return
    from pymol.Qt import QtCore

    lineedit = getattr(window, "lineedit", None)
    viewport = getattr(window, "pymolwidget", None)
    keys = getattr(QtCore.Qt, "Key", QtCore.Qt)
    modifiers = getattr(QtCore.Qt, "KeyboardModifier", QtCore.Qt)
    events = getattr(QtCore.QEvent, "Type", QtCore.QEvent)

    class NavigationKeys(QtCore.QObject):
        scene_clicked = False

        def console_edited(self, _text):
            self.scene_clicked = False

        def eventFilter(self, watched, event):
            if event.type() == events.MouseButtonPress:
                self.scene_clicked = watched is viewport
            elif event.type() == events.FocusIn and watched is lineedit:
                self.scene_clicked = False
            if event.type() != events.KeyPress:
                return False
            if watched is lineedit and lineedit.text() and not self.scene_clicked:
                return False
            if event.key() not in (keys.Key_Left, keys.Key_Right):
                return False
            modifier = event.modifiers()
            if modifier not in (modifiers.NoModifier, modifiers.ControlModifier):
                return False
            name = "left" if event.key() == keys.Key_Left else "right"
            if modifier == modifiers.ControlModifier:
                name = "CTRL-" + name
            if self.step_navigation(name):
                event.accept()
                return True
            if watched is not lineedit or not self.scene_clicked:
                return False
            # PyMOL's viewport can give keyboard focus to the console. Route
            # scene-origin arrows through the usual native dispatch in that case.
            window.keyPressEvent(event)
            event.accept()
            return True

        @staticmethod
        def step_navigation(name):
            navigation = sys.modules.get("pymolplugins.navigation.commands")
            controller = getattr(navigation, "_CONTROLLER", None)
            if controller is None or not controller.keys_bound:
                return False
            if navigation.cmd.key_mappings.get(name) != controller.installed_key_mappings.get(name):
                return False
            callbacks = {
                "left": navigation.pp_nav_prev, "right": navigation.pp_nav_next,
                "CTRL-left": navigation.pp_nav_prev_batch,
                "CTRL-right": navigation.pp_nav_next_batch,
            }
            callbacks[name]()
            return True

    dispatcher = NavigationKeys(window)
    for widget in (lineedit, viewport):
        if widget is not None:
            widget.installEventFilter(dispatcher)
    if lineedit is not None:
        lineedit.textEdited.connect(dispatcher.console_edited)
    window._pp_navigation_keys = dispatcher

