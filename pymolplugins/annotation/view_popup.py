"""Native dropdown for the candidate review view controls."""

from pymol.Qt import QtWidgets


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
