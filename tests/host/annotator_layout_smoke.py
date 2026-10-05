"""Exercise visible annotation controls and compact layouts in Windows PyMOL."""

import argparse
import csv
import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_workflow_smoke import fixture, wait_for
from annotator_review_smoke import click_row
from workspace_helpers import isolate_workspace_settings, resize_workspace


def visible(widget):
    from pymol.Qt import QtCore
    assert widget.isVisible(), f"Hidden control: {widget.objectName() or widget.accessibleName()}"
    assert widget.visibleRegion().boundingRect().contains(widget.rect()), "Control is clipped by an ancestor"
    bounds = QtCore.QRect(widget.mapTo(widget.window(), QtCore.QPoint()), widget.size())
    assert widget.window().rect().contains(bounds), f"Control outside window: {bounds}"
    return [bounds.x(), bounds.y(), bounds.width(), bounds.height()]


def combo_text_bounds(combo):
    from pymol.Qt import QtWidgets
    visible(combo)
    option = QtWidgets.QStyleOptionComboBox()
    combo.initStyleOption(option)
    contents = combo.style().subControlRect(QtWidgets.QStyle.CC_ComboBox, option,
                                           QtWidgets.QStyle.SC_ComboBoxEditField, combo)
    metrics = combo.fontMetrics()
    width = max(metrics.horizontalAdvance(combo.itemText(index)) for index in range(combo.count()))
    assert width <= contents.width(), f"Combo text is clipped: {combo.currentText()}, {width} > {contents.width()}"
    assert metrics.height() <= contents.height(), (
        f"Combo text height is clipped: {combo.currentText()}, {metrics.height()} > {contents.height()}")
    return {"text": [width, metrics.height()], "contents": [contents.width(), contents.height()]}


def click(widget):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    visible(widget)
    assert widget.isEnabled(), f"Disabled control: {widget.text()}"
    position = widget.rect().center()
    if isinstance(widget, QtWidgets.QCheckBox):
        option = QtWidgets.QStyleOptionButton()
        widget.initStyleOption(option)
        position = widget.style().subElementRect(QtWidgets.QStyle.SE_CheckBoxIndicator, option, widget).center()
    QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=position)
    QtWidgets.QApplication.processEvents()


def menu_action(button, action, submenu=None):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    menu, errors = button.menu(), []
    def choose():
        try:
            assert menu.isVisible() and action.isEnabled()
            target = menu
            if submenu is not None:
                rectangle = menu.actionGeometry(submenu.menuAction())
                assert menu.rect().contains(rectangle)
                QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=rectangle.center())
                QTest.qWait(100)
                assert submenu.isVisible(), "Clicking List columns did not open its submenu"
                target = submenu
            rectangle = target.actionGeometry(action)
            assert target.rect().contains(rectangle)
            QTest.mouseClick(target, QtCore.Qt.LeftButton, pos=rectangle.center())
        except Exception as exc:
            errors.append(exc)
            menu.close()
    QtCore.QTimer.singleShot(80, choose)
    click(button)
    QTest.qWait(100)
    if errors:
        raise errors[0]


def source_fixture(directory):
    path = fixture(directory, 100, 16)
    long_name = "candidate_" + "interface_quality_" * 6 + ".pdb"
    shutil.copyfile(directory / "model 0.pdb", directory / long_name)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    rows[1][0] = long_name
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path, long_name


