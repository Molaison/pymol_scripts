"""Reference track controls for the CSV Review toolbar."""

from pymol.Qt import QtWidgets

from .reference_track import parse_pdb_reference, validate_reference_track


class ReferenceTrackDialog(QtWidgets.QDialog):
    def __init__(self, parent, config, current, saved):
        super().__init__(parent)
        self.setWindowTitle("Reference track")
        self.resize(510, 0)
        self.config = config
        self.source = self._choice((
            ("PDB entry", "pdb"), ("CSV row", "row"), ("Structure file", "file"),
        ), config.get("source", "pdb"))
        self.row = QtWidgets.QComboBox()
        for entry in (current, saved):
            if entry is not None and self.row.findData(entry.id) < 0:
                self.row.addItem(entry.display_name, entry.id)
        self.row.setCurrentIndex(max(0, self.row.findData(config.get("value"))))
        self.pdb = QtWidgets.QLineEdit(placeholderText="6FZN and resn 9UY")
        self.file = QtWidgets.QLineEdit()
        self.browse = QtWidgets.QPushButton("Browse…")
        self.browse.clicked.connect(self._browse)
        self.reference_selection = QtWidgets.QLineEdit(placeholderText="Entire imported structure")
        if config.get("source") == "pdb":
            value, selection = config["value"], config["selection"]
            self.pdb.setText(f"{value} and {selection}" if selection else value)
        else:
            self.reference_selection.setText(config.get("selection", ""))
            if config.get("source") == "file":
                self.file.setText(config.get("value", ""))
        self.row_selection = QtWidgets.QLineEdit(config.get("row_selection", ""))
        self.row_selection.setPlaceholderText("Automatic selection")
        self.mode = self._choice((
            ("Auto", "auto"), ("Protein Cα", "protein"), ("Ligand heavy-atom names", "ligand"),
        ), config.get("mode", "auto"))
        self.direction = self._choice((
            ("Row → reference", "row_to_reference"), ("Reference → row", "reference_to_row"),
        ), config.get("direction", "row_to_reference"))
        file_row = QtWidgets.QWidget()
        file_layout = QtWidgets.QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(self.file)
        file_layout.addWidget(self.browse)
        self.form = form = QtWidgets.QFormLayout(self)
        for label, widget in (
            ("Source", self.source), ("PDB ID and selector", self.pdb),
            ("Reference row", self.row), ("Structure file", file_row),
            ("Reference selection", self.reference_selection), ("Current row selection", self.row_selection),
            ("Alignment", self.mode), ("Direction", self.direction),
        ):
            form.addRow(label, widget)
        self.source_widgets = {"pdb": self.pdb, "row": self.row, "file": file_row}
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Apply")
        clear = buttons.addButton("Clear reference", QtWidgets.QDialogButtonBox.ResetRole)
        clear.setEnabled(bool(config))
        clear.clicked.connect(self._clear)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.source.currentIndexChanged.connect(self._source_changed)
        self._source_changed()

    @staticmethod
    def _choice(items, value):
        combo = QtWidgets.QComboBox()
        for label, data in items:
            combo.addItem(label, data)
        combo.setCurrentIndex(combo.findData(value))
        return combo

    def _source_changed(self):
        source = self.source.currentData()
        for key, widget in self.source_widgets.items():
            self._show_field(widget, source == key)
        self._show_field(self.reference_selection, source != "pdb")

    def _show_field(self, widget, visible):
        widget.setVisible(visible)
        self.form.labelForField(widget).setVisible(visible)

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a reference structure", self.file.text(),
            "Structures (*.pdb *.cif *.mmcif *.sdf *.mol *.mol2 *.pdb.gz *.cif.gz);;All files (*)",
        )
        if path:
            self.file.setText(path)

    def _apply(self):
        source = self.source.currentData()
        try:
            selection = self.reference_selection.text()
            if source == "pdb":
                value, selection = parse_pdb_reference(self.pdb.text())
            elif source == "row":
                value = self.row.currentData() or ""
            else:
                value = self.file.text()
            self.config = validate_reference_track({
                "source": source, "value": value, "selection": selection,
                "row_selection": self.row_selection.text(), "direction": self.direction.currentData(),
                "mode": self.mode.currentData(),
            })
        except ValueError as error:
            QtWidgets.QMessageBox.warning(self, "Reference track", str(error))
            return
        self.accept()

    def _clear(self):
        self.config = {}
        self.accept()
