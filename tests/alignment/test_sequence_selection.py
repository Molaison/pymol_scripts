from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

from utilities.parameters import as_bool
from utilities.sequence import (
    SequenceOccurrenceError,
    build_residue_records,
    find_sequence_matches,
    index_atom_streams,
    split_queries,
)


THREE_LETTER = {
    "A": "ALA",
    "C": "CYS",
    "D": "ASP",
    "G": "GLY",
    "M": "MET",
}


def make_atoms(
    sequence,
    *,
    chain="",
    segi="",
    residue_ids=None,
    start_index=1,
    include_ca=True,
):
    residue_ids = residue_ids or [str(index + 1) for index in range(len(sequence))]
    atoms = []
    atom_index = start_index
    for letter, residue_id in zip(sequence, residue_ids):
        atom_names = ("N", "CA", "C") if include_ca else ("N", "C", "O")
        for atom_name in atom_names:
            atoms.append(
                {
                    "index": atom_index,
                    "name": atom_name,
                    "segi": segi,
                    "chain": chain,
                    "resi": residue_id,
                    "resn": THREE_LETTER[letter],
                    "hetatm": 0,
                }
            )
            atom_index += 1
    return atoms


class ParameterTests(unittest.TestCase):
    def test_string_booleans_are_strict(self):
        for token in ("0", "false", "FALSE", "off", " no "):
            with self.subTest(token=token):
                self.assertFalse(as_bool(token))
        for token in ("1", "true", "TRUE", "on", " yes "):
            with self.subTest(token=token):
                self.assertTrue(as_bool(token))
        with self.assertRaises(ValueError):
            as_bool("sometimes")


class ResidueIndexTests(unittest.TestCase):
    def test_blank_and_multi_character_chains_remain_distinct(self):
        blank = make_atoms("AC", chain="", start_index=1)
        multi = make_atoms("DG", chain="AB", start_index=20)
        index = index_atom_streams([("protein", 1, blank + multi)])

        self.assertEqual([track.chain for track in index.tracks], ["", "AB"])
        self.assertEqual([track.sequence for track in index.tracks], ["AC", "DG"])
        matches = find_sequence_matches(index, ["DG"], chain="AB")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].chain, "AB")

    def test_insertion_code_and_negative_number_are_not_coerced(self):
        atoms = make_atoms("AC", residue_ids=["-1", "42A"])
        records = build_residue_records(atoms, "protein", 1)

        self.assertEqual(records[0].resi, "-1")
        self.assertEqual(records[0].resv, -1)
        self.assertEqual(records[0].insertion, "")
        self.assertEqual(records[1].resi, "42A")
        self.assertEqual(records[1].resv, 42)
        self.assertEqual(records[1].insertion, "A")

    def test_modified_residue_missing_ca_and_altloc_keep_mapping(self):
        atoms = [
            dict(index=1, name="N", chain="", segi="", resi="1", resn="MSE", hetatm=1),
            dict(index=2, name="C", chain="", segi="", resi="1", resn="MSE", hetatm=1),
            dict(index=3, name="O", chain="", segi="", resi="1", resn="MSE", hetatm=1),
            dict(index=4, name="N", chain="", segi="", resi="2", resn="ALA", hetatm=0),
            dict(index=5, name="CA", chain="", segi="", resi="2", resn="ALA", hetatm=0),
            dict(index=6, name="CA", chain="", segi="", resi="2", resn="ALA", hetatm=0),
            dict(index=7, name="C", chain="", segi="", resi="2", resn="ALA", hetatm=0),
            dict(index=8, name="C1", chain="", segi="", resi="3", resn="LIG", hetatm=1),
            dict(index=9, name="N1", chain="", segi="", resi="3", resn="LIG", hetatm=1),
        ]
        index = index_atom_streams([("protein", 1, atoms)], protein_only=True)

        self.assertEqual(index.tracks[0].sequence, "MA")
        self.assertEqual(index.records[0].atom_indices, (1, 2, 3))
        self.assertEqual(index.records[1].atom_names["CA"], (5, 6))