def check_drafts(panel, annotation, app):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    form, store = panel.review.form, annotation.STATE.row_annotation_store
    first_id = annotation.STATE.current_entry_id()
    form.scroll.ensureWidgetVisible(panel._value_buttons[0])
    click(panel._value_buttons[0])
    assert store.snapshot() == {} and annotation.STATE.index == 0
    assert form.dirty == {"quality"} and not form.save_button.isEnabled()
    click(panel.settings_button)
    assert panel.main_tabs.currentIndex() == 2
    assert all(not shortcut.isEnabled() for shortcut in panel._qt_shortcuts.shortcuts)
    click(panel.back_button)
    click(panel.import_button)
    assert panel.main_tabs.currentIndex() == 1
    click(panel.back_button)
    assert form.values["quality"] == "Good" and form.dirty == {"quality"}
    click(panel.next_btn)
    wait_for(app, lambda: panel._pending_navigation is None)
    click(panel.prev_btn)
    wait_for(app, lambda: panel._pending_navigation is None)
    assert form.values["quality"] == "Good" and store.snapshot() == {}
    reason = form.editors["reason"]
    form.scroll.ensureWidgetVisible(reason)
    visible(reason)
    reason.setFocus()
    QTest.keyClick(reason, QtCore.Qt.Key_Down)
    assert form.can_save() and form.save_button.isEnabled()
    click(form.save_button)
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert store.get(first_id, "quality") == "Good"
    assert store.get(first_id, "reason") == "Interface"
    assert annotation.STATE.index == 1
    assert not panel.review.form.dirty and panel.review.form.hint_label.text() == ""


def check_batch(panel, annotation, app):
    from pymol import cmd
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    click_row(panel, 0, QTest, QtCore.Qt)
    wait_for(app, lambda: panel._pending_navigation is None)
    click_row(panel, 2, QTest, QtCore.Qt, QtCore.Qt.ShiftModifier)
    form, store = panel.review.form, annotation.STATE.row_annotation_store
    targets, before = panel.review.targets(), store.snapshot()
    assert len(targets) == 3 and form.save_button.text() == "Save 3 candidates"
    form.scroll.ensureWidgetVisible(panel._value_buttons[1])
    click(panel._value_buttons[1])
    reason = form.editors["reason"]
    form.scroll.ensureWidgetVisible(reason)
    reason.setFocus()
    QTest.keyClick(reason, QtCore.Qt.Key_End)
    assert store.snapshot() == before, "Choices were saved without submitting the batch"
    wait_for(app, lambda: not annotation._preload_queue and not annotation._materialization_futures)
    current = annotation.STATE.current_entry_id()
    with patch.object(cmd, "load", side_effect=AssertionError("Batch save loaded a structure")):
        click(form.save_button)
        wait_for(app, lambda: not panel._csv_task_id)
    assert annotation.STATE.current_entry_id() == current
    assert all(store.get(entry_id, "quality") == "Bad" for entry_id in targets)
    menu_action(panel.more_button, panel.undo_action)
    wait_for(app, lambda: not panel._csv_task_id)
    assert store.snapshot() == before


def check_filters(panel, annotation, app):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    click(panel.filter_button)
    assert panel.filter_dialog.isVisible()
    panel.filter_mode_combo.setFocus()
    QTest.keyClick(panel.filter_mode_combo, QtCore.Qt.Key_End)
    panel.filter_edit.setFocus()
    QTest.keyClicks(panel.filter_edit, "AC.iptm > 2")
    click(panel.apply_view_btn)
    wait_for(app, lambda: not panel._csv_task_id)
    assert not annotation.STATE.entries and panel.empty_view.isVisible()
    assert not panel.next_btn.isEnabled() and not panel.review.form.isEnabled()
    click(panel.filter_button)
    click(panel.clear_view_btn)
    wait_for(app, lambda: not panel._csv_task_id)
    assert len(annotation.STATE.entries) == 100 and not panel.empty_view.isVisible()


