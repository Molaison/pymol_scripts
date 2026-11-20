"""Modeless overlays and alignment of objects already loaded in PyMOL."""

import re
from uuid import uuid4

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets

from pymolplugins.annotation.comparison import COLORS, METHODS, Overlay, _align_copy, _style_overlay
from pymolplugins.annotation.panel_style import apply_style
from pymolplugins.annotation.review_view_presets import loaded_molecules


_dialog = None
ATOM_SETS = (
    ("Protein Cα", "polymer.protein and name CA"),
    ("Ligand heavy atoms", "organic and not elem H"),
    ("All heavy atoms", "not elem H"),
)


class AlignmentRange(QtWidgets.QWidget):
    def __init__(self, label):
        super().__init__()
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.atoms = QtWidgets.QComboBox()
        self.atoms.setAccessibleName(f"{label} atoms")
        for title, selection in ATOM_SETS:
            self.atoms.addItem(title, selection)
        self.chain = QtWidgets.QComboBox()
        self.chain.setAccessibleName(f"{label} chain")
        self.residues = QtWidgets.QLineEdit()
        self.residues.setAccessibleName(f"{label} residues")
        self.residues.setPlaceholderText("All residues or e.g. 40-80+105")
        form.addRow("Atoms", self.atoms)
        form.addRow("Chain", self.chain)
        form.addRow("Residues", self.residues)

    def set_chains(self, chains):
        previous = self.chain.currentData()
        self.chain.clear()
        self.chain.addItem("All chains", None)
        for chain in sorted(chains):
            self.chain.addItem(chain or "(blank)", chain)
        self.chain.setCurrentIndex(max(0, self.chain.findData(previous)))

    def selection(self):
        parts = [self.atoms.currentData()]
        chain = self.chain.currentData()
        if chain is not None:
            if any(char in chain for char in '"\\\r\n'):
                raise ValueError(f"Unsupported chain ID: {chain!r}")
            parts.append(f'chain "{chain}"')
        residues = self.residues.text().strip()
        if residues:
            if re.fullmatch(r"[0-9A-Za-z+\-]+", residues) is None:
                raise ValueError("Enter a residue range such as 40-80+105.")
            parts.append(f"resi {residues}")
        return " and ".join(parts)


class CompareStructuresDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PymolPlugins · Align loaded objects")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(610, 485)
        self.overlays = {}
        self.original_visibility = {}
        self._build()
        self.finished.connect(lambda _result: self.clear())
        self.refresh_objects()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Loaded objects"))
        row.addStretch(1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_objects)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.objects = QtWidgets.QListWidget()
        self.objects.setAccessibleName("Objects to compare")
        self.objects.setMinimumHeight(95)
        layout.addWidget(self.objects, 1)
        form = QtWidgets.QFormLayout()
        self.reference = QtWidgets.QComboBox()
        self.reference.setAccessibleName("Reference object")
        self.method = QtWidgets.QComboBox()
        self.method.setAccessibleName("Alignment method")
        for title, value in METHODS:
            self.method.addItem(title, value)
        form.addRow("Reference", self.reference)
        form.addRow("Method", self.method)
        layout.addLayout(form)
        sides = QtWidgets.QHBoxLayout()
        self.mobile_range = AlignmentRange("Mobile")
        self.reference_range = AlignmentRange("Reference")
        for title, controls in (("Mobiles", self.mobile_range), ("Reference", self.reference_range)):
            section = QtWidgets.QGroupBox(title)
            inner = QtWidgets.QVBoxLayout(section)
            inner.addWidget(controls)
            sides.addWidget(section, 1)
        layout.addLayout(sides)
        scope = QtWidgets.QLabel("Comparison is temporary. Closing this window clears the overlays.")
        scope.setProperty("role", "muted")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.status = QtWidgets.QLabel("Check at least two objects.")
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.clear_button = QtWidgets.QPushButton("Clear comparison")
        self.overlay_button = QtWidgets.QPushButton("Overlay")
        self.align_button = QtWidgets.QPushButton("Align")
        self.align_button.setProperty("role", "primary")
        self.clear_button.clicked.connect(self.clear)
        self.overlay_button.clicked.connect(lambda: self.apply(False))
        self.align_button.clicked.connect(lambda: self.apply(True))
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.overlay_button)
        actions.addWidget(self.align_button)
        layout.addLayout(actions)
        self.objects.itemChanged.connect(self._selection_changed)
        self.reference.currentIndexChanged.connect(self._update_ranges)
        apply_style(self, "compareStructures")

    def selected(self):
        return [self.objects.item(i).text() for i in range(self.objects.count())
                if self.objects.item(i).checkState() == QtCore.Qt.Checked]

    def refresh_objects(self):
        previous = set(self.selected())
        self.objects.blockSignals(True)
        self.objects.clear()
        for name in loaded_molecules(cmd):
            if name.startswith("sa_compare_"):
                continue
            item = QtWidgets.QListWidgetItem(name, self.objects)
            item.setCheckState(QtCore.Qt.Checked if name in previous else QtCore.Qt.Unchecked)
        self.objects.blockSignals(False)
        self._selection_changed()
        if not self.objects.count():
            self.status.setText("Load molecular objects in PyMOL, then click Refresh.")

    def _selection_changed(self):
        names = self.selected()
        previous = self.reference.currentText()
        self.reference.blockSignals(True)
        self.reference.clear()
        self.reference.addItems(names)
        self.reference.setCurrentIndex(max(0, self.reference.findText(previous)))
        self.reference.blockSignals(False)
        self._update_ranges()
        self.overlay_button.setEnabled(len(names) >= 2)
        self.align_button.setEnabled(len(names) >= 2)
        self.clear_button.setEnabled(bool(self.overlays))

    def _update_ranges(self):
        reference = self.reference.currentText()
        existing = set(cmd.get_names("objects"))
        mobiles = [set(cmd.get_chains(f"%{name}")) for name in self.selected()
                   if name != reference and name in existing]
        self.mobile_range.set_chains(set.intersection(*mobiles) if mobiles else ())
        self.reference_range.set_chains(cmd.get_chains(f"%{reference}") if reference in existing else ())

    def _decorate(self):
        self.objects.blockSignals(True)
        for i in range(self.objects.count()):
            row = self.objects.item(i)
            overlay = self.overlays.get(row.text())
            icon = QtGui.QIcon()
            if overlay is not None:
                swatch = QtGui.QPixmap(12, 12)
                swatch.fill(QtGui.QColor.fromRgbF(*cmd.get_color_tuple(overlay.color)))
                icon = QtGui.QIcon(swatch)
            row.setIcon(icon)
        self.objects.blockSignals(False)

    def apply(self, align):
        sources = self.selected()
        if len(sources) < 2:
            return
        try:
            staged, notes = _stage_comparison(sources, self.reference.currentText(), self.method.currentData(),
                                              self.mobile_range, self.reference_range, align)
        except Exception as error:
            self.status.setText(f"Comparison skipped · {error}")
            return
        self.clear()
        self.overlays = staged
        visible = cmd.get_vis()
        for source, overlay in staged.items():
            self.original_visibility[source] = bool(visible[source][0])
            cmd.disable(source)
            cmd.enable(overlay.name)
        cmd.zoom(" or ".join(f"%{overlay.name}" for overlay in staged.values()), 4, animate=0)
        if align:
            worst_rmsd = max(rmsd for _source, rmsd in notes)
            self.status.setText(
                f"Aligned {len(notes)} to {self.reference.currentText()} · max RMSD {worst_rmsd:.3f} Å"
            )
        else:
            self.status.setText(f"Showing {len(staged)} structures in their current frames.")
        self.status.setToolTip("\n".join(f"{source} · {rmsd:.3f} Å RMSD" for source, rmsd in notes))
        self._decorate()
        self.clear_button.setEnabled(True)

    def clear(self):
        for overlay in self.overlays.values():
            cmd.delete(overlay.name)
        names = set(cmd.get_names("objects"))
        for name, enabled in self.original_visibility.items():
            if name in names:
                (cmd.enable if enabled else cmd.disable)(name)
        self.overlays.clear()
        self.original_visibility.clear()
        self.status.setText("Check at least two objects.")
        self.status.setToolTip("")
        self._decorate()
        self.clear_button.setEnabled(False)


def _stage_comparison(sources, reference, method, mobile_range, reference_range, align):
    staged, notes = {}, []
    try:
        for index, source in enumerate(sources):
            name = f"sa_compare_{uuid4().hex[:12]}"
            overlay = staged[source] = Overlay(source, COLORS[index % len(COLORS)], name)
            cmd.create(name, f"%{source}", source_state=cmd.get_object_state(source), target_state=1, zoom=0)
            if not cmd.count_atoms(f"%{name}"):
                raise ValueError(f"{source} has no atoms in the displayed state.")
            cmd.disable(name)
            if align:
                # A moved object can carry a TTT or state matrix into the copy.
                # Fit in the displayed frame without applying that matrix twice.
                coords = cmd.get_coords(f"%{name}", state=1)
                cmd.matrix_reset(name, state=1, mode=1)
                cmd.matrix_reset(name, state=1, mode=2)
                cmd.load_coords(coords, f"%{name}", state=1)
            _style_overlay(name, overlay.color)
        if align:
            mobile_filter, target_filter = mobile_range.selection(), reference_range.selection()
            for source, overlay in staged.items():
                if source == reference:
                    continue
                try:
                    rmsd = _align_copy(overlay, staged[reference].name, method, mobile_filter, target_filter)
                except Exception as error:
                    raise ValueError(f"{source} · {error}") from error
                cmd.disable(overlay.name)
                notes.append((source, rmsd))
    except Exception:
        for overlay in staged.values():
            cmd.delete(overlay.name)
        raise
    return staged, notes


def show_compare():
    from pymol.gui import get_qtwindow

    if not isinstance(QtWidgets.QApplication.instance(), QtWidgets.QApplication):
        raise RuntimeError("Open the PyMOL Qt window, or pass mobile and reference to pp_align.")
    global _dialog
    if _dialog is None:
        _dialog = CompareStructuresDialog(get_qtwindow())
        _dialog.finished.connect(_release)
    _dialog.refresh_objects()
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog


def _release(_result):
    global _dialog
    _dialog = None
