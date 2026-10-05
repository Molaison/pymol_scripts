"""Exercise file/CSV pair processing through visible Windows PyMOL controls."""

import argparse
import csv
import datetime
import glob
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_workflow_smoke import wait_for
from annotator_layout_smoke import click, combo_text_bounds, visible
from workspace_helpers import isolate_workspace_settings, resize_workspace


RECIPE = "align {A}, {B}, cycles=0\ntranslate [0, 2, 0], {B}"
POINTS = [(0, 0, 0), (3.8, 0, 0), (6.1, 2.5, 0), (8, 3, 2), (10.5, 1, 3), (12.8, 2.2, 4.6)]


def write_structure(path, points):
    path.write_text("".join(
        "ATOM  %5d  CA  ALA A%4d    %8.3f%8.3f%8.3f  1.00 20.00           C\n" %
        (index, index, *point) for index, point in enumerate(points, 1)) + "END\n", encoding="ascii")


def fixtures(root):
    all_files, pairs = root / "all structures", root / "pairs [set]"
    all_files.mkdir()
    pairs.mkdir()
    write_structure(all_files / "ref.pdb", POINTS)
    for identifier in (1, 2, 10):
        mobile = [(-y + 17 + identifier, x - 9, z + 5) for x, y, z in POINTS]
        write_structure(all_files / ("A%d.pdb" % identifier), mobile)
        write_structure(pairs / ("%d_A.pdb" % identifier), mobile)
        write_structure(pairs / ("%d_B.pdb" % identifier),
                        [(x + identifier, y, z) for x, y, z in POINTS])
    table = root / "pairs, scored.csv"
    with table.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Design ID", "Candidate file", "Reference file", "Score"])
        for identifier in (10, 1):
            writer.writerow([identifier, "pairs [set]/%d_A.pdb" % identifier,
                             "pairs [set]/%d_B.pdb" % identifier, 0.8])
    return all_files, pairs, table


def choose(combo, text):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    visible(combo)
    target = combo.findText(text)
    assert target >= 0, text
    combo.setFocus()
    QTest.keyClick(combo, QtCore.Qt.Key_Home)
    for _ in range(target):
        QTest.keyClick(combo, QtCore.Qt.Key_Down)
    assert combo.currentText() == text, (combo.currentText(), text)
    QTest.qWait(30)


