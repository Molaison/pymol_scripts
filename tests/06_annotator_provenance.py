"""Check that Structure Annotator labels the native PyMOL object and its source file."""

import argparse
import builtins
from contextlib import contextmanager
import csv
import errno
import gzip
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
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


def fail_after_prefix(open_file, attempts):
    def open_with_failure(path, mode, *args, **kwargs):
        stream = open_file(path, mode, *args, **kwargs)
        if mode not in ("wt", "r+b"):
            return stream

        @contextmanager
        def interrupted_write():
            with stream:
                class PartialWriter:
                    remaining = 64

                    def __getattr__(self, name):
                        return getattr(stream, name)

                    def write(self, data):
                        written = stream.write(data[:self.remaining])
                        self.remaining -= written
                        if self.remaining == 0:
                            stream.flush()
                            attempts.append(64)
                            raise OSError(errno.ENOSPC, "simulated disk full after a partial write")
                        return written

                yield PartialWriter()

        return interrupted_write()

    return open_with_failure


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

    def replacement_structure(self):
        self.cmd.fragment("ala", "replacement_fixture")
        path = self.root / "replacement.pdb"
        self.cmd.save(str(path), "replacement_fixture")
        atom_count = self.cmd.count_atoms("replacement_fixture")
        self.cmd.delete("replacement_fixture")
        return path, atom_count

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

    def test_failed_annotation_preserves_source_and_allows_retry(self):
        for suffix in (".pdb", ".pdb.gz"):
            with self.subTest(suffix=suffix):
                dataset = self.root / f"dataset{suffix}"
                dataset.mkdir()
                source = dataset / f"model{suffix}"
                data = FIXTURE.read_bytes()
                original = gzip.compress(data) if suffix.endswith(".gz") else data
                source.write_bytes(original)
                self.annotator["sa_load_dir"](str(dataset))

                attempts = []
                with patch.dict(self.annotator, {"open": fail_after_prefix(builtins.open, attempts)}), \
                        patch.object(gzip, "open", fail_after_prefix(gzip.open, attempts)):
                    self.annotator["sa_annotate"]("Reviewed")
                self.assertEqual(attempts, [64])
                current = source.read_bytes()
                self.assertTrue(current == original, f"Source changed from {len(original)} to {len(current)} bytes")
                self.assertEqual(list(dataset.iterdir()), [source])

                reader = self.annotator["read_structure_annotations"]
                self.annotator["sa_annotate"]("Reviewed")
                self.assertEqual(reader(str(source))["quality"], "Reviewed")
                self.annotator["sa_undo"]()
                self.assertNotIn("quality", reader(str(source)))

    def test_annotation_preserves_symbolic_link_and_target_permissions(self):
        target = self.root / "target.raw"
        target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
        original_mode = target.stat().st_mode
        source = self.root / "linked.pdb.gz"
        try:
            source.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314 or exc.errno in (errno.EPERM, errno.ENOTSUP, errno.EACCES):
                self.skipTest("The filesystem does not allow creation of symbolic links")
            raise

        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Reviewed")
        self.assertTrue(source.is_symlink())
        self.assertEqual(target.stat().st_mode, original_mode)
        with gzip.open(target, "rt", encoding="utf-8") as stream:
            self.assertIn("REMARK 950 PYMOL_ANNOTATOR quality=Reviewed", stream.read())

    def test_annotation_preserves_read_only_source_without_leaving_a_temporary_file(self):
        dataset = self.root / "readonly"
        dataset.mkdir()
        source = dataset / "model.pdb"
        original = FIXTURE.read_bytes()
        source.write_bytes(original)
        self.addCleanup(source.chmod, source.stat().st_mode)
        source.chmod(stat.S_IREAD)
        try:
            with source.open("r+b"):
                self.skipTest("The process can write to read-only files")
        except PermissionError:
            pass

        self.annotator["sa_load_dir"](str(dataset))
        self.annotator["sa_annotate"]("Reviewed")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(dataset.iterdir()), [source])

    @unittest.skipUnless(sys.platform == "win32", "Windows file permissions")
    def test_annotation_keeps_windows_permissions_and_alternate_data_stream(self):
        source = self.root / "protected.pdb"
        shutil.copyfile(FIXTURE, source)
        alternate = f"{source}:annotator-regression"
        with open(alternate, "wb") as stream:
            stream.write(b"Existing structure metadata")
        identity = subprocess.check_output(["whoami.exe", "/user", "/fo", "csv", "/nh"])
        user_sid = next(csv.reader(identity.decode("ascii", errors="replace").strip().splitlines()))[1]
        subprocess.run(
            ["icacls.exe", str(source), "/inheritance:r", "/grant:r", f"*{user_sid}:(F)"],
            capture_output=True, check=True,
        )
        read_acl = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-Acl -LiteralPath $env:PYMOL_TEST_ACL_PATH).Sddl",
        ]
        env = dict(os.environ, PYMOL_TEST_ACL_PATH=str(source))
        before = subprocess.check_output(read_acl, env=env).strip()
        self.assertIn(b"D:P", before)

        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Reviewed")
        after = subprocess.check_output(read_acl, env=env).strip()
        self.assertTrue(before == after, "Replacing the structure changed its protected access control list")
        with open(alternate, "rb") as stream:
            self.assertEqual(stream.read(), b"Existing structure metadata")
        reader = self.annotator["read_structure_annotations"]
        self.assertEqual(reader(str(source))["quality"], "Reviewed")

    def test_undo_can_retry_after_source_is_temporarily_unavailable(self):
        source = self.root / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Reviewed")

        parked = source.with_suffix(".parked")
        source.rename(parked)
        self.annotator["sa_undo"]()
        parked.rename(source)
        self.annotator["sa_undo"]()
        reader = self.annotator["read_structure_annotations"]
        self.assertNotIn("quality", reader(str(source)))

    def test_undo_keeps_user_edits_to_the_preloaded_model(self):
        self.state.preload_enabled = True
        for earlier_label in (None, "Earlier label"):
            with self.subTest(earlier_label=earlier_label):
                dataset = self.root / ("unmarked" if earlier_label is None else "marked")
                dataset.mkdir()
                source = dataset / "model.pdb"
                shutil.copyfile(FIXTURE, source)
                if earlier_label:
                    self.annotator["annotate_structure_file"](str(source), "quality", earlier_label)
                self.annotator["sa_load_dir"](str(dataset))
                self.annotator["sa_annotate"]("Reviewed")
                self.cmd.alter(self.state.current_object, "b=77")

                self.annotator["sa_undo"]()
                model = self.cmd.get_model(self.state.current_object)
                self.assertEqual({atom.b for atom in model.atom}, {77.0})
                reader = self.annotator["read_structure_annotations"]
                self.assertEqual(reader(str(source)).get("quality"), earlier_label)

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

    def test_displayed_model_must_be_reloaded_after_replacing_its_source(self):
        dataset = self.root / "dataset"
        dataset.mkdir()
        source = dataset / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        self.state.preload_enabled = True
        self.annotator["sa_load_dir"](str(dataset))
        old_count = self.cmd.count_atoms(self.state.current_object)

        replacement, new_count = self.replacement_structure()
        self.assertNotEqual(old_count, new_count)
        replacement.replace(source)
        reader = self.annotator["read_structure_annotations"]
        self.annotator["sa_annotate"]("From stale display")
        self.assertNotIn("quality", reader(str(source)))

        self.annotator["sa_goto"](1)
        self.assertEqual(self.cmd.count_atoms(self.state.current_object), new_count)
        self.annotator["sa_annotate"]("Reloaded")
        self.assertEqual(reader(str(source))["quality"], "Reloaded")

    def test_annotation_does_not_adopt_replacements_during_write(self):
        for timing in ("read", "staging", "publish"):
            with self.subTest(timing=timing):
                dataset = self.root / timing
                dataset.mkdir()
                source = dataset / "model.pdb"
                shutil.copyfile(FIXTURE, source)
                self.annotator["sa_load_dir"](str(dataset))
                incoming, _ = self.replacement_structure()
                replacement_bytes = incoming.read_bytes()
                reader = self.annotator["read_structure_annotations"]
                writer = self.annotator["annotate_structure_file"]
                chmod = os.chmod

                def replace_after_read(path):
                    result = reader(path)
                    incoming.replace(source)
                    return result

                def replace_during_staging(path, mode):
                    chmod(path, mode)
                    if Path(path).name.startswith(".sa-write-") and incoming.exists():
                        incoming.replace(source)

                def replace_after_publish(*args, **kwargs):
                    result = writer(*args, **kwargs)
                    incoming.replace(source)
                    return result

                interpositions = {
                    "read": patch.dict(self.annotator, {"read_structure_annotations": replace_after_read}),
                    "staging": patch.object(os, "chmod", replace_during_staging),
                    "publish": patch.dict(self.annotator, {"annotate_structure_file": replace_after_publish}),
                }
                with interpositions[timing]:
                    self.annotator["sa_annotate"]("In progress")
                self.assertFalse(incoming.exists())
                self.annotator["sa_annotate"]("Still showing the old model")
                self.assertEqual(source.read_bytes(), replacement_bytes)
                self.assertEqual(list(dataset.iterdir()), [source])

    def test_background_gzip_cache_discards_replaced_source_before_navigation(self):
        dataset = self.root / "dataset"
        dataset.mkdir()
        shutil.copyfile(FIXTURE, dataset / "first.pdb")
        source = dataset / "second.pdb.gz"
        source.write_bytes(gzip.compress(FIXTURE.read_bytes()))
        replacement, expected_atoms = self.replacement_structure()

        self.state.preload_enabled = True
        self.state.preload_ahead = 1
        self.state.background_decompress_gz = True
        self.annotator["sa_load_dir"](str(dataset))
        self.addCleanup(self.annotator["_reset_bg_cache"])
        self.addCleanup(self.annotator["_bg_executor"].shutdown, wait=True)
        self.annotator["_bg_decompress_futures"][1].result(timeout=10)
        self.annotator["_collect_ready_decompressed"]([1])

        incoming = self.root / "next.pdb.gz"
        incoming.write_bytes(gzip.compress(replacement.read_bytes()))
        incoming.replace(source)
        self.annotator["sa_next"]()
        self.assertEqual(self.cmd.count_atoms(self.state.current_object), expected_atoms)
        self.annotator["sa_annotate"]("Current source")
        reader = self.annotator["read_structure_annotations"]
        self.assertEqual(reader(str(source))["quality"], "Current source")

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
