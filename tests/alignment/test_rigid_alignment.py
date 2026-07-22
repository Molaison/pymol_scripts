import importlib
import unittest

from utilities.math3d import apply_matrix, compose_rigid, homogeneous_to_ttt, rotation_from_to, ttt_to_homogeneous

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
            "mobile_key": [Atom("mobile_key", i + 1, name, point, elem) for i, (name, point, elem) in enumerate(zip(names, mobile, elems))],
            "target_key": [Atom("target_key", i + 1, name, point, elem) for i, (name, point, elem) in enumerate(zip(names, target, elems))],
        }
        self.ttt = {name: None for name, kind in self.types.items() if kind != "object:group"}
        self.fail_next_set = set()

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

    def test_preview_does_not_move_payload_then_commit_moves_group_leaves(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=0
        )
        self.assertNotEqual(result.get("status"), "error")
        self.assertFalse(result["committed"])
        self.assertIsNone(self.fake.ttt["mobile_complex"])
        self.assertIsNone(self.fake.ttt["mobile_cgo"])
        committed = alignment.pp_align_commit(result["id"])
        self.assertTrue(committed["committed"])
        self.assertIsNotNone(self.fake.ttt["mobile_complex"])
        self.assertEqual(self.fake.ttt["mobile_complex"], self.fake.ttt["mobile_cgo"])
        self.assertIsNone(self.fake.ttt["mobile_key"])

    def test_undo_restores_payload_baselines(self):
        baseline = compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (1, 2, 3))
        self.fake.ttt["mobile_complex"] = homogeneous_to_ttt(baseline)
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="mobile_complex", match="name", commit=1
        )
        self.assertTrue(result["committed"])
        alignment.pp_align_undo(result["id"])
        self.assertEqual(self.fake.ttt["mobile_complex"], homogeneous_to_ttt(baseline))

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

    def test_preview_commit_rejects_stale_move_object(self):
        result = alignment.pp_align_anchor(
            "mobile_key", "target_key", move="payload", match="name", commit=0
        )
        moved = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), (0.5, 0.0, 0.0)
        )
        self.fake.ttt["mobile_complex"] = homogeneous_to_ttt(moved)
        failed = alignment.pp_align_commit(result["id"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("preview is stale", failed["error"])
        self.assertEqual(self.fake.ttt["mobile_complex"], homogeneous_to_ttt(moved))

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
        refused = alignment.pp_align_undo(result["id"])
        self.assertEqual(refused["status"], "error")
        self.assertIn("changed after commit", refused["error"])
        self.assertEqual(
            self.fake.ttt["mobile_complex"], homogeneous_to_ttt(external)
        )
        forced = alignment.pp_align_undo(result["id"], force=1)
        self.assertEqual(forced["stale_overwritten"], ["mobile_complex"])
        self.assertEqual(ttt_to_homogeneous(self.fake.ttt["mobile_complex"]), ttt_to_homogeneous(None))

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


if __name__ == "__main__":
    unittest.main()
