"""Cluster the filtered review candidates and switch between group views."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from pymol.Qt import QtCore, QtWidgets

from .cluster_review import cluster_on_source, compile_cluster_score, rank_clusters
from .filtering import column_expression
from .panel_style import apply_style


class ClusterTable(QtCore.QAbstractTableModel):
    headers = ("Cluster", "Members", "Representative", "Score")

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []
        self.numbers = {}

    def update(self, rows, numbers):
        self.beginResetModel()
        self.rows, self.numbers = rows, numbers
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self.headers[section]

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or role not in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            return None
        cluster = self.rows[index.row()]
        score = "—" if cluster.score is None else f"{cluster.score:.6g}"
        return (self.numbers[cluster.key], len(cluster.members), cluster.label, score)[index.column()]


class ClusterDialog(QtWidgets.QDialog):
    def __init__(self, parent, rows, columns, source, current, show_view, *, sort="", higher=True):
        super().__init__(parent)
        self.rows, self.columns, self.source = rows, columns, source
        self.is_current, self.show_view = current, show_view
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pymol-cluster")
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(125)
        self.timer.timeout.connect(self._poll)
        self.cancel_event = None
        self.future = None
        self.result = None
        self.setWindowTitle("Cluster review candidates")
        self.resize(620, 495)
        self._build(sort, higher)

    def _build(self, sort, higher):
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f"{len(self.rows):,} candidates in the current filter"))
        self.inputs = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.inputs)
        form.setContentsMargins(0, 0, 0, 0)
        self.method = _combo((("Sequence", "sequence"), ("Structure", "structure"),
                              ("Sequence and structure", "both")))
        self.location = _combo(((f"SSH · {self.source.profile}", True), ("This computer", False))
                               if self.source else (("This computer", False),))
        self.location.setEnabled(self.source is not None)
        self.sequence = _combo((("Read from structure", ""), *((name, name) for name in self.columns)))
        self.chain = QtWidgets.QLineEdit()
        self.chain.setPlaceholderText("Auto for one protein chain, or enter the exact chain ID")
        self.identity, self.tm, self.coverage = (_percentage(value) for value in (90, 70, 80))
        self.threads = QtWidgets.QSpinBox()
        self.threads.setRange(1, 32)
        self.threads.setValue(2)
        for label, field in (("Cluster by", self.method), ("Run on", self.location), ("Sequence", self.sequence),
                             ("Protein chain", self.chain), ("Minimum sequence identity", self.identity),
                             ("Minimum TM-score", self.tm), ("Coverage on both sides", self.coverage),
                             ("CPU threads", self.threads)):
            form.addRow(label, field)
        self.method.currentIndexChanged.connect(lambda: self._fields(form))
        self.sequence.currentIndexChanged.connect(lambda: self._fields(form))
        self._fields(form)
        layout.addWidget(self.inputs)
        rank = QtWidgets.QHBoxLayout()
        self.score = _combo((("", ""), *((column_expression(name), name) for name in self.columns)))
        self.score.setEditable(True)
        self.score.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.score.lineEdit().setPlaceholderText("Column or expression, e.g. ipsae - 0.1 * rmsd")
        if sort:
            self.score.setEditText(column_expression(sort))
        self.direction = _combo((("Higher is better", True), ("Lower is better", False)))
        self.direction.setCurrentIndex(0 if higher else 1)
        rank.addWidget(QtWidgets.QLabel("Representative score"))
        rank.addWidget(self.score, 1)
        rank.addWidget(self.direction)
        layout.addLayout(rank)

        self.table = QtWidgets.QTableView()
        self.model = ClusterTable(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(lambda _index: self._show_members())
        layout.addWidget(self.table, 1)
        self.status = QtWidgets.QLabel("Leave the score blank to use the original row order.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        execution = QtWidgets.QHBoxLayout()
        self.all = _button("Full filtered list", self._show_all)
        self.representatives = _button("Show representatives", self._show_representatives)
        self.members = _button("Show cluster members", self._show_members)
        self.cancel = _button("Cancel run", self._cancel)
        self.run = _button("Run clustering", self._start)
        self.run.setProperty("role", "primary")
        self.score.lineEdit().returnPressed.connect(self._show_representatives)
        execution.addWidget(self.run)
        execution.addWidget(self.cancel)
        execution.addStretch()
        layout.insertLayout(3, execution)
        for button in (self.all, self.representatives, self.members):
            actions.addWidget(button)
        layout.addLayout(actions)
        apply_style(self, "clusterReview")
        self._set_running(False)

    def _fields(self, form):
        method = self.method.currentData()
        for field, visible in ((self.sequence, method != "structure"), (self.identity, method != "structure"),
                               (self.tm, method != "sequence"),
                               (self.chain, method != "sequence" or not self.sequence.currentData())):
            field.setVisible(visible)
            form.labelForField(field).setVisible(visible)

    def _set_running(self, running):
        self.inputs.setEnabled(not running)
        self.run.setEnabled(not running)
        self.representatives.setEnabled(not running and self.result is not None)
        self.members.setEnabled(not running and self.result is not None)
        self.cancel.setVisible(running)

    def _start(self):
        try:
            if not self.is_current():
                raise ValueError("The dataset or filter changed. Reopen Cluster to use the current candidates.")
            compile_cluster_score(self.score.currentText(), self.columns)
        except ValueError as error:
            self.status.setText(str(error))
            return
        method = self.method.currentData()
        options = {"method": method, "chain": self.chain.text().strip(),
                   "sequence_column": self.sequence.currentData() if method != "structure" else "",
                   "identity": self.identity.value() / 100, "tm": self.tm.value() / 100,
                   "coverage": self.coverage.value() / 100, "threads": self.threads.value()}
        self.cancel_event = threading.Event()
        self.started = time.monotonic()
        self.run_where = self.location.currentText()
        remote = self.source if self.location.currentData() else None
        self.future = self.pool.submit(cluster_on_source, self.rows, options, remote=remote,
                                       cancel_event=self.cancel_event)
        self.status.setToolTip("")
        self._set_running(True)
        self.timer.start()
        self._poll()

    def _poll(self):
        if not self.is_current():
            self._cancel()
        if not self.future.done():
            message = "Cancelling…" if self.cancel_event.is_set() else f"Clustering on {self.run_where}"
            self.status.setText(f"{message} · {time.monotonic() - self.started:.1f} s")
            return
        self.timer.stop()
        future, self.future = self.future, None
        if self.cancel_event.is_set():
            self.status.setText("Clustering cancelled. The review view did not change.")
        else:
            try:
                self.result = future.result()
                self.model.update([], {})
                self._show_representatives()
            except Exception as error:
                detail = str(error).strip() or type(error).__name__
                self.status.setText(detail.splitlines()[0])
                self.status.setToolTip(detail)
        self._set_running(False)

    def _show_representatives(self):
        if self.result is None or self.future is not None:
            return
        try:
            score = compile_cluster_score(self.score.currentText(), self.columns)
            rows = rank_clusters(self.rows, self.result["groups"], score, self.direction.currentData())
        except ValueError as error:
            self.status.setText(str(error))
            return
        if not rows:
            return
        assignments = self.result["groups"]
        keys = dict.fromkeys(assignments[row.id] for row in sorted(self.rows, key=lambda row: row.ordinal)
                             if row.id in assignments)
        self.model.update(rows, {key: index for index, key in enumerate(keys, 1)})
        self.table.selectRow(0)
        skipped = self.result["skipped"]
        missing = sum(row.score is None for row in rows) if score else 0
        self.status.setText(f"{len(rows):,} clusters · {len(self.result['groups']):,} members"
                            + (f" · {len(skipped):,} skipped" if skipped else "")
                            + (f" · {missing:,} representatives without a score" if missing else ""))
        labels = {row.id: row.display_name for row in self.rows if row.id in skipped}
        reasons = [f"{labels.get(key, key)}: {value}" for key, value in list(skipped.items())[:15]]
        self.status.setToolTip("\n".join(reasons))
        self._request_view(tuple(row.members[0] for row in rows), "Cluster representatives")

    def _request_view(self, ids, title):
        if not self.is_current():
            self.status.setText("The dataset or filter changed. Reopen Cluster to use the current candidates.")
            return False
        if not self.show_view(ids, title):
            self.status.setText("Wait for the current table operation, then try again.")
            return False
        return True

    def _show_members(self):
        row = self.table.currentIndex().row()
        if 0 <= row < len(self.model.rows):
            cluster = self.model.rows[row]
            self._request_view(cluster.members, f"Cluster {self.model.numbers[cluster.key]} members")

    def _show_all(self):
        self._request_view((), "")

    def _cancel(self):
        if self.future is not None:
            self.cancel_event.set()

    def closeEvent(self, event):
        self._cancel()
        super().closeEvent(event)

    def dispose(self):
        self._cancel()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.close()
        self.deleteLater()


def _combo(entries):
    control = QtWidgets.QComboBox()
    for label, value in entries:
        control.addItem(label, value)
    return control


def _percentage(value):
    control = QtWidgets.QSpinBox()
    control.setRange(10, 100)
    control.setValue(value)
    control.setSuffix("%")
    return control


def _button(label, action):
    control = QtWidgets.QPushButton(label)
    control.setAutoDefault(False)
    control.clicked.connect(action)
    return control