def type_text(editor, text):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    visible(editor)
    editor.setFocus()
    QTest.keyClick(editor, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
    for index, line in enumerate(str(text).splitlines()):
        if index:
            QTest.keyClick(editor, QtCore.Qt.Key_Return)
        QTest.keyClicks(editor, line)


def tab(dialog, index):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    bar = dialog.tabs.tabBar()
    QTest.mouseClick(bar, QtCore.Qt.LeftButton, pos=bar.tabRect(index).center())
    QTest.qWait(30)


def scroll_to(area, widget):
    # ensureWidgetVisible can follow a line edit's cursor instead of its padded bounds.
    point = widget.mapTo(area.widget(), widget.rect().center())
    area.ensureVisible(point.x(), point.y(), widget.width() // 2 + 8, widget.height() // 2 + 8)


def configure(dialog, a, b, output, *, paired=False, csv_mode=False, save_b=True, recipe=RECIPE,
              match="ID template", a_pattern="", b_pattern=""):
    from pymol.Qt import QtWidgets
    tab(dialog, 0)
    area = dialog.tabs.widget(0)
    scroll_to(area, dialog.source)
    choose(dialog.source, "CSV table" if csv_mode else "Folder / file patterns")
    scroll_to(area, dialog.mode)
    choose(dialog.mode, "A / B by ID" if paired else "All against reference")
    if paired and not csv_mode:
        scroll_to(area, dialog.match)
        choose(dialog.match, match)
        if match == "Regular expression":
            for editor, value in ((dialog.a_pattern, a_pattern), (dialog.b_pattern, b_pattern)):
                scroll_to(area, editor)
                type_text(editor, value)
    scroll_to(area, dialog.a_edit)
    type_text(dialog.a_edit, a)
    if csv_mode:
        scroll_to(area, dialog.headers_button)
        click(dialog.headers_button)
        wait_for(QtWidgets.QApplication.instance(), lambda: dialog.future is None)
        assert not dialog.error_box.isVisible(), dialog.error_box.toPlainText()
        for combo, title in ((dialog.a_column, "Candidate file"), (dialog.id_column, "Design ID")):
            scroll_to(area, combo)
            choose(combo, title)
    if csv_mode and paired:
        scroll_to(area, dialog.b_column)
        choose(dialog.b_column, b)
    else:
        scroll_to(area, dialog.b_edit)
        type_text(dialog.b_edit, b)
    scroll_to(area, dialog.output)
    type_text(dialog.output, output)
    if dialog.save_b.isChecked() != save_b:
        scroll_to(area, dialog.save_b)
        click(dialog.save_b)
    tab(dialog, 1)
    type_text(dialog.commands, recipe)


def prepare(dialog, app, expected_ids):
    from pymol import cmd
    from pymol.Qt import QtCore
    before = set(cmd.get_names("all"))
    click(dialog.preview_button)
    wait_for(app, lambda: dialog.future is None)
    assert not dialog.error_box.isVisible(), dialog.error_box.toPlainText()
    assert dialog.run_button.isEnabled()
    assert [row["id"] for row in dialog.model.rows] == expected_ids
    assert len(dialog.model.data(dialog.model.index(0, 4), QtCore.Qt.ToolTipRole).splitlines()) == 2
    assert not Path(dialog.config().output).exists()
    assert set(cmd.get_names("all")) == before, "Preview changed the scene"


def execute(dialog, app):
    click(dialog.run_button)
    wait_for(app, lambda: dialog.runner is not None and not dialog.runner.running and not dialog.run_timer.isActive())
    report = dialog.runner.report()
    assert json.loads(Path(report["report_path"]).read_text(encoding="utf-8")) == report
    assert dialog.export_button.isEnabled() and not dialog.stop_button.isEnabled()
    return report


def output_error(report, cmd):
    from pymol.Qt import QtWidgets
    app = QtWidgets.QApplication.instance()
    maximum = 0.0
    for result in report["results"]:
        assert result["status"] == "completed", result
        reference = cmd.get_coords("user_structure").copy()
        if report["config"]["mode"] == "paired":
            reference[:, 0] += int(result["id"])
        assert len(result["outputs"]) == 2
        for index, path in enumerate(result["outputs"]):
            name = "batch_check_" + uuid.uuid4().hex[:12]
            assert Path(path).stat().st_size > 0
            loaded = cmd.load(path, name, zoom=0)
            try:
                wait_for(app, lambda: cmd.get_coordset(name) is not None, timeout=5)
            except TimeoutError as exc:
                details = dict(path=path, load_result=loaded, objects=cmd.get_names("objects"),
                               current_state=cmd.get_state(), file_bytes=Path(path).read_bytes()[:1000].hex())
                if name in details["objects"]:
                    for label, query in (("type", lambda: cmd.get_type(name)),
                                         ("atoms", lambda: cmd.count_atoms(name)),
                                         ("states", lambda: cmd.count_states(name)),
                                         ("model", lambda: cmd.get_model(name, state=0)),
                                         ("coordinates", lambda: [str(cmd.get_coords(name, state=state))
                                                                  for state in (0, 1, 2, -1)])):
                        try:
                            details[label] = str(query())
                        except Exception:
                            details[label] = traceback.format_exc()
                raise AssertionError(details) from exc
            actual = cmd.get_coordset(name)
            assert actual is not None, dict(path=path, load_result=loaded, objects=cmd.get_names("objects"),
                                            current_state=cmd.get_state(), file_bytes=Path(path).read_bytes()[:1000].hex())
            expected = reference.copy()
            if index:
                expected[:, 1] += 2
            assert actual.shape == expected.shape
            selected = cmd.get_coords(name)
            assert selected is not None and (selected == actual).all()
            error = float(abs(actual - expected).max())
            maximum = max(maximum, error)
            assert error < 0.003, (path, error, actual.tolist(), expected.tolist())
            cmd.delete(name)
            wait_for(app, lambda: name not in cmd.get_names("objects"))
    return maximum


def screenshot(dialog, output, name):
    path = output.with_name(output.stem + "-" + name + ".png")
    assert dialog.grab().save(str(path))


def check_style(dialog, panel=None):
    from pymol.Qt import QtGui, QtWidgets
    from pymolplugins.annotation.panel_style import STYLE
    assert dialog.objectName() == "batchWorkspace" and dialog.styleSheet() == STYLE
    controls = (dialog.a_edit, dialog.source, dialog.run_button, dialog.table, dialog.summary)
    fonts = [QtGui.QFontInfo(widget.font()).family() for widget in controls]
    assert fonts == ["Segoe UI"] * len(controls), fonts
    command_font = QtGui.QFontInfo(dialog.commands.font()).family()
    assert command_font == "Consolas", command_font
    assert dialog.commands.objectName() == "commandEditor"
    assert dialog.run_button.property("role") == "primary"
    assert dialog.error_box.property("role") == "error"
    assert not dialog.table.showGrid() and dialog.table.verticalHeader().isHidden()
    assert dialog.table.verticalHeader().defaultSectionSize() == 36
    assert dialog.progress.height() == 3 and not dialog.progress.isTextVisible()
    icons = [button for button in dialog.findChildren(QtWidgets.QToolButton) if button.property("role") == "icon"]
    assert len(icons) == 5
    for button in icons:
        assert button.width() == button.height() == 34 and not button.icon().isNull(), dict(
            icon=button.toolTip(), size=[button.width(), button.height()], null=button.icon().isNull(),
            minimum=[button.minimumWidth(), button.minimumHeight()],
            maximum=[button.maximumWidth(), button.maximumHeight()])
        assert button.toolTip()
    image = dialog.run_button.grab().toImage()
    colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 3)
              for y in range(0, image.height(), 3)}
    expected = {"#356ae6", "#2858cb"} if dialog.run_button.isEnabled() else {"#dce5f7"}
    assert colors & expected, (dialog.run_button.isEnabled(), sorted(colors))
    if panel is not None:
        assert panel.styleSheet() == dialog.styleSheet()
        assert QtGui.QFontInfo(panel.path_column_edit.font()).family() == fonts[0]
        assert QtGui.QFontInfo(panel.step_cmd_edit.font()).family() == command_font
        assert panel.dataset_table.verticalHeader().defaultSectionSize() == 36
        assert panel.error_label.property("role") == dialog.error_box.property("role")
        assert all(button.width() == button.height() == 34
                   for button in (panel.prev_btn, panel.next_btn, panel.more_button))
    return dict(status="passed", control_fonts=fonts, command_font=command_font,
                primary_colors=sorted(colors & expected), table_row_height=36, icon_size=34,
                matches_annotation=panel is not None)


