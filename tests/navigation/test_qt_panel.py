import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pymolplugins.navigation import qt_panel


class PanelVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        self.panel = SimpleNamespace(
            invalidate=self.native, _needsUpdate=False,
            raw_rec_list=[["a", 0, 12, "", 1], ["b", 0, 12, "", 0]],
            group_toggle_info={"a": 0, "b": 0},
            content_sa_gb=SimpleNamespace(object_tile_list=[object()]),
        )
        self.groups = [["a", 0, 12, "", 0, 0], ["b", 0, 12, "", 0, 0]]
        self.lookup = patch.object(qt_panel, "_contents_panel", return_value=self.panel)
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def test_intermediate_disable_and_final_enable_reuse_then_restore(self):
        with patch.object(qt_panel, "_update_tiles") as update:
            restore = qt_panel.arm_visibility_update({"a": False, "b": True})
            self.panel.invalidate([["a", 0, 12, "", 0], ["b", 0, 12, "", 0]], self.groups)
            self.panel.invalidate([["a", 0, 12, "", 0], ["b", 0, 12, "", 1]], self.groups)
            self.assertEqual(update.call_count, 2)
            self.native.assert_not_called()
            self.assertIs(self.panel.invalidate, self.native)
            restore()
            self.assertIs(self.panel.invalidate, self.native)

    def test_external_structure_or_expansion_change_uses_native_path(self):
        for rows, groups in (
            ([["a", 0, 12, "b", 0], ["b", 0, 12, "", 1]], self.groups),
            ([["a", 0, 12, "", 0], ["b", 0, 12, "", 1]],
             [["a", 0, 12, "", 0, 1], ["b", 0, 12, "", 0, 0]]),
        ):
            with self.subTest(rows=rows, groups=groups):
                with patch.object(qt_panel, "_update_tiles") as update:
                    qt_panel.arm_visibility_update({"a": False, "b": True})
                    self.panel.invalidate(rows, groups)
                    update.assert_not_called()
                    self.native.assert_called_with(rows, groups)
                    self.assertIs(self.panel.invalidate, self.native)

    def test_failed_change_can_restore_without_waiting_for_invalidation(self):
        restore = qt_panel.arm_visibility_update({"a": False})
        restore()
        self.panel.invalidate(self.panel.raw_rec_list, self.groups)
        self.native.assert_called_once()
        self.assertFalse(hasattr(self.panel, "_pp_restore_visibility"))

    def test_pending_or_unknown_panel_is_left_alone(self):
        self.panel._needsUpdate = True
        self.assertIsNone(qt_panel.arm_visibility_update({"a": False}))
        self.assertIs(self.panel.invalidate, self.native)
        self.panel._needsUpdate = False
        self.panel.raw_rec_list = [["unknown", 0]]
        self.assertIsNone(qt_panel.arm_visibility_update({"a": False}))


if __name__ == "__main__":
    unittest.main()
