"""Single-click access to the primary tools in the PyMOL main window."""

from traceback import print_exc


def install_toolbar(entries, focus_callback=None):
    try:
        from pymol.gui import get_qtwindow
        from pymol.Qt import QtCore, QtWidgets
    except ImportError:
        return
    window = get_qtwindow()
    if not isinstance(window, QtWidgets.QMainWindow):
        return
    if window.findChild(QtWidgets.QToolBar, "pymolplugins-toolbar") is not None:
        return

    def launch(callback):
        try:
            callback()
        except Exception as error:
            print_exc()
            window.statusBar().showMessage(str(error), 8000)

    toolbar = QtWidgets.QToolBar("PymolPlugins", window)
    toolbar.setObjectName("pymolplugins-toolbar")
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
    for text, description, callback in entries:
        action = toolbar.addAction(text)
        action.setToolTip(description)
        if callback is focus_callback:
            action.setCheckable(True)
            action.setObjectName("pymolplugins-focus")
        action.triggered.connect(lambda _checked, run=callback: launch(run))
    area = QtCore.Qt.TopToolBarArea
    main_toolbar = getattr(window, "topToolBar", None)
    if main_toolbar is not None:
        window.addToolBarBreak(area)
        main_toolbar.visibilityChanged.connect(toolbar.setVisible)
    window.addToolBar(area, toolbar)


def set_focus_active(active):
    try:
        from pymol.gui import get_qtwindow
        from pymol.Qt import QtWidgets
    except ImportError:
        return
    window = get_qtwindow()
    if isinstance(window, QtWidgets.QMainWindow):
        toolbar = window.findChild(QtWidgets.QToolBar, "pymolplugins-toolbar")
        if toolbar:
            for action in toolbar.actions():
                if action.objectName() == "pymolplugins-focus":
                    action.setChecked(active)
                    return
