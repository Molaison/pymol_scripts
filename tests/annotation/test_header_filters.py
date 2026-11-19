import unittest
from types import SimpleNamespace

from pymolplugins.annotation.column_profiles import build_column_profiles
from pymolplugins.annotation.filtering import compile_view_filter
from pymolplugins.annotation.header_filters import HeaderChoice, HeaderFilters, read_header_filters


class HeaderFilterTests(unittest.TestCase):
    def test_choices_round_trip_and_preserve_the_manual_filter(self):
        base = """where:startswith(`review notes`, "__csv_column_0") or score > 4"""
        alpha, beta = HeaderChoice("category", "Alpha"), HeaderChoice("category", "Beta")
        run = HeaderChoice("category", "run-a")
        filters = HeaderFilters(base).change("task", alpha).change("task", beta).change("run", run)
        query = filters.query()
        self.assertIn("header_filter(task == 'Alpha' or task == 'Beta') and header_filter(run == 'run-a')", query)
        decoded = read_header_filters(query)
        self.assertEqual(decoded.selections, filters.selections)
        self.assertIn("`review notes`", decoded.base)
        self.assertIn("__csv_column_0", decoded.base)
        self.assertEqual(decoded.change("task", alpha).selections["task"], (beta,))
        self.assertNotIn("task", decoded.change("task", alpha).change("task", beta).selections)
        self.assertEqual(read_header_filters(HeaderFilters("Alpha").change("task", alpha).query()).base, "Alpha")

    def test_replace_range_preserves_missing_and_other_columns(self):
        missing = HeaderChoice("missing")
        other = HeaderChoice("other", ("A", "B"))
        low, high = HeaderChoice("range", (0.5, 2.0)), HeaderChoice("range", (3, 7))
        query = HeaderFilters().change("source batch", other).change("score", low).change("score", missing).query()
        decoded = read_header_filters(query)
        self.assertEqual(decoded.selections["source batch"], (other,))
        updated = decoded.change("score", high)
        self.assertEqual(updated.selections["score"], (missing, high))
        predicate = compile_view_filter(updated.query(), ("source batch", "score"))
        self.assertTrue(predicate(entry(**{"source batch": "C", "score": "5"})))
        self.assertFalse(predicate(entry(**{"source batch": "C", "score": "1"})))

    def test_alternatives_exclude_only_their_own_selection_and_keep_zero_count_values(self):
        rows = [entry(task=task, run=run, score=score) for task, run, score in
                (("Alpha", "1", "1"), ("Beta", "1", "2"), ("Gamma", "2", "3"))]
        filters = HeaderFilters().change("task", HeaderChoice("category", "Beta"))
        filters = filters.change("run", HeaderChoice("category", "2"))
        task, run = build_column_profiles((), ("task", "run"), {"run": "text"}, filters=filters, source=rows)
        self.assertEqual(task.categories, (("Gamma", 1), ("Beta", 0)))
        self.assertEqual(run.categories, (("1", 1), ("2", 0)))


def entry(**metadata):
    return SimpleNamespace(metadata=metadata, display_name="", source_path="", resolved_path="")
