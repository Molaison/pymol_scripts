import unittest
from types import SimpleNamespace

from pymolplugins.annotation.column_profiles import build_column_profiles, format_proportion


class ColumnProfileTests(unittest.TestCase):
    def test_categorical_counts_use_all_rows_including_missing(self):
        rows = [{"metadata": {"task": task}} for task in ("A", "B", "A", "C", "D", " ")]
        profile, = build_column_profiles(rows, ["task"], {}, preview=True)
        self.assertEqual((profile.total, profile.missing, profile.distinct), (6, 1, 4))
        self.assertEqual(profile.categories, (("A", 2), ("B", 1), ("C", 1)))
        self.assertEqual(profile.other, 1)
        self.assertEqual(format_proportion(2, profile.total), "33%")
        self.assertEqual(format_proportion(1, 2_000), "<1%")

    def test_numeric_bins_include_both_boundaries_and_keep_type_when_filtered(self):
        rows = [SimpleNamespace(metadata={"score": value}) for value in ("-1e308", "0", "1e308", "")]
        full, = build_column_profiles(rows, ["score"], {})
        self.assertEqual((full.minimum, full.maximum, full.distinct), (-1e308, 1e308, 3))
        self.assertEqual((full.bins[0], full.bins[6], full.bins[-1], sum(full.bins)), (1, 1, 1, 3))
        selected, = build_column_profiles(rows[1:2], ["score"], {"score": "number"})
        self.assertEqual((selected.minimum, selected.maximum, selected.bins), (0, 0, (1,)))
        self.assertEqual(selected.categories, ())
