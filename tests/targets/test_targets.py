import unittest

from pymolplugins.targets import commands
from pymolplugins.targets.resolver import expand_targets, resolve_targets
from tests.navigation.test_navigation_adapter import FakeCmd


class ParserFakeCmd(FakeCmd):
    """Model the public ``cmd.do`` contract used by checked foreach dispatch."""

    def do(self, command, echo=1):
        marker_clause = "; select ppfe_ack_"
        if marker_clause not in command:
            self.commands.append(command)
            return None
        original, marker_tail = command.rsplit("; select ", 1)
        self.commands.append(original)
        if original.startswith("silent_parser_error "):
            # Real PyMOL catches parser/CmdException failures and returns None.
            return None
        if original.startswith("raised_dispatch_error "):
            raise RuntimeError("synthetic command dispatch failure")
        marker = marker_tail.split(",", 1)[0].strip()
        self.types[marker] = "selection"
        self.enabled.discard(marker)
        return None


class TargetResolverTests(unittest.TestCase):
    def setUp(self):
        self.fake = ParserFakeCmd()
        self.fake.types.update(
            {
                "design_alpha": "object:molecule",
                "design_alfa": "object:molecule",
            }
        )
        commands.cmd = self.fake

    def test_exact_glob_contains_regex_and_fuzzy_matching(self):
        self.assertEqual(
            [value.name for value in resolve_targets(self.fake, "root_a")],
            ["root_a"],
        )
        self.assertEqual(
            {value.name for value in resolve_targets(self.fake, "design_*", match="glob")},
            {"design_alpha", "design_alfa"},
        )
        self.assertEqual(
            {value.name for value in resolve_targets(self.fake, "measure", match="contains")},
            {"a_measurement"},
        )
        self.assertEqual(
            {value.name for value in resolve_targets(self.fake, r"^a_(?:map|cgo)$", match="regex")},
            {"a_map", "a_cgo"},
        )
        fuzzy = resolve_targets(
            self.fake,
            "desgn_alpha",
            match="fuzzy",
            fuzzy_cutoff=0.7,
            limit=1,
        )
        self.assertEqual(fuzzy[0].name, "design_alpha")
        self.assertEqual(fuzzy[0].matched_by, "fuzzy")

    def test_group_can_remain_atomic_or_expand_every_non_group_leaf(self):
        matches = resolve_targets(self.fake, "root_a")
        self.assertEqual(
            [value.target for value in expand_targets(self.fake, matches, "atomic")],
            ["root_a"],
        )
        self.assertEqual(
            {value.target for value in expand_targets(self.fake, matches, "leaves")},
            {"a_model", "a_cgo", "a_map", "a_measurement"},
        )
        self.assertEqual(
            [value.target for value in expand_targets(self.fake, matches, "molecules")],
            ["a_model"],
        )

    def test_foreach_previews_by_default_and_executes_only_on_commit(self):
        preview = commands.pp_foreach(
            "root_a",
            "disable {target}",
            group_mode="leaves",
            commit=0,
            quiet=1,
        )
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.fake.commands, [])
        self.assertEqual(len(preview["commands"]), 4)

        applied = commands.pp_foreach(
            "a_*",
            "show cartoon, {target}",
            match="glob",
            kinds="object",
            commit=1,
            quiet=1,
        )
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(self.fake.commands), 4)
        self.assertTrue(all(value.startswith("show cartoon, a_") for value in self.fake.commands))
        self.assertFalse(
            any(name.startswith("ppfe_ack_") for name in self.fake.get_names("all"))
        )

    def test_registered_foreach_normalizes_strict_console_quotes(self):
        commands.register_commands(self.fake)

        applied = self.fake.extensions["pp_foreach"](
            '"root_a"',
            '"disable {target}"',
            group_mode='"molecules"',
            commit='"1"',
            quiet='"1"',
        )

        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["executed_targets"], ["a_model"])
        self.assertEqual(self.fake.commands, ["disable a_model"])

    def test_foreach_detects_silent_parser_errors_and_honors_policy(self):
        stopped = commands.pp_foreach(
            "a_*",
            "silent_parser_error {target}",
            match="glob",
            kinds="object",
            commit=1,
            on_error="stop",
            quiet=1,
        )
        self.assertEqual(stopped["status"], "partial_error")
        self.assertEqual(stopped["executed_targets"], [])
        self.assertEqual(len(stopped["errors"]), 1)

        continued = commands.pp_foreach(
            "a_*",
            "silent_parser_error {target}",
            match="glob",
            kinds="object",
            commit=1,
            on_error="continue",
            quiet=1,
        )
        self.assertEqual(continued["status"], "partial_error")
        self.assertEqual(continued["executed_targets"], [])
        self.assertEqual(len(continued["errors"]), 4)
        self.assertTrue(
            all("parser did not complete" in item["error"] for item in continued["errors"])
        )
        self.assertFalse(
            any(name.startswith("ppfe_ack_") for name in self.fake.get_names("all"))
        )

    def test_foreach_reports_cmd_do_exceptions_and_rejects_multiline_input(self):
        raised = commands.pp_foreach(
            "a_model",
            "raised_dispatch_error {target}",
            commit=1,
            quiet=1,
        )
        self.assertEqual(raised["status"], "partial_error")
        self.assertEqual(raised["executed_targets"], [])
        self.assertIn("dispatch raised RuntimeError", raised["errors"][0]["error"])

        before = list(self.fake.commands)
        multiline = commands.pp_foreach(
            "a_model",
            "disable {target}\nenable {target}",
            commit=1,
            quiet=1,
        )
        self.assertEqual(multiline["status"], "error")
        self.assertIn("one physical PyMOL command line", multiline["error"])
        self.assertEqual(self.fake.commands, before)


if __name__ == "__main__":
    unittest.main()
