import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pymolplugins.annotation.history_store import (
    HISTORY_ENVIRONMENT_VARIABLE,
    HistoryStoreError,
    configuration_snapshots,
    latest_config_snapshot,
    record_config_history,
)


class ConfigurationBrowseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "history.json"
        override = patch.dict(os.environ, {HISTORY_ENVIRONMENT_VARIABLE: str(self.path)})
        override.start()
        self.addCleanup(override.stop)

    def test_browsing_missing_history_does_not_create_files(self):
        self.assertEqual(configuration_snapshots(), [])
        self.assertFalse(self.path.exists())

    def test_complete_configs_for_one_source_stay_distinct_and_searchable(self):
        source = str(Path(self.temporary.name) / "CD47 candidates")
        record_config_history(source, {"label_name": "quality", "step_cmd": "zoom {object}"})
        record_config_history(source, {
            "annotation_fields": [{"name": "Reason", "type": "multiple", "required": True, "values": ["Interface", "Shape"]}],
            "step_cmd": "show cartoon, {object}",
            "score_guidance": {"columns": ["iptm"], "rules": {"iptm": {"direction": "higher", "good": .8}}},
            "filter_text": "where:iptm > 0.5", "sort_column": "iptm", "sort_descending": True,
        })
        before = self.path.read_bytes()
        all_configs = configuration_snapshots()
        self.assertEqual(len(all_configs), 2)
        self.assertEqual(all_configs[0]["config"]["step_cmd"], "show cartoon, {object}")
        matched = configuration_snapshots("cd47 INTERFACE cartoon")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["config"]["filter_text"], "where:iptm > 0.5")
        self.assertEqual(matched[0]["config"]["sort_column"], "iptm")
        self.assertEqual(configuration_snapshots("does-not-exist"), [])
        matched[0]["config"]["annotation_fields"][0]["values"].append("mutated")
        self.assertNotIn("mutated", latest_config_snapshot()["config"]["annotation_fields"][0]["values"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_history_is_reported_without_rewriting_it(self):
        self.path.write_text(json.dumps({"schema": "unsupported"}), encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(HistoryStoreError):
            configuration_snapshots()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
