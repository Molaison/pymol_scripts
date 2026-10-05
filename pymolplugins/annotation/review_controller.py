"""UI-only selection, draft, and score-cache coordination for the annotator."""

from concurrent.futures import ThreadPoolExecutor
import copy
import traceback

from pymol.Qt import QtCore, QtWidgets

from .review_config import decode_value, missing_required
from .review_widgets import AnnotationForm, AnnotationFieldsDialog, ScoreGuidanceDialog
from .score_summary import prepare_score_summary


_score_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="candidate-scores")


class SelectionState:
    def __init__(self, panel, owner, changed):
        self.panel, self.owner, self.changed = panel, owner, changed
        self.ids = set()
        self.revision = 0
        self.updating = False
        self.source_token = None
        panel.dataset_table.selectionModel().selectionChanged.connect(self._changed)

    def _changed(self, selected, deselected):
        if self.updating:
            return
        for ranges, adding in ((deselected, False), (selected, True)):
            for area in ranges:
                for row in range(area.top(), area.bottom() + 1):
                    entry_id = self.panel._dataset_model.entry_id_at(row)
                    if adding:
                        self.ids.add(entry_id)
                    else:
                        self.ids.discard(entry_id)
        self.ids.discard("")
        self.revision += 1
        self.changed()

    def clear(self):
        self.updating = True
        try:
            self.panel.dataset_table.clearSelection()
            self.ids.clear()
            self.revision += 1
        finally:
            self.updating = False

    def model_reset(self, source_token):
        if source_token != self.source_token:
            self.ids.clear()
        self.source_token = source_token
        self.ids.intersection_update(self.panel._entries_by_id)
        self.revision += 1
        if len(self.ids) < 2:
            return
        rows = sorted(self.panel._dataset_model.row_for_entry(entry_id) for entry_id in self.ids)
        selection = QtCore.QItemSelection()
        start = end = rows[0]
        for row in rows[1:] + [None]:
            if row is not None and row == end + 1:
                end = row
                continue
            selection.select(self.panel._dataset_model.index(start, 0), self.panel._dataset_model.index(end, self.panel._dataset_model.columnCount() - 1))
            start = end = row
        self.updating = True
        try:
            flags = QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows
            self.panel.dataset_table.selectionModel().select(selection, flags)
        finally:
            self.updating = False

    def ensure_current(self, row):
        if row < 0 or len(self.ids) > 1:
            return
        entry_id = self.owner.STATE.current_entry_id()
        if self.ids == {entry_id}:
            return
        self.updating = True
        try:
            self.panel.dataset_table.selectRow(row)
            self.ids = {entry_id}
            self.revision += 1
        finally:
            self.updating = False


class ScoreController:
    def __init__(self, panel, owner):
        self.panel, self.owner = panel, owner
        self.signature = None
        self.rules_revision = 0
        self.future = None
        self.summary = None
        self.timer = QtCore.QTimer(panel)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._ready)
        panel.score_footer.configureRequested.connect(self.configure)

    def refresh(self, selected_count=1):
        state = self.owner.STATE
        self.panel.score_footer.setVisible(state.source_mode == "csv" and state.dataset is not None)
        if self.panel._table_mode == "preview":
            self.panel.score_footer.show_scores("CSV preview — load to review", [], 0)
            return
        signature = (state.dataset_generation, tuple(state.display_columns), self.rules_revision)
        if signature != self.signature:
            self.signature = signature
            self.summary = None
            if self.future is not None:
                self.future.cancel()
            if state.entries and state.display_columns:
                # Relative ranks keep the full filtered population, never just the review sample.
                population = self.owner._view_entries(state.all_entries) if state.sampling.get("enabled") else state.entries
                self.future = _score_pool.submit(prepare_score_summary, tuple(population), tuple(state.display_columns),
                                                copy.deepcopy(state.score_guidance), excluded=(state.path_column, state.id_column))
                self.timer.start()
            else:
                self.future = None
                self.timer.stop()
        entry = state.current_entry()
        cards = self.summary.cards(entry) if self.summary is not None and entry is not None else []
        self.panel.score_footer.show_scores(entry.display_name if entry else "No candidate", cards,
                                           state.filtered_count, selected_count, self.future is not None)

    def _ready(self):
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        self.timer.stop()
        try:
            self.summary = future.result()
        except Exception as exc:
            traceback.print_exc()
            self.panel._show_error(f"Could not prepare score guidance: {exc}")
        self.refresh(max(1, len(self.panel.review.selection.ids)))

    def configure(self):
        dialog = ScoreGuidanceDialog(self.owner.STATE.display_columns, self.owner.STATE.score_guidance, self.panel)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.apply_config(dialog.result_config)

    def apply_config(self, config):
        self.owner.STATE.score_guidance = config
        self.rules_revision += 1
        self.owner._save_current_dataset_state(background=True)
        self.refresh(max(1, len(self.panel.review.selection.ids)))

    def close(self):
        self.timer.stop()
        if self.future is not None:
            self.future.cancel()


