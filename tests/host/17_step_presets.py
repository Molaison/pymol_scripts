"""Real Qt preset drafts, named references and bundle examples on a hidden desktop.

Run: D:/Softwares/Pymol3.1/python.exe -I -u tests/host/17_step_presets.py
Input: output/17_step_presets/1brs.pdb from https://files.rcsb.org/download/1BRS.pdb
All generated review state is temporary. Evidence stays in output/17_step_presets/.
"""

import argparse
import ctypes
from ctypes import wintypes
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--child", action="store_true")
parser.add_argument("--align-probe", action="store_true")
parser.add_argument("--plugin-root", type=Path, help="Validate an installed package instead of source")
parser.add_argument("--output", type=Path, default=ROOT / "output/17_step_presets")
args = parser.parse_args()
OUTPUT = args.output.resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
if args.plugin_root:
    sys.path.insert(0, str(args.plugin_root.resolve()))

if args.align_probe:
    import pymol
    from pymol import cmd
    pymol.finish_launching(["pymol", "-cqk"])
    cmd.load(str(ROOT / "output/17_step_presets/1brs.pdb"), "candidate")
    cmd.create("reference", "candidate")
    cmd.select("manual_selection", "reference and name CA")
    probe = {"before": cmd.get_names("selections")}
    cmd.do("align candidate and name CA, reference and name CA, quiet=1")
    probe["after_quiet"] = cmd.get_names("selections")
    cmd.do("align candidate and name CA, reference and name CA")
    probe["after"] = cmd.get_names("selections")
    (OUTPUT / "12_align_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    print(json.dumps(probe), flush=True)
    cmd.quit()
    raise SystemExit(0)

if not args.child:
    import win32event
    import win32process

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]

    def input_desktop():
        handle = user32.OpenInputDesktop(0, False, 1)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
            if not user32.GetUserObjectInformationW(
                handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return name.value
        finally:
            user32.CloseDesktop(handle)

    desktop_name = f"PyMOLPresets_{os.getpid()}"
    launch = {"input_before": input_desktop(), "desktop": desktop_name}
    desktop = user32.CreateDesktopW(desktop_name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\" + desktop_name
        process, thread, pid, _ = win32process.CreateProcess(
            sys.executable,
            subprocess.list2cmdline([sys.executable, "-I", "-u", str(Path(__file__).resolve()),
                                    *sys.argv[1:], "--child"]),
            None, None, False, 0x08000000, None, str(ROOT), startup,
        )
        launch["pid"] = pid
        try:
            wait = win32event.WaitForSingleObject(process, 120000)
            if wait == 258:
                win32process.TerminateProcess(process, 124)
                win32event.WaitForSingleObject(process, 5000)
            launch.update(exit_code=win32process.GetExitCodeProcess(process), timed_out=wait == 258)
        finally:
            thread.Close()
            process.Close()
        launch["input_after"] = input_desktop()
        assert launch["input_before"] == launch["input_after"], launch
    finally:
        user32.CloseDesktop(desktop)
        (OUTPUT / "02_desktop.json").write_text(json.dumps(launch, indent=2), encoding="utf-8")
    print(json.dumps(launch), flush=True)
    raise SystemExit(launch["exit_code"])

from pymol import cmd, gui, invocation
from pymol.Qt import QtCore, QtWidgets
from PyQt5.QtTest import QTest
from pmg_qt.pymol_qt_gui import PyMOLQtGUI
from annotator_workflow_smoke import wait_for
from annotator_source_review_smoke import open_table
from workspace_helpers import isolate_workspace_settings, resize_workspace

report = {"status": "running", "presets": {}, "checks": {}}
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
invocation.parse_args(["pymol", "-k", "-q"])
window = PyMOLQtGUI()
gui.createlegacypmgapp = window.createlegacypmgapp
window.add_lineedit()
window.resize(1200, 820)
window.show()
app.processEvents()


def capture(name):
    app.processEvents()
    window.pymolwidget._pymolProcess()
    window.pymolwidget.repaint()
    app.processEvents()
    image = window.pymolwidget.grabFrameBuffer()
    colors = {image.pixel(x, y) for x in range(0, image.width(), 4)
              for y in range(0, image.height(), 4)}
    assert len(colors) > 100, ("Empty/uniform OpenGL capture", len(colors))
    assert image.save(str(OUTPUT / name))
    return hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest()


def atom_style(selection):
    values = []
    cmd.iterate(selection, "values.append((color, reps))", space={"values": values})
    return values


def navigate(panel, index):
    panel._request_navigation(index)
    wait_for(app, lambda: not panel._csv_task_id and panel._pending_navigation is None)
    assert not panel.error_label.isVisible(), panel.error_label.text()
    assert annotation.STATE.index == index


def confirm_insert():
    box = app.activeModalWidget()
    assert isinstance(box, QtWidgets.QMessageBox)
    QTest.mouseClick(box.button(QtWidgets.QMessageBox.Yes), QtCore.Qt.LeftButton)


try:
    with tempfile.TemporaryDirectory(prefix="pymol-step-presets-") as temporary:
        directory = Path(temporary)
        isolate_workspace_settings(directory, window)
        os.environ.update(
            PYMOLPLUGINS_ANNOTATOR_HISTORY=str(directory / "history.json"),
            PYMOLPLUGINS_ANNOTATOR_MAPPINGS=str(directory / "mappings.json"),
            PYMOLPLUGINS_STRUCTURE_CACHE_DIR=str(directory / "cache"),
        )
        from pymolplugins.host.plugin import pp_load
        from pymolplugins.annotation import plugin as annotation, ai_widgets
        from pymolplugins.annotation.ai_commands import SCHEMA, parse_draft, build_prompt
        from pymolplugins.annotation.command_templates import viewing_preset
        from pymolplugins.annotation.persistence import flush_writes
        from pymolplugins.annotation.review_file import validate_review_config, validate_review_columns
        from pymolplugins.annotation.filtering import compile_filter
        from pymolplugins import __version__

        source = ROOT / "output/17_step_presets/1brs.pdb"
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        report.update(pymol=cmd.get_version()[0], plugin_version=__version__, renderer=cmd.get_renderer(),
                      source=str(source), source_sha256=source_hash,
                      source_url="https://files.rcsb.org/download/1BRS.pdb",
                      module=ai_widgets.__file__)
        if args.plugin_root:
            assert Path(ai_widgets.__file__).resolve().is_relative_to(args.plugin_root.resolve())
        assert pp_load()["status"] == "ok"
        cmd.load(str(source), "fixture")
        cmd.remove("fixture and not (chain A+D and polymer.protein)")
        assert cmd.get_chains("fixture") == ["A", "D"]
        # Two candidate files in a translated frame make alignment movement observable.
        reference_path = directory / "reference.pdb"
        cmd.save(str(reference_path), "fixture")
        cmd.translate([12, 6, 0], "fixture")
        candidate_paths = [directory / "candidate_a.pdb", directory / "candidate_b.pdb"]
        for path in candidate_paths:
            cmd.save(str(path), "fixture")
        cmd.delete("fixture")
        originals = {path: path.read_bytes() for path in [reference_path] + candidate_paths}
        columns = ["id", "structure_path", "absolute_path", "reference_path", "label",
                   "score1", "score2", "score3", "score4"]
        table = directory / "sum_scores.csv"
        rows = []
        for index, path in enumerate(candidate_paths):
            rows.append(dict(zip(columns, [f"test_{index}", path.name, str(path), reference_path.name,
                                          "representative" if index == 0 else "", "", 2, 3, 4])))
        with table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        examples = [json.loads(block) for block in re.findall(
            r"```json\n(.*?)\n```", (ROOT / "docs/02_agent_review.md").read_text(encoding="utf-8"), re.S)]
        assert len(examples) == 2
        for example in examples:
            validate_review_columns(validate_review_config(example), columns)
        assert sum(compile_filter("label is not None", columns)(row) for row in rows) == 1
        assert sum(compile_filter("score1 is None", columns)(row) for row in rows) == 2
        report["checks"]["prompt_examples_and_filters"] = "passed"
        review = examples[1]
        review_path = directory / "review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        review_bytes = review_path.read_bytes()
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        panel = annotation.show_structure_annotator_panel()
        resize_workspace(panel, 840, 650)
        open_table(panel, table, app, QTest, QtCore.Qt)
        assert len(annotation.STATE.entries) == 2
        candidate = annotation.STATE.current_object
        reference = annotation.STATE.step_context["reference"]
        ref_coords = cmd.get_coords(reference).copy()
        cmd.bg_color("white")
        cmd.orient(candidate)
        cmd.zoom(candidate, 4)
        first = capture("03_render_before.png")
        camera = cmd.get_view()
        cmd.turn("y", 35)
        cmd.color("orange", candidate)
        second = capture("04_render_changed.png")
        assert first != second
        cmd.set_view(camera)
        report["checks"]["hidden_desktop_render_updates"] = "passed"
        # An unrelated object and a manual selection must survive every preset.
        cmd.load(str(reference_path), "unrelated")
        cmd.hide("everything", "unrelated")
        cmd.color("magenta", "unrelated")
        cmd.select("manual_selection", "unrelated and name CA")
        selection_names = cmd.get_names("selections")
        manual_members = cmd.index("manual_selection")
        external_style = atom_style("unrelated")
        for number, name in enumerate(
            ["review_fixed_view", "ppi_surface", "reference_overlay", "reference_align"], 5
        ):
            panel.step_cmd_edit.clear()
            assert panel._apply_commands()
            navigate(panel, 0)
            candidate = annotation.STATE.current_object
            before = cmd.get_coords(candidate).copy()
            camera = cmd.get_view()
            style = atom_style(candidate)
            reference_style = atom_style(reference)
            dialog = ai_widgets.AICommandDialog(panel)
            dialog.show()
            dialog.preset_combo.setCurrentIndex(dialog.preset_combo.findData(name))
            if name.startswith("reference_"):
                assert dialog.reference_row.isVisible()
                assert dialog.reference_combo.count() == 2
                dialog.reference_combo.setCurrentIndex(dialog.reference_combo.findData("reference"))
            QTest.mouseClick(dialog.preset_button, QtCore.Qt.LeftButton)
            assert dialog.insert_button.isEnabled(), dialog.status.text()
            recipe = dialog.preview_editors["on_open"].toPlainText()
            assert atom_style(candidate) == style and atom_style(reference) == reference_style
            assert cmd.get_view() == camera and not annotation.STATE.step_cmd
            assert float(abs(cmd.get_coords(candidate) - before).max()) == 0
            if name == "reference_overlay":
                dialog.resize(860, 690)
                app.processEvents()
                assert dialog.grab().save(str(OUTPUT / "09_reference_draft.png"))
            QtCore.QTimer.singleShot(100, confirm_insert)
            QTest.mouseClick(dialog.insert_button, QtCore.Qt.LeftButton)
            assert panel.step_cmd_edit.toPlainText() == recipe
            assert not annotation.STATE.step_cmd
            assert panel._apply_commands()
            panel._reload()
            wait_for(app, lambda: panel._pending_navigation is None)
            assert not panel.error_label.isVisible(), panel.error_label.text()
            candidate = annotation.STATE.current_object
            moved = float(abs(cmd.get_coords(candidate) - before).max())
            assert float(abs(cmd.get_coords(reference) - ref_coords).max()) == 0
            if name == "reference_align":
                assert moved > 5
                assert cmd.rms_cur(candidate + " and name CA", reference + " and name CA") < 0.01
            else:
                assert moved == 0
            if name in {"review_fixed_view", "reference_overlay"}:
                assert max(abs(a-b) for a, b in zip(camera, cmd.get_view())) < 1e-5
            if name == "ppi_surface":
                assert any(reps & 4 for _, reps in atom_style(candidate))
            expected_selections = set(selection_names)
            if name == "reference_align":
                expected_selections.update(("_align1", "_align2"))
                report["checks"]["native_alignment_selections"] = ["_align1", "_align2"]
            assert set(cmd.get_names("selections")) == expected_selections
            assert cmd.index("manual_selection") == manual_members
            assert atom_style("unrelated") == external_style
            capture(f"{number:02d}_{name}.png")
            switch_camera = cmd.get_view()
            navigate(panel, 1)
            switched = annotation.STATE.current_object
            assert switched != candidate
            assert annotation.STATE.step_context["reference"] == reference
            assert float(abs(cmd.get_coords(reference) - ref_coords).max()) == 0
            if name in {"review_fixed_view", "reference_overlay"}:
                assert max(abs(a-b) for a, b in zip(switch_camera, cmd.get_view())) < 1e-5
            if name == "reference_align":
                assert cmd.rms_cur(switched + " and name CA", reference + " and name CA") < 0.01
            report["presets"][name] = {"commands": recipe, "candidate_max_movement": moved,
                                      "reference_max_movement": 0, "candidate_switch": "passed",
                                      "status": "passed"}
            dialog.deleteLater()

        candidate = annotation.STATE.current_object
        context = ai_widgets.current_context(panel)
        assert context["review_objects"] == {"reference": reference}
        # Native validation accepts only declared named object placeholders.
        document = {"schema": SCHEMA, "steps": [{"step_id": "on_open",
                    "commands": ["color gray70, {anchor}"]}]}
        named = dict(context, review_objects={"anchor": reference})
        parse_draft(json.dumps(document), {"on_open": ""}, named, cmd)
        bucket = {"schema": SCHEMA, "steps": [{"step_id": "after_label/Keep",
                  "commands": ["color gray70, {anchor}"]}]}
        parse_draft(json.dumps(bucket), {"after_label/Keep": ""}, named, cmd)
        try:
            parse_draft(json.dumps(document), {"on_open": ""}, context, cmd)
        except ValueError as exc:
            assert "unknown placeholder" in str(exc)
        else:
            raise AssertionError("Undeclared named object was accepted")
        assert "review_objects" in build_prompt({"on_open": ""}, cmd.get_names("objects"), context)
        try:
            viewing_preset("reference_align", candidate, cmd)
        except ValueError as exc:
            assert "Choose a loaded fixed object" in str(exc)
        else:
            raise AssertionError("Missing fixed reference was accepted")
        report["checks"]["named_object_validation"] = "passed"

        # Human edits persist as override and still bind to the next candidate.
        navigate(panel, 0)
        edited = panel.step_cmd_edit.toPlainText() + "\ncolor salmon, {candidate}"
        panel.step_cmd_edit.setPlainText(edited)
        assert panel._apply_commands()
        navigate(panel, 1)
        candidate = annotation.STATE.current_object
        assert annotation.STATE.step_context["reference"] == reference
        assert float(abs(cmd.get_coords(reference) - ref_coords).max()) == 0
        assert cmd.rms_cur(candidate + " and name CA", reference + " and name CA") < 0.01
        assert all(color == cmd.get_color_index("salmon") for color, _ in atom_style(candidate))
        panel.close()
        flush_writes()
        override = json.loads((directory / "review.override.json").read_text(encoding="utf-8"))
        assert override["step_cmd"] == edited
        assert review_path.read_bytes() == review_bytes
        annotation.STATE = annotation.AnnotatorState(preload_enabled=False)
        panel = annotation.show_structure_annotator_panel()
        open_table(panel, table, app, QTest, QtCore.Qt)
        assert panel.step_cmd_edit.toPlainText() == edited
        assert all(color == cmd.get_color_index("salmon")
                   for color, _ in atom_style(annotation.STATE.current_object))
        report["checks"]["manual_override_navigation_reopen"] = "passed"
        for path, content in originals.items():
            assert path.read_bytes() == content
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        report["checks"]["source_files_unchanged"] = "passed"
        panel.close()
        assert annotation._csv_async_pool().waitForDone(30000)
        flush_writes()
    report["status"] = "passed"
except Exception:
    report.update(status="failed", traceback=traceback.format_exc())
finally:
    (OUTPUT / "01_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True), file=sys.__stdout__, flush=True)
    cmd.quit(0 if report["status"] == "passed" else 1)
