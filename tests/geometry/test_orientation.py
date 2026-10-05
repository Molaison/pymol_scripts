import math
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from types import ModuleType, SimpleNamespace

import pymolplugins.geometry.commands as orientation
from pymolplugins.geometry.commands import (
    AxisResult,
    _reframe_anvil_result,
    build_membrane_slab,
    cyclic_axis,
    membrane_corners,
    orientation_rotation,
    parse_anvil_pdb,
    pca_axis,
    run_anvil_executable,
    two_point_axis,
    vector_axis,
)
from utilities.math3d import (
    GeometryError,
    compose_rigid,
    dot,
    homogeneous_to_ttt,
    identity4,
    norm,
    rotation_from_to,
    subtract,
    ttt_to_homogeneous,
)


def _pdb_atom(serial, resi, point):
    return (
        "ATOM  {:5d}  CA  ALA A{:4d}    {:8.3f}{:8.3f}{:8.3f}"
        "  1.00 20.00           C".format(serial, resi, *point)
    )


def _pdb_dummy(serial, point):
    return (
        "HETATM{:5d} DUM  DUM M{:4d}    {:8.3f}{:8.3f}{:8.3f}"
        "  1.00  0.00           X".format(serial, serial, *point)
    )


def _anvil_fixture(transform=lambda point: point):
    protein = ((0.0, 0.0, 0.0), (3.0, 0.0, 1.0), (0.0, 4.0, 2.0), (2.0, 3.0, 5.0))
    input_text = "\n".join(
        _pdb_atom(index, index, point)
        for index, point in enumerate(protein, start=1)
    )
    lines = [
        _pdb_atom(index, index, transform(point))
        for index, point in enumerate(protein, start=1)
    ]
    serial = len(lines) + 1
    for z in (-15.0, 15.0):
        for x, y in ((-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0), (0.0, 0.0)):
            lines.append(_pdb_dummy(serial, transform((x, y, z))))
            serial += 1
    return input_text + "\n", "\n".join(lines) + "\n"


class FailingTttCmd:
    def __init__(self):
        self.ttt = {"first": None, "second": None}
        self.fail_next_set = set()

    def get_object_ttt(self, name, quiet=1):
        return self.ttt[name]

    def set_object_ttt(self, name, value, quiet=1):
        if name in self.fail_next_set:
            self.fail_next_set.remove(name)
            raise RuntimeError(f"injected set failure for {name}")
        self.ttt[name] = tuple(value)


class SceneAtom:
    def __init__(
        self,
        model,
        index,
        coord,
        chain="A",
        segi="",
        resi=None,
        resn="ALA",
        alt="",
    ):
        self.model = model
        self.index = index
        self.coord = tuple(coord)
        self.symbol = "C"
        self.elem = "C"
        self.name = "CA"
        self.chain = chain
        self.segi = segi
        self.resi = str(index) if resi is None else resi
        self.resn = resn
        self.alt = alt


class SceneModel:
    def __init__(self, atoms):
        self.atom = list(atoms)


class GeometrySceneCmd:
    def __init__(self):
        self.types = {"protein": "object:molecule"}
        self.atoms = {
            "protein": [
                SceneAtom("protein", 1, (-6.0, 0.0, 0.0)),
                SceneAtom("protein", 2, (0.0, 1.0, 0.2)),
                SceneAtom("protein", 3, (6.0, -0.2, 0.4)),
            ]
        }
        self.ttt = {"protein": None}
        self.cgo_payload = {}
        self.titles = {}
        self.properties = {}
        self.groups = {}
        self.deleted = []
        self.fail_load = False
        self.fail_group = False
        self.fail_rename_targets = set()

    def add_name(self, name, object_type):
        self.types[name] = object_type
        if object_type.startswith("object:") and object_type != "object:group":
            self.ttt.setdefault(name, None)

    def get_names(self, mode="all", enabled_only=0, selection=""):
        if mode == "selections":
            return [name for name, kind in self.types.items() if kind == "selection"]
        return list(self.types)

    def get_type(self, name):
        return self.types.get(name, "")

    def get_unused_name(self, prefix):
        if prefix not in self.types:
            return prefix
        index = 1
        while "{}{}".format(prefix, index) in self.types:
            index += 1
        return "{}{}".format(prefix, index)

    def get_color_tuple(self, color):
        return (0.4, 0.5, 0.6)

    def load_cgo(self, payload, name):
        if self.fail_load:
            raise RuntimeError("injected load_cgo failure")
        if name in self.types:
            raise RuntimeError("name already exists: {}".format(name))
        self.types[name] = "object:cgo"
        self.ttt[name] = None
        self.cgo_payload[name] = list(payload)

    def group(self, group_name, member, action="add"):
        if self.fail_group:
            raise RuntimeError("injected group failure")
        current_type = self.types.get(group_name)
        if current_type and current_type != "object:group":
            raise RuntimeError("not a group: {}".format(group_name))
        self.types[group_name] = "object:group"
        members = self.groups.setdefault(group_name, [])
        if member not in members:
            members.append(member)

    def set_name(self, old, new):
        if new in self.fail_rename_targets:
            self.fail_rename_targets.remove(new)
            raise RuntimeError("injected rename failure for {}".format(new))
        if old not in self.types:
            raise RuntimeError("missing name: {}".format(old))
        if new in self.types:
            raise RuntimeError("target name exists: {}".format(new))
        self.types[new] = self.types.pop(old)
        if old in self.ttt:
            self.ttt[new] = self.ttt.pop(old)
        if old in self.atoms:
            self.atoms[new] = self.atoms.pop(old)
            for atom in self.atoms[new]:
                atom.model = new
        if old in self.cgo_payload:
            self.cgo_payload[new] = self.cgo_payload.pop(old)
        for key, title in list(self.titles.items()):
            if key[0] == old:
                self.titles[(new, key[1])] = title
                del self.titles[key]
        for key, value in list(self.properties.items()):
            if key[0] == old:
                self.properties[(new, key[1], key[2])] = value
                del self.properties[key]
        if old in self.groups:
            self.groups[new] = self.groups.pop(old)
        for members in self.groups.values():
            for index, value in enumerate(list(members)):
                if value == old:
                    members[index] = new

    def delete(self, name):
        self.deleted.append(name)
        self.types.pop(name, None)
        self.ttt.pop(name, None)
        self.atoms.pop(name, None)
        self.cgo_payload.pop(name, None)
        for key in [key for key in self.titles if key[0] == name]:
            del self.titles[key]
        for key in [key for key in self.properties if key[0] == name]:
            del self.properties[key]
        self.groups.pop(name, None)
        for members in self.groups.values():
            while name in members:
                members.remove(name)

    def get_object_list(self, selection="all"):
        expression = str(selection)
        if expression.strip("() ") == "all":
            return [name for name, kind in self.types.items() if kind == "object:molecule"]
        return [
            name
            for name, kind in self.types.items()
            if kind == "object:molecule" and name in expression
        ]

    def get_model(self, selection, state=1):
        expression = str(selection)
        atoms = []
        for name, values in self.atoms.items():
            if name in expression or expression.strip("() ") == "all":
                atoms.extend(values)
        return SceneModel(atoms)

    def get_object_ttt(self, name, quiet=1):
        return self.ttt[name]

    def set_object_ttt(self, name, value, quiet=1):
        self.ttt[name] = tuple(float(item) for item in value)

    def get_object_matrix(self, name, state=1, incl_ttt=1):
        return ttt_to_homogeneous(self.ttt[name])

    def set_title(self, name, state, title):
        if name not in self.types:
            raise RuntimeError("missing name: {}".format(name))
        self.titles[(name, int(state))] = str(title)

    def get_title(self, name, state):
        return self.titles.get((name, int(state)), "")

    def set_property(
        self, propname, value, object="*", state=0, proptype=-1, quiet=1
    ):
        if object not in self.types:
            raise RuntimeError("missing name: {}".format(object))
        self.properties[(object, str(propname), int(state))] = value

    def get_property(self, propname, name, state=0, quiet=1):
        return self.properties.get((name, str(propname), int(state)))


