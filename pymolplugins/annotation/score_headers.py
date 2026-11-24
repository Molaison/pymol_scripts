"""Draw metadata distributions in the native Scores table header."""

import html
from threading import Event
import traceback

from pymol.Qt import QtCore, QtGui, QtWidgets

from .category_picker import OtherCategoryPicker
from .column_profiles import build_column_profiles, format_proportion, other_category_counts
from .header_filters import HeaderChoice
from .model import COLUMN_INTEGER, COLUMN_NUMBER, format_numeric_cell


class ScoreProfileHeader(QtWidgets.QHeaderView):
    filterRequested = QtCore.Signal(str, object)
    choicesRequested = QtCore.Signal(str, object)
    sortRequested = QtCore.Signal(str)

    def __init__(self, table, model, get_executor):
        super().__init__(QtCore.Qt.Horizontal, table)
        self._table = table
        self.profiles = {}
        self.selections = {}
        self._other_popup = None
        self.can_filter = False
        self._show_profiles = False
        self.has_profiles = False
        self._expanded = False
        self._title_click = False
        self._profile_lines = 3
        self._future = None
        self._cancel = Event()
        self._get_executor = get_executor
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._collect)
        self._brush = _HeaderFilterInteraction(self)
        self.setSectionsClickable(True)
        self.sectionClicked.connect(self._sort_section)
        model.modelReset.connect(self._refresh)
        model.layoutChanged.connect(self._refresh_sort_indicator)

    def mouseReleaseEvent(self, event):
        self._title_click = (event.button() == QtCore.Qt.LeftButton
                             and (not self._expanded or event.pos().y() < super().sizeHint().height()))
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._title_click = False

    def _sort_section(self, index):
        column = self.model().sortable_column(index)
        if self._title_click and column:
            self.sortRequested.emit(column)
        self._refresh_sort_indicator()

    def _refresh_sort_indicator(self):
        index, descending = self.model().sort_indicator()
        order = QtCore.Qt.DescendingOrder if descending else QtCore.Qt.AscendingOrder
        self.setSortIndicator(index, order)
        self.setSortIndicatorShown(index >= 0)

    def set_profiles_visible(self, visible):
        self._show_profiles = bool(visible)
        self._refresh()

    def _refresh(self):
        if self._other_popup is not None:
            self._other_popup.close()
        self._timer.stop()
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()
        self._future = None
        self.profiles = {}
        self._brush.reset()
        rows, columns, kinds, preview, filters, source = self.model().profile_input()
        self.can_filter = not preview
        self.has_profiles = bool(columns and (rows or filters.selections))
        self._expanded = self._show_profiles and self.has_profiles
        self._first_profile = self.model().columnCount() - len(columns)
        self.selections = {index: filters.selections[column]
                           for index, column in enumerate(columns, self._first_profile)
                           if column in filters.selections}
        self._refresh_sort_indicator()
        if self._expanded:
            self._cancel = Event()
            self._future = self._get_executor().submit(
                build_column_profiles, rows, columns, kinds, preview=preview, cancelled=self._cancel,
                filters=filters, source=source,
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
        lines = max((self._profile_height_in_lines(index, profile)
                     for index, profile in self.profiles.items()), default=0)
        if lines != self._profile_lines:
            self._profile_lines = lines
            self.updateGeometry()
            self.geometriesChanged.emit()
        self.viewport().update()

    def is_selected(self, index, kind, label=None):
        return any(choice.key == (kind, label) for choice in self.selections.get(index, ()))

    def shows_missing(self, index, profile):
        return bool(profile.missing or self.is_selected(index, "missing"))

    def _profile_height_in_lines(self, index, profile):
        body = 3 if profile.bins else len(profile.categories) + self.has_other(index, profile)
        return body + self.shows_missing(index, profile)

    def _other_state(self, index, profile):
        visible = {label for label, _ in profile.categories}
        for choice in self.selections.get(index, ()):
            if choice.kind == "other":
                return QtCore.Qt.Checked if set(choice.value) == visible else QtCore.Qt.PartiallyChecked
            if choice.kind == "category" and choice.value not in visible:
                return QtCore.Qt.PartiallyChecked
        return QtCore.Qt.Unchecked

    def has_other(self, index, profile):
        return bool(profile.other or self._other_state(index, profile) != QtCore.Qt.Unchecked)

    def open_other(self, index, point):
        profile = self.profiles[index]
        name = self.model().headerData(index, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole)
        displayed = tuple(label for label, _ in profile.categories)
        choices = self.selections.get(index, ())
        explicit = {choice.value for choice in choices if choice.kind == "category"}
        remainder = next((choice.value for choice in choices if choice.kind == "other"), None)
        rows, _columns, _kinds, _preview, filters, source = self.model().profile_input()
        future = self._get_executor().submit(other_category_counts, rows, name, set(displayed), filters, source)
        popup = OtherCategoryPicker(self, future, explicit, remainder, profile.total)
        self._other_popup = popup
        kept = tuple(choice for choice in choices if choice.kind in ("missing", "range")
                     or (choice.kind == "category" and choice.value in displayed))
        popup.applied.connect(lambda values, all_checked: self.choicesRequested.emit(
            name, kept + ((HeaderChoice("other", displayed),) if all_checked
                          else tuple(HeaderChoice("category", value) for value in values))
        ))
        popup.aboutToHide.connect(self._close_other)
        popup.popup(self.viewport().mapToGlobal(point + QtCore.QPoint(0, 6)))

    def _close_other(self):
        popup, self._other_popup = self._other_popup, None
        popup.deleteLater()

    def _summary_font(self):
        font = QtGui.QFont(self.font())
        font.setPointSize(max(8, font.pointSize() - 1))
        return font

    def sizeHint(self):
        hint = super().sizeHint()
        if self._expanded:
            line = QtGui.QFontMetrics(self._summary_font()).height() + 1
            hint.setHeight(hint.height() + self._profile_lines * line + 8)
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
        if self.shows_missing(index, profile):
            missing = (f"Missing {profile.missing:,} ({format_proportion(profile.missing, profile.total)})"
                       if profile.missing else "Missing filter active")
            missing_row = QtCore.QRect(rect.x(), rect.y(), rect.width(), line)
            state = QtCore.Qt.Checked if self.is_selected(index, "missing") else QtCore.Qt.Unchecked
            self._paint_checkbox(painter, missing_row, missing, "", state)
            rect = rect.adjusted(0, line, 0, 0)
        if profile.bins:
            limits = next((choice.value for choice in self.selections.get(index, ()) if choice.kind == "range"), None)
            active = profile.bins_overlapping(*limits) if limits else None
            self._paint_histogram(painter, rect, line, profile, self._brush.selected_bins(index), active)
        else:
            for offset, (name, count) in enumerate(profile.categories):
                row = QtCore.QRect(rect.x(), rect.y() + line * offset, rect.width(), line)
                state = QtCore.Qt.Checked if self.is_selected(index, "category", name) else QtCore.Qt.Unchecked
                self._paint_checkbox(painter, row, name, format_proportion(count, profile.total), state)
            if self.has_other(index, profile):
                row = QtCore.QRect(rect.x(), rect.y() + line * len(profile.categories), rect.width(), line)
                self._paint_checkbox(painter, row, "Other…", format_proportion(profile.other, profile.total),
                                     self._other_state(index, profile))

    def _paint_checkbox(self, painter, row, title, value, state):
        style = self.style()
        option = QtWidgets.QStyleOptionButton()
        width = style.pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth, option, self)
        height = style.pixelMetric(QtWidgets.QStyle.PM_IndicatorHeight, option, self)
        option.rect = QtCore.QRect(row.x(), row.y() + (row.height() - height) // 2, width, height)
        option.state = {
            QtCore.Qt.Checked: QtWidgets.QStyle.State_On,
            QtCore.Qt.PartiallyChecked: QtWidgets.QStyle.State_NoChange,
            QtCore.Qt.Unchecked: QtWidgets.QStyle.State_Off,
        }[state]
        option.state |= QtWidgets.QStyle.State_Enabled
        if state != QtCore.Qt.Unchecked:
            painter.fillRect(row, QtGui.QColor("#dbe6fc"))
        style.drawPrimitive(QtWidgets.QStyle.PE_IndicatorCheckBox, option, painter, self)
        self._paint_pair(painter, row.adjusted(width + 4, 0, 0, 0), title, value)

    @staticmethod
    def _paint_pair(painter, row, left, right):
        metrics = painter.fontMetrics()
        align = QtCore.Qt.AlignVCenter
        right = metrics.elidedText(right, QtCore.Qt.ElideRight, max(0, row.width() // 2))
        available = max(0, row.width() - metrics.horizontalAdvance(right) - 4)
        painter.drawText(row, QtCore.Qt.AlignRight | align, right)
        painter.drawText(row, QtCore.Qt.AlignLeft | align,
                         metrics.elidedText(left, QtCore.Qt.ElideRight, available))

    def _paint_histogram(self, painter, rect, line, profile, selection, active):
        width = max(0, rect.width())
        height = _histogram_height(line)
        baseline = rect.y() + height
        peak = max(profile.bins)
        minimum, maximum = profile.minimum, profile.maximum
        if selection or active:
            first, last = selection or active
            left = first * width // len(profile.bins)
            right = (last + 1) * width // len(profile.bins)
            painter.fillRect(rect.x() + left, rect.y(), right - left, height, QtGui.QColor("#dbe6fc"))
            if selection:
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
        if event.type() == QtCore.QEvent.Wheel and self._table.scroll_shift_wheel(event):
            return True
        if event.type() == QtCore.QEvent.ToolTip:
            index = self.logicalIndexAt(event.pos())
            profile = self.profiles.get(index)
            if profile is not None:
                name = self.model().headerData(index, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole)
                scope = "rows matching the other filters" if index in self.selections else "displayed rows"
                lines = [str(name), f"{profile.total:,} {scope} · {profile.distinct:,} distinct"]
                if self.model().sortable_column(index):
                    lines.append("Click the column title to switch sort direction")
                if profile.missing:
                    lines.append(f"Missing: {profile.missing:,} ({format_proportion(profile.missing, profile.total)})")
                if self.is_selected(index, "missing"):
                    lines.append("Missing filter active")
                if profile.bins:
                    lines.append(f"Range: {profile.minimum} to {profile.maximum}")
                    limits = next((choice.value for choice in self.selections.get(index, ()) if choice.kind == "range"), None)
                    if limits:
                        lines.append(f"Selected range: {limits[0]} to {limits[1]}")
                    if self.can_filter:
                        lines.append("Drag across the histogram to set the range for this column")
                        lines.append("Double-click the histogram to clear this column's header filter")
                elif profile.categories and self.can_filter:
                    lines.append("Click categories to select or clear them")
                    if self.has_other(index, profile):
                        lines.append("Open Other to choose individual categories")
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
        self._click_timer = QtCore.QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._click_timer.timeout.connect(self._apply_click)
        self.reset()

    def reset(self):
        self._click_timer.stop()
        self._pending_click = None
        self._reset_press()

    def _reset_press(self):
        self.pressed = None
        self.current_bin = None
        self.viewport.unsetCursor()

    def _apply_click(self):
        if self._pending_click is not None:
            click, self._pending_click = self._pending_click, None
            self.header.filterRequested.emit(*click)

    def _column_name(self, index):
        return self.header.model().headerData(index, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole)

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
        if self.header.shows_missing(index, profile):
            if y < line:
                return index, "missing", None
            y -= line
        if profile.bins:
            return (index, "bins", self._bin_at(index, point.x())) if y < _histogram_height(line) else None
        category = y // line
        if 0 <= category < len(profile.categories):
            return index, "category", category
        if category == len(profile.categories) and self.header.has_other(index, profile):
            return index, "other", None
        return None

    def _choice(self, index, kind, value):
        profile = self.header.profiles[index]
        if kind == "missing":
            return HeaderChoice("missing")
        if kind == "category":
            return HeaderChoice("category", profile.categories[value][0])
        bounds = profile.selected_range(*self.selected_bins(index))
        return HeaderChoice("range", bounds) if bounds else None

    def eventFilter(self, _widget, event):
        event_type = event.type()
        if event_type == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
            point = event.pos()
            index = self.header.logicalIndexAt(point)
            active_range = any(choice.kind == "range" for choice in self.header.selections.get(index, ()))
            target = self._hit(point)
            histogram = target and target[1] == "bins"
            if (self.header.can_filter and self.header._expanded
                    and (histogram or (active_range and self._summary(index).contains(point)))):
                self.reset()
                self.viewport.update()
                if index in self.header.selections:
                    self.header.choicesRequested.emit(self._column_name(index), ())
                return True
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
            single_bin = False
            if self.pressed[1] == "bins":
                self.current_bin = self._bin_at(self.pressed[0], event.pos().x())
                single_bin = self.current_bin == self.pressed[2]
                target = self.pressed
            same_target = target == self.pressed
            choice = self._choice(*target) if same_target and target[1] != "other" else None
            self._reset_press()
            self.viewport.update()
            if same_target and target[1] == "other":
                self.header.open_other(target[0], event.pos())
            elif choice is not None:
                name = self._column_name(target[0])
                if single_bin:
                    self._pending_click = name, choice
                    self._click_timer.start(QtWidgets.QApplication.doubleClickInterval())
                else:
                    self._click_timer.stop()
                    self._pending_click = None
                    self.header.filterRequested.emit(name, choice)
            return True
        return False


def _histogram_height(line):
    return 2 * line - 4


def _edge_label(value):
    return format_numeric_cell(value, COLUMN_INTEGER if isinstance(value, int) else COLUMN_NUMBER)
