import json
import os
import tempfile
import unittest

from pymolplugins.annotation.state_store import (
    STATE_SCHEMA,
    STATE_SCHEMA_V1,
    StateStoreError,
    csv_dataset_id,
    dataset_state_path,
    read_dataset_state,
    write_dataset_state,
)


class StructureAnnotatorStateStoreTests(unittest.TestCase):
    @staticmethod
    def _directory_payload():
        return {
            "updated_at_utc": "2026-07-25T00:00:00+00:00",
            "config": {"label_name": "review"},
            "position": {"index": 2, "file": "models/c.pdb"},
        }

    def test_directory_path_is_unchanged_and_new_writes_use_v2(self):
        with tempfile.TemporaryDirectory() as dataset:
            expected = os.path.join(
                dataset, ".pymolplugins", "structure_annotator.json"
            )
            self.assertEqual(dataset_state_path(dataset), expected)

            written = write_dataset_state(dataset, self._directory_payload())
            with open(written, "r", encoding="utf-8") as handle:
                document = json.load(handle)

            self.assertEqual(document["schema"], STATE_SCHEMA)
            self.assertEqual(
                document["source"],
                {"mode": "directory", "dataset_dir": os.path.abspath(dataset)},
            )
            self.assertEqual(
                document["position"], {"index": 2, "file": "models/c.pdb"}
            )
            self.assertEqual(
                [name for name in os.listdir(os.path.dirname(written)) if name.endswith(".tmp")],
                [],
            )

    def test_v1_directory_state_is_migrated_in_memory_then_on_next_write(self):
        with tempfile.TemporaryDirectory() as dataset:
            path = dataset_state_path(dataset)
            os.makedirs(os.path.dirname(path))
            legacy = self._directory_payload()
            legacy["schema"] = STATE_SCHEMA_V1
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)

            migrated = read_dataset_state(dataset)

            self.assertIsNotNone(migrated)
            self.assertEqual(migrated["schema"], STATE_SCHEMA)
            self.assertEqual(migrated["config"], legacy["config"])
            self.assertEqual(migrated["position"], legacy["position"])
            self.assertEqual(migrated["source"]["mode"], "directory")
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["schema"], STATE_SCHEMA_V1)

            write_dataset_state(dataset, migrated)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["schema"], STATE_SCHEMA)

    def test_csv_state_uses_portable_noncolliding_dataset_id(self):
        first = r"K:\runs\Scores 2026.csv"
        alias = r"\\wsl.localhost\Debian\mnt\k\runs\scores 2026.CSV"
        second_extension = r"K:\runs\Scores 2026.tsv"

        self.assertEqual(csv_dataset_id(first), csv_dataset_id(alias))
        self.assertNotEqual(csv_dataset_id(first), csv_dataset_id(second_extension))
        self.assertRegex(
            csv_dataset_id(first), r"^scores_2026\.[0-9a-f]{8}$"
        )

    def test_csv_state_persists_source_config_view_mapping_and_entry_cursor(self):
        with tempfile.TemporaryDirectory() as root:
            csv_path = os.path.join(root, "ranked scores.csv")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("structure,iptm,ddg\nmodel.pdb,0.9,-3.2\n")
            payload = {
                "config": {"label_name": "decision"},
                "position": {
                    "index": 0,
                    "file": "model.pdb",
                    "entry_id": "csv:row-2",
                },
                "csv": {
                    "path_column": "structure",
                    "id_column": "",
                    "encoding": "utf-8-sig",
                    "delimiter": ",",
                    "display_columns": ["iptm", "ddg"],
                },
                "view": {
                    "display_columns": ["iptm", "ddg"],
                    "filters": [{"column": "iptm", "operator": ">=", "value": 0.8}],
                    "sort": [{"column": "ddg", "direction": "asc"}],
                },
                "mapping_profile": {
                    "name": "x_k",
                    "rules": [
                        {"source_prefix": "/xcfhome/zpzeng", "target_prefix": "K:\\"}
                    ],
                },
            }

            written = write_dataset_state(csv_path, payload, source_mode="csv")
            expected = os.path.join(
                root,
                ".pymolplugins",
                f"structure_annotator.{csv_dataset_id(csv_path)}.json",
            )
            self.assertEqual(written, expected)
            self.assertEqual(
                dataset_state_path(root, csv_path=csv_path), expected
            )

            restored = read_dataset_state(csv_path, source_mode="csv")
            self.assertEqual(restored["schema"], STATE_SCHEMA)
            self.assertEqual(restored["source"]["mode"], "csv")
            self.assertEqual(restored["source"]["csv_path"], csv_path)
            self.assertEqual(restored["csv"]["path_column"], "structure")
            self.assertEqual(restored["view"]["sort"][0]["column"], "ddg")
            self.assertEqual(restored["mapping_profile"]["name"], "x_k")
            self.assertEqual(restored["position"]["entry_id"], "csv:row-2")

    def test_csv_files_with_same_stem_have_distinct_state_filenames(self):
        with tempfile.TemporaryDirectory() as root:
            csv_path = os.path.join(root, "scores.csv")
            tsv_path = os.path.join(root, "scores.tsv")
            first = dataset_state_path(csv_path, source_mode="csv")
            second = dataset_state_path(tsv_path, source_mode="csv")

            self.assertNotEqual(first, second)
            self.assertTrue(first.endswith(".json"))
            self.assertTrue(second.endswith(".json"))

    def test_case_distinct_csv_siblings_preserve_the_exact_legacy_owner(self):
        with tempfile.TemporaryDirectory() as root:
            lower = os.path.join(root, "scores.csv")
            upper = os.path.join(root, "Scores.csv")
            with open(lower, "w", encoding="utf-8") as handle:
                handle.write("structure_path,id\na.pdb,a\n")

            legacy_id = csv_dataset_id(lower)
            legacy_path = write_dataset_state(
                lower,
                {
                    "config": {"label_name": "legacy-review"},
                    "position": {"index": 0, "entry_id": "legacy-entry"},
                },
                source_mode="csv",
            )

            with open(upper, "w", encoding="utf-8") as handle:
                handle.write("structure_path,id\nb.pdb,b\n")

            lower_id = csv_dataset_id(lower)
            upper_id = csv_dataset_id(upper)
            self.assertNotEqual(lower_id, upper_id)
            self.assertEqual(lower_id, legacy_id)
            self.assertNotIn(".case-", lower_id)
            self.assertIn(".case-", upper_id)
            self.assertNotEqual(
                dataset_state_path(lower, source_mode="csv"),
                dataset_state_path(upper, source_mode="csv"),
            )

            restored_lower = read_dataset_state(lower, source_mode="csv")
            self.assertEqual(restored_lower["config"]["label_name"], "legacy-review")
            self.assertIsNone(read_dataset_state(upper, source_mode="csv"))

            migrated_path = write_dataset_state(
                lower, restored_lower, source_mode="csv"
            )
            self.assertEqual(migrated_path, legacy_path)
            with open(migrated_path, "r", encoding="utf-8") as handle:
                self.assertEqual(
                    json.load(handle)["source"]["csv_path"], lower
                )

    def test_no_case_collision_keeps_the_legacy_csv_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "Scores.csv")
            before_creation = csv_dataset_id(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("structure_path\na.pdb\n")

            self.assertEqual(csv_dataset_id(path), before_creation)
            self.assertNotIn(".case-", csv_dataset_id(path))

    def test_identity_marker_blocks_case_variant_from_claiming_deleted_owner(self):
        with tempfile.TemporaryDirectory() as root:
            lower = os.path.join(root, "scores.csv")
            upper = os.path.join(root, "Scores.csv")
            if os.path.normcase(lower) == os.path.normcase(upper):
                self.skipTest("filesystem cannot represent case-distinct CSV siblings")
            with open(lower, "w", encoding="utf-8") as handle:
                handle.write("structure_path\na.pdb\n")
            legacy_id = csv_dataset_id(lower)
            state_path = write_dataset_state(
                lower,
                {
                    "config": {},
                    "position": {"index": 0, "entry_id": ""},
                },
                source_mode="csv",
            )
            marker_names = [
                name
                for name in os.listdir(os.path.dirname(state_path))
                if name.endswith(".identity.json")
            ]
            self.assertEqual(len(marker_names), 1)

            os.unlink(state_path)
            os.unlink(lower)
            with open(upper, "w", encoding="utf-8") as handle:
                handle.write("structure_path\nb.pdb\n")

            upper_id = csv_dataset_id(upper)
            self.assertNotEqual(upper_id, legacy_id)
            self.assertIn(".case-", upper_id)

            with open(lower, "w", encoding="utf-8") as handle:
                handle.write("structure_path\na.pdb\n")
            self.assertEqual(csv_dataset_id(lower), legacy_id)
            self.assertEqual(csv_dataset_id(upper), upper_id)

    def test_invalid_entry_cursor_is_rejected_on_write(self):
        with tempfile.TemporaryDirectory() as dataset:
            payload = self._directory_payload()
            payload["position"]["entry_id"] = 123
            with self.assertRaisesRegex(StateStoreError, "position.entry_id"):
                write_dataset_state(dataset, payload)


if __name__ == "__main__":
    unittest.main()