def _fake_pymol_module(cmd_api):
    module = ModuleType("pymol")
    module.cmd = cmd_api
    module.cgo = SimpleNamespace(
        CYLINDER=1,
        BEGIN=2,
        TRIANGLES=3,
        COLOR=4,
        ALPHA=5,
        NORMAL=6,
        VERTEX=7,
        END=8,
        LINES=9,
    )
    return module


def _geometry_scene_with_atoms(objects):
    fake = GeometrySceneCmd()
    fake.types = {name: "object:molecule" for name in objects}
    fake.atoms = {name: list(atoms) for name, atoms in objects.items()}
    fake.ttt = {name: None for name in objects}
    return fake


class OrientationTests(unittest.TestCase):
    def setUp(self):
        orientation._ORIENTATION_UNDO.clear()
        orientation._ORIENTATION_RECORDS.clear()
        orientation._LAST_ORIENTATION_ID = ""
        orientation._AXES.clear()
        orientation._AXIS_SOURCES.clear()
        orientation._MEMBRANES.clear()
        orientation._GEOMETRY_OUTPUT_OWNERS.clear()

    def test_pca_long_axis(self):
        points = [(x, 0.1 * ((-1) ** x), 0.05 * x) for x in range(-10, 11)]
        result = pca_axis(points, "long")
        self.assertGreater(abs(result.direction[0]), 0.99)
        self.assertGreater(result.confidence, 0.9)
        self.assertEqual(len(result.input_hash), 64)
        self.assertEqual(result.parameters["axis"], "long")
        self.assertEqual(result.backend["name"], "pymolplugins.math3d")
        self.assertEqual(result.warnings, ())

    def test_weighted_pca_uses_weighted_center(self):
        points = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 4.0, 0.0))
        unweighted = pca_axis(points)
        weighted = pca_axis(points, weights=(10.0, 1.0, 1.0))
        self.assertEqual(unweighted.center, (10.0 / 3.0, 4.0 / 3.0, 0.0))
        self.assertEqual(weighted.center, (10.0 / 12.0, 4.0 / 12.0, 0.0))
        self.assertEqual(weighted.diagnostics["weighting"], "weighted")

    def test_explicit_vector_two_points_and_secondary_roll(self):
        explicit = vector_axis((0.0, 0.0, -3.0), (1.0, 2.0, 3.0))
        self.assertEqual(explicit.center, (1.0, 2.0, 3.0))
        self.assertEqual(explicit.direction, (0.0, 0.0, 1.0))
        paired = two_point_axis(((0.0, 0.0, -4.0), (0.0, 0.0, 6.0)))
        self.assertEqual(paired.center, (0.0, 0.0, 1.0))
        rotation, constrained = orientation_rotation(
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        )
        self.assertTrue(constrained)
        rotated_x = (
            rotation[0],
            rotation[3],
            rotation[6],
        )
        self.assertAlmostEqual(rotated_x[0], 0.0, places=8)
        self.assertAlmostEqual(rotated_x[1], 1.0, places=8)
        self.assertAlmostEqual(rotated_x[2], 0.0, places=8)

    def test_synthetic_c6_axis(self):
        subunits = {}
        for index in range(6):
            angle = 2.0 * math.pi * index / 6.0
            center = (10.0 * math.cos(angle), 10.0 * math.sin(angle), 0.0)
            subunits[str(index)] = [
                (center[0] + 0.2, center[1], -1.0),
                (center[0] - 0.2, center[1], 0.0),
                (center[0], center[1] + 0.2, 1.0),
            ]
        result = cyclic_axis(subunits)
        self.assertEqual(result.diagnostics["order"], 6)
        self.assertGreater(abs(result.direction[2]), 0.999)
        self.assertGreater(result.confidence, 0.99)
        self.assertEqual(result.diagnostics["classification"], "cyclic")

    def test_synthetic_c3_and_nonuniform_ring_diagnostics(self):
        c3 = {}
        for index in range(3):
            angle = 2.0 * math.pi * index / 3.0
            center = (8.0 * math.cos(angle), 8.0 * math.sin(angle), 2.0)
            c3[str(index)] = [
                (center[0] + 0.2, center[1], 1.0),
                (center[0] - 0.2, center[1], 2.0),
                (center[0], center[1] + 0.2, 3.0),
            ]
        result = cyclic_axis(c3)
        self.assertEqual(result.diagnostics["order"], 3)
        self.assertGreater(abs(result.direction[2]), 0.999)
        self.assertGreater(result.confidence, 0.99)

        irregular = {}
        for index, angle_degree in enumerate((0.0, 35.0, 160.0, 275.0)):
            angle = math.radians(angle_degree)
            center = (10.0 * math.cos(angle), 10.0 * math.sin(angle), 0.0)
            irregular[str(index)] = [(center[0], center[1], z) for z in (-1.0, 0.0, 1.0)]
        result = cyclic_axis(irregular)
        self.assertGreater(result.diagnostics["angular_spacing_rms_degree"], 20.0)
        self.assertLess(result.confidence, 0.8)

    def test_helical_pitch_is_not_classified_as_cyclic(self):
        subunits = {}
        for index in range(6):
            angle = 2.0 * math.pi * index / 6.0
            center = (10.0 * math.cos(angle), 10.0 * math.sin(angle), 3.0 * index)
            subunits[str(index)] = [
                (center[0] + 0.2, center[1], center[2] - 0.5),
                (center[0] - 0.2, center[1], center[2]),
                (center[0], center[1] + 0.2, center[2] + 0.5),
            ]
        result = cyclic_axis(subunits)
        self.assertEqual(result.diagnostics["classification"], "helical")
        self.assertGreater(result.diagnostics["axial_pitch_rms"], 1.0)

    def test_c2_uses_paired_rotation(self):
        first = [(2, 0, 0), (3, 1, 0), (2, 0, 2), (3, -1, 1)]
        second = [(-x, -y, z) for x, y, z in first]
        result = cyclic_axis({"A": first, "B": second})
        self.assertEqual(result.diagnostics["order"], 2)
        self.assertGreater(abs(result.direction[2]), 0.999)
        self.assertAlmostEqual(result.diagnostics["rotation_angle_degree"], 180.0, places=5)

    def test_automatic_subunits_use_object_segi_and_chain_identity(self):
        fake = _geometry_scene_with_atoms(
            {
                "obj_a": [
                    SceneAtom(
                        "obj_a", 1, (1, 0, 0), chain=" A ", segi=" S1 ", resi=" 1 "
                    ),
                    SceneAtom(
                        "obj_a", 2, (2, 0, 0), chain=" A ", segi=" S2 ", resi=" 1 "
                    ),
                ],
                "obj_b": [
                    SceneAtom("obj_b", 1, (-1, 0, 0), chain="A", segi="S1", resi="1")
                ],
            }
        )
        orientation.cmd = fake

        clouds = orientation._subunit_clouds("obj_a or obj_b", "", 1)

        self.assertEqual(
            list(clouds),
            [
                "object=obj_a|segi=S1|chain=A",
                "object=obj_a|segi=S2|chain=A",
                "object=obj_b|segi=S1|chain=A",
            ],
        )
        self.assertEqual(
            clouds.resolution_diagnostics["subunit_identity_mode"],
            "object+segi+chain",
        )
        resolved = clouds.resolution_diagnostics["resolved_subunit_identities"]
        self.assertEqual(
            [(item["object"], item["segi"], item["chain"]) for item in resolved],
            [
                ("obj_a", "S1", "A"),
                ("obj_a", "S2", "A"),
                ("obj_b", "S1", "A"),
            ],
        )

    def test_host_c2_pairs_ca_atoms_by_residue_identity_not_atom_order(self):
        first_by_residue = {
            "10": (2, 0, 0),
            "2": (3, 1, 0),
            "3A": (2, 0, 2),
            "3": (3, -1, 1),
        }
        second_by_residue = {
            resi: (-point[0], -point[1], point[2])
            for resi, point in first_by_residue.items()
        }
        first_order = ("10", "3A", "2", "3")
        shuffled_second_order = ("3", "10", "2", "3A")
        fake = _geometry_scene_with_atoms(
            {
                "obj_a": [
                    SceneAtom(
                        "obj_a", index, first_by_residue[resi], resi=resi, resn="ALA"
                    )
                    for index, resi in enumerate(first_order, start=1)
                ],
                "obj_b": [
                    SceneAtom(
                        "obj_b",
                        index,
                        second_by_residue[resi],
                        resi=resi,
                        resn="ALA",
                    )
                    for index, resi in enumerate(shuffled_second_order, start=1)
                ],
            }
        )
        orientation.cmd = fake

        clouds = orientation._subunit_clouds("obj_a or obj_b", "", 1)
        labels = list(clouds)
        ordered_residues = ("2", "3", "3A", "10")
        self.assertEqual(
            clouds[labels[0]],
            [first_by_residue[resi] for resi in ordered_residues],
        )
        self.assertEqual(
            clouds[labels[1]],
            [second_by_residue[resi] for resi in ordered_residues],
        )

        result = orientation.pp_axis_cyclic(
            selection="obj_a or obj_b",
            name="paired_c2_axis",
            draw=0,
            min_confidence=0.0,
        )
        self.assertGreater(abs(result["direction"][2]), 0.999)
        self.assertAlmostEqual(
            result["diagnostics"]["rotation_angle_degree"], 180.0, places=5
        )
        self.assertEqual(result["diagnostics"]["c2_correspondence_count"], 4)
        self.assertEqual(
            len(result["diagnostics"]["c2_correspondence_hash"]), 64
        )
        self.assertEqual(
            result["parameters"]["subunit_identity_mode"],
            "object+segi+chain",
        )
        self.assertEqual(result["parameters"]["resolved_subunits"], labels)

    def test_host_c2_rejects_missing_residue_correspondence(self):
        fake = _geometry_scene_with_atoms(
            {
                "obj_a": [
                    SceneAtom("obj_a", index, (index, 0, 0), resi=str(index))
                    for index in (1, 2, 3)
                ],
                "obj_b": [
                    SceneAtom("obj_b", index, (-index, 0, 0), resi=str(index))
                    for index in (1, 3)
                ],
            }
        )
        orientation.cmd = fake

        with self.assertRaisesRegex(
            GeometryError, "C2 residue correspondence mismatch"
        ):
            orientation._subunit_clouds("obj_a or obj_b", "", 1)

    def test_host_c2_rejects_duplicate_ca_residue_identity(self):
        fake = _geometry_scene_with_atoms(
            {
                "obj_a": [
                    SceneAtom("obj_a", 1, (1, 0, 0), resi="7", alt="A"),
                    SceneAtom("obj_a", 2, (1, 0, 1), resi="7", alt="B"),
                ],
                "obj_b": [
                    SceneAtom("obj_b", 1, (-1, 0, 0), resi="7"),
                ],
            }
        )
        orientation.cmd = fake

        with self.assertRaisesRegex(
            GeometryError, "duplicate CA residue identities"
        ):
            orientation._subunit_clouds("obj_a or obj_b", "", 1)

    def test_host_c2_rejects_inconsistent_residue_names(self):
        fake = _geometry_scene_with_atoms(
            {
                "obj_a": [
                    SceneAtom("obj_a", 1, (1, 0, 0), resi="7", resn=" ala "),
                ],
                "obj_b": [
                    SceneAtom("obj_b", 1, (-1, 0, 0), resi="7", resn="GLY"),
                ],
            }
        )
        orientation.cmd = fake

        with self.assertRaisesRegex(
            GeometryError, "inconsistent residue names"
        ):
            orientation._subunit_clouds("obj_a or obj_b", "", 1)

    def test_host_c2_rejects_missing_residue_identity_fields(self):
        for field, value, message in (
            ("resi", "", "non-empty residue IDs"),
            ("resn", "", "non-empty residue names"),
        ):
            with self.subTest(field=field):
                atom_kwargs = {field: value}
                fake = _geometry_scene_with_atoms(
                    {
                        "obj_a": [
                            SceneAtom("obj_a", 1, (1, 0, 0), **atom_kwargs),
                        ],
                        "obj_b": [
                            SceneAtom("obj_b", 1, (-1, 0, 0), **atom_kwargs),
                        ],
                    }
                )
                orientation.cmd = fake

                with self.assertRaisesRegex(GeometryError, message):
                    orientation._subunit_clouds("obj_a or obj_b", "", 1)

    def test_membrane_corners_lie_in_plane(self):
        center = (1.0, 2.0, 3.0)
        normal = (1.0, 1.0, 1.0)
        corners = membrane_corners(center, normal, 20.0, 10.0)
        for corner in corners:
            self.assertAlmostEqual(dot(subtract(corner, center), normal), 0.0, places=8)
        self.assertAlmostEqual(norm(subtract(corners[1], corners[0])), 20.0, places=8)
        self.assertAlmostEqual(norm(subtract(corners[3], corners[0])), 10.0, places=8)

    def test_partial_orientation_failure_rolls_back_and_preserves_prior_undo(self):
        fake = FailingTttCmd()
        orientation.cmd = fake
        orientation._ORIENTATION_UNDO["prior"] = None
        fake.fail_next_set.add("second")
        matrix = compose_rigid(
            rotation_from_to((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (1.0, 2.0, 3.0),
        )
        with self.assertRaisesRegex(RuntimeError, "orientation commit failed"):
            orientation._apply_orientation(matrix, ("first", "second"))
        identity = ttt_to_homogeneous(None)
        self.assertEqual(ttt_to_homogeneous(fake.ttt["first"]), identity)
        self.assertEqual(ttt_to_homogeneous(fake.ttt["second"]), identity)
        self.assertEqual(orientation._ORIENTATION_UNDO, {"prior": None})

    def test_orientation_undo_refuses_external_edits_unless_forced(self):
        fake = FailingTttCmd()
        orientation.cmd = fake
        matrix = compose_rigid(
            rotation_from_to((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (2.0, 3.0, 4.0),
        )
        record = orientation._apply_orientation(matrix, ("first",))
        external = compose_rigid(
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            (99.0, 0.0, 0.0),
        )
        fake.ttt["first"] = homogeneous_to_ttt(external)

        refused = orientation.pp_orientation_undo(record.id)
        self.assertEqual(refused["error_code"], "stale_orientation_undo")
        self.assertEqual(refused["stale_objects"], ["first"])
        self.assertEqual(
            ttt_to_homogeneous(fake.ttt["first"]),
            external,
        )

        forced = orientation.pp_orientation_undo(record.id, force=1)
        self.assertEqual(forced["status"], "restored")
        self.assertTrue(forced["forced"])
        self.assertEqual(forced["stale_overwritten"], ["first"])
        self.assertEqual(ttt_to_homogeneous(fake.ttt["first"]), identity4())

    def test_public_orientation_commit_returns_operation_provenance(self):
        fake = FailingTttCmd()
        orientation.cmd = fake
        points = [
            (float(index), 0.2 * (index % 2), 0.1 * (index % 3))
            for index in range(8)
        ]
        with mock.patch.object(
            orientation, "_move_objects", return_value=("first",)
        ), mock.patch.object(
            orientation,
            "_pca_inputs",
            return_value=(points, None, "equal"),
        ):
            result = orientation.pp_orient_pca(
                selection="protein",
                move="first",
                target_axis="z",
                min_confidence=0.0,
                force=1,
                commit=1,
            )
        self.assertTrue(result["committed"])
        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["next_command"],
            "pp_orientation_undo {}".format(result["operation_id"]),
        )
        self.assertIn(result["operation_id"], orientation._ORIENTATION_RECORDS)
        self.assertEqual(len(result["committed_fingerprint"]), 64)
        undone = orientation.pp_orientation_undo(result["operation_id"])
        self.assertEqual(undone["status"], "restored")

    def test_orientation_records_form_a_bounded_multi_undo_stack(self):
        fake = FailingTttCmd()
        orientation.cmd = fake
        first_matrix = compose_rigid(
            rotation_from_to((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (1.0, 2.0, 3.0),
        )
        second_matrix = compose_rigid(
            rotation_from_to((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            (-2.0, 1.0, 4.0),
        )
        first = orientation._apply_orientation(first_matrix, ("first",))
        second = orientation._apply_orientation(second_matrix, ("second",))
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(orientation._LAST_ORIENTATION_ID, second.id)

        undone_second = orientation.pp_orientation_undo()
        self.assertEqual(undone_second["operation_id"], second.id)
        self.assertEqual(orientation._LAST_ORIENTATION_ID, first.id)
        undone_first = orientation.pp_orientation_undo()
        self.assertEqual(undone_first["operation_id"], first.id)
        self.assertEqual(orientation._LAST_ORIENTATION_ID, "")
        self.assertEqual(orientation._ORIENTATION_UNDO, {})

        created = []
        for _ in range(orientation._ORIENTATION_RECORD_LIMIT + 5):
            created.append(
                orientation._apply_orientation(identity4(), ("first",)).id
            )
        self.assertEqual(
            len(orientation._ORIENTATION_RECORDS),
            orientation._ORIENTATION_RECORD_LIMIT,
        )
        self.assertNotIn(created[0], orientation._ORIENTATION_RECORDS)
        self.assertIn(created[-1], orientation._ORIENTATION_RECORDS)

    def test_axis_collision_refuses_every_user_name_type(self):
        for object_type in (
            "object:molecule",
            "object:group",
            "object:cgo",
            "selection",
        ):
            with self.subTest(object_type=object_type):
                fake = GeometrySceneCmd()
                fake.add_name("reserved_axis", object_type)
                orientation.cmd = fake
                with mock.patch.dict(
                    sys.modules,
                    {"pymol": _fake_pymol_module(fake)},
                ):
                    result = orientation.pp_axis_vector(
                        vector="0,0,1",
                        center="0,0,0",
                        name="reserved_axis",
                        draw=1,
                    )
                self.assertEqual(result["error_code"], "output_name_collision")
                self.assertEqual(result["collisions"][0]["type"], object_type)
                self.assertEqual(fake.get_type("reserved_axis"), object_type)
                self.assertEqual(fake.deleted, [])
                self.assertNotIn("reserved_axis", orientation._AXES)
                orientation._GEOMETRY_OUTPUT_OWNERS.clear()

    def test_axis_status_distinguishes_compute_only_from_scene_output(self):
        fake = GeometrySceneCmd()
        orientation.cmd = fake
        with mock.patch.dict(sys.modules, {"pymol": _fake_pymol_module(fake)}):
            computed = orientation.pp_axis_vector(
                vector="z",
                center="0,0,0",
                name="computed_axis",
                draw=0,
            )
            drawn = orientation.pp_axis_vector(
                vector="z",
                center="0,0,0",
                name="drawn_axis",
                draw=1,
            )

        self.assertEqual(computed["status"], "computed")
        self.assertFalse(computed["changed"])
        self.assertEqual(computed["output_names"], [])
        self.assertIsNone(computed["ownership_id"])
        self.assertEqual(drawn["status"], "drawn")
        self.assertTrue(drawn["changed"])
        self.assertEqual(drawn["output_names"], ["drawn_axis"])
        self.assertTrue(drawn["ownership_id"].startswith("geometry_axis_"))

    def test_vector_axis_rejects_selection_silently_shadowed_by_center(self):
        fake = GeometrySceneCmd()
        orientation.cmd = fake
        with mock.patch.object(
            orientation.traceback, "print_exc"
        ) as print_exc:
            result = orientation.pp_axis_vector(
                vector="z",
                center="0,0,0",
                selection="protein",
                draw=0,
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("mutually exclusive", result["error"])
        self.assertEqual(orientation._AXES, {})
        print_exc.assert_not_called()

    def test_membrane_pca_axis_is_honored_and_parameters_are_visible(self):
        fake = _geometry_scene_with_atoms(
            {
                "protein": [
                    SceneAtom("protein", 1, (-5, -3, 0)),
                    SceneAtom("protein", 2, (5, -3, 0)),
                    SceneAtom("protein", 3, (5, 3, 0)),
                    SceneAtom("protein", 4, (-5, 3, 0)),
                ]
            }
        )
        orientation.cmd = fake
        with mock.patch.dict(sys.modules, {"pymol": _fake_pymol_module(fake)}):
            result = orientation.pp_membrane_slab(
                selection="protein",
                axis_source="pca",
                axis="short",
                name="short_axis_slab",
                color="marine",
                transparency=0.25,
            )

        self.assertEqual(result["status"], "drawn")
        self.assertTrue(result["changed"])
        self.assertTrue(
            result["ownership_id"].startswith("geometry_membrane_")
        )
        self.assertGreater(abs(result["normal"][2]), 0.999)
        self.assertEqual(result["parameters"]["axis"], "short")
        self.assertEqual(result["parameters"]["selection"], "protein")
        self.assertEqual(result["parameters"]["color"], "marine")
        self.assertEqual(result["parameters"]["transparency"], 0.25)
        self.assertEqual(
            result["diagnostics"]["axis"]["axis_mode"], "short"
        )

    def test_membrane_pca_rejects_low_confidence_unless_forced(self):
        coordinates = [
            (x, y, z)
            for x in (-1, 1)
            for y in (-1, 1)
            for z in (-1, 1)
        ]
        fake = _geometry_scene_with_atoms(
            {
                "protein": [
                    SceneAtom("protein", index, point)
                    for index, point in enumerate(coordinates, start=1)
                ]
            }
        )
        orientation.cmd = fake
        with mock.patch.dict(
            sys.modules, {"pymol": _fake_pymol_module(fake)}
        ), mock.patch.object(orientation.traceback, "print_exc") as print_exc:
            refused = orientation.pp_membrane_slab(
                selection="protein",
                axis_source="pca",
                axis="long",
                name="ambiguous_slab",
            )
            forced = orientation.pp_membrane_slab(
                selection="protein",
                axis_source="pca",
                axis="long",
                name="forced_slab",
                force=1,
            )

        self.assertEqual(refused["status"], "error")
        self.assertTrue(refused["scene_preserved"])
        self.assertIn("PCA axis confidence", refused["error"])
        self.assertNotIn("ambiguous_slab", fake.types)
        self.assertEqual(forced["status"], "drawn")
        self.assertEqual(forced["diagnostics"]["axis_confidence"], 0.0)
        print_exc.assert_not_called()

    def test_membrane_rejects_parameters_irrelevant_to_axis_source(self):
        fake = GeometrySceneCmd()
        orientation.cmd = fake
        with mock.patch.dict(
            sys.modules, {"pymol": _fake_pymol_module(fake)}
        ), mock.patch.object(orientation.traceback, "print_exc") as print_exc:
            result = orientation.pp_membrane_slab(
                selection="protein",
                axis_source="vector",
                axis="z",
                subunits="chain A,chain B",
                name="ignored_parameter_slab",
            )

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        self.assertIn("subunits is only valid", result["error"])
        self.assertNotIn("ignored_parameter_slab", fake.types)
        print_exc.assert_not_called()

    def test_anvil_modes_reject_parameters_that_would_be_ignored(self):
        orientation.cmd = GeometrySceneCmd()
        with mock.patch.dict(
            os.environ, {"PYMOLPLUGINS_ANVIL_EXECUTABLE": ""}
        ), mock.patch.object(
            orientation.traceback, "print_exc"
        ) as print_exc:
            precomputed = orientation.pp_membrane_from_anvil(
                "missing.pdb", arguments="--input {input}"
            )
            executable = orientation.pp_membrane_from_anvil(
                "protein",
                selection="protein",
                executable="fake-anvil",
            )

        self.assertEqual(precomputed["status"], "error")
        self.assertIn("only valid when executable", precomputed["error"])
        self.assertEqual(executable["status"], "error")
        self.assertIn("only used when parsing precomputed", executable["error"])
        print_exc.assert_not_called()

    def test_owned_axis_can_be_replaced_and_failed_replacement_is_atomic(self):
        fake = GeometrySceneCmd()
        orientation.cmd = fake
        pymol_module = _fake_pymol_module(fake)
        with mock.patch.dict(sys.modules, {"pymol": pymol_module}):
            first = orientation.pp_axis_vector(
                vector="0,0,1", center="0,0,0", name="owned_axis"
            )
            first_payload = list(fake.cgo_payload["owned_axis"])
            second = orientation.pp_axis_vector(
                vector="1,0,0", center="0,0,0", name="owned_axis"
            )
            second_payload = list(fake.cgo_payload["owned_axis"])
            self.assertNotEqual(first_payload, second_payload)
            self.assertEqual(first["name"], "owned_axis")
            self.assertEqual(second["name"], "owned_axis")

            fake.fail_load = True
            with mock.patch.object(orientation.traceback, "print_exc"):
                failed_load = orientation.pp_axis_vector(
                    vector="0,1,0", center="0,0,0", name="owned_axis"
                )
            fake.fail_load = False
            self.assertEqual(failed_load["status"], "error")
            self.assertEqual(fake.cgo_payload["owned_axis"], second_payload)
            self.assertEqual(
                orientation._AXES["owned_axis"].direction,
                (1.0, 0.0, 0.0),
            )

            fake.fail_rename_targets.add("owned_axis")
            with mock.patch.object(orientation.traceback, "print_exc"):
                failed_rename = orientation.pp_axis_vector(
                    vector="0,1,0", center="0,0,0", name="owned_axis"
                )
            self.assertEqual(failed_rename["status"], "error")
            self.assertEqual(fake.cgo_payload["owned_axis"], second_payload)

            # In-memory ownership alone must not authorize deletion after the
            # user removes and recreates a same-type object under the same name.
            fake.delete("owned_axis")
            fake.add_name("owned_axis", "object:cgo")
            fake.cgo_payload["owned_axis"] = [999.0]
            recreated = orientation.pp_axis_vector(
                vector="0,1,0", center="0,0,0", name="owned_axis"
            )
            self.assertEqual(recreated["error_code"], "output_name_collision")
            self.assertEqual(fake.cgo_payload["owned_axis"], [999.0])
        self.assertFalse(
            any(name.startswith("pp_geometry_axis_") for name in fake.types)
        )

    def test_membrane_collision_and_group_failure_preserve_scene(self):
        for collision_name in ("user_slab", "user_slab__planes"):
            for object_type in (
                "object:molecule",
                "object:group",
                "object:cgo",
                "selection",
            ):
                with self.subTest(
                    collision_name=collision_name,
                    object_type=object_type,
                ):
                    fake = GeometrySceneCmd()
                    fake.add_name(collision_name, object_type)
                    orientation.cmd = fake
                    with mock.patch.dict(
                        sys.modules,
                        {"pymol": _fake_pymol_module(fake)},
                    ):
                        collision = orientation.pp_membrane_slab(
                            selection="protein",
                            axis_source="vector",
                            axis="0,0,1",
                            name="user_slab",
                        )
                    self.assertEqual(
                        collision["error_code"], "output_name_collision"
                    )
                    self.assertEqual(fake.get_type(collision_name), object_type)
                    self.assertEqual(fake.deleted, [])

        fresh = GeometrySceneCmd()
        fresh.fail_group = True
        orientation.cmd = fresh
        with mock.patch.dict(
            sys.modules,
            {"pymol": _fake_pymol_module(fresh)},
        ), mock.patch.object(orientation.traceback, "print_exc"):
            failed = orientation.pp_membrane_slab(
                selection="protein",
                axis_source="vector",
                axis="0,0,1",
                name="new_slab",
            )
        self.assertEqual(failed["status"], "error")
        self.assertEqual(set(fresh.types), {"protein"})
        self.assertNotIn("new_slab", orientation._MEMBRANES)

    def test_owned_membrane_replacement_rolls_back_on_draw_failure(self):
        fake = GeometrySceneCmd()
        orientation.cmd = fake
        pymol_module = _fake_pymol_module(fake)
        with mock.patch.dict(sys.modules, {"pymol": pymol_module}):
            first = orientation.pp_membrane_slab(
                selection="protein",
                axis_source="vector",
                axis="0,0,1",
                thickness=20.0,
                name="owned_slab",
            )
            self.assertEqual(first["thickness"], 20.0)
            first_payload = list(fake.cgo_payload["owned_slab__planes"])
            fake.fail_load = True
            with mock.patch.object(orientation.traceback, "print_exc"):
                failed = orientation.pp_membrane_slab(
                    selection="protein",
                    axis_source="vector",
                    axis="0,0,1",
                    thickness=40.0,
                    name="owned_slab",
                )
        self.assertEqual(failed["status"], "error")
        self.assertEqual(fake.get_type("owned_slab"), "object:group")
        self.assertEqual(fake.get_type("owned_slab__planes"), "object:cgo")
        self.assertEqual(fake.cgo_payload["owned_slab__planes"], first_payload)
        self.assertEqual(orientation._MEMBRANES["owned_slab"].thickness, 20.0)

    def test_stored_axis_rejects_transform_and_coordinate_changes(self):
        fake = GeometrySceneCmd()
        orientation.cmd = fake
        created = orientation.pp_axis_pca(
            selection="protein",
            name="stored_axis",
            draw=0,
        )
        self.assertEqual(created["schema"], "pymolplugins.axis/1.0")
        orientation._validate_stored_axis("stored_axis")

        moved = compose_rigid(
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            (10.0, 0.0, 0.0),
        )
        fake.ttt["protein"] = homogeneous_to_ttt(moved)
        stale_transform = orientation.pp_membrane_slab(
            selection="protein",
            axis_source="stored_axis",
            name="stale_transform_slab",
        )
        self.assertEqual(stale_transform["error_code"], "stale_stored_axis")
        self.assertTrue(
            any("transform changed" in reason for reason in stale_transform["stale_reasons"])
        )

        fake.ttt["protein"] = None
        orientation.pp_axis_pca(
            selection="protein",
            name="stored_axis",
            draw=0,
        )
        atom = fake.atoms["protein"][0]
        atom.coord = (atom.coord[0] + 0.75, atom.coord[1], atom.coord[2])
        stale_coordinates = orientation.pp_membrane_slab(
            selection="protein",
            axis_source="stored_axis",
            name="stale_coordinate_slab",
        )
        self.assertEqual(stale_coordinates["error_code"], "stale_stored_axis")
        self.assertIn("source coordinates changed", stale_coordinates["stale_reasons"])
        self.assertEqual(orientation._MEMBRANES, {})

    def test_membrane_slab_thickness_and_auto_extent_cover_projection(self):
        points = [
            (-12.0, -5.0, -4.0),
            (14.0, -5.0, 3.0),
            (14.0, 7.0, 1.0),
            (-12.0, 7.0, -2.0),
        ]
        axis = AxisResult("vector", (1.0, 1.0, 0.0), (0.0, 0.0, 1.0), 1.0)
        result = build_membrane_slab(points, axis, thickness=28.0, padding=3.0)
        self.assertAlmostEqual(
            norm(subtract(result.plane_centers[1], result.plane_centers[0])),
            28.0,
        )
        self.assertGreaterEqual(result.width, 18.0)
        self.assertGreaterEqual(result.height, 32.0)
        self.assertEqual(len(result.input_hash), 64)
        self.assertEqual(result.parameters["thickness"], 28.0)
        self.assertEqual(result.backend["name"], "pymolplugins.math3d")
        for plane_center in result.plane_centers:
            for corner in membrane_corners(
                plane_center, result.normal, result.width, result.height
            ):
                self.assertAlmostEqual(
                    abs(dot(subtract(corner, result.center), result.normal)),
                    14.0,
                    places=8,
                )

    def test_parse_anvil_dummy_planes(self):
        lines = []
        serial = 1
        for z in (-15.0, 15.0):
            for x, y in ((-10, -10), (10, -10), (10, 10), (-10, 10), (0, 0)):
                lines.append(
                    "HETATM{:5d} DUM  DUM A{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00           X".format(
                        serial, serial, float(x), float(y), z
                    )
                )
                serial += 1
        result, thickness = parse_anvil_pdb("\n".join(lines))
        self.assertAlmostEqual(thickness, 30.0, places=6)
        self.assertGreater(abs(result.direction[2]), 0.999)
        self.assertAlmostEqual(result.center[2], 0.0, places=6)
        self.assertEqual(len(result.input_hash), 64)
        self.assertEqual(result.parameters["parser"], "anvil_dum_planes")

    def test_orientation_commands_resolve_move_objects_once_and_record_provenance(self):
        points = [(float(index), 0.1 * (index % 2), 0.0) for index in range(8)]
        output = io.StringIO()
        with mock.patch.object(
            orientation, "_move_objects", return_value=("mobile",)
        ) as resolve_move, mock.patch.object(
            orientation,
            "_pca_inputs",
            return_value=(points, None, "equal"),
        ), redirect_stdout(output):
            pca_result = orientation.pp_orient_pca(commit=0)
        self.assertEqual(resolve_move.call_count, 1)
        self.assertEqual(pca_result["status"], "preview")
        self.assertFalse(pca_result["changed"])
        self.assertIn("commit=1", pca_result["next_command"])
        self.assertIn("Preview only; nothing changed", output.getvalue())
        self.assertEqual(pca_result["axis"]["input_selection"], "all")
        self.assertEqual(pca_result["axis"]["parameters"]["move"], ["mobile"])

        first = [(2, 0, 0), (3, 1, 0), (2, 0, 2), (3, -1, 1)]
        second = [(-x, -y, z) for x, y, z in first]
        with mock.patch.object(
            orientation,
            "_subunit_clouds",
            return_value={"A": first, "B": second},
        ), mock.patch.object(
            orientation, "_move_objects", return_value=("assembly",)
        ) as resolve_move:
            cyclic_result = orientation.pp_orient_cyclic(commit=0)
        self.assertEqual(resolve_move.call_count, 1)
        self.assertEqual(cyclic_result["axis"]["input_selection"], "all")
        self.assertEqual(
            cyclic_result["axis"]["parameters"]["move"], ["assembly"]
        )
        self.assertEqual(len(cyclic_result["axis"]["input_hash"]), 64)

    def test_orientation_rejects_secondary_target_without_secondary_selection(self):
        with mock.patch.object(
            orientation, "_pca_inputs"
        ) as pca_inputs, mock.patch.object(
            orientation.traceback, "print_exc"
        ) as print_exc:
            result = orientation.pp_orient_pca(
                selection="protein",
                secondary_target="x",
                secondary_selection="",
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("secondary_target requires secondary_selection", result["error"])
        pca_inputs.assert_not_called()
        print_exc.assert_not_called()

    def test_orientation_validates_action_flags_before_reading_scene(self):
        with mock.patch.object(
            orientation, "_move_objects"
        ) as move_objects, mock.patch.object(
            orientation.traceback, "print_exc"
        ) as print_exc:
            result = orientation.pp_orient_pca(commit="maybe")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "invalid_geometry_request")
        self.assertTrue(result["scene_preserved"])
        move_objects.assert_not_called()
        print_exc.assert_not_called()

    def test_geometry_export_is_atomic_on_serialization_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "axis.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("old payload\n")
            orientation._AXES["broken"] = AxisResult(
                "vector",
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                1.0,
                diagnostics={"not_json": object()},
            )
            with mock.patch.object(
                orientation.traceback, "print_exc"
            ), mock.patch("builtins.print"):
                self.assertIsNone(orientation.pp_geometry_export("broken", path))
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old payload\n")
            self.assertEqual(os.listdir(directory), ["axis.json"])

    def test_parse_anvil_noisy_planes_is_order_independent(self):
        lines = []
        serial = 1
        for layer, z in enumerate((-14.0, 16.0)):
            for offset, (x, y) in enumerate(
                (
                    (-12, -8),
                    (0, -8),
                    (12, -8),
                    (-12, 0),
                    (0, 0),
                    (12, 0),
                    (-12, 8),
                    (0, 8),
                    (12, 8),
                )
            ):
                noise = 0.12 * ((offset + layer) % 3 - 1)
                lines.append(
                    "HETATM{:5d} DUM  DUM A{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00           X".format(
                        serial, serial, float(x), float(y), z + noise
                    )
                )
                serial += 1
        forward, forward_thickness = parse_anvil_pdb("\n".join(lines))
        reverse, reverse_thickness = parse_anvil_pdb("\n".join(reversed(lines)))
        self.assertAlmostEqual(forward_thickness, 30.0, delta=0.2)
        self.assertAlmostEqual(reverse_thickness, forward_thickness, places=8)
        self.assertGreater(abs(dot(forward.direction, reverse.direction)), 0.999)
        self.assertLess(max(forward.diagnostics["plane_fit_rms"]), 0.2)

    def test_parse_anvil_rejects_a_single_missing_layer(self):
        lines = []
        for serial, (x, y) in enumerate(
            ((-10, -10), (0, -10), (10, -10), (-10, 0), (0, 0), (10, 0), (-10, 10), (0, 10), (10, 10)),
            start=1,
        ):
            lines.append(
                "HETATM{:5d} DUM  DUM A{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00           X".format(
                    serial, serial, float(x), float(y), 0.0
                )
            )
        with self.assertRaisesRegex(GeometryError, "could not separate"):
            parse_anvil_pdb("\n".join(lines))

    def test_anvil_reorientation_is_detected_and_mapped_to_input_frame(self):
        def transform(point):
            x, y, z = point
            return (5.0 - y, -2.0 + x, 3.0 + z)

        input_text, output_text = _anvil_fixture(transform)
        result, thickness = _reframe_anvil_result(input_text, output_text)
        self.assertAlmostEqual(thickness, 30.0, places=6)
        self.assertLess(norm(result.center), 1.0e-6)
        self.assertGreater(abs(result.direction[2]), 0.999999)
        self.assertTrue(result.diagnostics["protein_reoriented"])
        self.assertEqual(result.diagnostics["protein_identity_validation"], "exact")
        self.assertLess(result.diagnostics["protein_rmsd_after_rigid_fit"], 1.0e-6)

    def test_anvil_nonrigid_protein_change_is_rejected(self):
        input_text, output_text = _anvil_fixture()
        lines = output_text.splitlines()
        line = lines[1]
        lines[1] = line[:30] + "{:8.3f}".format(float(line[30:38]) + 2.0) + line[38:]
        with self.assertRaisesRegex(GeometryError, "non-rigidly"):
            _reframe_anvil_result(input_text, "\n".join(lines), rmsd_tolerance=0.05)

    def test_anvil_runner_uses_explicit_argv_and_stdout(self):
        input_text, output_text = _anvil_fixture()
        with tempfile.TemporaryDirectory() as directory:
            script = os.path.join(directory, "fake_anvil.py")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(
                    "import pathlib, sys\n"
                    "print(pathlib.Path(sys.argv[1]).read_text(), end='')\n"
                    f"print({output_text[len(input_text):]!r}, end='')\n"
                )
            result, provenance = run_anvil_executable(
                sys.executable,
                input_text,
                arguments=f"{script} {{input}}",
            )
        parsed, thickness = parse_anvil_pdb(result)
        self.assertAlmostEqual(thickness, 30.0, places=6)
        self.assertEqual(provenance["returncode"], 0)
        self.assertEqual(provenance["output_source"], "stdout")
        self.assertGreater(abs(parsed.direction[2]), 0.999)


if __name__ == "__main__":
    unittest.main()
