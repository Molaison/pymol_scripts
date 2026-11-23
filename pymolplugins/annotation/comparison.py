"""Session-local overlays and alignment for the Review candidate table."""

from dataclasses import dataclass
from math import isfinite
from threading import Event
from uuid import uuid4

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets

from pymolplugins.core.pymol_display import display_updates
from .compare_scene import CompareScene
from .panel_style import ElidedLabel
from .reference_track import molecule_identity, preserve_alignment_selections


COLORS = ("marine", "salmon", "forest", "purple", "orange", "teal", "violet", "sand")
METHODS = (
    ("Sequence (align)", "align"),
    ("Structure (super)", "super"),
    ("Matching atom IDs (fit)", "fit"),
    ("Atom order (pair_fit)", "pair_fit"),
)


@dataclass
class Overlay:
    label: str
    color: str
    name: str = ""
    future: object = None
    cancel: object = None


@dataclass(frozen=True)
class ComparisonAlignment:
    reference: str
    method: str
    mobile_filter: str
    target_filter: str

    def apply(self, overlay):
        return _align_copy(overlay, self.reference, self.method, self.mobile_filter, self.target_filter)


class ComparisonBar(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.regular = QtWidgets.QWidget(self)
        regular = QtWidgets.QVBoxLayout(self.regular)
        regular.setContentsMargins(0, 0, 0, 0)
        regular.setSpacing(3)
        layout.setSpacing(3)
        buttons = QtWidgets.QHBoxLayout()
        self.items = QtWidgets.QComboBox()
        self.items.setMinimumContentsLength(7)
        self.items.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.items.setAccessibleName("Comparison structures")
        buttons.addWidget(self.items, 1)
        self.pin = QtWidgets.QPushButton("Pin current")
        self.remove = QtWidgets.QPushButton("Remove")
        self.frame = QtWidgets.QPushButton("Frame all")
        self.align = QtWidgets.QPushButton("Align…")
        self.clear = QtWidgets.QPushButton("Clear")
        self.done = QtWidgets.QPushButton("Default mode")
        self.done.setToolTip("Return to review tools")
        for button in (self.pin, self.remove, self.frame, self.align, self.clear, self.done):
            button.setProperty("role", "compact")
            buttons.addWidget(button)
        regular.addLayout(buttons)
        self.status = ElidedLabel()
        self.status.setProperty("role", "muted")
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        regular.addWidget(self.status)
        layout.addWidget(self.regular)
        self.pocket_row = QtWidgets.QWidget(self)
        pocket = QtWidgets.QHBoxLayout(self.pocket_row)
        pocket.setContentsMargins(0, 0, 0, 0)
        self.pocket_status = ElidedLabel()
        self.pocket_status.setProperty("role", "muted")
        self.pocket_counts = QtWidgets.QPushButton("Counts…")
        self.pocket_counts.setProperty("role", "compact")
        self.pocket_counts.setToolTip("Residue counts, interaction changes, and RMSD tolerances")
        self.pocket_done = QtWidgets.QPushButton("Default mode")
        self.pocket_done.setProperty("role", "compact")
        pocket.addWidget(self.pocket_status, 1)
        pocket.addWidget(self.pocket_counts)
        pocket.addWidget(self.pocket_done)
        layout.addWidget(self.pocket_row)
        self.set_pocket_mode(False)
        self.hide()

    def set_pocket_mode(self, active):
        self.pocket_mode = active
        self.regular.setVisible(not active)
        self.pocket_row.setVisible(active)

    def set_items(self, items, current):
        selected = self.items.currentData()
        self.items.blockSignals(True)
        self.items.clear()
        icons = {}
        for entry_id, item in items.items():
            suffix = " · loading…" if item.future else (" · current" if entry_id == current else "")
            swatch = QtGui.QPixmap(12, 12)
            swatch.fill(QtGui.QColor.fromRgbF(*cmd.get_color_tuple(item.color)))
            icons[entry_id] = QtGui.QIcon(swatch)
            self.items.addItem(icons[entry_id], item.label + suffix, entry_id)
        if selected in items:
            self.items.setCurrentIndex(self.items.findData(selected))
        self.items.blockSignals(False)
        return icons


def molecular_scope(name):
    if not name or name not in cmd.get_names("objects"):
        raise ValueError("The structure is no longer loaded.")
    objects = cmd.get_object_list(f"%{name}") if cmd.get_type(name) == "object:group" else [name]
    molecules = [f"%{obj}" for obj in objects if cmd.get_type(obj) == "object:molecule"]
    if not molecules:
        raise ValueError("Choose a molecular structure.")
    return "(" + " or ".join(molecules) + ")"


def _style_overlay(name, color):
    cmd.hide("everything", name)
    cmd.show("cartoon", f"%{name} and polymer")
    cmd.show("sticks", f"%{name} and not polymer and not solvent")
    cmd.color(color, name)
    cmd.set("cartoon_transparency", 0.45, name)


def bake_comparison_pose(name):
    """Preserve the displayed pose when removing inherited object matrices."""
    selection = f"%{name}"
    coords = cmd.get_coords(selection, state=1)
    cmd.matrix_reset(name, state=1, mode=1)
    cmd.matrix_reset(name, state=1, mode=2)
    cmd.load_coords(coords, selection, state=1)


def _align_copy(overlay, target, method, mobile_filter, target_filter):
    token = uuid4().hex[:12]
    trial = f"sa_compare_{token}"
    mobile_mask = f"sa_compare_mobile_{token}"
    target_mask = f"sa_compare_reference_{token}"
    target_copy = f"sa_compare_target_{token}"
    pairs = f"sa_compare_pairs_{token}"
    fitted = False
    try:
        cmd.create(trial, f"%{overlay.name}", source_state=1, target_state=1, zoom=0)
        bake_comparison_pose(trial)
        cmd.disable(trial)
        cmd.select(mobile_mask, mobile_filter, state=1)
        cmd.select(target_mask, f"{molecular_scope(target)} and ({target_filter})", state=1)
        cmd.create(target_copy, f"%{target_mask}", source_state=1, target_state=1, zoom=0)
        cmd.disable(target_copy)
        bake_comparison_pose(target_copy)
        mobile = f"%{trial} and %{mobile_mask}"
        reference = f"%{target_copy}"
        counts = (cmd.count_atoms(mobile), cmd.count_atoms(reference))
        if min(counts) < 3:
            raise ValueError("Each selection needs at least three atoms.")
        if method == "pair_fit":
            if counts[0] != counts[1]:
                raise ValueError("Atom order requires equal selection sizes.")
            rmsd = cmd.pair_fit(mobile, reference, quiet=1)
        elif method == "fit":
            rmsd = cmd.fit(mobile, reference, mobile_state=1, target_state=1,
                           matchmaker=0, cycles=0, object=pairs, quiet=1)
            if rmsd >= 0 and len(cmd.get_raw_alignment(pairs)) < 3:
                raise ValueError("Fewer than three atom identifiers matched.")
        else:
            with preserve_alignment_selections(cmd):
                rmsd, matched, *_ = getattr(cmd, method)(mobile, reference, mobile_state=1, target_state=1, quiet=1)
            if matched < 3:
                raise ValueError("Fewer than three atoms aligned.")
        if not isfinite(rmsd) or rmsd < 0:
            raise ValueError("PyMOL could not align these selections.")
        _style_overlay(trial, overlay.color)
        fitted = True
    finally:
        cmd.delete(mobile_mask)
        cmd.delete(target_mask)
        cmd.delete(target_copy)
        cmd.delete(pairs)
        if not fitted:
            cmd.delete(trial)
    # PyMOL can retain selection references after deleting an aligned object.
    # Keep fresh native names while the list retains the same candidate ID.
    cmd.delete(overlay.name)
    overlay.name = trial
    cmd.enable(trial)
    return rmsd


def _alignment_dialog(parent, overlays, references):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Align comparison")
    form = QtWidgets.QFormLayout(dialog)
    mobile = QtWidgets.QComboBox()
    mobile.addItem("All comparison copies", "")
    for entry_id, item in overlays.items():
        mobile.addItem(item.label, entry_id)
    target = QtWidgets.QComboBox()
    for label, object_name in references:
        target.addItem(label, object_name)
    method = QtWidgets.QComboBox()
    for label, value in METHODS:
        method.addItem(label, value)
    mobile_filter = QtWidgets.QLineEdit("polymer.protein and name CA")
    target_filter = QtWidgets.QLineEdit("polymer.protein and name CA")
    for label, widget in (("Move", mobile), ("Reference", target), ("Method", method),
                          ("Mobile selection", mobile_filter), ("Reference selection", target_filter)):
        form.addRow(label, widget)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Align")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    dialog.resize(470, dialog.sizeHint().height())
    if dialog.exec() == QtWidgets.QDialog.Accepted:
        return mobile.currentData(), target.currentData(), method.currentData(), mobile_filter.text(), target_filter.text()
    return None


class ReviewComparison:
    def __init__(self, panel, owner):
        from .pocket_compare import PocketComparison

        self.panel, self.owner = panel, owner
        self.bar = panel.compare_bar
        self.items = {}
        self.source = None
        self.alignment = None
        self.scene_sources = {}
        self.scene = CompareScene(panel, self._scene_changed, self.clear)
        self.pocket = PocketComparison(panel, owner, molecular_scope)
        panel.pocket_summary_action.triggered.connect(self._pocket_selected)
        panel.pocket_reference_action.triggered.connect(self._pocket_reference)
        panel.protein_interface_action.triggered.connect(lambda: self._pocket_selected(choose_proteins=True))
        self.timer = QtCore.QTimer(panel)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self._finish_loads)
        self.panel.compare_toggle.toggled.connect(self._compare_opened)
        self.bar.pin.clicked.connect(self._pin_selection)
        self.bar.remove.clicked.connect(lambda: self.remove(self.bar.items.currentData()))
        self.bar.clear.clicked.connect(self.clear)
        self.bar.frame.clicked.connect(self.frame)
        self.bar.align.clicked.connect(self.align)
        self.bar.items.currentIndexChanged.connect(self._actions)
        self._refresh()
        self._hint()

    def sync(self):
        state = self.owner.STATE
        source = (state.source_mode, state.csv_path, state.dataset_dir,
                  state.scene_parent, id(state.row_annotation_store))
        if self.source != source:
            if self.source is not None:
                self.clear()
            self.source = source
        if self.pocket.track.active:
            self.pocket.track.follow()
            return
        if self.pocket.request is not None and self.pocket.identity() != self.pocket.request.identity:
            self.close_pocket()
        if self.pocket.created:
            return
        if self.items:
            names = set(cmd.get_names("objects"))
            enabled = set(cmd.get_names("objects", enabled_only=1))
            current = state.current_entry_id()
            for entry_id, item in self.items.items():
                if item.name not in names:
                    continue
                if entry_id == current and item.name in enabled:
                    cmd.disable(item.name)
                elif entry_id != current and item.name not in enabled:
                    cmd.enable(item.name)
        self._refresh()

    def _compare_opened(self, active):
        if not active:
            track = self.pocket.track
            restore = track.active and track.dataset == track.dataset_key()
            self.clear()
            if restore and not self.panel._closed and self.owner.STATE.current_object:
                self.owner._load_current_structure()
        else:
            self.scene.start()
            if not self.bar.pocket_mode:
                self.add_entries(self.panel.review.targets())
            self._refresh()

    def _scene_changed(self, added, removed):
        track = self.pocket.track
        if track.active:
            if track.reference in removed:
                self.clear()
            elif track.source_objects & removed:
                track.identity = None
                track.follow()
            return
        state = self.owner.STATE
        added = [name for name in added if name != state.current_object and name not in state.managed_objects]
        if self.bar.pocket_mode:
            if (not self.pocket.request or self.pocket.request.identity[2] in removed
                    or self.pocket.request.captured_sources & removed):
                self.clear()
            else:
                self.pocket.add_scene_objects(added)
            return
        if self.alignment and self.alignment.reference in removed:
            self.clear()
            return
        lost = [key for key, (name, _, _) in self.scene_sources.items()
                if name in removed or self.items[key].name in removed]
        if not added and not lost:
            return
        results = []
        with display_updates(cmd):
            for key in lost:
                self.remove(key, refresh=False)
            for name in added:
                try:
                    rmsd = self._add_scene_object(name)
                    results.append(name if rmsd is None else f"{name} · {rmsd:.3f} Å RMSD")
                except Exception as error:
                    results.append(f"Could not add {name} · {error}")
        self._refresh()
        if results:
            self._status(results[-1], "\n".join(results))

    def _add_scene_object(self, name):
        key = f"scene:{name}"
        item = self._new_overlay(name)
        visible = name in cmd.get_names("objects", enabled_only=1)
        rmsd = self._load(item, selection=molecular_scope(name), state=cmd.get_object_state(name))
        self.items[key] = item
        self.scene_sources[key] = name, visible, molecule_identity(cmd, name)
        cmd.disable(name)
        return rmsd

    def _pocket_selected(self, *, choose_proteins=False):
        entries = tuple(self.panel._entries_by_id[key] for key in self.panel.review.targets())
        try:
            if not self.pocket.start(entries, choose_proteins=choose_proteins):
                return
        except Exception as error:
            self.close_pocket()
            self.panel._show_error(str(error))
            return
        self.bar.set_pocket_mode(True)
        self.panel.compare_toggle.setChecked(True)

    def _pocket_reference(self):
        self.pocket.clear()
        try:
            self.pocket.track.start()
        except Exception as error:
            self.close_pocket()
            self.panel._show_error(str(error))
            return
        self.bar.set_pocket_mode(True)
        self.panel.compare_toggle.setChecked(True)

    def close_pocket(self, *, keep_reference=False):
        if self.bar.pocket_mode and not (keep_reference and self.pocket.track.active):
            self.clear()

    def _pin_selection(self):
        self.add_entries(self.panel.review.targets() or (self.owner.STATE.current_entry_id(),))

    def add_entries(self, entry_ids):
        self.sync()
        state = self.owner.STATE
        entries = [self.panel._entries_by_id[key] for key in dict.fromkeys(entry_ids)
                   if key in self.panel._entry_id_to_active_index and key not in self.items]
        if not entries or not state.current_object:
            return
        self._status("")
        for entry in entries:
            self._add_entry(entry, state)
        if any(item.future is not None for item in self.items.values()) and not self.timer.isActive():
            self.timer.start()
        self.sync()
        self.bar.items.setCurrentIndex(self.bar.items.findData(entries[-1].id))

    def _add_entry(self, entry, state):
        entry_id = entry.id
        item = self._new_overlay(entry.display_name)
        self.items[entry_id] = item
        try:
            if entry_id == state.current_entry_id() or state.source_mode == "groups":
                source = state.current_object if entry_id == state.current_entry_id() else entry.source_path
                self._load(item, selection=molecular_scope(source))
            else:
                item.cancel = Event()
                item.future = self.owner._get_bg_executor().submit(
                    self.owner._materialize_entry, entry, self.owner._materialization_context(),
                    cancel_event=item.cancel,
                )
        except Exception as error:
            self._load_failed(entry_id, item, error)

    def _new_overlay(self, label):
        used = {item.color for item in self.items.values()}
        color = next((value for value in COLORS if value not in used), COLORS[len(self.items) % len(COLORS)])
        return Overlay(label, color)

    def _load(self, item, *, selection="", path="", state=1):
        name = f"sa_compare_{uuid4().hex[:12]}"
        item.name = name
        try:
            with display_updates(cmd):
                if selection:
                    cmd.create(name, selection, source_state=state, target_state=1 if state else 0, zoom=0)
                else:
                    cmd.load(self.owner._long_path(path), name, quiet=1, zoom=0)
                molecular_scope(name)
                _style_overlay(name, item.color)
                if self.alignment:
                    if state:
                        bake_comparison_pose(name)
                    return self.alignment.apply(item)
        except Exception:
            cmd.delete(item.name)
            item.name = ""
            raise

    def _load_failed(self, entry_id, item, error):
        self.items.pop(entry_id, None)
        self._refresh()
        self._status(f"Could not add {item.label} · {error}")

    def _finish_loads(self):
        if self.pocket.created:
            return
        completed = False
        for entry_id, item in list(self.items.items()):
            if item.future is None or not item.future.done():
                continue
            completed = True
            future, item.future = item.future, None
            try:
                self._load(item, path=future.result().path)
            except Exception as error:
                self._load_failed(entry_id, item, error)
        if not any(item.future is not None for item in self.items.values()):
            self.timer.stop()
        if completed:
            self.sync()

    def remove(self, entry_id, *, refresh=True):
        item = self.items.get(entry_id)
        if item is not None and self.alignment and self.alignment.reference == item.name:
            self.clear()
            return
        item = self.items.pop(entry_id, None)
        if item is not None:
            if item.future is not None:
                item.cancel.set()
                item.future.cancel()
            if item.name:
                cmd.delete(item.name)
        source = self.scene_sources.pop(entry_id, None)
        if (source is not None and source[0] in cmd.get_names("objects")
                and molecule_identity(cmd, source[0]) == source[2]):
            (cmd.enable if source[1] else cmd.disable)(source[0])
        if refresh:
            self._refresh()
            if not self.items:
                self._hint()

    def clear(self):
        if self.panel.compare_toggle.isChecked():
            self.panel.compare_toggle.setChecked(False)
            return
        self.scene.stop()
        self.timer.stop()
        self.alignment = None
        with display_updates(cmd):
            self.pocket.clear()
            self.bar.set_pocket_mode(False)
            for entry_id in tuple(self.items):
                self.remove(entry_id, refresh=False)
        self._refresh()
        self._hint()

    def _hint(self):
        self._status("Select rows, then use Add selected to compare them.")

    def _status(self, message, details=""):
        self.bar.status.setText(message)
        self.bar.status.setToolTip(details or message)

    def _refresh(self):
        current = self.owner.STATE.current_entry_id()
        self.panel._dataset_model.set_comparison_icons(self.bar.set_items(self.items, current))
        self.panel.compare_toggle.setText("Compare mode" if self.scene.active else "Compare")
        self._actions()

    def _actions(self):
        current = self.owner.STATE.current_entry_id()
        has_overlays = any(item.name for key, item in self.items.items() if key != current)
        self.update_selection(self.panel.review.targets() if self.panel.review is not None else ())
        self.bar.remove.setEnabled(self.bar.items.currentIndex() >= 0)
        self.bar.clear.setEnabled(bool(self.items))
        self.bar.align.setEnabled(has_overlays)
        self.bar.frame.setEnabled(has_overlays)

    def update_selection(self, targets):
        current = self.owner.STATE.current_entry_id()
        pocket_ready = len(targets) >= 2 and bool(self.owner.STATE.current_object)
        self.panel.pocket_summary_action.setEnabled(pocket_ready)
        self.panel.protein_interface_action.setEnabled(pocket_ready)
        is_group = bool(targets) and targets != (current,)
        pending = sum(entry_id not in self.items for entry_id in targets)
        text = f"Add selected ({pending})" if pending else "Add selected"
        self.bar.pin.setText(text if is_group else "Pin current")
        self.bar.pin.setEnabled(bool(pending) if is_group else bool(current) and current not in self.items)

    def frame(self):
        active = self.owner.STATE.current_object
        visible = set(cmd.get_names("objects", enabled_only=1))
        try:
            scopes = [molecular_scope(active)]
        except ValueError as error:
            self._status(str(error))
            return
        scopes.extend(f"%{item.name}" for item in self.items.values() if item.name in visible)
        cmd.zoom(" or ".join(scopes), 4, animate=0)

    def align(self):
        state = self.owner.STATE
        names = set(cmd.get_names("objects"))
        overlays = {key: item for key, item in self.items.items()
                    if key != state.current_entry_id() and item.name in names}
        if not overlays or state.current_object not in names:
            return
        references = [("Current candidate", state.current_object)]
        references.extend((item.label, item.name) for item in overlays.values())
        excluded = set(state.managed_objects) | {state.current_object} | {item.name for item in self.items.values()}
        references.extend(
            (f"Loaded: {name}", name)
            for name in sorted(names - excluded)
            if cmd.get_type(name) == "object:molecule" and not name.startswith("sa_")
        )
        preset = _alignment_dialog(self.panel, overlays, references)
        if preset is None:
            return
        mobile, target, method, mobile_filter, target_filter = preset
        targets = [item for key, item in overlays.items() if (not mobile or key == mobile) and item.name != target]
        if not targets:
            self._status("Choose different mobile and reference structures.")
            return
        results = []
        succeeded = 0
        alignment = ComparisonAlignment(target, method, mobile_filter, target_filter)
        with display_updates(cmd):
            for item in targets:
                try:
                    rmsd = alignment.apply(item)
                    results.append(f"{item.label} · {rmsd:.3f} Å RMSD")
                    succeeded += 1
                except Exception as error:
                    results.append(f"{item.label} · {error}")
        if succeeded:
            self.alignment = alignment
        message = results[0] if len(results) == 1 else f"Aligned {succeeded}/{len(targets)} structures"
        self._status(message, "\n".join(results))
