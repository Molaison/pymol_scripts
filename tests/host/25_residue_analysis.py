"""Exercise the installed ZIP on an owned hidden desktop via 21_hidden_desktop.py."""

import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
from zipfile import ZipFile


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--package", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--phase", choices=("all", "exports"), default="all")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
report = {"status": "running", "checks": {}}
result_path = output / "01_result.json"
if result_path.exists():
    previous = json.loads(result_path.read_text(encoding="utf-8"))
    report["prior_attempts"] = previous.get("prior_attempts", [])
    if previous.get("status") == "failed":
        report["prior_attempts"].append({key: previous[key] for key in ("status", "checks", "traceback") if key in previous})


def record_startup_error(kind, error, trace):
    report.update(status="failed", traceback="".join(traceback.format_exception(kind, error, trace)))
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.__excepthook__(kind, error, trace)


sys.excepthook = record_startup_error
temporary = tempfile.TemporaryDirectory(prefix="pymolplugins-residues-")
installed = Path(temporary.name) / "pymol_plugins"
with ZipFile(args.package) as archive:
    archive.extractall(temporary.name)
    assert "pymol_plugins/pymolplugins/residues/LICENSE.CoreToSurf" in archive.namelist()
sys.path[:0] = [str(installed), str(Path(__file__).parent)]

from pymol import cmd, gui, invocation, plugins
from pymol.Qt import QtCore, QtWidgets
from PyQt5.QtTest import QTest
from pmg_qt.pymol_qt_gui import PyMOLQtGUI
from workspace_helpers import isolate_workspace_settings


QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_DontUseNativeDialogs, True)
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
invocation.parse_args(["pymol", "-k", "-q"])
window = PyMOLQtGUI()
gui.createlegacypmgapp = window.createlegacypmgapp
window.add_lineedit()
window.resize(1100, 780)
isolate_workspace_settings(Path(temporary.name), window)
from pymolplugins.host.workspace import window_settings
settings = window_settings()
settings.setValue("updates/auto_check", False)
settings.sync()
window.show()
app.processEvents()


