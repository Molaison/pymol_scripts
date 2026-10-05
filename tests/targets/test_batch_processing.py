from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pymolplugins.targets.batch_plan import id_pairs, id_pattern_glob, reference_pairs, select_ids
from pymolplugins.targets.batch_runner import BatchRunner
from pymolplugins.targets.batch_sources import BatchConfig, prepare_batch


class PairPlanningTests(unittest.TestCase):
    def test_reference_excludes_self_preserves_order(self):
        pairs = reference_pairs(["B", "ref", "A"], "ref")
        self.assertEqual([(pair.identifier, pair.left, pair.right) for pair in pairs],
                         [("1", "B", "ref"), ("2", "A", "ref")])

    def test_reference_empty_and_duplicates_are_errors(self):
        for names, reference in ((["ref"], "ref"), ([], "ref"), (["A", "A"], "ref"), (["A"], "")):
            with self.subTest(names=names, reference=reference), self.assertRaises(ValueError):
                reference_pairs(names, reference)

    def test_id_pairing_is_natural_not_position_based(self):
        pairs = id_pairs(["A10", "A1", "A2"], ["B2", "B10", "B1"], "A{id}", "B{id}")
        self.assertEqual([pair.identifier for pair in pairs], ["1", "2", "10"])
        self.assertEqual([(pair.left, pair.right) for pair in pairs], [("A1", "B1"), ("A2", "B2"), ("A10", "B10")])

    def test_explicit_id_subset_retains_request_order(self):
        pairs = id_pairs(["A1", "A2", "A3"], ["B3", "B1"], "A{id}", "B{id}", "3,1")
        self.assertEqual([pair.identifier for pair in pairs], ["3", "1"])
        self.assertEqual(select_ids(["1", "2", "3", "9"], "2-3,1"), ("2", "3", "1"))

    def test_missing_partners_and_duplicate_ids_are_errors(self):
        cases = ((["A1", "A2"], ["B1"]), (["A1"], ["B1", "B2"]), (["A1", "A1"], ["B1"]))
        for left, right in cases:
            with self.subTest(left=left, right=right), self.assertRaisesRegex(ValueError, "Missing|Duplicate"):
                id_pairs(left, right, "A{id}", "B{id}")

    def test_id_errors_are_not_silently_skipped(self):
        for requested in ("4", "1,1", "1,,2", "3-1", "1-100001"):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                select_ids(["1", "2", "3"], requested)
        with self.assertRaisesRegex(ValueError, "different"):
            id_pairs(["A1"], ["A1"], "A{id}", "A{id}")

    def test_patterns_are_literal_and_windows_paths_work(self):
        pairs = id_pairs([r"C:\run [x]\1_A.pdb"], [r"D:\out\1_B.pdb"],
                         r"C:\run [x]\{id}_A.pdb", r"D:\out\{id}_B.pdb")
        self.assertEqual(pairs[0].identifier, "1")
        self.assertEqual(id_pattern_glob("run[1]/{id}.pdb"), "run[[]1]/*.pdb")
        with self.assertRaises(ValueError):
            id_pairs(["A1/2"], ["B1/2"], "A{id}", "B{id}")

    def test_invalid_patterns_are_rejected(self):
        for pattern in ("A", "A{id}{id}", "A{other}", "A{id:03}", "A{id!r}", "A{"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                id_pattern_glob(pattern)

    def test_wildcard_patterns_capture_ids(self):
        pairs = id_pairs(["model_10_A.pdb", "model_2_A.pdb"], ["ref_2_B.pdb", "ref_10_B.pdb"],
                         "model_*_A.pdb", "ref_*_B.pdb", match="glob")
        self.assertEqual([pair.identifier for pair in pairs], ["2", "10"])
        pairs = id_pairs(["run_x/A12.pdb"], ["run_y/B12.pdb"], "run_?/[A]{id}.pdb", "run_*/B{id}.pdb", match="glob")
        self.assertEqual(pairs[0].identifier, "12")

    def test_ambiguous_wildcards_are_rejected(self):
        for pattern in ("A.pdb", "A*_*", "A??.pdb"):
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, "wildcard pattern"):
                id_pattern_glob(pattern, "glob")
        self.assertEqual(id_pattern_glob("[*]A*.pdb", "glob"), "[*]A*.pdb")

    def test_regex_names_use_named_or_single_capture_groups(self):
        pairs = id_pairs(["left/A10_model.pdb", "left/A2_model.pdb", "left/notes.txt"],
                         ["right/B2.pdb", "right/B10.pdb"], r"A(?P<id>\d+)_model\.pdb", r"B(\d+)\.pdb",
                         match="regex")
        self.assertEqual([pair.identifier for pair in pairs], ["2", "10"])
        self.assertEqual(pairs[0].left, "left/A2_model.pdb")

    def test_regex_duplicate_empty_ids_and_missing_capture_are_errors(self):
        cases = ((["A1_x", "A1_y"], r"A(?P<id>\d+)_.+", "Duplicate"),
                 (["A"], r"A(?P<id>\d*)", "Empty"),
                 (["A1"], r"A\d+", "capture group"),
                 (["A1"], r"(A)(\d+)", "capture group"))
        for names, pattern, message in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, message):
                id_pairs(names, ["B1"], pattern, r"B(?P<id>\d+)", match="regex")


