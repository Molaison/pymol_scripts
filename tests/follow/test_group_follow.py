import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from utilities.math3d import (
    compose_rigid,
    homogeneous_to_ttt,
    identity4,
    mat4_multiply,
    rotation_from_to,
    ttt_to_homogeneous,
)

group_follow = importlib.import_module("pymolplugins.follow.commands")


class FakeCmd:
    def __init__(self):
        self.types = {
            "assembly": "object:group",
            "driver": "object:molecule",
            "peer": "object:molecule",
            "slab": "object:cgo",
            "unrelated": "object:molecule",
        }
        self.groups = {"assembly": ["driver", "peer", "slab"]}
        self.ttt = {name: None for name in self.types if name != "assembly"}

    def get_names(self, mode="all", enabled_only=0, selection=""):
        if selection in self.groups:
            return list(self.groups[selection])
        return list(self.types)

    def get_type(self, name):
        return self.types.get(name, "")

    def get_object_list(self, selection="all"):
        return [name for name in self.groups.get(selection, []) if self.types[name] == "object:molecule"]

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

    def get_object_ttt(self, name, quiet=1):
        return self.ttt[name]

    def set_object_ttt(self, name, ttt, quiet=1):
        self.ttt[name] = tuple(float(value) for value in ttt)

    def add_object(self, name, object_type="object:cgo", group="assembly"):
        self.types[name] = object_type
        self.ttt[name] = None
        self.groups.setdefault(group, []).append(name)


class OverlayCmd:
    def __init__(self):
        self.callbacks = {}
        self.commands = {}
        self.loaded_cgo = {}

    def extend(self, name, function):
        self.commands[name] = function

    def get_unused_name(self, prefix):
        return prefix + "01"

    def load_callback(self, callback, name):
        self.callbacks[name] = callback

    def load_cgo(self, obj, name):
        self.loaded_cgo[name] = list(obj)

    def set(self, name, value):
        pass


class GroupFollowTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCmd()
        group_follow.cmd = self.fake
        group_follow._SESSION = None
        group_follow._CALLBACK_OWNED.clear()
        group_follow._REBUILD_HOOKS.clear()

    def assert_matrix_close(self, actual, expected, places=8):
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=places)

    def test_group_follow_preserves_relative_offsets_for_molecule_and_cgo(self):
        peer_baseline = compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (4, 0, 0))
        slab_baseline = compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (0, 5, 0))
        self.fake.ttt["peer"] = homogeneous_to_ttt(peer_baseline)
        self.fake.ttt["slab"] = homogeneous_to_ttt(slab_baseline)
        armed = group_follow.pp_follow_arm("driver", group="assembly")
        self.assertEqual(armed["status"], "armed")

        delta = compose_rigid(rotation_from_to((1, 0, 0), (0, 1, 0)), (10, 2, -1))
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        result = group_follow.pp_follow_apply()
        self.assertEqual(result["status"], "applied")

        expected_peer = mat4_multiply(delta, peer_baseline)
        expected_slab = mat4_multiply(delta, slab_baseline)
        peer_actual = ttt_to_homogeneous(self.fake.ttt["peer"])
        slab_actual = ttt_to_homogeneous(self.fake.ttt["slab"])
        self.assert_matrix_close(peer_actual, expected_peer)
        self.assert_matrix_close(slab_actual, expected_slab)
        self.assertIsNone(self.fake.ttt["unrelated"])

    def test_nonidentity_baselines_support_a_180_degree_rotation(self):
        driver_baseline = compose_rigid(
            (0, -1, 0, 1, 0, 0, 0, 0, 1),
            (3, -4, 2),
        )
        peer_baseline = compose_rigid(
            (1, 0, 0, 0, 0, -1, 0, 1, 0),
            (-5, 2, 7),
        )
        rotation_180 = compose_rigid(
            (-1, 0, 0, 0, -1, 0, 0, 0, 1),
            (8, -3, 1),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(driver_baseline)
        self.fake.ttt["peer"] = homogeneous_to_ttt(peer_baseline)
        group_follow.pp_follow_arm("driver", group="assembly")

        driver_target = mat4_multiply(rotation_180, driver_baseline)
        self.fake.ttt["driver"] = homogeneous_to_ttt(driver_target)
        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "applied")
        self.assert_matrix_close(result["delta"], rotation_180)
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["peer"]),
            mat4_multiply(rotation_180, peer_baseline),
        )

    def test_multiple_apply_then_rebase_uses_the_new_relative_baseline(self):
        driver_baseline = compose_rigid(
            (0, -1, 0, 1, 0, 0, 0, 0, 1),
            (2, 3, 4),
        )
        peer_baseline = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (-2, 4, 1),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(driver_baseline)
        self.fake.ttt["peer"] = homogeneous_to_ttt(peer_baseline)
        group_follow.pp_follow_arm("driver", group="assembly")

        first_delta = compose_rigid(
            (1, 0, 0, 0, 0, -1, 0, 1, 0),
            (1, 2, 3),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(
            mat4_multiply(first_delta, driver_baseline)
        )
        first = group_follow.pp_follow_apply()
        self.assertEqual(first["apply_count"], 1)

        second_delta = compose_rigid(
            (-1, 0, 0, 0, -1, 0, 0, 0, 1),
            (6, -1, 2),
        )
        second_driver = mat4_multiply(second_delta, driver_baseline)
        second_peer = mat4_multiply(second_delta, peer_baseline)
        self.fake.ttt["driver"] = homogeneous_to_ttt(second_driver)
        second = group_follow.pp_follow_apply()
        self.assertEqual(second["apply_count"], 2)
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["peer"]),
            second_peer,
        )

        rebased = group_follow.pp_follow_rebase()
        self.assertEqual(rebased["status"], "armed")
        self.assertEqual(rebased["apply_count"], 0)
        post_rebase_delta = compose_rigid(
            (0, 1, 0, -1, 0, 0, 0, 0, 1),
            (-3, 5, 0),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(
            mat4_multiply(post_rebase_delta, second_driver)
        )
        third = group_follow.pp_follow_apply()
        self.assertEqual(third["apply_count"], 1)
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["peer"]),
            mat4_multiply(post_rebase_delta, second_peer),
        )

    def test_external_follower_change_is_rejected(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        self.fake.ttt["driver"] = homogeneous_to_ttt(compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (1, 0, 0)))
        self.fake.ttt["peer"] = homogeneous_to_ttt(compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (9, 0, 0)))
        result = group_follow.pp_follow_apply()
        self.assertEqual(result["status"], "error")
        self.assertIn("changed outside", result["error"])

    def test_undo_restores_driver_and_followers(self):
        initial_driver = compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (2, 3, 4))
        initial_peer = compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (-2, 1, 5))
        self.fake.ttt["driver"] = homogeneous_to_ttt(initial_driver)
        self.fake.ttt["peer"] = homogeneous_to_ttt(initial_peer)
        group_follow.pp_follow_arm("driver", group="assembly")
        self.fake.ttt["driver"] = homogeneous_to_ttt(compose_rigid((1, 0, 0, 0, 1, 0, 0, 0, 1), (20, 0, 0)))
        group_follow.pp_follow_apply()
        result = group_follow.pp_follow_undo()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(self.fake.ttt["driver"], homogeneous_to_ttt(initial_driver))
        self.assertEqual(self.fake.ttt["peer"], homogeneous_to_ttt(initial_peer))

    def test_stale_undo_refuses_driver_and_follower_changes_unless_forced(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        applied_driver = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (3, 4, 5),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(applied_driver)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")

        external_driver = compose_rigid(
            (-1, 0, 0, 0, -1, 0, 0, 0, 1),
            (7, 8, 9),
        )
        external_peer = compose_rigid(
            (1, 0, 0, 0, 0, -1, 0, 1, 0),
            (-7, 2, 4),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(external_driver)
        self.fake.ttt["peer"] = homogeneous_to_ttt(external_peer)
        before_refusal = dict(self.fake.ttt)

        refused = group_follow.pp_follow_undo()
        self.assertEqual(refused["status"], "error")
        self.assertIn("changed after the most recent apply", refused["error"])
        self.assertIn("driver", refused["error"])
        self.assertIn("peer", refused["error"])
        self.assertEqual(self.fake.ttt, before_refusal)

        forced = group_follow.pp_follow_undo(force=1)
        self.assertEqual(forced["status"], "restored")
        self.assertTrue(forced["forced"])
        self.assertEqual(forced["stale_overwritten"], ["driver", "peer"])
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["driver"]),
            identity4(),
        )
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["peer"]),
            identity4(),
        )

    def test_undo_before_first_apply_can_restore_an_intentionally_moved_driver(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        self.fake.ttt["driver"] = homogeneous_to_ttt(
            compose_rigid(
                (1, 0, 0, 0, 1, 0, 0, 0, 1),
                (12, 0, 0),
            )
        )
        result = group_follow.pp_follow_undo()
        self.assertEqual(result["status"], "restored")
        self.assertFalse(result["forced"])
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["driver"]),
            identity4(),
        )

    def test_callback_owned_object_can_be_skipped(self):
        group_follow.register_callback_owned_object("slab")
        result = group_follow.pp_follow_arm("driver", group="assembly")
        self.assertNotIn("slab", result["followers"])
        self.assertIn("slab", result["skipped"])

    def test_explicit_static_policy_can_include_a_callback_owned_object(self):
        group_follow.register_callback_owned_object("slab")
        result = group_follow.pp_follow_arm(
            "driver",
            group="assembly",
            cgo_policy="static",
        )
        self.assertEqual(result["status"], "armed")
        self.assertIn("slab", result["followers"])
        self.assertNotIn("slab", result["skipped"])

    def test_callback_owned_object_rebuild_hook_handles_apply_and_undo(self):
        slab_baseline = compose_rigid(
            (1, 0, 0, 0, 0, -1, 0, 1, 0),
            (2, -5, 8),
        )
        self.fake.ttt["slab"] = homogeneous_to_ttt(slab_baseline)
        calls = []

        def rebuild(name, delta, context):
            calls.append((name, tuple(delta), dict(context)))
            self.fake.ttt[name] = homogeneous_to_ttt(
                mat4_multiply(delta, slab_baseline)
            )

        group_follow.register_callback_owned_object("slab", rebuild)
        armed = group_follow.pp_follow_arm(
            "driver",
            group="assembly",
            cgo_policy="rebuild",
        )
        self.assertIn("slab", armed["followers"])
        delta = compose_rigid(
            (-1, 0, 0, 0, -1, 0, 0, 0, 1),
            (4, 3, 2),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        applied = group_follow.pp_follow_apply()
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(calls[0][2]["operation"], "apply")
        self.assert_matrix_close(calls[0][1], delta)
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["slab"]),
            mat4_multiply(delta, slab_baseline),
        )

        undone = group_follow.pp_follow_undo()
        self.assertEqual(undone["status"], "restored")
        self.assertEqual(calls[1][2]["operation"], "undo")
        self.assert_matrix_close(calls[1][1], identity4())
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["slab"]),
            slab_baseline,
        )

    def test_axes_overlay_auto_registers_and_is_safely_skipped_by_default(self):
        overlay_cmd = OverlayCmd()
        pymol_module = ModuleType("pymol")
        pymol_module.cmd = overlay_cmd
        pymol_module.cgo = SimpleNamespace(CYLINDER=1, CONE=2)
        chempy_module = ModuleType("chempy")
        chempy_module.cpv = SimpleNamespace(
            add=lambda left, right: [a + b for a, b in zip(left, right)],
            transform=lambda rotation, vector: vector,
        )
        module_name = "pymolplugins.geometry.axes_overlay"
        sys.modules.pop(module_name, None)
        try:
            with patch.dict(
                sys.modules,
                {"pymol": pymol_module, "chempy": chempy_module},
            ):
                overlay = importlib.import_module(module_name)
                overlay.axes("axes_widget")
        finally:
            sys.modules.pop(module_name, None)

        self.assertIn("axes_widget", group_follow._CALLBACK_OWNED)
        self.assertIn("axes_widget", overlay_cmd.loaded_cgo)
        self.assertEqual(len(overlay_cmd.callbacks), 1)
        self.fake.add_object("axes_widget")
        armed = group_follow.pp_follow_arm("driver", group="assembly")
        self.assertEqual(armed["cgo_policy"], "skip_callback")
        self.assertNotIn("axes_widget", armed["followers"])
        self.assertEqual(
            armed["skipped"]["axes_widget"],
            "callback-owned object",
        )

    def test_group_membership_change_is_rejected(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        self.fake.groups["assembly"].remove("slab")
        result = group_follow.pp_follow_apply()
        self.assertEqual(result["status"], "error")
        self.assertIn("membership changed", result["error"])


if __name__ == "__main__":
    unittest.main()
