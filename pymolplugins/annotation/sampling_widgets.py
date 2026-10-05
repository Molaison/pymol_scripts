"""Dataset overview and representative-sampling controls."""

from collections import Counter

from pymol.Qt import QtCore, QtGui, QtWidgets

from .model import COLUMN_INTEGER, COLUMN_NUMBER
from .sampling import _number, _sample_details, summarize_distribution


GROUP_COLORS = (
    ("#eaf0ff", "#2854ab"),
    ("#dcefdc", "#195724"),
    ("#fff0ce", "#71520b"),
    ("#fbe1de", "#8d2424"),
    ("#eceff2", "#46515c"),
)


def _qt_enum(group, name):
    value = getattr(QtCore.Qt, name, None)
    return value if value is not None else getattr(getattr(QtCore.Qt, group), name)


def _format_number(value):
    if value is None:
        return "—"
    if value == 0:
        return "0"
    magnitude = abs(value)
    if float(value).is_integer() and magnitude < 10_000_000:
        return f"{int(value):,}"
    return f"{value:.4g}"


def _column_name(column):
    return str(getattr(column, "name", column))


def _numeric_columns(columns, entries):
    result = []
    for column in columns:
        name = _column_name(column)
        kind = getattr(column, "kind", "")
        if kind in {COLUMN_INTEGER, COLUMN_NUMBER}:
            result.append(name)
            continue
        raw_values = [str(entry.metadata.get(name, "")).strip() for entry in entries]
        present = [value for value in raw_values if value]
        if present and all(_number(value) is not None for value in present):
            result.append(name)
    return result


def _read_only_item(value):
    item = QtWidgets.QTableWidgetItem(str(value))
    item.setFlags(item.flags() & ~_qt_enum("ItemFlag", "ItemIsEditable"))
    return item


def _table(headers, height):
    table = QtWidgets.QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.verticalHeader().hide()
    table.horizontalHeader().setStretchLastSection(True)
    table.setFixedHeight(height)
    return table


class DatasetOverviewDialog(QtWidgets.QDialog):
    """Review the full dataset and return an explicit sampling decision."""

    def __init__(
        self,
        all_entries,
        filtered_entries,
        columns,
        current_config=None,
        parent=None,
    ):
        super().__init__(parent)
        self.all_entries = tuple(all_entries)
        self.filtered_entries = tuple(filtered_entries)
        self._column_specs = tuple(columns)
        self.columns = tuple(
            dict.fromkeys(_column_name(column) for column in self._column_specs)
        )
        self.result_config = None
        self.setWindowTitle("Dataset overview and representative sample")
        self.resize(760, 560)
        self.setMinimumSize(620, 460)
        self._build(current_config or {})
        self._refresh()

    def _build(self, config):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._build_controls(layout, config)
        self._build_tables(layout)
        layout.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self._build_footer(outer)

        self.group_combo.currentIndexChanged.connect(self._refresh)
        self.metric_combo.currentIndexChanged.connect(self._refresh)
        self.per_stratum.valueChanged.connect(self._refresh)

    def _build_controls(self, layout, config):
        title = QtWidgets.QLabel("Full dataset overview")
        title.setObjectName("datasetTitle")
        layout.addWidget(title)
        self.count_label = QtWidgets.QLabel()
        self.count_label.setProperty("role", "muted")
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)

        form = QtWidgets.QFormLayout()
        self.group_combo = QtWidgets.QComboBox()
        self.group_combo.addItem("No grouping", "")
        for name in self.columns:
            self.group_combo.addItem(name, name)
        self.metric_combo = QtWidgets.QComboBox()
        self.metric_combo.addItem("No numeric layering", "")
        for name in _numeric_columns(self._column_specs, self.all_entries):
            self.metric_combo.addItem(name, name)
        self.per_stratum = QtWidgets.QSpinBox()
        configured_count = max(1, int(config.get("per_stratum", 2)))
        self.per_stratum.setRange(
            1, max(2, configured_count, len(self.filtered_entries))
        )
        self.per_stratum.setValue(configured_count)
        self._set_combo(self.group_combo, config.get("group_column", ""))
        self._set_combo(self.metric_combo, config.get("metric_column", ""))
        form.addRow("Group column:", self.group_combo)
        form.addRow("Numeric column:", self.metric_combo)
        form.addRow("Representatives per stratum:", self.per_stratum)
        layout.addLayout(form)

        self.method_label = QtWidgets.QLabel()
        self.method_label.setProperty("role", "muted")
        self.method_label.setWordWrap(True)
        layout.addWidget(self.method_label)

    def _build_tables(self, layout):
        layout.addWidget(self._heading("Numeric distribution (full data, no quality direction)"))
        self.distribution_table = _table(
            ["Set", "Numeric", "Missing", "Minimum", "Q1", "Median", "Q3", "Maximum"],
            100,
        )
        layout.addWidget(self.distribution_table)

        layout.addWidget(self._heading("Group coverage"))
        self.group_hint = QtWidgets.QLabel()
        self.group_hint.setProperty("role", "muted")
        self.group_hint.setWordWrap(True)
        layout.addWidget(self.group_hint)
        self.group_table = _table(["Group (color legend)", "All data", "Filtered"], 150)
        layout.addWidget(self.group_table)

        layout.addWidget(self._heading("Sampling strata"))
        self.strata_table = _table(["Stratum", "All data", "Filtered", "Sample"], 180)
        layout.addWidget(self.strata_table)

    def _build_footer(self, outer):
        footer = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        full = QtWidgets.QPushButton("Return to full filtered set")
        full.setAutoDefault(False)
        full.clicked.connect(lambda: self._finish(False))
        apply = QtWidgets.QPushButton("Apply sample")
        apply.setProperty("role", "primary")
        apply.clicked.connect(lambda: self._finish(True))
        footer.addWidget(cancel)
        footer.addStretch()
        footer.addWidget(full)
        footer.addWidget(apply)
        outer.addLayout(footer)

    @staticmethod
    def _heading(text):
        label = QtWidgets.QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    @staticmethod
    def _set_combo(combo, value):
        index = combo.findData(str(value or ""))
        combo.setCurrentIndex(max(0, index))

    def _config(self, enabled):
        return {
            "enabled": bool(enabled),
            "group_column": str(self.group_combo.currentData() or ""),
            "metric_column": str(self.metric_combo.currentData() or ""),
            "per_stratum": self.per_stratum.value(),
        }

    def _finish(self, enabled):
        self.result_config = self._config(enabled)
        self.accept()

    def _refresh(self, *args):
        group = str(self.group_combo.currentData() or "")
        metric = str(self.metric_combo.currentData() or "")
        per_stratum = self.per_stratum.value()
        sample, records = _sample_details(
            self.filtered_entries,
            group,
            metric,
            per_stratum,
            self.all_entries,
        )
        self.count_label.setText(
            f"All data: {len(self.all_entries):,} rows · Filtered: "
            f"{len(self.filtered_entries):,} rows · Sample preview: {len(sample):,} rows. "
            "Overview counts always use the full dataset, not the sample."
        )
        self.method_label.setText(
            f"Method: keep up to {per_stratum} evenly spaced source-order representatives "
            "per stratum. Numeric values use at most five quantile layers; missing values "
            "are a separate layer. Quantiles describe distribution only, not better or worse."
        )
        _show_distribution(self, metric)
        _show_groups(self, group)
        _show_strata(self, records)


