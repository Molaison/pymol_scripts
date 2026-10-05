import glob
import json
import os
import tempfile
import unittest

from pymolplugins.annotation.history_store import (
    HISTORY_ENVIRONMENT_VARIABLE,
    HISTORY_SCHEMA,
    HISTORY_SCHEMA_V1,
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

    def test_annotation_interaction_preferences_roundtrip_both_values(self):
        for name in ("save_on_label_click", "show_source_row", "show_file_status"):
            for enabled in (True, False):
                config = dict(self._config(), **{name: enabled})
                record_config_history(self.temporary.name, config)
                self.assertIs(latest_config_snapshot()["config"][name], enabled)
            with self.assertRaisesRegex(HistoryStoreError, name + " must be a boolean"):
                record_config_history(self.temporary.name, dict(self._config(), **{name: "true"}))

    def test_records_csv_source_fields_and_fuzzy_column_history(self):
        csv_path = os.path.join(self.temporary.name, "ranked designs.csv")
        with open(csv_path, "w", encoding="utf-8") as handle:
            handle.write("structure,iptm,interface_ddg\nmodel.pdb,0.9,-3.1\n")

        config = self._config()
        config.update(
            {
                "csv_encoding": "gb18030",
                "csv_delimiter": "semicolon",
                "source_base": r"K:\portable structures",
                "stage_root": "/home/admin/structures",
                "stage_alias": "structures",
            }
        )
        record_config_history(
            "",
            config,
            source_mode="csv",
            csv_path=csv_path,
            path_column="structure",
            id_column="design_id",
            display_columns=["iptm", "interface_ddg"],
            mapping_profile={"name": "x_k", "rules": []},
        )

        snapshot = latest_config_snapshot()
        self.assertEqual(snapshot["source_mode"], "csv")
        self.assertEqual(snapshot["csv_path"], os.path.abspath(csv_path))
        self.assertEqual(snapshot["dataset_dir"], os.path.dirname(csv_path))
        self.assertEqual(snapshot["config"]["path_column"], "structure")
        self.assertEqual(snapshot["config"]["csv_encoding"], "gb18030")
        self.assertEqual(snapshot["config"]["csv_delimiter"], "semicolon")
        self.assertEqual(
            snapshot["config"]["source_base"], r"K:\portable structures"
        )
        self.assertEqual(
            snapshot["config"]["stage_root"], "/home/admin/structures"
        )
        self.assertEqual(snapshot["config"]["stage_alias"], "structures")
        self.assertEqual(
            snapshot["config"]["display_columns"],
            ["iptm", "interface_ddg"],
        )
        self.assertEqual(snapshot["config"]["mapping_profile"]["name"], "x_k")
        self.assertEqual(search_config_history("source_mode", "cs"), ["csv"])
        self.assertEqual(
            search_config_history("csv_path", "ranked")[0],
            os.path.abspath(csv_path),
        )
        self.assertEqual(
            search_config_history("path_column", "strct")[0], "structure"
        )
        self.assertEqual(
            search_config_history("display_columns", "interddg")[0],
            "interface_ddg",
        )
        self.assertEqual(
            search_config_history("mapping_profile", "xk")[0], "x_k"
        )
        self.assertEqual(
            search_config_history("csv_encoding", "gb18")[0], "gb18030"
        )
        self.assertEqual(
            search_config_history("csv_delimiter", "semicln")[0],
            "semicolon",
        )
        self.assertEqual(
            search_config_history("source_base", "portable")[0],
            r"K:\portable structures",
        )
        self.assertEqual(
            search_config_history("stage_root", "structres")[0],
            "/home/admin/structures",
        )
        self.assertEqual(
            search_config_history("stage_alias", "strct")[0], "structures"
        )
        summary = history_summary()
        self.assertEqual(summary["latest_source_mode"], "csv")
        self.assertEqual(summary["latest_csv_path"], os.path.abspath(csv_path))

    def test_v1_history_is_migrated_in_memory_and_rewritten_on_record(self):
        dataset = os.path.join(self.temporary.name, "legacy dataset")
        os.mkdir(dataset)
        path = global_history_path()
        os.makedirs(os.path.dirname(path))
        legacy = {
            "schema": HISTORY_SCHEMA_V1,
            "updated_at_utc": "2026-07-24T00:00:00+00:00",
            "snapshots": [
                {
                    "dataset_dir": dataset,
                    "recorded_at_utc": "2026-07-24T00:00:00+00:00",
                    "count": 1,
                    "config": self._config(),
                }
            ],
            "fields": {
                "dataset_dir": [
                    {
                        "value": dataset,
                        "count": 1,
                        "last_used_utc": "2026-07-24T00:00:00+00:00",
                    }
                ],
                "pattern": [],
                "label_name": [],
                "values_text": [],
                "step_cmd": [],
                "bucket_cmd": [],
            },
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)

        migrated = read_global_history()

        self.assertEqual(migrated["schema"], HISTORY_SCHEMA)
        self.assertEqual(migrated["snapshots"][0]["source_mode"], "directory")
        self.assertEqual(
            migrated["snapshots"][0]["config"]["source_mode"], "directory"
        )
        self.assertEqual(migrated["fields"]["csv_path"], [])
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["schema"], HISTORY_SCHEMA_V1)

        record_config_history(dataset, self._config())
        with open(path, "r", encoding="utf-8") as handle:
            rewritten = json.load(handle)
        self.assertEqual(rewritten["schema"], HISTORY_SCHEMA)
        self.assertEqual(rewritten["snapshots"][0]["count"], 2)


if __name__ == "__main__":
    unittest.main()
