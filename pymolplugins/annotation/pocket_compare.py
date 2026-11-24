"""Render one representative per observed pocket residue type in Compare."""
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from threading import Event
from uuid import uuid4

from pymol import cmd, util
from pymol.Qt import QtCore, QtGui, QtWidgets
from pymolplugins.core.pymol_display import display_updates

from .panel_style import ElidedLabel
from .pocket_analysis import PocketAnalysis, Snapshot, capture_snapshot
from .pocket_candidate_overlay import (
    draw_candidate, draw_contact_focus, draw_pocket_interactions, load_fragment, load_protein_partner,
    reference_buried_atoms,
)
from .pocket_details import fill_interactions, fill_ligand_table, interactions_page, ligand_table, rmsd_page
from .pocket_ligands import PartnerPoseCounts, ligand_pose_color
from .pocket_reference_review import PocketReferenceReview
from .pocket_sides import choose_pocket_sides
from .reference_track import molecule_identity
from . import pocket_jobs
from .pocket_variants import VARIANT_COLORS, PocketTally, reference_pocket, site_label


BACKGROUND_OPACITY = 0.12
STICK_RADIUS = 0.18
FOCUSED_STICK_RADIUS = 0.27
REFERENCE_CARTOON_TRANSPARENCY = 0.62
ISOLATED_CARTOON_TRANSPARENCY = 0.88
ISOLATED_CONTEXT = (("cartoon_transparency", ISOLATED_CARTOON_TRANSPARENCY), ("stick_transparency", 0.82))
PREVIEW_CONTEXT = (("cartoon_transparency", 0.84), ("stick_transparency", 0.46))


def _set_if_changed(api, setting, value, selection):
    current = api.get_setting_float(setting, selection)
    if current is None or abs(current - float(value)) > 1e-6:
        api.set(setting, value, selection)


@dataclass
class PocketCandidate:
    id: str
    display_name: str
    source: str
    future: object = None
    scene: bool = False
    work: object = None


@dataclass
class PocketRequest:
    identity: tuple
    scope: str
    protein: str
    ligand: str
    radius: float
    reference: Snapshot
    tally: PocketTally
    analysis: PocketAnalysis
    ligands: PartnerPoseCounts
    total: int
    protein_partner: bool = False
    reference_buried: tuple = ()
    reader_settings: dict = field(default_factory=dict)
    parallel_files: bool = False
    executor: object = field(default_factory=pocket_jobs.new_executor)
    file_workers: object = field(default_factory=pocket_jobs.FileWorkers)
    queue: deque = field(default_factory=deque)
    cancel: Event = field(default_factory=Event)
    scene_sources: set = field(default_factory=set)
    captured_sources: set = field(default_factory=set)
    failures: dict = field(default_factory=dict)
    published: int = 0


