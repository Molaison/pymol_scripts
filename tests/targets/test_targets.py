import contextlib
import io
import unittest

from pymolplugins.targets import commands
from pymolplugins.targets.resolver import expand_targets, resolve_targets
from tests.navigation.test_navigation_adapter import FakeCmd


class ParserFakeCmd(FakeCmd):
    """Model the public ``cmd.do`` contract used by checked foreach dispatch."""

    def do(self, command, echo=1):
        marker_clause = "; /__import__('pymolplugins.targets.commands'"
        if marker_clause not in command:
            self.commands.append(command)
            return None
        original, marker_tail = command.rsplit(marker_clause, 1)
        self.commands.append(original)
        if "; silent_parser_error" in original:
            prefix = original.split(";", 1)[0].strip()
            if prefix.startswith("disable "):
                self.disable(prefix.split(None, 1)[1])
            # Model a valid mutating prefix followed by a swallowed parser
            # error. The completion marker is never reached.
            return None
        if original.startswith("silent_parser_error "):
            # Real PyMOL catches parser/CmdException failures and returns None.
            return None
        if original.startswith("raised_dispatch_error "):
            raise RuntimeError("synthetic command dispatch failure")
        if original.startswith("disable "):
            self.disable(original.split(None, 1)[1])
        commands._complete_execution(marker_tail.rsplit("'", 2)[1])
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

    def test_regex_is_one_scalar_with_commas_spaces_and_quantifiers(self):
        pattern = r"^design_(?:alpha|alfa) *[a-z]{0,1}$"

        matches = resolve_targets(self.fake, pattern, match="regex")

        self.assertEqual(
            {value.name for value in matches},
            {"design_alpha", "design_alfa"},
        )
        self.assertTrue(all(value.query == pattern for value in matches))

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
        direct_cgo = resolve_targets(self.fake, "a_cgo", kinds="object")
        self.assertEqual(
            [
                value.target
                for value in expand_targets(self.fake, direct_cgo, "molecules")
            ],
            ["a_cgo"],
        )

    def test_foreach_previews_only_when_commit_is_explicitly_disabled(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            preview = commands.pp_foreach(
                "root_a",
                "disable {target}",
                group_mode="leaves",
                commit=0,
            )
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.fake.commands, [])
        self.assertEqual(len(preview["commands"]), 4)
        self.assertIn("Preview only; nothing changed", output.getvalue().splitlines()[0])
        self.assertIn("commit=1", preview["commit_hint"])
        self.assertNotIn("completion_check", preview)
        self.assertEqual(
            preview["detail"]["completion_check"],
            "same-line parser sentinel",
        )

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

    def test_original_group_surface_command_executes_without_extra_flags(self):
        commands.register_commands(self.fake)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            result = self.fake.extensions["pp_foreach"](
                'root_a, "show surface,{target}"',
            )

        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["committed"])
        self.assertEqual(result["group_mode"], "molecules")
        self.assertEqual(result["executed_targets"], ["a_model"])
        self.assertEqual(self.fake.commands, ["show surface,a_model"])
        self.assertIn("pp_foreach applied 1 command(s)", output.getvalue())

    def test_mutating_fuzzy_ambiguity_requires_explicit_all_matches(self):
        self.fake.enabled.update({"design_alpha", "design_alfa"})
        before = set(self.fake.enabled)

        refused = commands.pp_foreach(
            "desgn_alpha",
            "disable {target}",
            match="fuzzy",
            fuzzy_cutoff=0.7,
            commit=1,
            quiet=1,
        )

        self.assertEqual(refused["status"], "error")
        self.assertEqual(refused["error_code"], "ambiguous_fuzzy_match")
        self.assertTrue(refused["scene_preserved"])
        self.assertEqual(self.fake.enabled, before)
        self.assertEqual(self.fake.commands, [])
        self.assertEqual(
            set(refused["ambiguous_matches"]["desgn_alpha"]),
            {"design_alpha", "design_alfa"},
        )

        applied = commands.pp_foreach(
            "desgn_alpha",
            "disable {target}",
            match="fuzzy",
            fuzzy_cutoff=0.7,
            commit=1,
            all_matches=1,
            quiet=1,
        )

        self.assertEqual(applied["status"], "applied")
        self.assertEqual(
            set(applied["executed_targets"]),
            {"design_alpha", "design_alfa"},
        )

    def test_auto_contains_ambiguity_requires_explicit_all_matches(self):
        self.fake.enabled.update({"design_alpha", "design_alfa"})
        before = set(self.fake.enabled)

        refused = commands.pp_foreach(
            "design_",
            "disable {target}",
            quiet=1,
        )

        self.assertEqual(refused["status"], "error")
        self.assertEqual(refused["error_code"], "ambiguous_auto_match")
        self.assertEqual(refused["ambiguous_match_methods"], ["contains"])
        self.assertTrue(refused["scene_preserved"])
        self.assertEqual(self.fake.enabled, before)
        self.assertEqual(self.fake.commands, [])

        applied = commands.pp_foreach(
            "design_",
            "disable {target}",
            all_matches=1,
            quiet=1,
        )

        self.assertEqual(applied["status"], "applied")
        self.assertEqual(
            set(applied["executed_targets"]),
            {"design_alpha", "design_alfa"},
        )

    def test_explicit_glob_and_contains_remain_intentional_set_matches(self):
        for mode, query in (("glob", "design_*"), ("contains", "design_")):
            with self.subTest(mode=mode):
                self.fake.commands.clear()
                self.fake.enabled.update({"design_alpha", "design_alfa"})

                result = commands.pp_foreach(
                    query,
                    "disable {target}",
                    match=mode,
                    kinds="object",
                    commit=1,
                    quiet=1,
                )

                self.assertEqual(result["status"], "applied")
                self.assertEqual(
                    set(result["executed_targets"]),
                    {"design_alpha", "design_alfa"},
                )

    def test_registered_foreach_normalizes_strict_console_quotes(self):
        commands.register_commands(self.fake)

        applied = self.fake.extensions["pp_foreach"](
            '"root_a", "disable {target}", group_mode="molecules", commit="1", quiet="1"',
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

    def test_foreach_reports_possible_partial_side_effects(self):
        self.fake.enabled.add("a_model")

        result = commands.pp_foreach(
            "a_model",
            "disable {target}; silent_parser_error",
            commit=1,
            quiet=1,
        )

        self.assertEqual(result["status"], "partial_error")
        self.assertEqual(result["attempted_targets"], ["a_model"])
        self.assertEqual(result["executed_targets"], [])
        self.assertEqual(result["possibly_modified_targets"], ["a_model"])
        self.assertTrue(result["partial_side_effect_possible"])
        self.assertNotIn("a_model", self.fake.enabled)

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

    def test_known_input_errors_are_concise_and_confirm_scene_preservation(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            result = commands.pp_foreach(
                "a_model",
                "disable {target}",
                on_error="not-a-policy",
            )

        message = output.getvalue()
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        self.assertEqual(self.fake.commands, [])
        self.assertIn("scene unchanged", message)
        self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
