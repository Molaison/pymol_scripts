import glob
import json
import os
import tempfile
import unittest
from unittest import mock

from pymolplugins.annotation.mapping_store import (
    DEFAULT_PROFILE_NAME,
    MAPPING_ENVIRONMENT_VARIABLE,
    MAPPING_SCHEMA,
    MAX_PREFIX_LENGTH,
    MAX_RULES_PER_PROFILE,
    MappingStoreError,
    get_mapping_profile,
    get_mapping_profile_readonly,
    global_mapping_path,
    list_mapping_profiles,
    mapping_store_status,
    read_mapping_store,
    remove_mapping_profile,
    upsert_mapping_profile,
)


class MappingStoreTests(unittest.TestCase):
    def test_readonly_lookup_uses_defaults_without_creating_or_quarantining(self):
        path = global_mapping_path()
        self.assertFalse(os.path.exists(path))

        profile = get_mapping_profile_readonly("x_k")

        self.assertEqual(profile["name"], "x_k")
        self.assertFalse(os.path.exists(path))

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        with self.assertRaises(MappingStoreError):
            get_mapping_profile_readonly("x_k")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(
            [name for name in os.listdir(os.path.dirname(path)) if ".corrupt-" in name],
            [],
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_override = os.environ.get(MAPPING_ENVIRONMENT_VARIABLE)
        os.environ[MAPPING_ENVIRONMENT_VARIABLE] = os.path.join(
            self.temporary.name, "global mappings", "profiles.json"
        )

    def tearDown(self):
        if self.previous_override is None:
            os.environ.pop(MAPPING_ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[MAPPING_ENVIRONMENT_VARIABLE] = self.previous_override
        self.temporary.cleanup()

    @staticmethod
    def _rule(source="/remote/project", target="D:\\project"):
        return {
            "source_prefix": source,
            "target_prefix": target,
            "priority": 20,
            "enabled": True,
            "case_sensitive": True,
            "require_exists": False,
            "provider": "test",
        }

    def test_first_access_persists_versioned_x_k_defaults_atomically(self):
        profiles = list_mapping_profiles()

        self.assertEqual([profile["name"] for profile in profiles], [DEFAULT_PROFILE_NAME])
        rules = profiles[0]["rules"]
        self.assertEqual(
            [rule["source_prefix"] for rule in rules],
            [
                "/xcfhome/zpzeng/mount_local",
                "/xcfhome/zpzeng",
                "~/",
            ],
        )
        self.assertEqual([rule["target_prefix"] for rule in rules], ["K:\\"] * 3)
        self.assertEqual([rule["priority"] for rule in rules], [300, 200, 100])
        with open(global_mapping_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema"], MAPPING_SCHEMA)
        self.assertEqual(glob.glob(os.path.dirname(global_mapping_path()) + "/*.tmp"), [])

    def test_upsert_get_list_and_remove_are_defensive_and_case_insensitive(self):
        saved = upsert_mapping_profile(
            "Lab Paths", [self._rule()], description="Lab mount"
        )
        saved["rules"][0]["target_prefix"] = "MUTATED"

        fetched = get_mapping_profile("lab paths")
        self.assertEqual(fetched["description"], "Lab mount")
        self.assertEqual(fetched["rules"][0]["target_prefix"], "D:\\project")
        upsert_mapping_profile("LAB PATHS", [self._rule(target="E:\\project")])
        matches = [
            profile
            for profile in list_mapping_profiles()
            if profile["name"].casefold() == "lab paths"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["rules"][0]["target_prefix"], "E:\\project")
        self.assertTrue(remove_mapping_profile("lab paths"))
        self.assertFalse(remove_mapping_profile("lab paths"))

    def test_removing_default_is_persistent_not_reseeded_on_every_read(self):
        self.assertTrue(remove_mapping_profile(DEFAULT_PROFILE_NAME))
        self.assertEqual(read_mapping_store()["profiles"], [])
        self.assertFalse(mapping_store_status()["default_profile_present"])

    def test_corrupt_store_is_quarantined_and_defaults_are_recreated(self):
        path = global_mapping_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not-json")

        profiles = list_mapping_profiles()

        self.assertEqual(profiles[0]["name"], DEFAULT_PROFILE_NAME)
        self.assertEqual(len(glob.glob(path + ".corrupt-*")), 1)
        with open(global_mapping_path(), "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["schema"], MAPPING_SCHEMA)

    def test_rejects_unbounded_and_mistyped_rule_values_without_mutation(self):
        before = (
            open(global_mapping_path(), "rb").read()
            if os.path.exists(global_mapping_path())
            else None
        )
        oversized = self._rule(source="x" * (MAX_PREFIX_LENGTH + 1))
        with self.assertRaisesRegex(MappingStoreError, "exceeds"):
            upsert_mapping_profile("bad", [oversized])
        mistyped = self._rule()
        mistyped["enabled"] = 1
        with self.assertRaisesRegex(MappingStoreError, "must be a boolean"):
            upsert_mapping_profile("bad", [mistyped])
        with self.assertRaisesRegex(MappingStoreError, "more than"):
            upsert_mapping_profile(
                "too-many-rules",
                [self._rule(source=f"/remote/{index}") for index in range(MAX_RULES_PER_PROFILE + 1)],
            )
        after = (
            open(global_mapping_path(), "rb").read()
            if os.path.exists(global_mapping_path())
            else None
        )
        self.assertEqual(after, before)

    def test_profile_count_limit_rejects_insert_but_allows_replacement(self):
        list_mapping_profiles()
        with mock.patch("pymolplugins.annotation.mapping_store.MAX_PROFILES", 1):
            with self.assertRaisesRegex(MappingStoreError, "more than 1 profiles"):
                upsert_mapping_profile("second", [self._rule()])
            updated = upsert_mapping_profile(
                DEFAULT_PROFILE_NAME, [self._rule()], description="replacement"
            )
        self.assertEqual(updated["description"], "replacement")
        self.assertEqual(len(list_mapping_profiles()), 1)

    def test_status_reports_override_counts_and_hidden_helper_is_used(self):
        with mock.patch(
            "pymolplugins.annotation.mapping_store.mark_hidden_on_windows"
        ) as hidden:
            status = mapping_store_status()

        hidden.assert_called_once_with(os.path.dirname(global_mapping_path()))
        self.assertTrue(status["exists"])
        self.assertTrue(status["environment_override"])
        self.assertEqual(status["profile_count"], 1)
        self.assertEqual(status["rule_count"], 3)
        self.assertGreater(status["size_bytes"], 0)

    def test_failed_atomic_replace_does_not_leave_partial_store(self):
        list_mapping_profiles()
        with open(global_mapping_path(), "rb") as handle:
            before = handle.read()

        with mock.patch(
            "pymolplugins.annotation.mapping_store.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                upsert_mapping_profile("new", [self._rule()])

        with open(global_mapping_path(), "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(
            glob.glob(os.path.join(os.path.dirname(global_mapping_path()), "*.tmp")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
