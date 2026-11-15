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
        _DOCK.setWidget(WorkflowPanel(_DOCK))
        _DOCK.setMinimumWidth(420)
        if parent is not None and hasattr(parent, "addDockWidget"):
            parent.addDockWidget(QtCore.Qt.RightDockWidgetArea, _DOCK)
            contents = getattr(parent, "contentsPanelDockWidget", None)
            if contents is not None and contents.isVisible():
                # PyMOL 3's external object panel shares this dock area.
                # Tabs preserve both panels without compressing the workflow.
                parent.tabifyDockWidget(contents, _DOCK)
        else:
            _DOCK.setFloating(True)
            _DOCK.resize(550, 780)
    _DOCK.show()
    _DOCK.raise_()
    return _DOCK
