"""Follow a CSV row in the fixed frame of a tracked ligand pocket."""

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import blake2b
from typing import Optional

from pymol import cmd, util
from pymol.Qt import QtCore, QtWidgets

from pymolplugins.core.math3d import fit_rigid_transform
from pymolplugins.core.pymol_display import display_updates
from .compare_scene import _geometry_signature
from .pocket_analysis import ContactChange, _aligned_pose, _distance_sq, capture_snapshot
from .pocket_candidate_overlay import draw_pocket_interactions, load_fragment
from .pocket_details import _contact_tip, _endpoint, _table
from .pocket_variants import LIGAND, PROTEIN, aligned_fragment, bonds_by_site, residues
from .provenance import source_version_token
from .reference_track import ligand_anchor_pairs, ligand_atom_coordinates, molecule_identity


@dataclass(frozen=True)
class LigandInteraction:
    reference: Optional[ContactChange]
    current: Optional[ContactChange]

    @property
    def shown(self):
        return self.current or self.reference


@dataclass(frozen=True)
class _PocketAnalysisResult:
    rmsd: float
    matched: int
    pose: object
    contacts: tuple


def _world_signature(api, selection):
    geometry, bonds = _geometry_signature(api, selection, 1)
    digest = blake2b(digest_size=16)
    digest.update(repr(geometry).encode())
    digest.update(repr(bonds).encode())
    return digest.hexdigest()


def _selection_signature(api, selection, object_positions):
    atoms = []

    def record(model, index):
        atoms.append((object_positions[model], index))

    api.iterate_state(1, selection, "record(model, index)", space={"record": record})
    return tuple(atoms)


def _contacts_by_ligand_atom(snapshot):
    contacts = {}
    for (site, kind, ligand_atoms), pose in snapshot.contacts.items():
        key = kind, ligand_atoms
        if key not in contacts or _distance_sq(pose.points) < _distance_sq(contacts[key][1].points):
            contacts[key] = site, pose
    return contacts


def _endpoints(snapshot, contacts, baseline, *, current=False, rotation=None, offset=None):
    mapping, bonds = residues(snapshot.protein), bonds_by_site(snapshot.protein)
    fragments, result = {}, {}
    for key, (site, pose) in contacts.items():
        if site not in mapping:
            continue
        if site not in fragments:
            fragments[site] = aligned_fragment(snapshot.protein, mapping[site].atoms, rotation, offset, bonds[site])
        if rotation is not None:
            pose = _aligned_pose(pose, rotation, offset)
        change = "Retained" if key in baseline else ("Gained" if current else "Lost")
        result[key] = ContactChange(site, key[0], change, pose, fragments[site])
    return result


def compare_ligand_pockets(reference, current):
    """Fit named ligand atoms and compare their contact types across scaffolds."""
    import numpy as np

    row = [(atom, atom.coord) for atom in current.ligand.atom]
    ref = [(atom, atom.coord) for atom in reference.ligand.atom]
    pairs = ligand_anchor_pairs(row, ref)
    fit = fit_rigid_transform(*zip(*pairs))
    matrix = np.array(fit.matrix).reshape((4, 4))
    rotation, offset = matrix[:3, :3].T, matrix[:3, 3]
    ref_contacts = _contacts_by_ligand_atom(reference)
    row_contacts = _contacts_by_ligand_atom(current)
    first = _endpoints(reference, ref_contacts, row_contacts)
    second = _endpoints(current, row_contacts, ref_contacts, current=True, rotation=rotation, offset=offset)
    contacts = [LigandInteraction(first.get(key), second.get(key))
                for key in sorted(first.keys() | second.keys(), key=lambda key: (key[0] == "Heavy-atom contact", key))]
    pose = aligned_fragment(current.ligand, range(len(current.ligand.atom)), rotation, offset)
    return fit.rmsd_after, len(pairs), pose, contacts


