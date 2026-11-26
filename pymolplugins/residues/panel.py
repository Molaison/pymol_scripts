"""Residue color controls embedded in the shared Scene view Color page."""

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets

import traceback

from . import presentation
from pymolplugins.residues.analysis import quoted
from ._coretosurf import DEFAULT_CUTOFFS
from .commands import pp_residues


COLOR_CHOICES = (("Core / boundary / surface", "residue_layers"),
                 ("Relative exposure (RSA)", "residue_rsa"),
                 ("Hydropathy (Kyte–Doolittle)", "residue_hydropathy"),
                 ("Exposed hydrophobic residues", "residue_exposed"),
                 ("Nonlocal Cα contacts", "residue_contacts"))
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


def _label(text, widget):
    label = QtWidgets.QLabel(text)
    label.setBuddy(widget)
    widget.setAccessibleName(text)
    return label


def _threshold(parent, row, label, value, name):
    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    slider.setRange(0, 500)
    slider.setValue(int(value))
    slider.setObjectName(name + "_slider")
    slider.setAccessibleName(label + " slider")
    spin = _spin(0, 500, value, 0)
    spin.setObjectName(name + "_cutoff")
    slider.valueChanged.connect(spin.setValue)
    spin.valueChanged.connect(lambda v: slider.setValue(int(v)))
    parent.addWidget(_label(label, spin), row, 0)
    parent.addWidget(slider, row, 1)
    parent.addWidget(spin, row, 2)
    return slider, spin


class ResidueControls:
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.state = QtWidgets.QSpinBox()
        self.state.setRange(0, 99999)
        self.state.setSpecialValueText("Current")
        self.state.setObjectName("residue_state")
        row.addWidget(_label("State", self.state))
        row.addWidget(self.state)
        self.live = QtWidgets.QCheckBox("Live thresholds after Apply")
        self.live.setChecked(True)
        row.addWidget(self.live)
        row.addStretch(1)
        layout.addLayout(row)
        self._build_parameters(layout)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.status)
        self.details_toggle = QtWidgets.QToolButton()
        self.details_toggle.setText("Residue values and export")
        self.details_toggle.setObjectName("residue_details")
        self.details_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.details_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.details_toggle.setCheckable(True)
        layout.addWidget(self.details_toggle)
        self.details = QtWidgets.QWidget(self)
        details = QtWidgets.QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        self._build_results(details)
        self._build_exports(details)
        layout.addWidget(self.details)
        self.details.hide()
        self.details_toggle.toggled.connect(self._toggle_details)
        self.state.valueChanged.connect(self.schedule)

    def _build_results(self, layout):
        self.table = QtWidgets.QTableWidget(0, len(HEADERS))
        self.table.setObjectName("residue_table")
        self.table.setAccessibleName("Residue values")
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.table.setMinimumHeight(150)
        self.table.setMaximumHeight(220)
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
        grid.addWidget(_label("Layer cutoffs", self.units), 0, 0)
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
                row.addWidget(_label(label, widget))
                row.addWidget(widget)
                widget.valueChanged.connect(self.schedule)
            row.addStretch(1)
            layout.addWidget(container)
        self.units.currentIndexChanged.connect(self.change_unit)
        self.core.valueChanged.connect(lambda v: self.threshold_changed(True, v))
        self.boundary.valueChanged.connect(lambda v: self.threshold_changed(False, v))

    def _build_exports(self, layout):
        row = QtWidgets.QHBoxLayout()
        self.format = QtWidgets.QComboBox()
        self.format.setAccessibleName("Layer export format")
        self.format.addItem("PyMOL selections", "pymol")
        self.format.addItem("Rosetta resnums", "rosetta")
        row.addWidget(self.format)
        row.addWidget(_button("Copy layers", self.copy_layers, "residue_copy"))
        row.addWidget(_button("Create selections", self.create_selections, "residue_create"))
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(_button("Residue CSV…", self.export_csv, "residue_export"))
        row.addWidget(_button("Contact CSV…", lambda: self.export_csv(True), "residue_export_contacts"))
        row.addWidget(_button("Recompute values", lambda: self.analyze(True), "residue_recompute"))
        layout.addLayout(row)

    def _toggle_details(self, visible):
        self.details.setVisible(visible)
        self.details_toggle.setArrowType(QtCore.Qt.DownArrow if visible else QtCore.Qt.RightArrow)
        if visible and not self.report:
            self.analyze()