class FakeCmd:
    def __init__(self):
        self.objects = {"unrelated"}
        self.calls = []

    def get_names(self, kind):
        return sorted(self.objects)

    def count_atoms(self, name):
        return int(name in self.objects)

    def load(self, path, name, **kwargs):
        self.calls.append(("load", str(path), name))
        self.objects.add(name)

    def create(self, name, selection, **kwargs):
        if selection not in self.objects:
            raise RuntimeError("missing source")
        self.objects.add(name)
        self.calls.append(("create", name, selection))

    def delete(self, name):
        self.objects.discard(name)
        self.calls.append(("delete", name))

    def disable(self, name):
        self.calls.append(("disable", name))

    def enable(self, name):
        self.calls.append(("enable", name))

    def save(self, path, name, **kwargs):
        self.calls.append(("save", str(path), name))
        Path(path).write_text("saved structure\n", encoding="utf-8")


class BatchProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pymol-batch-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "inputs"
        self.source.mkdir()
        for name in ("A1.pdb", "A2.pdb", "A10.pdb", "B1.pdb", "B2.pdb", "B10.pdb", "ref.pdb"):
            (self.source / name).write_text("ATOM\n", encoding="utf-8")
        self.config = BatchConfig(a=str(self.source / "A*.pdb"), b=str(self.source / "ref.pdb"),
                                  output=str(self.root / "output"), commands="first {id}\nsecond {id}")
        self.cmd = FakeCmd()

    def runner(self, config=None, fail=None):
        plan = prepare_batch(config or self.config)
        def execute(cmd, command):
            cmd.calls.append(("execute", command))
            if command == fail:
                raise RuntimeError("synthetic command failure")
        return BatchRunner(plan, self.cmd, execute)

    def test_preparation_is_read_only_and_renders_all_steps(self):
        plan = prepare_batch(self.config)
        self.assertFalse(Path(self.config.output).exists())
        self.assertEqual([item.pair.left for item in plan.items],
                         [str(self.source / name) for name in ("A1.pdb", "A2.pdb", "A10.pdb")])
        self.assertEqual(plan.items[1].commands, ((1, "first 2"), (2, "second 2")))

    def test_folder_pairs_use_exact_ids(self):
        config = replace(self.config, mode="paired", a=str(self.source / "A{id}.pdb"),
                         b=str(self.source / "B{id}.pdb"), ids="10,1")
        plan = prepare_batch(config)
        self.assertEqual([item.pair.identifier for item in plan.items], ["10", "1"])

    def test_folder_glob_and_regex_pairs_use_real_files(self):
        for options in (dict(match="glob", a=str(self.source / "A*.pdb"), b=str(self.source / "B*.pdb")),
                        dict(match="regex", a=str(self.source), b=str(self.source),
                             a_pattern=r"A(?P<id>\d+)\.pdb", b_pattern=r"B(\d+)\.pdb")):
            with self.subTest(match=options["match"]):
                plan = prepare_batch(replace(self.config, mode="paired", **options))
                self.assertEqual([item.pair.identifier for item in plan.items], ["1", "2", "10"])
                self.assertEqual(plan.items[1].pair.right, str(self.source / "B2.pdb"))

    def test_both_processed_structures_are_saved_by_default(self):
        self.assertTrue(BatchConfig().save_b)
        plan = prepare_batch(self.config)
        self.assertTrue(all(item.output_a and item.output_b for item in plan.items))

    def test_every_pair_has_distinct_temporary_names(self):
        plan = prepare_batch(self.config)
        names = [name for item in plan.items for name in (item.object_a, item.object_b, item.object_reference)]
        self.assertEqual(len(names), len(set(names)))

    def test_long_path_conversion_covers_final_output_and_report_names(self):
        from pymolplugins.targets import batch_sources
        with patch.object(batch_sources, "windows_long_path", wraps=batch_sources.windows_long_path) as convert:
            plan = prepare_batch(self.config)
        converted = [call.args[0] for call in convert.call_args_list]
        for path in (plan.items[0].output_a, plan.items[0].output_b, plan.report_path, plan.events_path):
            self.assertIn(path, converted)

    def test_csv_pairs_use_chosen_columns_and_relative_paths(self):
        csv = self.source / "scores.csv"
        csv.write_text("run,a,b,score\nz,A2.pdb,B2.pdb,0.2\ny,A1.pdb,B1.pdb,0.8\n", encoding="utf-8")
        config = replace(self.config, source="csv", mode="paired", a=str(csv),
                         a_column="a", id_column="run", b="b")
        plan = prepare_batch(config)
        self.assertEqual([item.pair.identifier for item in plan.items], ["z", "y"])
        self.assertEqual(plan.items[0].pair.right, str(self.source / "B2.pdb"))
        self.assertFalse((self.source / ".pymolplugins").exists())

    def test_csv_missing_ids_columns_and_files_are_errors(self):
        csv = self.source / "pairs.csv"
        csv.write_text("id,a,b\n1,A1.pdb,missing.pdb\n", encoding="utf-8")
        config = replace(self.config, source="csv", mode="paired", a=str(csv),
                         a_column="a", id_column="id", b="b")
        for change in ({"id_column": ""}, {"b": "wrong"}, {}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare_batch(replace(config, **change))
        self.assertFalse(Path(config.output).exists())

    def test_csv_duplicate_and_empty_ids_are_errors(self):
        csv = self.source / "pairs.csv"
        config = replace(self.config, source="csv", mode="paired", a=str(csv),
                         a_column="a", id_column="id", b="b")
        for rows in ("1,A1.pdb,B1.pdb\n1,A2.pdb,B2.pdb", ",A1.pdb,B1.pdb"):
            csv.write_text("id,a,b\n" + rows + "\n", encoding="utf-8")
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                prepare_batch(config)

    def test_csv_reference_exclusion_and_id_subset(self):
        csv = self.source / "pairs.csv"
        csv.write_text("id,a\nreference,ref.pdb\n10,A10.pdb\n1,A1.pdb\n", encoding="utf-8")
        config = replace(self.config, source="csv", a=str(csv), a_column="a", id_column="id")
        self.assertEqual([item.pair.identifier for item in prepare_batch(config).items], ["10", "1"])
        self.assertEqual([item.pair.identifier for item in prepare_batch(replace(config, ids="1")).items], ["1"])

    def test_csv_cancellation_reaches_parser(self):
        from pymolplugins.annotation.sources.csv_source import CsvDatasetSource
        csv = self.source / "pairs.csv"
        csv.write_text("id,a\n1,A1.pdb\n", encoding="utf-8")
        config = replace(self.config, source="csv", a=str(csv), a_column="a", id_column="id")
        event = threading.Event()
        with patch.object(CsvDatasetSource, "load", side_effect=InterruptedError("cancelled")) as load:
            with self.assertRaises(InterruptedError):
                prepare_batch(config, event)
        load.assert_called_once_with(cancel_event=event)

    def test_csv_changed_during_preparation_is_rejected(self):
        from pymolplugins.targets import batch_sources
        csv = self.source / "pairs.csv"
        csv.write_text("id,a\n1,A1.pdb\n", encoding="utf-8")
        config = replace(self.config, source="csv", a=str(csv), a_column="a", id_column="id")
        original = batch_sources._csv_inputs
        def changed(config, event):
            pairs = original(config, event)
            csv.write_text("id,a\n2,A2.pdb\n3,A10.pdb\n", encoding="utf-8")
            return pairs
        with patch.object(batch_sources, "_csv_inputs", side_effect=changed), self.assertRaisesRegex(ValueError, "CSV changed"):
            prepare_batch(config)

    def test_reference_alias_is_excluded_and_pair_alias_is_rejected(self):
        alias = self.source / "reference_alias.pdb"
        os.link(self.source / "ref.pdb", alias)
        plan = prepare_batch(replace(self.config, a=str(self.source)))
        self.assertFalse(any(item.pair.left in (str(alias), self.config.b) for item in plan.items))
        os.link(self.source / "A1.pdb", self.source / "same1.pdb")
        with self.assertRaisesRegex(ValueError, "same file"):
            prepare_batch(replace(self.config, mode="paired", a=str(self.source / "A{id}.pdb"),
                                  b=str(self.source / "same{id}.pdb"), ids="1"))

    def test_save_b_placeholder_requires_save_b_and_formats_are_retained(self):
        with self.assertRaisesRegex(ValueError, "output_B"):
            prepare_batch(replace(self.config, commands="print {output_B}", save_b=False))
        (self.source / "A.cif.gz").write_bytes(b"compressed fixture")
        plan = prepare_batch(replace(self.config, a=str(self.source / "A.cif.gz"), save_b=True,
                                     commands="print {output_A}, {output_B}"))
        self.assertTrue(plan.items[0].output_a.endswith("A.cif.gz"))
        self.assertTrue(plan.items[0].output_b.endswith("ref.pdb"))

    def test_unknown_template_and_same_output_directory_are_errors(self):
        for change in ({"commands": "align {wrong}, {B}"}, {"commands": "# comment"}, {"output": str(self.source)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare_batch(replace(self.config, **change))

    def test_input_changes_after_preview_prevent_start(self):
        runner = self.runner()
        (self.source / "A1.pdb").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            runner.start()
        self.assertEqual(self.cmd.objects, {"unrelated"})
        self.assertFalse(Path(self.config.output).exists())

    def test_preparation_cancellation(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            prepare_batch(self.config, event)

    def test_success_keeps_sources_and_writes_outputs_and_report(self):
        before = {path: path.read_bytes() for path in self.source.iterdir()}
        runner = self.runner(replace(self.config, save_b=True))
        runner.start()
        while runner.advance():
            pass
        self.assertEqual(runner.status, "completed")
        self.assertEqual(runner.report()["completed"], 3)
        self.assertEqual(len(list(Path(self.config.output).glob("*.pdb"))), 6)
        self.assertEqual(self.cmd.objects, {"unrelated"})
        self.assertEqual(before, {path: path.read_bytes() for path in self.source.iterdir()})
        self.assertEqual(json.loads(Path(runner.plan.report_path).read_text())["status"], "completed")
        commands = [call[1] for call in self.cmd.calls if call[0] == "execute"]
        self.assertEqual(commands, ["first 1", "second 1", "first 2", "second 2", "first 3", "second 3"])
        reference_loads = [call for call in self.cmd.calls if call[0] == "load" and call[1].endswith("ref.pdb")]
        self.assertEqual(len(reference_loads), 1)

    def test_stop_on_error_does_not_run_later_steps_or_pairs(self):
        runner = self.runner(fail="first 2")
        runner.start()
        while runner.advance():
            pass
        self.assertEqual([row["status"] for row in runner.results], ["completed", "failed", "not_run"])
        self.assertNotIn(("execute", "second 2"), self.cmd.calls)
        self.assertNotIn(("execute", "first 3"), self.cmd.calls)
        self.assertEqual(self.cmd.objects, {"unrelated"})

    def test_continue_on_error_resets_reference_and_runs_next_pair(self):
        runner = self.runner(replace(self.config, on_error="continue"), fail="first 2")
        runner.start()
        while runner.advance():
            pass
        self.assertEqual(runner.status, "partial_error")
        self.assertEqual([row["status"] for row in runner.results], ["completed", "failed", "completed"])
        self.assertEqual(self.cmd.objects, {"unrelated"})

    def test_cancel_retains_completed_results_and_cleans_temporaries(self):
        runner = self.runner()
        runner.start()
        while runner.index == 0:
            runner.advance()
        runner.advance()
        runner.cancel()
        self.assertFalse(runner.advance())
        self.assertEqual([row["status"] for row in runner.results], ["completed", "cancelled", "not_run"])
        self.assertEqual(self.cmd.objects, {"unrelated"})
        self.assertTrue(Path(runner.plan.items[0].output_a).is_file())

    def test_existing_output_is_never_overwritten(self):
        runner = self.runner()
        path = Path(runner.plan.items[0].output_a)
        path.parent.mkdir()
        path.write_text("user output", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            runner.start()
        self.assertEqual(path.read_text(), "user output")
        self.assertEqual(self.cmd.calls, [])

    def test_final_report_failure_keeps_completed_pair_states(self):
        runner = self.runner()
        real_open = open
        def report_failure(path, *args, **kwargs):
            if str(path) == runner.plan.report_path:
                raise OSError("report disk full")
            return real_open(path, *args, **kwargs)
        with patch("builtins.open", side_effect=report_failure):
            runner.start()
            while runner.advance():
                pass
        self.assertEqual(runner.status, "error")
        self.assertEqual((runner.completed, runner.failed), (3, 0))
        self.assertEqual([row["status"] for row in runner.results], ["completed"] * 3)
        self.assertIn("report disk full", runner.report()["error"])
        events = [json.loads(line) for line in Path(runner.plan.events_path).read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "aborted")
        self.assertEqual(self.cmd.objects, {"unrelated"})

    def test_event_log_failure_is_fatal_even_when_continue_is_selected(self):
        runner = self.runner(replace(self.config, on_error="continue"))
        runner.start()
        with patch.object(runner, "_event", side_effect=OSError("log disk full")):
            self.assertFalse(runner.advance())
        self.assertEqual(runner.status, "error")
        self.assertIn("log disk full", runner.error)
        self.assertEqual((runner.completed, runner.failed), (0, 1))
        self.assertEqual(self.cmd.objects, {"unrelated"})
        self.assertFalse(any(call[0] == "load" for call in self.cmd.calls))

    def test_cleanup_failure_releases_other_owned_objects_and_reports_leftovers(self):
        runner = self.runner(fail="first 1")
        delete = self.cmd.delete
        def fail_a(name):
            if name == runner.plan.items[0].object_a:
                raise RuntimeError("A could not be removed")
            delete(name)
        with patch.object(self.cmd, "delete", side_effect=fail_a):
            runner.start()
            while runner.advance():
                pass
        self.assertEqual(runner.status, "error")
        self.assertIn("A could not be removed", runner.error)
        self.assertEqual(runner.results[0]["error"], "synthetic command failure")
        self.assertEqual(self.cmd.objects, {"unrelated", runner.plan.items[0].object_a})
        self.assertEqual(runner.report()["owned_objects"], [runner.plan.items[0].object_a])

    def test_save_failure_removes_partial_file_and_marks_pair_failed(self):
        runner = self.runner()
        def save_failure(path, name, **kwargs):
            Path(path).write_text("partial data", encoding="utf-8")
            raise OSError("save disk full")
        with patch.object(self.cmd, "save", side_effect=save_failure):
            runner.start()
            while runner.advance():
                pass
        self.assertEqual(runner.status, "partial_error")
        self.assertEqual(runner.results[0]["failed_step"], "Save A")
        self.assertFalse(Path(runner.plan.items[0].output_a).exists())
        self.assertEqual(self.cmd.objects, {"unrelated"})

    def test_empty_structure_is_not_a_successful_output(self):
        runner = self.runner()
        def remove_a(cmd, command):
            cmd.objects.discard(runner.plan.items[0].object_a)
        runner.execute = remove_a
        runner.start()
        while runner.advance():
            pass
        self.assertEqual(runner.status, "partial_error")
        self.assertIn("empty structure", runner.results[0]["error"])
        self.assertEqual(runner.results[0]["outputs"], [])

    def test_report_path_collision_is_read_only(self):
        runner = self.runner()
        path = Path(runner.plan.events_path)
        path.parent.mkdir()
        path.write_text("existing report", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Report already exists"):
            runner.start()
        self.assertEqual(path.read_text(), "existing report")
        self.assertEqual(self.cmd.calls, [])

    def test_cancellation_before_first_step_does_not_load_anything(self):
        runner = self.runner()
        runner.start()
        runner.cancel()
        self.assertFalse(runner.advance())
        self.assertEqual(runner.status, "cancelled")
        self.assertEqual((runner.completed, runner.failed), (0, 0))
        self.assertEqual(self.cmd.calls, [])


if __name__ == "__main__":
    unittest.main()
