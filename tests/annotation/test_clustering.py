
import unittest
from types import SimpleNamespace

from pymolplugins.annotation.cluster_review import compile_cluster_score, rank_clusters


class ClusterRankingTests(unittest.TestCase):
    def test_rank_by_expression_and_keep_original_row_ids(self):
        rows = [
            self.row("a", 0, ".9", "4"),
            self.row("b", 1, ".8", "1"),
            self.row("c", 2, ".1", ""),
            self.row("d", 3, ".8", "1"),
            self.row("e", 4, "3", "inf"),
        ]
        assignments = dict(a="one", b="one", c="one", d="one", e="two")
        columns = ("pair.iptm", "RMS deviation")
        score = compile_cluster_score("pair.iptm - 0.1 * `RMS deviation`", columns)
        higher = rank_clusters(reversed(rows), assignments, score)
        self.assertEqual(higher[0].members, ("b", "d", "a", "c"))
        self.assertAlmostEqual(higher[0].score, .7)
        self.assertIsNone(higher[1].score)
        lower = rank_clusters(rows, assignments, score, higher=False)
        self.assertEqual(lower[0].members, ("a", "b", "d", "c"))
        self.assertEqual(rank_clusters(rows, assignments, None)[0].members, ("a", "b", "c", "d"))

    def test_rejects_non_arithmetic_and_unknown_columns(self):
        for expression in ("unknown", "round(score)", "score > 1", "score ** 2", "score[0]", "`score"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                compile_cluster_score(expression, ("score",))
        score = compile_cluster_score("4 / (score - 1)", ("score",))
        self.assertIsNone(score({"score": "1"}))

    def row(self, identifier, ordinal, iptm, rmsd):
        return SimpleNamespace(id=identifier, ordinal=ordinal, display_name=identifier,
                               metadata={"pair.iptm": iptm, "RMS deviation": rmsd})
