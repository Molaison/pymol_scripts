"""Explicit confirmation of a live group membership refresh."""

from pymol import gui
from pymol.Qt import QtWidgets

from . import commands


def show_refresh_dialog(controller, plan):
    """Show current group changes and reject confirmation if the scene changed."""
    window = gui.get_qtwindow()
    dialog = QtWidgets.QDialog(window)
    dialog.setWindowTitle("Review group changes")
    dialog.resize(600, 400)
    layout = QtWidgets.QVBoxLayout(dialog)
    details = QtWidgets.QPlainTextEdit()
    details.setReadOnly(True)
    lines = ["Continue at: " + plan["current_entry"].removeprefix("group:"),
             "Current group retained." if plan["position_preserved"] else
             "Current group was removed; the nearest remaining position will be used.", ""]
    for group, change in plan["membership_changes"].items():
        lines.append(group + (" (removed)" if change["group_missing"] else ""))
        lines.extend("  + " + name for name in change["added"])
        lines.extend("  - " + name for name in change["removed"])
    details.setPlainText("\n".join(lines))
    layout.addWidget(details)
    status = QtWidgets.QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
    refresh = buttons.addButton("Refresh groups", QtWidgets.QDialogButtonBox.AcceptRole)
    layout.addWidget(buttons)

    def accept():
        try:
            if commands._CONTROLLER is not controller or commands._group_refresh_plan(controller)[1] != plan:
                status.setText("Groups changed again. Close this dialog and review the latest changes.")
                refresh.setEnabled(False)
                return
            result = commands.pp_nav_refresh(confirm=1)
            if result["status"] != "refreshed":
                status.setText(str(result.get("error", result)))
                return
            dialog.accept()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            status.setText(str(exc))

    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    dialog.refresh_button = refresh
    window._pp_group_refresh_dialog = dialog
    dialog.show()
    return dialog
