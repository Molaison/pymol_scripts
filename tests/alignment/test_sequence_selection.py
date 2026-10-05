from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

import pymolplugins.core.sequence as sequence_core
from utilities.parameters import as_bool
from utilities.sequence import (
    SequenceMatchLimitError,
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

    def test_first_stops_stream_after_one_bounded_match(self):
        consumed = []

        def positions(_sequence, _query, _gap):
            consumed.append(1)
            yield (0,)
            raise AssertionError("occurrence=first enumerated beyond its first match")

        with mock.patch.object(sequence_core, "_bounded_positions", positions):
            matches = find_sequence_matches(
                self.index("A"),
                ["A"],
                gap_mode="bounded",
                max_gap=3,
                occurrence="first",
            )
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(consumed), 1)

    def test_unique_stops_as_soon_as_second_match_proves_ambiguity(self):
        yielded = []

        def positions(_sequence, _query, _gap):
            for position in ((0,), (0,)):
                yielded.append(position)
                yield position
            raise AssertionError("unique search enumerated after ambiguity was proven")

        with mock.patch.object(sequence_core, "_bounded_positions", positions):
            with self.assertRaises(SequenceOccurrenceError) as ambiguous:
                find_sequence_matches(
                    self.index("A"),
                    ["A"],
                    gap_mode="bounded",
                    max_gap=3,
                    occurrence="unique",
                )
        self.assertEqual(ambiguous.exception.match_count, 2)
        self.assertTrue(ambiguous.exception.at_least)
        self.assertEqual(len(yielded), 2)

    def test_all_stops_at_explicit_global_match_limit(self):
        yielded = []

        def positions(_sequence, _query, _gap):
            while True:
                yielded.append(1)
                yield (0,)

        with mock.patch.object(sequence_core, "_bounded_positions", positions):
            with self.assertRaises(SequenceMatchLimitError) as limited:
                find_sequence_matches(
                    self.index("A"),
                    ["A"],
                    gap_mode="bounded",
                    max_gap=3,
                    occurrence="all",
                    max_matches=5,
                )
        self.assertEqual(limited.exception.limit, 5)
        self.assertEqual(limited.exception.observed_at_least, 6)
        self.assertEqual(len(yielded), 6)


class FakeCmd:
    def __init__(self, atoms):
        self.atoms = atoms
        self.objects = {"protein"}
        self.selections = {}
        self.commands = {}
        self.auto_arg = {0: {}, 1: {}}
        self.object_sc = object()
        self.select_list_calls = []
        self.select_calls = []
        self.deleted = []
        self.shown = []
        self.colored = []
        self.fail_set_name = set()
        self.fail_set_name_to = set()
        self.fail_set_name_after_to = set()
        self.fail_set_name_after_old = set()
        self.fail_visualization = False

    def Shortcut(self, values):
        return values

    def extend(self, name, function):
        self.commands[name] = function

    def get_object_list(self, selection):
        names = []
        if "protein" in selection:
            names.append("protein")
        for selected_name, identities in self.selections.items():
            if "%{}".format(selected_name) in selection:
                names.extend(object_name for object_name, _index in identities)
        return list(dict.fromkeys(names))

    def get_names(self, mode="all", enabled_only=0, selection=""):
        if mode == "objects":
            return sorted(self.objects)
        if mode == "selections":
            return sorted(self.selections)
        return sorted(self.objects.union(self.selections))

    def get_type(self, name):
        if name in self.objects:
            return "object:molecule"
        if name in self.selections:
            return "selection"
        return ""

    def get_model(self, selection, state):
        return types.SimpleNamespace(atom=self.atoms)

    def get_unused_name(self, base):
        candidate = base + "_unused"
        suffix = 1
        while candidate in self.objects or candidate in self.selections:
            candidate = "{}_unused_{}".format(base, suffix)
            suffix += 1
        return candidate

    def select_list(self, name, object_name, indices, state=0, mode="index"):
        self.select_list_calls.append((name, object_name, list(indices), state, mode))
        self.selections[name] = {
            (str(object_name), int(index)) for index in indices
        }

    def select(self, name, expression):
        self.select_calls.append((name, expression))
        if expression == "none":
            self.selections[name] = set()
            return
        identities = set()
        for selected_name in re.findall(r"%([A-Za-z0-9_.-]+)", expression):
            identities.update(self.selections.get(selected_name, set()))
        self.selections[name] = identities

    def index(self, expression, state=0):
        identities = set()
        for selected_name in re.findall(r"%([A-Za-z0-9_.-]+)", expression):
            identities.update(self.selections.get(selected_name, set()))
        return sorted(identities)

    def set_name(self, old_name, new_name):
        if new_name in self.fail_set_name_to:
            self.fail_set_name_to.remove(new_name)
            raise RuntimeError("injected rename failure")
        if (old_name, new_name) in self.fail_set_name or old_name in self.fail_set_name:
            self.fail_set_name.discard((old_name, new_name))
            self.fail_set_name.discard(old_name)
            raise RuntimeError("injected rename failure")
        self.selections[new_name] = self.selections.pop(old_name)
        if old_name in self.fail_set_name_after_old:
            self.fail_set_name_after_old.remove(old_name)
            raise RuntimeError("injected post-rename failure")
        if new_name in self.fail_set_name_after_to:
            self.fail_set_name_after_to.remove(new_name)
            raise RuntimeError("injected post-rename failure")

    def delete(self, name):
        self.deleted.append(name)
        self.selections.pop(name, None)

    def show(self, representation, selection):
        if self.fail_visualization:
            raise RuntimeError("injected visualization failure")
        self.shown.append((representation, selection))

    def color(self, color, selection):
        if self.fail_visualization:
            raise RuntimeError("injected visualization failure")
        self.colored.append((color, selection))