class ResidueColorControls(ResidueControls, QtWidgets.QWidget):
    message = QtCore.Signal(str)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setObjectName("residue_colors")
        self.source, self.target = source, None
        self.report, self.mode, self.unit = None, "layers", "abs"
        self.preview_active = False
        self.snapshot_id = None
        self.cutoffs = {u: list(v) for u, v in DEFAULT_CUTOFFS.items()}
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._preview)
        self._build_ui()

    def stop_preview(self):
        self.timer.stop()
        self.preview_active = False

    def _clear_report(self):
        self.report = None
        self.table.setRowCount(0)
        self.status.clear()
        self.message.emit("")

    def refresh(self):
        if self.target != self.source():
            self.stop_preview()
            self.target = self.source()
            self._clear_report()

    def set_mode(self, mode):
        self.stop_preview()
        if self.mode != mode:
            self._clear_report()
        self.mode = mode
        self.layer_options.setVisible(mode in ("layers", "rsa"))
        self.contact_options.setVisible(mode == "contacts")
        self.exposure_options.setVisible(mode == "exposed")

    def threshold_changed(self, is_core, value):
        if is_core and value > self.boundary.value():
            self.boundary.setValue(value)
        elif not is_core and value < self.core.value():
            self.core.setValue(value)
        self.cutoffs[self.unit] = [self.core.value(), self.boundary.value()]
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
        self.message.emit("")
        if self.preview_active and self.live.isChecked() and self.isVisible():
            self.timer.start(100)
        else:
            self._clear_report()

    def arguments(self):
        return dict(mode=self.mode, core=self.core.value(), boundary=self.boundary.value(),
                    unit=self.unit, state=self.state.value(), cutoff=self.cutoff.value(),
                    gap=int(self.gap.value()), exposure=self.exposure.value(),
                    hydrophobicity=self.hydrophobicity.value())

    def command(self):
        values = ", ".join("%s=%s" % item for item in self.arguments().items())
        return "pp_residues (model %s), %s" % (quoted(self.source()), values)

    def analyze(self, force=False):
        self.timer.stop()
        self.refresh()
        if not self.source():
            self.status.setText("Choose a loaded protein object.")
            return False
        try:
            self.report = pp_residues("model " + quoted(self.source()), color=0,
                                      recompute=int(bool(force)), **self.arguments())
            self._show_report()
            return True
        except Exception as error:
            self._clear_report()
            self.status.setText("Error: " + str(error))
            traceback.print_exc()
            return False

    def apply(self):
        if not self.analyze():
            return None
        self.snapshot_id = presentation.apply_colors(self.report, self.mode, self.exposure.value(),
                                                     self.hydrophobicity.value())
        self.preview_active = True
        return self.snapshot_id

    def _color(self):
        presentation.color_report(self.report, self.mode, self.exposure.value(),
                                   self.hydrophobicity.value(), remember=False)

    def _preview(self):
        if not self.preview_active or not self.isVisible() or not self.live.isChecked():
            return
        if not presentation.can_preview(self.snapshot_id):
            self.stop_preview()
            self._clear_report()
            self.message.emit("Apply again to update residue colors after the newer style or object change.")
            return
        if self.analyze():
            try:
                self._color()
            except Exception as error:
                self.status.setText("Error: " + str(error))
                traceback.print_exc()

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
                   if self.mode in ("layers", "rsa") else
                   "%d protein residues · %d nonlocal Cα contacts" % (len(self.report["residues"]), len(self.report["pairs"])))
        self.status.setText(summary + "\n" + self.legend() + " " + " ".join(self.report["warnings"]))

    def legend(self):
        return {"layers": "Orange: core · Green: boundary · Blue: surface",
                "rsa": "Blue: 0% exposed → orange: 100% exposed. Values above 100% are retained.",
                "hydropathy": "Blue: hydrophilic (−4.5) → orange: hydrophobic (+4.5). Gray: unavailable.",
                "exposed": "Orange: residues meeting the RSA and hydropathy cutoffs. Gray: other residues.",
                "contacts": "Blue: fewer nonlocal Cα neighbors → orange: more. Gray: missing Cα."}[self.mode]

    def copy_layers(self):
        if self.analyze():
            text, warnings = presentation.format_layers(self.report, self.format.currentData())
            QtWidgets.QApplication.clipboard().setText(text)
            self.message.emit("Copied layer definitions. " + " ".join(warnings))

    def create_selections(self):
        if self.analyze():
            try:
                names = presentation.create_selections(self.report)
                self.message.emit("Created %d layer selections." % len(names))
            except Exception as error:
                self.message.emit("Error: " + str(error))
                traceback.print_exc()

    def export_csv(self, pairs=False):
        if not self.analyze():
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export contacts" if pairs else "Export residues",
                                                       "contacts.csv" if pairs else "residues.csv", "CSV (*.csv)")
        if path:
            try:
                self.message.emit("Saved " + presentation.write_csv(self.report, path, pairs))
            except Exception as error:
                self.message.emit("Error: " + str(error))
                traceback.print_exc()

    def zoom_residue(self, row, _column):
        if self.report:
            index = self.table.item(row, 0).data(QtCore.Qt.UserRole)
            cmd.zoom(presentation.residue_expression([self.report["residues"][index]], indices=True), buffer=5)

    def hideEvent(self, event):
        self.stop_preview()
        super().hideEvent(event)


def show_panel():
    from pymolplugins.host.show_presets import show_presets
    return show_presets(tab="color", palette="residue_layers")
