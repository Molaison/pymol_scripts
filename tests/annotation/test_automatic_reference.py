"""Deterministic task delivery around the real CSV/reference preparation path.

Reuse the core fake PyMOL scene; native alignment is covered by the host smoke.
"""
import json
import os
import unittest
from unittest import mock

from tests.annotation import test_structure_annotator_core as core


class AutomaticReferenceTests(unittest.TestCase):
    def tearDown(self):
        from pymolplugins.annotation.persistence import flush_writes
        try:
            flush_writes()
        finally:
            core.StructureAnnotatorCoreTests.tearDown(self)

    def setUp(self):
        core.StructureAnnotatorCoreTests.setUp(self)
        self.addCleanup(setattr, self.module, "_panel", None)
        root = self.history_temporary.name
        for name in ("a.pdb", "b.pdb", "ref.pdb"):
            core.StructureAnnotatorCoreTests._write_pdb(os.path.join(root, name), name)
        self.csv = os.path.join(root, "scores.csv")
        with open(self.csv, "w") as handle:
            handle.write("id,structure_path,reference\na,a.pdb,ref.pdb\nb,b.pdb,ref.pdb\n")
        with open(os.path.join(root, "review.json"), "w") as handle:
            json.dump({"reference_column": "reference"}, handle)
        panel = self.module.StructureAnnotatorPanel.__new__(self.module.StructureAnnotatorPanel)
        self.panel = panel
        panel._closed = False
        panel._csv_task_id = panel._csv_task_kind = ""
        panel._csv_task_epoch = 0
        panel._reference_step = None
        panel._reference_attempted = False
        panel._reference_object = panel._reference_owner = ""
        panel._reference_alignment = None
        panel._reference_timer = mock.Mock()
        panel._maintenance_timer = mock.Mock()
        panel._source_mode_changed = mock.Mock()
        panel._schedule_state_save = mock.Mock()
        panel._refresh_status = panel._queue_reference_update
        panel._show_error = mock.Mock()
        panel.status_label = mock.Mock()
        panel.error_label = mock.Mock()
        self.module._panel = panel
        self.jobs = {}
        self.counter = 0
        def submit(owner, kind, worker, args, kwargs, **job):
            self.counter += 1
            name = str(self.counter)
            self.jobs[name] = (job, worker, args, kwargs)
            return name
        self.patch = mock.patch.object(self.module, "_submit_csv_async_job", side_effect=submit)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.cancel = mock.patch.object(self.module, "_cancel_csv_async_job")
        self.cancel.start()
        self.addCleanup(self.cancel.stop)
        self.module.QtCore.QCoreApplication = core.DummyApplication
        self.fake.count_atoms = lambda name: 1 if name in self.fake.types else 0
        self.fake.align = mock.Mock(return_value=(0.1, 1))
        self.fake.color = mock.Mock()
        self.fake.show = mock.Mock()
        result = self.module.sa_load_csv(self.csv, path_column="structure_path", id_column="id")
        self.assertIsNotNone(result)
        self.assertEqual(self.module.STATE.reference_column, "reference")

    def deliver(self, job_id=None, error=None):
        job_id = job_id or self.panel._csv_task_id
        job, worker, args, kwargs = self.jobs[job_id]
        result = None
        if error is None:
            try:
                result = worker(*args, **kwargs)
            except Exception as exc:
                error = exc
        self.panel._complete_csv_task(job_id, job, result, error)

    def test_import_next_revisit_and_hide(self):
        panel, state = self.panel, self.module.STATE
        self.assertEqual(self.counter, 0)  # Candidate commits before deferred work.
        first = state.current_object
        panel._auto_reference()
        self.deliver()
        self.assertTrue(panel._reference_object)
        self.fake.align.assert_called_with(panel._reference_object, first, quiet=1)
        panel._compare_reference()
        panel._auto_reference()
        self.assertFalse(panel._reference_object)
        self.assertEqual(self.counter, 1)
        self.module.sa_next()
        panel._auto_reference()
        self.deliver()
        second_reference = panel._reference_object
        self.assertNotEqual(state.current_object, first)
        self.module.sa_prev()
        self.assertNotIn(second_reference, self.fake.types)
        panel._auto_reference()
        self.deliver()
        self.assertEqual(state.current_object, first)
        self.assertEqual(self.counter, 3)

    def test_blank_and_unconfigured_are_candidate_only(self):
        from dataclasses import replace
        state, panel = self.module.STATE, self.panel
        for column, path in (("", "ref.pdb"), ("reference", "  ")):
            state.reference_column = column
            state.entries[state.index] = replace(state.current_entry(), metadata={"reference": path})
            panel._queue_reference_update()
            panel._auto_reference()
            self.assertFalse(panel._reference_object)
        self.assertEqual(self.counter, 0)

    def test_failure_and_cancellation_preserve_candidate_without_auto_retry(self):
        panel, state = self.panel, self.module.STATE
        candidate = state.current_object
        panel._auto_reference()
        self.deliver(error=ValueError("reference unavailable"))
        panel._show_error.assert_called_once()
        panel._queue_reference_update()
        panel._auto_reference()
        self.assertEqual(self.counter, 1)
        panel._compare_reference()  # Explicit retry remains available.
        cancelled = panel._csv_task_id
        panel._cancel_csv_task()
        self.deliver(cancelled)
        panel._auto_reference()
        self.assertEqual(self.counter, 2)
        self.assertFalse(panel._reference_object)
        self.assertEqual(state.current_object, candidate)
        self.assertIn(candidate, self.fake.enabled)

    def test_late_result_after_navigation_and_revisit_is_rejected(self):
        panel = self.panel
        panel._auto_reference()
        old = panel._csv_task_id
        self.module.sa_next()
        self.module.sa_prev()
        panel._auto_reference()
        current = panel._csv_task_id
        self.deliver(old)
        self.assertFalse(panel._reference_object)
        self.assertEqual(panel._csv_task_id, current)
        self.deliver(current)
        self.assertTrue(panel._reference_object)

    def test_dataset_generation_rejects_result_and_cleans_existing_reference(self):
        panel = self.panel
        panel._auto_reference()
        old = panel._csv_task_id
        self.module.STATE.dataset_generation += 1
        self.deliver(old)
        self.assertFalse(panel._reference_object)
        panel._auto_reference()
        self.deliver()
        reference = panel._reference_object
        self.module.STATE.dataset_generation += 1
        self.module.STATE.reference_column = ""
        panel._queue_reference_update()
        panel._auto_reference()
        self.assertNotIn(reference, self.fake.types)

    def test_worker_cancelled_does_not_retry(self):
        panel = self.panel
        panel._auto_reference()
        self.deliver(error=self.module._CsvPreparationCancelled())
        panel._queue_reference_update()
        panel._auto_reference()
        self.assertEqual(self.counter, 1)
        self.assertFalse(panel._reference_object)
        self.assertIn(self.module.STATE.current_object, self.fake.enabled)

    def test_closed_panel_rejects_completion(self):
        panel = self.panel
        panel._auto_reference()
        panel._closed = True
        self.deliver()
        self.assertFalse(panel._reference_object)
        panel._auto_reference()
        self.assertEqual(self.counter, 1)