def visible_table(table):
    from pymol.Qt import QtCore
    # Qt excludes an opaque header's paint area from its parent's visibleRegion.
    visible(table.horizontalHeader().viewport())
    visible(table.viewport())
    bounds = QtCore.QRect(table.mapTo(table.window(), QtCore.QPoint()), table.size())
    assert table.isVisible() and table.window().rect().contains(bounds)
    return list(bounds.getRect())


def check_layout(dialog, app, output):
    from pymol.Qt import QtWidgets
    from PyQt5.QtTest import QTest
    bounds = {}
    for width, height in ((1000, 720), (760, 560)):
        resize_workspace(dialog, width, height)
        QTest.qWait(80)
        assert dialog.width() == width and dialog.height() == height
        controls = [dialog.tabs, dialog.table, dialog.summary, dialog.progress, dialog.preview_button,
                    dialog.run_button, dialog.stop_button, dialog.export_button]
        bounds[str(width)] = []
        for widget in controls:
            try:
                bounds[str(width)].append(visible_table(widget) if widget is dialog.table else visible(widget))
            except AssertionError as exc:
                raise AssertionError(dict(widget=type(widget).__name__, name=widget.objectName(),
                                          geometry=widget.geometry().getRect(), rect=widget.rect().getRect(),
                                          visible=widget.visibleRegion().boundingRect().getRect())) from exc
        for button in controls[-4:]:
            option = QtWidgets.QStyleOptionButton()
            button.initStyleOption(option)
            contents = button.style().subElementRect(QtWidgets.QStyle.SE_PushButtonContents, option, button)
            needed = button.fontMetrics().horizontalAdvance(button.text()) + button.iconSize().width() + 4
            assert contents.width() >= needed, (button.text(), contents.width(), needed)
            assert contents.height() >= button.fontMetrics().height()
        buttons = controls[-4:]
        for index, button in enumerate(buttons):
            for other in buttons[index + 1:]:
                assert not button.geometry().intersects(other.geometry())
        assert dialog.summary.fontMetrics().horizontalAdvance(dialog.summary.text()) < dialog.summary.width()
        screenshot(dialog, output, "layout-%d" % width)
        tab(dialog, 0)
        dialog.tabs.widget(0).verticalScrollBar().setValue(0)
        for combo in (dialog.source, dialog.mode):
            combo_text_bounds(combo)
        assert dialog.tabs.widget(0).horizontalScrollBar().maximum() == 0
        screenshot(dialog, output, "inputs-%d" % width)
        tab(dialog, 1)
    resize_workspace(dialog, 1000, 720)
    app.processEvents()
    return bounds


