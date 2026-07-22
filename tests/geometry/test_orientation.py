import math
import os
import sys
import tempfile
import unittest
from unittest import mock

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


class OrientationTests(unittest.TestCase):
    def setUp(self):
        orientation._ORIENTATION_UNDO.clear()
        orientation._AXES.clear()
        orientation._MEMBRANES.clear()

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
        with mock.patch.object(
            orientation, "_move_objects", return_value=("mobile",)
        ) as resolve_move, mock.patch.object(
            orientation,
            "_pca_inputs",
            return_value=(points, None, "equal"),
        ):
            pca_result = orientation.pp_orient_pca(commit=0)
        self.assertEqual(resolve_move.call_count, 1)
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