class PocketComparison:
    def __init__(self, panel, owner, scope):
        self.panel, self.owner, self.scope = panel, owner, scope
        self.bar = panel.compare_bar
        self.request = None
        self.variants = []
        self.created = []
        self.variant_objects = {}
        self.variant_colors = {}
        self.ligand_objects = {}
        self.reference_object = ""
        self.reference_transparency = {}
        self.hidden = set()
        self.hidden_identities = {}
        self.original_view = None
        self.managed = set()
        self.timer = QtCore.QTimer(panel)
        self.timer.setInterval(8)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.timeout.connect(self._step)
        self.track = PocketReferenceReview(self)
        self.bar.pocket_counts.clicked.connect(self.show_counts)
        self.dialog = None
        self.counts_table = None
        self.ligands_table = None
        self.interaction_table = None
        self.contact_toggle = None
        self.contact_objects = []
        self.contacts = []
        self.candidate_preview = None
        self.preview_objects = {}
        self.preview_details = None
        self.refresh_rmsd = None
        self.focused_contact = None
        self.contact_focus_objects = []
        self.ligand_limit = 2.0
        self.backbone_limit = 1.0
        self.all_sites = None
        self.only_site = None
        self.focused_site = None
        self.focused_interaction = None
        self.summary = ""

    def identity(self):
        state = self.owner.STATE
        return state.source_mode, state.current_entry_id(), state.current_object

    def start(self, entries, *, choose_proteins=False):
        if len(entries) < 2:
            raise ValueError("Select at least two candidates to summarize a pocket.")
        state = self.owner.STATE
        scope = self.scope(state.current_object)
        config = self.owner._bundle_view_for_index(state.index) or {}
        sides = choose_pocket_sides(cmd, self.panel, scope, config, choose_proteins=choose_proteins)
        if sides is None:
            return False
        protein, ligand = sides.target, sides.partner
        radius = float(self.panel._interface_radius)
        protein_selection, ligand_selection = f"{scope} and ({protein})", f"{scope} and ({ligand})"
        sites = reference_pocket(cmd, protein_selection, ligand_selection, radius,
                                 side_name="partner" if sides.protein_partner else "ligand")
        if sides.protein_partner:
            sites |= reference_pocket(cmd, ligand_selection, protein_selection, radius, side_name="target")
        reference = capture_snapshot(cmd, protein_selection, ligand_selection, radius,
                                     protein_partner=sides.protein_partner)
        tally = PocketTally(reference.protein, sites, reference.ligand if sides.protein_partner else None)
        analysis = PocketAnalysis(reference, tally.sites, protein_partner=sides.protein_partner)
        if len(tally.reference) < 3 or (sides.protein_partner and len(analysis.reference_partner) < 3):
            raise ValueError("Protein sides need at least three Cα atoms in the reference.")
        ligands = PartnerPoseCounts(reference.ligand, state.current_entry().display_name,
                                   protein_partner=sides.protein_partner)
        buried = reference_buried_atoms(cmd, scope, protein_selection, ligand_selection, radius) if sides.protein_partner else ()
        self.clear()
        self.request = PocketRequest(self.identity(), scope, protein, ligand, radius,
                                     reference, tally, analysis, ligands, len(entries), sides.protein_partner, buried)
        self.request.captured_sources.update(cmd.get_object_list(scope))
        try:
            self._queue_candidates(entries)
        except Exception:
            self.clear()
            raise
        self._status(f"Analyzing 0 / {len(entries)} candidates…")
        self.timer.start()
        return True

    def _queue_candidates(self, entries):
        request = self.request
        state = self.owner.STATE
        context = self.owner._materialization_context()
        for entry in sorted(entries, key=lambda item: item.ordinal):
            future = None
            if state.source_mode != "groups" and entry.id != request.identity[1]:
                future = self.owner._get_bg_executor().submit(
                    self.owner._materialize_entry, entry, context, cancel_event=request.cancel)
            request.queue.append(PocketCandidate(entry.id, entry.display_name, entry.source_path, future))
        count = sum(candidate.future is not None for candidate in request.queue)
        request.parallel_files = pocket_jobs.FILE_WORKERS_AVAILABLE and count >= pocket_jobs.MIN_PARALLEL_FILES
        if request.parallel_files:
            request.reader_settings = pocket_jobs.reader_settings(cmd)

    def add_scene_objects(self, sources):
        request = self.request
        for name in sources:
            if name not in request.scene_sources:
                request.scene_sources.add(name)
                request.queue.append(PocketCandidate(f"scene:{name}", name, name, scene=True))
                request.total += 1
        if request.queue and not self.timer.isActive():
            self.timer.start()

    def _step(self):
        request = self.request
        if request is None or self.identity() != request.identity:
            self.clear()
            return
        self._submit_ready(request)
        committed = False
        while request.queue:
            candidate = request.queue[0]
            if candidate.work is None or not candidate.work.done():
                break
            request.queue.popleft()
            self._commit_candidate(request, candidate)
            committed = True
        if request.queue:
            if committed:
                self._status(f"Analyzing {request.tally.total} / {request.total} candidates…")
            return
        self.timer.stop()
        request.file_workers.close(cancel=False)
        if request.tally.total > request.published:
            self._publish()
        elif self.created:
            self._update_status()
        else:
            self._status("No candidates could be compared.")

    def _submit_ready(self, request):
        running = sum(candidate.work is not None and not candidate.work.done() for candidate in request.queue)
        captured = False
        for candidate in request.queue:
            if running >= pocket_jobs.WORKERS:
                break
            if candidate.work is not None or (candidate.future is not None and not candidate.future.done()):
                continue
            native = candidate.future is not None and request.parallel_files
            live = not native and candidate.id != request.identity[1]
            if live and captured:
                continue
            try:
                if native:
                    path = self.owner._long_path(candidate.future.result().path)
                    candidate.work = request.executor.submit(request.file_workers.prepare, request, candidate, path)
                else:
                    captured |= live
                    snapshot = self._candidate_snapshot(candidate)
                    candidate.work = request.executor.submit(pocket_jobs.prepare_snapshot, request, candidate, snapshot)
            except Exception as error:
                candidate.work = Future()
                candidate.work.set_exception(error)
            else:
                running += 1

    def _commit_candidate(self, request, candidate):
        try:
            observations, contribution = candidate.work.result()
            request.analysis.add(contribution)
            request.tally.add(observations)
        except pocket_jobs.WorkerStartupError:
            request.parallel_files = False
            candidate.work = None
            request.queue.appendleft(candidate)
        except Exception as error:
            request.total -= 1
            request.failures[candidate.display_name] = str(error)
            if candidate.scene:
                request.scene_sources.discard(candidate.source)
        else:
            request.failures.pop(candidate.display_name, None)
            if candidate.scene and self.created and candidate.source in cmd.get_names("objects", enabled_only=1):
                self.hidden.add(candidate.source)
                self.hidden_identities[candidate.source] = molecule_identity(cmd, candidate.source)
                cmd.disable(candidate.source)

    def _candidate_snapshot(self, candidate):
        request = self.request
        if candidate.id == request.identity[1]:
            return request.reference
        if candidate.future is None:
            scope = self.scope(candidate.source)
            request.captured_sources.update(cmd.get_object_list(scope))
            return self._snapshot(scope)
        temporary = f"_sa_pocket_input_{uuid4().hex[:12]}"
        with display_updates(cmd):
            path = self.owner._long_path(candidate.future.result().path)
            return pocket_jobs.snapshot_file(cmd, path, request.protein, request.ligand, request.radius, temporary,
                                             protein_partner=request.protein_partner)

    def _snapshot(self, scope):
        request = self.request
        return capture_snapshot(cmd, f"{scope} and ({request.protein})",
                                f"{scope} and ({request.ligand})", request.radius,
                                protein_partner=request.protein_partner)

    def _publish(self):
        request = self.request
        previous = {(variant.site, variant.name): variant.model for variant in self.variants}
        self.variants = request.tally.variants()
        self.contacts[:] = request.analysis.interactions()
        request.ligands.update(request.analysis, self.ligand_limit)
        if self.focused_interaction is not None:
            selected = self.focused_interaction
            self.focused_interaction = next((contact for contact in self.contacts
                                             if (contact.site, contact.kind, contact.partner_site)
                                             == (selected.site, selected.kind, selected.partner_site)), None)
        first = not self.created
        with display_updates(cmd):
            if first:
                self._begin_scene(request)
            self._draw_variants(previous)
            self._draw_ligands()
            self._apply_focus_style()
            self._redraw_contacts()
            if first:
                self._frame_pocket(request)
        sites = len({variant.site for variant in self.variants})
        unmapped = sum(request.total - sum(request.tally.counts[site].values()) for site in request.tally.sites)
        region = "interface" if request.protein_partner else "pocket"
        status = f"{sites} varying {region} sites · {request.total} candidates · {request.radius:g} Å"
        self.summary = f"{status} · {unmapped} unmapped" if unmapped else status
        request.published = request.tally.total
        self._refresh_details()
        self._update_status()
        self.bar.pocket_counts.setEnabled(True)

    def _begin_scene(self, request):
        self.original_view = cmd.get_view()
        self.hidden = set(cmd.get_names("objects", enabled_only=1))
        self.hidden_identities = {name: molecule_identity(cmd, name) for name in self.hidden
                                  if cmd.get_type(name) == "object:molecule"}
        self.managed = set(self.owner.STATE.managed_objects) | set(cmd.get_object_list(request.scope))
        self.managed.add(request.identity[2])
        self.managed.update(item.name for item in self.panel.comparison.items.values())
        self.panel._view_jobs.cancel()
        for name in self.hidden:
            cmd.disable(name)
        prefix = f"sa_pocket_{uuid4().hex[:10]}"
        reference = f"{prefix}_reference"
        self.reference_object = reference
        self.created.append(reference)
        cmd.create(reference, f"{request.scope} and (({request.protein}) or ({request.ligand}))", 1, 1, zoom=0)
        cmd.hide("everything", reference)
        protein = f"%{reference} and ({request.protein})"
        cmd.color("gray70", protein)
        cmd.set("cartoon_color", "gray70", protein)
        cmd.set("cartoon_transparency", REFERENCE_CARTOON_TRANSPARENCY, protein)
        cmd.show("cartoon", protein)
        ligand = f"%{reference} and ({request.ligand})"
        cmd.color("slate", ligand)
        cmd.set("stick_color", -1, reference)
        util.cnc(ligand, _self=cmd)
        self.reference_transparency = {name: cmd.get_setting_float(name, reference)
                                       for name, _ in ISOLATED_CONTEXT}
        cmd.group(prefix, reference)
        self.created.append(prefix)

    def _draw_variants(self, previous):
        prefix = self.created[-1]
        types = {variant.name for variant in self.variants}
        for name in sorted(types - self.variant_colors.keys()):
            self.variant_colors[name] = VARIANT_COLORS[len(self.variant_colors) % len(VARIANT_COLORS)]
        for variant in self.variants:
            if variant.model is None:
                continue
            key = variant.site, variant.name
            name = self.variant_objects.get(key)
            if not name or previous.get(key) is not variant.model:
                if name:
                    cmd.delete(name)
                    self.created.remove(name)
                name = cmd.get_unused_name(f"{prefix}_{variant.name}_")
                self.variant_objects[key] = name
                self.created.insert(-1, name)
                cmd.load_model(variant.model, name, quiet=1, zoom=0)
                cmd.hide("everything", name)
                cmd.show("sticks", name)
                cmd.color(self.variant_colors[variant.name], name)
                util.cnc(name, _self=cmd)
                cmd.set("stick_radius", STICK_RADIUS, name)
                cmd.group(prefix, name)
            _set_if_changed(cmd, "stick_transparency", 1 - variant.opacity, name)

    def _draw_ligands(self):
        poses = self.request.ligands.poses
        for pose in self.ligand_objects.keys() - set(poses):
            name = self.ligand_objects.pop(pose)
            cmd.delete(name)
            self.created.remove(name)
        group = self.created[-1]
        for index, pose in enumerate(poses):
            if pose not in self.ligand_objects:
                name = cmd.get_unused_name(f"{group}_ligand_pose_")
                self.ligand_objects[pose] = name
                self.created.insert(-1, name)
                if self.request.protein_partner:
                    load_protein_partner(cmd, name, pose.model, ligand_pose_color(index), 0)
                else:
                    load_fragment(cmd, name, pose.model, ligand_pose_color(index), STICK_RADIUS, 0)
                cmd.group(group, name)

    def _frame_pocket(self, request):
        ligand = f"%{self.reference_object} and ({request.ligand})"
        changed = " or ".join(f"%{name}" for name in (*self.variant_objects.values(), *self.ligand_objects.values()))
        cmd.zoom(f"({ligand}) or ({changed})" if changed else ligand, 4, animate=0)

    def show_counts(self):
        if self.track.active:
            self.track.show_details()
            return
        if not self.created:
            return
        if self.dialog is not None:
            self.dialog.show()
            self.dialog.raise_()
            return
        dialog = self.dialog = QtWidgets.QDialog(self.panel)
        protein_partner = self.request.protein_partner
        dialog.setWindowTitle("Protein–protein interface changes" if protein_partner else "Pocket changes")
        dialog.resize(670, 430)
        layout = QtWidgets.QVBoxLayout(dialog)
        header = QtWidgets.QHBoxLayout()
        reference = self.owner.STATE.current_entry()
        if reference is not None:
            text = f"Pocket reference · {reference.display_name}"
            label = ElidedLabel(text)
            label.setToolTip(text)
            header.addWidget(label, 1)
        self.only_site = QtWidgets.QCheckBox("Only this site", dialog)
        self.only_site.setToolTip("Hide other side chains and fade the interface context")
        self.only_site.toggled.connect(self._apply_focus_style)
        header.addWidget(self.only_site)
        self.all_sites = QtWidgets.QPushButton("All sites", dialog)
        self.all_sites.setAutoDefault(False)
        self.all_sites.setProperty("role", "compact")
        self.all_sites.clicked.connect(lambda: self._focus_site(None))
        header.addWidget(self.all_sites)
        layout.addLayout(header)
        table = self.counts_table = QtWidgets.QTableWidget(len(self.variants), 5)
        table.setHorizontalHeaderLabels(("Site", "Residue", "Candidates", "Opacity", "Unmapped"))
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.verticalHeader().hide()
        self._fill_counts()
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        tabs = QtWidgets.QTabWidget(dialog)
        tabs.addTab(table, "Residues")
        table.cellClicked.connect(self._focus_row)
        table.cellActivated.connect(self._focus_row)
        self.ligands_table = ligand_table(tabs, protein_partner=protein_partner)
        self._fill_ligands()
        tabs.addTab(self.ligands_table, "Partner poses" if protein_partner else "Ligands")
        interactions, self.interaction_table, self.contact_toggle = interactions_page(
            self.contacts, self.request.radius,
            lambda contact: self._focus_site(contact.site, contact), self._redraw_contacts,
            protein_partner=protein_partner)
        tabs.addTab(interactions, "Interactions")
        geometry, self.preview_details, self.refresh_rmsd = rmsd_page(
            self.request.analysis.geometry, self.ligand_limit, self.backbone_limit, self._set_rmsd_limits,
            self._show_candidate_preview, self._clear_candidate_preview, self._focus_candidate_contact,
            protein_partner=protein_partner)
        tabs.addTab(geometry, "RMSD")
        if self.contacts:
            tabs.setCurrentWidget(interactions)
        tabs.currentChanged.connect(
            lambda index: self._clear_candidate_preview() if tabs.widget(index) is not geometry else None)
        layout.addWidget(tabs)
        dialog.show()
        self._mark_focused_rows()

    def _fill_counts(self):
        table = self.counts_table
        table.setRowCount(len(self.variants))
        for row, variant in enumerate(self.variants):
            values = (site_label(variant.site), variant.name, f"{variant.count}/{variant.total}",
                      f"{variant.opacity:.1%}" if variant.model is not None else "No sticks", str(variant.missing))
            for index, value in enumerate(values):
                table.setItem(row, index, QtWidgets.QTableWidgetItem(value))
            table.item(row, 0).setToolTip(f"Highlight all variants at {values[0]}")
            if variant.model is not None:
                swatch = QtGui.QPixmap(12, 12)
                swatch.fill(QtGui.QColor.fromRgbF(*cmd.get_color_tuple(self.variant_colors[variant.name])))
                table.item(row, 1).setIcon(QtGui.QIcon(swatch))

    def _refresh_details(self):
        if self.dialog is not None:
            self._fill_counts()
            self._fill_ligands()
            fill_interactions(self.interaction_table, self.contacts)
            self.refresh_rmsd()
            self._mark_focused_rows()

    def _fill_ligands(self):
        fill_ligand_table(self.ligands_table, self.request.ligands,
                          lambda index: cmd.get_color_tuple(ligand_pose_color(index)))

    def _focus_row(self, row, _column):
        self._focus_site(self.variants[row].site)

    def _focus_site(self, site, contact=None):
        if site != self.focused_site or contact is not self.focused_interaction:
            self.focused_site = site
            self.focused_interaction = contact
            if site is None and self.only_site.isChecked():
                self.only_site.setChecked(False)
            else:
                self._apply_focus_style()
            self._redraw_contacts()
        self._mark_focused_rows()

    def _apply_focus_style(self):
        site = self.focused_site
        isolated = site is not None and self.only_site is not None and self.only_site.isChecked()
        previewing = self.candidate_preview is not None
        context = dict(PREVIEW_CONTEXT if previewing else ISOLATED_CONTEXT if isolated else ())
        with display_updates(cmd):
            if not previewing:
                self._apply_variant_style(site, isolated)
            for name, original in self.reference_transparency.items():
                _set_if_changed(cmd, name, max(original, context.get(name, original)),
                                self.reference_object)
            # The reference cartoon also carries a transparency override on its protein atoms.
            protein = f"%{self.reference_object} and ({self.request.protein})"
            cartoon = context.get("cartoon_transparency", REFERENCE_CARTOON_TRANSPARENCY)
            cmd.set("cartoon_transparency", cartoon, protein)
            self._apply_ligand_style(context.get("stick_transparency", 0), previewing)
        self._update_status()

    def _apply_ligand_style(self, fade, previewing):
        poses = self.request.ligands
        reference = poses.poses[0]
        visible = set(cmd.get_names("objects", enabled_only=1))
        for pose, name in self.ligand_objects.items():
            show = not previewing or pose is reference
            if not show and name in visible:
                cmd.disable(name)
            elif show and name not in visible:
                cmd.enable(name)
            if self.request.protein_partner:
                setting = "cartoon_transparency"
            else:
                setting = "stick_transparency" if pose.model.bond else "sphere_transparency"
            level = fade if previewing else max(fade, 1 - poses.opacity(pose))
            _set_if_changed(cmd, setting, level, name)

    def _apply_variant_style(self, site, isolated):
        visible = set(cmd.get_names("objects", enabled_only=1))
        for variant in self.variants:
            name = self.variant_objects.get((variant.site, variant.name))
            if not name:
                continue
            focused = variant.site == site
            if isolated and not focused:
                if name in visible:
                    cmd.disable(name)
                continue
            if name not in visible:
                cmd.enable(name)
            multiplier = BACKGROUND_OPACITY if site is not None and not focused else 1
            _set_if_changed(cmd, "stick_transparency", 1 - variant.opacity * multiplier, name)
            _set_if_changed(cmd, "stick_radius", FOCUSED_STICK_RADIUS if focused else STICK_RADIUS,
                            name)

    def _set_rmsd_limits(self, ligand, backbone):
        changed = ligand != self.ligand_limit
        self.ligand_limit, self.backbone_limit = ligand, backbone
        if self.candidate_preview is not None:
            if self.candidate_preview.geometry.status(ligand, backbone) != "Outside tolerance":
                self._clear_candidate_preview()
        if changed and self.created:
            self.request.ligands.update(self.request.analysis, ligand)
            with display_updates(cmd):
                self._draw_ligands()
                self._apply_focus_style()
            self._fill_ligands()
        self._update_status()

    def _show_candidate_preview(self, entry_id):
        analysis = self.request.analysis
        preview = analysis.previews.get(entry_id)
        if preview is None or preview.geometry.status(self.ligand_limit, self.backbone_limit) != "Outside tolerance":
            self._clear_candidate_preview()
            return
        if preview is self.candidate_preview:
            return
        with display_updates(cmd):
            try:
                objects = draw_candidate(cmd, self.reference_object, self.created[-1],
                                         preview, analysis.reference_fragment,
                                         protein_partner=self.request.protein_partner)
            except Exception as error:
                self.panel._show_error(f"Could not overlay candidate · {error}")
                return
            self._delete_candidate_objects()
            self.candidate_preview = preview
            self.preview_objects = objects
            self._clear_contact_objects()
            self._apply_focus_style()
        self.preview_details.show_preview(preview)
        self._mark_focused_rows()

    def _clear_candidate_preview(self):
        if self.candidate_preview is None:
            return
        with display_updates(cmd):
            self._delete_candidate_objects()
            self._apply_focus_style()
            self._redraw_contacts()
        self.preview_details.show_preview(None)
        self._mark_focused_rows()

    def _delete_candidate_objects(self):
        self._delete_contact_focus()
        for name in self.preview_objects.values():
            cmd.delete(name)
        self.preview_objects.clear()
        self.candidate_preview = None

    def _focus_candidate_contact(self, contact):
        if self.candidate_preview is None or contact is self.focused_contact:
            return
        with display_updates(cmd):
            try:
                objects = (draw_contact_focus(cmd, self.reference_object, self.created[-1], contact)
                           if contact is not None else [])
            except Exception as error:
                self.panel._show_error(f"Could not highlight contact · {error}")
                self.preview_details.show_contact(self.focused_contact)
                return
            self._delete_contact_focus()
            self.contact_focus_objects = objects
            self.focused_contact = contact
            set_visible = cmd.enable if contact is None else cmd.disable
            for kind, name in self.preview_objects.items():
                if kind.startswith("interaction_"):
                    set_visible(name)
        self.preview_details.show_contact(contact)

    def _delete_contact_focus(self):
        for name in self.contact_focus_objects:
            cmd.delete(name)
        self.contact_focus_objects.clear()
        self.focused_contact = None

    def _update_status(self):
        site = self.focused_site
        records = self.request.analysis.geometry
        outside = sum(row.status(self.ligand_limit, self.backbone_limit) == "Outside tolerance" for row in records)
        unmapped = sum(row.unmapped for row in records)
        geometry = f"RMSD {outside}/{len(records)} outside"
        if unmapped:
            geometry += f" · {unmapped} incomplete"
        if self.candidate_preview is not None:
            self._status(f"Overlay {self.candidate_preview.geometry.candidate} · {geometry}")
            return
        if site is None:
            self._status(f"{geometry} · {self.summary}")
            return
        counts = " · ".join(f"{v.name} {v.count}/{v.total}" for v in self.variants if v.site == site)
        label = "Only" if self.only_site.isChecked() else "Focused"
        details = f" · {counts}" if counts else ""
        self._status(f"{label} {site_label(site)} · {geometry}{details}")

    def _redraw_contacts(self):
        with display_updates(cmd):
            self._clear_contact_objects()
            if self.candidate_preview is not None or (self.contact_toggle is not None
                                                       and not self.contact_toggle.isChecked()):
                return
            site_contacts = [contact for contact in self.contacts
                             if self.focused_site is None or self.focused_site in (contact.site, contact.partner_site)]
            buried = tuple(atom for atom in self.request.reference_buried
                           if self.focused_site is None or atom.site == self.focused_site)
            draw_pocket_interactions(cmd, self.reference_object, site_contacts, self.contact_objects,
                                     selected=self.focused_interaction, buried=buried)
            if self.contact_objects:
                cmd.group(self.created[-1], " ".join(self.contact_objects))

    def _clear_contact_objects(self):
        for name in self.contact_objects:
            cmd.delete(name)
        self.contact_objects.clear()

    def _mark_focused_rows(self):
        for table, rows in ((self.counts_table, self.variants), (self.interaction_table, self.contacts)):
            model = table.model()
            selection = QtCore.QItemSelection()
            for row, record in enumerate(rows):
                if table is self.interaction_table and self.focused_interaction is not None:
                    match = record is self.focused_interaction
                else:
                    match = self.focused_site in (record.site, getattr(record, "partner_site", ()))
                if match:
                    selection.select(model.index(row, 0), model.index(row, model.columnCount() - 1))
            table.selectionModel().select(selection, QtCore.QItemSelectionModel.ClearAndSelect)
        site_controls = self.focused_site is not None and self.candidate_preview is None
        self.all_sites.setEnabled(site_controls)
        self.only_site.setEnabled(site_controls)

    def _status(self, message):
        failures = self.request.failures if self.request else {}
        if failures:
            message += f" · {len(failures)} skipped"
        details = "\n".join(f"{name} · {detail}" for name, detail in failures.items())
        self.bar.pocket_status.setText(message)
        self.bar.pocket_status.setToolTip("\n".join(filter(None, (message, details))))

    def clear(self):
        self.track.clear()
        request, self.request = self.request, None
        self.timer.stop()
        if request is not None:
            request.cancel.set()
            for candidate in request.queue:
                if candidate.future is not None:
                    candidate.future.cancel()
            request.file_workers.close()
            request.executor.shutdown(wait=False, cancel_futures=True)
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        self.counts_table = None
        self.ligands_table = None
        self.interaction_table = None
        self.contact_toggle = None
        self.preview_details = None
        self.refresh_rmsd = None
        self.all_sites = None
        self.only_site = None
        self.focused_site = None
        self.focused_interaction = None
        self.summary = ""
        self.bar.pocket_counts.setEnabled(False)
        if self.created or self.hidden:
            same_reference = request is not None and self.identity() == request.identity
            with display_updates(cmd):
                self._delete_candidate_objects()
                self._clear_contact_objects()
                for name in reversed(self.created):
                    cmd.delete(name)
                existing = set(cmd.get_names("objects"))
                for name in self.hidden:
                    if (name in existing and (same_reference or name not in self.managed)
                            and (name not in self.hidden_identities
                                 or molecule_identity(cmd, name) == self.hidden_identities[name])):
                        cmd.enable(name)
                if same_reference and self.original_view is not None:
                    cmd.set_view(self.original_view)
        self.created.clear()
        self.variant_objects.clear()
        self.variant_colors.clear()
        self.ligand_objects.clear()
        self.reference_object = ""
        self.reference_transparency.clear()
        self.variants = []
        self.contacts = []
        self.hidden.clear()
        self.hidden_identities.clear()
        self.original_view = None
        self.managed.clear()

