import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pymolplugins.annotation.review_file import read_review_file, write_review_override
from pymolplugins.annotation.sources.csv_source import CsvDatasetSource, CsvSourceError, read_csv_header
from pymolplugins.annotation.path_resolver import PathMappingRule, PathResolver
from pymolplugins.annotation.mapping_store import validate_mapping_profile
from tests.annotation import test_structure_annotator_core as core


class ReviewImportTests(unittest.TestCase):
    setUp = core.StructureAnnotatorCoreTests.setUp
    tearDown = core.StructureAnnotatorCoreTests.tearDown

    def fixture(self, root, *, delimiter=",", suffix="csv"):
        directory = Path(root)
        for name in ("a.pdb", "b.pdb"):
            core.StructureAnnotatorCoreTests._write_pdb(directory / name, name)
        table = directory / ("sum_scores." + suffix)
        with table.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(["structure_path", "absolute_path", "label", "score1", "score2"])
            writer.writerow(["a.pdb", str(directory / "a.pdb"), "unusual interface", "1.2", "4"])
            writer.writerow(["b.pdb", str(directory / "b.pdb"), "", "0.8", "2"])
        config = {"path_column": "structure_path", "display_columns": ["score2", "score1"],
                  "label_column": "label", "step_cmd": "show cartoon, {object}\nzoom {object}"}
        (directory / "review.json").write_text(json.dumps(config), encoding="utf-8")
        return table, config

    def test_auto_preview_and_load_use_review_without_source_writes(self):
        with tempfile.TemporaryDirectory() as root:
            table, config = self.fixture(root)
            originals = {path: path.read_bytes() for path in Path(root).iterdir()}
            preview = self.module.sa_csv_preview(str(table))
            self.assertEqual(preview["display_columns"], config["display_columns"])
            self.assertEqual(preview["rows"][0]["display_name"], "unusual interface")
            self.assertIsNone(self.module.STATE.dataset)
            self.assertFalse((Path(root) / ".pymolplugins").exists())
            loaded = self.module.sa_load_csv(str(table))
            self.assertEqual(loaded["status"], "loaded")
            self.assertEqual(self.module.STATE.step_cmd, config["step_cmd"])
            self.assertEqual(self.module.STATE.display_columns, ["score2", "score1"])
            self.assertEqual(self.module.STATE.label_column, "label")
            self.assertEqual(self.module.STATE.entries[1].display_name, "b.pdb")
            self.assertEqual(self.module.STATE.row_annotation_store.snapshot(), {})
            self.assertTrue(self.fake.commands[0].startswith("show cartoon, sa_"))
            self.assertFalse((Path(root) / "review.override.json").exists())
            for path, before in originals.items():
                self.assertEqual(path.read_bytes(), before)

    def test_user_edits_save_separate_override_and_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            table, config = self.fixture(root)
            self.module.sa_load_csv(str(table))
            self.module.STATE.step_cmd = "color cyan, {object}"
            self.module.STATE.display_columns = ["score1"]
            self.module.sa_save_state()
            self.assertEqual(self.module.latest_config_snapshot()["config"]["label_column"], "label")
            override = json.loads((Path(root) / "review.override.json").read_text())
            self.assertEqual(override["display_columns"], ["score1"])
            self.assertEqual(json.loads((Path(root) / "review.json").read_text()), config)
            self.module.STATE = self.module.AnnotatorState(preload_enabled=False)
            self.module.sa_load_csv(str(table))
            self.assertEqual(self.module.STATE.step_cmd, "color cyan, {object}")
            self.assertEqual(self.module.STATE.display_columns, ["score1"])

    def test_partial_override_and_explicit_arguments_take_precedence(self):
        with tempfile.TemporaryDirectory() as root:
            table, config = self.fixture(root)
            write_review_override(str(table), {"display_columns": [], "step_cmd": ""})
            loaded = self.module.sa_load_csv(str(table), path_column="absolute_path", display_columns=["score1"])
            self.assertEqual(loaded["status"], "loaded")
            self.assertEqual(self.module.STATE.path_column, "absolute_path")
            self.assertEqual(self.module.STATE.display_columns, ["score1"])
            self.assertEqual(self.module.STATE.step_cmd, "")
            self.assertEqual(self.module.STATE.label_column, "label")
            self.assertEqual(read_review_file(str(table)).config["path_column"], "absolute_path")

    def test_tsv_with_ragged_rows_discovers_columns_and_imports(self):
        with tempfile.TemporaryDirectory() as root:
            table, config = self.fixture(root, delimiter="\t", suffix="tsv")
            with table.open("a", encoding="utf-8") as handle:
                handle.write("b.pdb\n")
            self.assertEqual(read_csv_header(str(table))["delimiter"], "\t")
            self.assertEqual(self.module._prepare_csv_header(str(table))["review_config"], config)
            loaded = self.module.sa_load_csv(str(table))
            self.assertEqual(loaded["status"], "loaded")
            self.assertEqual(self.module.STATE.csv_delimiter, "\t")
            self.assertEqual(len(self.module.STATE.entries), 3)

    def test_labels_do_not_change_stable_ids_or_require_uniqueness(self):
        with tempfile.TemporaryDirectory() as root:
            table, _ = self.fixture(root)
            with table.open(newline="") as handle:
                rows = list(csv.reader(handle))
            rows[2][2] = rows[1][2]
            with table.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            plain = CsvDatasetSource(str(table), "structure_path").load()
            source = CsvDatasetSource(str(table), "structure_path", label_column="label")
            labeled = CsvDatasetSource.from_config(source.config).load()
            self.assertEqual([entry.id for entry in plain.entries], [entry.id for entry in labeled.entries])
            self.assertEqual(labeled.entries[0].display_name, labeled.entries[1].display_name)
            self.assertNotEqual(labeled.entries[0].id, labeled.entries[1].id)
            self.assertEqual(plain.entries[0].metadata, labeled.entries[0].metadata)
            with self.assertRaisesRegex(CsvSourceError, "label_column.*not an exact"):
                CsvDatasetSource(str(table), "structure_path", label_column="missing").load()

    def test_bad_review_fails_before_scene_or_state_change(self):
        with tempfile.TemporaryDirectory() as root:
            table, _ = self.fixture(root)
            self.module.sa_load_csv(str(table))
            previous = self.module.STATE.dataset
            enabled = set(self.fake.enabled)
            for text in ('{broken', '[]', '{"display_columns": ["absent"]}', '{"step_cmd": "zoom {missing}"}'):
                (Path(root) / "review.json").write_text(text)
                self.assertIsNone(self.module.sa_load_csv(str(table)))
                self.assertIs(self.module.STATE.dataset, previous)
                self.assertEqual(self.fake.enabled, enabled)
                self.assertFalse((Path(root) / "review.override.json").exists())

    def test_failed_override_replace_preserves_both_documents(self):
        with tempfile.TemporaryDirectory() as root:
            table, config = self.fixture(root)
            path = Path(root) / "review.override.json"
            path.write_text('{"step_cmd": ""}')
            original = path.read_bytes()
            with patch("pymolplugins.annotation.review_file.os.replace", side_effect=PermissionError("read only")):
                with self.assertRaises(PermissionError):
                    write_review_override(str(table), config)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(Path(root).glob(".review.override.*.tmp")))
            self.assertEqual(json.loads((Path(root) / "review.json").read_text()), config)

    def test_background_state_save_uses_prepared_path_without_directory_probe(self):
        with tempfile.TemporaryDirectory() as root:
            table, _ = self.fixture(root)
            self.module.sa_load_csv(str(table))
            self.module.STATE.step_cmd = ""
            with patch.object(self.module, "dataset_state_path", side_effect=AssertionError("UI probed remote directory")), patch.object(self.module, "submit_write") as submit:
                self.assertEqual(self.module._save_current_dataset_state(background=True), self.module.STATE.state_path)
                self.assertEqual(submit.call_count, 1)

    def test_folder_preparation_and_cancel_do_not_touch_live_state(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root)
            context = self.module._capture_csv_runtime_context()
            with patch.object(self.module, "STATE", object()):
                plan = self.module._prepare_directory_load(root, "", context)
            self.assertEqual(len(plan.entries), 2)
            self.assertEqual(self.fake.loaded_paths, {})
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(self.module._CsvPreparationCancelled):
                self.module._prepare_directory_load(root, "", context, cancel_event=cancelled)
            self.module._commit_prepared_directory_load(plan)
            self.assertEqual(len(self.module.STATE.entries), 2)

    def test_drive_root_shorthand_is_normalized_and_persistable(self):
        rule = PathMappingRule("/", "K:")
        profile = validate_mapping_profile({"name": "root", "rules": [rule.to_dict()], "source_format": "posix", "target_format": "windows"})
        result = PathResolver.from_profile(profile).resolve("/010_rna/summary.tsv", require_exists=False)
        self.assertEqual(profile["rules"][0]["target_prefix"], "K:\\")
        self.assertEqual(result.resolved_path, "K:\\010_rna\\summary.tsv")
        with self.assertRaises(ValueError):
            PathMappingRule("/", "K:relative")


if __name__ == "__main__":
    unittest.main()
