import unittest

from pymolplugins.annotation.path_resolver import (
    DEFAULT_X_K_RULES,
    PathMappingRule,
    PathResolver,
    PathResolverError,
    STATUS_AMBIGUOUS,
    STATUS_AVAILABLE,
    STATUS_ERROR,
    STATUS_INVALID,
    STATUS_MISSING,
    STATUS_UNCHECKED,
    STATUS_UNREADABLE,
    resolve_path,
)


class FakeHostFilesystem:
    def __init__(self, existing=(), unreadable=(), directories=()):
        self.existing = {path.lower() for path in existing}
        self.unreadable = {path.lower() for path in unreadable}
        self.directories = {path.lower() for path in directories}
        self.exists_calls = []
        self.readable_calls = []
        self.is_file_calls = []

    def exists(self, path):
        self.exists_calls.append(path)
        return path.lower() in self.existing

    def readable(self, path):
        self.readable_calls.append(path)
        return path.lower() not in self.unreadable

    def is_file(self, path):
        self.is_file_calls.append(path)
        return path.lower() in self.existing and path.lower() not in self.directories


class PathResolverTests(unittest.TestCase):
    def test_default_x_k_profile_is_complete_and_literal(self):
        self.assertEqual(
            [rule.source_prefix for rule in DEFAULT_X_K_RULES],
            [
                "/xcfhome/zpzeng/mount_local",
                "/xcfhome/zpzeng",
                "~/",
            ],
        )
        self.assertEqual(
            [rule.target_prefix for rule in DEFAULT_X_K_RULES], ["K:\\"] * 3
        )
        self.assertEqual([rule.priority for rule in DEFAULT_X_K_RULES], [300, 200, 100])

        result = resolve_path("~/models/a.pdb", require_exists=False)

        self.assertEqual(result.resolved_path, "K:\\models\\a.pdb")
        self.assertEqual(result.matched_rule, "x_k_home_alias")
        self.assertEqual(result.provider, "sshfs-win")
        self.assertEqual(result.status, STATUS_UNCHECKED)

    def test_windows_drive_and_unc_paths_are_direct_candidates(self):
        filesystem = FakeHostFilesystem(
            existing=("K:\\models\\a.pdb", "\\\\server\\share\\b.cif")
        )
        resolver = PathResolver(
            (), exists=filesystem.exists, readable=filesystem.readable
        )

        drive = resolver.resolve("K:/models/a.pdb")
        unc = resolver.resolve("//server/share/b.cif")

        self.assertEqual(drive.resolved_path, "K:\\models\\a.pdb")
        self.assertEqual(unc.resolved_path, "\\\\server\\share\\b.cif")
        self.assertEqual(drive.status, STATUS_AVAILABLE)
        self.assertEqual(unc.status, STATUS_AVAILABLE)
        self.assertEqual(drive.method, "direct")
        self.assertEqual(drive.provider, "windows-host")

    def test_csv_relative_path_uses_windows_base_without_importing_os_semantics(self):
        filesystem = FakeHostFilesystem(existing=("C:\\data\\models\\a.pdb",))
        result = PathResolver(
            (), exists=filesystem.exists, readable=filesystem.readable
        ).resolve("models/a.pdb", source_base="C:\\data")

        self.assertEqual(result.status, STATUS_AVAILABLE)
        self.assertEqual(result.resolved_path, "C:\\data\\models\\a.pdb")
        self.assertEqual(result.method, "csv-relative")

    def test_csv_relative_path_can_join_unc_base(self):
        filesystem = FakeHostFilesystem(
            existing=("\\\\server\\share\\dataset\\a.pdb",)
        )
        result = PathResolver(
            (), exists=filesystem.exists, readable=filesystem.readable
        ).resolve("a.pdb", source_base="\\\\server\\share\\dataset")

        self.assertEqual(result.status, STATUS_AVAILABLE)
        self.assertEqual(
            result.resolved_path, "\\\\server\\share\\dataset\\a.pdb"
        )

    def test_posix_csv_relative_path_is_mapped_after_safe_join(self):
        filesystem = FakeHostFilesystem(existing=("K:\\datasets\\run\\a.pdb",))
        rule = PathMappingRule(
            "/remote", "K:\\", provider="test-mount", name="remote-k"
        )
        result = PathResolver(
            (rule,), exists=filesystem.exists, readable=filesystem.readable
        ).resolve("a.pdb", source_base="/remote/datasets/run")

        self.assertEqual(result.status, STATUS_AVAILABLE)
        self.assertEqual(result.resolved_path, "K:\\datasets\\run\\a.pdb")
        self.assertEqual(result.method, "csv-relative-mapping")
        self.assertEqual(result.matched_rule, "remote-k")

    def test_longest_prefix_wins_even_when_parent_rule_also_matches(self):
        filesystem = FakeHostFilesystem(existing=("K:\\model.pdb",))
        result = PathResolver(
            DEFAULT_X_K_RULES,
            exists=filesystem.exists,
            readable=filesystem.readable,
        ).resolve("/xcfhome/zpzeng/mount_local/model.pdb")

        self.assertEqual(result.status, STATUS_AVAILABLE)
        self.assertEqual(result.resolved_path, "K:\\model.pdb")
        self.assertEqual(result.matched_rule, "x_k_mount_local")
        self.assertEqual(len(result.candidates), 1)

    def test_prefix_matching_requires_a_component_boundary(self):
        result = resolve_path(
            "/xcfhome/zpzeng-other/model.pdb", require_exists=False
        )

        self.assertEqual(result.status, STATUS_MISSING)
        self.assertEqual(result.resolved_path, "")
        self.assertIn("no enabled path mapping rule", result.error)

    def test_priority_breaks_ties_between_equally_specific_rules(self):
        rules = (
            PathMappingRule(
                "/remote", "L:\\", priority=10, name="low", require_exists=False
            ),
            PathMappingRule(
                "/remote", "H:\\", priority=20, name="high", require_exists=False
            ),
        )

        result = PathResolver(rules).resolve("/remote/a.pdb")

        self.assertEqual(result.status, STATUS_UNCHECKED)
        self.assertEqual(result.resolved_path, "H:\\a.pdb")
        self.assertEqual(result.matched_rule, "high")

    def test_equal_precedence_existing_candidates_are_reported_as_ambiguous(self):
        rules = (
            PathMappingRule("/remote", "L:\\", priority=20, name="left"),
            PathMappingRule("/remote", "R:\\", priority=20, name="right"),
        )
        filesystem = FakeHostFilesystem(existing=("L:\\a.pdb", "R:\\a.pdb"))

        result = PathResolver(
            rules, exists=filesystem.exists, readable=filesystem.readable
        ).resolve("/remote/a.pdb")

        self.assertEqual(result.status, STATUS_AMBIGUOUS)
        self.assertEqual(result.resolved_path, "")
        self.assertEqual(len(result.candidates), 2)
        self.assertIn("left", result.error)
        self.assertIn("right", result.error)

    def test_one_existing_candidate_disambiguates_equal_rules(self):
        rules = (
            PathMappingRule("/remote", "L:\\", priority=20, name="left"),
            PathMappingRule("/remote", "R:\\", priority=20, name="right"),
        )
        filesystem = FakeHostFilesystem(existing=("R:\\a.pdb",))

        result = PathResolver(
            rules, exists=filesystem.exists, readable=filesystem.readable
        ).resolve("/remote/a.pdb")

        self.assertEqual(result.status, STATUS_AVAILABLE)
        self.assertEqual(result.resolved_path, "R:\\a.pdb")
        self.assertEqual(result.matched_rule, "right")
        self.assertEqual(len(result.candidates), 2)

    def test_repeated_and_alias_candidates_share_one_snapshot_probe(self):
        rules = (
            PathMappingRule(
                "/remote-a",
                "K:\\models",
                provider="provider-a",
                name="alias-a",
            ),
            PathMappingRule(
                "/remote-b",
                "k:/models/.",
                provider="provider-b",
                name="alias-b",
            ),
        )
        filesystem = FakeHostFilesystem(existing=("K:\\models\\same.pdb",))
        resolver = PathResolver(
            rules,
            exists=filesystem.exists,
            readable=filesystem.readable,
            is_file=filesystem.is_file,
        )

        first = resolver.resolve("/remote-a/same.pdb")
        repeated = resolver.resolve("/remote-a/same.pdb")
        alias = resolver.resolve("/remote-b/same.pdb")

        self.assertTrue(all(value.status == STATUS_AVAILABLE for value in (
            first, repeated, alias
        )))
        self.assertEqual(filesystem.exists_calls, ["K:\\models\\same.pdb"])
        self.assertEqual(filesystem.readable_calls, ["K:\\models\\same.pdb"])
        self.assertEqual(filesystem.is_file_calls, ["K:\\models\\same.pdb"])
        self.assertEqual(first.matched_rule, "alias-a")
        self.assertEqual(alias.matched_rule, "alias-b")
        self.assertEqual(first.provider, "provider-a")
        self.assertEqual(alias.provider, "provider-b")
        self.assertEqual(first.resolved_path, "K:\\models\\same.pdb")
        self.assertEqual(alias.resolved_path, "k:\\models\\same.pdb")

    def test_probe_cache_covers_failures_and_is_fresh_per_resolver(self):
        missing_fs = FakeHostFilesystem()
        missing_resolver = PathResolver(
            (),
            exists=missing_fs.exists,
            readable=missing_fs.readable,
            is_file=missing_fs.is_file,
        )
        self.assertEqual(missing_resolver.resolve("K:\\missing.pdb").status, STATUS_MISSING)
        self.assertEqual(missing_resolver.resolve("K:\\missing.pdb").status, STATUS_MISSING)
        self.assertEqual(len(missing_fs.exists_calls), 1)
        self.assertEqual(missing_fs.is_file_calls, [])
        self.assertEqual(missing_fs.readable_calls, [])

        unreadable_fs = FakeHostFilesystem(
            existing=("K:\\private.pdb",),
            unreadable=("K:\\private.pdb",),
        )
        unreadable_resolver = PathResolver(
            (),
            exists=unreadable_fs.exists,
            readable=unreadable_fs.readable,
            is_file=unreadable_fs.is_file,
        )
        self.assertEqual(
            unreadable_resolver.resolve("K:\\private.pdb").status,
            STATUS_UNREADABLE,
        )
        self.assertEqual(
            unreadable_resolver.resolve("K:\\private.pdb").status,
            STATUS_UNREADABLE,
        )
        self.assertEqual(len(unreadable_fs.exists_calls), 1)
        self.assertEqual(len(unreadable_fs.is_file_calls), 1)
        self.assertEqual(len(unreadable_fs.readable_calls), 1)

        unreadable_fs.existing.clear()
        self.assertEqual(
            unreadable_resolver.resolve("K:\\private.pdb").status,
            STATUS_UNREADABLE,
            "one resolver must remain a stable snapshot",
        )
        refreshed = PathResolver(
            (),
            exists=unreadable_fs.exists,
            readable=unreadable_fs.readable,
            is_file=unreadable_fs.is_file,
        ).resolve("K:\\private.pdb")
        self.assertEqual(refreshed.status, STATUS_MISSING)

        failure_calls = []

        def broken(path):
            failure_calls.append(path)
            raise OSError("probe failed")

        failure_resolver = PathResolver((), exists=broken)
        self.assertEqual(failure_resolver.resolve("K:\\error.pdb").status, STATUS_ERROR)
        self.assertEqual(failure_resolver.resolve("K:\\error.pdb").status, STATUS_ERROR)
        self.assertEqual(failure_calls, ["K:\\error.pdb"])

    def test_mapping_store_dictionary_shape_is_accepted(self):
        rule = {
            "source_prefix": "/remote/lab",
            "target_prefix": "M:\\lab",
            "priority": 42,
            "enabled": True,
            "case_sensitive": True,
            "require_exists": False,
            "provider": "lab-mount",
        }

        result = PathResolver((rule,)).resolve("/remote/lab/a.pdb")

        self.assertEqual(result.status, STATUS_UNCHECKED)
        self.assertEqual(result.resolved_path, "M:\\lab\\a.pdb")
        self.assertEqual(result.matched_rule, "/remote/lab")
        self.assertEqual(result.provider, "lab-mount")

        persisted = PathMappingRule.from_mapping(rule).to_dict()
        self.assertEqual(set(persisted), set(rule))
        self.assertEqual(persisted, rule)

    def test_disabled_and_case_sensitive_rules_are_respected(self):
        rules = (
            PathMappingRule(
                "/Remote", "D:\\", enabled=False, require_exists=False
            ),
            PathMappingRule(
                "/Remote",
                "E:\\",
                case_sensitive=False,
                require_exists=False,
                name="folded",
            ),
        )

        result = PathResolver(rules).resolve("/remote/a.pdb")

        self.assertEqual(result.resolved_path, "E:\\a.pdb")
        self.assertEqual(result.matched_rule, "folded")

    def test_require_exists_override_skips_injected_io(self):
        def unexpected(_path):
            raise AssertionError("filesystem callback should not run")

        resolver = PathResolver((), exists=unexpected, readable=unexpected)
        result = resolver.resolve("K:\\missing.pdb", require_exists=False)

        self.assertEqual(result.status, STATUS_UNCHECKED)
        self.assertTrue(result.is_resolved)

    def test_missing_and_unreadable_have_candidates_and_diagnostics(self):
        filesystem = FakeHostFilesystem(
            existing=("K:\\private.pdb",), unreadable=("K:\\private.pdb",)
        )
        resolver = PathResolver(
            (), exists=filesystem.exists, readable=filesystem.readable
        )

        missing = resolver.resolve("K:\\missing.pdb")
        unreadable = resolver.resolve("K:\\private.pdb")

        self.assertEqual(missing.status, STATUS_MISSING)
        self.assertEqual(missing.resolved_path, "K:\\missing.pdb")
        self.assertIn("does not exist", missing.error)
        self.assertEqual(unreadable.status, STATUS_UNREADABLE)
        self.assertIn("not readable", unreadable.error)

    def test_existing_directory_is_invalid_not_navigable(self):
        filesystem = FakeHostFilesystem(
            existing=("K:\\dataset",), directories=("K:\\dataset",)
        )

        result = PathResolver(
            (),
            exists=filesystem.exists,
            readable=filesystem.readable,
            is_file=filesystem.is_file,
        ).resolve("K:\\dataset")

        self.assertEqual(result.status, STATUS_INVALID)
        self.assertFalse(result.is_resolved)
        self.assertIn("not a regular file", result.error)
        self.assertFalse(result.candidates[0].regular_file)

    def test_callback_failure_becomes_error_result_not_exception(self):
        def broken(_path):
            raise OSError("probe failed")

        result = PathResolver((), exists=broken).resolve("K:\\a.pdb")

        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("probe failed", result.error)
        self.assertEqual(result.candidates[0].status, STATUS_ERROR)

    def test_invalid_inputs_reject_nul_relative_and_root_escape_traversal(self):
        resolver = PathResolver(())

        values = (
            resolver.resolve("bad\x00name.pdb"),
            resolver.resolve("a.pdb"),
            resolver.resolve("../outside.pdb", source_base="C:\\dataset"),
            resolver.resolve("~/../outside.pdb", require_exists=False),
            resolver.resolve("K:\\..\\outside.pdb", require_exists=False),
            resolver.resolve("K:relative.pdb", require_exists=False),
            resolver.resolve("\\root-relative.pdb", require_exists=False),
        )

        self.assertTrue(all(value.status == STATUS_INVALID for value in values))
        self.assertTrue(all(value.error for value in values))

    def test_internal_relative_normalization_does_not_escape_base(self):
        result = resolve_path(
            "models/old/../a.pdb",
            source_base="C:\\dataset",
            rules=(),
            require_exists=False,
        )

        self.assertEqual(result.status, STATUS_UNCHECKED)
        self.assertEqual(result.resolved_path, "C:\\dataset\\models\\a.pdb")

    def test_ambiguous_result_serializes_all_diagnostics_and_candidates(self):
        rules = (
            PathMappingRule(
                "/remote", "L:\\", priority=1, name="left", require_exists=False
            ),
            PathMappingRule(
                "/remote", "R:\\", priority=1, name="right", require_exists=False
            ),
        )

        payload = PathResolver(rules).resolve("/remote/a.pdb").to_dict()

        self.assertEqual(payload["raw_path"], "/remote/a.pdb")
        self.assertEqual(payload["status"], STATUS_AMBIGUOUS)
        self.assertEqual(len(payload["candidates"]), 2)
        self.assertTrue(payload["diagnostics"])

    def test_invalid_mapping_configuration_fails_early(self):
        with self.assertRaisesRegex(PathResolverError, "absolute POSIX"):
            PathMappingRule("relative", "K:\\")
        with self.assertRaisesRegex(PathResolverError, "absolute Windows"):
            PathMappingRule("/remote", "relative")
        with self.assertRaisesRegex(PathResolverError, "parent traversal"):
            PathMappingRule("/remote/../secret", "K:\\")


if __name__ == "__main__":
    unittest.main()
