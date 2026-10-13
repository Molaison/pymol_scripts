"""Run in a separate Qt process so the core's fake PyMOL fixture stays isolated."""
import os
import subprocess
import sys
import unittest


class ReviewStepsWidgetTests(unittest.TestCase):
    def test_add_reorder_edit_fixed_and_save(self):
        program = r'''
from pymol.Qt import QtWidgets
from pymolplugins.annotation.review_steps_widgets import ReviewStepsDialog
app = QtWidgets.QApplication([])
dialog = ReviewStepsDialog([{"type": "object", "name": "candidate"}, {"type": "commands"}],
                           ["structure_path", "anchor_path"], "show cartoon, {candidate}")
dialog.add_object_button.click()
dialog.name_edit.setText("scaffold")
dialog._store()
dialog.path_column_combo.setCurrentText("anchor_path")
dialog.fixed_checkbox.setChecked(True)
dialog.commands_edit.setPlainText("color cyan, {scaffold}")
dialog.move_up_button.click()
dialog.move_up_button.click()
assert [item.get("name", item["type"]) for item in dialog.steps] == ["scaffold", "candidate", "commands"]
dialog.add_commands_button.click()
dialog.commands_edit.setPlainText("show sticks, {scaffold}")
dialog.apply_button.click()
assert dialog.result() == QtWidgets.QDialog.Accepted, dialog.error_label.text()
assert dialog.steps[0] == {"type": "object", "name": "scaffold", "path_column": "anchor_path",
                           "fixed": True, "on_load_cmd": "color cyan, {scaffold}"}
assert dialog.steps[-1] == {"type": "commands", "commands": "show sticks, {scaffold}"}
print("ordered step editor passed")
'''
        result = subprocess.run([sys.executable, "-c", program], env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ordered step editor passed", result.stdout)
