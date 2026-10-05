from pathlib import Path
import tempfile
import unittest

from pymolplugins.annotation.annotation_store import AnnotationStoreError, RowAnnotationStore
from pymolplugins.annotation.review_actions import save_annotations


class AnnotationBatchTests(unittest.TestCase):
    def test_fifty_thousand_candidates_commit_and_undo_as_one_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            store = RowAnnotationStore(str(path))
            changes = [{"entry_id": f"csv:{index:064x}", "label": label, "value": value}
                       for index in range(50000)
                       for label, value in (("quality", "Good"), ("reason", '["interface"]'))]
            event = store.set_many(changes)
            self.assertEqual(len(store.snapshot()), 50000)
            self.assertEqual(path.read_bytes().count(b"\n"), 1)
            store.undo_batch(event["seq"])
            self.assertEqual(store.snapshot(), {})
            self.assertEqual(RowAnnotationStore(str(path)).snapshot(), {})

    def test_required_checks_cover_the_whole_selection_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            store = RowAnnotationStore(str(path))
            store.set("one", "quality", "Good")
            before = path.read_bytes()
            fields = [{"name": "quality", "type": "single", "required": True, "values": ["Good", "Bad"]},
                      {"name": "notes", "type": "text", "required": False, "values": []}]
            with self.assertRaisesRegex(ValueError, "required.*quality"):
                save_annotations(store, ["one", "two"], {"notes": "test"}, fields)
            self.assertEqual(path.read_bytes(), before)
            save_annotations(store, ["one", "two"], {"quality": "Bad", "notes": "test"}, fields)
            self.assertEqual(store.annotations_for("two"), {"quality": "Bad", "notes": "test"})

    def test_batch_is_one_event_and_undo_restores_all_rows_and_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            store = RowAnnotationStore(str(path), baseline={"one": {"quality": "Old"}})
            event = store.set_many([
                {"entry_id": "one", "label": "quality", "value": "Good"},
                {"entry_id": "one", "label": "notes", "value": "reviewed"},
                {"entry_id": "two", "label": "quality", "value": "Good"},
            ])
            self.assertEqual(len(path.read_text().splitlines()), 1)
            reopened = RowAnnotationStore(str(path), baseline={"one": {"quality": "Old"}})
            self.assertEqual(reopened.snapshot(), store.snapshot())
            reopened.undo_batch(event["seq"])
            self.assertEqual(reopened.snapshot(), {"one": {"quality": "Old"}})
            self.assertEqual(RowAnnotationStore(str(path), baseline={"one": {"quality": "Old"}}).snapshot(), reopened.snapshot())

    def test_batch_undo_refuses_newer_edits_and_works_after_they_are_undone(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RowAnnotationStore(str(Path(directory) / "labels.jsonl"))
            batch = store.set_many([{"entry_id": "one", "label": "quality", "value": "Good"}])
            store.set("one", "quality", "Bad")
            with self.assertRaisesRegex(AnnotationStoreError, "newest"):
                store.undo_batch(batch["seq"])
            store.undo()
            store.undo_batch(batch["seq"])
            self.assertEqual(store.snapshot(), {})

    def test_invalid_batch_does_not_create_partial_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            store = RowAnnotationStore(str(path))
            with self.assertRaises(AnnotationStoreError):
                store.set_many([
                    {"entry_id": "one", "label": "quality", "value": "Good"},
                    {"entry_id": "two", "label": "quality", "value": 4},
                ])
            self.assertFalse(path.exists())
            self.assertEqual(store.snapshot(), {})

    def test_batch_history_filter_and_compaction_preserve_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RowAnnotationStore(str(Path(directory) / "labels.jsonl"))
            store.set_many([{"entry_id": "one", "label": "flags", "value": '["A","B"]'},
                            {"entry_id": "two", "label": "notes", "value": "text"}])
            history = store.history(entry_id="one")
            self.assertEqual(len(history), 1)
            history[0]["changes"][0]["current"] = "mutated"
            self.assertEqual(store.get("one", "flags"), '["A","B"]')
            before = store.snapshot()
            store.compact()
            self.assertEqual(store.snapshot(), before)
            self.assertIsNone(store.undo())


if __name__ == "__main__":
    unittest.main()
