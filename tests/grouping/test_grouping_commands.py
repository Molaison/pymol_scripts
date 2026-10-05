import importlib
import inspect
import sys
import types
import unittest


class FakeCmd:
    """Small scene model with real-PyMOL-like group selection semantics."""

    def __init__(self):
        self.types = {}
        self.groups = {}
        self.object_selections = {}
        self.delete_calls = []
        self.group_calls = []
        self.align_calls = []
        self.create_calls = []
        self.extensions = {}

    def extend(self, name, callback):
        self.extensions[name] = callback

    def get_names(self, mode="all", enabled_only=0, selection=""):
        if selection:
            # PyMOL treats a group name here as an atom selection and returns
            # atom-bearing members, not the group object itself.
            return list(self.groups.get(selection, ()))
        return list(self.types)

    def get_type(self, name):
        return self.types.get(name, "")

    def get_object_list(self, selection="all"):
        token = str(selection).strip()
        if token.startswith("(") and token.endswith(")"):
            token = token[1:-1]
        if token in self.object_selections:
            return list(self.object_selections[token])
        return [
            name
            for name in self.groups.get(token, ())
            if self.types.get(name) == "object:molecule"
        ]

    def delete(self, name):
        self.delete_calls.append(name)
        self.types.pop(name, None)
        self.groups.pop(name, None)

    def group(self, group_name, member, action="add"):
        self.group_calls.append((group_name, member, action))
        self.types[group_name] = "object:group"
        self.groups.setdefault(group_name, []).append(member)

    def align(self, mobile, reference):
        self.align_calls.append((mobile, reference))
        return [0.25, 10, 2, 0.5, 12]

    def create(self, name, selection):
        self.create_calls.append((name, selection))
        self.types[name] = "object:molecule"


def _import_grouping_module():
    previous_pymol = sys.modules.get("pymol")
    bootstrap = FakeCmd()
    pymol = types.ModuleType("pymol")
    pymol.cmd = bootstrap
    sys.modules["pymol"] = pymol
    try:
        return importlib.import_module("pymolplugins.grouping.commands")
    finally:
        if previous_pymol is None:
            sys.modules.pop("pymol", None)
        else:
            sys.modules["pymol"] = previous_pymol


grouping = _import_grouping_module()


class GroupingCommandTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCmd()
        grouping.cmd = self.fake

    def _set_partition(self, names):
        self.fake.object_selections["chosen"] = list(names)
        self.fake.types.update({name: "object:molecule" for name in names})

    def _set_alignment_scene(self):
        self.fake.types.update(
            {
                "G1": "object:group",
                "G2": "object:group",
                "zeta": "object:molecule",
                "alpha": "object:molecule",
                "beta": "object:molecule",
                "other_b": "object:molecule",
                "other_a": "object:molecule",
            }
        )
        self.fake.groups.update(
            {
                "G1": ["zeta", "alpha", "beta"],
                "G2": ["other_b", "other_a"],
            }
        )

    def test_replace_rejects_every_non_group_name_collision_without_mutation(self):
        for collision_type in (
            "object:molecule",
            "object:cgo",
            "object:map",
            "selection",
        ):
            with self.subTest(collision_type=collision_type):
                self.setUp()
                self._set_partition(["sample_1", "sample_2"])
                self.fake.types["sample"] = collision_type
                before = dict(self.fake.types)

                result = grouping.group_objects_by_name(
                    selection="chosen", replace=1
                )

                self.assertEqual(result["status"], "error")
                self.assertIn("sample", result["error"])
                self.assertIn(collision_type, result["error"])
                self.assertEqual(result["groups"], {})
                self.assertEqual(self.fake.types, before)
                self.assertEqual(self.fake.delete_calls, [])
                self.assertEqual(self.fake.group_calls, [])

    def test_all_collisions_are_preflighted_before_replacing_any_group(self):
        self._set_partition(["alpha_1", "blocked_1"])
        self.fake.types.update(
            {
                "alpha": "object:group",
                "blocked": "object:molecule",
            }
        )
        before = dict(self.fake.types)

        result = grouping.group_objects_by_name(selection="chosen", replace=1)

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.fake.types, before)
        self.assertEqual(self.fake.delete_calls, [])
        self.assertEqual(self.fake.group_calls, [])

    def test_non_group_collision_is_rejected_even_without_replace(self):
        self._set_partition(["sample_1", "sample_2"])
        self.fake.types["sample"] = "object:cgo"
        before = dict(self.fake.types)

        result = grouping.group_objects_by_name(selection="chosen", replace=0)

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.fake.types, before)
        self.assertEqual(self.fake.delete_calls, [])
        self.assertEqual(self.fake.group_calls, [])

    def test_replace_accepts_an_existing_group_after_preflight(self):
        self._set_partition(["sample_2", "sample_1"])
        self.fake.types["sample"] = "object:group"
        self.fake.groups["sample"] = ["old_member"]

        result = grouping.group_objects_by_name(selection="chosen", replace=1)

        self.assertEqual(result, {"sample": ["sample_1", "sample_2"]})
        self.assertEqual(self.fake.delete_calls, ["sample"])
        self.assertEqual(
            self.fake.group_calls,
            [
                ("sample", "sample_1", "add"),
                ("sample", "sample_2", "add"),
            ],
        )

    def test_align_resolves_only_the_explicit_group_and_sorts_reference(self):
        self._set_alignment_scene()

        result = grouping.align_in_groups("G1")

        self.assertEqual(
            self.fake.align_calls,
            [("beta", "alpha"), ("zeta", "alpha")],
        )
        self.assertEqual(set(result), {"G1"})
        self.assertEqual(
            [mobile for mobile, _details in result["G1"]],
            ["beta", "zeta"],
        )

    def test_align_empty_or_unknown_group_never_falls_back_to_all(self):
        self._set_alignment_scene()
        for selection in ("", "NO_SUCH_GROUP"):
            with self.subTest(selection=selection):
                self.fake.align_calls.clear()

                result = grouping.align_in_groups(selection)

                self.assertEqual(result["status"], "error")
                self.assertTrue(result["error"])
                self.assertEqual(result["groups"], {})
                self.assertEqual(self.fake.align_calls, [])

    def test_align_supports_an_explicit_member_reference(self):
        self._set_alignment_scene()

        result = grouping.align_in_groups("G1", reference="zeta")

        self.assertEqual(
            self.fake.align_calls,
            [("alpha", "zeta"), ("beta", "zeta")],
        )
        self.assertEqual(set(result), {"G1"})

    def test_invalid_reference_is_rejected_before_any_alignment(self):
        self._set_alignment_scene()

        result = grouping.align_in_groups("G1", reference="other_a")

        self.assertEqual(result["status"], "error")
        self.assertIn("reference", result["error"])
        self.assertEqual(self.fake.align_calls, [])

    def test_reference_is_preflighted_for_every_group_before_alignment(self):
        self._set_alignment_scene()

        result = grouping.align_in_groups("G1,G2", reference="zeta")

        self.assertEqual(result["status"], "error")
        self.assertIn("G2", result["error"])
        self.assertEqual(self.fake.align_calls, [])

    def test_all_four_registered_commands_use_console_quote_normalization(self):
        self._set_partition(["sample_1", "sample_2"])
        self._set_alignment_scene()
        grouping.register_commands(self.fake)

        expected = {
            "group_objects_by_name",
            "merge_models_by_name",
            "align_in_groups",
            "merge_groups",
        }
        self.assertEqual(set(self.fake.extensions), expected)
        self.assertTrue(
            all(
                getattr(callback, "_pymolplugins_console_adapter", False)
                for callback in self.fake.extensions.values()
            )
        )

        grouped = self.fake.extensions["group_objects_by_name"](
            selection='"chosen"',
            method='"split_lastn_sep"',
            sep='"_"',
            num='"1"',
            prefix='"new_"',
            replace='"0"',
        )
        aligned = self.fake.extensions["align_in_groups"](
            '"G1"', reference='"zeta"'
        )

        self.assertEqual(
            grouped,
            {"new_sample": ["sample_1", "sample_2"]},
        )
        self.assertEqual(set(aligned), {"G1"})

    def test_merge_groups_has_a_strict_parser_safe_explicit_signature(self):
        grouping.register_commands(self.fake)
        callback = self.fake.extensions["merge_groups"]
        original = inspect.unwrap(callback)

        self.assertFalse(original.__code__.co_flags & 0xC)
        self.assertNotIn(
            inspect.Parameter.VAR_KEYWORD,
            {parameter.kind for parameter in inspect.signature(original).parameters.values()},
        )

        self._set_partition(["sample_1", "sample_2"])
        result = callback(
            model_selection='"chosen"',
            prefix='"new_"',
            methods='"split_lastn_sep"',
            sep='"_"',
            num='"1"',
            replace='"0"',
        )
        self.assertEqual(result, {"new_sample": ["sample_1", "sample_2"]})

    def test_known_input_errors_are_structured_for_all_command_paths(self):
        self._set_partition(["sample_1"])

        grouped = grouping.group_objects_by_name(
            selection="chosen", method="not_a_method"
        )
        merged = grouping.merge_models_by_name(
            selection="chosen", method="not_a_method"
        )
        legacy = grouping.merge_groups(
            model_selection="chosen", methods="not_a_method"
        )
        for result in (grouped, merged, legacy):
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["error"])


if __name__ == "__main__":
    unittest.main()
