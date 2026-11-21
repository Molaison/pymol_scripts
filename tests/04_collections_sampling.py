"""Regression cases for score screening, stratified sampling and native CA matching."""

import argparse
from collections import Counter
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