def check_locations(dialog, app, output):
    from PyQt5.QtTest import QTest
    tab(dialog, 0)
    area = dialog.tabs.widget(0)
    scroll_to(area, dialog.location_toggle)
    click(dialog.location_toggle)
    QTest.qWait(80)
    assert dialog.location_box.isVisible()
    for width, height in ((1000, 720), (760, 560)):
        resize_workspace(dialog, width, height)
        QTest.qWait(80)
        for name in ("profile", "base", "target_format", "distribution", "encoding", "delimiter"):
            widget = getattr(dialog, name)
            scroll_to(area, widget)
            try:
                visible(widget)
            except AssertionError as exc:
                raise AssertionError(dict(widget=name, kind=type(widget).__name__,
                                          geometry=widget.geometry().getRect(), rect=widget.rect().getRect(),
                                          visible=widget.visibleRegion().boundingRect().getRect(),
                                          parent=widget.parentWidget().rect().getRect(),
                                          scroll=area.verticalScrollBar().value(),
                                          maximum=area.verticalScrollBar().maximum())) from exc
        assert area.horizontalScrollBar().maximum() == 0
        scroll_to(area, dialog.profile)
        screenshot(dialog, output, "locations-%d" % width)
    scroll_to(area, dialog.location_toggle)
    click(dialog.location_toggle)
    QTest.qWait(80)
    assert not dialog.location_box.isVisible()
    resize_workspace(dialog, 1000, 720)
    QTest.qWait(80)


def check_cancellation(dialog, app, root, all_files):
    configure(dialog, all_files, all_files / "ref.pdb", root / "cancelled")
    prepare(dialog, app, ["1", "2", "3"])
    dialog.run_timer.setInterval(20)
    click(dialog.run_button)
    wait_for(app, lambda: dialog.runner.completed == 1)
    click(dialog.stop_button)
    wait_for(app, lambda: not dialog.runner.running and not dialog.run_timer.isActive())
    dialog.run_timer.setInterval(0)
    report = dialog.runner.report()
    assert report["status"] == "cancelled" and report["completed"] == 1, report
    assert report["results"][0]["status"] == "completed" and not report["owned_objects"]
    assert json.loads(Path(report["report_path"]).read_text())["status"] == "cancelled"
    return report


