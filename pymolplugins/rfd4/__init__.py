"""RFD4-Proteina workflow, opened lazily by the PymolPlugins menu."""
_DOCK = None


def open_panel():
    global _DOCK
    from pymol.Qt import QtCore, QtWidgets
    from .panel import WorkflowPanel
    if _DOCK is None:
        try:
            from pymol.gui import get_qtwindow
            parent = get_qtwindow()
        except (ImportError, AttributeError):
            parent = None
        _DOCK = QtWidgets.QDockWidget("RFD4 · Proteina", parent)
        _DOCK.setObjectName("RFD4ProteinaDock")
        panel = WorkflowPanel(_DOCK)
        _DOCK.setWidget(panel)
        _DOCK.visibilityChanged.connect(panel.workspace_visibility_changed)
        _DOCK.setMinimumWidth(420)
        if parent is not None and hasattr(parent, "addDockWidget"):
            parent.addDockWidget(QtCore.Qt.LeftDockWidgetArea, _DOCK)
        else:
            _DOCK.setFloating(True)
            _DOCK.resize(550, 780)
    _DOCK.show()
    _DOCK.raise_()
    return _DOCK
