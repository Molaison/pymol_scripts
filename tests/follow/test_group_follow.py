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
        self.set_calls = []
        self.fail_set_counts = {}

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
        self.set_calls.append(name)
        remaining = self.fail_set_counts.get(name, 0)
        if remaining:
            self.fail_set_counts[name] = remaining - 1
            raise RuntimeError(f"injected set failure for {name}")
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
        self.assertEqual(rebased["status"], "rebased")
        self.assertEqual(rebased["apply_count"], 0)
        self.assertEqual(rebased["previous_apply_count"], 2)
        self.assertTrue(rebased["undo_baseline_replaced"])
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

    def test_active_session_requires_explicit_replace(self):
        first = group_follow.pp_follow_arm("driver", group="assembly")
        old_session = group_follow._SESSION

        refused = group_follow.pp_follow_arm("peer", group="assembly")

        self.assertEqual(first["status"], "armed")
        self.assertEqual(refused["status"], "error")
        self.assertIn("already armed", refused["error"])
        self.assertIs(group_follow._SESSION, old_session)
        self.assertEqual(group_follow._SESSION.driver, "driver")

        replaced = group_follow.pp_follow_arm(
            "peer", group="assembly", replace=1
        )
        self.assertEqual(replaced["status"], "armed")
        self.assertTrue(replaced["replaced"])
        self.assertFalse(replaced["discarded_undo_baseline"])
        self.assertEqual(group_follow._SESSION.driver, "peer")

    def test_replacing_applied_session_reports_discarded_undo_baseline(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        self.fake.ttt["driver"] = homogeneous_to_ttt(
            compose_rigid(
                (1, 0, 0, 0, 1, 0, 0, 0, 1),
                (3, 0, 0),
            )
        )
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")

        replaced = group_follow.pp_follow_arm(
            "peer", group="assembly", replace=1
        )

        self.assertEqual(replaced["status"], "armed")
        self.assertEqual(replaced["replaced_apply_count"], 1)
        self.assertTrue(replaced["discarded_undo_baseline"])

    def test_expected_user_errors_are_structured_without_tracebacks(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        with patch.object(group_follow.traceback, "print_exc") as print_exc:
            active = group_follow.pp_follow_arm("peer", group="assembly")
        self.assertEqual(active["error_code"], "follow_session_active")
        self.assertTrue(active["scene_preserved"])
        self.assertIn("replace=1", active["guidance"])
        print_exc.assert_not_called()

    def test_automatic_group_inference_prefers_direct_parent_over_ancestor(self):
        self.fake.types.update(
            {
                "inner": "object:group",
                "outer": "object:group",
            }
        )
        self.fake.groups = {
            "inner": ["driver", "peer"],
            "outer": ["inner"],
        }
        result = group_follow.pp_follow_arm("driver")

        self.assertEqual(result["status"], "armed")
        self.assertEqual(result["group"], "inner")
        self.assertTrue(result["group_inferred"])
        self.assertEqual(result["group_candidates"], ["inner", "outer"])
        self.assertEqual(result["followers"], ["peer"])

    def test_automatic_group_inference_is_visible_in_status(self):
        result = group_follow.pp_follow_arm("driver")

        self.assertEqual(result["status"], "armed")
        self.assertTrue(result["group_inferred"])
        self.assertEqual(result["group_candidates"], ["assembly"])
        self.assertIn("pp_follow_apply", result["next_command"])

    def test_failed_replacement_preserves_old_session_and_undo(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        old_session = group_follow._SESSION
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (5, -2, 1),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")
        expected_before_failure = dict(old_session.expected_h)

        failed = group_follow.pp_follow_arm(
            "missing_driver", group="assembly", replace=1
        )

        self.assertEqual(failed["status"], "error")
        self.assertIs(group_follow._SESSION, old_session)
        self.assertEqual(old_session.apply_count, 1)
        self.assertEqual(old_session.expected_h, expected_before_failure)
        undone = group_follow.pp_follow_undo()
        self.assertEqual(undone["status"], "restored")
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["driver"]), identity4()
        )

    def test_identity_driver_delta_is_a_no_change_without_follower_writes(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        before = dict(self.fake.ttt)
        self.fake.set_calls.clear()

        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["followers"], [])
        self.assertEqual(result["apply_count"], 0)
        self.assertEqual(self.fake.set_calls, [])
        self.assertEqual(self.fake.ttt, before)

    def test_repeated_apply_without_new_driver_motion_is_no_change(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (2, 0, 0),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")
        before = dict(self.fake.ttt)
        self.fake.set_calls.clear()

        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["apply_count"], 1)
        self.assertEqual(self.fake.set_calls, [])
        self.assertEqual(self.fake.ttt, before)

    def test_no_change_still_detects_an_external_follower_edit(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        self.fake.ttt["peer"] = homogeneous_to_ttt(
            compose_rigid(
                (1, 0, 0, 0, 1, 0, 0, 0, 1),
                (9, 0, 0),
            )
        )
        self.fake.set_calls.clear()

        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "error")
        self.assertIn("changed outside", result["error"])
        self.assertEqual(self.fake.set_calls, [])

    def test_returning_driver_to_baseline_restores_followers(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (6, 0, 0),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")
        self.fake.ttt["driver"] = homogeneous_to_ttt(identity4())

        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "applied")
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["peer"]), identity4()
        )
        self.assert_matrix_close(
            ttt_to_homogeneous(self.fake.ttt["slab"]), identity4()
        )

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

    def test_undo_set_failure_rolls_back_every_attempted_object(self):
        group_follow.pp_follow_arm("driver", group="assembly")
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (4, 5, 6),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")
        before = {
            name: ttt_to_homogeneous(value)
            for name, value in self.fake.ttt.items()
        }
        old_expected = dict(group_follow._SESSION.expected_h)
        self.fake.fail_set_counts["peer"] = 1

        result = group_follow.pp_follow_undo()

        self.assertEqual(result["status"], "error")
        self.assertIn("injected set failure", result["error"])
        self.assertEqual(result["rollback_failures"], [])
        self.assertEqual(group_follow._SESSION.apply_count, 1)
        self.assertEqual(group_follow._SESSION.expected_h, old_expected)
        for name, expected in before.items():
            self.assert_matrix_close(
                ttt_to_homogeneous(self.fake.ttt[name]), expected
            )

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

    def test_callback_apply_failure_replays_previous_state(self):
        slab_baseline = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (0, 3, 0),
        )
        self.fake.ttt["slab"] = homogeneous_to_ttt(slab_baseline)
        operations = []

        def rebuild(name, delta, context):
            operations.append(context["operation"])
            self.fake.ttt[name] = homogeneous_to_ttt(
                mat4_multiply(delta, slab_baseline)
            )
            if context["operation"] == "apply":
                raise RuntimeError("injected callback apply failure")

        group_follow.register_callback_owned_object("slab", rebuild)
        group_follow.pp_follow_arm(
            "driver", group="assembly", cgo_policy="rebuild"
        )
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (7, 0, 0),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        before = {
            name: ttt_to_homogeneous(value)
            for name, value in self.fake.ttt.items()
        }

        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["rollback_failures"], [])
        self.assertEqual(operations, ["apply", "rollback"])
        self.assertEqual(group_follow._SESSION.apply_count, 0)
        for name, expected in before.items():
            self.assert_matrix_close(
                ttt_to_homogeneous(self.fake.ttt[name]), expected
            )

    def test_lost_callback_hook_is_rejected_before_follower_writes(self):
        def rebuild(name, delta, context):
            self.fake.ttt[name] = homogeneous_to_ttt(delta)

        group_follow.register_callback_owned_object("slab", rebuild)
        group_follow.pp_follow_arm(
            "driver", group="assembly", cgo_policy="rebuild"
        )
        group_follow.unregister_callback_owned_object("slab")
        self.fake.ttt["driver"] = homogeneous_to_ttt(
            compose_rigid(
                (1, 0, 0, 0, 1, 0, 0, 0, 1),
                (8, 0, 0),
            )
        )
        before = dict(self.fake.ttt)
        self.fake.set_calls.clear()

        result = group_follow.pp_follow_apply()

        self.assertEqual(result["status"], "error")
        self.assertIn("lost their rebuild hooks", result["error"])
        self.assertEqual(result["rollback_failures"], [])
        self.assertEqual(self.fake.set_calls, [])
        self.assertEqual(self.fake.ttt, before)

    def test_callback_undo_failure_rolls_back_driver_and_all_followers(self):
        slab_baseline = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (0, 2, 0),
        )
        self.fake.ttt["slab"] = homogeneous_to_ttt(slab_baseline)
        fail_undo = {"value": False}
        operations = []

        def rebuild(name, delta, context):
            operations.append(context["operation"])
            self.fake.ttt[name] = homogeneous_to_ttt(
                mat4_multiply(delta, slab_baseline)
            )
            if context["operation"] == "undo" and fail_undo["value"]:
                group_follow.unregister_callback_owned_object(name)
                raise RuntimeError("injected callback undo failure")

        group_follow.register_callback_owned_object("slab", rebuild)
        group_follow.pp_follow_arm(
            "driver", group="assembly", cgo_policy="rebuild"
        )
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (3, 4, 0),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")
        before = {
            name: ttt_to_homogeneous(value)
            for name, value in self.fake.ttt.items()
        }
        old_expected = dict(group_follow._SESSION.expected_h)
        fail_undo["value"] = True

        result = group_follow.pp_follow_undo()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["rollback_failures"], [])
        self.assertEqual(operations[-2:], ["undo", "rollback"])
        self.assertEqual(group_follow._SESSION.apply_count, 1)
        self.assertEqual(group_follow._SESSION.expected_h, old_expected)
        for name, expected in before.items():
            self.assert_matrix_close(
                ttt_to_homogeneous(self.fake.ttt[name]), expected
            )

    def test_callback_rollback_failure_is_reported(self):
        slab_baseline = identity4()

        def rebuild(name, delta, context):
            self.fake.ttt[name] = homogeneous_to_ttt(
                mat4_multiply(delta, slab_baseline)
            )
            if context["operation"] in {"undo", "rollback"}:
                raise RuntimeError(
                    f"injected callback {context['operation']} failure"
                )

        group_follow.register_callback_owned_object("slab", rebuild)
        group_follow.pp_follow_arm(
            "driver", group="assembly", cgo_policy="rebuild"
        )
        delta = compose_rigid(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            (1, 0, 0),
        )
        self.fake.ttt["driver"] = homogeneous_to_ttt(delta)
        self.assertEqual(group_follow.pp_follow_apply()["status"], "applied")

        result = group_follow.pp_follow_undo()

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            [item["object"] for item in result["rollback_failures"]],
            ["slab"],
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
