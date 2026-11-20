"""Shared visual hierarchy for annotation and batch workspaces."""

from pymol.Qt import QtCore, QtGui, QtWidgets


SCROLLBAR_STYLE = """
QScrollBar { background: transparent; margin: 0; border: 0; }
QScrollBar:vertical, QTableView QScrollBar:vertical, QTreeWidget QScrollBar:vertical,
QScrollArea QScrollBar:vertical { width: 6px; }
QScrollBar:horizontal, QTableView QScrollBar:horizontal, QTreeWidget QScrollBar:horizontal,
QScrollArea QScrollBar:horizontal { height: 6px; }
QScrollBar::handle { background: #9ba5af; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 24px; }
QScrollBar::handle:horizontal { min-width: 24px; }
QScrollBar::handle:hover, QScrollBar::handle:pressed { background: #637280; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""

STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #292d32; }
QDialog, QWidget#annotationWorkspace, QWidget#batchWorkspace, QWidget#rfd4Workspace { background: white; }
QLabel { background: transparent; }
QLabel[role='muted'] { color: #687078; font-size: 9pt; }
QLabel#datasetTitle { font-size: 12pt; font-weight: 600; }
QLabel#currentTitle { font-size: 15pt; font-weight: 600; }
QLabel#position { color: #687078; font-size: 9pt; }
QLabel#selectionCount { color: #2854ab; font-size: 10pt; }
QFrame#candidateCard { background: #f6f7f8; border: 0; }
QFrame#reviewCard, QFrame#sourceCard { background: white; border: 0; }
QPushButton, QToolButton {
    background: #fff; border: 1px solid #d8dce0; border-radius: 4px;
    padding: 6px 11px; min-height: 20px;
}
QPushButton[role='compact'], QToolButton[role='compact'] { padding: 4px 8px; }
QPushButton:hover, QToolButton:hover { background: #f0f4fc; border-color: #aabddd; }
QPushButton:focus, QToolButton:focus { border-color: #356ae6; }
QPushButton[role='quiet']:focus, QToolButton[role='quiet']:focus,
QToolButton[role='view']:focus { border-color: #356ae6; }
QPushButton[role='primary']:focus { border-color: #203a78; }
QPushButton:disabled, QToolButton:disabled { color: #9ba1a8; background: #f5f6f7; border-color: #e5e7e9; }
QPushButton[role='primary'] { background: #356ae6; border-color: #356ae6; color: white; font-weight: 600; padding: 9px 20px; }
QPushButton[role='primary']:hover { background: #2858cb; }
QPushButton[role='primary']:disabled { background: #dce5f7; border-color: #dce5f7; color: #849bc6; }
QPushButton[role='choice'] { padding: 9px 12px; border: 2px solid #d8dce0; min-width: 42px; font-weight: 500; }
QPushButton[role='choice']:checked { background: #eaf0ff; border-color: #356ae6; color: #2854ab; }
QWidget#annotationForm QPushButton[role='choice'] { padding: 5px 9px; }
QWidget#annotationForm QPushButton[role='primary'] { padding: 5px 14px; }
QPushButton[role='quiet'], QToolButton[role='quiet'] { background: transparent; border-color: transparent; color: #626a73; }
QPushButton[role='quiet']:hover, QToolButton[role='quiet']:hover { background: #eaf0fa; color: #274a8b; }
QToolButton[role='view'] { background: transparent; border-color: transparent; color: #4d5968; }
QToolButton[role='view']:hover { background: #edf2fa; }
QToolButton[role='view']:checked { background: #eaf0ff; border-color: #9db4e5; color: #2854ab; }
QToolButton[role='view']:disabled { color: #9ba1a8; }
QToolButton[role='icon'] {
    background: transparent; border: 0; padding: 0;
    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
}
QToolButton[role='icon']:hover { background: #eef1f4; }
QToolButton[role='icon']::menu-indicator { image: none; width: 0; }
QToolButton::menu-button { background: transparent; border: 0; }
QPushButton[role='segment'] { padding: 8px 16px; }
QPushButton[role='segment']:checked { background: #eaf0ff; border-color: #9db4e5; color: #2854ab; }
QLineEdit, QPlainTextEdit {
    background: white; border: 1px solid #d8dce0; border-radius: 4px;
    padding: 6px 8px; selection-background-color: #dbe6fc; selection-color: #203456;
}
QLineEdit:focus, QPlainTextEdit:focus { border-color: #6c91e8; }
QLineEdit:disabled { background: #f5f7fa; color: #9ba6b6; }
QLabel[role='error'], QPlainTextEdit[role='error'] {
    color: #982e36; background: #fff0f1; border: 0; padding: 10px; border-radius: 6px;
}
QComboBox, QSpinBox { min-height: 28px; }
QTableWidget QComboBox, QTableWidget QSpinBox { min-height: 24px; }
QTableWidget QLineEdit { padding: 2px 5px; }
QPlainTextEdit#commandEditor { font-family: Consolas; font-size: 10pt; }
QTableView { background: transparent; alternate-background-color: #f7f8f9; border: 0; gridline-color: transparent; }
QTableView::item { padding: 6px; border-bottom: 1px solid #eaedef; }
QTableView::item:selected { background: #eaf0ff; color: #23488d; }
QTableView::item:hover { background: #f3f6fc; }
QTableView#candidateScores::item { padding: 2px 6px; }
QTableWidget::item { padding: 2px 5px; }
QHeaderView::section { background: #f6f7f8; color: #687078; border: 0; border-bottom: 1px solid #e3e6e8; padding: 7px; font-weight: 400; }
QHeaderView#candidateScoreHeader::section { padding: 3px 7px; }
QHeaderView#sourceRowGutter::section {
    padding: 0 4px; border: 0; background: #f1f3f5; color: #626b76; font-size: 9pt;
}
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
QSplitter#reviewSplitter::handle { background: transparent; }
QSplitter#reviewSplitter::handle:hover { background: #dce3ea; }
QTableView::corner { background: transparent; border: 0; }
QTableView#candidateScores QTableCornerButton::section { background: #f6f7f8; border: 0; }
QTreeWidget#conditionPreview { background: white; border: 0; }
QTreeWidget#conditionPreview::item { min-height: 22px; }
QTreeWidget#conditionPreview::item:selected { background: #eaf0ff; color: #23488d; }
QProgressBar { border: 0; background: #e8edf7; max-height: 3px; }
QProgressBar::chunk { background: #6488da; }
QGroupBox { border: 0; margin-top: 9px; padding-top: 14px; font-weight: 500; }
QMenu { background: white; border: 1px solid #dce3ee; padding: 5px; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background: #eaf0ff; color: #23488d; }
QCheckBox { spacing: 8px; }
QTabWidget::pane { border: 0; border-top: 1px solid #e3e6e8; }
QTabBar::tab { background: transparent; border: 0; border-bottom: 2px solid transparent; padding: 8px 14px; color: #687078; }
QTabBar::tab:selected { border-bottom-color: #356ae6; color: #292d32; }
"""


class StatusLabel(QtWidgets.QLabel):
    """Show operation feedback only while it contains a message."""

    def setText(self, text):
        super().setText(str(text))
        self.setVisible(bool(text))


class ElidedLabel(QtWidgets.QLabel):
    """Keep long file and field names within their layout track."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(QtCore.Qt.PlainText)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), QtCore.Qt.ElideMiddle, self.contentsRect().width())
        painter.setPen(self.palette().color(QtGui.QPalette.WindowText))
        painter.drawText(self.contentsRect(), self.alignment(), text)


def apply_style(panel, name="annotationWorkspace"):
    panel.setObjectName(name)
    panel.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    panel.setStyleSheet(STYLE + SCROLLBAR_STYLE)


def card(name):
    widget = QtWidgets.QFrame()
    widget.setObjectName(name)
    return widget


def thin_scrollbars(area):
    for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
        bar.setStyleSheet(SCROLLBAR_STYLE)
