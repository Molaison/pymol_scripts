"""One modeless panel for exposure, hydropathy and residue contacts."""

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets

import traceback

from . import presentation
from ._coretosurf import DEFAULT_CUTOFFS
from .commands import pp_residues


_panel = None
HEADERS = ("Object", "Segment", "Chain", "Residue", "AA", "SASA Å²", "RSA %",
           "Layer", "Hydropathy", "Cα neighbors")
FIELDS = ("object", "segi", "chain", "resi", "resn", "sasa", "rsa", "layer", "hydropathy", "contacts")


def _button(text, slot, name):
    button = QtWidgets.QPushButton(text)
    button.setObjectName(name)
    button.clicked.connect(slot)
    return button


def _spin(low, high, value, decimals=1):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setRange(low, high)
    widget.setDecimals(decimals)
    widget.setValue(value)
    return widget


def _threshold(parent, row, label, value, name):
    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    slider.setRange(0, 500)
    slider.setValue(int(value))
    slider.setObjectName(name + "_slider")
    spin = _spin(0, 500, value, 0)
    spin.setObjectName(name + "_cutoff")
    slider.valueChanged.connect(spin.setValue)
    spin.valueChanged.connect(lambda v: slider.setValue(int(v)))
    parent.addWidget(QtWidgets.QLabel(label), row, 0)
    parent.addWidget(slider, row, 1)
    parent.addWidget(spin, row, 2)
    return slider, spin


