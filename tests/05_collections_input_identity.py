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
import unittest

from PIL import Image


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
        report = json.loads(receipt.read_text(encoding="utf-8"))
        for row in report["results"]:
            row.pop("source_sha256", None)
            row.pop("image_sha256", None)
        self.core["write_json"](receipt, report)
        self.assertEqual(self.export(manifest, retry=True)["reused"], 0)
        self.assertEqual(self.export(manifest, retry=True)["reused"], 1)

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
