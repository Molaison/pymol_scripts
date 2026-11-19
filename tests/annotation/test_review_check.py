import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from pymolplugins.annotation.path_resolver import PathResolver
from pymolplugins.annotation.review_check import check_bundle


class BundleCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.table = self.root / "scores.csv"
        (self.root / "candidate.pdb").write_text("fixture")
        self.config = {"path_column": "structure_path", "steps": [
            {"type": "object", "name": "reference", "path_column": "ref", "fixed": True},
            {"type": "object", "name": "candidate"},
            {"type": "commands"}], "step_cmd": "align {candidate}, {reference}"}
        self.rows = [
            ["a", "candidate.pdb", "candidate.pdb", "2"],
            ["b", "candidate.pdb", "candidate.pdb", ""],
        ]

    def write(self):
        with self.table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "structure_path", "ref", "score"])
            writer.writerows(self.rows)
        (self.root / "review.json").write_text(json.dumps(self.config))

    def check(self):
        resolver = PathResolver(rules=[], target_format="custom")
        def resolve(dataset):
            return replace(dataset, entries=tuple(entry.with_resolution(
                resolver.resolve(entry.source_path, source_base=entry.source_base, require_exists=True))
                for entry in dataset.entries))
        return check_bundle(str(self.table), id_column="id", resolve_dataset=resolve)

    def test_valid_bundle_reports_paths_filter_hits_without_writes(self):
        self.config["filter_text"] = 'where:text_search("candidate.pdb") and score > 1'
        self.config["filter_presets"] = [{"name": "missing", "expression": "score is None"}]
        self.write()
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        result = self.check()
        self.assertEqual(result["errors"], 0, result)
        self.assertEqual(result["paths_checked"], 4)
        self.assertEqual(result["filters"][0]["matches"], 1)
        self.assertEqual(result["filters"][-1]["matches"], 1)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_multiple_categories_are_reported_together(self):
        self.rows[1] = ["a", "missing.pdb", "", "3"]
        self.config["display_columns"] = ["absent"]
        self.config["filter_presets"] = [{"name": "bad", "expression": "missing > 1"}]
        self.write()
        (self.root / "review.override.json").write_text(json.dumps({"step_cmd": "zoom {reference}"}))
        result = self.check()
        findings = "\n".join(item["location"] + ": " + item["message"] for item in result["issues"])
        for text in ("duplicate id", "missing.pdb", "absent", "step 3", "filter:bad", "Override replaces"):
            self.assertIn(text, findings)
        self.assertEqual(result["status"], "failed")

    def test_bad_step_order_does_not_skip_candidate_paths(self):
        self.config["steps"].reverse()
        self.rows[0][1] = "missing.pdb"
        self.write()
        result = self.check()
        self.assertTrue(any(item["location"] == "steps/order" for item in result["issues"]))
        self.assertEqual(result["paths_checked"], 2)


if __name__ == "__main__":
    unittest.main()
