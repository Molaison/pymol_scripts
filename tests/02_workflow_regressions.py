"""Run the standalone workflow regressions with the Python bundled with PyMOL."""

import gzip
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import pymol
import pymol2

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "data/helical_bundle.pdb"
sys.path.insert(0, str(REPO))
from workflows.dataset_navigation import collections_core as core


class WorkflowRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        instance = pymol2.PyMOL()
        instance.start()
        self.addCleanup(instance.stop)
        self.cmd = instance.cmd
        self.cmd.set("max_threads", 1)

    def annotator(self):
        with patch.object(pymol, "cmd", self.cmd):
            scope = runpy.run_path(str(REPO / "plugins/structure_annotator.py"))["sa_load_dir"].__globals__
        state = scope["STATE"]
        state.step_cmd = ""
        state.auto_advance = False
        state.keep_previous = True
        return scope, state

    def test_annotator_switches_same_named_model_before_labeling(self):
        old_file = self.root / "old/same.pdb"
        new_file = self.root / "new/same.pdb"
        old_file.parent.mkdir()
        new_file.parent.mkdir()
        shutil.copyfile(FIXTURE, old_file)
        self.cmd.load(str(old_file), "sa_00001_same")  # Existing user object with the default name.
        self.cmd.create("shifted", "sa_00001_same")
        self.cmd.translate([100, 0, 0], "shifted", state=1, camera=0)
        self.cmd.save(str(new_file), "shifted")
        old_coords = self.cmd.get_coords("sa_00001_same", 1)
        new_coords = self.cmd.get_coords("shifted", 1)
        self.cmd.delete("shifted")

        annotator, state = self.annotator()
        annotator["sa_load_dir"](str(old_file.parent))
        self.assertNotEqual(state.current_object, "sa_00001_same")
        np.testing.assert_allclose(self.cmd.get_coords(state.current_object, 1), old_coords)
        annotator["sa_load_dir"](str(new_file.parent))
        np.testing.assert_allclose(self.cmd.get_coords(state.current_object, 1), new_coords, atol=0.001)
        self.assertCountEqual(self.cmd.get_names("objects"), ["sa_00001_same", state.current_object])
        annotator["sa_annotate"]("Good")
        self.assertNotIn("quality", annotator["read_structure_annotations"](str(old_file)))
        self.assertEqual(annotator["read_structure_annotations"](str(new_file))["quality"], "Good")

    def test_annotator_reads_each_file_once_when_skipping_labels(self):
        plain = FIXTURE.read_bytes()
        (self.root / "one.pdb.gz").write_bytes(gzip.compress(plain))
        (self.root / "two.pdb.gz").write_bytes(gzip.compress(plain))
        annotated = self.root / "already_annotated.pdb"
        annotated.write_bytes(plain)
        annotator, state = self.annotator()
        annotator["annotate_structure_file"](str(annotated), "quality", "Good")
        state.skip_annotated = True
        state.preload_enabled = False
        state.background_decompress_gz = False

        inspect = unittest.mock.Mock(wraps=annotator["has_annotation_fast"])
        with patch.dict(annotator, {"has_annotation_fast": inspect}):
            annotator["sa_load_dir"](str(self.root))
        reads = [Path(call.args[0]).name for call in inspect.call_args_list]
        self.assertCountEqual(reads, ["already_annotated.pdb", "one.pdb.gz", "two.pdb.gz"])
        self.assertCountEqual([Path(path).name for path in state.files], ["one.pdb.gz", "two.pdb.gz"])
        self.assertEqual(state.annotated_indices, set())

    def test_export_blocks_stale_pose_until_reapplied(self):
        source = self.root / "sample.pdb"
        shutil.copyfile(FIXTURE, source)
        scores = self.root / "scores.csv"
        scores.write_text("path,quality\nsample.pdb,1\n", encoding="utf-8")
        config = {"scores": str(scores), "root": str(self.root), "path_column": "path",
                  "metrics": [{"name": "quality", "bins": 1}], "count": 1, "orientation": "pca",
                  "binder": "polymer.protein", "target": "polymer.protein"}
        output = self.root / "collection"
        manifest = core.create_collection(config, output)
        path = output / "01_collection.json"
        baseline = self.export(path)
        image = Path(baseline["image"])
        original_png = image.read_bytes()

        legacy = self.root / "legacy"
        legacy.mkdir()
        legacy_path = legacy / path.name
        del manifest["samples"][0]["coordinate_signature"]
        core.write_json(legacy_path, manifest)
        self.assertTrue(self.export(legacy_path)["ok"])

        rotation = np.asarray(manifest["samples"][0]["matrix"])[:3, :3]
        self.cmd.load(str(source), "moved")
        self.cmd.translate(list(rotation[0] * 250), "moved", state=1, camera=0)
        self.cmd.save(str(source), "moved")
        for stale_path in (path, legacy_path):
            failed = self.export(stale_path, retry=True)
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["error"], core.STALE_POSE)
            self.assertFalse(core.current_images(core.read_json(stale_path), stale_path.parent / "images"))
        self.assertEqual(image.read_bytes(), original_png)

        core.revise_collection(path, {"action": "orient", "config": {}})
        self.assertTrue(self.export(path, retry=True)["ok"])
        with Image.open(image) as rendered:
            painted = np.count_nonzero((np.asarray(rendered.convert("RGB")) < 230).any(axis=2))
        self.assertGreater(painted, 50)

    def export(self, path, retry=False):
        return core.export_collection(path, workers=1, width=192, height=144,
                                      retry_failed=retry)["results"][0]

    def test_shape_duplicate_stops_after_first_match(self):
        self.cmd.load(str(FIXTURE), "col_candidate")
        for index in range(12):
            self.cmd.create(f"col_keep_{index}", "col_candidate", source_state=1, target_state=1)
        self.cmd.translate([10, 20, 30], "col_keep_0", state=1, camera=0)
        config = {"diversity": "shape", "diversity_selection": "polymer.protein", "rmsd": 2}
        with patch.object(core, "structural_distance", wraps=core.structural_distance) as compare:
            decision = core.check_diversity(self.cmd, {}, config, [{} for _ in range(12)])
        self.assertEqual(decision, "duplicate")
        compare.assert_called_once()
        self.assertEqual(compare.call_args.args[:4], (self.cmd, "col_candidate", "col_keep_0", config))


if __name__ == "__main__":
    unittest.main(verbosity=2)
