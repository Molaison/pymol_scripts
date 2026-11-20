"""Render one representative per observed pocket residue type in Compare."""
from collections import deque
from dataclasses import dataclass, field
from threading import Event
from uuid import uuid4

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets
from pymolplugins.core.pymol_display import display_updates

from .panel_style import ElidedLabel
from .pocket_analysis import CONTACT_COLORS, PocketAnalysis, Snapshot, capture_snapshot
from .pocket_candidate_overlay import draw_candidate, draw_contact_focus
from .pocket_details import interactions_page, rmsd_page
from .pocket_variants import LIGAND, PROTEIN, VARIANT_COLORS, PocketTally, reference_pocket, site_label


BACKGROUND_OPACITY = 0.12
STICK_RADIUS = 0.18
FOCUSED_STICK_RADIUS = 0.27
REFERENCE_CARTOON_TRANSPARENCY = 0.62
ISOLATED_CARTOON_TRANSPARENCY = 0.88
ISOLATED_CONTEXT = (("cartoon_transparency", ISOLATED_CARTOON_TRANSPARENCY), ("stick_transparency", 0.82))
PREVIEW_CONTEXT = (("cartoon_transparency", 0.84), ("stick_transparency", 0.46))


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
    total: int
    queue: deque = field(default_factory=deque)
    cancel: Event = field(default_factory=Event)


