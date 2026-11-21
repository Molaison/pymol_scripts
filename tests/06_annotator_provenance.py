"""Check that Structure Annotator labels the native PyMOL object and its source file."""

import argparse
import csv
import gzip
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymol
import pymol2


REPO = Path(__file__).resolve().parents[1]
SCRIPT = "plugins/structure_annotator.py"
FIXTURE = REPO / "data/helical_bundle.pdb"
REVISION = None


class AnnotatorProvenance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        instance = pymol2.PyMOL()
        instance.start()
        self.addCleanup(instance.stop)
        self.cmd = instance.cmd
        self.cmd.set("max_threads", 1)
        script = REPO / SCRIPT
        if REVISION:
            script = self.root / "structure_annotator.py"
            script.write_bytes(subprocess.check_output(["git", "show", f"{REVISION}:{SCRIPT}"], cwd=REPO))
        with patch.object(pymol, "cmd", self.cmd):
            self.annotator = runpy.run_path(str(script))["sa_load_dir"].__globals__
        self.state = self.annotator["STATE"]
        self.state.step_cmd = ""
        self.state.preload_enabled = False
        self.state.auto_advance = False
        self.state.background_decompress_gz = False

    def test_gzip_preference_changes_only_when_dataset_is_reloaded(self):
        plain, compressed = self.root / "model.pdb", self.root / "model.pdb.gz"
        shutil.copyfile(FIXTURE, plain)
        with gzip.open(compressed, "wb") as stream:
            stream.write(FIXTURE.read_bytes())
        self.state.prefer_gz = False
        self.annotator["sa_load_dir"](str(self.root))
        self.assertEqual(Path(self.state.current_file()), plain)

        self.state.prefer_gz = True
        self.annotator["sa_annotate"]("Needs review")
        reader = self.annotator["read_pdb_annotations"]
        self.assertEqual(reader(str(plain)).get("quality"), "Needs review")
        self.assertNotIn("quality", reader(str(compressed)))
        with Path(self.annotator["sa_export_csv"]()).open(newline="", encoding="utf-8") as report:
            row = next(csv.DictReader(report))
        self.assertEqual((row["path"], row["quality"]), (str(plain), "Needs review"))
        self.annotator["sa_annotate"]("Reviewed")
        self.annotator["sa_undo"]()
        self.assertEqual(reader(str(plain)).get("quality"), "Needs review")

        self.annotator["sa_load_dir"](str(self.root))
        self.assertEqual(Path(self.state.current_file()), compressed)
        self.annotator["sa_annotate"]("Compressed")
        self.assertEqual(reader(str(compressed)).get("quality"), "Compressed")
        self.assertEqual(reader(str(plain)).get("quality"), "Needs review")

    def test_cif_multiword_annotation_survives_csv_export_and_undo(self):
        self.cmd.load(str(FIXTURE), "template")
        path = self.root / "review.cif"
        self.cmd.save(str(path), "template")
        self.annotator["sa_load_dir"](str(self.root))

        self.annotator["sa_annotate"]("Needs review")
        with Path(self.annotator["sa_export_csv"]()).open(newline="", encoding="utf-8") as report:
            self.assertEqual(next(csv.DictReader(report))["quality"], "Needs review")
        self.annotator["sa_annotate"]("Reviewed")
        self.annotator["sa_undo"]()
        reader = self.annotator["read_structure_annotations"]
        self.assertEqual(reader(str(path))["quality"], "Needs review")

    def test_same_name_manual_replacement_cannot_be_annotated_or_deleted(self):
        source = self.root / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        self.annotator["sa_load_dir"](str(self.root))
        original = self.state.current_object
        self.cmd.delete(original)
        self.cmd.fragment("ala", original)
        manual_coords = self.cmd.get_coords(original, 1)

        reader = self.annotator["read_pdb_annotations"]
        self.annotator["sa_annotate"]("Wrong model")
        self.assertNotIn("quality", reader(str(source)))
        self.annotator["sa_load_dir"](str(self.root))
        self.assertNotEqual(self.state.current_object, original)
        np.testing.assert_allclose(self.cmd.get_coords(original, 1), manual_coords)
        self.annotator["sa_annotate"]("Right model")
        self.assertEqual(reader(str(source)).get("quality"), "Right model")

    def test_swapping_two_managed_names_does_not_swap_file_ownership(self):
        for name in ("first", "second"):
            shutil.copyfile(FIXTURE, self.root / f"{name}.pdb")
        self.state.keep_previous = True
        self.annotator["sa_load_dir"](str(self.root))
        original_first = self.state.current_object
        self.annotator["sa_next"]()
        original_second = self.state.current_object
        self.cmd.set_name(original_first, "spare_model")
        self.cmd.set_name(original_second, original_first)
        self.cmd.set_name("spare_model", original_second)
        self.cmd.disable(original_first)
        self.cmd.enable(original_second)

        reader = self.annotator["read_pdb_annotations"]
        second = str(self.root / "second.pdb")
        self.annotator["sa_annotate"]("Mislabeled")
        self.assertNotIn("quality", reader(second))
        self.annotator["sa_goto"](2)
        self.assertEqual(self.state.current_object, original_first)
        self.annotator["sa_annotate"]("Correct")
        self.assertEqual(reader(second).get("quality"), "Correct")

    def test_preloaded_object_preserves_ownership_and_evicts_previous_model(self):
        for name in ("first", "second", "third"):
            shutil.copyfile(FIXTURE, self.root / f"{name}.pdb")
        self.state.preload_enabled = True
        self.state.preload_ahead = 1
        self.annotator["sa_load_dir"](str(self.root))
        first = self.state.current_object
        self.annotator["_process_preload_tick"]()
        self.assertEqual(len(self.cmd.get_names("objects")), 2)
        self.annotator["sa_next"]()
        self.assertNotIn(first, self.cmd.get_names("objects"))
        self.assertEqual(self.cmd.get_names("objects", enabled_only=1), [self.state.current_object])
        self.annotator["sa_annotate"]("Preloaded")
        self.assertEqual(self.annotator["read_pdb_annotations"](str(self.root / "second.pdb"))["quality"],
                         "Preloaded")

    def test_legacy_pdb_markers_beyond_the_old_probe_boundary_are_still_skipped(self):
        self.cmd.load(str(FIXTURE), "template")
        for index, chain in enumerate("ABCDEFGHIJ"):
            name = f"part{index}"
            self.cmd.create(name, "template")
            self.cmd.alter(name, f"chain={chain!r}")
        self.cmd.create("large_model", "part*")
        scratch = self.root / "large_input.pdb"
        self.cmd.save(str(scratch), "large_model")
        body, separator, end = scratch.read_bytes().rpartition(b"END\n")
        self.assertTrue(separator and len(body) > 512 * 1024)
        scratch.unlink()
        marker = b"REMARK 950 PYMOL_ANNOTATOR quality=Already reviewed ts=2026-11-21T00:00:00\n"
        legacy = body + marker + separator + end
        self.state.skip_annotated = True
        for suffix in (".pdb", ".pdb.gz"):
            with self.subTest(suffix=suffix):
                dataset = self.root / f"dataset{suffix}"
                dataset.mkdir()
                labelled = dataset / f"labelled{suffix}"
                labelled.write_bytes(gzip.compress(legacy) if suffix.endswith(".gz") else legacy)
                shutil.copyfile(FIXTURE, dataset / "unlabelled.pdb")
                self.annotator["sa_load_dir"](str(dataset))
                self.assertEqual([Path(path).name for path in self.state.files], ["unlabelled.pdb"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Test the Annotator script from an immutable Git object")
    options, remaining = parser.parse_known_args()
    REVISION = options.revision
    unittest.main(argv=[__file__, *remaining], verbosity=2)
