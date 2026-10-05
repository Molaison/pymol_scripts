import unittest

from pymolplugins.annotation.filtering import FilterSyntaxError, compile_filter, validate_filter_presets


class ColumnFilterTests(unittest.TestCase):
    columns = ("AC.iptm", "score", "full_pass", "source batch", "notes")

    def test_numeric_boolean_and_quoted_columns(self):
        predicate = compile_filter('AC.iptm >= 0.8 and full_pass == True and `source batch` == "A"', self.columns)
        self.assertTrue(predicate({"AC.iptm": "0.9", "full_pass": "True", "source batch": "A"}))
        self.assertFalse(predicate({"AC.iptm": "0.7", "full_pass": "True", "source batch": "A"}))
        self.assertTrue(compile_filter("score < 10", self.columns)({"score": "2"}))
        self.assertFalse(compile_filter("score < 10", self.columns)({"score": "invalid"}))

    def test_empty_contains_parentheses_and_negative_numbers(self):
        predicate = compile_filter('score is None or (score < -1 and contains(notes, "KEEP"))', self.columns)
        self.assertTrue(predicate({"score": ""}))
        self.assertTrue(predicate({"score": "-2.5", "notes": "keep this"}))
        self.assertFalse(predicate({"score": "0", "notes": "keep"}))
        self.assertFalse(compile_filter("score > 0", self.columns)({"score": "NaN"}))
        self.assertTrue(compile_filter('notes == "`literal`"', self.columns)({"notes": "`literal`"}))

    def test_unknown_columns_and_execution_are_rejected(self):
        for expression in ('socre > 0', 'score >', '__import__("os")', 'notes.lower()', 'score[0]', '[score for score in notes]'):
            with self.subTest(expression=expression), self.assertRaises(FilterSyntaxError):
                compile_filter(expression, self.columns)


class FilterPresetTests(unittest.TestCase):
    def test_old_review_and_named_views_share_the_existing_filter_language(self):
        from pymolplugins.annotation.review_file import validate_review_config, validate_review_columns
        old = {"path_column": "path", "display_columns": ["score"]}
        self.assertEqual(validate_review_config(old), old)
        raw = [{"name": "Priority", "expression": "score >= 0.8", "sort_column": "score", "sort_descending": True},
               {"name": "Missing", "expression": "score is None"}]
        config = validate_review_config(dict(old, filter_presets=raw))
        validate_review_columns(config, ["path", "score"])
        self.assertFalse(config["filter_presets"][1]["sort_descending"])
        self.assertTrue(compile_filter(config["filter_presets"][1]["expression"], ["score"])({"score": ""}))

    def test_groups_can_overlap_without_mutating_rows(self):
        rows = [{"score": "0.5"}, {"score": "0.8"}, {"score": "0.9"}]
        groups = validate_filter_presets([{"name": "High", "expression": "score >= 0.8"},
                                          {"name": "Middle", "expression": "score <= 0.8"}], ["score"])
        members = [{i for i, row in enumerate(rows) if compile_filter(g["expression"], ["score"])(row)} for g in groups]
        self.assertEqual(members[0] & members[1], {1})
        self.assertEqual(rows, [{"score": "0.5"}, {"score": "0.8"}, {"score": "0.9"}])

    def test_invalid_group_definitions_are_rejected(self):
        valid = {"name": "High", "expression": "score > 0"}
        for value in (None, {}, [dict(valid, name=" ")], [dict(valid, expression="")], [valid, valid],
                      [dict(valid, sort_descending="true")], [dict(valid, extra=1)],
                      [dict(valid, expression="unknown > 0")], [dict(valid, expression="__import__('os')")],
                      [dict(valid, sort_column="unknown")]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_filter_presets(value, ["score"])

    def test_user_list_replaces_defaults_and_empty_list_removes_groups(self):
        import json
        import tempfile
        from pathlib import Path
        from pymolplugins.annotation.review_file import read_review_file, write_review_override
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "scores.csv"
            generated = Path(root) / "review.json"
            generated.write_text(json.dumps({"filter_presets": [{"name": "High", "expression": "score > 1"}]}))
            before = generated.read_bytes()
            write_review_override(str(source), {"filter_presets": [{"name": "Mine", "expression": "score > 2"}]})
            self.assertEqual(read_review_file(str(source)).config["filter_presets"][0]["name"], "Mine")
            write_review_override(str(source), {"filter_presets": []})
            self.assertEqual(read_review_file(str(source)).config["filter_presets"], [])
            self.assertEqual(generated.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
