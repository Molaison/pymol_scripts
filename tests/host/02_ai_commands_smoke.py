"""Real test/gpt-6-astra -> Windows PyMOL GUI -> editable triggers -> execution.

Run: just test-ai-commands
Use --stage presets with --preset-inputs to continue only the viewing checks.
Uses two real structures identified by the existing 600-row acceptance JSON;
does NOT repeat that dataset test. Source files remain read-only. Outputs beside
--output: native JSON, provider text/usage and request (no credentials), screenshots,
saved session and annotation state. Bad documents are explicitly injected at the
response boundary; network failure uses the real transport via a refused proxy.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotator_workflow_smoke import wait_for
from annotator_source_review_smoke import open_table
from workspace_helpers import isolate_workspace_settings, resize_workspace


def run(args, result):
    global _APPLICATION
    from pymol import cmd, gui, invocation
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI

    assert os.name == "nt"
    evidence = json.loads(args.source_evidence.read_text(encoding="utf-8"))
    sources = [Path(evidence["reference"][key]) for key in ("candidate", "reference")]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _APPLICATION = app
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.resize(1400, 900)
    window.show()
    app.processEvents()
    result.update(platform=sys.platform, pymol=cmd.get_version()[0], sources=hashes)
    with tempfile.TemporaryDirectory(prefix="PymolPlugins-ai-") as temporary:
        directory = Path(temporary)
        os.environ.update(PYMOLPLUGINS_ANNOTATOR_HISTORY=str(directory / "history.json"),
                          PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(directory / "mappings.json"),
                          PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(directory / "cache"))
        isolate_workspace_settings(directory, window, mode="floating")
        if args.package:
            # Reuse the real Plugin Manager acceptance; only installer notices are
            # captured. No cmd, Qt, network or generated command is mocked.
            from types import SimpleNamespace
            from unittest.mock import patch
            from pymol import plugins
            from pymol.plugins import legacysupport
            from pmg_qt.mimic_pmg_tk import PmwMenuBar
            from plugin_package_smoke import _check_install, _unexpected_prompt
            menu = QtWidgets.QMenu("Plugin", window)
            pg = SimpleNamespace(menuBar=PmwMenuBar({"PluginQt": menu}))
            notices = []
            messagebox = SimpleNamespace(showinfo=lambda *a, **kw: notices.append(a),
                                        askyesno=_unexpected_prompt, askokcancel=_unexpected_prompt)
            plugins.preferences["instantsave"] = False
            plugins.set_startup_path([str(directory)], autosave=False)
            # The existing installation acceptance checks initially docked hosts.
            isolate_workspace_settings(directory, window, mode="docked")
            with patch.object(plugins, "get_pmgapp", return_value=pg), \
                    patch.object(plugins, "HAVE_QT", True), \
                    patch.object(legacysupport, "tkMessageBox", messagebox):
                result["installation"] = _check_install(args.package, directory, window, menu)
            result["installation"]["package_sha256"] = hashlib.sha256(args.package.read_bytes()).hexdigest()
            result["installation"]["package_bytes"] = args.package.stat().st_size
            result["installation"]["notices"] = notices
            isolate_workspace_settings(directory, window, mode="floating")
        from pymolplugins.annotation import ai_widgets
        from pymolplugins.annotation.ai_commands import SCHEMA
        from pymolplugins.host.workspace import window_settings
        result["ai_module"] = ai_widgets.__file__
        if args.stage == "all":
            # Reuse the authorized provider without printing its credential or changing
            # Codex. The selected Custom API is exercised by native Windows Python.
            helper_path = Path(ai_widgets.__file__).with_name("ai_provider.py")
            linux_helper = subprocess.run(["wsl.exe", "--distribution", "Debian", "--exec", "wslpath", "-u", str(helper_path)],
                                          capture_output=True, text=True, check=True).stdout.strip()
            config_read = subprocess.run(["wsl.exe", "--distribution", "Debian", "--exec", "python3", "-c",
                "import json,runpy,sys; m=runpy.run_path(sys.argv[1]); print(json.dumps(m['_codex_connection'](True)))", linux_helper],
                capture_output=True, text=True, check=True)
            live_connection = json.loads(config_read.stdout)
            result["configured_provider"] = {key: live_connection[key] for key in ("provider", "model", "api")}
        table = directory / "review.csv"
        with table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["candidate", "reference", "id"])
            for i, source in enumerate(sources):
                writer.writerow([source, sources[1-i], "ai-review-" + str(i)])
        (directory / "review.json").write_text(json.dumps({
            "path_column": "candidate", "reference_column": "reference",
            "step_cmd": "zoom {object}"}), encoding="utf-8")
        from pymolplugins.host.plugin import pp_load
        import pymolplugins.annotation.plugin as annotation
        from pymolplugins.annotation.persistence import flush_writes
        assert pp_load()["status"] == "ok"
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        panel = annotation.show_structure_annotator_panel()
        resize_workspace(panel, 950, 720)
        open_table(panel, table, app, QTest, QtCore.Qt)
        assert len(annotation.STATE.entries) == 2
        if args.stage == "all":
            QTest.mouseClick(panel.reference_button, QtCore.Qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            reference = panel._reference_object
            assert reference and cmd.count_atoms(reference) > 100
            candidate = annotation.STATE.current_object
            baseline = cmd.get_coordset(candidate).copy()
            before_colors = []
            cmd.iterate(candidate, "before_colors.append(color)", space={"before_colors": before_colors})
            old_step = annotation.STATE.step_cmd
            old_editors = {k: v.toPlainText() for k, v in ai_widgets.command_editors(panel).items()}
            label = next(iter(panel._bucket_inputs))
            target_step = "after_label/" + label
            result["target_step"] = target_step
            beats = []
            timer = QtCore.QTimer()
            timer.timeout.connect(lambda: beats.append(time.perf_counter()))
            timer.start(20)
            errors = []

            def interact():
                dialog = panel.ai_dialog
                try:
                    settings_errors = []
                    def configure_api():
                        settings = dialog.connection_dialog
                        try:
                            settings.mode.setCurrentIndex(settings.mode.findData("custom"))
                            settings.base_url.setText(live_connection["base_url"])
                            settings.api.setCurrentIndex(settings.api.findData(live_connection["api"]))
                            settings.model.setCurrentText(live_connection["model"])
                            settings.key.setText("temporary-ui-test-key")
                            settings.remember.setChecked(True)
                            settings.save()
                            assert settings.saved, settings.error.text()
                        except Exception:
                            settings_errors.append(traceback.format_exc())
                            settings.reject()
                    QtCore.QTimer.singleShot(100, configure_api)
                    QTest.mouseClick(dialog.connection_button, QtCore.Qt.LeftButton)
                    assert not settings_errors, settings_errors
                    ai_widgets._SESSION_API_KEY = ""
                    assert ai_widgets.connection_settings()["api_key"] == "temporary-ui-test-key"
                    settings_path = Path(window_settings().fileName())
                    assert b"temporary-ui-test-key" not in settings_path.read_bytes()
                    result["windows_encrypted_key_roundtrip"] = True
                    def use_live_api():
                        settings = dialog.connection_dialog
                        try:
                            assert settings.key.text() == "temporary-ui-test-key"
                            assert settings.base_url.text() == live_connection["base_url"]
                            settings.key.setText(live_connection["api_key"])
                            settings.remember.setChecked(False)
                            QTest.mouseClick(settings.models_button, QtCore.Qt.LeftButton)
                            wait_for(app, lambda: settings.process is None, timeout=130)
                            result["model_list"] = {"count": settings.model.count(), "error": settings.error.text()}
                            settings.model.setCurrentText(live_connection["model"])
                            settings.grab().save(str(args.output.with_name(args.output.stem + "-connection.png")))
                            settings.save()
                            assert settings.saved, settings.error.text()
                        except Exception:
                            settings_errors.append(traceback.format_exc())
                            settings.reject()
                    QtCore.QTimer.singleShot(100, use_live_api)
                    QTest.mouseClick(dialog.connection_button, QtCore.Qt.LeftButton)
                    assert not settings_errors, settings_errors
                    assert not window_settings().value("ai_commands/protected_key", "")
                    assert live_connection["api_key"] not in settings_path.read_text()
                    result["custom_api_settings_reopen"] = True
                    for i in range(dialog.triggers.count()):
                        item = dialog.triggers.item(i)
                        item.setCheckState(QtCore.Qt.Checked if item.data(QtCore.Qt.UserRole) in
                                           {"on_open", target_step} else QtCore.Qt.Unchecked)
                    dialog.request_edit.setPlainText(
                        "On open, in this order: hide everything on {object}, show cartoon on {object}, "
                        "color {selection} cyan, set cartoon_transparency to 0.2 on {object}, then zoom {object} with buffer 2. "
                        "After saving label " + label + ", color {object} yellow then show sticks on {selection}. "
                        "Keep all recipes reusable for the next structure. Do not modify reference object " + reference + ".")
                    start = time.perf_counter()
                    QTest.mouseClick(dialog.generate_button, QtCore.Qt.LeftButton)
                    wait_for(app, lambda: dialog.process is None, timeout=145)
                    result["request_seconds"] = time.perf_counter() - start
                    result["provider_status"] = dialog.status.text()
                    assert dialog.response is not None, dialog.status.text()
                    assert dialog.response["provider"] == "custom" and dialog.response["model"] == live_connection["model"]
                    assert "api_key" not in dialog.request_payload
                    args.output.with_name(args.output.stem + "-provider.json").write_text(
                        json.dumps(dialog.response, ensure_ascii=False, indent=2), encoding="utf-8")
                    args.output.with_name(args.output.stem + "-request.json").write_text(
                        json.dumps(dialog.request_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    assert set(dialog.preview_editors) == {"on_open", target_step}
                    assert all(len(edit.toPlainText().splitlines()) >= 2 for edit in dialog.preview_editors.values())
                    assert annotation.STATE.step_cmd == old_step
                    assert {k:v.toPlainText() for k,v in ai_widgets.command_editors(panel).items()} == old_editors
                    colors = []
                    cmd.iterate(candidate, "colors.append(color)", space={"colors": colors})
                    assert colors == before_colors
                    dialog.resize(560, 520)
                    app.processEvents()
                    assert dialog.rect().contains(dialog.insert_button.geometry().bottomRight())
                    assert dialog.insert_button.visibleRegion().isEmpty() is False
                    dialog.grab().save(str(args.output.with_name(args.output.stem + "-draft.png")))
                    result["generation_no_execution"] = True
                    drafts_before = {key: edit.toPlainText() for key, edit in dialog.preview_editors.items()}
                    dialog.receive_response({"text": json.dumps({"schema": SCHEMA, "steps": [
                        {"step_id": key, "commands": [], "note": "Requested operation is not available."}
                        for key in dialog.request_steps]})})
                    assert "Requested operation is not available" in dialog.status.text()
                    assert drafts_before == {key: edit.toPlainText() for key, edit in dialog.preview_editors.items()}
                    assert dialog.insert_button.isEnabled()
                    result["failed_retry_preserves_draft"] = True
                    # Real editable preview; the next stage also edits the actual command box.
                    edit = dialog.preview_editors["on_open"]
                    edit.setPlainText(edit.toPlainText() + "\n\n# User note\ncenter {selection}\n")

                    def reject_replace():
                        box = app.activeModalWidget()
                        QTest.mouseClick(box.button(QtWidgets.QMessageBox.No), QtCore.Qt.LeftButton)
                    QtCore.QTimer.singleShot(100, reject_replace)
                    QTest.mouseClick(dialog.insert_button, QtCore.Qt.LeftButton)
                    assert {k:v.toPlainText() for k,v in ai_widgets.command_editors(panel).items()} == old_editors
                    result["declined_overwrite_preserves_all"] = True
                    def accept_replace():
                        box = app.activeModalWidget()
                        QTest.mouseClick(box.button(QtWidgets.QMessageBox.Yes), QtCore.Qt.LeftButton)
                    QtCore.QTimer.singleShot(100, accept_replace)
                    QTest.mouseClick(dialog.insert_button, QtCore.Qt.LeftButton)
                except Exception:
                    errors.append(traceback.format_exc())
                    dialog.reject()
            QtCore.QTimer.singleShot(150, interact)
            # Use the actual page entry (button click enters the real modal event loop).
            panel.main_tabs.setCurrentIndex(2)
            from pymolplugins.annotation.qt_widgets import CollapsibleSection
            section = next(s for s in panel.findChildren(CollapsibleSection) if s.toggle.text() == "Automation")
            section.toggle.setChecked(True)
            app.processEvents()
            QTest.mouseClick(panel.ai_commands_button, QtCore.Qt.LeftButton)
            assert not errors, "\n".join(errors)
            timer.stop()
            result["gui_heartbeat_ticks"] = len(beats)
            assert len(beats) > 5
            assert annotation.STATE.step_cmd == old_step
            generated = panel.step_cmd_edit.toPlainText()
            assert "{object}" in generated and "{selection}" in generated
            panel.step_cmd_edit.setPlainText(generated + "\norient {object}")
            assert panel._bucket_inputs[label].toPlainText().count("\n") >= 1
            panel.commands_apply_btn.click()
            assert annotation.STATE.step_cmd == panel.step_cmd_edit.toPlainText()
            assert annotation.STATE.bucket_cmds[label] == panel._bucket_inputs[label].toPlainText()
            assert float(abs(baseline - cmd.get_coordset(candidate)).max()) == 0
            result["applied_templates"] = {"on_open": annotation.STATE.step_cmd,
                                           target_step: annotation.STATE.bucket_cmds[label]}
            panel.main_tabs.setCurrentIndex(0)
            # Existing navigation actually executes the confirmed per-open recipe on another object.
            QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
            wait_for(app, lambda: panel._pending_navigation is None and annotation.STATE.index == 1)
            candidate = annotation.STATE.current_object
            atoms, colors, reps = cmd.count_atoms(candidate), set(), set()
            cmd.iterate(candidate, "observed_colors.add(color); observed_reps.add(reps)",
                        space={"observed_colors": colors, "observed_reps": reps})
            assert atoms > 100 and colors == {cmd.get_color_index("cyan")}, colors
            assert all(value & 32 for value in reps), reps  # native cartoon representation bit
            assert abs(cmd.get_setting_float("cartoon_transparency", candidate) - 0.2) < 0.001
            result["on_open_execution"] = {"atoms": atoms, "colors": sorted(colors), "representations": sorted(reps)}
            panel.auto_advance_cb.setChecked(False)
            panel.review.form.scroll.ensureWidgetVisible(panel._value_buttons[0])
            QTest.mouseClick(panel._value_buttons[0], QtCore.Qt.LeftButton)
            QTest.mouseClick(panel.review.form.save_button, QtCore.Qt.LeftButton)
            wait_for(app, lambda: not panel._csv_task_id)
            colors, reps = set(), set()
            cmd.iterate(candidate, "observed_colors.add(color); observed_reps.add(reps)",
                        space={"observed_colors": colors, "observed_reps": reps})
            assert colors == {cmd.get_color_index("yellow")}, colors
            assert all(value & 1 for value in reps), reps  # native sticks bit
            result["after_label_execution"] = {"atoms": atoms, "colors": sorted(colors), "representations": sorted(reps)}
            cmd.save(str(args.output.with_suffix(".pse")))
            window.raise_()
            window.activateWindow()
            cmd.refresh()
            window.pymolwidget._pymolProcess()
            QTest.qWait(150)
            window.pymolwidget.repaint()
            app.processEvents()
            window.pymolwidget.grabFrameBuffer().save(str(args.output.with_name(args.output.stem + "-scene.png")))

            # Explicit adversarial response injection at the real GUI's parse boundary.
            bad = ai_widgets.AICommandDialog(panel)
            settings = window_settings()
            settings.setValue("ai_commands/mode", "codex")
            settings.sync()
            bad.refresh_connection()
            bad.show()
            bad.context = ai_widgets.current_context(panel)
            bad.request_steps = {"on_open": panel.step_cmd_edit.toPlainText()}
            good_doc = {"schema": SCHEMA, "steps": [{"step_id": "on_open", "commands": ["zoom {object}"]}]}
            invalid = {"markdown": "```json\n" + json.dumps(good_doc) + "\n```",
                       "truncated": json.dumps(good_doc)[:-4], "missing": '{"schema":"' + SCHEMA + '"}',
                       "extra_text": json.dumps(good_doc) + "\nDone",
                       "duplicate_field": '{"schema":"x","schema":"' + SCHEMA + '","steps":[]}',
                       "wrong_step": json.dumps(good_doc).replace('"on_open"', '"step_1"'),
                       "unknown_command": json.dumps(good_doc).replace("zoom {object}", "imaginary {object}"),
                       "python": json.dumps(good_doc).replace("zoom {object}", "python print(1)"),
                       "chained": json.dumps(good_doc).replace("zoom {object}", "zoom {object}; delete all"),
                       "placeholder": json.dumps(good_doc).replace("{object}", "{reference}"),
                       "object_missing": json.dumps(good_doc).replace("{object}", "absent_ai_object")}
            invalid["unsupported_setting"] = json.dumps(good_doc).replace("zoom {object}", "set security, 0, {object}")
            invalid["empty_with_reason"] = json.dumps({"schema": SCHEMA, "steps": [
                {"step_id": "on_open", "commands": [], "note": "This request needs an unsupported operation."}]})
            snapshots = {key: edit.toPlainText() for key, edit in ai_widgets.command_editors(panel).items()}
            result["injected_invalid_documents"] = {}
            for name, text in invalid.items():
                bad.receive_response({"text": text})
                assert bad.status.text().startswith("Failed"), bad.status.text()
                assert not bad.insert_button.isEnabled()
                assert snapshots == {key:edit.toPlainText() for key,edit in ai_widgets.command_editors(panel).items()}
                result["injected_invalid_documents"][name] = bad.status.text()
            bad.request_edit.setPlainText("Show cartoon and zoom the current structure")
            # Actual provider transport, cancel after it announces request dispatch.
            bad.generate_button.click()
            process = bad.process
            progress = bytearray()
            def dispatched():
                progress.extend(bytes(process.readAllStandardError()))
                return b"request_started" in progress
            wait_for(app, dispatched, timeout=20)
            stopped = []
            process.finished.connect(lambda *_: stopped.append(True))
            start = time.perf_counter()
            bad.cancel_button.click()
            wait_for(app, lambda: bool(stopped), timeout=10)
            assert snapshots == {key:edit.toPlainText() for key,edit in ai_widgets.command_editors(panel).items()}
            result["actual_request_cancel"] = {"seconds": time.perf_counter()-start, "status": bad.status.text()}

            # A deliberately refused HTTP proxy: real socket failure, same provider/model/auth.
            original_launch = ai_widgets.provider_process
            def refused_proxy_launch():
                program, arguments = original_launch()
                index = arguments.index("--exec") + 1
                return program, arguments[:index] + ["env", "http_proxy=http://127.0.0.1:1",
                    "https_proxy=http://127.0.0.1:1", "no_proxy=", "HTTP_PROXY=http://127.0.0.1:1",
                    "HTTPS_PROXY=http://127.0.0.1:1", "NO_PROXY="] + arguments[index:]
            ai_widgets.provider_process = refused_proxy_launch
            try:
                bad.generate_button.click()
                wait_for(app, lambda: bad.process is None, timeout=145)
                assert "network connection failed" in bad.status.text(), bad.status.text()
                assert bad.generate_button.isEnabled() and not bad.insert_button.isEnabled()
                assert snapshots == {key:edit.toPlainText() for key,edit in ai_widgets.command_editors(panel).items()}
                result["injected_network_refusal"] = bad.status.text()
                bad.grab().save(str(args.output.with_name(args.output.stem + "-error.png")))
            finally:
                ai_widgets.provider_process = original_launch
            bad.reject()
            flush_writes()
            state_path = Path(annotation.dataset_state_path(str(table), **annotation._state_kwargs()))
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            assert saved["config"]["step_cmd"] == annotation.STATE.step_cmd
            assert saved["config"]["bucket_cmds"][label] == annotation.STATE.bucket_cmds[label]
            shutil.copyfile(state_path, args.output.with_name(args.output.stem + "-state.json"))
            result["saved_multiline_templates"] = True
            panel.close()
            flush_writes()
            annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
            panel = annotation.show_structure_annotator_panel()
            open_table(panel, table, app, QTest, QtCore.Qt)
            assert panel.step_cmd_edit.toPlainText() == saved["config"]["step_cmd"]
            assert panel._bucket_inputs[label].toPlainText() == saved["config"]["bucket_cmds"][label]
            result["reopened_multiline_templates"] = True
        if args.preset_inputs:
            from pymolplugins.annotation.command_templates import LIPID_SELECTION
            preset_inputs = args.preset_inputs
            scenarios = [("cartoon", sources[0]), ("ppi", preset_inputs / "15_preset_1brs.pdb"),
                         ("pli", preset_inputs / "15_preset_1a4w.pdb"),
                         ("oligomer", preset_inputs / "15_preset_1brs.pdb"),
                         ("membrane", preset_inputs / "15_preset_2rh1.pdb"),
                         ("nucleic", sources[0]), ("surface", preset_inputs / "15_preset_1a4w.pdb")]
            if args.preset:
                scenarios = [scenario for scenario in scenarios if scenario[0] == args.preset]
                assert scenarios, "Unknown preset: " + args.preset
            preset_directory = directory / "viewing-presets"
            preset_directory.mkdir()
            preset_table = preset_directory / "presets.csv"
            with preset_table.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["structure_path", "id"])
                writer.writerows((str(path), name) for name, path in scenarios)
            (preset_directory / "review.json").write_text(json.dumps({"path_column": "structure_path", "step_cmd": "", "bucket_cmds": {}}))
            panel.step_cmd_edit.clear()
            panel._apply_commands()
            open_table(panel, preset_table, app, QTest, QtCore.Qt)
            assert annotation.STATE.reference_column == ""
            result["new_source_clears_reference_column"] = True
            result["viewing_presets"] = {}
            for index, (name, path) in enumerate(scenarios):
                panel.step_cmd_edit.clear()
                panel._apply_commands()
                panel._request_navigation(index)
                wait_for(app, lambda: panel._pending_navigation is None)
                assert not panel.error_label.isVisible(), panel.error_label.text()
                candidate = annotation.STATE.current_object
                before = cmd.get_coordset(candidate).copy()
                colors_before = []
                cmd.iterate(candidate, "values.append(color)", space={"values": colors_before})
                preset_dialog = ai_widgets.AICommandDialog(panel)
                preset_dialog.show()
                preset_dialog.preset_combo.setCurrentIndex(preset_dialog.preset_combo.findData(name))
                QTest.mouseClick(preset_dialog.preset_button, QtCore.Qt.LeftButton)
                assert preset_dialog.insert_button.isEnabled(), preset_dialog.status.text()
                colors_after = []
                cmd.iterate(candidate, "values.append(color)", space={"values": colors_after})
                assert colors_after == colors_before and not annotation.STATE.step_cmd
                recipe = preset_dialog.preview_editors["on_open"].toPlainText()
                note = preset_dialog.status.text()
                def confirm_preset():
                    box = app.activeModalWidget()
                    QTest.mouseClick(box.button(QtWidgets.QMessageBox.Yes), QtCore.Qt.LeftButton)
                QtCore.QTimer.singleShot(100, confirm_preset)
                QTest.mouseClick(preset_dialog.insert_button, QtCore.Qt.LeftButton)
                assert panel.step_cmd_edit.toPlainText() == recipe
                panel.commands_apply_btn.click()
                panel._reload()
                assert not panel.error_label.isVisible(), panel.error_label.text()
                assert float(abs(before - cmd.get_coordset(candidate)).max()) == 0
                representations = []
                cmd.iterate(candidate, "values.append(reps)", space={"values": representations})
                if name in {"ppi", "pli", "nucleic"}:
                    assert any(value & 1 for value in representations)
                if name in {"oligomer", "surface"}:
                    assert any(value & 4 for value in representations)
                if name == "membrane":
                    assert cmd.count_atoms("(%s) and (%s)" % (candidate, LIPID_SELECTION)) > 0
                result["viewing_presets"][name] = {"source": str(path), "atoms": cmd.count_atoms(candidate),
                    "commands": recipe, "status": note, "coordinate_max_error": 0.0}
                cmd.refresh()
                window.pymolwidget._pymolProcess()
                QTest.qWait(100)
                window.pymolwidget.repaint()
                app.processEvents()
                window.pymolwidget.grabFrameBuffer().save(str(args.output.with_name(args.output.stem + "-" + name + ".png")))
        for path, digest in hashes.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        panel.close()
        assert annotation._csv_async_pool().waitForDone(30000)
        flush_writes()
    result["status"] = "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", type=Path, help="Install and run from this ZIP, not the worktree")
    parser.add_argument("--stage", choices=("all", "presets"), default="all")
    parser.add_argument("--preset", default="", help="Limit the preset stage to one changed preset")
    parser.add_argument("--preset-inputs", type=Path, help="Folder containing the documented 15_preset_*.pdb inputs")
    args = parser.parse_args()
    result = {"status": "running"}
    try:
        run(args, result)
    except Exception:
        result.update(status="failed", traceback=traceback.format_exc())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)
