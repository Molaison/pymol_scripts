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
from workspace_helpers import isolate_workspace_settings, resize_workspace


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
settings.setValue("residues/mode", "floating")
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
    button = panel.findChild(QtWidgets.QPushButton, name)
    assert button is not None and button.isVisible() and button.isEnabled(), name
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


def export(button_name, filename):
    destination = output / filename
    if destination.exists():
        destination.unlink()
    errors = []

    def fill_dialog():
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
                    QTest.mouseClick(save_button, QtCore.Qt.LeftButton)
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

    QtCore.QTimer.singleShot(100, fill_dialog)
    click(button_name)
    assert not errors, errors
    assert destination.is_file(), panel.status.text()
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


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
    from pymolplugins.host import plugin
    plugin.__init_plugin__()
    toolkit = next(action.menu() for action in window.menudict["Plugin"].actions()
                   if action.menu() and action.text() == "PymolPlugins")
    action = next(action for action in toolkit.actions() if action.text() == "Residue analysis…")
    toolkit.popup(window.mapToGlobal(QtCore.QPoint(80, 100)))
    app.processEvents()
    QTest.mouseClick(toolkit, QtCore.Qt.LeftButton, pos=toolkit.actionGeometry(action).center())
    from pymolplugins.residues import panel as panel_module
    panel = panel_module._panel
    assert panel is not None and panel.isVisible()
    assert panel.report and panel.table.rowCount() > 0, panel.status.text()
    report["gui_thread"] = cmd.is_gui_thread()
    native_analyze = panel_module.pp_residues
    native_table = panel._show_report

    def timed_analyze(*arguments, **options):
        started = time.monotonic()
        result = native_analyze(*arguments, **options)
        report.setdefault("diagnostic_calls", []).append({"kind": "analysis", "mode": options.get("mode"),
                                                         "seconds": time.monotonic() - started})
        return result

    def timed_table():
        started = time.monotonic()
        native_table()
        report.setdefault("diagnostic_calls", []).append({"kind": "table", "seconds": time.monotonic() - started})

    panel_module.pp_residues = timed_analyze
    panel._show_report = timed_table
    assert not {"pp_residues_core", "pp_residues_boundary", "pp_residues_surface"}.intersection(cmd.get_names())
    for name, module in list(sys.modules.items()):
        if name == "pymolplugins" or name.startswith("pymolplugins."):
            location = getattr(module, "__file__", None)
            if location:
                Path(location).resolve().relative_to(installed)
            else:
                for directory in getattr(module, "__path__", ()):
                    Path(directory).resolve().relative_to(installed)
    checkpoint("installed_native_menu", {"residues": panel.table.rowCount(), "counts": panel.report["counts"]})

    if args.phase == "all":
        panel.core_slider.setFocus()
        QTest.keyClick(panel.core_slider, QtCore.Qt.Key_Right)
        wait_until(lambda: panel.report["core_cutoff"] == 21)
        choose(panel.units, 1)
        wait_until(lambda: panel.report["unit"] == "rel")
        assert panel.core.value() == 20 and panel.boundary.value() == 50
        choose(panel.units, 0)
        wait_until(lambda: panel.report["unit"] == "abs")
        assert panel.core.value() == 21 and panel.boundary.value() == 40
        panel.grab().save(str(output / "04_layers_panel.png"))
        checkpoint("live_thresholds_and_units", "passed")

        for index, mode in enumerate(presentation_modes := ("layers", "rsa", "hydropathy", "exposed", "contacts")):
            report["stage"] = "color_" + mode
            checkpoint("active_color_mode", mode)
            choose(panel.mode, index)
            wait_until(lambda mode=mode: panel.report and panel.report["mode"] == mode)
            assert not panel.status.text().startswith("Error"), panel.status.text()
        assert panel.contact_options.isVisible() and not panel.exposure_options.isVisible()
        click("residue_copy")
        text = app.clipboard().text()
        assert "insight_model" in text and "select pp_residues_" in text
        checkpoint("color_metrics_and_qualified_clipboard", list(presentation_modes))
    else:
        choose(panel.mode, 4)
        wait_until(lambda: panel.report["mode"] == "contacts")
    report["stage"] = "residue_export"
    checkpoint("opening_residue_export", True)
    rows = export("residue_export", "06_residues.csv")
    report["stage"] = "contact_export"
    checkpoint("opening_contact_export", True)
    pairs = export("residue_export_contacts", "07_contacts.csv")
    assert len(rows) == panel.table.rowCount()
    assert len(pairs) == len(panel.report["pairs"]) > 0
    checkpoint("native_file_dialog_exports", {"residues": len(rows), "pairs": len(pairs)})

    click("residue_create")
    assert cmd.count_atoms("pp_residues_surface") >= 0
    render("05_renderer_contacts.png")
    resize_workspace(panel, 620, 500)
    app.processEvents()
    for name in ("residue_analyze", "residue_restore", "residue_copy", "residue_create", "residue_export", "residue_export_contacts"):
        widget = panel.findChild(QtWidgets.QPushButton, name)
        bounds = QtCore.QRect(widget.mapTo(panel, QtCore.QPoint()), widget.size())
        assert panel.rect().contains(bounds), (name, bounds, panel.rect())
    assert panel.table.viewport().height() > 60
    panel.grab().save(str(output / "08_compact_panel.png"))
    checkpoint("compact_controls", [panel.width(), panel.height()])
    click("residue_restore")
    assert colors() == baseline_colors
    assert not any(name.startswith("pp_residues_") for name in cmd.get_names())
    assert (cmd.get_coords("insight_model") == baseline_coords).all()
    assert [a.b for a in cmd.get_model("insight_model").atom] == baseline_b
    checkpoint("restore_coordinates_b_factors_colors", "passed")
    report.update(status="passed", version=cmd.get_version()[0], package=str(args.package.resolve()))
except Exception:
    report.update(status="failed", traceback=traceback.format_exc())
    print(report["traceback"], flush=True)
    exit_code = 1
finally:
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
    cmd.quit(exit_code)