def _reference_selectors(api, scope, selection, radius):
    selected = f"({scope}) and ({selection or 'all'})"
    protein, ligand = f"({scope}) and ({PROTEIN})", f"({selected}) and ({LIGAND})"
    selected_protein = f"({selected}) and ({PROTEIN})"
    if api.count_atoms(selected_protein):
        protein = f"(byres ({selected_protein})) and ({PROTEIN})"
        if not api.count_atoms(ligand):
            ligand = f"byres ((({scope}) and ({LIGAND})) within {radius:g} of ({protein}))"
    if not api.count_atoms(protein) or not api.count_atoms(ligand):
        raise ValueError("Choose a reference ligand with its protein in the source structure.")
    return protein, ligand


class PocketReferenceReview:
    def __init__(self, pocket):
        self.pocket, self.panel, self.bar = pocket, pocket.panel, pocket.bar
        self.reference = None
        self.dataset = None
        self.job = None
        self.snapshot = None
        self.identity = None
        self.view = None
        self.reference_protein = None
        self.baseline_signature = None
        self.objects, self.contacts, self.focus = [], [], []
        self.focus_detail = []
        self.focused_contact = None
        self.showing_all = False
        self.analysis_cache = OrderedDict()
        self.analysis_cache_limit = 8
        self.hidden, self.managed = set(), set()
        self.hidden_identities = {}
        self.source_objects = set()
        self.dialog = None
        self.timer = QtCore.QTimer(self.panel)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._advance)

    @property
    def active(self):
        return self.dataset is not None

    @property
    def state(self):
        return self.pocket.owner.STATE

    def dataset_key(self):
        return self.state.csv_path, id(self.state.row_annotation_store)

    def start(self):
        config = dict(self.state.reference_track)
        if not self.state.current_object or not config:
            raise ValueError("Configure Reference… and open a ligand-bearing CSV row.")
        self.label, resolve = self.panel._reference_source(config)
        self.config = config
        self.radius = float(self.panel._interface_radius)
        self.dataset = self.dataset_key()
        self.panel._view_jobs.cancel()
        self.panel.quick_view_status.setText("")
        self.panel.reference_track_status.setText("")
        self.panel._reference_track.suspend(cmd)
        self.job = self.pocket.owner._get_bg_executor().submit(resolve)
        self.bar.pocket_counts.setText("Interactions…")
        self.bar.pocket_counts.setToolTip("Inspect contact changes for the current row")
        self._status(f"Loading reference {self.label}…")
        self.timer.start()

    def save_view(self):
        if self.reference is not None:
            self.view = cmd.get_view()

    def follow(self):
        identity = self.pocket.identity()
        if identity == self.identity:
            return
        if not identity[2]:
            self.identity = identity
            with display_updates(cmd):
                self._clear_overlay()
                if self.reference is not None:
                    cmd.disable(self.reference)
            self._status("Reference pocket · No matching rows")
            self._fill_details()
            return
        if self.reference is not None:
            with display_updates(cmd):
                self._hide_scene()
                if self.view is not None:
                    cmd.set_view(self.view)
        if self.job is not None or self.reference is not None:
            self.timer.start()

    def _advance(self):
        if self.dataset != self.dataset_key():
            self.clear()
            return
        if self.job is not None and not self.job.done():
            return
        self.timer.stop()
        self.identity = self.pocket.identity()
        try:
            with display_updates(cmd):
                if self.job is not None:
                    job, self.job = self.job, None
                    self._load(job.result())
                self._clear_overlay()
                self._draw(self.identity[2])
        except Exception as error:
            with display_updates(cmd):
                self._clear_overlay()
            self._status(f"Reference pocket · {error}")
            self._fill_details()

    def _load(self, path):
        source = cmd.get_unused_name("sa_pocket_track_source")
        name = cmd.get_unused_name("sa_pocket_track_reference")
        try:
            cmd.load(self.pocket.owner._long_path(path), source, quiet=1, zoom=0)
            protein, ligand = _reference_selectors(cmd, f"%{source}", self.config["selection"], self.radius)
            snapshot = capture_snapshot(cmd, protein, ligand, self.radius, ligand_sites=True)
            ligand_atom_coordinates((atom, atom.coord) for atom in snapshot.ligand.atom)
            cmd.create(name, f"({protein}) or ({ligand})", 1, 1, zoom=0, quiet=1)
            cmd.hide("everything", name)
            cmd.color("gray70", f"%{name} and ({PROTEIN})")
            cmd.set("cartoon_transparency", .75, name)
            cmd.show("cartoon", f"%{name} and ({PROTEIN})")
            self.reference_ligand = f"%{name} and ({LIGAND})"
            cmd.show("sticks", self.reference_ligand)
            cmd.color("slate", self.reference_ligand)
            util.cnc(self.reference_ligand, _self=cmd)
            cmd.set("stick_radius", .19, self.reference_ligand)
            self.reference_protein = f"%{name} and ({PROTEIN})"
            self.reference, self.snapshot = name, snapshot
            self.baseline_signature = _world_signature(cmd, f"%{name}")
            self.analysis_cache.clear()
        except Exception:
            cmd.delete(name)
            raise
        finally:
            cmd.delete(source)

    def _source_version(self):
        state = self.state
        entry_id = state.current_entry_id()
        loaded = state.loaded_source_versions.get(state.current_object)
        if loaded is not None and loaded.entry_id == entry_id:
            return loaded.token
        materialized = state.materialized_paths.get(entry_id)
        if materialized is not None:
            entry = state.current_entry()
            return source_version_token(entry.source_path or entry.id, materialized.identity)
        return None

    def _analysis_key(self, scope, selections, members, reference_signature):
        return (self.dataset, self.state.source_mode, self.state.current_entry_id(),
                self._source_version(), repr(sorted(self.config.items())), self.radius,
                selections, members, reference_signature, _world_signature(cmd, scope))

    def _refresh_reference_snapshot(self, signature):
        snapshot = capture_snapshot(cmd, self.reference_protein, self.reference_ligand,
                                    self.radius, ligand_sites=True)
        ligand_atom_coordinates((atom, atom.coord) for atom in snapshot.ligand.atom)
        self.snapshot = snapshot
        self.baseline_signature = signature
        self.analysis_cache.clear()

    def _cached_analysis(self, key):
        result = self.analysis_cache.pop(key, None)
        if result is not None:
            self.analysis_cache[key] = result
        return result

    def _remember_analysis(self, key, result):
        self.analysis_cache[key] = result
        self.analysis_cache.move_to_end(key)
        while len(self.analysis_cache) > self.analysis_cache_limit:
            self.analysis_cache.popitem(last=False)

    def _draw(self, owner):
        if not owner or self.reference is None:
            return
        scope = self.pocket.scope(owner)
        source_objects = cmd.get_object_list(scope)
        self.source_objects = set(source_objects)
        bundle = self.pocket.owner._bundle_view_for_index(self.state.index) or {}
        protein = f"({scope}) and ({bundle.get('protein_selection') or PROTEIN})"
        ligand = bundle.get("ligand_selection") or LIGAND
        anchor = self.config["row_selection"]
        if anchor:
            selected = f"({scope}) and ({anchor})"
            if (self.config["mode"] != "ligand" and cmd.count_atoms(f"({selected}) and ({PROTEIN})")
                    and not cmd.count_atoms(f"({selected}) and ({LIGAND})")):
                protein = f"(byres ({selected})) and ({PROTEIN})"
            else:
                ligand = anchor
        ligand = f"({scope}) and ({ligand}) and ({LIGAND})"
        reference_signature = _world_signature(cmd, self.reference)
        if reference_signature != self.baseline_signature:
            self._refresh_reference_snapshot(reference_signature)
        selections = (bundle.get("protein_selection") or PROTEIN,
                      bundle.get("ligand_selection") or LIGAND)
        positions = {name: i for i, name in enumerate(source_objects)}
        members = tuple(_selection_signature(cmd, selection, positions)
                        for selection in (protein, ligand))
        key = self._analysis_key(scope, selections, members, reference_signature)
        result = self._cached_analysis(key)
        if result is None:
            current = capture_snapshot(cmd, protein, ligand, self.radius, ligand_sites=True)
            rmsd, matched, pose, contacts = compare_ligand_pockets(self.snapshot, current)
            result = _PocketAnalysisResult(rmsd, matched, pose, tuple(contacts))
            self._remember_analysis(key, result)
        rmsd, matched, pose = result.rmsd, result.matched, result.pose
        contacts = result.contacts
        cmd.enable(self.reference)
        self._hide_scene()
        pose_name = cmd.get_unused_name("sa_pocket_track_current")
        self.objects.append(pose_name)
        load_fragment(cmd, pose_name, pose, "magenta", .22, .33)
        self.contacts[:] = contacts
        self._redraw_contacts()
        if self.view is None:
            cmd.zoom(f"({self.reference_ligand}) or %{pose_name}", 6, animate=0)
            self.view = cmd.get_view()
        name = self.state.current_entry().display_name
        gained = sum(item.reference is None for item in contacts)
        lost = sum(item.current is None for item in contacts)
        self._status(f"{self.label} · Ligand fit {rmsd:.3f} Å ({matched} atoms) · Gained {gained} · Lost {lost}",
                     candidate=name)
        self._fill_details()

    def _hide_scene(self):
        owned = {self.reference, *self.objects, *self.focus, *self.focus_detail}
        visible = set(cmd.get_names("objects", enabled_only=1)) - owned
        self.managed.update(self.state.managed_objects)
        self.managed.add(self.panel._reference_track.name)
        for owner in self.state.managed_objects:
            self.managed.update(self.panel._reference_products(owner))
        self.hidden.update(visible)
        for name in visible:
            if cmd.get_type(name) == "object:molecule":
                self.hidden_identities[name] = molecule_identity(cmd, name)
            cmd.disable(name)

    def show_details(self):
        if not self.contacts:
            return
        if self.dialog is None:
            self.dialog = dialog = QtWidgets.QDialog(self.panel)
            dialog.setWindowTitle("Reference pocket interactions")
            dialog.setProperty("annotationNavigation", True)
            dialog.resize(790, 430)
            layout = QtWidgets.QVBoxLayout(dialog)
            self.label_widget = QtWidgets.QLabel()
            self.label_widget.setWordWrap(True)
            layout.addWidget(self.label_widget)
            self.table = _table(("Change", "Interaction", "Ligand atoms", "Reference protein", "Current protein"),
                                0, "referencePocketInteractions")
            self.table.cellClicked.connect(self._focus_contact)
            self.table.cellActivated.connect(self._focus_contact)
            layout.addWidget(self.table)
            all_contacts = QtWidgets.QPushButton("Show all contacts")
            all_contacts.clicked.connect(self._redraw_contacts)
            layout.addWidget(all_contacts)
        self._fill_details()
        self.dialog.show()

    def _fill_details(self):
        self.bar.pocket_counts.setEnabled(bool(self.contacts))
        if self.dialog is None:
            return
        self.label_widget.setText(self.bar.pocket_status.toolTip())
        self.table.clearSelection()
        self.table.setRowCount(len(self.contacts))
        for row, item in enumerate(self.contacts):
            shown = item.shown
            values = (shown.change, shown.kind, ", ".join(atom.label for atom in shown.pose.ligand_atoms),
                      _endpoint(item.reference.pose.protein_atoms) if item.reference else "—",
                      _endpoint(item.current.pose.protein_atoms) if item.current else "—")
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                contact = item.reference if column == 3 else item.current if column == 4 else shown
                if contact is not None:
                    cell.setToolTip(_contact_tip(contact))
                self.table.setItem(row, column, cell)

    def _focus_contact(self, row, column):
        item = self.contacts[row]
        contact = (item.reference if column == 3 else item.current if column == 4 else None) or item.shown
        if (contact is self.focused_contact and not self.showing_all
                and self._objects_exist(self.focus) and self._objects_exist(self.focus_detail)):
            self._set_objects_enabled(self.focus, False)
            self._set_objects_enabled(self.focus_detail, True)
            return
        with display_updates(cmd):
            self._ensure_full_contacts()
            self._clear_contact_focus()
            self._set_objects_enabled(self.focus, False)
            try:
                draw_pocket_interactions(cmd, self.reference, (contact,), self.focus_detail,
                                         selected=contact)
            except Exception:
                self._clear_contact_focus()
                self._set_objects_enabled(self.focus, True)
                self.showing_all = True
                raise
            self.focused_contact = contact
            self.showing_all = False

    def _redraw_contacts(self):
        if self.showing_all and self._objects_exist(self.focus):
            self._set_objects_enabled(self.focus, True)
            self._set_objects_enabled(self.focus_detail, False)
            return
        with display_updates(cmd):
            self._clear_contact_focus()
            self._ensure_full_contacts()
            self._set_objects_enabled(self.focus, True)
            self.showing_all = True

    def _objects_exist(self, names):
        existing = set(cmd.get_names("objects"))
        return all(name in existing for name in names)

    def _set_objects_enabled(self, names, enabled):
        visible = set(cmd.get_names("objects", enabled_only=1))
        action = cmd.enable if enabled else cmd.disable
        for name in names:
            if (name in visible) != enabled:
                action(name)

    def _ensure_full_contacts(self):
        if self._objects_exist(self.focus) and self.focus:
            return
        self._clear_full_contacts()
        draw_pocket_interactions(cmd, self.reference, [item.shown for item in self.contacts], self.focus)

    def _clear_contact_focus(self):
        for name in self.focus_detail:
            cmd.delete(name)
        self.focus_detail.clear()
        self.focused_contact = None

    def _clear_full_contacts(self):
        for name in self.focus:
            cmd.delete(name)
        self.focus.clear()

    def _clear_contacts(self):
        self._clear_contact_focus()
        self._clear_full_contacts()
        self.showing_all = False

    def _clear_overlay(self):
        self._clear_contacts()
        for name in self.objects:
            cmd.delete(name)
        self.objects.clear()
        self.contacts.clear()

    def _status(self, text, candidate=""):
        self.bar.pocket_status.setText(text)
        details = f"{candidate}\n{text}" if candidate else text
        self.bar.pocket_status.setToolTip(details)

    def clear(self):
        if not self.active:
            return
        same_dataset = self.dataset == self.dataset_key()
        self.dataset = self.identity = self.view = None
        self.timer.stop()
        if self.job is not None:
            self.job.cancel()
            self.job = None
        with display_updates(cmd):
            self._clear_overlay()
            if self.reference is not None:
                cmd.delete(self.reference)
            visible = set(cmd.get_names("objects"))
            restore = self.hidden - self.managed
            restore = {name for name in restore & visible
                       if name not in self.hidden_identities
                       or molecule_identity(cmd, name) == self.hidden_identities[name]}
            if same_dataset and self.state.current_object:
                restore.add(self.state.current_object)
                restore.update(self.panel._reference_products(self.state.current_object))
            for name in restore & visible:
                cmd.enable(name)
        self.reference = self.snapshot = None
        self.reference_protein = self.baseline_signature = None
        self.analysis_cache.clear()
        self.hidden.clear()
        self.hidden_identities.clear()
        self.source_objects.clear()
        self.managed.clear()
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        self.bar.pocket_counts.setText("Counts…")
        self.bar.pocket_counts.setToolTip("Residue counts, interaction changes, and RMSD tolerances")
        self.bar.pocket_counts.setEnabled(False)
