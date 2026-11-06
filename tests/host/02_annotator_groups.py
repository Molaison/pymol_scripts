"""Real Windows PyMOL group review, ownership, persistence and query acceptance."""

import argparse
import ast
import csv
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from workspace_helpers import isolate_workspace_settings

APPLICATION = None


def fixed_output_check(output):
    import pymol
    pymol.finish_launching(["pymol", "-ckq"])
    from pymol import cmd
    from pymolplugins.core.pymol_groups import group_parent_snapshot
    import pymolplugins.annotation.plugin as a
    with tempfile.TemporaryDirectory(prefix="annotator-fixed-output-") as temporary:
        root = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(root / "history.json")
        cmd.fragment("gly", "fixture")
        for index in range(2):
            cmd.save(str(root / f"{index}.pdb"), "fixture")
        a.STATE = a.AnnotatorState(preload_enabled=False, keep_previous=True,
            step_cmd="distance contacts, {object} and index 1, {object} and index 2")
        a.sa_load_dir(str(root), restore_saved=False, raise_errors=True)
        first = a.STATE.current_object
        first_group = a._candidate_bundles.groups[first]
        before = set(cmd.get_names("objects"))
        try:
            a._navigate_annotation("goto", 1)
            raise AssertionError("Fixed output silently reused by another candidate")
        except ValueError as exc:
            error = str(exc)
            assert "another candidate" in error and "{index}" in error
        assert a.STATE.index == 0 and a.STATE.current_object == first
        assert a._verify_current_object_enabled()
        assert set(cmd.get_names("objects")) == before
        assert group_parent_snapshot(cmd)["contacts"] == first_group
        a.STATE.step_cmd = "distance contacts_{index}, {object} and index 1, {object} and index 2"
        a._navigate_annotation("goto", 1)
        current_group = a._candidate_bundles.groups[a.STATE.current_object]
        assert group_parent_snapshot(cmd)["contacts_1"] == current_group
        a.STATE.keep_previous = False
        a._evict_unneeded_objects()
        assert "contacts_1" in cmd.get_names("objects") and "contacts" not in cmd.get_names("objects")
        a._delete_managed_objects(False)
        return {"status": "passed", "pymol": cmd.get_version()[0], "fixed_name_rejected": error,
                "cursor_and_owned_scene_preserved": True, "unique_output_switch_and_eviction": True}


def compare_candidate_queries(a, cmd):
    """Compare the owning pre-change functions on the same already-loaded scene."""
    source = subprocess.check_output(["git", "show", "a883fe9:pymolplugins/annotation/plugin.py"],
                                     cwd=ROOT, text=True, encoding="utf-8")
    tree = ast.parse(source)
    names = {"_object_exists", "_ensure_candidate_loaded", "_evict_unneeded_objects"}
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    old = dict(vars(a))
    exec(compile(tree, "baseline-a883fe9", "exec"), old)
    extra = []
    for index in range(100):
        name = "benchmark_object_" + str(index)
        cmd.create(name, "user_molecule")
        extra.append(name)
    a.STATE.managed_objects.extend(extra)
    a.STATE.keep_previous = False
    results = {}
    originals = {name: getattr(cmd, name) for name in ("get_names", "get_object_list", "get_session")}
    for label, namespace in (("baseline", old), ("current", vars(a))):
        calls = dict.fromkeys(originals, 0)
        def counted(name):
            def call(*args, **kwargs):
                calls[name] += 1
                return originals[name](*args, **kwargs)
            return call
        with patch.object(cmd, "get_names", counted("get_names")), patch.object(cmd, "get_object_list", counted("get_object_list")), patch.object(cmd, "get_session", counted("get_session")):
            started = time.perf_counter()
            for _ in range(20):
                namespace["_ensure_candidate_loaded"](0, True, run_commands=False)
                namespace["_evict_unneeded_objects"]()
            elapsed = (time.perf_counter() - started) * 1000
        results[label] = {"queries": calls, "elapsed_ms": elapsed, "iterations": 20}
    for name in extra:
        a.STATE.managed_objects.remove(name)
        cmd.delete(name)
    a.STATE.keep_previous = True
    assert sum(results["current"]["queries"].values()) < sum(results["baseline"]["queries"].values())
    return results


