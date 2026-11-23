"""Independent color and lighting pages for Structure views."""

from pymol.Qt import QtCore, QtWidgets

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
        for label, name in COLOR_CHOICES if kind == "color" else LIGHTING_CHOICES:
            self.preset.addItem(label, name)
        form.addRow("Palette" if kind == "color" else "Scene lighting", self.preset)
        self.variant = QtWidgets.QComboBox()
        for name in MACARON_VARIANTS:
            self.variant.addItem(name.title(), name)
        form.addRow("Macaron tone", self.variant)
        self.variant_label = form.labelForField(self.variant)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self._preset_changed()
        layout.addLayout(form)

        self.status = QtWidgets.QLabel()
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        self.copy = QtWidgets.QPushButton("Copy command")
        self.copy.clicked.connect(self._copy)
        self.apply_button = QtWidgets.QPushButton("Apply scene lighting" if kind == "lighting" else "Apply object color")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.clicked.connect(self._apply)
        undo = QtWidgets.QPushButton("Undo last style")
        undo.clicked.connect(self._undo)
        buttons.addWidget(self.copy)
        buttons.addWidget(undo)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def _preset_changed(self):
        show_variant = self.preset.currentData() == "macaron_palette"
        self.variant.setVisible(show_variant)
        self.variant_label.setVisible(show_variant)

    def refresh(self):
        ready = bool(self.source())
        self.copy.setEnabled(ready)
        self.apply_button.setEnabled(ready)

    def _arguments(self):
        preset = self.preset.currentData()
        selection = "all" if self.kind == "lighting" else "%" + self.source()
        variant = self.variant.currentData() if preset == "macaron_palette" else ""
        return preset, selection, variant

    def _copy(self):
        preset, selection, variant = self._arguments()
        command = f"pp_style_apply {preset}, {selection}"
        if variant:
            command += f", variant={variant}"
        QtWidgets.QApplication.clipboard().setText(command)
        self.status.setText("Command copied.")

    def _apply(self):
        try:
            preset, selection, variant = self._arguments()
            snapshot = registry.pp_style_apply(preset, selection, variant=variant)
        except Exception as error:
            self.status.setText(str(error))
            return
        if snapshot is not None:
            self._owned_snapshots.append(int(snapshot))
        target = "the whole scene" if self.kind == "lighting" else self.source()
        self.status.setText(f"{self.preset.currentText()} applied to {target}.")

    def _undo(self):
        active = {snapshot.snapshot_id for snapshot in registry._REGISTRY.snapshots}
        self._owned_snapshots = [key for key in self._owned_snapshots if key in active]
        if not self._owned_snapshots:
            self.status.setText("No style from this page to undo.")
            return
        try:
            snapshot = registry.pp_style_revert(self._owned_snapshots[-1])
        except Exception as error:
            self.status.setText(str(error))
            return
        if snapshot is not None:
            self._owned_snapshots.pop()
        self.status.setText("Previous style restored." if snapshot else "No style to undo.")
