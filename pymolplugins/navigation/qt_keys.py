"""Route navigation arrows from the Qt viewport and empty command line."""
import sys


def install_qt_navigation_keys(navigation):
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
        def eventFilter(self, watched, event):
            if event.type() != events.KeyPress:
                return False
            controller = navigation._CONTROLLER
            if controller is None or not controller.keys_bound:
                return False
            if watched is lineedit and lineedit.text():
                return False
            if event.key() not in (keys.Key_Left, keys.Key_Right):
                return False
            modifier = event.modifiers()
            if modifier not in (modifiers.NoModifier, modifiers.ControlModifier):
                return False
            name = "left" if event.key() == keys.Key_Left else "right"
            if modifier == modifiers.ControlModifier:
                name = "CTRL-" + name
            # An external owner may have replaced a mapping during this session.
            if navigation.cmd.key_mappings.get(name) != controller.installed_key_mappings.get(name):
                return False
            callbacks = {
                "left": navigation.pp_nav_prev, "right": navigation.pp_nav_next,
                "CTRL-left": navigation.pp_nav_prev_batch,
                "CTRL-right": navigation.pp_nav_next_batch,
            }
            callbacks[name]()
            event.accept()
            return True

    dispatcher = NavigationKeys(window)
    for widget in (lineedit, viewport):
        if widget is not None:
            widget.installEventFilter(dispatcher)
    window._pp_navigation_keys = dispatcher