def check_failures(dialog, app, root, all_files, pairs, output):
    reports = []
    for policy in ("stop", "continue"):
        configure(dialog, all_files, all_files / "ref.pdb", root / ("error-" + policy),
                  recipe="align {A}, missing_batch_reference\ntranslate [0, 2, 0], {B}")
        tab(dialog, 0)
        scroll_to(dialog.tabs.widget(0), dialog.on_error)
        choose(dialog.on_error, "Stop on error" if policy == "stop" else "Continue with next pair")
        prepare(dialog, app, ["1", "2", "3"])
        report = execute(dialog, app)
        assert report["status"] == "partial_error"
        assert report["failed"] == (1 if policy == "stop" else 3)
        assert report["completed"] == 0 and not report["owned_objects"]
        assert all(not row["outputs"] for row in report["results"])
        assert "Line 1" in dialog.error_box.toPlainText() and dialog.error_box.isVisible()
        image = dialog.error_box.grab().toImage()
        colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 5)
                  for y in range(0, image.height(), 5)}
        assert "#fff0f1" in colors, "Error feedback does not use the shared background"
        reports.append(report)
    screenshot(dialog, output, "parser-error")
    configure(dialog, pairs / "{id}_A.pdb", pairs / "missing_{id}_B.pdb", root / "missing", paired=True)
    click(dialog.preview_button)
    wait_for(app, lambda: dialog.future is None)
    assert dialog.error_box.isVisible() and "Missing" in dialog.error_box.toPlainText()
    assert dialog.plan is None and not dialog.run_button.isEnabled() and not (root / "missing").exists()
    return reports


def check_annotation_entry(app, table, output):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    import pymolplugins.annotation.plugin as annotation
    from pymolplugins.targets import batch_widgets
    from pymolplugins.annotation.persistence import flush_writes
    annotation.STATE = annotation.AnnotatorState()
    annotation.STATE.preload_enabled = False
    loaded = annotation.sa_load_csv(str(table), path_column="Candidate file", id_column="Design ID")
    assert loaded is not None
    panel = annotation.show_structure_annotator_panel()
    app.processEvents()
    try:
        before = annotation.STATE.current_entry_id()
        action = next(action for action in panel.import_button.menu().actions()
                      if action.text() == "Batch processing...")
        menu, errors = panel.import_button.menu(), []
        def select_action():
            try:
                assert menu.isVisible() and action.isEnabled()
                QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu.actionGeometry(action).center())
            except Exception as exc:
                errors.append(exc)
                menu.close()
        option = QtWidgets.QStyleOptionToolButton()
        panel.import_button.initStyleOption(option)
        arrow = panel.import_button.style().subControlRect(
            QtWidgets.QStyle.CC_ToolButton, option, QtWidgets.QStyle.SC_ToolButtonMenu, panel.import_button)
        QtCore.QTimer.singleShot(80, select_action)
        QTest.mouseClick(panel.import_button, QtCore.Qt.LeftButton, pos=arrow.center())
        app.processEvents()
        if errors:
            raise errors[0]
        dialog = batch_widgets._DIALOG
        assert dialog is not None and dialog.isVisible()
        assert dialog.config().source == "csv" and dialog.config().a == str(table)
        assert dialog.config().a_column == "Candidate file" and dialog.config().id_column == "Design ID"
        assert annotation.STATE.current_entry_id() == before
        screenshot(dialog, output, "annotation-entry")
        screenshot(panel, output, "annotation-workspace")
        shared_style = check_style(dialog, panel)
        dialog.close()
        return shared_style
    finally:
        panel.close()
        annotation._reset_bg_cache()
        assert annotation._csv_async_pool().waitForDone(30000)
        flush_writes()
        annotation._delete_managed_objects(False)


