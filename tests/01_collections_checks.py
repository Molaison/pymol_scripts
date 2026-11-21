"""Reproducible Collections checks in the actual PyMOL Python installation."""

import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--stage", required=True, choices=("math", "native", "regression", "qt", "all"))
parser.add_argument("--output", type=Path, default=Path("output/01_collections_checks"))
parser.add_argument("--hidden-qt-child", action="store_true", help=argparse.SUPPRESS)
args = parser.parse_args()
repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository))
from workflows.dataset_navigation import collections_core as core

args.output.mkdir(parents=True, exist_ok=True)
results = {}

# %% Numeric contract: joint strata, ties, rigid transforms and stable axes.
if args.stage in ("math", "all"):
    rng = np.random.default_rng(28)
    points = rng.normal(size=(40, 3)) * [7, 3, 1]
    rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(rotation) < 0:
        rotation[:, 0] *= -1
    transformed = points @ rotation + [40, -70, 12]
    fitted, shift = core.rigid_fit(transformed, points)
    np.testing.assert_allclose(transformed @ fitted + shift, points, atol=1e-10)
    center, axes = core.pca_frame(points)
    new_center, new_axes = core.pca_frame(transformed)
    np.testing.assert_allclose((points - center) @ axes,
                               (transformed - new_center) @ new_axes, atol=1e-10)
    centroids = np.array([[10, 0, 0], [-5, 8.660254, 0], [-5, -8.660254, 0]])
    ring = np.concatenate([points / [7, 3, 1] + c for c in centroids])
    center, axes = core.symmetry_frame(ring, centroids)
    new_center, new_axes = core.symmetry_frame(ring @ rotation + 12, centroids @ rotation + 12)
    np.testing.assert_allclose((ring - center) @ axes,
                               (ring @ rotation + 12 - new_center) @ new_axes, atol=1e-6)
    rows = [{"scores": {"a": float(i // 3), "b": float(i % 3)}} for i in range(9)]
    groups, edges = core.make_strata(rows, [{"name": "a", "bins": 3}, {"name": "b", "bins": 3}], 9)
    assert len(groups) == 9
    assert len({row["stratum"][0] for row in rows if row["scores"]["a"] == 1}) == 1
    constant = [{"scores": {"a": 2}} for _ in range(10)]
    tied_groups, _ = core.make_strata(constant, [{"name": "a", "bins": 4}], 0)
    assert len(tied_groups) == 1
    with tempfile.TemporaryDirectory(dir=str(args.output.resolve())) as temporary:
        temporary = Path(temporary).resolve()
        assert temporary.parent == args.output.resolve()
        for name, text in (("rows.csv", "path,quality\na.pdb,0.8\n"),
                           ("rows.tsv", "path\tquality\na.pdb\t0.8\n"),
                           ("rows.json", '[{"path":"a.pdb","quality":0.8}]'),
                           ("rows.sc", "SEQUENCE: AAA\nSCORE: total_score quality description\n"
                            "SCORE: -30.2 0.8 a\n")):
            (temporary / name).write_text(text, encoding="utf-8")
            parsed = core.read_scores(temporary / name)
            assert len(parsed) == 1 and float(parsed[0]["quality"]) == 0.8
    results["math"] = {"rigid_fit": "PASS", "pca_rotation_invariance": "PASS",
                       "symmetry_rotation_invariance": "PASS", "joint_cells": len(groups),
                       "constant_score_cells": len(tied_groups), "csv_tsv_json_rosetta": "PASS"}
    print("PASS math: rigid fit, PCA/symmetry stability, joint quantiles and ties")

# %% Native fixture: derive a two-chain complex from the repository's real PDB.
if args.stage in ("native", "all"):
    import pymol2
    from PIL import Image

    fixture = args.output / "01_inputs"
    fixture.mkdir(exist_ok=True)
    records = []
    with pymol2.PyMOL() as pm:
        cmd = pm.cmd
        cmd.load(str(repository / "data/helical_bundle.pdb"), "col_source")
        chain = cmd.get_chains("col_source")[0]
        cmd.create("col_target", f'model col_source and chain "{chain}"')
        cmd.alter("col_target", "chain='A'")
        cmd.create("col_binder", "col_target")
        cmd.alter("col_binder", "chain='B'")
        cmd.translate([18, 0, 0], "col_binder")
        cmd.create("col_base", "col_target or col_binder")
        base_coords = cmd.get_coords("col_base", 1)
        binder_indices = [i for i, atom in enumerate(cmd.get_model("col_base", 1).atom) if atom.chain == "B"]
        for index, amplitude in enumerate((0, 0, 8, 8, 18, 18)):
            cmd.create("col_variant", "col_base")
            coords = base_coords.copy()
            z = coords[binder_indices, 2]
            coords[binder_indices, 0] += amplitude * np.sin((z - z.min()) / max(np.ptp(z), 1) * np.pi)
            coords[binder_indices, 1] += amplitude * np.cos((z - z.min()) / max(np.ptp(z), 1) * np.pi)
            if index % 2:
                coords = coords @ rotation + [15, -20, 40] if args.stage == "all" else coords + [15, -20, 40]
            cmd.load_coords(coords, "col_variant", 1)
            path = fixture / f"sample_{index}.pdb"
            cmd.save(str(path), "col_variant")
            records.append({"path": path.name, "quality": index // 2, "confidence": index // 2})
            cmd.delete("col_variant")
        cmd.create("col_pose", "col_base")
        cmd.translate([0, 10, 0], "col_pose and chain B")
        shape = core.structural_distance(cmd, "col_pose", "col_base",
                                         {"binder": "chain B", "diversity": "shape"})
        pose = core.structural_distance(cmd, "col_pose", "col_base",
                                        {"binder": "chain B", "target": "chain A", "diversity": "pose"})
        assert shape < 1e-4 and pose > 9.9, (shape, pose)
        cmd.create("col_reference", "col_base")
        for mode in ("binder", "pca", "symmetry"):
            options = {"orientation": mode, "binder": "chain B", "target": "chain A",
                       "frame_selection": "polymer.protein", "symmetry_axis": [0, 0, 1]}
            matrix = core.canonical_pose(cmd, "col_pose", options)
            assert np.all(np.isfinite(matrix)) and abs(np.linalg.det(matrix[:3, :3]) - 1) < 1e-6
            if mode == "binder":
                mobile = cmd.get_coords("col_pose and chain B and name CA", 1)
                reference = cmd.get_coords("col_reference and chain B and name CA", 1)
                center, axes = core.pca_frame(reference)
                np.testing.assert_allclose(mobile @ matrix[:3, :3].T + matrix[:3, 3],
                                           (reference - center) @ axes, atol=1e-4)
            if mode == "symmetry":
                np.testing.assert_allclose(abs(matrix[:3, :3] @ [0, 0, 1]), [0, 1, 0], atol=1e-6)
        for index, angle in enumerate((0, 2 * np.pi / 3, 4 * np.pi / 3)):
            obj = f"col_ring_{index}"
            cmd.create(obj, "col_target")
            cmd.alter(obj, f"chain='{chr(65 + index)}'")
            coords = cmd.get_coords(obj, 1)
            coords -= cmd.get_coords(f"{obj} and name CA", 1).mean(0)
            coords += [10 * np.cos(angle), 10 * np.sin(angle), 0]
            cmd.load_coords(coords, obj, 1)
        cmd.create("col_symmetric", "col_ring_0 or col_ring_1 or col_ring_2")
        automatic = core.canonical_pose(cmd, "col_symmetric", {"orientation": "symmetry"})
        chain_centers = np.array([cmd.get_coords(f"col_symmetric and chain {chain} and name CA", 1).mean(0)
                                  for chain in ("A", "B", "C")])
        assert np.ptp((chain_centers @ automatic[:3, :3].T + automatic[:3, 3])[:, 1]) < 1e-4
    scores = fixture / "scores.csv"
    with scores.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "quality", "confidence"])
        writer.writeheader()
        writer.writerows(records)
    config = {"scores": str(scores.resolve()), "root": str(fixture.resolve()), "path_column": "path",
              "metrics": [{"name": "quality", "bins": 3}, {"name": "confidence", "bins": 3}],
              "count": 6, "seed": 7, "rmsd": 2, "coverage": 0.8, "diversity": "shape",
              "target": "chain A", "binder": "chain B", "orientation": "target"}
    collection = args.output / "02_collection"
    manifest = core.create_collection(config, collection)
    assert len(manifest["samples"]) == 3, manifest
    assert manifest["rejected_duplicates"] == 3 and manifest["shortfall"] == 3
    assert manifest["covered_strata"] == 3 and not manifest["invalid_structures"]
    with pymol2.PyMOL() as pm:
        first_anchor = None
        for i, row in enumerate(manifest["samples"]):
            obj = f"col_check_{i}"
            pm.cmd.load(row["path"], obj)
            core.apply_pose(pm.cmd, obj, row["matrix"])
            anchor = pm.cmd.get_coords(f"{obj} and chain A and name CA", 1)
            if first_anchor is None:
                first_anchor = anchor
            np.testing.assert_allclose(anchor, first_anchor, atol=0.003)
    exported = core.export_collection(collection / "01_collection.json", workers=2, width=640, height=480)
    assert len(exported["results"]) == 3 and all(row["ok"] for row in exported["results"]), exported
    assert len({row["pid"] for row in exported["processes"]}) == 2
    first, second = exported["processes"]
    assert max(first["started"], second["started"]) < min(first["finished"], second["finished"])
    for row in exported["results"]:
        with Image.open(row["image"]) as image:
            assert image.size == (640, 480)
            pixels = np.asarray(image.convert("RGB"))
            assert np.mean(np.any(pixels < 220, axis=2)) > 0.005
    with tempfile.TemporaryDirectory(dir=str(args.output.resolve())) as temporary:
        temporary = Path(temporary).resolve()
        assert temporary.parent == args.output.resolve()
        external = core.analyze_external(config, temporary)
        assert [row["path"] for row in external["samples"]] == [row["path"] for row in manifest["samples"]]
        failed = core.export_collection(temporary / "01_collection.json", workers=2, width=128, height=128,
                                         python=str(temporary / "missing_python.exe"))
        assert len(failed["results"]) == 3 and all(not row["ok"] for row in failed["results"])
    results["native"] = {"selected": 3, "duplicates_rejected": 3, "shortfall_reported": 3,
                         "shape_rmsd": shape, "pose_rmsd": pose, "target_alignment": "PASS",
                         "binder_pca_symmetry_native": "PASS",
                         "external_analysis": "PASS", "failed_worker_reporting": "PASS",
                         "concurrent_processes": 2, "png_dimensions": [640, 480]}
    print("PASS native: duplicate rejection, pose/shape distinction, target alignment, 2 overlapping ray processes")

# %% Real Qt widgets rendered without opening a desktop window.
if args.stage in ("regression", "all"):
    import pymol2
    from PIL import Image

    original_manifest = core.read_json(args.output / "02_collection/01_collection.json")
    with tempfile.TemporaryDirectory(dir=str(args.output.resolve())) as temporary:
        temporary = Path(temporary).resolve()
        assert temporary.parent == args.output.resolve()
        source = args.output.resolve() / "01_inputs/sample_0.pdb"
        different = args.output.resolve() / "01_inputs/sample_2.pdb"
        with pymol2.PyMOL() as instance:
            command = instance.cmd
            command.load(str(source), "check_full")
            command.create("check_short", "check_full")
            command.remove("check_short and chain B and not resi 1-100")
            short_path = temporary / "short.pdb"
            command.save(str(short_path), "check_short")
            assert core.structural_distance(command, "check_full", "check_short",
                                            {"binder": "chain B"}) is None
            command.load(str(different), "check_bad")
            command.remove("check_bad and chain A")
            bad_path = temporary / "bad.pdb"
            command.save(str(bad_path), "check_bad")
        scores = temporary / "scores.json"
        core.write_json(scores, [{"path": str(source), "score": 1}, {"path": str(short_path), "score": 2}])
        config = {"scores": str(scores), "metrics": [{"name": "score", "bins": 2}],
                  "count": 2, "seed": 0, "reference": str(source), "orientation": "target",
                  "target": "chain A", "binder": "chain B"}
        strict = core.create_collection(config, temporary / "strict")
        assert len(strict["samples"]) == 1 and len(strict["unverified_candidates"]) == 1
        allowed = core.create_collection(dict(config, allow_unverified=True), temporary / "allowed")
        assert len(allowed["samples"]) == 2
        assert allowed["samples"][1]["diversity_status"] == "unverified"
        core.write_json(scores, [{"path": str(source), "score": 1}, {"path": str(bad_path), "score": 2}])
        partial = core.create_collection(dict(config, reference=""), temporary / "partial")
        assert len(partial["samples"]) == 1 and len(partial["invalid_structures"]) == 1
        assert (temporary / "partial/01_collection.json").is_file()

        editing = temporary / "editing"
        initial = core.create_collection(dict(original_manifest["config"], count=2, orientation="target"), editing)
        manifest_path = editing / "01_collection.json"
        original_paths = [row["path"] for row in initial["samples"]]
        updated = core.revise_external(manifest_path, {"action": "orient",
                                                       "config": {"orientation": "binder"}})
        assert updated["config"]["orientation"] == "binder"
        assert [row["path"] for row in updated["samples"]] == original_paths
        assert any(not np.allclose(before["matrix"], after["matrix"])
                   for before, after in zip(initial["samples"], updated["samples"]))
        pinned = core.revise_collection(manifest_path, {"action": "pin", "indices": [0]})
        for action in ("remove", "replace"):
            try:
                core.revise_collection(manifest_path, {"action": action, "indices": [0]})
            except ValueError:
                pass
            else:
                raise AssertionError("Pinned sample must be protected")
            assert core.read_json(manifest_path) == pinned
        replaced = core.revise_collection(manifest_path, {"action": "replace", "indices": [1]})
        assert replaced["samples"][0]["path"] == pinned["samples"][0]["path"]
        assert replaced["samples"][0]["pinned"]
        assert replaced["samples"][1]["path"] not in original_paths
        removed = core.revise_collection(manifest_path, {"action": "remove", "indices": [1]})
        assert len(removed["samples"]) == 1 and removed["samples"][0]["pinned"]

        camera_path = temporary / "camera.json"
        camera_manifest = json.loads(json.dumps(original_manifest))
        with pymol2.PyMOL() as instance:
            command = instance.cmd
            command.set_view(core.common_view(*camera_manifest["bounds"], 640, 480))
            command.turn("x", 23)
            command.turn("y", 41)
            camera_manifest["view"] = list(command.get_view())
        core.write_json(camera_path, camera_manifest)
        portrait = core.export_collection(camera_path, workers=2, width=160, height=400)
        assert all(row["ok"] for row in portrait["results"])
        for row in portrait["results"]:
            with Image.open(row["image"]) as image:
                mask = np.any(np.asarray(image.convert("RGB")) < 220, axis=2)
                assert mask.sum() > 50
                assert not mask[:, :2].any() and not mask[:, -2:].any()
                assert not mask[:2, :].any() and not mask[-2:, :].any()

        retry_directory = temporary / "retry"
        retry_directory.mkdir()
        retry_path = retry_directory / "01_collection.json"
        retry_manifest = json.loads(json.dumps(original_manifest))
        absent = retry_directory / "temporarily_missing.pdb"
        real_input = retry_manifest["samples"][1]["path"]
        retry_manifest["samples"][1]["path"] = str(absent)
        core.write_json(retry_path, retry_manifest)
        failed = core.export_collection(retry_path, workers=2, width=192, height=144)
        successful = {row["index"]: Path(row["image"]).stat().st_mtime_ns
                      for row in failed["results"] if row["ok"]}
        assert len(successful) == 2 and sum(not row["ok"] for row in failed["results"]) == 1
        shutil.copyfile(real_input, absent)
        retried = core.export_collection(retry_path, workers=2, width=192, height=144, retry_failed=True)
        assert retried["reused"] == 2 and all(row["ok"] for row in retried["results"])
        for index, timestamp in successful.items():
            assert Path(retried["results"][index]["image"]).stat().st_mtime_ns == timestamp

        cancellation_directory = temporary / "cancel"
        cancellation_directory.mkdir()
        cancellation_path = cancellation_directory / "01_collection.json"
        cancellation_manifest = json.loads(json.dumps(original_manifest))
        cancellation_manifest["samples"] = []
        for index in range(9):
            row = dict(original_manifest["samples"][index % 3])
            row["image"] = f"sample_{index}.png"
            cancellation_manifest["samples"].append(row)
        core.write_json(cancellation_path, cancellation_manifest)
        control = core.TaskControl()
        observed_progress = []
        def cancel_after_first(completed, total):
            observed_progress.append(completed)
            if 0 < completed < total:
                # Report a completed image after it reaches its public path.
                assert len(list((cancellation_directory / 'images').glob('sample_*.png'))) >= completed
                control.cancelled.set()
        cancelled = core.export_collection(cancellation_path, workers=1, width=1280, height=960,
                                            control=control, progress=cancel_after_first)
        assert cancelled["cancelled"] and not control.processes
        assert any(0 < completed < 9 for completed in observed_progress)
        kept = {row["index"]: Path(row["image"]).stat().st_mtime_ns
                for row in cancelled["results"] if row["ok"]}
        assert 0 < len(kept) < 9
        resumed = core.export_collection(cancellation_path, workers=2, width=1280, height=960,
                                         retry_failed=True)
        assert all(row["ok"] for row in resumed["results"]) and resumed["reused"] == len(kept)
        for index, timestamp in kept.items():
            assert Path(resumed["results"][index]["image"]).stat().st_mtime_ns == timestamp
    results["regression"] = {"incomparable_excluded": "PASS", "explicit_unverified_opt_in": "PASS",
                             "bad_target_partial_collection": "PASS", "reorient_same_samples": "PASS",
                             "pin_remove_replace": "PASS", "rotated_portrait_no_clipping": "PASS",
                             "failed_retry_preserves_success": "PASS", "live_progress": "PASS",
                             "cancel_join_resume": "PASS"}
    print("PASS regression: diversity, partial results, reorientation, editing, framing, retry and cancellation")

if args.stage in ("qt", "all") and os.name == "nt" and not args.hidden_qt_child:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    desktop_name = f"CollectionsCheck_{os.getpid()}"
    desktop = user32.CreateDesktopW(desktop_name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = subprocess.STARTUPINFO()
        startup.lpDesktop = desktop_name
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--stage", "qt",
                                "--output", str(args.output.resolve()), "--hidden-qt-child"],
                               startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
                               capture_output=True, text=True, timeout=60)
        print(child.stdout)
        if child.returncode:
            raise RuntimeError(child.stderr)
        results["qt"] = json.loads((args.output / "04_qt_results.json").read_text(encoding="utf-8"))["qt"]
    finally:
        user32.CloseDesktop(desktop)

if args.stage in ("qt", "all") and (args.hidden_qt_child or os.name != "nt"):
    os.environ["QT_QPA_PLATFORM"] = "windows" if os.name == "nt" else "offscreen"
    from pymol.Qt import QtCore, QtGui, QtWidgets
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    spec = importlib.util.spec_from_file_location("col_panel_test", repository / "plugins/structure_collections.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    panel = plugin.CollectionDialog()
    panel.sampling.table.populate([{"path": "a.pdb", "quality": 0.8, "confidence": 0.9}])
    panel.sampling.table.item(0, 0).setCheckState(QtCore.Qt.Checked)
    assert panel.sampling.table.specifications()[0]["name"] == "quality"
    manifest_path = args.output / "02_collection/01_collection.json"
    if not manifest_path.exists():
        raise SystemExit("qt stage needs native stage's collection output")
    panel.load_manifest(manifest_path)
    assert panel.samples.count() == 3
    assert panel.rendering.orientation.currentData() == panel.manifest["config"]["orientation"]
    assert panel.sampling.count.value() == panel.manifest["config"]["count"]
    from pymol import CmdException, cmd
    if sys.platform == "darwin":
        from pymol2 import SingletonPyMOL
        pymol_instance = SingletonPyMOL()
        pymol_instance.start()
    cmd.run(str(repository / "plugins/structure_collections.py"))
    cmd.fragment("ala", "col_original_scene")
    original_view = tuple(cmd.get_view())
    original_enabled = cmd.get_names("objects", enabled_only=1)
    panel.preview(0)
    assert cmd.get_names("objects", enabled_only=1) == [panel.scene.object_name]
    current_preview = panel.scene.object_name
    current_coordinates = cmd.get_coords(current_preview, 1).copy()
    current_view = tuple(cmd.get_view())
    next_sample = panel.manifest["samples"][1]
    next_path = next_sample["path"]
    try:
        with tempfile.TemporaryDirectory() as temporary:
            unavailable = str(Path(temporary) / "missing.pdb")
            for invalid_path in (panel.manifest["samples"][0]["path"], unavailable):
                next_sample["path"] = invalid_path
                try:
                    panel.preview(1)
                except (ValueError, CmdException):
                    pass
                else:
                    raise AssertionError(f"Unexpectedly previewed {invalid_path}")
                assert panel.scene.object_name == current_preview
                assert cmd.get_names("objects", enabled_only=1) == [current_preview]
                np.testing.assert_array_equal(cmd.get_coords(current_preview, 1), current_coordinates)
                np.testing.assert_array_equal(cmd.get_view(), current_view)
    finally:
        next_sample["path"] = next_path

    import pymol2

    with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as analysis:
        source = Path(temporary) / "reordered.pdb"
        analysis.cmd.fragment("ala", "fixture")
        atoms = [line for line in analysis.cmd.get_pdbstr("fixture").splitlines() if line.startswith("ATOM")]
        source.write_text("\n".join(reversed(atoms)) + "\nEND\n", encoding="ascii")
        analysis.cmd.load(str(source), "worker_order")
        signature = core.coordinate_signature(analysis.cmd.get_coords("worker_order", 1))
        analysis.cmd.set("retain_order", 1, "worker_order")
        source_coordinates = analysis.cmd.get_coords("worker_order", 1)
        assert core.coordinate_signature(source_coordinates) != signature
        original_retain_order = cmd.get_setting_boolean("retain_order")
        panel.manifest["samples"][1] = dict(next_sample, path=str(source),
                                             coordinate_signature=signature, matrix=np.eye(4).tolist())
        cmd.set("retain_order", 1)
        try:
            panel.preview(1)
            assert cmd.get_setting_boolean("retain_order")
            np.testing.assert_array_equal(cmd.get_coords(panel.scene.object_name, 1), source_coordinates)
        finally:
            cmd.set("retain_order", original_retain_order)
            panel.manifest["samples"][1] = next_sample
    panel.preview(0)
    panel.capture_view()
    assert "view" in json.loads(manifest_path.read_text(encoding="utf-8"))
    def finish_ui_task():
        deadline = time.monotonic() + 20
        while panel.task.future is not None and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.01)
        assert panel.task.future is None, "UI task did not finish"
    finish_ui_task()
    assert len(core.current_images(panel.manifest, manifest_path.parent / "previews")) == 3
    panel.restore_scene()
    assert cmd.get_names("objects", enabled_only=1) == original_enabled
    np.testing.assert_allclose(cmd.get_view(), original_view)
    panel.rendering.orientation.setCurrentIndex(panel.rendering.orientation.findData("binder"))
    panel.rendering.width.setValue(320)
    panel.rendering.height.setValue(480)
    panel.export()
    finish_ui_task()
    assert panel.manifest["config"]["orientation"] == "binder"
    assert panel.manifest["config"]["width"] == 320 and panel.manifest["config"]["height"] == 480
    assert len(core.current_images(panel.manifest, manifest_path.parent / "images", 320, 480)) == 3
    previous_signature = core.current_images(panel.manifest, manifest_path.parent / "previews")[0]["signature"]
    panel.preview(0)
    cmd.turn("x", 40)
    panel.capture_view()
    finish_ui_task()
    assert core.current_images(panel.manifest, manifest_path.parent / "previews")[0]["signature"] != previous_signature
    assert all(not panel.samples.item(index).icon().isNull() for index in range(3))
    panel.samples.item(0).setSelected(True)
    previous_pin = panel.manifest["samples"][0].get("pinned", False)
    panel.actions.edit("pin")
    finish_ui_task()
    assert panel.manifest["samples"][0]["pinned"] != previous_pin
    assert not panel.findChildren(QtWidgets.QTabWidget)
    assert panel.sampling.title() == "数据与采样" and panel.rendering.title() == "朝向与导出"
    panel.show()
    application.processEvents()
    image_path = args.output / "03_collections_panel.png"
    assert panel.grab().save(str(image_path))
    panel.rendering.width.setValue(2048)
    panel.rendering.height.setValue(1536)
    panel.export()
    deadline = time.monotonic() + 5
    while not panel.task.control.processes and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    panel.close()
    assert panel.task.control.cancelled.is_set()
    finish_ui_task()
    assert not panel.task.control.processes and not panel.isVisible()
    panel.task.executor.shutdown()
    results["qt"] = {"score_selection": "PASS", "manifest_sample_count": 3,
                     "preview_restore_capture": "PASS", "cmd_run_registration": "PASS",
                     "preview_failures_keep_scene": "PASS",
                     "preview_with_retained_atom_order": "PASS",
                     "form_restore_and_apply": "PASS", "capture_refreshes_thumbnails": "PASS",
                     "pin_ui": "PASS", "close_cancels_and_joins_workers": "PASS",
                     "native_groupbox_style_without_tabs": "PASS",
                     "widget_render": str(image_path.resolve()),
                     "platform": os.environ["QT_QPA_PLATFORM"], "input_desktop_switched": False}
    print("PASS qt: score table, cmd.run, preview/capture/restore, real panel rendering")

core.write_json(args.output / f"04_{args.stage}_results.json", results)
