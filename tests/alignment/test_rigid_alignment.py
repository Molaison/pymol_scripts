import importlib
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from utilities.math3d import (
    apply_matrix,
    compose_rigid,
    homogeneous_to_ttt,
    rotation_from_to,
    ttt_to_homogeneous,
)
from utilities.parameters import console_command

alignment = importlib.import_module("pymolplugins.alignment.rigid")


class Atom:
    def __init__(self, model, index, name, coord, elem="C", resn="LIG", resi="1", chain="A"):
        self.model = model
        self.index = index
        self.name = name
        self.coord = coord
        self.symbol = elem
        self.resn = resn
        self.resi = resi
        self.chain = chain
        self.segi = ""
        self.alt = ""
        self.resv = int(resi) if str(resi).isdigit() else 0
        self.hetatm = resn == "LIG"


class Model:
    def __init__(self, atoms):
        self.atom = atoms


class FakeCmd:
    def __init__(self):
        self.types = {
            "mobile_key": "object:molecule",
            "target_key": "object:molecule",
            "mobile_complex": "object:molecule",
            "mobile_cgo": "object:cgo",
            "payload": "object:group",
        }
        self.groups = {"payload": ["mobile_complex", "mobile_cgo"]}
        mobile = [(0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 4)]
        transform = compose_rigid(rotation_from_to((1, 0, 0), (0, 1, 0)), (5, -2, 3))
        target = [apply_matrix(transform, point) for point in mobile]
        names = ["C1", "N1", "O1", "S1"]
        elems = ["C", "N", "O", "S"]
        self.atoms = {
            "mobile_key": [
                Atom("mobile_key", i + 1, name, point, elem)
                for i, (name, point, elem) in enumerate(
                    zip(names, mobile, elems)
                )
            ],
            "target_key": [
                Atom("target_key", i + 1, name, point, elem)
                for i, (name, point, elem) in enumerate(
                    zip(names, target, elems)
                )
            ],
        }
        self.ttt = {name: None for name, kind in self.types.items() if kind != "object:group"}
        self.fail_next_set = set()
        self.fail_after_set = set()

    def get_type(self, name):
        return self.types.get(name, "")

    def get_names(self, mode="all", enabled_only=0, selection=""):
        if selection:
            return [
                name
                for name in self.groups.get(selection, [])
                if self.types.get(name) == "object:molecule"
            ]
        return list(self.types)

    def get_session(self, names="", partial=0, quiet=1):
        parent = {
            child: group for group, children in self.groups.items() for child in children
        }
        return {
            "names": [
                [name, 0, 1, None, -1, None, parent.get(name, "")]
                for name in self.types
            ]
        }

    def get_object_list(self, selection):
        hits = []
        for name in self.atoms:
            if name in selection:
                hits.append(name)
        return hits

    def get_model(self, selection, state=1):
        atoms = []
        for name, values in self.atoms.items():
            if name in selection:
                atoms.extend(values)
        return Model(atoms)

    def get_object_ttt(self, name, quiet=1):
        return self.ttt[name]

    def set_object_ttt(self, name, value, quiet=1):
        if name in self.fail_after_set:
            self.fail_after_set.remove(name)
            self.ttt[name] = tuple(value)
            raise RuntimeError(f"injected post-set failure for {name}")
        if name in self.fail_next_set:
            self.fail_next_set.remove(name)
            raise RuntimeError(f"injected set failure for {name}")
        self.ttt[name] = tuple(value)

    def get_object_matrix(self, name, state=1, incl_ttt=1):
        return ttt_to_homogeneous(self.ttt[name])


class RigidAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCmd()
        alignment.cmd = self.fake
        alignment._RECORDS.clear()
        alignment._LAST_RECORD_ID = ""
        alignment._COMMIT_COUNTER = 0

    def _install_alanine_pair(self, residue_count=4):
        self.fake.types.update(
            {
                "mobile_protein": "object:molecule",
                "target_protein": "object:molecule",
            }
        )
        self.fake.ttt.update({"mobile_protein": None, "target_protein": None})
        for object_name in ("mobile_protein", "target_protein"):
            atoms = []
            atom_index = 1
            for residue_index in range(residue_count):
                for atom_name, offset in (
                    ("N", (0.0, 0.0, 0.0)),
                    ("CA", (0.3, 0.5, 0.2)),
                    ("C", (0.8, 0.0, 0.1)),
                    ("O", (1.0, -0.2, 0.4)),
                ):
                    atoms.append(
                        Atom(
                            object_name,
                            atom_index,
                            atom_name,
                            (
                                residue_index * 2.0 + offset[0],
                                offset[1],
                                residue_index * 0.4 + offset[2],
                            ),
                            "O" if atom_name == "O" else "N" if atom_name == "N" else "C",
                            resn="ALA",
                            resi=str(residue_index + 1),
                        )
                    )
                    atom_index += 1
            self.fake.atoms[object_name] = atoms

    def test_preview_does_not_move_payload_then_commit_moves_group_leaves(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = alignment.pp_align_anchor(
                "mobile_key", "target_key", move="payload", match="name", commit=0
            )
        self.assertNotEqual(result.get("status"), "error")
        self.assertFalse(result["committed"])
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["changed"])
        self.assertEqual(
            result["next_command"], "pp_align_commit {}".format(result["id"])
        )
        self.assertEqual(result["parameters"]["move"], ["payload"])
        self.assertIn("Preview only; nothing changed", output.getvalue().splitlines()[0])
        self.assertIn("pp_align_commit {}".format(result["id"]), output.getvalue())
        self.assertIsNone(self.fake.ttt["mobile_complex"])
        self.assertIsNone(self.fake.ttt["mobile_cgo"])
        committed = alignment.pp_align_commit(result["id"])
        self.assertTrue(committed["committed"])
        self.assertTrue(committed["changed"])
        self.assertEqual(
            committed["next_command"],
            "pp_align_undo {}".format(result["id"]),
        )
        self.assertIsNotNone(self.fake.ttt["mobile_complex"])
        self.assertEqual(self.fake.ttt["mobile_complex"], self.fake.ttt["mobile_cgo"])
        self.assertIsNone(self.fake.ttt["mobile_key"])

    def test_preview_zero_requires_explicit_commit_and_has_no_side_effect(self):
        result = alignment.pp_align_anchor(
            "mobile_key",
            "target_key",
            move="mobile_complex",
            match="name",
            preview=0,
            commit=0,
        )

        self.assertEqual(result["error_code"], "alignment_no_action")
        self.assertEqual(alignment._RECORDS, {})
        self.assertIsNone(self.fake.ttt["mobile_complex"])

    def test_preview_zero_commit_one_directly_applies(self):
        result = alignment.pp_align_anchor(
            "mobile_key",
            "target_key",
            move="mobile_complex",
            match="name",
            preview=0,
            commit=1,
        )

        self.assertTrue(result["committed"])
        self.assertFalse(result["parameters"]["preview"])
        self.assertTrue(result["parameters"]["commit"])
        self.assertIsNotNone(self.fake.ttt["mobile_complex"])

    def test_preview_and_commit_flags_are_mutually_exclusive(self):
        with mock.patch.object(alignment, "_atom_points") as atom_points:
            result = alignment.pp_align_anchor(
                "mobile_key",
                "target_key",
                move="mobile_complex",
                match="name",
                preview=1,
                commit=1,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "alignment_action_conflict")
        self.assertIn("mutually exclusive", result["error"])
        self.assertEqual(alignment._RECORDS, {})
        self.assertIsNone(self.fake.ttt["mobile_complex"])
        atom_points.assert_not_called()

    def test_console_wrapper_accepts_quoted_selections_and_flags(self):
        wrapped = console_command(alignment.pp_align_anchor)

        result = wrapped(
            '"mobile_key"',
            "'target_key'",
            move="'''mobile_complex'''",
            match="'name'",
            preview="'1'",
            commit='"0"',
        )

        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["move_objects"], ["mobile_complex"])

    def test_console_commit_one_remains_a_one_flag_shortcut(self):
        wrapped = console_command(alignment.pp_align_anchor)

        result = wrapped(
            '"mobile_key"',
            '"target_key"',
            move='"mobile_complex"',
            match='"name"',
            commit='"1"',
        )

        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["changed"])
        self.assertIsNotNone(self.fake.ttt["mobile_complex"])

    def test_undo_restores_payload_baselines(self):
        baseline = compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (1, 2, 3))
        self.fake.ttt["mobile_complex"] = homogeneous_to_ttt(baseline)
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="mobile_complex", match="name", commit=1
        )
        self.assertTrue(result["committed"])
        undone = alignment.pp_align_undo(result["id"])
        self.assertEqual(undone["status"], "restored")
        self.assertEqual(undone["record_status"], "undone")
        self.assertEqual(self.fake.ttt["mobile_complex"], homogeneous_to_ttt(baseline))

    def test_implicit_undo_uses_latest_committed_not_latest_preview(self):
        committed = alignment.pp_align_anchor(
            "mobile_key",
            "target_key",
            move="mobile_complex",
            match="name",
            commit=1,
        )
        preview = alignment.pp_align_anchor(
            "mobile_key",
            "target_key",
            move="mobile_cgo",
            match="name",
            commit=0,
        )

        undone = alignment.pp_align_undo()

        self.assertEqual(undone["id"], committed["id"])
        self.assertTrue(undone["undone"])
        self.assertFalse(alignment._RECORDS[preview["id"]].committed)
        self.assertIsNone(self.fake.ttt["mobile_cgo"])
        self.assertEqual(
            ttt_to_homogeneous(self.fake.ttt["mobile_complex"]),
            ttt_to_homogeneous(None),
        )

    def test_implicit_undo_uses_commit_time_not_preview_creation_order(self):
        first_preview = alignment.pp_align_anchor(
            "mobile_key",
            "target_key",
            move="mobile_complex",
            match="name",
            commit=0,
        )
        second_preview = alignment.pp_align_anchor(
            "mobile_key",
            "target_key",
            move="mobile_cgo",
            match="name",
            commit=0,
        )
        alignment.pp_align_commit(second_preview["id"])
        alignment.pp_align_commit(first_preview["id"])

        undone = alignment.pp_align_undo()

        self.assertEqual(undone["id"], first_preview["id"])
        self.assertFalse(alignment._RECORDS[first_preview["id"]].committed)
        self.assertTrue(alignment._RECORDS[second_preview["id"]].committed)

    def test_partial_undo_failure_rolls_forward_to_committed_state(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=1
        )
        committed_complex = self.fake.ttt["mobile_complex"]
        committed_cgo = self.fake.ttt["mobile_cgo"]
        self.fake.fail_next_set.add("mobile_cgo")

        failed = alignment.pp_align_undo(result["id"])

        self.assertEqual(failed["error_code"], "alignment_undo_failed")
        self.assertTrue(failed["scene_preserved"])
        self.assertEqual(self.fake.ttt["mobile_complex"], committed_complex)
        self.assertEqual(self.fake.ttt["mobile_cgo"], committed_cgo)
        self.assertTrue(alignment._RECORDS[result["id"]].committed)

    def test_post_set_undo_failure_rolls_forward_to_committed_state(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=1
        )
        committed = {
            name: self.fake.ttt[name]
            for name in ("mobile_complex", "mobile_cgo")
        }
        self.fake.fail_after_set.add("mobile_cgo")

        failed = alignment.pp_align_undo(result["id"])

        self.assertEqual(failed["error_code"], "alignment_undo_failed")
        self.assertTrue(failed["scene_preserved"])
        self.assertEqual(self.fake.ttt["mobile_complex"], committed["mobile_complex"])
        self.assertEqual(self.fake.ttt["mobile_cgo"], committed["mobile_cgo"])
        self.assertTrue(alignment._RECORDS[result["id"]].committed)

    def test_partial_commit_failure_rolls_back_every_payload_object(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=0
        )
        self.fake.fail_next_set.add("mobile_cgo")
        failed = alignment.pp_align_commit(result["id"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("alignment commit failed", failed["error"])
        self.assertFalse(alignment._RECORDS[result["id"]].committed)
        identity = ttt_to_homogeneous(None)
        self.assertEqual(ttt_to_homogeneous(self.fake.ttt["mobile_complex"]), identity)
        self.assertEqual(ttt_to_homogeneous(self.fake.ttt["mobile_cgo"]), identity)

    def test_post_set_commit_failure_also_rolls_back_every_payload_object(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=0
        )
        self.fake.fail_after_set.add("mobile_cgo")

        failed = alignment.pp_align_commit(result["id"])

        self.assertEqual(failed["error_code"], "alignment_commit_failed")
        self.assertTrue(failed["scene_preserved"])
        identity = ttt_to_homogeneous(None)
        self.assertEqual(
            ttt_to_homogeneous(self.fake.ttt["mobile_complex"]), identity
        )
        self.assertEqual(ttt_to_homogeneous(self.fake.ttt["mobile_cgo"]), identity)

    def test_preview_commit_rejects_stale_move_object(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=0
        )
        moved = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), (0.5, 0.0, 0.0)
        )
        self.fake.ttt["mobile_complex"] = homogeneous_to_ttt(moved)
        with mock.patch.object(alignment.traceback, "print_exc") as print_exc:
            failed = alignment.pp_align_commit(result["id"])
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["error_code"], "stale_alignment_preview")
        self.assertIn("preview is stale", failed["error"])
        self.assertEqual(self.fake.ttt["mobile_complex"], homogeneous_to_ttt(moved))
        print_exc.assert_not_called()

    def test_preview_commit_rejects_stale_anchor_coordinates(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=0
        )
        target_atom = self.fake.atoms["target_key"][0]
        target_atom.coord = (
            target_atom.coord[0] + 0.75,
            target_atom.coord[1],
            target_atom.coord[2],
        )
        failed = alignment.pp_align_commit(result["id"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("anchor atoms or transforms changed", failed["error"])
        self.assertIsNone(self.fake.ttt["mobile_complex"])
        self.assertIsNone(self.fake.ttt["mobile_cgo"])

    def test_record_exports_state_hash_parameters_and_backend(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", state=1
        )
        self.assertEqual(result["state"], 1)
        self.assertEqual(len(result["input_hash"]), 64)
        self.assertEqual(result["parameters"]["match"], "name")
        self.assertEqual(result["backend"]["name"], "pymolplugins.math3d")
        self.assertEqual(result["backend"]["version"], "1.0")

    def test_undo_refuses_post_commit_edits_unless_forced(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="mobile_complex", match="name", commit=1
        )
        external = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (50, 0, 0),
        )
        self.fake.ttt["mobile_complex"] = homogeneous_to_ttt(external)
        with mock.patch.object(alignment.traceback, "print_exc") as print_exc:
            refused = alignment.pp_align_undo(result["id"])
        self.assertEqual(refused["status"], "error")
        self.assertEqual(refused["error_code"], "stale_alignment_undo")
        self.assertIn("changed after commit", refused["error"])
        self.assertEqual(
            self.fake.ttt["mobile_complex"], homogeneous_to_ttt(external)
        )
        forced = alignment.pp_align_undo(result["id"], force=1)
        self.assertEqual(forced["stale_overwritten"], ["mobile_complex"])
        self.assertEqual(
            ttt_to_homogeneous(self.fake.ttt["mobile_complex"]),
            ttt_to_homogeneous(None),
        )
        print_exc.assert_not_called()

    def test_duplicate_ligand_names_are_rejected(self):
        self.fake.atoms["mobile_key"].append(Atom("mobile_key", 99, "C1", (8, 8, 8), "C"))
        result = alignment.pp_align_anchor("mobile_key", "target_key", match="name")
        self.assertEqual(result["status"], "error")
        self.assertIn("ambiguous", result["error"])
        self.assertEqual(result["error_code"], "ambiguous_atom_mapping")
        self.assertIn("match=order", result["guidance"])

    def test_ligand_alignment_rejects_symmetry_but_allows_verified_order(self):
        self.fake.atoms["mobile_key"].append(
            Atom("mobile_key", 99, "C1", (1.0, 1.0, 1.0), "C")
        )
        self.fake.atoms["target_key"].append(
            Atom("target_key", 99, "C1", (4.0, -1.0, 4.0), "C")
        )
        ambiguous = alignment.pp_align_ligand(
            "mobile_key", "target_key", match="name"
        )
        self.assertEqual(ambiguous["error_code"], "ambiguous_atom_mapping")

        ordered = alignment.pp_align_ligand(
            "mobile_key", "target_key", match="order", commit=0
        )
        self.assertEqual(ordered["kind"], "ligand")
        self.assertEqual(ordered["point_count"], 5)
        self.assertAlmostEqual(ordered["rmsd_after"], 0.0, places=7)

    def test_motif_alignment_pairs_positions_not_residue_numbers(self):
        self.fake.types.update(
            {
                "mobile_protein": "object:molecule",
                "target_protein": "object:molecule",
            }
        )
        self.fake.ttt.update({"mobile_protein": None, "target_protein": None})
        transform = compose_rigid(
            rotation_from_to((1, 0, 0), (0, 1, 0)), (5, -2, 3)
        )
        mobile_atoms = []
        target_atoms = []
        index = 1
        offsets = {
            "N": (-0.4, 0.0, 0.0),
            "CA": (0.0, 0.3, 0.2),
            "C": (0.5, 0.0, 0.1),
            "O": (0.8, -0.2, 0.3),
        }
        for position, resn in enumerate(("ALA", "GLY", "SER")):
            for atom_name, offset in offsets.items():
                point = (
                    2.0 * position + offset[0],
                    offset[1],
                    position * 0.4 + offset[2],
                )
                mobile_atoms.append(
                    Atom(
                        "mobile_protein",
                        index,
                        atom_name,
                        point,
                        "O" if atom_name == "O" else "N" if atom_name == "N" else "C",
                        resn=resn,
                        resi=str(position + 5),
                    )
                )
                target_atoms.append(
                    Atom(
                        "target_protein",
                        index,
                        atom_name,
                        apply_matrix(transform, point),
                        "O" if atom_name == "O" else "N" if atom_name == "N" else "C",
                        resn=resn,
                        resi=str(position + 101),
                    )
                )
                index += 1
        self.fake.atoms["mobile_protein"] = mobile_atoms
        self.fake.atoms["target_protein"] = target_atoms

        result = alignment.pp_align_motif(
            "mobile_protein", "target_protein", "AGS", commit=0
        )
        self.assertEqual(result["kind"], "motif")
        self.assertEqual(result["point_count"], 12)
        self.assertAlmostEqual(result["rmsd_after"], 0.0, places=7)

    def test_motif_alignment_rejects_occurrence_all_before_scene_reads(self):
        with mock.patch.object(alignment, "_motif_points") as motif_points:
            result = alignment.pp_align_motif(
                "mobile_protein",
                "target_protein",
                "AGS",
                occurrence="all",
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("cannot define one rigid correspondence", result["error"])
        self.assertEqual(result["error_code"], "invalid_motif_occurrence")
        self.assertIn("occurrence=unique", result["guidance"])
        self.assertEqual(alignment._RECORDS, {})
        motif_points.assert_not_called()

    def test_motif_no_match_and_ambiguity_are_actionable_without_tracebacks(self):
        self._install_alanine_pair(residue_count=4)

        with mock.patch.object(alignment.traceback, "print_exc") as print_exc:
            missing = alignment.pp_align_motif(
                "mobile_protein", "target_protein", "C"
            )
            ambiguous = alignment.pp_align_motif(
                "mobile_protein", "target_protein", "AAA"
            )

        self.assertEqual(missing["error_code"], "motif_no_match")
        self.assertIn("pp_select_sequence", missing["guidance"])
        self.assertEqual(ambiguous["error_code"], "motif_ambiguous")
        self.assertIn("longer motif", ambiguous["guidance"])
        print_exc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
