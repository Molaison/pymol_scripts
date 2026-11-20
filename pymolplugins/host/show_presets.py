"""Modeless display presets for molecules already open in PyMOL."""

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets

from pymolplugins.annotation.bundle_view import CandidateCavity, draw_candidate_cavity, prepare_candidate_cavity
from pymolplugins.annotation.panel_style import apply_style
from pymolplugins.annotation.review_membrane import MembraneNotice
from pymolplugins.annotation.review_view_presets import (
    SHOW_PRESETS, draw_review_view, loaded_molecules, prepare_review_command, show_preset_command,
)
from pymolplugins.annotation.view_controls import CavityControls, ViewLayers
from pymolplugins.annotation.view_jobs import ViewJobs
from pymolplugins.styles.widgets import AppearancePage


_dialog = None


def _combo(entries, label):
    combo = QtWidgets.QComboBox()
    combo.setAccessibleName(label)
    for title, value in entries:
        combo.addItem(title, value)
    return combo


class ShowPresetsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PymolPlugins · Change scene view")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.resize(500, 290)
        self.request = 0
        self._build()
        self.jobs = ViewJobs(self, self._is_current, self._prepare, self._draw)
        self.finished.connect(lambda _result: self.jobs.close())
        self.refresh_objects()

    def _refresh_scope(self, *_):
        if self.tabs.currentIndex() == 2:
            self.scope_label.setText("Applies to the whole scene")
        else:
            target = self.objects.currentText()
            self.scope_label.setText(f"Applies to {target}" if target else "Choose a loaded object")

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        object_picker = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(object_picker)
        form.setContentsMargins(0, 0, 0, 0)
        self.objects = _combo((), "Object")
        self.objects.setMinimumContentsLength(16)
        self.objects.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        object_row = QtWidgets.QHBoxLayout()
        object_row.addWidget(self.objects, 1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_objects)
        object_row.addWidget(refresh)
        form.addRow("Object", object_row)
        layout.addWidget(object_picker)
        self.scope_label = QtWidgets.QLabel()
        self.scope_label.setProperty("role", "muted")
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self.tabs)
        structure = QtWidgets.QWidget(self.tabs)
        self.tabs.addTab(structure, "Structure")
        self.color_page = AppearancePage("color", self.objects.currentData, self.tabs)
        self.lighting_page = AppearancePage("lighting", self.objects.currentData, self.tabs)
        self.tabs.addTab(self.color_page, "Color")
        self.tabs.addTab(self.lighting_page, "Lighting")
        self.tabs.currentChanged.connect(lambda index: object_picker.setVisible(index != 2))
        self.tabs.currentChanged.connect(self._refresh_scope)
        layout = QtWidgets.QVBoxLayout(structure)
        form = QtWidgets.QFormLayout()
        self.preset = _combo(((title, key) for key, title in SHOW_PRESETS.items()), "Structure view")
        form.addRow("View", self.preset)
        layout.addLayout(form)
        self.options = QtWidgets.QWidget()
        choices = QtWidgets.QFormLayout(self.options)
        choices.setContentsMargins(0, 0, 0, 0)
        self.mode = _combo((("Auto", "auto"), ("Protein–protein", "protein"),
                            ("Protein–ligand", "ligand")), "Interface type")
        self.radius = _combo((("4 Å", "4"), ("6 Å", "6")), "Contact distance")
        self.scope = _combo((("Nearby", "nearby"), ("Interacting", "interacting")), "Side chains")
        self.first = _combo((), "First chain")
        self.second = _combo((), "Second chain")
        for label, element in (("Interface type", self.mode), ("Contact distance", self.radius),
                               ("Side chains", self.scope), ("First chain", self.first),
                               ("Second chain", self.second)):
            choices.addRow(label, element)
        layout.addWidget(self.options)
        self.layers = ViewLayers(self)
        layout.addWidget(self.layers)
        self.cavities = CavityControls(self)
        layout.addWidget(self.cavities)
        self.quick_view_status = QtWidgets.QLabel()
        self.quick_view_status.setWordWrap(True)
        self.quick_view_status.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.quick_view_status)
        row = QtWidgets.QHBoxLayout()
        self.copy = QtWidgets.QPushButton("Copy command")
        self.apply = QtWidgets.QPushButton("Apply to object")
        self.apply.setProperty("role", "primary")
        self.apply.clicked.connect(self.apply_preset)
        self.copy.clicked.connect(self.copy_command)
        row.addWidget(self.copy)
        row.addStretch(1)
        row.addWidget(self.apply)
        layout.addLayout(row)
        self.objects.currentIndexChanged.connect(self._object_changed)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.mode.currentIndexChanged.connect(self._update_options)
        self.first.currentIndexChanged.connect(self._update_options)
        apply_style(self, "showPresets")

    def refresh_objects(self):
        previous = self.objects.currentData()
        names = loaded_molecules(cmd)
        enabled = set(cmd.get_names("objects", enabled_only=1))
        choice = previous if previous in names else next((name for name in names if name in enabled), None)
        self.objects.blockSignals(True)
        self.objects.clear()
        for name in names:
            self.objects.addItem(name, name)
        if choice is not None:
            self.objects.setCurrentIndex(self.objects.findData(choice))
        self.objects.blockSignals(False)
        if previous != self.objects.currentData():
            self._object_changed()
        self._update_options()

    def _object_changed(self):
        self._refresh_scope()
        self.jobs.cancel()
        source = self.objects.currentData()
        chains = cmd.get_chains(f"%{source} and polymer.protein") if source else []
        for combo in (self.first, self.second):
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Auto", "auto")
            for chain in chains:
                combo.addItem(chain or "(blank)", "chain:" + chain)
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)
        self._preset_changed()

    def _preset_changed(self):
        self.jobs.cancel()
        self.cavities.reset(False)
        self.quick_view_status.clear()
        self._update_options()

    def _update_options(self):
        key = self.preset.currentData()
        self.layers.set_view(key)
        self.options.setVisible(key in ("interface", "mass"))
        form = self.options.layout()
        for control in (self.mode, self.radius, self.scope):
            control.setVisible(key == "interface")
            form.labelForField(control).setVisible(key == "interface")
        interface = key == "interface"
        form.labelForField(self.first).setText("Target chain" if interface else "First chain")
        form.labelForField(self.second).setText("Partner chain" if interface else "Second chain")
        self.second.setEnabled(self.mode.currentData() != "ligand" if interface else self.first.currentData() != "auto")
        ready = bool(self.objects.currentData())
        self.apply.setEnabled(ready)
        self.copy.setEnabled(ready)
        self.color_page.refresh()
        self.lighting_page.refresh()
        if not ready:
            self.quick_view_status.setText("Load a structure in PyMOL, then click Refresh.")

    def command(self):
        key = self.preset.currentData()
        first = self.first.currentData()
        second = self.second.currentData() if self.second.isEnabled() else "auto"
        target, binder = (second, first) if key == "mass" else (first, second)
        return show_preset_command(key, self.objects.currentData(), self.mode.currentData(),
                                   self.radius.currentData(), self.scope.currentData(), target, binder,
                                   **self.layers.arguments())

    def copy_command(self):
        QtWidgets.QApplication.clipboard().setText(self.command())
        self.quick_view_status.setText("Command copied.")

    def apply_preset(self):
        source = self.objects.currentData()
        if source not in cmd.get_names("objects"):
            self.refresh_objects()
            return
        self.request += 1
        key = self.preset.currentData()
        self.cavities.reset(key == "interface")
        self.jobs.start((self.request, source), [(source, self.command())], title=SHOW_PRESETS[key], exclusive=True)

    def _is_current(self, identity):
        return (identity == (self.request, self.objects.currentData())
                and identity[1] in cmd.get_names("objects"))

    def _prepare(self, owner, command):
        return prepare_review_command(command, cmd)

    def _draw(self, owner, source, action, draw, data, exclusive):
        followup = draw_review_view(cmd, source, action, draw, data, exclusive=exclusive)
        if isinstance(followup, MembraneNotice):
            return followup
        if not isinstance(followup, CandidateCavity):
            self.jobs.warnings.append(f"{self.preset.currentText()} applied to {owner}.")
            return
        try:
            compute, draw_cavity = prepare_candidate_cavity(cmd, followup)
        except Exception as error:
            self.jobs.warnings.append(f"{followup.title} skipped · {error}")
            return

        def show_cavity(result):
            rendered = draw_review_view(cmd, source, draw_candidate_cavity, draw_cavity, result)
            self.cavities.show_result(rendered)
            return "", rendered.tooltip

        self.jobs.start_cavity(followup.title, compute, show_cavity)

def show_presets(tab=None, source=None, palette=None):
    from pymol.gui import get_qtwindow

    if not isinstance(QtWidgets.QApplication.instance(), QtWidgets.QApplication):
        raise RuntimeError("Open the PyMOL Qt window, or pass a preset and object to pp_view.")
    global _dialog
    if _dialog is None:
        _dialog = ShowPresetsDialog(get_qtwindow())
        _dialog.finished.connect(_release_dialog)
    _dialog.refresh_objects()
    if source is not None:
        index = _dialog.objects.findData(source)
        if index < 0:
            raise ValueError(f"The loaded object {source} is unavailable in Structure views.")
        _dialog.objects.setCurrentIndex(index)
    if tab is not None:
        _dialog.tabs.setCurrentIndex({"structure": 0, "color": 1, "lighting": 2}[tab])
    if palette is not None:
        control = _dialog.color_page.preset
        control.setCurrentIndex(control.findData(palette))
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog


def _release_dialog(_result):
    global _dialog
    _dialog = None