def select_scores(panel, app):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    panel.review.form.scroll.verticalScrollBar().setValue(0)
    picker = panel.column_picker
    click(picker)
    for name in ("AC.iptm", "score_0", "score_1", "score_2", "score_3", "score_4"):
        picker.search.setText(name)
        app.processEvents()
        item = next(picker.items.item(index) for index in range(picker.items.count())
                    if picker.items.item(index).text() == name)
        picker.items.scrollToItem(item)
        rectangle = picker.items.visualItemRect(item)
        assert not item.isHidden() and picker.items.viewport().rect().contains(rectangle)
        QTest.mouseClick(picker.items.viewport(), QtCore.Qt.LeftButton,
                         pos=rectangle.topLeft() + QtCore.QPoint(10, rectangle.height() // 2))
    click(picker.apply_button)
    wait_for(app, lambda: panel.review.scores.future is None and panel.review.scores.summary is not None)
    assert len(panel.score_footer.cards) == 6


def select_candidate(panel, app, row):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    click_row(panel, row, QTest, QtCore.Qt)
    wait_for(app, lambda: panel._pending_navigation is None)
    assert len(panel.review.targets()) == 1


def choose_reason(panel):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    editor = panel.review.form.editors["reason"]
    panel.review.form.scroll.ensureWidgetVisible(editor)
    visible(editor)
    editor.setFocus()
    QTest.keyClick(editor, QtCore.Qt.Key_Home)
    QTest.keyClick(editor, QtCore.Qt.Key_Down)


def click_label(panel, index=0, shift=False):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    button = panel._value_buttons[index]
    panel.review.form.scroll.ensureWidgetVisible(button)
    visible(button)
    QTest.mouseClick(button, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier if shift else QtCore.Qt.NoModifier)
    QtWidgets.QApplication.processEvents()


def annotation_idle(panel, app):
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)


def check_click_save_modes(panel, annotation, app, output):
    from PyQt5.QtTest import QTest
    store = annotation.STATE.row_annotation_store
    assert not panel.save_on_label_click_cb.isChecked()
    menu_action(panel.more_button, panel.save_on_label_click_action)
    assert panel.save_on_label_click_cb.isChecked() and panel.auto_advance_cb.isChecked()
    select_candidate(panel, app, 10)
    before, entry_id = store.snapshot(), annotation.STATE.current_entry_id()
    click_label(panel)
    assert not panel._csv_task_id and annotation.STATE.index == 10
    assert store.snapshot() == before and "reason" in panel.review.form.hint_label.text()
    choose_reason(panel)
    assert store.snapshot() == before, "Completing a secondary field auto-saved"
    with patch.object(annotation, "save_annotations", wraps=annotation.save_annotations) as saves:
        click_label(panel)
        annotation_idle(panel, app)
        assert saves.call_count == 1
    assert store.get(entry_id, "quality") == "Good" and store.get(entry_id, "reason") == "Interface"
    assert annotation.STATE.index == 11
    menu_action(panel.more_button, panel.auto_advance_action)
    assert not panel.auto_advance_cb.isChecked() and panel.review.form.save_button.text() == "Save"
    choose_reason(panel)
    entry_id = annotation.STATE.current_entry_id()
    click_label(panel, 1)
    annotation_idle(panel, app)
    assert annotation.STATE.index == 11 and store.get(entry_id, "quality") == "Bad"
    click(panel.settings_button)
    click(panel.auto_advance_cb)
    assert panel.auto_advance_action.isChecked()
    QTest.qWait(180)
    assert panel.grab().save(str(output.with_name(output.stem + "-save-options.png")))
    click(panel.back_button)
    before = store.snapshot()
    click_label(panel, shift=True)
    assert store.snapshot() == before and annotation.STATE.index == 11 and not panel._csv_task_id
    click(panel.review.form.save_button)
    annotation_idle(panel, app)
    assert annotation.STATE.index == 12 and store.get(entry_id, "quality") == "Good"


def check_click_save_batch_and_shortcut(panel, annotation, app):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    store = annotation.STATE.row_annotation_store
    select_candidate(panel, app, 14)
    click_row(panel, 16, QTest, QtCore.Qt, QtCore.Qt.ShiftModifier)
    targets, before, current = panel.review.targets(), store.snapshot(), annotation.STATE.current_entry_id()
    assert len(targets) == 3
    choose_reason(panel)
    click_label(panel)
    assert store.snapshot() == before and not panel._csv_task_id and panel.review.form.can_save()
    click(panel.review.form.save_button)
    annotation_idle(panel, app)
    assert annotation.STATE.current_entry_id() == current
    assert all(store.get(entry_id, "quality") == "Good" for entry_id in targets)
    click(panel.settings_button)
    click(panel.save_on_label_click_cb)
    assert not panel.save_on_label_click_action.isChecked()
    click(panel.back_button)
    select_candidate(panel, app, 20)
    choose_reason(panel)
    before = store.snapshot()
    click_label(panel)
    assert store.snapshot() == before and annotation.STATE.index == 20 and not panel._csv_task_id
    menu_action(panel.more_button, panel.save_on_label_click_action)
    assert store.snapshot() == before, "Enabling click-to-save committed an existing draft"
    entry_id = annotation.STATE.current_entry_id()
    with patch.object(annotation, "save_annotations", wraps=annotation.save_annotations) as saves:
        QTest.keyClick(panel, QtCore.Qt.Key_1, QtCore.Qt.AltModifier)
        annotation_idle(panel, app)
        assert saves.call_count == 1
    assert store.get(entry_id, "quality") == "Good" and annotation.STATE.index == 21


def check_click_save_failure(panel, annotation, app):
    store = annotation.STATE.row_annotation_store
    choose_reason(panel)
    entry_id, index = annotation.STATE.current_entry_id(), annotation.STATE.index
    before, undo_count = store.snapshot(), len(annotation.STATE.undo_history)
    with patch.object(annotation, "save_annotations", side_effect=OSError("synthetic click-save failure")):
        click_label(panel)
        annotation_idle(panel, app)
    assert store.snapshot() == before and annotation.STATE.index == index
    assert len(annotation.STATE.undo_history) == undo_count
    assert panel.review.form.dirty and panel.review.form.values["quality"] == "Good"
    assert panel.error_label.isVisible() and "synthetic click-save failure" in panel.error_label.text()
    click_label(panel)
    annotation_idle(panel, app)
    assert store.get(entry_id, "quality") == "Good" and annotation.STATE.index == index + 1
    assert not panel.error_label.isVisible(), panel.error_label.text()


def configure_primary_choices(panel, app, kind="single"):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pymolplugins.annotation.review_widgets import AnnotationFieldsDialog
    errors = []
    def add_choices():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, AnnotationFieldsDialog)
            dialog.table.item(0, 3).setText("Good;Bad;Maybe;Review;Skip")
            field_type = dialog.table.cellWidget(0, 1)
            field_type.setCurrentIndex(field_type.findData(kind))
            buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
            click(buttons.button(QtWidgets.QDialogButtonBox.Ok))
            assert dialog.result() == QtWidgets.QDialog.Accepted
        except Exception as exc:
            errors.append(exc)
            if dialog is not None:
                dialog.reject()
    action = next(action for action in panel.more_button.menu().actions() if action.text().startswith("Annotation fields"))
    QtCore.QTimer.singleShot(180, add_choices)
    menu_action(panel.more_button, action)
    if errors:
        raise errors[0]


