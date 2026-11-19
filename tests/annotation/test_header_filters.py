import unittest
from types import SimpleNamespace

from pymolplugins.annotation.column_profiles import build_column_profiles, other_category_counts
from pymolplugins.annotation.filtering import compile_view_filter, remove_unmatched_parentheses
from pymolplugins.annotation.header_filters import HeaderChoice, HeaderFilters, normalize_filter_query, read_header_filters


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

    def test_old_append_chain_recovers_latest_ranges_and_one_category(self):
        old = ("where:((((((0.0 <= other_variant_ipsae <= 0.075523) and (0.426619 <= ipsae <= 0.853031))"
               " and (0.533275 <= ipsae <= 0.816941)) and (0.651585 <= ipsae <= 0.816941)) and (target == 'GLP1R'))"
               " and (target == 'GLP1R')) and (target == 'GLP1R')")
        normalized = normalize_filter_query(old, restore=True)
        self.assertEqual(normalized,
                         "where:header_filter(0.0 <= other_variant_ipsae <= 0.075523) and "
                         "header_filter(0.651585 <= ipsae <= 0.816941) and header_filter(target == 'GLP1R')")
        toggled = read_header_filters(normalized).change("target", HeaderChoice("category", "GLP1R"))
        self.assertNotIn("target", toggled.query())
        self.assertEqual(normalize_filter_query("where:(score > 2 or score is None) and (task == 'Alpha')"),
                         "where:(score > 2 or score is None) and header_filter(task == 'Alpha')")

    def test_duplicate_managed_conditions_and_standalone_legacy_choice(self):
        source = "where:header_filter(task == 'A' or task == 'A') and header_filter(task == 'B')"
        self.assertEqual(normalize_filter_query(source), "where:header_filter(task == 'A' or task == 'B')")
        self.assertEqual(normalize_filter_query("where:task == 'A'", restore=True), "where:header_filter(task == 'A')")
        self.assertEqual(normalize_filter_query("where:task == 'A'"), "where:task == 'A'")
        self.assertEqual(normalize_filter_query("where:task == 'A' and task == 'B'", restore=True),
                         "where:task == 'A' and task == 'B'")

    def test_unmatched_parentheses_leave_quoted_values_and_headers_intact(self):
        text = """(contains(`field)`, 'literal ( )') and (score > 4))"""
        missing_close = text[:-1]
        self.assertEqual(remove_unmatched_parentheses(missing_close), text[1:-1])
        self.assertEqual(remove_unmatched_parentheses(text + ")"), text)
        self.assertEqual(remove_unmatched_parentheses('task == "open ("'), 'task == "open ("')

    def test_other_counts_keep_hidden_options_and_zero_count_selections(self):
        rows = [entry(task=task) for task in ("Alpha", "Alpha", "Beta", "Gamma", "Delta", "Epsilon")]
        filters = HeaderFilters().change("task", HeaderChoice("category", "Zeta"))
        counts = other_category_counts((), "task", {"Alpha", "Beta", "Gamma"}, filters, rows)
        self.assertEqual(counts, (("Delta", 1), ("Epsilon", 1), ("Zeta", 0)))


def entry(**metadata):
    return SimpleNamespace(metadata=metadata, display_name="", source_path="", resolved_path="")
