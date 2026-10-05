import concurrent.futures
import json
import os
import tempfile
import unittest
from unittest import mock

from pymolplugins.annotation.annotation_store import (
    ANNOTATION_SCHEMA,
    ANNOTATION_SCHEMA_V1,
    MAX_ENTRY_ID_LENGTH,
    MAX_VALUE_LENGTH,
    AnnotationStoreError,
    RowAnnotationStore,
    annotation_sidecar_path,
)


class RowAnnotationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = os.path.join(
            self.temporary.name,
            ".pymolplugins",
            "scores.annotations.jsonl",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_sidecar_path_is_hidden_deterministic_and_movable(self):
        first = annotation_sidecar_path(
            os.path.join(self.temporary.name, "one", "Scores 2026.csv")
        )
        second = annotation_sidecar_path(
            os.path.join(self.temporary.name, "moved", "Scores 2026.csv")
        )

        self.assertEqual(os.path.basename(first), os.path.basename(second))
        self.assertEqual(os.path.basename(os.path.dirname(first)), ".pymolplugins")
        self.assertRegex(
            os.path.basename(first),
            r"^structure_annotator\.scores_2026\.[0-9a-f]{8}\.annotations\.jsonl$",
        )
        custom = annotation_sidecar_path(
            os.path.join(self.temporary.name, "scores.csv"), "review-v2"
        )
        self.assertTrue(custom.endswith("structure_annotator.review-v2.annotations.jsonl"))
        unicode_name = annotation_sidecar_path(
            os.path.join(self.temporary.name, "结构评分.csv")
        )
        self.assertIn("structure_annotator.结构评分.", unicode_name)
        with self.assertRaisesRegex(AnnotationStoreError, "dataset_id"):
            annotation_sidecar_path("scores.csv", "../escape")
        windows = annotation_sidecar_path(r"K:\mapped\Scores 2026.csv")
        self.assertRegex(
            windows,
            r"^K:\\mapped\\\.pymolplugins\\structure_annotator\."
            r"scores_2026\.[0-9a-f]{8}\.annotations\.jsonl$",
        )

    def test_case_distinct_csv_siblings_use_distinct_sidecars(self):
        with tempfile.TemporaryDirectory() as root:
            lower = os.path.join(root, "scores.csv")
            upper = os.path.join(root, "Scores.csv")
            for path in (lower, upper):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("structure_path\nmodel.pdb\n")

            lower_sidecar = annotation_sidecar_path(lower)
            upper_sidecar = annotation_sidecar_path(upper)

            self.assertNotEqual(lower_sidecar, upper_sidecar)
            self.assertIn(".case-", os.path.basename(lower_sidecar))
            self.assertIn(".case-", os.path.basename(upper_sidecar))

    def test_set_remove_reload_snapshot_and_export_mapping(self):
        store = RowAnnotationStore(self.path)

        first = store.set("row-1", "decision", "Keep")
        second = store.set("row-1", "note", "membrane\ninterface")
        removed = store.remove("row-1", "decision")
        missing = store.remove("row-2", "decision")

        self.assertFalse(first["had_previous"])
        self.assertIsNone(first["previous"])
        self.assertEqual(first["current"], "Keep")
        self.assertEqual(first["seq"], 1)
        self.assertTrue(first["appended"])
        self.assertEqual(second["current"], "membrane\ninterface")
        self.assertTrue(removed["had_previous"])
        self.assertEqual(removed["previous"], "Keep")
        self.assertIsNone(removed["current"])
        self.assertFalse(missing["had_previous"])
        self.assertEqual(store.get("row-1", "note"), "membrane\ninterface")
        self.assertEqual(store.get("row-1", "unknown", "fallback"), "fallback")
        self.assertEqual(store.annotations_for("row-1"), {"note": "membrane\ninterface"})
        self.assertEqual(
            store.snapshot(), {"row-1": {"note": "membrane\ninterface"}}
        )
        self.assertEqual(store.export_mapping(), store.snapshot())

        reloaded = RowAnnotationStore(self.path)
        self.assertEqual(reloaded.snapshot(), store.snapshot())
        self.assertEqual(
            [event["op"] for event in reloaded.history()],
            ["set", "set", "remove", "remove"],
        )
        with open(self.path, "rb") as handle:
            records = handle.readlines()
        self.assertEqual(len(records), 4)
        self.assertTrue(all(record.endswith(b"\n") for record in records))
        self.assertTrue(
            all(json.loads(record)["schema"] == ANNOTATION_SCHEMA for record in records)
        )

    def test_legacy_v1_log_is_read_and_new_appends_use_current_schema(self):
        os.makedirs(os.path.dirname(self.path))
        legacy = {
            "schema": ANNOTATION_SCHEMA_V1,
            "seq": 1,
            "timestamp_utc": "2026-07-25T00:00:00+00:00",
            "op": "set",
            "entry_id": "row-1",
            "label": "decision",
            "value": "Keep",
        }
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(legacy) + "\n")

        store = RowAnnotationStore(self.path)
        self.assertEqual(store.get("row-1", "decision"), "Keep")
        store.set("row-2", "decision", "Reject")

        with open(self.path, "r", encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle]
        self.assertEqual(
            [event["schema"] for event in events],
            [ANNOTATION_SCHEMA_V1, ANNOTATION_SCHEMA],
        )

    def test_source_baseline_is_overlaid_by_events_and_undo_restores_it(self):
        baseline = {
            "row-1": {
                "decision": "Needs Review = 需要复核",
                "note": "source baseline",
            }
        }
        original = json.loads(json.dumps(baseline, ensure_ascii=False))
        store = RowAnnotationStore(self.path, baseline=baseline)

        self.assertEqual(store.snapshot(), baseline)
        changed = store.set("row-1", "decision", "Reject")
        self.assertTrue(changed["had_previous"])
        self.assertEqual(changed["previous"], "Needs Review = 需要复核")
        self.assertEqual(store.get("row-1", "decision"), "Reject")

        undo_set = store.undo(entry_id="row-1", label="decision")
        self.assertEqual(undo_set["current"], "Needs Review = 需要复核")
        removed = store.remove("row-1", "decision")
        self.assertEqual(removed["previous"], "Needs Review = 需要复核")
        self.assertIsNone(store.get("row-1", "decision"))

        reloaded = RowAnnotationStore(self.path, baseline=baseline)
        self.assertIsNone(reloaded.get("row-1", "decision"))
        self.assertEqual(reloaded.get("row-1", "note"), "source baseline")
        undo_remove = reloaded.undo(entry_id="row-1", label="decision")
        self.assertEqual(undo_remove["current"], "Needs Review = 需要复核")
        self.assertEqual(reloaded.snapshot(), baseline)
        self.assertEqual(baseline, original)

    def test_baseline_compaction_preserves_overrides_and_durable_tombstones(self):
        baseline = {
            "row-1": {"decision": "Keep", "note": "source note"},
            "row-2": {"decision": "Review"},
        }
        store = RowAnnotationStore(self.path, baseline=baseline)
        store.set("row-1", "decision", "Reject")
        store.remove("row-1", "note")
        store.set("row-3", "decision", "Added in sidecar")
        before = store.snapshot()

        result = store.compact()

        self.assertEqual(result["previous_events"], 3)
        self.assertEqual(result["events"], 3)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(store.history(), [])
        compacted = store.history(include_baseline=True)
        self.assertEqual(
            [event["op"] for event in compacted],
            ["snapshot_set", "snapshot_remove", "snapshot_set"],
        )
        self.assertTrue(all(event["baseline"] for event in compacted))
        self.assertIsNone(store.undo())

        self.assertEqual(
            RowAnnotationStore(self.path, baseline=baseline).snapshot(),
            before,
        )
        changed_source = {
            "row-1": {"decision": "Changed", "note": "changed note"},
            "row-2": {"decision": "Changed by source"},
        }
        self.assertEqual(
            RowAnnotationStore(self.path, baseline=changed_source).snapshot(),
            {
                "row-1": {"decision": "Reject"},
                "row-2": {"decision": "Changed by source"},
                "row-3": {"decision": "Added in sidecar"},
            },
        )

    def test_label_snapshot_and_cached_reads_reload_only_after_external_change(self):
        store = RowAnnotationStore(self.path)
        store.set("row-1", "decision", "Keep")
        store.set("row-1", "note", "reviewed")
        store.set("row-2", "decision", "Reject")

        with mock.patch.object(
            store, "_read_events_unlocked", wraps=store._read_events_unlocked
        ) as reader:
            self.assertEqual(
                store.snapshot_for_label("decision"),
                {"row-1": "Keep", "row-2": "Reject"},
            )
            self.assertEqual(store.annotations_for("row-1")["note"], "reviewed")
            first_revision = store.revision()
            self.assertEqual(reader.call_count, 0)

            other = RowAnnotationStore(self.path)
            other.set("row-3", "decision", "Maybe")
            self.assertEqual(
                store.snapshot_for_label("decision"),
                {"row-1": "Keep", "row-2": "Reject", "row-3": "Maybe"},
            )
            self.assertEqual(reader.call_count, 1)
            self.assertNotEqual(store.revision(), first_revision)
            self.assertEqual(reader.call_count, 1)

    def test_local_append_and_undo_are_incremental_not_full_log_replays(self):
        store = RowAnnotationStore(self.path)
        with mock.patch.object(
            store, "_read_events_unlocked", wraps=store._read_events_unlocked
        ) as reader:
            for index in range(1000):
                store.set(f"row-{index:04d}", "decision", "Keep")
            undone = store.undo(entry_id="row-0999", label="decision")

            self.assertEqual(reader.call_count, 0)
            self.assertEqual(undone["target_seq"], 1000)
            self.assertNotIn("row-0999", store.snapshot())
            self.assertEqual(store.get("row-0000", "decision"), "Keep")
            self.assertEqual(len(store.history()), 1001)
            self.assertEqual(reader.call_count, 0)

        reloaded = RowAnnotationStore(self.path)
        self.assertEqual(reloaded.snapshot(), store.snapshot())
        self.assertTrue(reloaded.history()[-2]["undone"])

    def test_undo_is_durable_filterable_and_marks_target_history(self):
        store = RowAnnotationStore(self.path)
        store.set("row-1", "decision", "Keep")
        store.set("row-2", "decision", "Reject")
        store.set("row-1", "decision", "Maybe")
        store.remove("row-1", "decision")

        undo_remove = store.undo(entry_id="row-1", label="decision")
        self.assertEqual(undo_remove["target_seq"], 4)
        self.assertEqual(undo_remove["previous"], None)
        self.assertEqual(undo_remove["current"], "Maybe")
        undo_set = store.undo(entry_id="row-1")
        self.assertEqual(undo_set["target_seq"], 3)
        self.assertEqual(undo_set["previous"], "Maybe")
        self.assertEqual(undo_set["current"], "Keep")
        self.assertEqual(
            store.snapshot(),
            {
                "row-1": {"decision": "Keep"},
                "row-2": {"decision": "Reject"},
            },
        )

        history = store.history(entry_id="row-1")
        self.assertEqual([event["seq"] for event in history], [1, 3, 4, 5, 6])
        self.assertTrue(next(event for event in history if event["seq"] == 3)["undone"])
        self.assertTrue(next(event for event in history if event["seq"] == 4)["undone"])
        self.assertEqual([event["seq"] for event in store.history(limit=2)], [5, 6])

        reloaded = RowAnnotationStore(self.path)
        self.assertEqual(reloaded.snapshot(), store.snapshot())
        final = reloaded.undo(label="decision")
        self.assertEqual(final["target_seq"], 2)
        self.assertEqual(reloaded.snapshot(), {"row-1": {"decision": "Keep"}})

    def test_truncated_final_line_is_ignored_then_removed_before_append(self):
        store = RowAnnotationStore(self.path)
        store.set("row-1", "decision", "Keep")
        with open(self.path, "ab") as handle:
            handle.write(b'{"schema":"torn')

        reloaded = RowAnnotationStore(self.path)
        self.assertEqual(
            reloaded.snapshot(), {"row-1": {"decision": "Keep"}}
        )
        reloaded.set("row-2", "decision", "Reject")

        with open(self.path, "rb") as handle:
            payload = handle.read()
        self.assertNotIn(b'"schema":"torn', payload)
        self.assertTrue(payload.endswith(b"\n"))
        self.assertEqual(len(payload.splitlines()), 2)
        self.assertEqual(
            RowAnnotationStore(self.path).snapshot(),
            {
                "row-1": {"decision": "Keep"},
                "row-2": {"decision": "Reject"},
            },
        )

    def test_newline_terminated_corruption_is_never_ignored(self):
        store = RowAnnotationStore(self.path)
        store.set("row-1", "decision", "Keep")
        with open(self.path, "ab") as handle:
            handle.write(b"not-json\n")

        with self.assertRaisesRegex(AnnotationStoreError, "line 2"):
            RowAnnotationStore(self.path)

        # A truncated tail after the bad complete record cannot hide it.
        with open(self.path, "ab") as handle:
            handle.write(b"partial")
        with self.assertRaisesRegex(AnnotationStoreError, "line 2"):
            RowAnnotationStore(self.path)

    def test_duplicate_fields_and_invalid_undo_target_are_corruption(self):
        duplicate = (
            '{"schema":"%s","schema":"%s","seq":1,'
            '"timestamp_utc":"2026-07-25T00:00:00+00:00",'
            '"op":"remove","entry_id":"row","label":"decision"}\n'
            % (ANNOTATION_SCHEMA, ANNOTATION_SCHEMA)
        )
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(duplicate)
        with self.assertRaisesRegex(AnnotationStoreError, "duplicate JSON field"):
            RowAnnotationStore(self.path)

        bad_undo = {
            "schema": ANNOTATION_SCHEMA,
            "seq": 1,
            "timestamp_utc": "2026-07-25T00:00:00+00:00",
            "op": "undo",
            "entry_id": "row",
            "label": "decision",
            "target_seq": 999,
        }
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(bad_undo) + "\n")
        with self.assertRaisesRegex(AnnotationStoreError, "not a reversible event"):
            RowAnnotationStore(self.path)

    def test_field_event_and_log_bounds_fail_without_damaging_state(self):
        store = RowAnnotationStore(self.path)
        with self.assertRaisesRegex(AnnotationStoreError, "entry_id must not be empty"):
            store.set("", "decision", "Keep")
        with self.assertRaisesRegex(AnnotationStoreError, "control character"):
            store.set("row\n1", "decision", "Keep")
        with self.assertRaisesRegex(AnnotationStoreError, "entry_id exceeds"):
            store.set("x" * (MAX_ENTRY_ID_LENGTH + 1), "decision", "Keep")
        with self.assertRaisesRegex(AnnotationStoreError, "value exceeds"):
            store.set("row", "decision", "x" * (MAX_VALUE_LENGTH + 1))
        with self.assertRaisesRegex(AnnotationStoreError, "value must be a string"):
            store.set("row", "decision", 7)
        with self.assertRaisesRegex(AnnotationStoreError, "valid UTF-8"):
            store.set("row", "decision", "\ud800")
        with self.assertRaisesRegex(AnnotationStoreError, "must not exceed"):
            RowAnnotationStore(
                self.path,
                max_log_bytes=65 * 1024 * 1024,
                max_event_bytes=1024,
            )
        self.assertFalse(os.path.exists(self.path))

        bounded_path = os.path.join(self.temporary.name, "bounded.jsonl")
        bounded = RowAnnotationStore(
            bounded_path, max_log_bytes=900, max_event_bytes=700
        )
        bounded.set("row-1", "decision", "x" * 300)
        with open(bounded_path, "rb") as handle:
            before = handle.read()
        with self.assertRaisesRegex(AnnotationStoreError, "would exceed"):
            bounded.set("row-2", "decision", "y" * 300)
        with open(bounded_path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(
            RowAnnotationStore(
                bounded_path, max_log_bytes=900, max_event_bytes=700
            ).snapshot(),
            {"row-1": {"decision": "x" * 300}},
        )

    def test_compact_is_atomic_preserves_mapping_and_clears_undo_history(self):
        store = RowAnnotationStore(self.path)
        store.set("row-2", "decision", "Reject")
        store.set("row-1", "decision", "Keep")
        store.set("row-1", "note", "reviewed")
        store.set("row-1", "decision", "Maybe")
        before = store.snapshot()

        result = store.compact()

        self.assertEqual(result["entry_count"], 2)
        self.assertEqual(result["annotation_count"], 3)
        self.assertEqual(result["previous_events"], 4)
        self.assertEqual(result["events"], 3)
        self.assertTrue(result["undo_history_cleared"])
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(store.history(), [])
        baseline = store.history(include_baseline=True)
        self.assertEqual([event["op"] for event in baseline], ["snapshot_set"] * 3)
        self.assertTrue(all(event["baseline"] for event in baseline))
        self.assertIsNone(store.undo())
        self.assertEqual(RowAnnotationStore(self.path).snapshot(), before)
        self.assertEqual(
            [name for name in os.listdir(os.path.dirname(self.path)) if name.endswith(".tmp")],
            [],
        )

    def test_failed_compact_keeps_original_and_cleans_temporary_file(self):
        store = RowAnnotationStore(self.path)
        store.set("row-1", "decision", "Keep")
        with open(self.path, "rb") as handle:
            original = handle.read()

        with mock.patch(
            "pymolplugins.annotation.annotation_store.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(AnnotationStoreError, "replace failed"):
                store.compact()

        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), original)
        self.assertEqual(
            [name for name in os.listdir(os.path.dirname(self.path)) if name.endswith(".tmp")],
            [],
        )
        self.assertEqual(store.reload(), {"row-1": {"decision": "Keep"}})

    def test_mutations_and_compaction_call_fsync(self):
        store = RowAnnotationStore(self.path)
        real_fsync = os.fsync
        with mock.patch(
            "pymolplugins.annotation.annotation_store.os.fsync",
            wraps=real_fsync,
        ) as fsync:
            store.set("row-1", "decision", "Keep")
            set_calls = fsync.call_count
            store.compact()
        self.assertGreaterEqual(set_calls, 1)
        self.assertGreaterEqual(fsync.call_count, set_calls + 1)

    def test_same_path_instances_are_thread_safe_and_see_each_other(self):
        stores = [RowAnnotationStore(self.path), RowAnnotationStore(self.path)]

        def write(index):
            store = stores[index % len(stores)]
            store.set(f"row-{index:03d}", "decision", str(index))
            return index

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            completed = list(executor.map(write, range(48)))

        self.assertEqual(completed, list(range(48)))
        snapshot = RowAnnotationStore(self.path).snapshot()
        self.assertEqual(len(snapshot), 48)
        self.assertEqual(snapshot["row-000"], {"decision": "0"})
        self.assertEqual(snapshot["row-047"], {"decision": "47"})
        history = stores[0].history()
        self.assertEqual(len(history), 48)
        self.assertEqual(sorted(event["seq"] for event in history), list(range(1, 49)))


if __name__ == "__main__":
    unittest.main()