def _show_distribution(dialog, metric):
    dialog.distribution_table.setRowCount(0 if not metric else 2)
    if not metric:
        dialog.distribution_table.setToolTip("Choose a numeric column to show its distribution.")
        return
    dialog.distribution_table.setToolTip("")
    for row, (label, entries) in enumerate(
        (("All data", dialog.all_entries), ("Filtered", dialog.filtered_entries))
    ):
        summary = summarize_distribution(entries, metric)
        values = (
            label, f"{summary['numeric']:,}", f"{summary['missing']:,}",
            _format_number(summary["minimum"]), _format_number(summary["q1"]),
            _format_number(summary["median"]), _format_number(summary["q3"]),
            _format_number(summary["maximum"]),
        )
        for column, value in enumerate(values):
            dialog.distribution_table.setItem(row, column, _read_only_item(value))
    dialog.distribution_table.resizeColumnsToContents()


def _show_groups(dialog, group):
    dialog.group_table.setRowCount(0)
    if not group:
        dialog.group_hint.setText("Choose a group column to show full-data and filtered coverage.")
        return
    key = lambda entry: (
        bool(str(entry.metadata.get(group, "")).strip()),
        str(entry.metadata.get(group, "")).strip(),
    )
    full = Counter(key(entry) for entry in dialog.all_entries)
    filtered = Counter(key(entry) for entry in dialog.filtered_entries)
    labels = list(full)
    dialog.group_hint.setText(
        f"{len(labels):,} groups. Each color is paired with its text label and counts."
    )
    dialog.group_table.setRowCount(len(labels))
    for row, label in enumerate(labels):
        values = (label[1] if label[0] else "Missing value", f"{full[label]:,}", f"{filtered[label]:,}")
        for column, value in enumerate(values):
            item = _read_only_item(value)
            if column == 0:
                background, foreground = GROUP_COLORS[row % len(GROUP_COLORS)]
                item.setBackground(QtGui.QColor(background))
                item.setForeground(QtGui.QColor(foreground))
            dialog.group_table.setItem(row, column, item)
    dialog.group_table.resizeColumnsToContents()


def _show_strata(dialog, records):
    dialog.strata_table.setRowCount(len(records))
    for row, record in enumerate(records):
        values = (record["label"], f"{record['total']:,}", f"{record['filtered']:,}", f"{record['selected']:,}")
        for column, value in enumerate(values):
            item = _read_only_item(value)
            if column == 0:
                item.setToolTip(value)
            dialog.strata_table.setItem(row, column, item)
    dialog.strata_table.resizeColumnsToContents()
    dialog.strata_table.horizontalHeader().setStretchLastSection(False)
    dialog.strata_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)


__all__ = ["DatasetOverviewDialog"]
