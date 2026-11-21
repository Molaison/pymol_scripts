"""Regression cases for score screening, stratified sampling and native CA matching."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import pymol2


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "data/helical_bundle.pdb"
CORE_PATH = "workflows/dataset_navigation/collections_core.py"
sys.path.insert(0, str(REPO))
from workflows.dataset_navigation import collections_core as core


def core_at(revision):
    blob = subprocess.check_output(["git", "show", f"{revision}:{CORE_PATH}"], cwd=REPO)
    module = types.ModuleType("baseline_collections_core")
    module.__file__ = str(REPO / CORE_PATH)
    exec(compile(blob, module.__file__, "exec"), vars(module))
    return module


class CollectionsSampling(unittest.TestCase):
    def test_rare_extreme_on_either_side_keeps_separate_stratum(self):
        specs = [{"name": "quality", "bins": 3}]
        for values in ([0.7] * 99 + [0.98], [0.7] + [0.98] * 99):
            with self.subTest(rare=min(values, key=values.count)):
                rows = [{"scores": {"quality": value}} for value in values]
                queues, _ = core.make_strata(rows, specs, seed=0)
                available = {queue[-1]["scores"]["quality"]: len(queue) for queue in queues}
                self.assertEqual(available, dict(Counter(values)))

    def test_low_ties_preserve_an_effective_interpolated_cut_for_later_rows(self):
        specs = [{"name": "quality", "bins": 3}]
        originals = [{"scores": {"quality": value}} for value in [0, 0, 0, 1, 2]]
        _, edges = core.make_strata(originals, specs, seed=0)
        self.assertTrue(any(0 < edge < 0.8 for edge in edges["quality"]))
        candidate = {"scores": {"quality": 0.8}}
        core.make_strata([*originals, candidate], specs, seed=0, saved_edges=edges)
        self.assertEqual(candidate["stratum"], originals[3]["stratum"])
        self.assertNotEqual(candidate["stratum"], originals[0]["stratum"])

    def test_extreme_finite_scores_keep_a_finite_cut_in_the_saved_collection(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            coords = cmd.get_coords("fixture", 1)
            cmd.save(str(root / "low.pdb"), "fixture")
            cmd.load_coords(coords * [1.8, 1.4, 1.35], "fixture", 1)
            cmd.save(str(root / "high.pdb"), "fixture")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": "low.pdb", "quality": -1e308},
                {"path": "high.pdb", "quality": 1e308},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality", "bins": 2}],
                      "count": 2, "seed": 8, "rmsd": 0.15, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            output = root / "collection"
            core.create_collection(config, output)

            def reject_nonfinite(value):
                self.fail(f"non-finite manifest token: {value}")

            manifest = json.loads((output / "01_collection.json").read_text(encoding="utf-8"),
                                  parse_constant=reject_nonfinite)
            self.assertEqual(manifest["quantile_edges"], {"quality": [0.0]})
            self.assertEqual(manifest["occupied_strata"], 2)
            self.assertEqual({row["id"]: row["stratum"] for row in manifest["samples"]},
                             {"low.pdb": [0], "high.pdb": [1]})

    def test_repeated_metric_keeps_independent_strata_and_saved_boundaries(self):
        specs = [{"name": "quality", "bins": 2}, {"name": "quality", "bins": 4}]
        rows = [{"scores": {"quality": score}} for score in range(12)]
        queues, edges = core.make_strata(rows, specs, seed=0)
        self.assertEqual(len(queues), 4)
        self.assertEqual(edges, [[5.5], [2.75, 5.5, 8.25]])
        self.assertEqual(rows[8]["stratum"], [1, 2])

        refreshed = [{"scores": {"quality": score}} for score in (0, 8, 100)]
        _, reused = core.make_strata(refreshed, specs, seed=0, saved_edges=edges)
        self.assertEqual(reused, edges)
        self.assertEqual(refreshed[1]["stratum"], [1, 2])
        core.make_strata(refreshed, specs, seed=0, saved_edges={"quality": edges[1]})
        self.assertEqual(refreshed[1]["stratum"], [2, 2])

    def test_replacement_uses_saved_strata_after_the_score_table_changes(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            coords = cmd.get_coords("fixture", 1)
            for name, scale in (("old", 1.0), ("alternate", 1.5), ("low1", 2.0),
                                ("low2", 2.2), ("low3", 2.4)):
                cmd.load_coords(coords * [scale, 1, 1], "fixture", 1)
                cmd.save(str(root / f"{name}.pdb"), "fixture")
            scores_path = root / "scores.json"
            scores = {"old": 0.92, "alternate": 0.85, "low1": 0.7, "low2": 0.7, "low3": 0.7}

            def write_scores(names):
                scores_path.write_text(json.dumps([
                    {"path": f"{name}.pdb", "quality": scores[name]} for name in names
                ]), encoding="utf-8")

            write_scores(["low1", "old"])
            config = {"scores": str(scores_path), "root": str(root),
                      "metrics": [{"name": "quality", "bins": 2}], "count": 1,
                      "seed": 1, "rmsd": 0.15, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            manifest = core.create_collection(config, root / "collection")
            self.assertEqual(manifest["samples"][0]["id"], "old.pdb")
            self.assertEqual(manifest["samples"][0]["stratum"], [1])
            write_scores(["old", "low1", "alternate", "low2", "low3"])
            edited = core.revise_collection(root / "collection/01_collection.json", {"action": "replace", "indices": [0]})
            self.assertEqual(edited["samples"][0]["id"], "alternate.pdb")
            self.assertEqual(edited["samples"][0]["stratum"], [1])
            self.assertEqual(edited["quantile_edges"], manifest["quantile_edges"])

    def test_scores_reject_rows_before_file_probes_and_keep_extension_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "retained.pdb")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": "rejected.pdb", "quality": 0.1},
                {"path": "invalid.pdb", "quality": "NaN"},
                {"path": "retained", "quality": 0.9},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality", "min": 0.8}]}
            probes = []
            is_file = Path.is_file

            def inspect(path):
                probes.append(path.name)
                return is_file(path)

            with patch.object(Path, "is_file", inspect):
                rows, skipped = core.prepare_rows(config)
            self.assertEqual(probes, ["retained", "retained.pdb"])
            self.assertEqual(rows[0]["path"], str((root / "retained.pdb").resolve()))
            self.assertEqual([item["row"] for item in skipped], [2, 3])

    def test_nonfinite_bounds_are_rejected_before_screening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "candidate.pdb")
            scores = root / "scores.csv"
            scores.write_text("path,quality\ncandidate.pdb,0.5\n", encoding="utf-8")
            for field in ("min", "max"):
                for value in (float("nan"), float("inf"), -float("inf")):
                    with self.subTest(field=field, value=value):
                        metric = {"name": "quality", field: value}
                        with self.assertRaisesRegex(ValueError, "有限"):
                            core.prepare_rows({"scores": str(scores), "metrics": [metric]})

    def test_each_bound_for_the_same_metric_is_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("low.pdb", "high.pdb"):
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.csv"
            scores.write_text("path,quality\nlow.pdb,0.2\nhigh.pdb,0.9\n", encoding="utf-8")
            metrics = [{"name": "quality", "max": 0.5}, {"name": "quality", "min": 0.1}]
            rows, skipped = core.prepare_rows({"scores": str(scores), "metrics": metrics})
            self.assertEqual([row["id"] for row in rows], ["low.pdb"])
            self.assertEqual([item["row"] for item in skipped], [3])

    def test_exported_table_roundtrips_repeated_and_metadata_named_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("low.pdb", "high.pdb"):
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.csv"
            scores.write_text(
                "path,id,quality,nearest_rmsd,score_id\n"
                "low.pdb,17,0.2,0.6,0.75\n"
                "high.pdb,19,0.9,0.3,0.8\n",
                encoding="utf-8",
            )
            metrics = [
                {"name": "quality", "max": 0.5}, {"name": "quality", "min": 0.1},
                {"name": "id"}, {"name": "nearest_rmsd"}, {"name": "score_id"},
            ]
            config = {"scores": str(scores), "metrics": metrics, "count": 1, "seed": 1,
                      "orientation": "pca", "target": "polymer.protein", "binder": "polymer.protein"}
            output = root / "collection"
            manifest = core.create_collection(config, output)
            self.assertEqual(manifest["quantile_edges"], [[] for _ in metrics])
            rows = core.read_scores(output / "02_samples.csv")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["path"], str((root / "low.pdb").resolve()))
            self.assertEqual(rows[0]["id"], "17")
            self.assertEqual(rows[0]["quality"], "0.2")
            self.assertEqual(rows[0]["score_id"], "0.75")
            self.assertEqual(rows[0]["score_score_id"], "17.0")
            self.assertEqual(rows[0]["score_nearest_rmsd"], "0.6")

    def test_legacy_export_repeats_only_identical_metric_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "low.pdb")
            scores = root / "02_samples.csv"
            header = "id,path,stratum,nearest_rmsd,quality,quality\n"
            same = "legacy,low.pdb,1/1,,0.2,0.2\n"
            scores.write_text(header + same, encoding="utf-8-sig")
            rows, skipped = core.prepare_rows({"scores": str(scores), "metrics": [{"name": "quality"}]})
            self.assertEqual([row["id"] for row in rows], ["legacy"])
            self.assertEqual(rows[0]["scores"]["quality"], 0.2)
            self.assertFalse(skipped)

            scores.write_text(header + same + "conflict,low.pdb,1/1,,0.2,0.95\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "重复"):
                core.read_scores(scores)

    def test_duplicate_metric_headers_are_rejected(self):
        fixtures = {
            "scores.csv": "path,quality,quality\nlow.pdb,0.1,0.95\n",
            "scores.tsv": "path\tquality\tquality\nlow.pdb\t0.1\t0.95\n",
            "scores.sc": "SCORE: quality quality description\nSCORE: 0.1 0.95 low\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in fixtures.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(content, encoding="utf-8-sig")
                    with self.assertRaisesRegex(ValueError, "重复"):
                        core.read_scores(path)

    def test_delimited_scores_keep_missing_and_extra_field_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scores.csv"
            path.write_text("path,quality,pae\nfirst\n\nsecond,0.9,4,extra\n", encoding="utf-8-sig")
            self.assertEqual(core.read_scores(path), [
                {"path": "first", "quality": None, "pae": None},
                {"path": "second", "quality": "0.9", "pae": "4", None: ["extra"]},
            ])
            path.write_text("path,quality\nfirst,0.9,extra\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                core.read_scores(path)

    def test_quoted_structure_paths_and_ids_preserve_line_separators(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            intended = root / "a\u2028b.pdb"
            cmd.fragment("ala", "target")
            cmd.fragment("gly", "other")
            cmd.save(str(intended), "target")
            cmd.save(str(root / "ab.pdb"), "other")
            for suffix, delimiter in (("csv", ","), ("tsv", "\t")):
                with self.subTest(suffix=suffix):
                    path = root / f"scores.{suffix}"
                    with path.open("w", newline="", encoding="utf-8-sig") as stream:
                        writer = csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL)
                        writer.writerow(["path", "id", "quality"])
                        writer.writerow([intended.name, "first\r\nsecond\nthird", "0.9"])
                    config = {"scores": str(path), "metrics": [{"name": "quality"}]}
                    rows, skipped = core.prepare_rows(config)
                    self.assertEqual(len(rows), 1)
                    self.assertFalse(skipped)
                    row = rows[0]
                    self.assertEqual(Path(row["path"]), intended)
                    self.assertEqual(row["id"], "first\r\nsecond\nthird")
                    cmd.load(row["path"], "selected")
                    self.assertEqual(cmd.count_atoms("selected"), cmd.count_atoms("target"))
                    cmd.delete("selected")

    def test_json_duplicate_keys_cannot_override_screening_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("low.pdb", "high.pdb"):
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.json"
            config = {"scores": str(scores), "metrics": [{"name": "quality", "max": 0.5}]}
            scores.write_text('[{"path":"low.pdb","quality":0.2},'
                              '{"path":"high.pdb","quality":0.9}]', encoding="utf-8")
            rows, _ = core.prepare_rows(config)
            self.assertEqual([row["id"] for row in rows], ["low.pdb"])
            scores.write_text('[{"path":"low.pdb","quality":0.2,"quality":0.95},'
                              '{"path":"high.pdb","quality":0.9,"quality":0.2}]', encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "quality"):
                core.prepare_rows(config)

    def test_out_of_range_json_integer_does_not_abort_screening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "retained.pdb")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": "invalid.pdb", "quality": 10 ** 400},
                {"path": "retained.pdb", "quality": 0.9},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}]}
            rows, skipped = core.prepare_rows(config)
            self.assertEqual([row["id"] for row in rows], ["retained.pdb"])
            self.assertEqual([item["row"] for item in skipped], [2])

    def test_ca_reuse_across_pairs_preserves_decision_after_reloading_candidate(self):
        with pymol2.PyMOL() as instance:
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "col_candidate")
            for index in range(12):
                cmd.create(f"col_keep_{index}", "col_candidate")
            coords = cmd.get_coords("col_candidate", 1)
            cmd.load_coords(coords * [1.8, 1.4, 1.35], "col_candidate", 1)
            selected = [{} for _ in range(12)]
            config = {"diversity": "shape", "diversity_selection": "polymer.protein", "rmsd": 0.1}
            with patch.object(cmd, "get_model", wraps=cmd.get_model) as extraction:
                self.assertEqual(core.check_diversity(cmd, {}, config, selected), "accepted")
            self.assertEqual(extraction.call_count, 13)
            cmd.delete("col_candidate")
            cmd.load(str(FIXTURE), "col_candidate")
            self.assertEqual(core.check_diversity(cmd, {}, config, selected), "duplicate")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Run against an immutable Git revision")
    options, remaining = parser.parse_known_args()
    if options.revision:
        core = core_at(options.revision)
    unittest.main(argv=[__file__, *remaining], verbosity=2)
