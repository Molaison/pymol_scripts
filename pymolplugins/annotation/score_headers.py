"""Draw metadata distributions in the native Scores table header."""

import html
from threading import Event
import traceback

from pymol.Qt import QtCore, QtGui, QtWidgets

from .column_profiles import build_column_profiles, format_proportion
from .filtering import column_expression


class ScoreProfileHeader(QtWidgets.QHeaderView):
    filterRequested = QtCore.Signal(str)

    def __init__(self, table, model, get_executor):
        super().__init__(QtCore.Qt.Horizontal, table)
        self.profiles = {}
        self.can_filter = False
        self._expanded = False
        self._future = None
        self._cancel = Event()
        self._get_executor = get_executor
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._collect)
        self._brush = _HeaderFilterInteraction(self)
        model.modelReset.connect(self._refresh)

    def _refresh(self):
        self._timer.stop()
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()
        self._future = None
        self.profiles = {}
        self._brush.reset()
        rows, columns, kinds, preview = self.model().profile_input()
        self.can_filter = not preview
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

    def summary_rect(self, section):
        return section.adjusted(7, super().sizeHint().height() + 3, -7, -4)

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
            self._paint_profile(painter, self.summary_rect(rect), profile, index)
        painter.restore()

    def _paint_profile(self, painter, rect, profile, index):
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
            self._paint_histogram(painter, rect, line, profile, self._brush.selected_bins(index))
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

    def _paint_histogram(self, painter, rect, line, profile, selection):
        width = max(0, rect.width())
        height = 3 * line - 4
        baseline = rect.y() + height
        peak = max(profile.bins)
        minimum, maximum = profile.minimum, profile.maximum
        if selection:
            first, last = selection
            left = first * width // len(profile.bins)
            right = (last + 1) * width // len(profile.bins)
            painter.fillRect(rect.x() + left, rect.y(), right - left, height, QtGui.QColor("#dbe6fc"))
            selected = profile.selected_range(first, last)
            if selected:
                minimum, maximum = selected
        for index, count in enumerate(profile.bins):
            if count:
                left = index * width // len(profile.bins)
                right = (index + 1) * width // len(profile.bins)
                bar = max(1, count * height // peak)
                painter.fillRect(rect.x() + left, baseline - bar, max(1, right - left - 1), bar,
                                 QtGui.QColor("#356ae6"))
        scale = QtCore.QRect(rect.x(), baseline + 2, width, line)
        self._paint_pair(painter, scale, _edge_label(minimum), _edge_label(maximum) if minimum != maximum else "")

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
                    if self.can_filter:
                        lines.append("Drag across the histogram to add a range filter")
                elif profile.categories and self.can_filter:
                    lines.append("Click a category to add a filter")
                lines.extend(f"{value}: {count:,} ({format_proportion(count, profile.total)})"
                             for value, count in profile.categories)
                if profile.other:
                    lines.append(f"Other values: {profile.other:,} ({format_proportion(profile.other, profile.total)})")
                QtWidgets.QToolTip.showText(event.globalPos(), "<br>".join(map(html.escape, lines)), self)
                return True
        return super().viewportEvent(event)


class _HeaderFilterInteraction(QtCore.QObject):
    """Translate presses on the painted summary into filter expressions."""

    def __init__(self, header):
        super().__init__(header)
        self.header = header
        self.viewport = header.viewport()
        self.viewport.setMouseTracking(True)
        self.viewport.installEventFilter(self)
        self.reset()

    def reset(self):
        self.pressed = None
        self.current_bin = None
        self.viewport.unsetCursor()

    def selected_bins(self, index):
        if self.pressed and self.pressed[:2] == (index, "bins"):
            first = self.pressed[2]
            return min(first, self.current_bin), max(first, self.current_bin)
        return None

    def _summary(self, index):
        section = QtCore.QRect(self.header.sectionViewportPosition(index), 0,
                               self.header.sectionSize(index), self.viewport.height())
        return self.header.summary_rect(section)

    def _bin_at(self, index, x):
        bounds = self._summary(index)
        count = len(self.header.profiles[index].bins)
        return max(0, min(count - 1, (x - bounds.left()) * count // max(1, bounds.width())))

    def _hit(self, point):
        if not self.header.can_filter:
            return None
        index = self.header.logicalIndexAt(point)
        profile = self.header.profiles.get(index)
        if profile is None:
            return None
        rect = self._summary(index)
        if not rect.contains(point):
            return None
        line = QtGui.QFontMetrics(self.header._summary_font()).height() + 1
        y = point.y() - rect.top()
        if y < line and profile.missing:
            return index, "missing", None
        if profile.bins and 2 * line <= y < 5 * line - 4:
            return index, "bins", self._bin_at(index, point.x())
        category = y // line - 2
        if 0 <= category < len(profile.categories):
            return index, "category", category
        if category == len(profile.categories) and profile.other:
            return index, "other", None
        return None

    def _expression(self, index, kind, value):
        name = self.header.model().headerData(index, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole)
        field = column_expression(name)
        profile = self.header.profiles[index]
        if kind == "missing":
            return f"{field} is None"
        if kind == "category":
            return f"{field} == {profile.categories[value][0]!r}"
        if kind == "other":
            return " and ".join([f"{field} is not None"] +
                                [f"{field} != {label!r}" for label, _count in profile.categories])
        bounds = profile.selected_range(*self.selected_bins(index))
        return f"{bounds[0]!r} <= {field} <= {bounds[1]!r}" if bounds else ""

    def eventFilter(self, _widget, event):
        event_type = event.type()
        if event_type == QtCore.QEvent.MouseMove:
            if self.pressed and self.pressed[1] == "bins":
                self.current_bin = self._bin_at(self.pressed[0], event.pos().x())
                self.viewport.update()
                return True
            target = self._hit(event.pos())
            if target:
                cursor = QtCore.Qt.CrossCursor if target[1] == "bins" else QtCore.Qt.PointingHandCursor
                self.viewport.setCursor(cursor)
                return True
            self.viewport.unsetCursor()
        if event_type == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            self.pressed = self._hit(event.pos())
            if self.pressed:
                self.current_bin = self.pressed[2]
                self.viewport.update()
                return True
        if event_type == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton and self.pressed:
            target = self._hit(event.pos())
            if self.pressed[1] == "bins":
                self.current_bin = self._bin_at(self.pressed[0], event.pos().x())
                target = self.pressed
            expression = self._expression(*target) if target == self.pressed else ""
            self.reset()
            self.viewport.update()
            if expression:
                self.header.filterRequested.emit(expression)
            return True
        return False


def _edge_label(value):
    try:
        return f"{value:.4g}"
    except OverflowError:
        return str(value)