def check_click_save_dropdown(panel, annotation, app):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    configure_primary_choices(panel, app)
    select_candidate(panel, app, 25)
    before = annotation.STATE.row_annotation_store.snapshot()
    with patch.object(annotation, "save_annotations", wraps=annotation.save_annotations) as saves:
        panel._sync_from_state()
        panel._refresh_status()
        app.processEvents()
        assert saves.call_count == 0 and annotation.STATE.row_annotation_store.snapshot() == before
        choose_reason(panel)
        editor = panel.review.form.editors["quality"]
        assert isinstance(editor, QtWidgets.QComboBox)
        panel.review.form.scroll.ensureWidgetVisible(editor)
        visible(editor)
        editor.setFocus()
        entry_id = annotation.STATE.current_entry_id()
        QTest.keyClick(editor, QtCore.Qt.Key_End)
        annotation_idle(panel, app)
        assert saves.call_count == 1
    assert annotation.STATE.row_annotation_store.get(entry_id, "quality") == "Skip"
    assert annotation.STATE.index == 26


def check_multiple_label_shortcuts(panel, annotation, app):
    from pymol.Qt import QtCore
    from PyQt5.QtTest import QTest
    configure_primary_choices(panel, app, "multiple")
    choose_reason(panel)
    panel.activateWindow()
    panel.raise_()
    wait_for(app, lambda: app.activeWindow() is panel.window() and app.activeModalWidget() is None
             and app.activePopupWidget() is None)
    store = annotation.STATE.row_annotation_store
    before, current = store.snapshot(), annotation.STATE.current_entry_id()
    with patch.object(panel, "_annotate_digit", wraps=panel._annotate_digit) as shortcuts:
        for digit in (QtCore.Qt.Key_1, QtCore.Qt.Key_2):
            QTest.keyClick(panel, digit, QtCore.Qt.AltModifier)
        assert shortcuts.call_count == 2, dict(calls=shortcuts.call_count, active=app.activeWindow(),
                                              popup=app.activePopupWidget(), focus=app.focusWidget())
    assert not panel._csv_task_id and annotation.STATE.current_entry_id() == current
    assert store.snapshot() == before and panel.review.form.values["quality"] == ["Good", "Bad"]
    click(panel.review.form.save_button)
    annotation_idle(panel, app)
    assert json.loads(store.get(current, "quality")) == ["Good", "Bad"]
    assert annotation.STATE.index == 27


