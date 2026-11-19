"""Shared visibility controls for the two pocket-cavity layers."""

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets


class CavityControls(QtWidgets.QWidget):
    def __init__(self, parent=None, *, on_toggle=None):
        super().__init__(parent)
        self.checks, self.volumes, self.objects = {}, {}, {}
        self.on_toggle = on_toggle
        toggle = on_toggle or self._set_layer
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setVerticalSpacing(2)
        layout.setColumnStretch(3, 1)
        layers = (
            ("recesses", "Cyan · Water-accessible recess", "#0385cc"),
            ("enclosed", "Violet · Enclosed void", "#a84dd1"),
        )
        for row, (kind, caption, color) in enumerate(layers):
            dot = QtWidgets.QLabel("●")
            dot.setStyleSheet(f"color: {color};")
            check = QtWidgets.QCheckBox(caption)
            check.setChecked(True)
            check.toggled.connect(lambda enabled, layer=kind: toggle(layer, enabled))
            volume = QtWidgets.QLabel("—")
            volume.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            for column, widget in enumerate((dot, check, volume)):
                layout.addWidget(widget, row, column)
            self.checks[kind], self.volumes[kind] = check, volume
        self.hide()

    def reset(self, visible):
        self.objects = {}
        for volume in self.volumes.values():
            volume.setText("—")
        self.setToolTip("")
        self.setVisible(visible)

    def show_result(self, rendered):
        self.objects = rendered.objects
        self.setToolTip(rendered.tooltip)
        for kind, check in self.checks.items():
            self.volumes[kind].setText(f"{rendered.volumes[kind]:.1f} Å³")
            if self.on_toggle is None:
                self._set_layer(kind, check.isChecked())

    def _set_layer(self, kind, visible):
        name = self.objects.get(kind)
        if name and name in cmd.get_names("objects"):
            (cmd.enable if visible else cmd.disable)(name)
