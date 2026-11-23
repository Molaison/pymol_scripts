"""Versioned events preserve stable entry IDs and source-specific label histories."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pymolplugins.annotation.annotation_store import (
    ANNOTATION_VERSION_SCHEMA, AnnotationStoreError, RowAnnotationStore,
)
from pymolplugins.annotation.review_actions import save_annotations


class AnnotationVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "labels.jsonl"
        self.store = RowAnnotationStore(str(self.path), versioned=True,
                                        unversioned_labels=("review_status",))

    def test_new_version_has_no_old_labels_or_reviewed_snapshot(self):
        store = self.store
        store.bind_source("row", "old")
        store.set("row", "quality", "Keep")
        store.set("row", "review_status", "Later")
        store.bind_source("row", "new")
        self.assertEqual(store.annotations_for("row"), {"review_status": "Later"})
        self.assertEqual(store.snapshot_for_label("quality"), {})
        self.assertEqual(store.export_mapping(), {"row": {"review_status": "Later"}})
        self.assertIsNone(store.get("row", "quality"))
        self.assertEqual(store.annotations_for("row", source_version="old"), {"quality": "Keep"})
        store.set("row", "quality", "Reject")
        store.reload()
        self.assertEqual(store.get("row", "quality"), "Reject")
        store.bind_source("row", "old")
        self.assertEqual(store.get("row", "quality"), "Keep")

    def test_batch_and_single_undo_are_scoped_to_the_recorded_version(self):
        store = self.store
        store.bind_source("row", "old")
        store.set("row", "quality", "Keep")
        event = store.set_many([dict(entry_id="row", label="quality", value="Review"),
                               dict(entry_id="row", label="note", value="old view")])
        store.bind_source("row", "new")
        self.assertIsNone(store.undo(entry_id="row", label="quality"))
        store.set("row", "quality", "Reject")
        store.reload()
        store.undo_batch(event["seq"])
        self.assertEqual(store.annotations_for("row"), {"quality": "Reject"})
        self.assertEqual(store.annotations_for("row", source_version="old"), {"quality": "Keep"})
        store.undo(entry_id="row", label="quality")
        self.assertEqual(store.annotations_for("row"), {})
        self.assertIsNone(store.undo(entry_id="row", label="quality"))
        store.bind_source("row", "old")
        store.remove("row", "quality")
        store.undo(entry_id="row", label="quality")
        self.assertEqual(store.get("row", "quality"), "Keep")
        replay = RowAnnotationStore(str(self.path), versioned=True)
        replay.bind_source("row", "old")
        self.assertEqual(replay.get("row", "quality"), "Keep")
        self.assertTrue(any(event.get("source_version") == "new" for event in replay.history()))

    def test_compaction_preserves_all_versions_and_legacy_baseline_tombstones(self):
        legacy = RowAnnotationStore(str(self.path), baseline={"row": {"quality": "Legacy", "removed": "x"}})
        legacy.set("row", "note", "unbound")
        legacy.remove("row", "removed")
        legacy.set("row", "quality", "Keep", source_version="old")
        legacy.set("row", "quality", "Reject", source_version="new")
        legacy.compact()
        replay = RowAnnotationStore(str(self.path), baseline={"row": {"quality": "Legacy", "removed": "x"}})
        self.assertEqual(replay.annotations_for("row"), {"quality": "Legacy", "note": "unbound"})
        self.assertEqual(replay.annotations_for("row", source_version="old"), {"quality": "Keep"})
        self.assertEqual(replay.annotations_for("row", source_version="new"), {"quality": "Reject"})
        records = replay.history(include_baseline=True)
        self.assertEqual({item["source_version"] for item in records if "source_version" in item}, {"old", "new"})
        self.assertIsNone(replay.undo())

    def test_legacy_event_is_readable_but_not_bound_to_a_newly_observed_version(self):
        legacy = RowAnnotationStore(str(self.path))
        legacy.set("row", "quality", "Legacy")
        self.store.reload()
        self.assertIsNone(self.store.get("row", "quality"))
        self.store.bind_source("row", "new")
        self.assertEqual(self.store.annotations_for("row"), {})
        self.assertEqual(self.store.annotations_for("row", source_version=None), {"quality": "Legacy"})
        self.assertNotIn("source_version", self.store.history()[0])
        self.store.set("row", "quality", "New review")
        self.assertEqual(self.store.history()[-1]["schema"], ANNOTATION_VERSION_SCHEMA)

    def test_multifield_save_binds_every_change_and_rejects_unobserved_targets_atomically(self):
        fields = [{"name": "quality", "type": "single", "values": ["Keep", "Reject"], "required": True}]
        self.store.bind_source("row", "old")
        with self.assertRaisesRegex(AnnotationStoreError, "Load candidate"):
            save_annotations(self.store, ["row", "not_loaded"], {"quality": "Keep"}, fields)
        self.assertFalse(self.path.exists())
        result = save_annotations(self.store, ["row"], {"quality": "Keep"}, fields)
        self.assertEqual(result["changes"][0]["source_version"], "old")
        self.store.bind_source("row", "new")
        second = save_annotations(self.store, ["row"], {"quality": "Keep"}, fields)
        self.assertEqual(second["status"], "saved")
        self.assertEqual(second["changes"][0]["source_version"], "new")

    def test_bind_and_cached_projection_do_no_io_and_change_the_view_revision(self):
        self.store.bind_source("row", "old")
        self.store.set("row", "quality", "Keep")
        with patch("os.stat", side_effect=AssertionError("unexpected I/O")), patch("builtins.open", side_effect=AssertionError("unexpected I/O")):
            revision = self.store.revision(refresh=False)
            self.assertTrue(self.store.bind_source("row", "new"))
            self.assertNotEqual(self.store.revision(refresh=False), revision)
            self.assertFalse(self.store.bind_source("row", "new"))
            self.assertEqual(self.store.annotations_for("row", refresh=False), {})
            self.assertEqual(self.store.snapshot(refresh=False), {})
            self.assertEqual(self.store.snapshot_for_label("quality", refresh=False), {})
            self.assertIsNone(self.store.get("row", "quality", refresh=False))

    def test_invalid_version_event_is_rejected_on_replay(self):
        self.store.bind_source("row", "old")
        self.store.set("row", "quality", "Keep")
        event = json.loads(self.path.read_text())
        event["source_version"] = None
        self.path.write_text(json.dumps(event) + "\n")
        with self.assertRaisesRegex(AnnotationStoreError, "source_version"):
            self.store.reload()


if __name__ == "__main__":
    unittest.main()
