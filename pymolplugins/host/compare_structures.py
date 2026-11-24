"""Modeless overlays and alignment of objects already loaded in PyMOL."""

import re
from time import perf_counter
from uuid import uuid4

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets

from pymolplugins.annotation.compare_scene import CompareScene
from pymolplugins.annotation.comparison import (
    COLORS, METHODS, ComparisonAlignment, Overlay, _style_overlay, bake_comparison_pose,
)
from pymolplugins.annotation.panel_style import apply_style
from pymolplugins.annotation.review_view_presets import loaded_molecules
from pymolplugins.annotation.reference_track import molecule_identity
from pymolplugins.core.pymol_display import display_updates


_dialog = None
_COMPARISON_SLICE_SECONDS = 0.095
_COMPARISON_YIELD_MS = 1
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
        self.setWindowTitle("PymolPlugins · Default mode")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(610, 485)
        self.overlays = {}
        self.original_visibility = {}
        self.reference_source = ""
        self.alignment = None
        self.rmsds = {}
        self.failures = {}
        self._pending_comparison = None
        self._comparison_identities = {}
        self._comparison_reference = ""
        self._comparison_was_active = False
        self._comparison_refreshes_reference = False
        self._comparison_progress = 0
        self._comparison_timer = QtCore.QTimer(self)
        self._comparison_timer.setSingleShot(True)
        self._comparison_timer.timeout.connect(self._advance_comparison)
        self.scene = CompareScene(self, self._scene_changed, self.clear)
        self._build()
        self.finished.connect(lambda _result: self.clear())
        self.refresh_objects()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Loaded objects"))
        row.addStretch(1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(lambda: self.refresh_objects())
        self.refresh_button = refresh
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
        scope = QtWidgets.QLabel("Overlay or Align starts Compare mode. Newly loaded objects join the comparison.")
        scope.setProperty("role", "muted")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.status = QtWidgets.QLabel("Check at least two objects.")
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.pending_settings = QtWidgets.QLabel("Settings changed · Click Overlay or Align to apply.")
        self.pending_settings.setProperty("role", "muted")
        self.pending_settings.setWordWrap(True)
        self.pending_settings.hide()
        layout.addWidget(self.pending_settings)
        actions = QtWidgets.QHBoxLayout()
        self.clear_button = QtWidgets.QPushButton("Return to Default mode")
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
        self.method.currentIndexChanged.connect(self._update_pending_settings)
        for scope in (self.mobile_range, self.reference_range):
            scope.atoms.currentIndexChanged.connect(self._update_pending_settings)
            scope.chain.currentIndexChanged.connect(self._update_pending_settings)
            scope.residues.textChanged.connect(self._update_pending_settings)
        apply_style(self, "compareStructures")

    def selected(self):
        return [self.objects.item(i).text() for i in range(self.objects.count())
                if self.objects.item(i).checkState() == QtCore.Qt.Checked]

    def refresh_objects(self, *, new_overlays=()):
        known = {self.objects.item(i).text() for i in range(self.objects.count())}
        previous = set(self.selected()) | (set(new_overlays) - known)
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
        ready = len(names) >= 2 and self._pending_comparison is None
        self.overlay_button.setEnabled(ready)
        self.align_button.setEnabled(ready)
        self.clear_button.setEnabled(self.scene.active)

    def _update_ranges(self):
        reference = self.reference.currentText()
        existing = set(cmd.get_names("objects"))
        mobiles = [set(cmd.get_chains(f"%{name}")) for name in self.selected()
                   if name != reference and name in existing]
        self.mobile_range.set_chains(set.intersection(*mobiles) if mobiles else ())
        self.reference_range.set_chains(cmd.get_chains(f"%{reference}") if reference in existing else ())
        self._update_pending_settings()

    def _update_pending_settings(self):
        changed = False
        if self.overlays and self._pending_comparison is None:
            changed = set(self.selected()) != set(self.overlays) or self.reference.currentText() != self.reference_source
            if not changed and self.alignment:
                try:
                    changed = (self.method.currentData() != self.alignment.method
                               or self.mobile_range.selection() != self.alignment.mobile_filter
                               or self.reference_range.selection() != self.alignment.target_filter)
                except ValueError:
                    changed = True
        self.pending_settings.setVisible(changed)

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
            mobile = self.mobile_range.selection() if align else ""
            target = self.reference_range.selection() if align else ""
        except Exception as error:
            self.status.setText(f"Comparison skipped · {error}")
            return
        self._start_comparison(sources, self.reference.currentText(), self.method.currentData(),
                               mobile, target, align)

    def _start_comparison(self, sources, reference, method, mobile, target, align, *, refresh_reference=False):
        self._cancel_comparison()
        try:
            identities = {name: (molecule_identity(cmd, name), cmd.get_object_state(name)) for name in sources}
        except Exception as error:
            self.status.setText(f"Comparison skipped · {error}")
            return
        self._comparison_was_active = self.scene.active
        self._comparison_refreshes_reference = refresh_reference
        self.scene.start()
        # The bulk transaction owns these objects until it commits. The watcher
        # resumes from its old snapshot so new loads are handled exactly once.
        self.scene.timer.stop()
        self.scene._scan = None
        self._comparison_identities = identities
        self._comparison_reference = reference
        self._comparison_progress = 0
        self._pending_comparison = _stage_comparison_steps(sources, reference, method, mobile, target, align)
        self._comparison_controls(False)
        self._update_pending_settings()
        self._show_comparison_progress()
        self._comparison_timer.start(_COMPARISON_YIELD_MS)

    def _comparison_controls(self, enabled):
        for control in (self.objects, self.reference, self.method, self.mobile_range,
                        self.reference_range, self.refresh_button):
            control.setEnabled(enabled)
        ready = enabled and len(self.selected()) >= 2
        self.overlay_button.setEnabled(ready)
        self.align_button.setEnabled(ready)
        self.clear_button.setEnabled(self.scene.active)

    def _cancel_comparison(self):
        if self._pending_comparison is None:
            return
        self._comparison_timer.stop()
        with display_updates(cmd):
            self._pending_comparison.close()
        self._pending_comparison = None
        self._comparison_identities.clear()
        if self._comparison_was_active:
            self.scene.timer.start()
        else:
            self.scene.stop()
        self._comparison_controls(True)
        self._update_pending_settings()

    def _stage_comparison_slice(self, total):
        deadline = perf_counter() + _COMPARISON_SLICE_SECONDS
        while self._comparison_progress < total:
            started = perf_counter()
            next(self._pending_comparison)
            self._comparison_progress += 1
            now = perf_counter()
            # Another native alignment cannot be interrupted. Allow room for
            # a similar step before beginning another object in this turn.
            if now + (now - started) >= deadline:
                break

    def _advance_comparison(self):
        if self._pending_comparison is None:
            return
        staged = None
        try:
            current = set(cmd.get_names("objects"))
            for name, identity in self._comparison_identities.items():
                if name not in current or (molecule_identity(cmd, name), cmd.get_object_state(name)) != identity:
                    raise ValueError(f"{name} changed during preparation. Apply the comparison again.")
            with display_updates(cmd):
                try:
                    total = len(self._comparison_identities)
                    if self._comparison_progress < total:
                        self._stage_comparison_slice(total)
                    else:
                        # Publish the finished overlays after yielding once more.
                        next(self._pending_comparison)
                except StopIteration as finished:
                    staged, alignment, notes = finished.value
                    self._pending_comparison = None
                    self._comparison_identities.clear()
                    self._clear_overlays()
                    self.reference_source = self._comparison_reference
                    self.alignment, self.rmsds = alignment, notes
                    self._show_overlays(staged)
                    if not self._comparison_refreshes_reference:
                        cmd.zoom(" or ".join(f"%{overlay.name}" for overlay in staged.values()), 4, animate=0)
        except Exception as error:
            if staged is None:
                self._cancel_comparison()
            else:
                with display_updates(cmd):
                    for overlay in staged.values():
                        cmd.delete(overlay.name)
                self.clear()
                self._comparison_controls(True)
            self.status.setText(f"Comparison skipped · {error}")
            return
        if self._pending_comparison is not None:
            self._show_comparison_progress()
            self._comparison_timer.start(_COMPARISON_YIELD_MS)
            return
        self.scene.timer.start()
        self._comparison_controls(True)
        self.setWindowTitle("PymolPlugins · Compare mode")
        self._update_status()

    def _show_comparison_progress(self):
        action = "Updating reference" if self._comparison_refreshes_reference else "Preparing comparison"
        self.status.setText(f"{action} · {self._comparison_progress}/{len(self._comparison_identities)}")

    def _show_overlays(self, overlays):
        if not overlays:
            return
        visible = cmd.get_vis()
        for source, overlay in overlays.items():
            self.original_visibility[source] = bool(visible[source][0]), molecule_identity(cmd, source)
            self.overlays[source] = overlay
            cmd.disable(source)
            cmd.enable(overlay.name)

    def _scene_changed(self, added, removed):
        self._cancel_comparison()
        lost = [source for source, overlay in self.overlays.items()
                if source in removed or overlay.name in removed]
        added = [name for name in added if name not in self.overlays or name in lost]
        if self.reference_source in lost:
            current = set(loaded_molecules(cmd))
            if self.reference_source in current:
                sources = [name for name in dict.fromkeys([*self.overlays, *added]) if name in current]
                alignment = self.alignment
                self.refresh_objects(new_overlays=added)
                self._start_comparison(sources, self.reference_source,
                                       alignment.method if alignment else "",
                                       alignment.mobile_filter if alignment else "",
                                       alignment.target_filter if alignment else "",
                                       alignment is not None, refresh_reference=True)
                return
            self.clear()
            self.refresh_objects()
            self.status.setText("Reference removed · Returned to Default mode")
            return
        forgotten = self.failures.keys() & removed
        if not added and not lost and not forgotten:
            return
        for source in forgotten:
            self.failures.pop(source)
        with display_updates(cmd):
            for source in lost:
                self._remove_overlay(source)
            self._add_overlays(added)
        self.refresh_objects(new_overlays=added)
        self._update_status()

    def _add_overlays(self, sources):
        new = {}
        used = {overlay.color for overlay in self.overlays.values()}
        for source in sources:
            color = next((color for color in COLORS if color not in used),
                         COLORS[(len(self.overlays) + len(new)) % len(COLORS)])
            overlay = None
            try:
                overlay = _stage_overlay(source, color, bool(self.alignment))
                if self.alignment:
                    self.rmsds[source] = self.alignment.apply(overlay)
                    cmd.disable(overlay.name)
            except Exception as error:
                if overlay is not None:
                    cmd.delete(overlay.name)
                self.failures[source] = str(error)
            else:
                new[source] = overlay
                used.add(color)
                self.failures.pop(source, None)
        self._show_overlays(new)

    def _update_status(self):
        if self.alignment:
            worst = max(self.rmsds.values(), default=0)
            message = f"Aligned {len(self.rmsds)} to {self.reference_source} · max RMSD {worst:.3f} Å"
        else:
            message = f"Showing {len(self.overlays)} structures in their current frames."
        if self.failures:
            message += f" · {len(self.failures)} skipped"
        self.status.setText(message)
        details = [f"{source} · {rmsd:.3f} Å RMSD" for source, rmsd in self.rmsds.items()]
        details.extend(f"{source} · {error}" for source, error in self.failures.items())
        self.status.setToolTip("\n".join(details))
        self._decorate()
        self._update_pending_settings()
        self.clear_button.setEnabled(True)

    def _remove_overlay(self, source):
        cmd.delete(self.overlays.pop(source).name)
        enabled, identity = self.original_visibility.pop(source)
        if source in cmd.get_names("objects") and molecule_identity(cmd, source) == identity:
            (cmd.enable if enabled else cmd.disable)(source)
        self.rmsds.pop(source, None)

    def _clear_overlays(self):
        for source in tuple(self.overlays):
            self._remove_overlay(source)
        self.failures.clear()

    def clear(self):
        self._cancel_comparison()
        self.scene.stop()
        with display_updates(cmd):
            self._clear_overlays()
        self.alignment, self.reference_source = None, ""
        self.setWindowTitle("PymolPlugins · Default mode")
        self.status.setText("Check at least two objects.")
        self.status.setToolTip("")
        self._decorate()
        self._update_pending_settings()
        self.clear_button.setEnabled(False)


def _stage_overlay(source, color, align):
    name = f"sa_compare_{uuid4().hex[:12]}"
    try:
        cmd.create(name, f"%{source}", source_state=cmd.get_object_state(source), target_state=1, zoom=0)
        if not cmd.count_atoms(f"%{name}"):
            raise ValueError(f"{source} has no atoms in the displayed state.")
        cmd.disable(name)
        if align:
            bake_comparison_pose(name)
        _style_overlay(name, color)
    except Exception:
        cmd.delete(name)
        raise
    return Overlay(source, color, name)


def _stage_comparison_steps(sources, reference, method, mobile_filter, target_filter, align):
    staged, notes = {}, {}
    alignment = None
    completed = False
    ordered = [reference] + [source for source in sources if source != reference]
    colors = {source: COLORS[index % len(COLORS)] for index, source in enumerate(sources)}
    try:
        for source in ordered:
            overlay = staged[source] = _stage_overlay(source, colors[source], align)
            if align:
                if source == reference:
                    alignment = ComparisonAlignment(overlay.name, method, mobile_filter, target_filter)
                else:
                    try:
                        notes[source] = alignment.apply(overlay)
                    except Exception as error:
                        raise ValueError(f"{source} · {error}") from error
                    cmd.disable(overlay.name)
            yield
        completed = True
        return {source: staged[source] for source in sources}, alignment, notes
    finally:
        if not completed:
            for overlay in staged.values():
                cmd.delete(overlay.name)


def _stage_comparison(sources, reference, method, mobile_filter, target_filter, align):
    steps = _stage_comparison_steps(sources, reference, method, mobile_filter, target_filter, align)
    while True:
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value


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
