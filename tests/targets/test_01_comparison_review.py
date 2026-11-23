"""File-level contracts for comparison review publication."""

import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pymolplugins.targets.batch_review import prepare_comparison_review, write_comparison_review
from pymolplugins.targets.batch_sources import BatchConfig


class ComparisonReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        for name in ("parent", "child1", "child2", "child3"):
            (self.source / (name + ".pdb")).write_text("ATOM\n", encoding="utf-8")
        self.table = self.source / "pairs.csv"
        self.table.write_text("id,candidate,parent\na,child1.pdb,parent.pdb\n"
                              "b,child2.pdb,parent.pdb\nc,child3.pdb,child2.pdb\n", encoding="utf-8")
        self.config = BatchConfig(source="csv", mode="paired", a=str(self.table),
                                  a_column="candidate", b="parent", id_column="id")

    def test_lineage_is_read_only_and_reopens_with_explicit_pairs(self):
        before = {p: p.read_bytes() for p in self.source.iterdir()}
        review = prepare_comparison_review(self.config, "lineage")
        self.assertEqual(review.rounds, (1, 1, 2))
        table = write_comparison_review(review, self.output)
        with table.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["ID"] for r in rows], ["a", "b", "c"])
        self.assertEqual(rows[1]["Promotion"], "Next parent")
        self.assertEqual(rows[2]["Reference file"], rows[1]["Candidate file"])
        self.assertEqual(rows[0]["Anchor file"], rows[2]["Anchor file"])
        config = json.loads(table.with_name("review.json").read_text(encoding="utf-8"))
        self.assertEqual(config["path_column"], "Candidate file")
        self.assertEqual(before, {p: p.read_bytes() for p in self.source.iterdir()})
        self.assertEqual({p.name for p in table.parent.iterdir()}, {"review.json", "sum_scores.csv"})
        second = write_comparison_review(review, self.output)
        self.assertNotEqual(table.parent, second.parent)

    def test_invalid_ids_and_nonadjacent_promotion_do_not_publish(self):
        for rows in ("a,child1.pdb,parent.pdb\na,child2.pdb,parent.pdb\n",
                     ",child1.pdb,parent.pdb\n",
                     "a,child1.pdb,parent.pdb\nb,child2.pdb,child3.pdb\n"):
            with self.subTest(rows=rows):
                self.table.write_text("id,candidate,parent\n" + rows, encoding="utf-8")
                with self.assertRaises(ValueError):
                    prepare_comparison_review(self.config, "lineage")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_file_alias_is_not_a_different_reference(self):
        os.link(self.source / "child1.pdb", self.source / "alias1.pdb")
        config = BatchConfig(mode="paired", a=str(self.source / "child{id}.pdb"),
                             b=str(self.source / "alias{id}.pdb"), ids="1")
        with self.assertRaisesRegex(ValueError, "same file"):
            prepare_comparison_review(config, "overlay")

    def test_changed_inputs_invalidate_preview(self):
        for filename in ("child1.pdb", "pairs.csv"):
            with self.subTest(filename=filename):
                review = prepare_comparison_review(self.config, "overlay")
                path = self.source / filename
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaisesRegex(ValueError, "Input changed"):
                    write_comparison_review(review, self.output)
                path.write_bytes(original)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_change_during_publication_cleans_only_its_own_folder(self):
        review = prepare_comparison_review(self.config, "lineage")
        untouched = self.output / "existing.txt"
        untouched.write_text("keep", encoding="utf-8")
        original = csv.DictWriter.writerow

        def write_and_change(writer, row):
            result = original(writer, row)
            (self.source / "child1.pdb").write_text("changed during write\n", encoding="utf-8")
            return result

        with patch.object(csv.DictWriter, "writerow", write_and_change):
            with self.assertRaisesRegex(ValueError, "Input changed"):
                write_comparison_review(review, self.output)
        self.assertEqual(list(self.output.iterdir()), [untouched])
        self.assertEqual(untouched.read_text(), "keep")

    def test_cancellation_and_publish_failure_leave_no_partial_review(self):
        review = prepare_comparison_review(self.config, "lineage")
        event = threading.Event()
        original = csv.DictWriter.writerow

        def write_and_cancel(writer, row):
            result = original(writer, row)
            event.set()
            return result

        with patch.object(csv.DictWriter, "writerow", write_and_cancel):
            with self.assertRaises(InterruptedError):
                write_comparison_review(review, self.output, event)
        with patch("pymolplugins.targets.batch_review.os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                write_comparison_review(review, self.output)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