class ReviewController:
    def __init__(self, panel, owner):
        self.panel, self.owner = panel, owner
        self.form = None
        self.form_signature = None
        self.form_targets = ()
        self.draft_store = None
        self.drafts = {}
        self.pending = None
        self.selection = SelectionState(panel, owner, self.refresh)
        self.scores = ScoreController(panel, owner)

    def rebuild_form(self):
        if self.form is not None:
            if self.form_targets and self.form.dirty:
                self.drafts[self.form_targets] = self.form.updates()
            self.panel.score_footer.setParent(self.panel)
            self.panel.quick_values_layout.removeWidget(self.form)
            self.form.deleteLater()
        self.form = AnnotationForm(self.owner._annotation_fields(), self.panel)
        self.form.content_layout.insertWidget(0, self.panel.score_footer)
        self.form.primaryChosen.connect(self._label_clicked)
        self.form.saveRequested.connect(self.save)
        self.form.advanceRequested.connect(self._advance_clicked)
        self.form.configureRequested.connect(self.configure_fields)
        self.form.changed.connect(self._draft_changed)
        self.panel.quick_values_layout.addWidget(self.form)
        self.panel._value_buttons = self.form.primary_buttons
        self.form_signature = None
        self.refresh()

    def targets(self):
        if self.selection.ids:
            return tuple(sorted(self.selection.ids, key=lambda value: self.panel._dataset_model.row_for_entry(value)))
        current = self.owner.STATE.current_entry_id()
        return (current,) if current else ()

    def _draft_changed(self):
        self.form.hint_label.setText("Not saved")
        self._refresh_actions()

    def _refresh_actions(self):
        targets = self.targets()
        if len(targets) > 1:
            text = f"Save {len(targets):,} candidates"
        else:
            text = "Save and next" if self.panel.auto_advance_cb.isChecked() else "Save"
        self.form.save_button.setText(text)
        self.form.save_button.setEnabled(self.form.can_save())
        if self.form.dirty:
            missing = self.form.required_missing()
            message = "Required: " + ", ".join(name.replace("_", " ") for name in missing) if missing else "Not saved"
            self.form.hint_label.setText(message)
            self.form.hint_label.setToolTip(message)
        self.panel.selection_label.setText(f"{len(targets):,} selected" if len(targets) > 1 else "")
        self.panel.selection_label.setVisible(len(targets) > 1)

    def refresh(self):
        if self.form is None:
            return
        state = self.owner.STATE
        if self.draft_store is not state.row_annotation_store:
            self.drafts.clear()
            self.form_targets = ()
            self.form_signature = None
            self.form.dirty.clear()
            self.draft_store = state.row_annotation_store
        busy = self.panel._csv_task_kind in {"annotations", "annotation_undo"}
        targets = self.targets()
        current = state.current_entry_id()
        ready = bool(state.entries and targets) and not self.panel._filter_empty and self.panel._table_mode != "preview" and self.panel._pending_navigation is None and (len(targets) > 1 or targets[0] == current)
        self.form.setEnabled(ready and not busy)
        self.form.target_label.setText(f"Annotate {len(targets):,} selected candidates" if len(targets) > 1 else "Annotate the displayed candidate")
        signature = (state.dataset_generation, state.annotation_generation, current, self.selection.revision)
        if ready and not busy and signature != self.form_signature:
            if self.form_targets and self.form.dirty:
                self.drafts[self.form_targets] = self.form.updates()
            self.form_targets = targets
            store = state.row_annotation_store
            values = {entry_id: store.annotations_for(entry_id, refresh=False) for entry_id in targets} if store is not None else {}
            first = values.get(targets[0], {})
            common, mixed = {}, set()
            for field in self.form.fields:
                name = field["name"]
                value = first.get(name)
                if all(values.get(entry_id, {}).get(name) == value for entry_id in targets):
                    common[name] = value
                else:
                    mixed.add(name)
            required_filled = {field["name"]: all(not missing_required([field], values.get(entry_id, {})) for entry_id in targets)
                               for field in self.form.fields if field["required"]}
            self.form.load_annotations(common, mixed, self.drafts.get(targets), required_filled=required_filled)
            self.form_signature = signature
        self._refresh_actions()
        self.scores.refresh(max(1, len(targets)))

    def quick_label(self, value):
        if not self.form.isEnabled():
            return
        field = self.form.fields[0]
        if field["type"] == "multiple":
            selected = list(self.form.values[field["name"]])
            selected.remove(value) if value in selected else selected.append(value)
            self.form.set_value(field, selected, dirty=True)
            return
        self.form.set_value(field, value, dirty=True)

    def _label_clicked(self, value):
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        self.quick_label(value)
        if modifiers & QtCore.Qt.ShiftModifier:
            return
        if value and modifiers & QtCore.Qt.ControlModifier:
            self._advance_clicked()
            return
        if (value and self.form.fields[0]["type"] == "single"
                and self.panel.save_on_label_click_cb.isChecked()
                and len(self.targets()) == 1 and self.form.can_save()):
            self.save(quick=True)

    def _advance_clicked(self):
        if len(self.targets()) == 1:
            self.save(force_advance=True)

    def quick_digit(self, digit):
        values = self.form.fields[0]["values"]
        if 1 <= digit <= len(values):
            self.quick_label(values[digit - 1])
            if self.form.fields[0]["type"] == "single":
                self.save(quick=True)

    def save(self, quick=False, force_advance=False):
        if self.panel._csv_task_id or self.panel._pending_navigation is not None or not self.form.isEnabled():
            return
        if not self.form.can_save():
            if self.form.dirty:
                self.panel._show_error("Complete the required annotation fields before saving.")
            return
        self.panel._sync_to_state(update_directory=False, persist=False)
        targets = self.targets()
        if not targets or self.owner.STATE.row_annotation_store is None:
            return
        try:
            updates = self.form.updates()
        except ValueError as exc:
            self.panel._show_error(str(exc))
            return
        self.pending = {"store": self.owner.STATE.row_annotation_store, "targets": targets,
                        "current": self.owner.STATE.current_entry_id(), "undo": False,
                        "advance": len(targets) == 1, "updates": updates,
                        "force_advance": bool(force_advance),
                        "fields": copy.deepcopy(self.form.fields)}
        self.panel._start_csv_task("annotations", self.owner.save_annotations,
                                  {"source": self.owner._active_source_reference()}, worker_arguments={
                                      "store": self.pending["store"], "entry_ids": targets,
                                      "updates": updates, "fields": copy.deepcopy(self.form.fields)})
        self.refresh()

    def undo(self):
        history = self.owner.STATE.undo_history
        if self.panel._csv_task_id or not history or history[-1].get("mode") != "batch":
            return False
        entry = history[-1]
        self.pending = {"store": self.owner.STATE.row_annotation_store, "targets": tuple(entry["entry_ids"]),
                        "current": self.owner.STATE.current_entry_id(), "undo": True, "advance": False,
                        "undo_record": entry}
        self.panel._start_csv_task("annotation_undo", self.owner.undo_annotations,
                                  {"source": self.owner._active_source_reference()}, worker_arguments={
                                      "store": self.pending["store"], "sequence": entry["batch_seq"]})
        self.refresh()
        return True

    def complete(self, result, error):
        pending, self.pending = self.pending, None
        self.panel._clear_csv_task()
        if error is not None:
            traceback.print_exception(type(error), error, error.__traceback__)
            self.panel._show_error(f"Could not save annotations: {error}")
            self.panel._refresh_status()
            return
        state = self.owner.STATE
        if pending["store"] is not state.row_annotation_store:
            self.panel.status_label.setText("Saved annotations to the previous dataset.")
            return
        if pending["undo"]:
            state.undo_history.remove(pending["undo_record"])
        elif result.get("seq") is not None:
            state.undo_history.append({"mode": "batch", "batch_seq": result["seq"],
                                       "entry_ids": list(result["entry_ids"]), "entry_id": pending["current"]})
            state.undo_history[:] = state.undo_history[-100:]
            # A completed quality annotation is no longer a deferred triage item.
            self.owner._clear_review_status_entries(result["entry_ids"])
        state.annotation_generation += 1
        fields = self.owner._annotation_fields()
        primary = fields[0]["name"]
        displayed = {}
        for entry_id in result["entry_ids"]:
            values = state.row_annotation_store.annotations_for(entry_id, refresh=False)
            displayed[entry_id] = values.get(primary, "")
            index = self.panel._entry_id_to_active_index.get(entry_id)
            if index is not None:
                if self.owner._reviewed(fields, values):
                    state.annotated_entry_ids.add(entry_id)
                    state.annotated_indices.add(index)
                else:
                    state.annotated_entry_ids.discard(entry_id)
                    state.annotated_indices.discard(index)
        self.panel._dataset_model.update_annotation_entries(displayed)
        self.panel._table_annotation_revision = state.row_annotation_store.revision(refresh=False)
        self.form.dirty.clear()
        self.drafts.pop(pending["targets"], None)
        self.form_signature = None
        self.panel._schedule_state_save()
        self.panel._refresh_status()
        if not pending["undo"] and len(pending["targets"]) == 1 and state.current_entry_id() == pending["current"]:
            primary = pending["fields"][0]
            value = pending["updates"].get(primary["name"])
            if primary["type"] == "single" and value:
                entry = state.current_entry()
                context = {"object": state.current_object, "file": state.current_file(),
                           "index": str(state.index), "count": str(len(state.files)),
                           "entry_id": entry.id, "row_number": str(entry.row_number or ""),
                           "label": primary["name"], "value": value}
                self.owner._run_pymol_commands(state.bucket_cmds.get(value, ""), context)
        if pending["advance"] and (pending.get("force_advance") or self.panel.auto_advance_cb.isChecked()) and state.current_entry_id() == pending["current"]:
            self.panel._next()

    def configure_fields(self):
        if self.panel._csv_task_kind in {"annotations", "annotation_undo"}:
            return
        dialog = AnnotationFieldsDialog(self.owner._annotation_fields(), self.panel)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.owner.STATE.annotation_fields = dialog.result_fields
        self.owner.STATE.label_name = dialog.result_fields[0]["name"]
        self.owner.STATE.values_text = ",".join(dialog.result_fields[0]["values"])
        self.panel._sync_from_state()
        self.panel._rebuild_value_controls(sync_to_state=False)
        self.owner._rescan_annotated_indices(refresh=False)
        self.panel._save_ui_state()
        self.panel._refresh_status()

    def close(self):
        self.scores.close()
