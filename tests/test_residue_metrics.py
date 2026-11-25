"""Tests for residue hydropathy and CA contact metrics."""

import math
import random
import unittest

from pymolplugins.residues.metrics import KYTE_DOOLITTLE, contact_counts, hydropathy


def _residue(index, ca, object="obj", segi="", chain="A", sequence_index=None):
    return {
        "object": object,
        "segi": segi,
        "chain": chain,
        "resi": str(index + 1),
        "sequence_index": index if sequence_index is None else sequence_index,
        "ca": ca,
    }


def _brute_force(residues, cutoff, gap):
    counts = [None if residue["ca"] is None else 0 for residue in residues]
    pairs = []
    for i in range(len(residues)):
        for j in range(i + 1, len(residues)):
            first, second = residues[i], residues[j]
            if first["ca"] is None or second["ca"] is None:
                continue
            if first["object"] != second["object"]:
                continue
            same_segment = first["segi"] == second["segi"]
            if (
                same_segment
                and first["chain"] == second["chain"]
                and abs(first["sequence_index"] - second["sequence_index"]) <= gap
            ):
                continue
            distance = math.dist(first["ca"], second["ca"])
            if distance <= cutoff:
                pairs.append({"i": i, "j": j, "distance": distance})
                counts[i] += 1
                counts[j] += 1
    return counts, pairs


class HydropathyTests(unittest.TestCase):
    def test_known_kyte_doolittle_values(self):
        self.assertEqual(len(KYTE_DOOLITTLE), 20)
        self.assertEqual(KYTE_DOOLITTLE["ILE"], 4.5)
        self.assertEqual(KYTE_DOOLITTLE["ALA"], 1.8)
        self.assertEqual(KYTE_DOOLITTLE["ARG"], -4.5)
        self.assertEqual(KYTE_DOOLITTLE["TRP"], -0.9)
        self.assertEqual(hydropathy("Val"), 4.2)

    def test_unknown_and_aliases(self):
        self.assertEqual(hydropathy("MSE"), 1.9)
        self.assertEqual(hydropathy("mse"), 1.9)
        self.assertEqual(hydropathy("CYX"), 2.5)
        self.assertEqual(hydropathy("HSD"), -3.2)
        self.assertIsNone(hydropathy("UNK"))
        self.assertIsNone(hydropathy(""))
        self.assertIsNone(hydropathy(None))


class ContactCountTests(unittest.TestCase):
    def test_hand_geometry_cutoff_boundary(self):
        residues = [
            _residue(0, (0.0, 0.0, 0.0), sequence_index=10),
            _residue(1, (8.0, 0.0, 0.0), sequence_index=20),
            _residue(2, (0.0, 8.0001, 0.0), sequence_index=30),
        ]
        counts, pairs = contact_counts(residues, cutoff=8.0, gap=2)
        self.assertEqual(counts, [1, 1, 0])
        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0]["i"], pairs[0]["j"]), (0, 1))
        self.assertEqual(pairs[0]["distance"], 8.0)

    def test_same_chain_neighbor_cross_chain_and_cross_object(self):
        residues = [
            _residue(0, (0.0, 0.0, 0.0), sequence_index=10),
            _residue(1, (3.0, 0.0, 0.0), sequence_index=12),
            _residue(2, (0.0, 3.0, 0.0), chain="B", sequence_index=11),
            _residue(3, (3.0, 0.0, 0.0), object="other", sequence_index=10),
        ]
        counts, pairs = contact_counts(residues, cutoff=8.0, gap=2)
        self.assertEqual(counts, [1, 1, 2, 0])
        self.assertEqual([(p["i"], p["j"]) for p in pairs], [(0, 2), (1, 2)])

    def test_cross_segment_ignores_sequence_gap(self):
        residues = [
            _residue(0, (0.0, 0.0, 0.0), segi="S1", sequence_index=10),
            _residue(1, (0.0, 4.0, 0.0), segi="S2", sequence_index=11),
        ]
        counts, pairs = contact_counts(residues)
        self.assertEqual(counts, [1, 1])
        self.assertEqual([(p["i"], p["j"]) for p in pairs], [(0, 1)])

    def test_missing_ca_and_empty_input(self):
        residues = [_residue(0, None), _residue(1, (0.0, 0.0, 0.0))]
        self.assertEqual(contact_counts(residues), ([None, 0], []))
        self.assertEqual(contact_counts([]), ([], []))

    def test_invalid_boundary_arguments(self):
        for cutoff in (0, -1.0, math.inf, -math.inf, math.nan, "8", True):
            with self.subTest(cutoff=cutoff):
                with self.assertRaises(ValueError):
                    contact_counts([], cutoff=cutoff)
        for gap in (-1, 1.5, "2", True):
            with self.subTest(gap=gap):
                with self.assertRaises(ValueError):
                    contact_counts([], gap=gap)
        for ca in ((0.0, 0.0), (0.0, 0.0, math.nan), (0.0, 0.0, math.inf), "abc"):
            with self.subTest(ca=ca):
                with self.assertRaises(ValueError):
                    contact_counts([_residue(0, ca)])

    def test_matches_brute_force_for_about_30_points(self):
        rng = random.Random(20261126)
        residues = []
        for index in range(30):
            ca = None
            if index not in (4, 17):
                ca = (
                    rng.uniform(-10.0, 10.0),
                    rng.uniform(-10.0, 10.0),
                    rng.uniform(-10.0, 10.0),
                )
            residues.append(
                _residue(
                    index,
                    ca,
                    object="obj%d" % (index % 2),
                    segi="S1" if index < 20 else "S2",
                    chain="AB"[index % 2],
                    sequence_index=index,
                )
            )
        cutoff = 8.0
        gap = 2
        expected_counts, expected_pairs = _brute_force(residues, cutoff, gap)
        counts, pairs = contact_counts(residues, cutoff=cutoff, gap=gap)

        self.assertTrue(expected_pairs)
        self.assertEqual(counts, expected_counts)
        self.assertEqual(
            [(p["i"], p["j"]) for p in pairs],
            [(p["i"], p["j"]) for p in expected_pairs],
        )
        for actual, expected in zip(pairs, expected_pairs):
            self.assertAlmostEqual(actual["distance"], expected["distance"], places=12)


if __name__ == "__main__":
    unittest.main()
