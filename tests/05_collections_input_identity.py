"""Exercise Rosetta score row identity and the native PNG retry cache."""

import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PIL import Image, ImageChops


REPO = Path(__file__).resolve().parents[1]
CORE_PATH = "workflows/dataset_navigation/collections_core.py"
FIXTURE = REPO / "data/helical_bundle.pdb"
REVISION = None


class CollectionsInputIdentity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        script = REPO / CORE_PATH
        if REVISION:
            script = self.root / "collections_core.py"
            script.write_bytes(subprocess.check_output(["git", "show", f"{REVISION}:{CORE_PATH}"], cwd=REPO))
        self.core = runpy.run_path(str(script))

    def test_rosetta_description_is_a_valid_decoy_name(self):
        for model in ("description", "another"):
            shutil.copyfile(FIXTURE, self.root / f"{model}.pdb")
        scores = self.root / "scores.sc"
        scores.write_text(
            "SEQUENCE: ALA\n"
            "SCORE: total_score quality description\n"
            "SCORE: -10 0.98 description\n"
            "SCORE: quality total_score description\n"
            "SCORE: 0.65 -7 another\n",
            encoding="utf-8",
        )
        rows, skipped = self.core["prepare_rows"]({
            "scores": str(scores), "path_column": "description", "metrics": [{"name": "quality"}],
        })
        self.assertFalse(skipped)
        self.assertEqual([(row["id"], row["scores"]["quality"]) for row in rows],
                         [("description", 0.98), ("another", 0.65)])

    def test_retry_rejects_changed_structure_with_unchanged_file_metadata(self):
        manifest, source = self.collection()
        original = self.export(manifest)
        self.assertTrue(original["results"][0]["ok"])
        self.assertEqual(self.export(manifest, retry=True)["reused"], 1)

        metadata = source.stat()
        replacement = []
        for line in source.read_bytes().splitlines(keepends=True):
            if line.startswith(b"ATOM  "):
                x = float(line[30:38]) + 100
                line = line[:30] + f"{x:8.3f}".encode("ascii") + line[38:]
            replacement.append(line)
        source.write_bytes(b"".join(replacement))
        os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assertEqual((source.stat().st_size, source.stat().st_mtime_ns),
                         (metadata.st_size, metadata.st_mtime_ns))

        result = self.export(manifest, retry=True)
        self.assertEqual(result["reused"], 0)
        self.assertEqual(result["results"][0]["error"], self.core["STALE_POSE"])
        self.core["revise_collection"](manifest, {"action": "orient", "config": {}})
        self.assertTrue(self.export(manifest, retry=True)["results"][0]["ok"])
        self.assertEqual(self.export(manifest, retry=True)["reused"], 1)

    def test_retry_replaces_a_truncated_cached_png(self):
        manifest, _ = self.collection()
        row = self.export(manifest)["results"][0]
        self.assertTrue(row["ok"])
        image = Path(row["image"])
        image.write_bytes(image.read_bytes()[:24])
        repaired = self.export(manifest, retry=True)
        self.assertEqual(repaired["reused"], 0)
        self.assertTrue(repaired["results"][0]["ok"])
        with Image.open(image) as png:
            png.verify()
        self.assertEqual(self.export(manifest, retry=True)["reused"], 1)

    def test_retry_upgrades_an_old_export_report_once(self):
        manifest, _ = self.collection()
        self.assertTrue(self.export(manifest)["results"][0]["ok"])
        receipt = manifest.parent / "images/03_export.json"
        for absent in (("source_sha256", "image_sha256", "source_snapshot"),
                       ("source_snapshot",)):
            with self.subTest(missing_report_fields=absent):
                report = json.loads(receipt.read_text(encoding="utf-8"))
                for row in report["results"]:
                    for field in absent:
                        row.pop(field, None)
                self.core["write_json"](receipt, report)
                self.assertEqual(self.export(manifest, retry=True)["reused"], 0)
                self.assertEqual(self.export(manifest, retry=True)["reused"], 1)

    def test_export_hash_matches_the_structure_actually_loaded(self):
        manifest, source = self.collection()
        contents = source.read_bytes()
        changed = b"".join(
            line[:17] + b"ALA" + line[20:]
            if line.startswith(b"ATOM") and line[17:20] == b"GLY" else line
            for line in contents.splitlines(keepends=True)
        )
        self.assertNotEqual(contents, changed)
        metadata = source.stat()

        state = json.loads(manifest.read_text(encoding="utf-8"))
        state["config"]["binder"] = "resi 1-8 or resn ALA"
        self.core["write_json"](manifest, state)
        worker = self.core["render_worker"]
        directory = manifest.parent / "images"
        directory.mkdir()
        previous = worker(manifest, [0], directory, 144, 108, 1,
                          directory / "03_export.json")[0]
        self.assertTrue(previous["ok"], previous)
        with Image.open(previous["image"]) as image:
            expected = image.convert("RGB")

        globals_ = worker.__globals__
        file_sha256, apply_pose = globals_["_file_sha256"], globals_["apply_pose"]
        swapped = []

        def replace_after_hash(path, **options):
            digest = file_sha256(path, **options)
            if Path(path) == source:
                source.write_bytes(changed)
                os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
                swapped.append("hashed")
            return digest

        def restore_after_load(*args, **kwargs):
            self.assertEqual(source.read_bytes(), changed)
            source.write_bytes(contents)
            os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            swapped.append("loaded")
            return apply_pose(*args, **kwargs)

        with patch.dict(globals_, {"_file_sha256": replace_after_hash,
                                   "apply_pose": restore_after_load}):
            row = worker(manifest, [0], directory, 144, 108, 1,
                         directory / "03_export.json")[0]
        self.assertEqual(swapped, ["hashed", "loaded"])
        self.assertTrue(row["ok"], row)
        self.assertEqual(row["source_sha256"], previous["source_sha256"])

        retried = self.export(manifest, retry=True)
        self.assertEqual(retried["reused"], 1)
        with Image.open(retried["results"][0]["image"]) as image:
            self.assertIsNone(ImageChops.difference(expected, image.convert("RGB")).getbbox(),
                              "Retry accepted a render of different contents")

    def test_cancel_cleans_a_source_interrupted_during_staging(self):
        manifest, _ = self.collection()
        marker = self.root / "copy-checkpoint"
        private_temp = self.root / "worker-temp"
        private_temp.mkdir()
        source = Path(self.core["__file__"]).read_text(encoding="utf-8")
        checkpoint = "                copy_to.write(chunk)\n"
        self.assertEqual(source.count(checkpoint), 1)
        paused = (checkpoint
                  + f'                Path({str(marker)!r}).write_text(copy_to.name, encoding="utf-8")\n'
                  + '                time.sleep(30)\n')
        worker_script = self.root / "collections_paused.py"
        worker_script.write_text(source.replace(checkpoint, paused), encoding="utf-8")
        core = runpy.run_path(str(worker_script))
        control = core["TaskControl"]()

        paths = {name: str(private_temp) for name in ("TMPDIR", "TEMP", "TMP")}
        with patch.dict(os.environ, paths), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(core["export_collection"], manifest, workers=1, width=144,
                                     height=108, python=sys.executable, control=control)
            staged = None
            try:
                deadline = time.monotonic() + 10
                while not future.done() and time.monotonic() < deadline:
                    if marker.is_file():
                        path = marker.read_text(encoding="utf-8")
                        if path and Path(path).is_file():
                            staged = Path(path)
                            break
                    time.sleep(0.03)
                self.assertIsNotNone(staged, "Worker did not reach the staging checkpoint")
            finally:
                control.cancelled.set()
            report = future.result(timeout=10)

        self.assertTrue(report["cancelled"])
        self.assertFalse(control.processes)
        self.assertIsNotNone(staged)
        self.assertFalse(staged.exists(), "Cancelled export left a staged source file")

    def test_export_updates_worker_diagnostics_on_cancel_or_full_cache_hit(self):
        manifest, _ = self.collection()
        directory = manifest.parent / "images"
        for state in ("cancelled", "all_cached"):
            with self.subTest(state=state):
                exported = self.export(manifest)
                row = exported["results"][0]
                self.assertTrue(row["ok"], row)
                receipt = directory / "02_worker_01.json"
                self.assertTrue(receipt.is_file())
                unrelated = directory / "02_worker_notes.json"
                unrelated.write_text("notes", encoding="utf-8")
                image = Path(row["image"])
                changed_at = image.stat().st_mtime_ns
                control = self.core["TaskControl"]()
                if state == "cancelled":
                    control.cancelled.set()
                report = self.core["export_collection"](
                    manifest, workers=1, width=144, height=108, python=sys.executable,
                    control=control, retry_failed=(state == "all_cached"),
                )
                self.assertEqual(report["processes"], [])
                self.assertEqual(report["cancelled"], state == "cancelled")
                self.assertEqual(report["reused"], int(state == "all_cached"))
                self.assertEqual(image.stat().st_mtime_ns, changed_at)
                self.assertFalse(receipt.exists(), "Public worker report belonged to the prior export")
                self.assertEqual(unrelated.read_text(encoding="utf-8"), "notes")

    def collection(self):
        source = self.root / "model.pdb"
        shutil.copyfile(FIXTURE, source)
        scores = self.root / "scores.json"
        scores.write_text(json.dumps([{"path": source.name, "score": 1}]), encoding="utf-8")
        config = {"scores": str(scores), "metrics": [{"name": "score"}], "count": 1,
                  "orientation": "pca", "target": "polymer.protein", "binder": "polymer.protein"}
        destination = self.root / "collection"
        self.core["create_collection"](config, destination)
        return destination / "01_collection.json", source

    def export(self, manifest, retry=False):
        return self.core["export_collection"](manifest, workers=1, width=144, height=108,
                                              python=sys.executable, retry_failed=retry)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Run both the coordinator and render worker from this Git revision")
    options, remaining = parser.parse_known_args()
    REVISION = options.revision
    unittest.main(argv=[__file__, *remaining], verbosity=2)