def run(output):
    global APPLICATION
    from pymol import cmd, invocation, gui
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pmg_qt.pymol_qt_gui import PyMOLQtGUI
    APPLICATION = app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    invocation.parse_args(["pymol", "-k", "-q"])
    window = PyMOLQtGUI()
    gui.createlegacypmgapp = window.createlegacypmgapp
    window.add_lineedit()
    window.resize(1100, 760)
    window.show()
    app.processEvents()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="annotator-groups-") as temporary:
        directory = Path(temporary)
        os.environ["PYMOLPLUGINS_ANNOTATOR_HISTORY"] = str(directory / "history.json")
        os.environ["PYMOLPLUGINS_ANNOTATOR_MAPPINGS"] = str(directory / "mappings.json")
        os.environ["PYMOLPLUGINS_STRUCTURE_CACHE_DIR"] = str(directory / "cache")
        isolate_workspace_settings(directory, window)
        import pymolplugins.annotation.plugin as a
        from pymolplugins.annotation.persistence import flush_writes
        from pymolplugins.core.pymol_groups import group_parent_snapshot
        a.STATE = a.AnnotatorState(preload_enabled=False, keep_previous=True, auto_advance=False)
        cmd.fragment("gly", "user_molecule")
        cmd.select("user_selection", "user_molecule and name CA")
        selection_before = cmd.index("user_selection")
        for index in range(3):
            cmd.save(str(directory / f"{index}.pdb"), "user_molecule")
        table = directory / "candidates.csv"
        with table.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["structure_path", "reference"])
            writer.writerows([[f"{i}.pdb", "0.pdb"] for i in range(3)])
        steps = [
            {"type": "object", "name": "reference", "path_column": "reference", "fixed": True,
             "on_load_cmd": "pseudoatom fixed_label, label=Reference"},
            {"type": "object", "name": "candidate", "on_load_cmd":
             "create ligand_{index}, {candidate} and name CA\n"
             "distance contact_{index}, {candidate} and name CA, {candidate} and name N\n"
             "pseudoatom label_{index}, label=Candidate\ncolor blue, user_molecule"},
            {"type": "commands", "commands": "zoom {candidate}"}]
        (directory / "review.json").write_text(json.dumps({"path_column": "structure_path", "steps": steps}))
        a.sa_load_csv(str(table), path_column="structure_path")
        assert a.STATE.files, "CSV import failed"
        first = a.STATE.current_object
        reference = a._review_objects.active["reference"][1]
        reference_coords = cmd.get_coords(reference).copy()
        first_group = a._candidate_bundles.groups[first]
        parents = group_parent_snapshot(cmd)
        assert all(parents[name] == first_group for name in [first, "ligand_0", "contact_0", "label_0"])
        assert not parents.get("user_molecule") and not parents.get(reference)
        a._navigate_annotation("goto", 1)
        assert first_group not in cmd.get_names("objects", enabled_only=1)
        assert "fixed_label" in cmd.get_names("objects", enabled_only=1)
        a._navigate_annotation("goto", 0)
        assert a.STATE.current_object == first and (cmd.get_coords(reference) == reference_coords).all()
        assert a._review_objects.active["reference"][1] == reference
        before = set(cmd.get_names("objects"))
        a.STATE.steps[1]["on_load_cmd"] += "\ngroup failed_owned_group, user_molecule\nthis_is_not_a_command"
        try:
            a._navigate_annotation("goto", 2)
            raise AssertionError("Expected command failure")
        except RuntimeError:
            pass
        assert set(cmd.get_names("objects")) == before
        assert a.STATE.current_object == first and a._verify_current_object_enabled()
        a.STATE.steps[1]["on_load_cmd"] = steps[1]["on_load_cmd"]
        assert cmd.index("user_selection") == selection_before
        # Reimport parks generated objects too, so explicit on-load names can be recreated.
        a.sa_load_csv(str(table), path_column="structure_path")
        first = a.STATE.current_object
        assert {"ligand_0", "contact_0", "label_0"} <= set(cmd.get_names("objects"))
        a.sa_group_candidate(0)
        assert not a._candidate_bundles.groups
        assert {first, "ligand_0", "contact_0", "label_0"} <= set(cmd.get_names("objects"))
        a.sa_group_candidate(1)
        assert group_parent_snapshot(cmd)["contact_0"] == a._candidate_bundles.groups[first]
        other_table = directory / "other.csv"
        other_table.write_bytes(table.read_bytes())
        a.sa_load_csv(str(other_table), path_column="structure_path")
        first = a.STATE.current_object
        assert "fixed_label" in cmd.get_names("objects")
        assert not any(name.startswith("sa_step_tx_") for name in cmd.get_names("objects"))
        a._run_pymol_commands("group owned_foreign_group, user_molecule", a.STATE.step_context)
        assert cmd.count_atoms("user_molecule") == 7
        performance = compare_candidate_queries(a, cmd)
        file_result = {"bundle_members": [first, "ligand_0", "contact_0", "label_0"],
                       "fixed_reference_reused": True, "failed_step_rollback": True}
        # A large unrelated scene makes whole-scene serialization visible.
        for index in range(3):
            cmd.create(f"model_{index}", "user_molecule")
            cmd.distance(f"distance_{index}", f"model_{index} and name CA", f"model_{index} and name N")
            cmd.pseudoatom(f"text_{index}", label=f"Group {index}")
            cmd.group(f"candidate_{index}", f"model_{index} distance_{index} text_{index}")
            cmd.group("review_parent", f"candidate_{index}")
        cmd.disable("review_parent")
        owned = set(a.STATE.managed_objects) | a._review_objects.owned | set(a._candidate_bundles.groups.values())
        owned.update(set().union(*a._candidate_bundles.products.values()))
        scene_names = set(cmd.get_names("objects")) - owned
        a.STATE.step_cmd = "zoom {object}"
        manifest = directory / "scene-review.json"
        a.sa_load_groups("review_parent", str(manifest), 0)
        assert a.STATE.files == ["candidate_0", "candidate_1", "candidate_2"]
        assert "review_parent" in cmd.get_names("objects", enabled_only=1)
        assert not any(name.startswith("sa_") for name in cmd.get_names("objects"))
        calls = {name: 0 for name in ("get_names", "get_object_list", "get_session")}
        originals = {name: getattr(cmd, name) for name in calls}
        def counted(name):
            def call(*args, **kwargs):
                calls[name] += 1
                return originals[name](*args, **kwargs)
            return call
        with patch.object(cmd, "get_names", counted("get_names")), patch.object(cmd, "get_object_list", counted("get_object_list")), patch.object(cmd, "get_session", counted("get_session")):
            started = time.perf_counter()
            for index in range(30):
                a._navigate_annotation("goto", index % 3)
            elapsed = (time.perf_counter() - started) * 1000
        assert calls["get_session"] == 0, calls
        assert cmd.index("user_selection") == selection_before
        cmd.disable("review_parent")
        assert not a._verify_current_object_enabled(check_parents=True)
        a.sa_annotate("Bad")
        assert a.STATE.row_annotation_store.get(a.STATE.current_entry_id(), "quality", "") == ""
        a._navigate_annotation("goto", 1)
        assert a._verify_current_object_enabled(check_parents=True)
        a.sa_annotate("Good")
        assert a.STATE.row_annotation_store.get("group:candidate_1", "quality") == "Good"
        a.sa_load_groups("", str(manifest), 1)
        assert a.STATE.current_object == "candidate_1"
        assert a.STATE.row_annotation_store.get("group:candidate_1", "quality") == "Good"
        try:
            a.sa_load_groups("candidate_0", str(manifest), 1)
            raise AssertionError("Conflicting manifest parent was accepted")
        except ValueError as exc:
            assert "another parent" in str(exc)
        from pymolplugins.host.plugin import pp_review_groups
        result = pp_review_groups("review_parent", str(manifest), 1)
        assert result["active_count"] == 3
        panel = a._panel
        panel.window().resize(630, 720)
        app.processEvents()
        QTest.mouseClick(panel.next_btn, QtCore.Qt.LeftButton)
        app.processEvents()
        assert a.STATE.current_object == "candidate_2", panel.error_label.text()
        QTest.mouseClick(panel.prev_btn, QtCore.Qt.LeftButton)
        app.processEvents()
        assert a.STATE.current_object == "candidate_1"
        # Open the real More menu; capture after native painting.
        def capture_menu():
            rect = panel.window().frameGeometry().united(panel.more_button.menu().frameGeometry())
            app.primaryScreen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(output.with_name(output.stem + "_menu.png")))
            panel.more_button.menu().close()
        QtCore.QTimer.singleShot(300, capture_menu)
        QTest.mouseClick(panel.more_button, QtCore.Qt.LeftButton)
        app.processEvents()
        window.repaint()
        panel.window().repaint()
        QTest.qWait(200)
        rect = panel.window().frameGeometry()
        app.primaryScreen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(output.with_suffix(".png")))
        panel.close()
        flush_writes()
        assert set(cmd.get_names("objects")) == scene_names, set(cmd.get_names("objects")) ^ scene_names
        assert "review_parent" not in cmd.get_names("objects", enabled_only=1)
        assert cmd.index("user_selection") == selection_before
        return {"status": "passed", "pymol": cmd.get_version()[0], "file_candidates": file_result,
                "scene_groups": {"distance_and_labels_preserved": True, "closed_parent_restored": True,
                    "annotations_reopened": True, "gui_next_previous": True, "external_objects_preserved": True},
                "switches": 30, "query_counts": calls, "elapsed_ms": elapsed,
                "candidate_query_comparison": performance,
                "per_switch_ms": elapsed / 30, "screenshot": str(output.with_suffix(".png"))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixed-output-check", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    try:
        result = fixed_output_check(args.output) if args.fixed_output_check else run(args.output)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), file=sys.__stdout__, flush=True)
    from pymol import cmd
    cmd.quit(0 if result["status"] == "passed" else 1)