class PocketComparison:
    def __init__(self, panel, owner, scope):
        self.panel, self.owner, self.scope = panel, owner, scope
        self.bar = panel.compare_bar
        self.request = None
        self.variants = []
        self.created = []
        self.variant_objects = {}
        self.variant_colors = {}
        self.reference_object = ""
        self.reference_transparency = {}
        self.hidden = ()
        self.original_view = None
        self.managed = set()
        self.timer = QtCore.QTimer(panel)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self._step)
        self.bar.pocket_counts.clicked.connect(self.show_counts)
        self.dialog = None
        self.counts_table = None
        self.interaction_table = None
        self.contact_toggle = None
        self.contact_lines = []
        self.contacts = []
        self.candidate_preview = None
        self.preview_objects = {}
        self.preview_details = None
        self.focused_contact = None
        self.contact_focus_objects = {}
        self.ligand_limit = 2.0
        self.backbone_limit = 1.0
        self.all_sites = None
        self.only_site = None
        self.focused_site = None
        self.summary = ""

    def identity(self):
        state = self.owner.STATE
        return state.source_mode, state.current_entry_id(), state.current_object

    def start(self, entries):
        self.clear()
        if len(entries) < 2:
            raise ValueError("Select at least two candidates to summarize a pocket.")
        state = self.owner.STATE
        scope = self.scope(state.current_object)
        config = self.owner._bundle_view_for_index(state.index) or {}
        protein = config.get("protein_selection") or PROTEIN
        ligand = config.get("ligand_selection") or LIGAND
        radius = float(self.panel._interface_radius)
        protein_selection, ligand_selection = f"{scope} and ({protein})", f"{scope} and ({ligand})"
        sites = reference_pocket(cmd, protein_selection, ligand_selection, radius)
        reference = capture_snapshot(cmd, protein_selection, ligand_selection, radius)
        tally = PocketTally(reference.protein, sites)
        analysis = PocketAnalysis(reference, tally.sites)
        self.request = PocketRequest(self.identity(), scope, protein, ligand, radius,
                                     reference, tally, analysis, len(entries))
        try:
            self._queue_candidates(entries)
        except Exception:
            self.clear()
            raise
        self._status(f"Aligning 0 / {len(entries)} candidates…")
        self.timer.start()

    def _queue_candidates(self, entries):
        request = self.request
        state = self.owner.STATE
        context = self.owner._materialization_context()
        for entry in sorted(entries, key=lambda item: item.ordinal):
            future = None
            if state.source_mode != "groups" and entry.id != request.identity[1]:
                future = self.owner._get_bg_executor().submit(
                    self.owner._materialize_entry, entry, context, cancel_event=request.cancel)
            request.queue.append((entry, future))

    def _step(self):
        request = self.request
        if request is None or self.identity() != request.identity:
            self.clear()
            return
        entry, future = request.queue[0]
        if future is not None and not future.done():
            return
        try:
            snapshot = self._candidate_snapshot(entry, future)
            alignment = request.tally.add(snapshot.protein)
            request.analysis.add(entry.id, entry.display_name, snapshot, *alignment)
            request.queue.popleft()
            if request.queue:
                self._status(f"Aligning {request.tally.total} / {request.total} candidates…")
            else:
                self.timer.stop()
                self._publish()
        except Exception as error:
            self.clear()
            self._status(f"Pocket changes could not be shown · {entry.display_name} · {error}")

    def _candidate_snapshot(self, entry, future):
        request = self.request
        if entry.id == request.identity[1]:
            return request.reference
        if future is None:
            return self._snapshot(self.scope(entry.source_path))
        temporary = f"_sa_pocket_input_{uuid4().hex[:12]}"
        with display_updates(cmd):
            try:
                cmd.load(self.owner._long_path(future.result().path), temporary, quiet=1, zoom=0)
                cmd.disable(temporary)
                return self._snapshot(f"%{temporary}")
            finally:
                cmd.delete(temporary)

    def _snapshot(self, scope):
        request = self.request
        return capture_snapshot(cmd, f"{scope} and ({request.protein})",
                                f"{scope} and ({request.ligand})", request.radius)

    def _publish(self):
        request = self.request
        self.variants = request.tally.variants()
        self.contacts = request.analysis.interactions()
        with display_updates(cmd):
            self.original_view = cmd.get_view()
            self.hidden = tuple(cmd.get_names("objects", enabled_only=1))
            self.managed = set(self.owner.STATE.managed_objects) | set(cmd.get_object_list(request.scope))
            self.managed.add(request.identity[2])
            self.managed.update(item.name for item in self.panel.comparison.items.values())
            self.panel._view_jobs.cancel()
            for name in self.hidden:
                cmd.disable(name)
            self._draw(request)
        sites = len({variant.site for variant in self.variants})
        unmapped = sum(request.total - sum(request.tally.counts[site].values()) for site in request.tally.sites)
        status = f"{sites} varying pocket sites · {request.total} candidates · {request.radius:g} Å"
        self.summary = f"{status} · {unmapped} unmapped" if unmapped else status
        self._update_status()
        self.bar.pocket_counts.setEnabled(True)

    def _draw(self, request):
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
        cmd.show("sticks", ligand)
        cmd.color("slate", ligand)
        cmd.set("stick_color", "slate", ligand)
        self.reference_transparency = {name: cmd.get_setting_float(name, reference)
                                       for name, _ in ISOLATED_CONTEXT}
        types = sorted({variant.name for variant in self.variants})
        self.variant_colors = {name: VARIANT_COLORS[i % len(VARIANT_COLORS)] for i, name in enumerate(types)}
        for index, variant in enumerate(self.variants, start=1):
            if variant.model is None:
                continue
            name = f"{prefix}_{index}_{variant.name}_{variant.count}of{variant.total}"
            self.created.append(name)
            self.variant_objects[(variant.site, variant.name)] = name
            cmd.load_model(variant.model, name, quiet=1, zoom=0)
            cmd.hide("everything", name)
            cmd.show("sticks", name)
            cmd.color(self.variant_colors[variant.name], name)
            cmd.set("stick_radius", STICK_RADIUS, name)
            cmd.set("stick_transparency", 1 - variant.opacity, name)
        cmd.group(prefix, " ".join(self.created))
        self.created.append(prefix)
        changed = " or ".join(f"%{name}" for name in self.variant_objects.values())
        cmd.zoom(f"({ligand}) or ({changed})" if changed else ligand, 4, animate=0)

    def show_counts(self):
        if not self.created:
            return
        if self.dialog is not None:
            self.dialog.show()
            self.dialog.raise_()
            return
        dialog = self.dialog = QtWidgets.QDialog(self.panel)
        dialog.setWindowTitle("Pocket changes")
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
        self.only_site.setToolTip("Hide other side chains and fade the reference and ligand")
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
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        tabs = QtWidgets.QTabWidget(dialog)
        tabs.addTab(table, "Residues")
        table.cellClicked.connect(self._focus_row)
        table.cellActivated.connect(self._focus_row)
        interactions, self.interaction_table, self.contact_toggle = interactions_page(
            self.contacts, self.request.radius, self._focus_site, self._redraw_contacts)
        tabs.addTab(interactions, "Interactions")
        geometry, self.preview_details = rmsd_page(
            self.request.analysis.geometry, self.ligand_limit, self.backbone_limit, self._set_rmsd_limits,
            self._show_candidate_preview, self._clear_candidate_preview, self._focus_candidate_contact)
        tabs.addTab(geometry, "RMSD")
        tabs.currentChanged.connect(
            lambda index: self._clear_candidate_preview() if tabs.widget(index) is not geometry else None)
        layout.addWidget(tabs)
        dialog.show()
        self._mark_focused_rows()

    def _focus_row(self, row, _column):
        self._focus_site(self.variants[row].site)

    def _focus_site(self, site):
        if site != self.focused_site:
            self.focused_site = site
            if site is None and self.only_site.isChecked():
                self.only_site.setChecked(False)
            else:
                self._apply_focus_style()
            self._redraw_contacts()
        self._mark_focused_rows()

    def _apply_focus_style(self):
        site = self.focused_site
        isolated = site is not None and self.only_site.isChecked()
        previewing = self.candidate_preview is not None
        context = dict(PREVIEW_CONTEXT if previewing else ISOLATED_CONTEXT if isolated else ())
        with display_updates(cmd):
            if not previewing:
                self._apply_variant_style(site, isolated)
            for name, original in self.reference_transparency.items():
                cmd.set(name, max(original, context.get(name, original)), self.reference_object)
            # The reference cartoon also carries a transparency override on its protein atoms.
            protein = f"%{self.reference_object} and ({self.request.protein})"
            cartoon = context.get("cartoon_transparency", REFERENCE_CARTOON_TRANSPARENCY)
            cmd.set("cartoon_transparency", cartoon, protein)
        self._update_status()

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
            cmd.set("stick_transparency", 1 - variant.opacity * multiplier, name)
            cmd.set("stick_radius", FOCUSED_STICK_RADIUS if focused else STICK_RADIUS, name)

    def _set_rmsd_limits(self, ligand, backbone):
        self.ligand_limit, self.backbone_limit = ligand, backbone
        if self.candidate_preview is not None:
            if self.candidate_preview.geometry.status(ligand, backbone) != "Outside tolerance":
                self._clear_candidate_preview()
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
                                         preview, analysis.reference_fragment)
            except Exception as error:
                self.panel._show_error(f"Could not overlay candidate · {error}")
                return
            self._delete_candidate_objects()
            self.candidate_preview = preview
            self.preview_objects = objects
            self._clear_contact_lines()
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
                           if contact is not None else {})
            except Exception as error:
                self.panel._show_error(f"Could not highlight contact · {error}")
                self.preview_details.show_contact(self.focused_contact)
                return
            self._delete_contact_focus()
            self.contact_focus_objects = objects
            self.focused_contact = contact
            set_visible = cmd.enable if contact is None else cmd.disable
            for kind in ("gained", "lost"):
                name = self.preview_objects.get(kind)
                if name:
                    set_visible(name)
        self.preview_details.show_contact(contact)

    def _delete_contact_focus(self):
        for name in self.contact_focus_objects.values():
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
        from .review_view_presets import _cylinders

        with display_updates(cmd):
            self._clear_contact_lines()
            if self.candidate_preview is not None or self.focused_site is None or not self.contact_toggle.isChecked():
                return
            site_contacts = [contact for contact in self.contacts if contact.site == self.focused_site]
            for change, color in CONTACT_COLORS.items():
                pairs = dict.fromkeys(contact.points for contact in site_contacts if contact.change == change)
                name = _cylinders(cmd, self.reference_object, "interaction", pairs, color, .085, .55, zoom=0)
                if name:
                    self.contact_lines.append(name)
            if self.contact_lines:
                cmd.group(self.created[-1], " ".join(self.contact_lines))

    def _clear_contact_lines(self):
        for name in self.contact_lines:
            cmd.delete(name)
        self.contact_lines.clear()

    def _mark_focused_rows(self):
        for table, rows in ((self.counts_table, self.variants), (self.interaction_table, self.contacts)):
            model = table.model()
            selection = QtCore.QItemSelection()
            for row, record in enumerate(rows):
                if record.site == self.focused_site:
                    selection.select(model.index(row, 0), model.index(row, model.columnCount() - 1))
            table.selectionModel().select(selection, QtCore.QItemSelectionModel.ClearAndSelect)
        site_controls = self.focused_site is not None and self.candidate_preview is None
        self.all_sites.setEnabled(site_controls)
        self.only_site.setEnabled(site_controls)

    def _status(self, message):
        self.bar.pocket_status.setText(message)
        self.bar.pocket_status.setToolTip(message)

    def clear(self):
        request, self.request = self.request, None
        self.timer.stop()
        if request is not None:
            request.cancel.set()
            for _, future in request.queue:
                if future is not None:
                    future.cancel()
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        self.counts_table = None
        self.interaction_table = None
        self.contact_toggle = None
        self.preview_details = None
        self.all_sites = None
        self.only_site = None
        self.focused_site = None
        self.summary = ""
        self.bar.pocket_counts.setEnabled(False)
        if self.created or self.hidden:
            same_reference = request is not None and self.identity() == request.identity
            with display_updates(cmd):
                self._delete_candidate_objects()
                self._clear_contact_lines()
                for name in reversed(self.created):
                    cmd.delete(name)
                existing = set(cmd.get_names("objects"))
                for name in self.hidden:
                    if name in existing and (same_reference or name not in self.managed):
                        cmd.enable(name)
                if same_reference and self.original_view is not None:
                    cmd.set_view(self.original_view)
        self.created.clear()
        self.variant_objects.clear()
        self.variant_colors.clear()
        self.reference_object = ""
        self.reference_transparency.clear()
        self.variants = []
        self.contacts = []
        self.hidden = ()
        self.original_view = None
        self.managed.clear()

