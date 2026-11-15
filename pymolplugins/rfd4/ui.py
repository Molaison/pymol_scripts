"""Small native Qt components; never change the application's stylesheet."""
from pymol.Qt import QtCore, QtGui, QtWidgets


class CompactLabel(QtWidgets.QLabel):
    """Keep the complete text for scripts/tooltips without widening the panel."""
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setTextFormat(QtCore.Qt.PlainText)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        super().setText(str(text))
        self.setToolTip(str(text))
        self.setPalette(QtGui.QPalette())

    def set_error(self):
        palette = self.palette()
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#b42318"))
        self.setPalette(palette)

    def sizeHint(self):
        return QtCore.QSize(min(super().sizeHint().width(), 240), self.fontMetrics().height() + 4)

    def minimumSizeHint(self):
        return QtCore.QSize(0, self.sizeHint().height())

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        group = QtGui.QPalette.Active if self.isEnabled() else QtGui.QPalette.Disabled
        painter.setPen(self.palette().color(group, self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text().replace("\n", " · "),
                                            QtCore.Qt.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)


def button(text, callback, layout):
    widget = QtWidgets.QPushButton(text)
    widget.setAutoDefault(False)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def primary(widget):
    font = widget.font()
    font.setBold(True)
    widget.setFont(font)
    widget.setMinimumHeight(32)
    return widget


def icon_button(widget, icon, tooltip):
    widget.setText("")
    widget.setIcon(widget.style().standardIcon(icon))
    widget.setToolTip(tooltip)
    widget.setAccessibleName(tooltip)
    widget.setFixedWidth(30)
    return widget


def menu_button(text, layout):
    widget = QtWidgets.QPushButton(text)
    widget.setAutoDefault(False)
    widget.setMenu(QtWidgets.QMenu(widget))
    layout.addWidget(widget)
    return widget


class Disclosure(QtWidgets.QToolButton):
    def __init__(self, title, widget):
        super().__init__()
        self.content = widget
        self.setCheckable(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setText(title)
        self.setArrowType(QtCore.Qt.RightArrow)
        widget.hide()
        self.toggled.connect(self._toggle)

    def _toggle(self, checked):
        self.content.setVisible(checked)
        self.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)


def collapsible(title, widget, layout):
    toggle = Disclosure(title, widget)
    layout.addWidget(toggle)
    layout.addWidget(widget)
    return toggle
