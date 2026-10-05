import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pymolplugins.annotation.mapping_store import (
    LEGACY_MAPPING_SCHEMA, MAPPING_ENVIRONMENT_VARIABLE, MAPPING_SCHEMA,
    get_mapping_profile_readonly, read_mapping_store, upsert_mapping_profile,
)
from pymolplugins.annotation.path_resolver import PathMappingRule, PathResolver


class MappingProfileTests(unittest.TestCase):
    def test_ordered_text_rewrites_then_root_mapping_preserve_raw_path(self):
        profile = dict(name="lab", source_format="posix", target_format="windows", rules=[
            PathMappingRule("/remote/run7", "D:/models").to_dict()], rewrites=[
            dict(kind="strip_prefix", pattern="artifact:", replacement=""),
            dict(kind="regex", pattern=r"^job-(\d+):", replacement=r"/remote/run\1/"),
        ])
        result = PathResolver.from_profile(profile).resolve("artifact:job-7:model a.pdb", require_exists=False)
        self.assertEqual(result.raw_path, "artifact:job-7:model a.pdb")
        self.assertEqual(result.rewritten_path, "/remote/run7/model a.pdb")
        self.assertEqual(result.resolved_path, "D:\\models\\model a.pdb")
        self.assertTrue(result.is_resolved)

    def test_prefix_addition_can_supply_an_absolute_root(self):
        resolver = PathResolver([], target_format="posix", rewrites=[dict(kind="prepend", replacement="/data/")])
        self.assertEqual(resolver.resolve("model.pdb", require_exists=False).resolved_path, "/data/model.pdb")

    def test_windows_root_can_map_to_posix_and_windows(self):
        rule = PathMappingRule("Z:/Data", "/mnt/data", case_sensitive=False)
        result = PathResolver([rule], target_format="posix").resolve("z:\\data\\one.pdb", require_exists=False)
        self.assertEqual(result.resolved_path, "/mnt/data/one.pdb")
        self.assertFalse(PathResolver([rule], target_format="posix").resolve("Z:/Database/one.pdb", require_exists=False).is_resolved)
        result = PathResolver([PathMappingRule("Z:/Data", "D:/Local")]).resolve("Z:/Data/one.pdb", require_exists=False)
        self.assertEqual(result.resolved_path, "D:\\Local\\one.pdb")

    def test_wsl_drive_and_distribution_conversions_are_explicit(self):
        resolver = PathResolver([], source_format="wsl", target_format="windows")
        self.assertEqual(resolver.resolve("/mnt/c/data/a.pdb", require_exists=False).resolved_path, "C:\\data\\a.pdb")
        self.assertIn("distribution", resolver.resolve("/home/lab/a.pdb", require_exists=False).error)
        resolver = PathResolver([], source_format="wsl", target_format="windows", wsl_distribution="Debian")
        self.assertEqual(resolver.resolve("/home/lab/a.pdb", require_exists=False).resolved_path, "\\\\wsl.localhost\\Debian\\home\\lab\\a.pdb")
        resolver = PathResolver([], target_format="wsl")
        self.assertEqual(resolver.resolve("D:/models/a.pdb", require_exists=False).resolved_path, "/mnt/d/models/a.pdb")
        self.assertFalse(PathResolver([], source_format="posix").resolve("/home/lab/a.pdb", require_exists=False).is_resolved)

    def test_custom_and_posix_preserve_case_in_filesystem_cache(self):
        calls = []
        resolver = PathResolver([], target_format="custom", exists=lambda path: calls.append(path) or True)
        for path in ("/data/A.pdb", "/data/a.pdb"):
            self.assertEqual(resolver.resolve(path).resolved_path, path)
        self.assertEqual(calls, ["/data/A.pdb", "/data/a.pdb"])

    def test_invalid_regex_and_replacement_fail_before_use(self):
        for pattern, replacement in (("[", ""), ("(x)", r"\2"), ("(x)", r"\g<unknown>")):
            with self.assertRaisesRegex(ValueError, "rewrite 1"):
                PathResolver([], rewrites=[dict(kind="regex", pattern=pattern, replacement=replacement)])
        resolver = PathResolver([PathMappingRule("/remote", "D:/")], rewrites=[dict(kind="strip_prefix", pattern="prefix:")])
        self.assertIn("traversal", resolver.resolve("prefix:/remote/../secret.pdb").error)

    def test_profile_roundtrip_and_readonly_migration(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {MAPPING_ENVIRONMENT_VARIABLE: directory + "/profiles.json"}):
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(dict(schema=LEGACY_MAPPING_SCHEMA, updated_at_utc="", profiles=[dict(name="old", rules=[])])))
            before = path.read_bytes()
            self.assertEqual(get_mapping_profile_readonly("old")["name"], "old")
            self.assertEqual(path.read_bytes(), before)
            profile = dict(name="my server", rules=[PathMappingRule("/run", "/models").to_dict()],
                           source_format="posix", target_format="posix", wsl_distribution="",
                           rewrites=[dict(kind="strip_prefix", pattern="path:", replacement="", enabled=True)])
            saved = upsert_mapping_profile(profile)
            self.assertEqual(saved, get_mapping_profile_readonly("MY SERVER"))
            self.assertEqual(json.loads(path.read_text())["schema"], MAPPING_SCHEMA)

    def test_future_store_is_never_replaced_or_quarantined(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {MAPPING_ENVIRONMENT_VARIABLE: directory + "/profiles.json"}):
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(dict(schema="pymolplugins.structure-annotator-path-mappings/99.0", extra=True)))
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "unsupported mapping schema"):
                read_mapping_store()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_invalid_saved_regex_remains_visible_without_resetting_profiles(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {MAPPING_ENVIRONMENT_VARIABLE: directory + "/profiles.json"}):
            path = Path(directory) / "profiles.json"
            profile = dict(name="lab", rules=[], rewrites=[dict(kind="regex", pattern="[", replacement="")])
            path.write_text(json.dumps(dict(schema=MAPPING_SCHEMA, updated_at_utc="", profiles=[profile])))
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "rewrite 1"):
                read_mapping_store()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
