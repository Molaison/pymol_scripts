"""Independent color and lighting pages for Structure views."""

from pymol.Qt import QtCore, QtWidgets

import traceback

from pymolplugins.residues import presentation
from pymolplugins.residues.panel import COLOR_CHOICES as RESIDUE_COLORS, ResidueColorControls
from . import registry
from .presets import COLOR_CHOICES, LIGHTING_CHOICES, MACARON_VARIANTS


class AppearancePage(QtWidgets.QWidget):
    def __init__(self, kind, source, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.source = source
        self._owned_snapshots = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 6)
        form = QtWidgets.QFormLayout()
        self.preset = QtWidgets.QComboBox()
        choices = COLOR_CHOICES + RESIDUE_COLORS if kind == "color" else LIGHTING_CHOICES
        for label, name in choices:
            self.preset.addItem(label, name)
        form.addRow("Palette" if kind == "color" else "Scene lighting", self.preset)
        self.variant = QtWidgets.QComboBox()
        for name in MACARON_VARIANTS:
            self.variant.addItem(name.title(), name)
        form.addRow("Macaron tone", self.variant)
        self.variant_label = form.labelForField(self.variant)
        layout.addLayout(form)
        self.residues = ResidueColorControls(source, self) if kind == "color" else None
        if self.residues:
            layout.addWidget(self.residues)
        self.status = QtWidgets.QLabel()
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        if self.residues:
            self.residues.message.connect(self.status.setText)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self._preset_changed()
        buttons = QtWidgets.QHBoxLayout()
        self.copy = QtWidgets.QPushButton("Copy command")
        self.copy.clicked.connect(self._copy)
        self.apply_button = QtWidgets.QPushButton("Apply scene lighting" if kind == "lighting" else "Apply object color")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.clicked.connect(self._apply)
        undo = QtWidgets.QPushButton("Undo last style")
        undo.setObjectName("appearance_undo")
        self.apply_button.setObjectName("appearance_apply")
        undo.clicked.connect(self._undo)
        buttons.addWidget(self.copy)
        buttons.addWidget(undo)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def _preset_changed(self):
        self.status.clear()
        show_variant = self.preset.currentData() == "macaron_palette"
        self.variant.setVisible(show_variant)
        self.variant_label.setVisible(show_variant)
        if self.residues:
            palette = self.preset.currentData()
            mode = palette[len("residue_"):] if palette.startswith("residue_") else ""
            enabled = mode in presentation.MODES
            self.residues.setVisible(enabled)
            if enabled:
                self.residues.set_mode(mode)
            else:
                self.residues.stop_preview()

    def refresh(self):
        ready = bool(self.source())
        self.copy.setEnabled(ready)
        self.apply_button.setEnabled(ready)
        if self.residues:
            self.residues.refresh()

    def _uses_residues(self):
        return self.kind == "color" and self.preset.currentData().startswith("residue_")

    def _arguments(self):
        preset = self.preset.currentData()
        selection = "all" if self.kind == "lighting" else "%" + self.source()
        variant = self.variant.currentData() if preset == "macaron_palette" else ""
        return preset, selection, variant

    def _copy(self):
        if self._uses_residues():
            QtWidgets.QApplication.clipboard().setText(self.residues.command())
            self.status.setText("Command copied.")
            return
        preset, selection, variant = self._arguments()
        command = f"pp_style_apply {preset}, {selection}"
        if variant:
            command += f", variant={variant}"
        QtWidgets.QApplication.clipboard().setText(command)
        self.status.setText("Command copied.")

    def _apply(self):
        try:
            if self._uses_residues():
                snapshot = self.residues.apply()
                if snapshot is None:
                    self.status.setText(self.residues.status.text())
                    return
                self._owned_snapshots.append(int(snapshot))
            else:
                if self.residues:
                    self.residues.stop_preview()
                preset, selection, variant = self._arguments()
                snapshot = registry.pp_style_apply(preset, selection, variant=variant)
                if snapshot is not None:
                    self._owned_snapshots.append(int(snapshot))
        except Exception as error:
            self.status.setText(str(error))
            traceback.print_exc()
            return
        target = "the whole scene" if self.kind == "lighting" else self.source()
        self.status.setText(f"{self.preset.currentText()} applied to {target}.")

    def _undo(self):
        active = {snapshot.snapshot_id for snapshot in registry._REGISTRY.snapshots}
        self._owned_snapshots = [value for value in self._owned_snapshots if value in active]
        if not self._owned_snapshots:
            self.status.setText("No style from this page to undo.")
            return
        try:
            value = self._owned_snapshots[-1]
            if self.residues:
                self.residues.stop_preview()
            registry.pp_style_revert(value)
        except Exception as error:
            self.status.setText(str(error))
            traceback.print_exc()
            return
        self._owned_snapshots.pop()
        self.status.setText("Previous style restored.")