def check_source_columns_and_details(panel, annotation, app, output):
    from pymol import cmd
    from pymol.Qt import QtWidgets
    current = annotation.STATE.current_entry_id()
    ids = tuple(entry.id for entry in annotation.STATE.entries)
    wait_for(app, lambda: not annotation._preload_queue and not annotation._materialization_futures)
    with patch.object(cmd, "load", side_effect=AssertionError("Column toggle loaded a structure")), \
            patch.object(annotation, "_table_view_entries", side_effect=AssertionError("Column toggle rebuilt the view")):
        for action in (panel.show_source_row_action, panel.show_file_status_action):
            assert not action.isChecked()
            menu_action(panel.more_button, action, panel.list_columns_menu)
        assert not panel.dataset_table.isColumnHidden(0) and not panel.dataset_table.isColumnHidden(1)
        for action in (panel.show_source_row_action, panel.show_file_status_action):
            menu_action(panel.more_button, action, panel.list_columns_menu)
        assert panel.dataset_table.isColumnHidden(0) and panel.dataset_table.isColumnHidden(1)
        for action in (panel.show_source_row_action, panel.show_file_status_action):
            menu_action(panel.more_button, action, panel.list_columns_menu)
    assert tuple(entry.id for entry in annotation.STATE.entries) == ids
    assert annotation.STATE.current_entry_id() == current
    panel.dataset_table.horizontalScrollBar().setValue(0)
    header = panel.dataset_table.horizontalHeader()
    assert panel.dataset_table.columnWidth(0) >= header.fontMetrics().horizontalAdvance("Source row") + 24
    assert panel.grab().save(str(output.with_name(output.stem + "-source-columns.png")))
    store = annotation.STATE.row_annotation_store
    store.set(current, "legacy_review", "Kept from an earlier label configuration")
    action = next(action for action in panel.more_button.menu().actions() if action.text().startswith("Structure details"))
    for _ in range(2):
        menu_action(panel.more_button, action)
        assert panel.details_dialog.isVisible()
        table = panel.metadata_table
        values = {table.item(row, 0).text(): table.item(row, 1).text() for row in range(table.rowCount())}
        assert values["Annotation: quality"] == "Good"
        assert values["Annotation: legacy_review"] == "Kept from an earlier label configuration"
        assert values["raw_path"] == annotation.STATE.current_entry().source_path
        for row in range(table.rowCount()):
            assert table.item(row, 1).toolTip() == table.item(row, 1).text()
        table.scrollToItem(table.item(table.rowCount() - 1, 1))
        app.processEvents()
        assert panel.details_dialog.grab().save(str(output.with_name(output.stem + "-details.png")))
        close = next(button for button in panel.details_dialog.findChildren(QtWidgets.QPushButton) if button.text() == "Close")
        click(close)
        assert not panel.details_cb.isChecked()