def run(output):
    assert os.name == "nt" and not os.getcwd().startswith("\\\\")
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    from pymolplugins.core.windows_paths import windows_long_path
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-batch-") as temporary:
        root = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(root / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(root / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(root / "cache")
        isolate_workspace_settings(root, window)
        all_files, pairs, table = fixtures(root)
        sources = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in [*all_files.iterdir(), *pairs.iterdir(), table]}
        from pymolplugins.host.plugin import pp_load
        from pymolplugins.targets import batch_widgets
        assert pp_load()["status"] == "ok"
        cmd.load(str(all_files / "ref.pdb"), "user_structure")
        cmd.show("spheres", "user_structure")
        cmd.color("teal", "user_structure")
        cmd.zoom("user_structure")
        baseline = cmd.get_coords("user_structure").copy()
        cmd.undo_enable()
        undo_mode = cmd.undo_get_mode()
        application_font, application_style = app.font().toString(), app.styleSheet()
        dialog = batch_widgets.show_batch_dialog()
        app.processEvents()
        assert dialog.save_b.isChecked(), "Both A and B must be saved by default"
        try:
            shared_style = {"standalone": check_style(dialog)}
            configure(dialog, all_files, all_files / "ref.pdb", root / "against-ref")
            prepare(dialog, app, ["1", "2", "3"])
            shared_style["ready"] = check_style(dialog)
            screenshot(dialog, output, "ready")
            type_text(dialog.commands, RECIPE + "\n# changed draft")
            assert dialog.plan is None and not dialog.run_button.isEnabled()
            prepare(dialog, app, ["1", "2", "3"])
            loads, actual_load = [], cmd.load
            def checked_load(path, name, **kwargs):
                assert QtCore.QThread.currentThread() == app.thread()
                loads.append(str(path))
                return actual_load(path, name, **kwargs)
            with patch.object(cmd, "load", side_effect=checked_load):
                reference = execute(dialog, app)
            assert loads.count(str(all_files / "ref.pdb")) == 1
            error = output_error(reference, cmd)
            geometry = check_layout(dialog, app, output)
            configure(dialog, pairs / "{id}_A.pdb", pairs / "{id}_B.pdb", root / "paired", paired=True)
            prepare(dialog, app, ["1", "2", "10"])
            paired = execute(dialog, app)
            error = max(error, output_error(paired, cmd))
            configure(dialog, glob.escape(str(pairs)) + "/*_A.pdb", glob.escape(str(pairs)) + "/*_B.pdb",
                      root / "wildcard", paired=True, match="Wildcard")
            prepare(dialog, app, ["1", "2", "10"])
            wildcard = execute(dialog, app)
            error = max(error, output_error(wildcard, cmd))
            configure(dialog, pairs, pairs, root / "regex", paired=True, match="Regular expression",
                      a_pattern=r"(?P<id>\d+)_A\.pdb", b_pattern=r"(?P<id>\d+)_B\.pdb")
            prepare(dialog, app, ["1", "2", "10"])
            regex = execute(dialog, app)
            error = max(error, output_error(regex, cmd))
            tab(dialog, 0)
            dialog.tabs.widget(0).verticalScrollBar().setValue(0)
            screenshot(dialog, output, "regex-inputs")
            resize_workspace(dialog, 760, 560)
            app.processEvents()
            for editor in (dialog.match, dialog.a_pattern, dialog.b_pattern, dialog.a_edit, dialog.b_edit):
                scroll_to(dialog.tabs.widget(0), editor)
                visible(editor)
            dialog.tabs.widget(0).verticalScrollBar().setValue(0)
            screenshot(dialog, output, "regex-compact")
            resize_workspace(dialog, 1000, 720)
            configure(dialog, table, "Reference file", root / "csv-paired", paired=True, csv_mode=True)
            prepare(dialog, app, ["10", "1"])
            csv_report = execute(dialog, app)
            error = max(error, output_error(csv_report, cmd))
            screenshot(dialog, output, "csv-results")
            export_path = root / "export.json"
            with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(export_path), "JSON")):
                click(dialog.export_button)
            assert json.loads(export_path.read_text()) == csv_report
            configure(dialog, table, all_files / "ref.pdb", root / "csv-reference", csv_mode=True)
            prepare(dialog, app, ["10", "1"])
            csv_reference = execute(dialog, app)
            for pair_index, result in enumerate(csv_reference["results"]):
                for side, path in enumerate(result["outputs"]):
                    output.with_name(output.stem + "-csv-reference-%d-%d.pdb" % (pair_index, side)).write_bytes(Path(path).read_bytes())
            error = max(error, output_error(csv_reference, cmd))
            check_locations(dialog, app, output)
            many = root / "many structures"
            many.mkdir()
            for index in range(36):
                path = many / ("A%d.pdb" % index)
                write_structure(path, [(x + index, y - 8, z + 2) for x, y, z in POINTS])
                sources[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            configure(dialog, many, all_files / "ref.pdb", root / "many-output")
            prepare(dialog, app, [str(index) for index in range(1, 37)])
            many_report = execute(dialog, app)
            error = max(error, output_error(many_report, cmd))
            assert many_report["completed"] == 36 and many_report["failed"] == 0
            formats = root / "formats"
            formats.mkdir()
            cmd.save(str(formats / "A.cif"), "user_structure", state=0)
            for name, source in (("A.cif.gz", formats / "A.cif"), ("A.pdb.gz", all_files / "ref.pdb")):
                (formats / name).write_bytes(gzip.compress(source.read_bytes()))
            sources.update({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in formats.iterdir()})
            configure(dialog, formats, all_files / "ref.pdb", root / "format-output")
            prepare(dialog, app, ["1", "2", "3"])
            format_report = execute(dialog, app)
            error = max(error, output_error(format_report, cmd))
            long_source = root / ("structure_" + "long_filename_" * 12 + ".pdb")
            write_structure(long_source, POINTS)
            sources[long_source] = hashlib.sha256(long_source.read_bytes()).hexdigest()
            long_output = root / ("output_" + "x" * (235 - len(str(root)) - 8))
            assert len(str(long_output)) < 240
            configure(dialog, long_source, all_files / "ref.pdb", long_output)
            prepare(dialog, app, ["1"])
            long_report = execute(dialog, app)
            long_path_length = len(long_report["results"][0]["outputs"][0])
            assert long_path_length > 400
            error = max(error, output_error(long_report, cmd))
            cancelled = check_cancellation(dialog, app, root, all_files)
            failures = check_failures(dialog, app, root, all_files, pairs, output)
            assert not any(name.startswith("pp_batch_") for name in cmd.get_names("all"))
            assert (cmd.get_coords("user_structure") == baseline).all()
            undo_after_batch = cmd.undo_get_mode()
            dialog.close()
            shared_style["annotation_entry"] = check_annotation_entry(app, table, output)
            assert (app.font().toString(), app.styleSheet()) == (application_font, application_style)
            assert sources == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
            assert cmd.undo_get_mode() == undo_mode, dict(before=undo_mode, after_batch=undo_after_batch,
                                                         after_annotation=cmd.undo_get_mode())
            cmd.enable("user_structure")
            cmd.zoom("user_structure")
            QTest.qWait(200)
            viewport = window.pymolwidget.grabFrameBuffer()
            assert viewport.save(str(output.with_name(output.stem + "-viewport.png")))
            colors = {viewport.pixel(x, y) for x in range(0, viewport.width(), max(1, viewport.width() // 80))
                      for y in range(0, viewport.height(), max(1, viewport.height() // 80))}
            assert len(colors) > 8
            return dict(status="passed", pymol=cmd.get_version()[0], max_coordinate_error=error,
                        geometry=geometry, viewport_sample_colors=len(colors),
                        runs=[reference, paired, wildcard, regex, csv_report, csv_reference, many_report, format_report, long_report],
                        cancellation=cancelled, errors=failures, unchanged_source_files=len(sources),
                        preview_and_invalidation="passed", annotation_entry="passed", export="passed",
                        main_thread_loads="passed", reference_loaded_once_and_copied="passed",
                        native_undo_mode_unchanged="passed", compressed_and_cif_outputs="passed",
                        long_output_path_length=long_path_length, shared_style=shared_style,
                        application_style_unchanged="passed", file_locations_layout="passed")
        except Exception:
            screenshot(dialog, output, "failed")
            raise
        finally:
            if dialog.runner is not None and dialog.runner.running:
                dialog.stop()
                wait_for(app, lambda: not dialog.runner.running)
            dialog.close()
            shutil.rmtree(windows_long_path(str(root), threshold=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.output)
    except Exception:
        result = dict(status="failed", traceback=traceback.format_exc())
    result["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