class ResidueControls:
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.objects = QtWidgets.QComboBox()
        self.objects.setEditable(True)
        self.objects.setObjectName("residue_selection")
        self.objects.setToolTip("Loaded object or a PyMOL protein selection")
        row.addWidget(QtWidgets.QLabel("Object / selection"))
        row.addWidget(self.objects, 1)
        row.addWidget(_button("Refresh", self.refresh_objects, "residue_refresh"))
        self.state = QtWidgets.QSpinBox()
        self.state.setRange(0, 99999)
        self.state.setSpecialValueText("Current")
        self.state.setObjectName("residue_state")
        row.addWidget(QtWidgets.QLabel("State"))
        row.addWidget(self.state)
        from pymolplugins.host.workspace import WindowModeButton
        self.window_mode_button = WindowModeButton(self)
        row.addWidget(self.window_mode_button)
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.setObjectName("residue_mode")
        for text, mode in (("Core / boundary / surface", "layers"), ("Relative exposure", "rsa"),
                           ("Hydropathy (Kyte–Doolittle)", "hydropathy"),
                           ("Exposed hydrophobic residues", "exposed"), ("Nonlocal Cα contacts", "contacts")):
            self.mode.addItem(text, mode)
        row.addWidget(QtWidgets.QLabel("Color by"))
        row.addWidget(self.mode, 1)
        self.live = QtWidgets.QCheckBox("Live thresholds")
        self.live.setChecked(True)
        row.addWidget(self.live)
        layout.addLayout(row)
        self._build_parameters(layout)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(_button("Analyze", self.analyze, "residue_analyze"))
        row.addWidget(_button("Recompute SASA", lambda: self.analyze(True), "residue_recompute"))
        row.addWidget(_button("Restore colors", self.restore, "residue_restore"))
        row.addStretch(1)
        layout.addLayout(row)
        self._build_results(layout)
        self._build_exports(layout)
        self.mode.currentIndexChanged.connect(self.schedule)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.state.valueChanged.connect(self.schedule)
        self.objects.activated.connect(self.schedule)
        self.objects.lineEdit().returnPressed.connect(self.analyze)

    def _build_results(self, layout):
        self.status = QtWidgets.QLabel("Load a protein and choose Analyze.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setObjectName("residue_table")
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.table.setToolTip("Double-click a residue to zoom to it.")
        self.table.cellDoubleClicked.connect(self.zoom_residue)
        layout.addWidget(self.table, 1)

    def _build_parameters(self, layout):
        self.layer_options = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(self.layer_options)
        grid.setContentsMargins(0, 0, 0, 0)
        self.units = QtWidgets.QComboBox()
        self.units.setObjectName("residue_units")
        self.units.addItem("SASA Å²", "abs")
        self.units.addItem("Relative ASA % (Tien)", "rel")
        grid.addWidget(QtWidgets.QLabel("Layer cutoffs"), 0, 0)
        grid.addWidget(self.units, 0, 1, 1, 2)
        self.core_slider, self.core = _threshold(grid, 1, "Core below", 20, "core")
        self.boundary_slider, self.boundary = _threshold(grid, 2, "Surface from", 40, "boundary")
        layout.addWidget(self.layer_options)
        self.cutoff, self.gap = _spin(0.1, 50, 8), _spin(0, 100, 2, 0)
        self.exposure, self.hydrophobicity = _spin(0, 500, 25), _spin(-4.5, 4.5, 0)
        self.contact_options, self.exposure_options = QtWidgets.QWidget(), QtWidgets.QWidget()
        for container, items in ((self.contact_options, (("Cα distance Å", self.cutoff), ("Sequence gap", self.gap))),
                                  (self.exposure_options, (("Exposed RSA ≥ %", self.exposure), ("Hydropathy >", self.hydrophobicity)))):
            row = QtWidgets.QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            for label, widget in items:
                row.addWidget(QtWidgets.QLabel(label))
                row.addWidget(widget)
                widget.valueChanged.connect(self.schedule)
            row.addStretch(1)
            container.hide()
            layout.addWidget(container)
        self.units.currentIndexChanged.connect(self.change_unit)
        self.core.valueChanged.connect(lambda v: self.threshold_changed(True, v))
        self.boundary.valueChanged.connect(lambda v: self.threshold_changed(False, v))
        for slider in (self.core_slider, self.boundary_slider):
            slider.sliderReleased.connect(self.analyze)

    def _build_exports(self, layout):
        row = QtWidgets.QHBoxLayout()
        self.format = QtWidgets.QComboBox()
        self.format.addItem("PyMOL selections", "pymol")
        self.format.addItem("Rosetta resnums", "rosetta")
        row.addWidget(self.format)
        row.addWidget(_button("Copy layers", self.copy_layers, "residue_copy"))
        row.addWidget(_button("Create selections", self.create_selections, "residue_create"))
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(_button("Export residue CSV…", self.export_csv, "residue_export"))
        row.addWidget(_button("Export contact CSV…", lambda: self.export_csv(True), "residue_export_contacts"))
        row.addStretch(1)
        layout.addLayout(row)


class ResiduePanel(ResidueControls, QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Residue analysis")
        self.setObjectName("residue_analysis")
        self.setMinimumSize(540, 440)
        self.resize(780, 650)
        self.report = None
        self.unit = "abs"
        self.cutoffs = {u: list(v) for u, v in DEFAULT_CUTOFFS.items()}
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.analyze)
        self._build_ui()
        from pymolplugins.annotation.panel_style import workspace_style
        self.setStyleSheet(workspace_style() + "\nQWidget#residue_analysis { background: white; }")
        self.refresh_objects()

    def update_mode(self):
        mode = self.mode.currentData()
        self.layer_options.setVisible(mode in ("layers", "rsa"))
        self.contact_options.setVisible(mode == "contacts")
        self.exposure_options.setVisible(mode == "exposed")

    def refresh_objects(self):
        current = self.objects.currentText()
        names = [n for n in cmd.get_names("objects") if cmd.get_type(n) == "object:molecule"]
        self.objects.blockSignals(True)
        self.objects.clear()
        self.objects.addItems(names)
        if current:
            self.objects.setEditText(current)
        self.objects.blockSignals(False)

    def threshold_changed(self, is_core, value):
        if is_core and value > self.boundary.value():
            self.boundary.setValue(value)
        elif not is_core and value < self.core.value():
            self.core.setValue(value)
        self.cutoffs[self.unit] = [self.core.value(), self.boundary.value()]
        if self.live.isChecked():
            self.schedule()

    def change_unit(self):
        self.unit = self.units.currentData()
        core, boundary = self.cutoffs[self.unit]
        self.core.blockSignals(True)
        self.boundary.blockSignals(True)
        maximum = 500 if self.unit == "abs" else 150
        for slider, spin, value in ((self.core_slider, self.core, core),
                                    (self.boundary_slider, self.boundary, boundary)):
            slider.setMaximum(maximum)
            spin.setMaximum(maximum)
            spin.setValue(value)
            slider.setValue(int(value))
        self.core.blockSignals(False)
        self.boundary.blockSignals(False)
        self.schedule()

    def schedule(self, *_):
        self.timer.start(100)

    def analyze(self, force=False):
        self.timer.stop()
        selection = self.objects.currentText().strip()
        if not selection:
            self.status.setText("No protein loaded. Load a structure, then Refresh.")
            return False
        try:
            self.report = pp_residues(selection, mode=self.mode.currentData(),
                                      core=self.core.value(), boundary=self.boundary.value(),
                                      unit=self.unit, state=self.state.value(), recompute=int(bool(force)),
                                      cutoff=self.cutoff.value(), gap=int(self.gap.value()),
                                      exposure=self.exposure.value(), hydrophobicity=self.hydrophobicity.value())
            self._show_report()
            return True
        except Exception as error:
            self.report = None
            self.status.setText("Error: " + str(error))
            traceback.print_exc()
            return False

    def _show_report(self):
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.report["residues"]))
        for row, record in enumerate(self.report["residues"]):
            for col, field in enumerate(FIELDS):
                value = record[field]
                if isinstance(value, float):
                    value = round(value, 2)
                item = QtWidgets.QTableWidgetItem()
                item.setData(QtCore.Qt.DisplayRole, "—" if value is None else value)
                item.setData(QtCore.Qt.UserRole, row)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.table.setUpdatesEnabled(True)
        counts = self.report["counts"]
        summary = ("Core {core} · Boundary {boundary} · Surface {surface} · Unclassified {unclassified}".format(**counts)
                   if self.mode.currentData() in ("layers", "rsa") else
                   "%d protein residues · %d nonlocal Cα contacts" % (len(self.report["residues"]), len(self.report["pairs"])))
        self.status.setText(summary + "\n" + self.legend() + " " + " ".join(self.report["warnings"]))

    def legend(self):
        mode = self.mode.currentData()
        return {"layers": "Orange: core · Green: boundary · Blue: surface",
                "rsa": "Blue: 0% exposed → orange: 100% exposed. Values above 100% are retained.",
                "hydropathy": "Blue: hydrophilic (−4.5) → orange: hydrophobic (+4.5). Gray: unavailable.",
                "exposed": "Orange: residues meeting the RSA and hydropathy cutoffs. Gray: other residues.",
                "contacts": "Blue: fewer nonlocal Cα neighbors → orange: more. Gray: missing Cα."}[mode]

    def restore(self):
        presentation.clear()
        self.status.setText("Original atom colors restored; generated layer selections removed.")

    def copy_layers(self):
        if self.analyze():
            text, warnings = presentation.format_layers(self.report, self.format.currentData())
            QtWidgets.QApplication.clipboard().setText(text)
            print(text)
            self.status.setText("Copied layer definitions. " + " ".join(warnings))

    def create_selections(self):
        if self.analyze():
            try:
                names = presentation.create_selections(self.report)
                self.status.setText("Created: " + ", ".join(names))
            except Exception as error:
                self.status.setText("Error: " + str(error))
                traceback.print_exc()

    def export_csv(self, pairs=False):
        if not self.analyze():
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export contacts" if pairs else "Export residues",
                                                       "contacts.csv" if pairs else "residues.csv", "CSV (*.csv)")
        if path:
            try:
                self.status.setText("Saved " + presentation.write_csv(self.report, path, pairs))
            except Exception as error:
                self.status.setText("Error: " + str(error))
                traceback.print_exc()

    def zoom_residue(self, row, _column):
        if self.report:
            index = self.table.item(row, 0).data(QtCore.Qt.UserRole)
            cmd.zoom(presentation.residue_expression([self.report["residues"][index]], indices=True), buffer=5)


def show_panel():
    global _panel
    from pymolplugins.host.workspace import show_workspace
    if _panel is None:
        _panel = ResiduePanel()
    else:
        _panel.refresh_objects()
    show_workspace(_panel, "residues")
    _panel.analyze()
    return _panel