class SequenceMatchingTests(unittest.TestCase):
    @staticmethod
    def index(sequence):
        return index_atom_streams([("protein", 1, make_atoms(sequence))])

    def test_exact_matches_overlap(self):
        matches = find_sequence_matches(self.index("AAAA"), "AAA")
        self.assertEqual(
            [match.residue_positions for match in matches],
            [(0, 1, 2), (1, 2, 3)],
        )

    def test_x_is_a_single_residue_wildcard(self):
        matches = find_sequence_matches(self.index("AGC"), ["AXC"])
        self.assertEqual([match.residue_positions for match in matches], [(0, 1, 2)])
        self.assertEqual(split_queries(" a-x-c | G C "), ("AXC", "GC"))

    def test_bounded_gap_never_becomes_unbounded(self):
        index = self.index("AGGC")
        self.assertEqual(
            find_sequence_matches(index, ["AC"], gap_mode="bounded", max_gap=1),
            (),
        )
        matches = find_sequence_matches(
            index, ["AC"], gap_mode="bounded", max_gap=2
        )
        self.assertEqual([match.residue_positions for match in matches], [(0, 3)])

    def test_unique_reports_zero_and_ambiguous_matches(self):
        index = self.index("AAAA")
        with self.assertRaises(SequenceOccurrenceError) as ambiguous:
            find_sequence_matches(index, ["AAA"], occurrence="unique")
        self.assertEqual(ambiguous.exception.match_count, 2)

        with self.assertRaises(SequenceOccurrenceError) as missing:
            find_sequence_matches(index, ["C"], occurrence="unique")
        self.assertEqual(missing.exception.match_count, 0)

    def test_multi_object_matches_keep_object_identity(self):
        index = index_atom_streams(
            [
                ("first", 1, make_atoms("AC", start_index=1)),
                ("second", 1, make_atoms("AC", start_index=20)),
            ]
        )
        matches = find_sequence_matches(index, ["AC"])
        self.assertEqual([match.object for match in matches], ["first", "second"])
        with self.assertRaises(SequenceOccurrenceError) as ambiguous:
            find_sequence_matches(index, ["AC"], occurrence="unique")
        self.assertEqual(ambiguous.exception.match_count, 2)


class FakeCmd:
    def __init__(self, atoms):
        self.atoms = atoms
        self.commands = {}
        self.auto_arg = {0: {}, 1: {}}
        self.object_sc = object()
        self.select_list_calls = []
        self.select_calls = []
        self.deleted = []
        self.shown = []
        self.colored = []

    def Shortcut(self, values):
        return values

    def extend(self, name, function):
        self.commands[name] = function

    def get_object_list(self, selection):
        return ["protein"]

    def get_model(self, selection, state):
        return types.SimpleNamespace(atom=self.atoms)

    def get_unused_name(self, base):
        return base + "_unused"

    def select_list(self, name, object_name, indices, state=0, mode="index"):
        self.select_list_calls.append((name, object_name, list(indices), state, mode))

    def select(self, name, expression):
        self.select_calls.append((name, expression))

    def delete(self, name):
        self.deleted.append(name)

    def show(self, representation, selection):
        self.shown.append((representation, selection))

    def color(self, color, selection):
        self.colored.append((color, selection))


def load_adapter(fake_cmd):
    pymol = types.ModuleType("pymol")
    pymol.cmd = fake_cmd
    previous = sys.modules.get("pymol")
    sys.modules["pymol"] = pymol
    module_name = "_test_select_by_sequence_adapter"
    path = (
        Path(__file__).resolve().parents[2]
        / "analysis"
        / "alignment_selection"
        / "select_by_sequence.py"
    )
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("pymol", None)
        else:
            sys.modules["pymol"] = previous


class AdapterTests(unittest.TestCase):
    def test_adapter_returns_structure_and_selects_exact_indices(self):
        fake = FakeCmd(make_atoms("AAAA"))
        adapter = load_adapter(fake)

        result = adapter.pp_select_sequence(
            "protein", "AAA", visualize="false", occurrence="all"
        )

        self.assertTrue(result["ok"])
        json.dumps(result)
        self.assertEqual(result["match_count"], 2)
        self.assertEqual(result["selected_atom_count"], 12)
        self.assertEqual(fake.shown, [])
        self.assertEqual(fake.colored, [])
        self.assertEqual(fake.select_list_calls[0][1:], ("protein", list(range(1, 13)), 1, "index"))
        self.assertNotRegex(fake.select_calls[-1][1], r"\bresi\b")
        for alias in ("pp_select_sequence", "sbs", "select_sequence", "seq_select"):
            self.assertIn(alias, fake.commands)

        legacy_result = fake.commands["sbs"](
            "protein", "AAA", visualize="0", allow_gaps="false"
        )
        self.assertIsInstance(legacy_result, dict)
        self.assertTrue(legacy_result["ok"])


if __name__ == "__main__":
    unittest.main()
