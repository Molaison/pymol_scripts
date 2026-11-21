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

    def test_csv_export_cannot_overwrite_a_dataset_source_or_its_hardlink(self):
        for alias in (False, True):
            with self.subTest(alias=alias):
                directory = self.root / str(alias)
                directory.mkdir()
                source = directory / "model.pdb"
                shutil.copyfile(FIXTURE, source)
                self.annotator["sa_load_dir"](str(directory))
                self.annotator["sa_annotate"]("Reviewed before export")
                destination = source
                if alias:
                    destination = directory / "report.csv"
                    try:
                        os.link(source, destination)
                    except OSError as exc:
                        self.skipTest(f"hard links are unavailable: {exc}")
                original = source.read_bytes()
                self.assertIsNone(self.annotator["sa_export_csv"](str(destination)))
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(destination.read_bytes(), original)

    def test_csv_export_protects_inputs_admitted_by_a_custom_dataset_pattern(self):
        source = self.root / "model.mol2"
        self.cmd.fragment("ala", "fixture")
        self.cmd.save(str(source), "fixture")
        self.cmd.delete("fixture")
        self.annotator["sa_load_dir"](str(self.root), r"^.+\.mol2$")
        self.assertEqual(self.state.current_file(), str(source))
        original = source.read_bytes()
        self.assertIsNone(self.annotator["sa_export_csv"](str(source)))
        self.assertEqual(source.read_bytes(), original)

    def test_csv_export_rejects_a_dataset_path_even_if_source_changes_while_reading(self):
        source = self.root / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Keep the correct structure")
        original = source.read_bytes()
        read_annotations = self.annotator["read_structure_annotations"]

        def substitute_during_read(path, **options):
            parked = source.with_suffix(".parked")
            os.replace(source, parked)
            try:
                shutil.copyfile(FIXTURE, source)
                return read_annotations(path, **options)
            finally:
                os.replace(parked, source)

        with patch.dict(self.annotator, read_structure_annotations=substitute_during_read):
            self.assertIsNone(self.annotator["sa_export_csv"](str(source)))
        self.assertEqual(source.read_bytes(), original)

    def test_csv_export_preserves_labels_named_for_metadata_columns(self):
        source = self.root / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        self.annotator["sa_load_dir"](str(self.root))
        for name in ("filename", "path"):
            with self.subTest(label=name):
                self.state.label_name = name
                self.annotator["sa_annotate"]("Reviewed in PyMOL")
                exported = self.annotator["sa_export_csv"]()
                with Path(exported).open(newline="", encoding="utf-8") as report:
                    reader = csv.DictReader(report)
                    row = next(reader)
                self.assertEqual(reader.fieldnames, ["filename", f"label_{name}", "path"])
                self.assertEqual(row[f"label_{name}"], "Reviewed in PyMOL")
                self.assertEqual((row["filename"], row["path"]), (source.name, str(source)))

    def test_unreadable_source_preserves_the_existing_annotation_csv(self):
        source = self.root / "model.pdb.gz"
        with gzip.open(source, "wb") as stream:
            stream.write(FIXTURE.read_bytes())
        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Verified label")
        report = Path(self.annotator["sa_export_csv"]())
        original = report.read_bytes()
        self.assertIn(b"Verified label", original)
        source.write_bytes(b"corrupted gzip")
        replacement = self.root / "new.csv"
        self.assertIsNone(self.annotator["sa_export_csv"](str(report)))
        self.assertEqual(report.read_bytes(), original)
        self.assertIsNone(self.annotator["sa_export_csv"](str(replacement)))
        self.assertFalse(replacement.exists())

    def test_missing_custom_format_preserves_an_existing_annotation_csv(self):
        source = self.root / "model.mol2"
        self.cmd.fragment("ala", "fixture")
        self.cmd.save(str(source), "fixture")
        self.cmd.delete("fixture")
        self.annotator["sa_load_dir"](str(self.root), r"^.+\.mol2$")
        report = Path(self.annotator["sa_export_csv"]())
        original = report.read_bytes()
        source.rename(source.with_suffix(".parked"))
        self.assertIsNone(self.annotator["sa_export_csv"](str(report)))
        self.assertEqual(report.read_bytes(), original)

    def test_csv_export_uses_the_entire_loaded_dataset_when_navigation_is_filtered(self):
        done, pending = self.root / "done.pdb", self.root / "pending.pdb"
        shutil.copyfile(FIXTURE, done)
        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Already reviewed")
        shutil.copyfile(FIXTURE, pending)
        self.state.skip_annotated = True
        self.annotator["sa_load_dir"](str(self.root))
        self.assertEqual(self.state.files, [str(pending)])
        report = Path(self.annotator["sa_export_csv"]())
        with report.open(newline="", encoding="utf-8") as stream:
            exported = {row["filename"]: row["quality"] for row in csv.DictReader(stream)}
        self.assertEqual(exported, {"done.pdb": "Already reviewed", "pending.pdb": ""})
        original = done.read_bytes()
        self.assertIsNone(self.annotator["sa_export_csv"](str(done)))
        self.assertEqual(done.read_bytes(), original)
        self.annotator["sa_annotate"]("Newly reviewed")
        self.annotator["sa_load_dir"](str(self.root))
        self.assertEqual(self.state.files, [])
        self.assertEqual(self.annotator["sa_export_csv"](), str(report))
        with report.open(newline="", encoding="utf-8") as stream:
            exported = {row["filename"]: row["quality"] for row in csv.DictReader(stream)}
        self.assertEqual(exported, {"done.pdb": "Already reviewed", "pending.pdb": "Newly reviewed"})

    def test_default_csv_export_stays_in_the_loaded_directory_after_cwd_changes(self):
        loaded, unrelated = self.root / "loaded", self.root / "unrelated"
        loaded.mkdir()
        unrelated.mkdir()
        shutil.copyfile(FIXTURE, loaded / "model.pdb")
        previous_report = unrelated / "annotations_quality.csv"
        original = b"Report from a different dataset\n"
        previous_report.write_bytes(original)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(loaded)
        self.annotator["sa_load_dir"](".")
        self.annotator["sa_annotate"]("Reviewed in the loaded directory")
        os.chdir(unrelated)
        exported = self.annotator["sa_export_csv"]()
        self.assertEqual(Path(exported), loaded / "annotations_quality.csv")
        self.assertEqual(previous_report.read_bytes(), original)
        self.assertIn(b"Reviewed in the loaded directory", Path(exported).read_bytes())

    def test_reloading_the_plugin_requires_a_complete_inventory_before_export(self):
        source = self.root / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("Reviewed before plugin reload")
        report = Path(self.annotator["sa_export_csv"]())
        original = report.read_bytes()
        del self.state.dataset_files
        script = Path(self.annotator["__file__"])
        with patch.object(pymol, "cmd", self.cmd):
            exec(compile(script.read_bytes(), str(script), "exec"), self.annotator)
        self.assertIs(self.annotator["STATE"], self.state)
        with patch.object(builtins, "print") as output:
            self.assertIsNone(self.annotator["sa_export_csv"](str(report)))
        self.assertIn("reload the dataset", output.call_args.args[0])
        self.assertEqual(report.read_bytes(), original)
        self.annotator["sa_load_dir"](str(self.root))
        self.assertEqual(self.annotator["sa_export_csv"](), str(report))
        self.assertEqual(report.read_bytes(), original)

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

    def test_undo_cannot_transfer_old_labels_to_a_replacement_model(self):
        dataset = self.root / "dataset"
        dataset.mkdir()
        source = dataset / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        write = self.annotator["annotate_structure_file"]
        read = self.annotator["read_structure_annotations"]
        write(str(source), "quality", "Original label")
        self.annotator["sa_load_dir"](str(dataset))
        self.annotator["sa_annotate"]("Reviewed")

        replacement, new_count = self.replacement_structure()
        write(str(replacement), "quality", "Replacement label")
        replacement.replace(source)
        replacement_bytes = source.read_bytes()
        self.annotator["sa_undo"]()
        self.assertEqual(source.read_bytes(), replacement_bytes)

        self.annotator["sa_goto"](1)
        self.assertEqual(self.cmd.count_atoms(self.state.current_object), new_count)
        self.annotator["sa_annotate"]("Second review")
        self.annotator["sa_undo"]()
        self.assertEqual(read(str(source))["quality"], "Replacement label")

        replacement_bytes = source.read_bytes()
        self.annotator["sa_undo"]()
        self.assertEqual(source.read_bytes(), replacement_bytes)

    def test_undo_keeps_active_label_progress_and_restores_interleaved_edits(self):
        sources = [self.root / name for name in ("first.pdb", "second.pdb")]
        write = self.annotator["annotate_structure_file"]
        read = self.annotator["read_structure_annotations"]
        for source in sources:
            shutil.copyfile(FIXTURE, source)
            write(str(source), "confidence", "Yes")
        self.annotator["sa_load_dir"](str(self.root))
        self.annotator["sa_annotate"]("First review")
        self.annotator["sa_next"]()
        self.annotator["sa_annotate"]("Second review")
        self.annotator["sa_goto"](1)
        self.annotator["sa_annotate"]("Third review")
        self.state.label_name = "confidence"
        self.annotator["_rescan_annotated_indices"]()

        for target, expected in ((sources[0], "First review"), (sources[1], None), (sources[0], None)):
            with self.subTest(target=target.name, expected=expected):
                self.annotator["sa_undo"]()
                self.assertEqual(read(str(target)).get("quality"), expected)
                self.assertEqual(len(self.state.annotated_indices), 2)
        with Path(self.annotator["sa_export_csv"]()).open(newline="", encoding="utf-8") as report:
            self.assertEqual([row["confidence"] for row in csv.DictReader(report)], ["Yes", "Yes"])

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
                reader = self.annotator["_read_text_file"]
                writer = self.annotator["annotate_structure_file"]
                chmod = os.chmod

                def replace_after_read(path, *args, **kwargs):
                    result = reader(path, *args, **kwargs)
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
                    "read": patch.dict(self.annotator, {"_read_text_file": replace_after_read}),
                    "staging": patch.object(os, "chmod", replace_during_staging),
                    "publish": patch.dict(self.annotator, {"annotate_structure_file": replace_after_publish}),
                }
                with interpositions[timing]:
                    self.annotator["sa_annotate"]("In progress")
                self.assertFalse(incoming.exists())
                self.annotator["sa_annotate"]("Still showing the old model")
                self.assertEqual(source.read_bytes(), replacement_bytes)
                self.assertEqual(list(dataset.iterdir()), [source])

    def test_tag_and_undo_use_one_verified_read_of_the_original_source(self):
        for operation in ("structure", "previous_label"):
            with self.subTest(operation=operation):
                directory = self.root / operation
                directory.mkdir()
                source = directory / "original.pdb"
                original_path = directory / "parked_source"
                shutil.copyfile(FIXTURE, source)
                self.annotator["sa_load_dir"](str(directory))
                self.annotator["sa_annotate"]("Original label")
                original_content = source.read_bytes()
                incoming, _ = self.replacement_structure()
                self.annotator["annotate_structure_file"](str(incoming), "quality", "Donor label")

                namespace = self.annotator["sa_annotate"].__globals__
                function = "_read_text_file" if operation == "structure" else "read_structure_annotations"
                reader = namespace[function]
                observed_swaps = []

                def read_while_swapped(path, *args, **options):
                    if Path(path) != source:
                        return reader(path, *args, **options)
                    source.replace(original_path)
                    incoming.replace(source)
                    observed_swaps.append(True)
                    try:
                        return reader(path, *args, **options)
                    finally:
                        source.replace(incoming)
                        original_path.replace(source)

                undo_count = len(self.state.undo_history)
                with patch.dict(namespace, {function: read_while_swapped}):
                    self.annotator["sa_annotate"]("Updated")

                labels = self.annotator["read_structure_annotations"]
                if operation == "structure":
                    self.assertEqual(observed_swaps, [True])
                    self.assertTrue(source.read_bytes() == original_content,
                                    "A read from the donor replaced the original structure")
                    self.assertEqual(len(self.state.undo_history), undo_count)
                else:
                    self.assertEqual(labels(str(source)).get("quality"), "Updated")
                    self.annotator["sa_undo"]()
                    self.assertEqual(labels(str(source)).get("quality"), "Original label",
                                     "Undo restored the donor's label")

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

    def test_loading_uses_the_validated_source_even_if_path_changes_during_parse(self):
        for extension in (".pdb", ".pdb.gz"):
            with self.subTest(extension=extension):
                dataset = self.root / ("plain_parser" if extension == ".pdb" else "gzip_parser")
                dataset.mkdir()
                source = dataset / f"model{extension}"
                contents = FIXTURE.read_bytes()
                source.write_bytes(gzip.compress(contents) if extension.endswith(".gz") else contents)
                incoming, donor_count = self.replacement_structure()
                swapped = self.root / f"replacement{extension}.tmp"
                payload = incoming.read_bytes()
                swapped.write_bytes(gzip.compress(payload) if extension.endswith(".gz") else payload)
                parked = self.root / "parked_structure"
                calls = []
                load, load_raw = self.cmd.load, self.cmd.load_raw

                @contextmanager
                def exchange():
                    source.replace(parked)
                    swapped.replace(source)
                    calls.append(True)
                    try:
                        yield
                    finally:
                        source.replace(swapped)
                        parked.replace(source)

                def path_loader(path, *args, **options):
                    if Path(path) == source:
                        with exchange():
                            return load(path, *args, **options)
                    return load(path, *args, **options)

                def memory_loader(*args, **options):
                    with exchange():
                        return load_raw(*args, **options)

                with patch.object(self.cmd, "load", path_loader), patch.object(
                    self.cmd, "load_raw", memory_loader
                ):
                    self.annotator["sa_load_dir"](str(dataset))
                self.assertEqual(calls, [True])
                shown = self.cmd.count_atoms(self.state.current_object)
                self.assertGreater(shown, donor_count, "The displayed object came from a different file")
                self.annotator["sa_annotate"]("Reviewed scene")
                self.cmd.load(str(source), "validation_source")
                self.assertEqual(self.cmd.count_atoms("validation_source"), shown)
                self.assertEqual(self.annotator["read_structure_annotations"](str(source)).get("quality"),
                                 "Reviewed scene")
                self.cmd.delete("validation_source")

    def test_corrupt_gzip_does_not_block_the_next_valid_preload(self):
        shutil.copyfile(FIXTURE, self.root / "a_current.pdb")
        (self.root / "b_broken.pdb.gz").write_bytes(b"bad gzip fixture")
        data = gzip.compress(FIXTURE.read_bytes())
        (self.root / "c_valid.pdb.gz").write_bytes(data)
        (self.root / "d_valid.pdb.gz").write_bytes(data)
        self.state.preload_enabled = True
        self.state.preload_ahead = 3
        self.state.background_decompress_gz = True
        self.annotator["sa_load_dir"](str(self.root))
        self.addCleanup(self.annotator["_reset_bg_cache"])
        self.addCleanup(self.annotator["_bg_executor"].shutdown, wait=True)
        futures = self.annotator["_bg_decompress_futures"]
        futures[2].result(timeout=10)
        futures[3].result(timeout=10)
        with self.assertRaises(gzip.BadGzipFile):
            futures[1].result(timeout=10)

        self.annotator["_process_preload_tick"]()
        first_tick = self.cmd.get_names("objects")
        loaded = next((name for name in first_tick if "c_valid" in name), None)
        self.assertIsNotNone(loaded)
        self.assertFalse(any("d_valid" in name for name in first_tick))

        self.annotator["_process_preload_tick"]()
        self.assertTrue(any("d_valid" in name for name in self.cmd.get_names("objects")))
        self.assertEqual(self.cmd.get_names("objects", enabled_only=1), [self.state.current_object])
        self.annotator["sa_goto"](3)
        self.assertEqual(self.state.current_object, loaded)

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
