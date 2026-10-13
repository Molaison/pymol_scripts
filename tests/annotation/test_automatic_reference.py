"""Ordered review steps through real CSV preparation and deterministic task delivery."""
import csv
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.annotation import test_structure_annotator_core as core
from pymolplugins.annotation.review_file import validate_review_config, write_review_override
from pymolplugins.annotation.review_steps import validate_steps


class OrderedReviewStepsTests(unittest.TestCase):
    def tearDown(self):
        from pymolplugins.annotation.persistence import flush_writes
        try:
            flush_writes()
            self.module._review_objects.clear(self.fake)
            self.module._panel = None
        finally:
            core.StructureAnnotatorCoreTests.tearDown(self)

    def setUp(self):
        core.StructureAnnotatorCoreTests.setUp(self)
        root = Path(self.history_temporary.name)
        for name in ("a.pdb", "b.pdb", "c.pdb", "anchor.pdb", "context.pdb"):
            core.StructureAnnotatorCoreTests._write_pdb(root / name, name)
        self.csv = str(root / "scores.csv")
        with open(self.csv, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "structure_path", "anchor_path", "context_path", "row_type", "rank"])
            writer.writerows([
                ["a", "a.pdb", "anchor.pdb", "context.pdb", "candidate", "2"],
                ["b", "b.pdb", "anchor.pdb", "context.pdb", "candidate", "1"],
                ["c", "c.pdb", "c.pdb", "context.pdb", "reference", "0"],
            ])
        self.recipe = {
            "steps": [
                {"type": "object", "name": "scaffold", "path_column": "anchor_path", "fixed": True,
                 "on_load_cmd": "color yellow, {scaffold}"},
                {"type": "object", "name": "environment", "path_column": "context_path", "fixed": True},
                {"type": "object", "name": "candidate"},
                {"type": "commands", "commands": "show sticks, {environment}"},
                {"type": "commands"},
            ], "step_cmd": "show cartoon, {candidate}"
        }
        self.events = []
        for name in ("load", "enable", "disable", "delete", "do"):
            original = getattr(self.fake, name)
            def traced(*args, _name=name, _original=original, **kwargs):
                self.events.append((_name, args))
                return _original(*args, **kwargs)
            setattr(self.fake, name, traced)
        self.fake.count_atoms = lambda name: int(name in self.fake.types)
        self.fake.align = mock.Mock(side_effect=AssertionError("Unexpected implicit alignment"))
        self.fake.color = mock.Mock(side_effect=AssertionError("Unexpected implicit coloring"))
        self.fake.zoom = mock.Mock(side_effect=AssertionError("Unexpected implicit zoom"))

    def load(self, recipe=None):
        (Path(self.csv).parent / "review.json").write_text(json.dumps(self.recipe if recipe is None else recipe))
        result = self.module.sa_load_csv(self.csv, path_column="structure_path", id_column="id")
        self.assertIsNotNone(result)
        return result

    def test_three_named_objects_exact_order_and_no_implicit_alignment(self):
        self.load()
        loaded = [args[1] for event, args in self.events if event == "load"]
        self.assertTrue(loaded[0].startswith("sa_scaffold_"))
        self.assertTrue(loaded[1].startswith("sa_environment_"))
        self.assertEqual(loaded[2], self.module.STATE.current_object)
        actions = [(event, args) for event, args in self.events if event in {"load", "do"}]
        self.assertEqual([event for event, args in actions], ["load", "do", "load", "load", "do", "do"])
        self.assertIn("color yellow, " + loaded[0], actions[1][1][0])
        self.assertIn("show sticks, " + loaded[1], actions[4][1][0])
        self.assertIn("show cartoon, " + loaded[2], actions[5][1][0])
        self.fake.align.assert_not_called()
        self.fake.zoom.assert_not_called()

    def test_adjacent_fixed_objects_have_no_reload_toggle_or_resolution(self):
        self.load()
        names = {value[1] for value in self.module._review_objects.active.values()}
        self.events.clear()
        with mock.patch.object(self.module, "_prepare_step_object", side_effect=AssertionError("cached path resolved again")):
            self.module.sa_next()
        touched = [(event, args) for event, args in self.events
                   if event in {"enable", "disable", "delete", "load"} and any(name in args for name in names)]
        self.assertEqual(touched, [])
        self.assertEqual(self.module.STATE.current_entry().metadata["id"], "b")
        self.assertTrue(names.issubset(self.fake.enabled))

    def test_pending_default_preload_cannot_load_objects_after_steps_are_enabled(self):
        self.load()
        self.events.clear()
        self.module._preload_queue = [(self.module._materialization_generation, self.module.STATE.entries[1].id, 1)]
        self.module._process_preload_tick()
        self.assertEqual(self.module._preload_queue, [])
        self.assertFalse(any(event == "load" for event, args in self.events))

    def test_changed_path_disables_old_fixed_and_revisit_reuses_it(self):
        self.load()
        first = self.module._review_objects.active["scaffold"][1]
        self.module._navigate_annotation("goto", 2)
        second = self.module._review_objects.active["scaffold"][1]
        self.assertNotEqual(first, second)
        self.assertIn(first, self.fake.types)
        self.assertNotIn(first, self.fake.enabled)
        self.module._navigate_annotation("goto", 0)
        self.assertEqual(self.module._review_objects.active["scaffold"][1], first)
        self.assertIn(second, self.fake.types)
        self.assertNotIn(second, self.fake.enabled)
        self.module._review_objects.clear(self.fake)
        self.assertFalse({first, second} & set(self.fake.types))

    def test_failure_prepares_before_changing_candidate_or_scene(self):
        self.load()
        from dataclasses import replace
        state = self.module.STATE
        entry = state.entries[1]
        state.entries[1] = replace(entry, metadata=dict(entry.metadata, anchor_path="missing.pdb"))
        before = (state.index, state.current_object, set(self.fake.enabled), list(self.events))
        with self.assertRaises(ValueError):
            self.module._navigate_annotation("goto", 1)
        self.assertEqual((state.index, state.current_object, set(self.fake.enabled), self.events), before)

    def test_empty_auxiliary_path_is_optional_until_a_command_uses_it(self):
        self.load()
        from dataclasses import replace
        state = self.module.STATE
        state.entries[1] = replace(state.entries[1], metadata=dict(state.entries[1].metadata, anchor_path=""))
        self.module._navigate_annotation("goto", 1)
        self.assertNotIn("scaffold", state.step_context)
        state.step_assets.pop(state.entries[0].id)
        state.entries[0] = replace(state.entries[0], metadata=dict(state.entries[0].metadata, anchor_path=""))
        state.step_cmd = "zoom {scaffold}"
        with self.assertRaisesRegex(ValueError, "unknown placeholder"):
            self.module._navigate_annotation("goto", 0)
        self.assertEqual(state.index, 1)

    def test_review_filter_sort_defaults_and_override_round_trip(self):
        recipe = dict(self.recipe, filter_text='where:row_type == "candidate"', sort_column="rank", sort_descending=False)
        self.load(recipe)
        self.assertEqual([entry.metadata["id"] for entry in self.module.STATE.entries], ["b", "a"])
        config = {key: getattr(self.module.STATE, key) for key in self.module.REVIEW_KEYS}
        config.update(sort_descending=True, steps=list(self.recipe["steps"]))
        write_review_override(self.csv, config)
        self.module.STATE = self.module.AnnotatorState(preload_enabled=False)
        self.load(recipe)
        self.assertEqual([entry.metadata["id"] for entry in self.module.STATE.entries], ["a", "b"])
        self.assertEqual(self.module.STATE.steps, self.recipe["steps"])

    def test_explicit_object_definition_edit_reloads_once_and_applies_new_commands(self):
        self.load()
        state = self.module.STATE
        previous = self.module._review_objects.active["scaffold"][1]
        state.steps[0] = dict(state.steps[0], on_load_cmd="color cyan, {scaffold}")
        self.events.clear()
        self.module._navigate_annotation("goto", state.index)
        current = self.module._review_objects.active["scaffold"][1]
        self.assertNotEqual(current, previous)
        self.assertNotIn(previous, self.fake.types)
        self.assertTrue(any(event == "do" and "color cyan, " + current in args[0] for event, args in self.events))
        self.events.clear()
        self.module.sa_next()
        self.assertFalse(any(event in {"load", "enable", "disable"} and current in args for event, args in self.events))

    def test_schema_rejects_forward_placeholders_and_duplicate_or_missing_candidate(self):
        for steps in ([{"type": "commands", "commands": "zoom {scaffold}"}] + self.recipe["steps"],
                      self.recipe["steps"] + [{"type": "object", "name": "candidate"}],
                      [{"type": "commands", "commands": ""}]):
            with self.assertRaises(ValueError):
                validate_steps(steps)
        validate_review_config(self.recipe)

    def test_default_and_legacy_reference_have_no_forced_style_or_alignment(self):
        self.load({"step_cmd": ""})
        self.assertEqual(len(self.module._review_objects.owned), 0)
        self.load({"reference_column": "anchor_path", "step_cmd": ""})
        self.assertIn("reference", self.module._review_objects.active)
        self.fake.align.assert_not_called()
        self.assertEqual([event for event, args in self.events if event == "do"], [])

    def _panel(self):
        panel = self.module.StructureAnnotatorPanel.__new__(self.module.StructureAnnotatorPanel)
        panel._closed = False
        panel._csv_task_id = panel._csv_task_kind = ""
        panel._csv_task_epoch = 0
        panel._pending_navigation = None
        panel._pending_csv_action = ""
        panel._filter_empty = False
        panel._table_mode = "active"
        panel._qt_shortcuts = None
        panel.error_label = mock.Mock()
        panel.status_label = mock.Mock()
        panel.review = SimpleNamespace(selection=SimpleNamespace(clear=mock.Mock()))
        for name in ("_source_mode_changed", "_sync_to_state", "_refresh_status", "_show_error",
                     "_schedule_state_save", "_queue_preload_maintenance"):
            setattr(panel, name, mock.Mock())
        self.module.QtCore.QCoreApplication = core.DummyApplication
        self.module._panel = panel
        return panel

    def test_async_cancel_stale_and_source_change_cannot_commit(self):
        self.load()
        panel = self._panel()
        jobs = {}
        def submit(owner, kind, worker, args, kwargs, **job):
            name = str(len(jobs) + 1)
            job["target_index"] = kwargs["index"]
            jobs[name] = (job, worker, kwargs)
            return name
        def deliver(name):
            job, worker, kwargs = jobs[name]
            panel._complete_csv_task(name, job, worker(**kwargs), None)
        with mock.patch.object(self.module, "_submit_csv_async_job", side_effect=submit), mock.patch.object(self.module, "_cancel_csv_async_job"):
            panel._request_navigation(1)
            first = panel._csv_task_id
            panel._cancel_csv_task()
            deliver(first)
            self.assertEqual(self.module.STATE.index, 0)
            panel._request_navigation(1)
            second = panel._csv_task_id
            self.module.STATE.dataset_generation += 1
            deliver(second)
            self.assertEqual(self.module.STATE.index, 0)
            panel._request_navigation(1)
            third = panel._csv_task_id
            self.module.STATE.csv_path = "another.csv"
            deliver(third)
            self.assertEqual(self.module.STATE.index, 0)
            panel._request_navigation(1)
            deliver(panel._csv_task_id)
            self.assertEqual(self.module.STATE.index, 1)