class HostIndexSignatureFakeCmd(FakeCmd):
    """PyMOL 3.1 exposes ``cmd.index(selection, quiet=1)`` without state."""

    def index(self, expression):
        return super().index(expression)


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
        canonical = sys.modules["pymolplugins.alignment.sequence"]
        canonical.cmd = fake_cmd
        canonical._SELECTION_OWNERS.clear()
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
        self.assertEqual(
            fake.select_list_calls[0][1:],
            ("protein", list(range(1, 13)), 1, "index"),
        )
        self.assertNotRegex(fake.select_calls[-1][1], r"\bresi\b")
        for alias in ("pp_select_sequence", "sbs", "select_sequence", "seq_select"):
            self.assertIn(alias, fake.commands)

        legacy_result = fake.commands["sbs"](
            "protein", "AAA", visualize="0", allow_gaps="false"
        )
        self.assertIsInstance(legacy_result, dict)
        self.assertTrue(legacy_result["ok"])
        self.assertTrue(
            legacy_result["selection_ownership"]["replaced_owned_selection"]
        )

    def test_console_registration_removes_one_quote_layer(self):
        fake = FakeCmd(make_atoms("AAAA"))
        load_adapter(fake)

        result = fake.commands["pp_select_sequence"](
            "'''protein'''",
            '"AAA"',
            selection_name="'quoted_result'",
            visualize="'0'",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["objects"], ["protein"])
        self.assertEqual(result["queries"], ["AAA"])
        self.assertEqual(result["selection_name"], "quoted_result")

    def test_host_index_signature_uses_compatible_fallback(self):
        fake = HostIndexSignatureFakeCmd(make_atoms("AAAA"))
        adapter = load_adapter(fake)

        result = adapter.pp_select_sequence("protein", "AAA", visualize=0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_atom_count"], 12)

    def test_object_and_unowned_selection_collisions_do_not_mutate_scene(self):
        for collision_kind in ("object", "selection"):
            with self.subTest(collision_kind=collision_kind):
                fake = FakeCmd(make_atoms("AAAA"))
                if collision_kind == "object":
                    fake.objects.add("selected_residues")
                else:
                    fake.selections["selected_residues"] = {("protein", 1)}
                adapter = load_adapter(fake)
                baseline = dict(
                    (name, set(identity))
                    for name, identity in fake.selections.items()
                )

                result = adapter.pp_select_sequence(
                    "protein", "AAA", visualize=0
                )

                self.assertEqual(result["error_code"], "selection_name_collision")
                self.assertEqual(fake.selections, baseline)
                self.assertEqual(fake.select_list_calls, [])

    def test_modified_owned_selection_is_not_silently_replaced(self):
        fake = FakeCmd(make_atoms("AAAA"))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence("protein", "AAA", visualize=0)
        self.assertTrue(created["ok"])
        fake.selections["selected_residues"].add(("protein", 999))
        baseline = set(fake.selections["selected_residues"])

        refused = adapter.pp_select_sequence("protein", "A", visualize=0)

        self.assertEqual(refused["error_code"], "selection_name_collision")
        self.assertEqual(fake.selections["selected_residues"], baseline)

    def test_replace_zero_preserves_owned_selection_with_actionable_retry(self):
        fake = FakeCmd(make_atoms("AAAC"))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence("protein", "AAA", visualize=0)
        self.assertTrue(created["ok"])
        baseline = set(fake.selections["selected_residues"])

        refused = adapter.pp_select_sequence(
            "protein", "C", visualize=0, replace=0
        )

        self.assertEqual(refused["error_code"], "selection_name_collision")
        self.assertIn("replace=1", refused["guidance"])
        self.assertEqual(fake.selections["selected_residues"], baseline)

    def test_no_match_preserves_previous_owned_selection(self):
        fake = FakeCmd(make_atoms("AAAA"))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence("protein", "AAA", visualize=0)
        self.assertTrue(created["ok"])
        baseline = set(fake.selections["selected_residues"])

        missing = adapter.pp_select_sequence("protein", "C", visualize=0)

        self.assertEqual(missing["error_code"], "sequence_no_match")
        self.assertTrue(missing["scene_preserved"])
        self.assertEqual(fake.selections["selected_residues"], baseline)

    def test_multi_query_reports_unmatched_motif_as_partial(self):
        fake = FakeCmd(make_atoms("AAAA"))
        adapter = load_adapter(fake)

        result = adapter.pp_select_sequence(
            "protein", "AAA|C", visualize=0
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["unmatched_queries"], ["C"])
        self.assertIn("some queries had no match", result["partial_reasons"])
        self.assertIn("selected_residues", fake.selections)

    def test_replacement_rename_failure_restores_owned_selection(self):
        fake = FakeCmd(make_atoms("AAAC"))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence("protein", "AAA", visualize=0)
        self.assertTrue(created["ok"])
        baseline = set(fake.selections["selected_residues"])
        fake.fail_set_name_to.add("selected_residues")

        failed = adapter.pp_select_sequence("protein", "C", visualize=0)

        self.assertEqual(failed["error_code"], "selection_transaction_failed")
        self.assertTrue(failed["scene_preserved"])
        self.assertEqual(fake.selections["selected_residues"], baseline)
        self.assertEqual(set(fake.selections), {"selected_residues"})

    def test_first_creation_post_rename_failure_leaves_no_selection(self):
        fake = FakeCmd(make_atoms("AAAA"))
        fake.fail_set_name_after_to.add("selected_residues")
        adapter = load_adapter(fake)

        failed = adapter.pp_select_sequence("protein", "AAA", visualize=0)

        self.assertEqual(failed["error_code"], "selection_transaction_failed")
        self.assertTrue(failed["scene_preserved"])
        self.assertEqual(fake.selections, {})

    def test_post_rename_failure_also_restores_owned_selection(self):
        fake = FakeCmd(make_atoms("AAAC"))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence("protein", "AAA", visualize=0)
        self.assertTrue(created["ok"])
        baseline = set(fake.selections["selected_residues"])
        fake.fail_set_name_after_to.add("selected_residues")

        failed = adapter.pp_select_sequence("protein", "C", visualize=0)

        self.assertEqual(failed["error_code"], "selection_transaction_failed")
        self.assertTrue(failed["scene_preserved"])
        self.assertEqual(fake.selections["selected_residues"], baseline)
        self.assertEqual(set(fake.selections), {"selected_residues"})

    def test_post_backup_rename_failure_restores_owned_selection(self):
        fake = FakeCmd(make_atoms("AAAC"))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence("protein", "AAA", visualize=0)
        self.assertTrue(created["ok"])
        baseline = set(fake.selections["selected_residues"])
        fake.fail_set_name_after_old.add("selected_residues")

        failed = adapter.pp_select_sequence("protein", "C", visualize=0)

        self.assertEqual(failed["error_code"], "selection_transaction_failed")
        self.assertTrue(failed["scene_preserved"])
        self.assertEqual(fake.selections["selected_residues"], baseline)
        self.assertEqual(set(fake.selections), {"selected_residues"})

    def test_match_limit_fails_before_any_selection_mutation(self):
        fake = FakeCmd(make_atoms("AAAA"))
        adapter = load_adapter(fake)

        result = adapter.pp_select_sequence(
            "protein", "AA", occurrence="all", max_matches=2, visualize=0
        )

        self.assertEqual(result["error_code"], "sequence_match_limit")
        self.assertEqual(fake.selections, {})
        self.assertEqual(fake.select_list_calls, [])

    def test_visualization_failure_reports_partial_but_keeps_valid_selection(self):
        fake = FakeCmd(make_atoms("AAAA"))
        fake.fail_visualization = True
        adapter = load_adapter(fake)

        result = adapter.pp_select_sequence("protein", "AAA", visualize=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["visualization_status"], "failed")
        self.assertIn("selected_residues", fake.selections)


if __name__ == "__main__":
    unittest.main()
