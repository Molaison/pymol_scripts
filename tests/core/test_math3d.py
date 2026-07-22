import math
import unittest

from utilities.math3d import (
    GeometryError,
    apply_matrix,
    compose_about_center,
    compose_rigid,
    fit_rigid_transform,
    homogeneous_to_ttt,
    identity4,
    mat4_multiply,
    rigid_inverse,
    rotation_from_to,
    ttt_to_homogeneous,
    validate_rigid_matrix,
)


class Math3DTests(unittest.TestCase):
    def assertPointAlmostEqual(self, left, right, places=8):
        for actual, expected in zip(left, right):
            self.assertAlmostEqual(actual, expected, places=places)

    def test_ttt_round_trip_with_pre_and_post_translation(self):
        ttt = (
            0.0, -1.0, 0.0, 10.0,
            1.0, 0.0, 0.0, 20.0,
            0.0, 0.0, 1.0, 30.0,
            2.0, 3.0, 4.0, 1.0,
        )
        homogeneous = ttt_to_homogeneous(ttt)
        self.assertPointAlmostEqual(apply_matrix(homogeneous, (5.0, 6.0, 7.0)), (1.0, 27.0, 41.0))
        canonical = homogeneous_to_ttt(homogeneous)
        self.assertEqual(canonical[12:15], (0.0, 0.0, 0.0))
        self.assertPointAlmostEqual(
            apply_matrix(ttt_to_homogeneous(canonical), (5.0, 6.0, 7.0)),
            (1.0, 27.0, 41.0),
        )

    def test_rigid_inverse(self):
        rotation = rotation_from_to((1, 0, 0), (0, 1, 0))
        matrix = compose_rigid(rotation, (3, -2, 7))
        product = mat4_multiply(matrix, rigid_inverse(matrix))
        for actual, expected in zip(product, identity4()):
            self.assertAlmostEqual(actual, expected, places=9)

    def test_rotation_from_to_antiparallel(self):
        rotation = rotation_from_to((0, 0, 1), (0, 0, -1))
        matrix = compose_rigid(rotation, (0, 0, 0))
        self.assertPointAlmostEqual(apply_matrix(matrix, (0, 0, 2)), (0, 0, -2))
        validate_rigid_matrix(matrix)

    def test_compose_about_center_keeps_center(self):
        rotation = rotation_from_to((1, 0, 0), (0, 1, 0))
        matrix = compose_about_center(rotation, (4, 5, 6))
        self.assertPointAlmostEqual(apply_matrix(matrix, (4, 5, 6)), (4, 5, 6))

    def test_horn_fit_recovers_known_transform(self):
        mobile = [(0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 4), (1, 2, 3)]
        rotation = rotation_from_to((1, 1, 0), (-1, 1, 1))
        expected = compose_rigid(rotation, (8.5, -3.25, 1.75))
        target = [apply_matrix(expected, point) for point in mobile]
        result = fit_rigid_transform(mobile, target)
        self.assertLess(result.rmsd_after, 1.0e-8)
        for point, expected_point in zip(mobile, target):
            self.assertPointAlmostEqual(apply_matrix(result.matrix, point), expected_point, places=7)

    def test_horn_fit_rejects_collinear_points(self):
        with self.assertRaisesRegex(GeometryError, "collinear"):
            fit_rigid_transform([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1, 0), (1, 1, 0), (2, 1, 0)])

    def test_rigid_validation_rejects_scale_and_reflection(self):
        scaled = list(identity4())
        scaled[0] = 2.0
        with self.assertRaisesRegex(GeometryError, "scale or shear"):
            validate_rigid_matrix(scaled)
        reflected = list(identity4())
        reflected[0] = -1.0
        with self.assertRaisesRegex(GeometryError, "proper rotation"):
            validate_rigid_matrix(reflected)


if __name__ == "__main__":
    unittest.main()
