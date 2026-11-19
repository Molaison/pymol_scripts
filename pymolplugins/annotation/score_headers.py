"""Draw metadata distributions in the native Scores table header."""

import html
from threading import Event
import traceback

from pymol.Qt import QtCore, QtGui, QtWidgets

from .column_profiles import build_column_profiles, format_proportion


class ScoreProfileHeader(QtWidgets.QHeaderView):
    def __init__(self, table, model, get_executor):
        super().__init__(QtCore.Qt.Horizontal, table)
        self.profiles = {}
        self._expanded = False
        self._future = None
        self._cancel = Event()
        self._get_executor = get_executor
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._collect)
        model.modelReset.connect(self._refresh)

    def _refresh(self):
        self._timer.stop()
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()
        self._future = None
        self.profiles = {}
        rows, columns, kinds, preview = self.model().profile_input()
        self._expanded = bool(rows and columns)
        self._first_profile = self.model().columnCount() - len(columns)
        if self._expanded:
            self._cancel = Event()
            self._future = self._get_executor().submit(
                build_column_profiles, rows, columns, kinds, preview=preview, cancelled=self._cancel
            )
            self._timer.start()
        self.updateGeometry()
        self.geometriesChanged.emit()
        self.viewport().update()

    def _collect(self):
        if self._future is None or not self._future.done():
            return
        self._timer.stop()
        future, self._future = self._future, None
        try:
            self.profiles = dict(enumerate(future.result(), self._first_profile))
        except Exception:
            traceback.print_exc()
        self.viewport().update()

    def _summary_font(self):
        font = QtGui.QFont(self.font())
        font.setPointSize(max(8, font.pointSize() - 1))
        return font

    def sizeHint(self):
        hint = super().sizeHint()
        if self._expanded:
            line = QtGui.QFontMetrics(self._summary_font()).height() + 1
            hint.setHeight(hint.height() + 6 * line + 8)
        return hint

    def paintSection(self, painter, rect, index):
        if not self._expanded or not rect.isValid():
            super().paintSection(painter, rect, index)
            return
        title = QtCore.QRect(rect)
        title.setHeight(super().sizeHint().height())
        super().paintSection(painter, title, index)
        painter.save()
        detail = rect.adjusted(0, title.height(), 0, 0)
        painter.setClipRect(detail)
        painter.fillRect(detail, QtGui.QColor("#f6f7f8"))
        painter.setPen(QtGui.QColor("#e3e6e8"))
        painter.drawLine(detail.bottomLeft(), detail.bottomRight())
        painter.drawLine(detail.topRight(), detail.bottomRight())
        profile = self.profiles.get(index)
        if profile is not None:
            self._paint_profile(painter, detail.adjusted(7, 3, -7, -4), profile)
        painter.restore()

    def _paint_profile(self, painter, rect, profile):
        painter.setFont(self._summary_font())
        metrics = painter.fontMetrics()
        line = metrics.height() + 1
        painter.setPen(QtGui.QColor("#687078"))
        missing = f"Missing {profile.missing:,} ({format_proportion(profile.missing, profile.total)})"
        for offset, text in enumerate((missing, f"{profile.distinct:,} distinct")):
            bounds = QtCore.QRect(rect.x(), rect.y() + offset * line, rect.width(), line)
            painter.drawText(bounds, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             metrics.elidedText(text, QtCore.Qt.ElideRight, bounds.width()))
        rect = rect.adjusted(0, 2 * line, 0, 0)
        if profile.bins:
            self._paint_histogram(painter, rect, line, profile)
        else:
            categories = profile.categories + (("Other", profile.other),) if profile.other else profile.categories
            for offset, (name, count) in enumerate(categories):
                row = QtCore.QRect(rect.x(), rect.y() + line * offset, rect.width(), line)
                self._paint_pair(painter, row, name, format_proportion(count, profile.total))

    @staticmethod
    def _paint_pair(painter, row, left, right):
        metrics = painter.fontMetrics()
        align = QtCore.Qt.AlignVCenter
        right = metrics.elidedText(right, QtCore.Qt.ElideRight, max(0, row.width() // 2))
        available = max(0, row.width() - metrics.horizontalAdvance(right) - 4)
        painter.drawText(row, QtCore.Qt.AlignRight | align, right)
        painter.drawText(row, QtCore.Qt.AlignLeft | align,
                         metrics.elidedText(left, QtCore.Qt.ElideRight, available))

    def _paint_histogram(self, painter, rect, line, profile):
        width = max(0, rect.width())
        height = 3 * line - 4
        baseline = rect.y() + height
        peak = max(profile.bins)
        for index, count in enumerate(profile.bins):
            if count:
                left = index * width // len(profile.bins)
                right = (index + 1) * width // len(profile.bins)
                bar = max(1, count * height // peak)
                painter.fillRect(rect.x() + left, baseline - bar, max(1, right - left - 1), bar,
                                 QtGui.QColor("#356ae6"))
        scale = QtCore.QRect(rect.x(), baseline + 2, width, line)
        minimum = _edge_label(profile.minimum)
        maximum = _edge_label(profile.maximum) if profile.minimum != profile.maximum else ""
        self._paint_pair(painter, scale, minimum, maximum)

    def viewportEvent(self, event):
        if event.type() == QtCore.QEvent.ToolTip:
            index = self.logicalIndexAt(event.pos())
            profile = self.profiles.get(index)
            if profile is not None:
                name = self.model().headerData(index, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole)
                lines = [str(name), f"{profile.total:,} displayed rows · {profile.distinct:,} distinct",
                         f"Missing: {profile.missing:,} ({format_proportion(profile.missing, profile.total)})"]
                if profile.bins:
                    lines.append(f"Range: {profile.minimum} to {profile.maximum}")
                lines.extend(f"{value}: {count:,} ({format_proportion(count, profile.total)})"
                             for value, count in profile.categories)
                if profile.other:
                    lines.append(f"Other values: {profile.other:,} ({format_proportion(profile.other, profile.total)})")
                QtWidgets.QToolTip.showText(event.globalPos(), "<br>".join(map(html.escape, lines)), self)
                return True
        return super().viewportEvent(event)


def _edge_label(value):
    try:
        return f"{value:.4g}"
    except OverflowError:
        return str(value)
