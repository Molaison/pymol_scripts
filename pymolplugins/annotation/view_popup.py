"""Native dropdown for the candidate review view controls."""

from pymol.Qt import QtWidgets


def review_color_button(panel):
    button = QtWidgets.QToolButton(panel.quick_views)
    button.setText("Color…")
    button.setObjectName("review_view_color")
    button.setProperty("role", "view")
    button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    button.setToolTip("Choose colors and residue metrics for the current candidate.")
    button.clicked.connect(lambda: _open_review_colors(panel))
    return button


def _open_review_colors(panel):
    from pymol import cmd
    from .plugin import STATE
    from pymolplugins.host.show_presets import show_presets

    objects = cmd.get_object_list("%" + STATE.current_object) if STATE.current_object else []
    if not objects:
        return None
    panel.review_view_dialog.hide()
    return show_presets(tab="color", source=objects[0])


class ReviewViewPopup(QtWidgets.QMenu):
    """A menu-shaped container for quick views and their layer controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reviewViewPopup")
        self.setAccessibleName("Review view")
        self.setToolTipsVisible(True)

        content = self.content = QtWidgets.QWidget(self)
        content.setObjectName("reviewViewPopupContent")
        content.setMinimumWidth(480)
        self.content_layout = QtWidgets.QVBoxLayout(content)
        self.content_layout.setContentsMargins(6, 4, 6, 4)
        self.content_layout.setSpacing(4)

        action = self.widget_action = QtWidgets.QWidgetAction(self)
        action.setDefaultWidget(content)
        self.addAction(action)