def check_legacy_navigation(panel, annotation, app):
    from pymol.Qt import QtCore, QtWidgets
    errors = []
    def choose_position():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, QtWidgets.QInputDialog)
            dialog.setIntValue(31)
            buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
            click(buttons.button(QtWidgets.QDialogButtonBox.Ok))
        except Exception as exc:
            errors.append(exc)
            if dialog is not None:
                dialog.reject()
    action = next(action for action in panel.more_button.menu().actions() if action.text().startswith("Go to candidate"))
    QtCore.QTimer.singleShot(180, choose_position)
    menu_action(panel.more_button, action)
    if errors:
        raise errors[0]
    annotation_idle(panel, app)
    assert annotation.STATE.index == 30
    menu_action(panel.more_button, panel.next_unann_action)
    annotation_idle(panel, app)
    assert annotation.STATE.index == 31 and 31 not in annotation.STATE.annotated_indices
    action = next(action for action in panel.more_button.menu().actions() if action.text() == "Reload structure")
    menu_action(panel.more_button, action)
    assert annotation.STATE.index == 31 and annotation._verify_current_object_enabled()


def check_layout(panel, annotation, app, window, output, long_name):
    from pymol import cmd
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    panel._request_navigation(0)
    wait_for(app, lambda: panel._pending_navigation is None)
    select_scores(panel, app)
    geometry = {}
    for label, size in (("normal", (1000, 720)), ("small", (760, 560))):
        resize_workspace(panel, *size)
        QTest.qWait(80)
        assert (panel.width(), panel.height()) == size, "Child size hints enlarged the window"
        assert panel.current_title_label.text() == long_name
        assert long_name in panel.current_title_label.toolTip()
        assert isinstance(panel.main_tabs, QtWidgets.QStackedWidget)
        primary = [button for button in panel.findChildren(QtWidgets.QPushButton)
                   if button.property("role") == "primary" and button.isVisible()]
        assert primary == [panel.review.form.save_button]
        button = primary[0]
        option = QtWidgets.QStyleOptionButton()
        button.initStyleOption(option)
        contents = button.style().subElementRect(QtWidgets.QStyle.SE_PushButtonContents, option, button)
        assert button.fontMetrics().horizontalAdvance(button.text()) <= contents.width(), "Save text is clipped"
        geometry[label] = {"size": size, "save": visible(button), "title": visible(panel.current_title_label)}
        panel.review.form.scroll.verticalScrollBar().setValue(0)
        app.processEvents()
        assert panel.grab().save(str(output.with_name(output.stem + "-" + label + ".png")))
    resize_workspace(panel, 1000, 720)
    cmd.bg_color("white")
    cmd.show("spheres", annotation.STATE.current_object)
    cmd.color("teal", annotation.STATE.current_object)
    cmd.set("sphere_scale", 0.45, annotation.STATE.current_object)
    cmd.zoom(annotation.STATE.current_object)
    QTest.qWait(250)
    viewport = window.pymolwidget.grabFrameBuffer()
    assert viewport.save(str(output.with_name(output.stem + "-viewport.png")))
    colors = {viewport.pixel(x, y) for x in range(0, viewport.width(), max(1, viewport.width() // 80))
              for y in range(0, viewport.height(), max(1, viewport.height() // 80))}
    assert len(colors) > 8, "PyMOL viewport is blank"
    geometry["viewport_sample_colors"] = len(colors)
    return geometry


def check_config_dialogs(panel, app, output):
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pymolplugins.annotation.review_widgets import AnnotationFieldsDialog, ScoreGuidanceDialog
    results = {}
    for name, title, kind, combo_column, sizes in (
            ("fields", "Annotation fields", AnnotationFieldsDialog, 1, ((680, 390), (560, 360))),
            ("scores", "Score rules", ScoreGuidanceDialog, 2, ((920, 420), (640, 360)))):
        errors = []
        def inspect():
            dialog = app.activeModalWidget()
            try:
                assert isinstance(dialog, kind)
                geometry = {}
                for label, size in zip(("normal", "small"), sizes):
                    dialog.resize(*size)
                    QTest.qWait(80)
                    assert (dialog.width(), dialog.height()) == size
                    for column in range(dialog.table.columnCount()):
                        title = dialog.table.horizontalHeaderItem(column).text()
                        width = dialog.table.horizontalHeader().fontMetrics().horizontalAdvance(title)
                        assert width + 14 <= dialog.table.columnWidth(column), f"Header is clipped: {title}"
                    controls = []
                    for row in range(dialog.table.rowCount()):
                        dialog.table.scrollTo(dialog.table.model().index(row, combo_column))
                        app.processEvents()
                        controls.append(combo_text_bounds(dialog.table.cellWidget(row, combo_column)))
                    buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
                    geometry[label] = {"size": size, "combos": controls,
                                       "save": visible(buttons.button(QtWidgets.QDialogButtonBox.Ok))}
                    dialog.table.scrollToTop()
                    dialog.table.horizontalScrollBar().setValue(0)
                    app.processEvents()
                    assert dialog.grab().save(str(output.with_name(output.stem + "-" + name + "-" + label + ".png")))
                click(buttons.button(QtWidgets.QDialogButtonBox.Ok))
                assert dialog.result() == QtWidgets.QDialog.Accepted, dialog.error.text()
                results[name] = geometry
            except Exception as exc:
                errors.append(exc)
                if dialog is not None:
                    dialog.grab().save(str(output.with_name(output.stem + "-" + name + "-failed.png")))
                    dialog.reject()
        action = next(action for action in panel.more_button.menu().actions() if action.text().startswith(title))
        QtCore.QTimer.singleShot(180, inspect)
        menu_action(panel.more_button, action)
        if errors:
            raise errors[0]
        assert name in results
    return results


def exercise(panel, annotation, app, window, path, long_name, output):
    from PyQt5.QtTest import QTest
    assert panel.main_tabs.currentIndex() == 1 and not panel.load_btn.isEnabled()
    assert panel.directory_mode_button.isChecked() and not panel.csv_group.isVisible()
    assert panel.grab().save(str(output.with_name(output.stem + "-open.png")))
    click(panel.csv_mode_button)
    panel.csv_edit.setFocus()
    QTest.keyClicks(panel.csv_edit, str(path))
    click(panel.csv_load_btn)
    wait_for(app, lambda: not panel._csv_task_id and bool(annotation.STATE.entries))
    assert panel.main_tabs.currentIndex() == 0 and not panel.status_label.isVisible()
    check_drafts(panel, annotation, app)
    check_batch(panel, annotation, app)
    check_filters(panel, annotation, app)
    geometry = check_layout(panel, annotation, app, window, output, long_name)
    config_dialogs = check_config_dialogs(panel, app, output)
    check_source_columns_and_details(panel, annotation, app, output)
    check_legacy_navigation(panel, annotation, app)
    check_click_save_modes(panel, annotation, app, output)
    check_click_save_batch_and_shortcut(panel, annotation, app)
    check_click_save_failure(panel, annotation, app)
    check_click_save_dropdown(panel, annotation, app)
    check_multiple_label_shortcuts(panel, annotation, app)
    click(panel.import_button)
    click(panel.mapping_section.toggle)
    panel.main_tabs.currentWidget().ensureWidgetVisible(panel.mapping_edit_btn)
    visible(panel.mapping_edit_btn)
    visible(panel.target_format_combo)
    assert panel.grab().save(str(output.with_name(output.stem + "-locations.png")))
    click(panel.back_button)
    return {"status": "passed", "visible_import": "passed", "drafts_required_fields_save_next": "passed",
            "settings_import_navigation_preserve_draft": "passed", "batch_save_menu_undo": "passed",
            "filter_empty_reset": "passed", "mapping_controls": "passed", "geometry": geometry,
            "config_dialogs": config_dialogs,
            "click_save_required_and_advance": "passed", "click_save_stay_and_shift": "passed",
            "click_save_batch_stays_explicit": "passed", "click_save_disable_returns_to_manual": "passed",
            "click_save_shortcut_exactly_once": "passed", "click_save_failure_preserves_draft_and_retry": "passed",
            "click_save_dropdown_and_programmatic_restore": "passed",
            "multiple_label_shortcuts_stage_until_save": "passed",
            "source_columns_no_reload_or_reorder": "passed", "all_saved_annotations_details_reopen": "passed",
            "visible_jump_next_unreviewed_reload": "passed"}


def run(output):
    assert os.name == "nt" and not os.getcwd().startswith("\\\\"), "Use Windows PyMOL from a local directory"
    sys.path.insert(0, str(ROOT))
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtWidgets
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.show()
    app.processEvents()
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-layout-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        isolate_workspace_settings(directory, window)
        path, long_name = source_fixture(directory)
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        annotation.STATE = annotation.AnnotatorState()
        annotation.STATE.auto_advance = True
        annotation.STATE.annotation_fields = [
            {"name": "quality", "type": "single", "required": True, "values": ["Good", "Bad"]},
            {"name": "reason", "type": "single", "required": True, "values": ["Interface", "Fold"]},
            {"name": "notes", "type": "text", "required": False, "values": []},
        ]
        panel = annotation.show_structure_annotator_panel()
        panel.activateWindow()
        panel.raise_()
        app.processEvents()
        try:
            result = exercise(panel, annotation, app, window, path, long_name, output)
            from pymolplugins.annotation.history_store import latest_config_snapshot
            from pymolplugins.annotation.state_store import read_dataset_state
            panel.close()
            flush_writes()
            config = read_dataset_state(str(path), source_mode="csv")["config"]
            assert config["save_on_label_click"] is True and config["auto_advance"] is True
            for name in ("save_on_label_click", "show_source_row", "show_file_status"):
                assert config[name] is True and latest_config_snapshot()["config"][name] is True
            before, index = annotation.STATE.row_annotation_store.snapshot(), annotation.STATE.index
            panel = annotation.show_structure_annotator_panel()
            app.processEvents()
            assert panel.save_on_label_click_cb.isChecked() and panel.save_on_label_click_action.isChecked()
            assert panel.auto_advance_cb.isChecked() and panel.auto_advance_action.isChecked()
            assert panel.show_source_row_action.isChecked() and not panel.dataset_table.isColumnHidden(0)
            assert panel.show_file_status_action.isChecked() and not panel.dataset_table.isColumnHidden(1)
            assert annotation.STATE.row_annotation_store.snapshot() == before and annotation.STATE.index == index
            result["click_save_preferences_history_dataset_reopen"] = "passed"
            result["pymol"] = cmd.get_version()[0]
            return result
        except Exception:
            panel.grab().save(str(output.with_name(output.stem + "-failed.png")))
            raise
        finally:
            if panel._csv_task_kind in {"annotations", "annotation_undo"}:
                wait_for(app, lambda: not panel._csv_task_id)
            panel.close()
            futures = list(annotation._materialization_futures.values())
            annotation._reset_bg_cache()
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=30)
            assert annotation._csv_async_pool().waitForDone(30000)
            flush_writes()
            annotation._delete_managed_objects(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.output)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    result["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = json.dumps(result, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text, file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