def wait_until(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for UI state")
        QTest.qWait(30)
        app.processEvents()


def checkpoint(name, value):
    report["checks"][name] = value
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def click(name):
    app.processEvents()
    button = next((item for item in view.findChildren(QtWidgets.QPushButton, name)
                   if item.isVisible() and item.isEnabled()), None)
    assert button is not None, name
    QTest.mouseClick(button, QtCore.Qt.LeftButton)
    app.processEvents()


def choose(combo, index):
    combo.setFocus()
    QTest.keyClick(combo, QtCore.Qt.Key_Home)
    for _ in range(index):
        QTest.keyClick(combo, QtCore.Qt.Key_Down)
    QTest.qWait(130)
    app.processEvents()


def colors():
    result = []
    cmd.iterate("insight_model", "out.append((index, color))", space={"out": result})
    return result


def render(filename):
    window.pymolwidget._pymolProcess()
    window.pymolwidget.repaint()
    app.processEvents()
    image = window.pymolwidget.grabFrameBuffer()
    distinct = {image.pixel(x, y) for x in range(0, image.width(), 4)
                for y in range(0, image.height(), 4)}
    assert len(distinct) > 30, ("Empty renderer", len(distinct))
    assert image.save(str(output / filename))


def fill_export_dialog(destination, errors):
    try:
        dialog = QtWidgets.QApplication.activeModalWidget()
        assert isinstance(dialog, QtWidgets.QFileDialog), type(dialog)
        field = dialog.findChild(QtWidgets.QLineEdit, "fileNameEdit")
        field.setFocus()
        QTest.keyClick(field, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
        expected_path = str(destination).replace("\\", "/")
        QTest.keyClicks(field, expected_path, delay=0)
        assert field.text() == expected_path, (field.text(), expected_path)
        box = dialog.findChild(QtWidgets.QDialogButtonBox)
        save_button = box.button(QtWidgets.QDialogButtonBox.Save)
        checkpoint("file_dialog", {"field_length": len(field.text()),
                                   "save_enabled": save_button.isEnabled(),
                                   "visible": dialog.isVisible()})

        def save_when_ready(tries=0):
            if save_button.isEnabled():
                field.setFocus()
                QTest.keyClick(field, QtCore.Qt.Key_Return)
            elif tries < 20:
                QtCore.QTimer.singleShot(100, lambda: save_when_ready(tries + 1))
            else:
                errors.append("File dialog Save did not become enabled")
                dialog.reject()

        QtCore.QTimer.singleShot(100, save_when_ready)
    except Exception:
        errors.append(traceback.format_exc())
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal:
            modal.reject()


def export(button_name, filename):
    destination = output / filename
    if destination.exists():
        destination.unlink()
    errors = []
    QtCore.QTimer.singleShot(100, lambda: fill_export_dialog(destination, errors))

    def cancel_stalled_dialog():
        modal = QtWidgets.QApplication.activeModalWidget()
        if isinstance(modal, QtWidgets.QFileDialog):
            modal.grab().save(str(output / "11_stalled_export.png"))
            errors.append("File dialog did not accept the save key")
            modal.reject()

    watchdog = QtCore.QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(cancel_stalled_dialog)
    watchdog.start(10000)
    click(button_name)
    watchdog.stop()
    assert not errors, errors
    assert destination.is_file(), panel.status.text()
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def review_lifecycle_regressions():
    choose(page.preset, page.preset.findData("residue_layers"))
    assert not page.status.text(), "A previous palette confirmation survived selection"
    for widget in (panel.state, panel.core, panel.boundary, panel.core_slider,
                   panel.boundary_slider, panel.units, panel.cutoff, panel.gap,
                   panel.exposure, panel.hydrophobicity, panel.format):
        assert widget.accessibleName(), widget.objectName()
    for title, control in (("State", panel.state), ("Core below", panel.core),
                           ("Surface from", panel.boundary)):
        assert any(label.text() == title and label.buddy() is control
                   for label in panel.findChildren(QtWidgets.QLabel)), title
    click("appearance_apply")
    view_colors = colors()
    pp_residues("insight_model", mode="contacts")
    command_colors = colors()
    assert command_colors != view_colors
    panel.core_slider.setFocus()
    QTest.keyClick(panel.core_slider, QtCore.Qt.Key_Right)
    wait_until(lambda: not panel.preview_active)
    assert colors() == command_colors and panel.report is None
    click("appearance_undo")
    assert colors() == command_colors and "LIFO" in page.status.text()
    pp_residue_clear()
    assert colors() == view_colors
    click("appearance_undo")
    assert colors() == baseline_colors
    checkpoint("independent_review_fixes", {"labels_and_buddies": "passed",
               "palette_message_reset": "passed", "summary_survives_copy": "passed",
               "newer_cli_blocks_preview_and_view_undo": "passed"})


exit_code = 0
try:
    cmd.load(str(root / "data/helical_bundle.pdb"), "insight_model")
    cmd.show_as("cartoon", "insight_model")
    cmd.color("cyan", "insight_model")
    cmd.zoom("insight_model")
    baseline_colors = colors()
    baseline_coords = cmd.get_coords("insight_model").copy()
    baseline_b = [a.b for a in cmd.get_model("insight_model").atom]
    render("03_renderer_before.png")

    window.menudict["PluginQt"] = window.menudict["Plugin"]
    plugins.HAVE_QT = True
    from pymolplugins.host import plugin, show_presets
    from pymolplugins.residues import presentation
    from pymolplugins.residues.commands import pp_residues, pp_residue_clear
    from pymolplugins.styles import registry
    plugin.__init_plugin__()
    toolkit = next(action.menu() for action in window.menudict["Plugin"].actions()
                   if action.menu() and action.text() == "PymolPlugins")
    assert not any(action.text() == "Residue analysis…" for action in toolkit.actions())
    action = next(action for action in toolkit.actions() if action.text() == "Change scene view…")
    toolkit.popup(window.mapToGlobal(QtCore.QPoint(80, 100)))
    app.processEvents()
    QTest.mouseClick(toolkit, QtCore.Qt.LeftButton, pos=toolkit.actionGeometry(action).center())
    view = show_presets._dialog
    assert view is not None and view.isVisible()
    assert [view.tabs.tabText(i) for i in range(view.tabs.count())] == ["Structure", "Color", "Lighting"]
    bar = view.tabs.tabBar()
    QTest.mouseClick(bar, QtCore.Qt.LeftButton, pos=bar.tabRect(1).center())
    page = view.color_page
    panel = page.residues
    choose(page.preset, page.preset.findData("residue_layers"))
    assert panel.isVisible() and not panel.isWindow() and panel.window() is view
    assert not hasattr(panel, "objects")
    click("appearance_apply")
    assert panel.report and panel.table.rowCount() > 0, panel.status.text()
    report["gui_thread"] = cmd.is_gui_thread()
    assert not {"pp_residues_core", "pp_residues_boundary", "pp_residues_surface"}.intersection(cmd.get_names())
    for name, module in list(sys.modules.items()):
        if name == "pymolplugins" or name.startswith("pymolplugins."):
            location = getattr(module, "__file__", None)
            if location:
                Path(location).resolve().relative_to(installed)
            else:
                for directory in getattr(module, "__path__", ()):
                    Path(directory).resolve().relative_to(installed)
    checkpoint("installed_view_color_entry", {"residues": panel.table.rowCount(), "counts": panel.report["counts"],
                                                "separate_residue_window": False})

    undo_count = len(page._owned_snapshots)
    panel.core_slider.setFocus()
    QTest.keyClick(panel.core_slider, QtCore.Qt.Key_Right)
    wait_until(lambda: panel.report["core_cutoff"] == 21)
    choose(panel.units, 1)
    wait_until(lambda: panel.report["unit"] == "rel")
    assert panel.core.value() == 20 and panel.boundary.value() == 50
    choose(panel.units, 0)
    wait_until(lambda: panel.report["unit"] == "abs")
    assert panel.core.value() == 21 and panel.boundary.value() == 40
    assert len(page._owned_snapshots) == undo_count
    view.resize(620, 480)
    app.processEvents()
    view.grab().save(str(output / "04_layers_panel.png"))
    checkpoint("live_thresholds_units_single_undo", "passed")

    for mode in ("layers", "rsa", "hydropathy", "exposed", "contacts"):
        report["stage"] = "color_" + mode
        choose(page.preset, page.preset.findData("residue_" + mode))
        click("appearance_apply")
        assert panel.report and panel.report["mode"] == mode, panel.status.text()
        assert not panel.status.text().startswith("Error"), panel.status.text()
    assert panel.contact_options.isVisible() and not panel.exposure_options.isVisible()
    QTest.mouseClick(page.copy, QtCore.Qt.LeftButton)
    assert "pp_residues (model" in app.clipboard().text() and "mode=contacts" in app.clipboard().text()
    QTest.mouseClick(panel.details_toggle, QtCore.Qt.LeftButton)
    app.processEvents()
    assert panel.details.isVisible()
    click("residue_copy")
    text = app.clipboard().text()
    assert "insight_model" in text and "select pp_residues_" in text, (text, panel.status.text())
    assert "protein residues" in panel.status.text() and page.status.text().startswith("Copied layer")
    checkpoint("color_metrics_and_qualified_clipboard", ["layers", "rsa", "hydropathy", "exposed", "contacts"])

    contact_colors = colors()
    rows = export("residue_export", "06_residues.csv")
    pairs = export("residue_export_contacts", "07_contacts.csv")
    assert len(rows) == panel.table.rowCount()
    assert len(pairs) == len(panel.report["pairs"]) > 0
    assert colors() == contact_colors, "Inspecting or exporting values recolored the structure"
    checkpoint("native_file_dialog_exports", {"residues": len(rows), "pairs": len(pairs)})
    click("residue_create")
    assert cmd.count_atoms("pp_residues_surface") >= 0
    render("05_renderer_contacts.png")
    view.resize(620, 600)
    app.processEvents()
    for name in ("appearance_apply", "appearance_undo", "residue_copy", "residue_create",
                 "residue_export", "residue_export_contacts"):
        widget = next(item for item in view.findChildren(QtWidgets.QPushButton, name) if item.isVisible())
        bounds = QtCore.QRect(widget.mapTo(view, QtCore.QPoint()), widget.size())
        assert view.rect().contains(bounds), (name, bounds, view.rect())
    view.grab().save(str(output / "08_compact_panel.png"))
    assert panel.table.viewport().height() > 60, panel.table.viewport().size()
    checkpoint("compact_view_controls", [view.width(), view.height()])

    choose(page.preset, page.preset.findData("alphafold_plddt"))
    assert not panel.isVisible()
    click("appearance_apply")
    assert colors() != contact_colors
    click("appearance_undo")
    assert colors() == contact_colors
    choose(page.preset, page.preset.findData("residue_exposed"))
    click("appearance_apply")
    residue_colors = colors()
    external = registry.pp_style_apply("professional_secondary_structure", "%insight_model")
    external_colors = colors()
    click("appearance_undo")
    assert colors() == external_colors and "LIFO" in page.status.text()
    registry.pp_style_revert(external)
    assert colors() == residue_colors
    click("appearance_undo")
    assert colors() == contact_colors
    while page._owned_snapshots:
        count = len(page._owned_snapshots)
        click("appearance_undo")
        assert len(page._owned_snapshots) == count - 1, page.status.text()
    assert colors() == baseline_colors
    assert pp_residue_clear()["restored_atoms"] == 0, "View previews leaked into command color cleanup"
    checkpoint("shared_undo_mixed_palettes_and_external_styles", "passed")
    review_lifecycle_regressions()

    cmd.create("second_model", "insight_model and chain A")
    cmd.color("magenta", "second_model")
    cmd.disable("second_model")
    view.refresh_objects()
    choose(page.preset, page.preset.findData("residue_contacts"))
    choose(view.objects, view.objects.findData("second_model"))
    assert panel.report is None
    click("appearance_apply")
    assert {record["object"] for record in panel.report["residues"]} == {"second_model"}
    assert colors() == baseline_colors
    click("appearance_undo")
    choose(view.objects, view.objects.findData("insight_model"))
    assert panel.report is None
    assert pp_residues() is view
    assert view.tabs.currentIndex() == 1 and page.preset.currentData() == "residue_layers"
    assert panel.window() is view
    checkpoint("shared_object_picker_and_command_reuses_view", "passed")

    QTest.mouseClick(bar, QtCore.Qt.LeftButton, pos=bar.tabRect(2).center())
    assert not view.objects.isVisible()
    lighting = view.lighting_page
    before_lighting = len(registry._REGISTRY.snapshots)
    QTest.mouseClick(lighting.apply_button, QtCore.Qt.LeftButton)
    assert len(registry._REGISTRY.snapshots) == before_lighting + 1, lighting.status.text()
    undo = next(b for b in lighting.findChildren(QtWidgets.QPushButton) if b.text() == "Undo last style")
    QTest.mouseClick(undo, QtCore.Qt.LeftButton)
    assert len(registry._REGISTRY.snapshots) == before_lighting
    checkpoint("existing_lighting_apply_and_undo", "passed")
    pp_residue_clear()
    assert not any(name.startswith("pp_residues_") for name in cmd.get_names())
    assert colors() == baseline_colors
    assert (cmd.get_coords("insight_model") == baseline_coords).all()
    assert [a.b for a in cmd.get_model("insight_model").atom] == baseline_b
    checkpoint("preserve_coordinates_b_factors_and_restore_colors", "passed")
    report.update(status="passed", version=cmd.get_version()[0], package=str(args.package.resolve()))
except Exception:
    report.update(status="failed", traceback=traceback.format_exc())
    print(report["traceback"], flush=True)
    exit_code = 1
finally:
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
    cmd.quit(exit_code)
