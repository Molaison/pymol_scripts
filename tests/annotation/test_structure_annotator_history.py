import glob
import json
import os
import tempfile
import unittest

from pymolplugins.annotation.history_store import (
    HISTORY_ENVIRONMENT_VARIABLE,
    HISTORY_SCHEMA,
    HistoryStoreError,
    global_history_path,
    history_summary,
    latest_config_snapshot,
    read_global_history,
    record_config_history,
    search_config_history,
)


class StructureAnnotatorHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_override = os.environ.get(HISTORY_ENVIRONMENT_VARIABLE)
        os.environ[HISTORY_ENVIRONMENT_VARIABLE] = os.path.join(
            self.temporary.name, "global history", "annotator.json"
        )

    def tearDown(self):
        if self.previous_override is None:
            os.environ.pop(HISTORY_ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[HISTORY_ENVIRONMENT_VARIABLE] = self.previous_override
        self.temporary.cleanup()

    @staticmethod
    def _config(step_cmd="show cartoon, {object}", bucket_cmd="color cyan, {object}"):
        return {
            "pattern": r"^design_.*\.pdb$",
            "label_name": "review quality",
            "values_text": "Keep,Reject",
            "step_cmd": step_cmd,
            "bucket_cmds": {"Keep": bucket_cmd, "Reject": ""},
            "auto_advance": True,
            "keep_previous": False,
            "prefer_gz": True,
            "skip_annotated": False,
            "preload_enabled": True,
            "preload_ahead": 3,
            "preload_interval_ms": 250,
            "background_decompress_gz": True,
            "restore_last_dataset": True,
        }

    def test_records_atomic_versioned_snapshot_and_field_history(self):
        dataset = os.path.join(self.temporary.name, "dataset alpha")
        os.mkdir(dataset)

        written = record_config_history(dataset, self._config())

        self.assertEqual(written, global_history_path())
        with open(written, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema"], HISTORY_SCHEMA)
        self.assertEqual(payload["snapshots"][0]["dataset_dir"], dataset)
        self.assertEqual(
            payload["fields"]["step_cmd"][0]["value"],
            "show cartoon, {object}",
        )
        self.assertEqual(
            payload["fields"]["bucket_cmd"][0]["value"],
            "color cyan, {object}",
        )
        self.assertEqual(
            glob.glob(os.path.join(os.path.dirname(written), "*.tmp")), []
        )
        self.assertEqual(history_summary()["snapshot_count"], 1)

    def test_deduplicates_counts_and_fuzzy_ranks_commands(self):
        dataset = os.path.join(self.temporary.name, "dataset")
        os.mkdir(dataset)
        config = self._config()
        record_config_history(dataset, config)
        record_config_history(
            dataset,
            self._config(
                step_cmd="color magenta, {object}",
                bucket_cmd="hide everything, {object}",
            ),
        )
        record_config_history(dataset, config)

        history = read_global_history()
        matching_snapshots = [
            entry
            for entry in history["snapshots"]
            if entry["config"]["step_cmd"] == "show cartoon, {object}"
        ]
        self.assertEqual(len(matching_snapshots), 1)
        self.assertEqual(matching_snapshots[0]["count"], 2)
        self.assertEqual(
            search_config_history("step_cmd", "cartn")[0],
            "show cartoon, {object}",
        )
        self.assertEqual(
            search_config_history("bucket_cmd", "evrythng")[0],
            "hide everything, {object}",
        )
        self.assertEqual(
            search_config_history("step_cmd", "")[0],
            "show cartoon, {object}",
        )

    def test_latest_snapshot_is_defensive_and_preserves_full_config(self):
        dataset = os.path.join(self.temporary.name, "dataset")
        os.mkdir(dataset)
        record_config_history(dataset, self._config())

        snapshot = latest_config_snapshot()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["dataset_dir"], dataset)
        self.assertEqual(snapshot["config"]["preload_ahead"], 3)
        snapshot["config"]["preload_ahead"] = 19
        self.assertEqual(latest_config_snapshot()["config"]["preload_ahead"], 3)

    def test_corrupt_history_is_quarantined_and_recording_recovers(self):
        path = global_history_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not-json")

        with self.assertRaises(HistoryStoreError):
            read_global_history()
        record_config_history(self.temporary.name, self._config())

        self.assertEqual(read_global_history()["schema"], HISTORY_SCHEMA)
        self.assertEqual(len(glob.glob(path + ".corrupt-*")), 1)

    def test_unknown_search_field_is_rejected(self):
        with self.assertRaisesRegex(HistoryStoreError, "unknown history field"):
            search_config_history("not-a-field", "anything")


if __name__ == "__main__":
    unittest.main()
