import unittest

from pymolplugins.annotation.history_store import fuzzy_matches


class FuzzyCompletionTests(unittest.TestCase):
    def test_exact_case_distinguishes_ssh_aliases(self):
        self.assertEqual(fuzzy_matches(["X", "x", "x_b"], "x")[:2], ["x", "X"])

    def test_exact_then_prefix_then_subsequence(self):
        values = ["interfacial_ddg", "score_iptm", "iptm_mean", "iptm"]
        result = fuzzy_matches(values, "iptm")
        self.assertEqual(result[:2], ["iptm", "iptm_mean"])
        self.assertIn("score_iptm", result)

    def test_noncontiguous_and_multiple_words_preserve_original_spelling(self):
        values = ["AC.iptm", "Interface confidence score", "Structure_Path"]
        self.assertEqual(fuzzy_matches(values, "STRP"), ["Structure_Path"])
        self.assertEqual(fuzzy_matches(values, "iface scr"), ["Interface confidence score"])
        self.assertEqual(fuzzy_matches(values, "acipm"), ["AC.iptm"])

    def test_typo_is_bounded_and_short_unrelated_values_are_not_suggestions(self):
        self.assertEqual(fuzzy_matches(["interface_ddg"], "interfcae_ddg"), ["interface_ddg"])
        self.assertEqual(fuzzy_matches(["structure_path", "interface_ddg"], "zz"), [])

    def test_empty_query_deduplicates_without_reordering(self):
        self.assertEqual(fuzzy_matches(["beta", "alpha", "beta", "Alpha"]), ["beta", "alpha", "Alpha"])
        self.assertEqual(fuzzy_matches(["beta", "alpha"], limit=1), ["beta"])

    def test_subsequence_ranking_prefers_compact_early_and_word_boundaries(self):
        values = ["xx_a---b---c", "xabc", "a-b-c", "axbyc"]
        self.assertEqual(fuzzy_matches(values, "abc"), ["xabc", "a-b-c", "axbyc", "xx_a---b---c"])
