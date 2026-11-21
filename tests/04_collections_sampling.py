"""Regression cases for score screening, stratified sampling and native CA matching."""

import argparse
from collections import Counter
import csv
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import pymol2


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "data/helical_bundle.pdb"
CORE_PATH = "workflows/dataset_navigation/collections_core.py"
sys.path.insert(0, str(REPO))
from workflows.dataset_navigation import collections_core as core


def core_at(revision):
    blob = subprocess.check_output(["git", "show", f"{revision}:{CORE_PATH}"], cwd=REPO)
    module = types.ModuleType("baseline_collections_core")
    module.__file__ = str(REPO / CORE_PATH)
    exec(compile(blob, module.__file__, "exec"), vars(module))
    return module


class CollectionsSampling(unittest.TestCase):
    def test_rare_extreme_on_either_side_keeps_separate_stratum(self):
        specs = [{"name": "quality", "bins": 3}]
        for values in ([0.7] * 99 + [0.98], [0.7] + [0.98] * 99):
            with self.subTest(rare=min(values, key=values.count)):
                rows = [{"scores": {"quality": value}} for value in values]
                queues, _ = core.make_strata(rows, specs, seed=0)
                available = {queue[-1]["scores"]["quality"]: len(queue) for queue in queues}
                self.assertEqual(available, dict(Counter(values)))

    def test_low_ties_preserve_an_effective_interpolated_cut_for_later_rows(self):
        specs = [{"name": "quality", "bins": 3}]
        originals = [{"scores": {"quality": value}} for value in [0, 0, 0, 1, 2]]
        _, edges = core.make_strata(originals, specs, seed=0)
        self.assertTrue(any(0 < edge < 0.8 for edge in edges["quality"]))
        candidate = {"scores": {"quality": 0.8}}
        core.make_strata([*originals, candidate], specs, seed=0, saved_edges=edges)
        self.assertEqual(candidate["stratum"], originals[3]["stratum"])
        self.assertNotEqual(candidate["stratum"], originals[0]["stratum"])

    def test_extreme_finite_scores_keep_a_finite_cut_in_the_saved_collection(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            coords = cmd.get_coords("fixture", 1)
            cmd.save(str(root / "low.pdb"), "fixture")
            cmd.load_coords(coords * [1.8, 1.4, 1.35], "fixture", 1)
            cmd.save(str(root / "high.pdb"), "fixture")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": "low.pdb", "quality": -1e308},
                {"path": "high.pdb", "quality": 1e308},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality", "bins": 2}],
                      "count": 2, "seed": 8, "rmsd": 0.15, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            output = root / "collection"
            core.create_collection(config, output)

            def reject_nonfinite(value):
                self.fail(f"non-finite manifest token: {value}")

            manifest = json.loads((output / "01_collection.json").read_text(encoding="utf-8"),
                                  parse_constant=reject_nonfinite)
            self.assertEqual(manifest["quantile_edges"], {"quality": [0.0]})
            self.assertEqual(manifest["occupied_strata"], 2)
            self.assertEqual({row["id"]: row["stratum"] for row in manifest["samples"]},
                             {"low.pdb": [0], "high.pdb": [1]})

    def test_nonfinite_native_coordinates_do_not_exclude_valid_samples(self):
        good_lines = FIXTURE.read_text(encoding="ascii").splitlines(keepends=True)
        for mode, atom in (("target", "CA"), ("pca", "N")):
            with self.subTest(mode=mode, atom=atom), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shutil.copyfile(FIXTURE, root / "valid.pdb")
                bad_lines = good_lines.copy()
                index = next(i for i, line in enumerate(good_lines)
                             if line.startswith("ATOM") and line[12:16].strip() == atom)
                line = good_lines[index]
                bad_lines[index] = line[:30] + "     nan" + line[38:]
                bad = root / "invalid.pdb"
                bad.write_text("".join(bad_lines), encoding="ascii")
                scores = root / "scores.json"
                scores.write_text(json.dumps([
                    {"path": bad.name, "quality": 0},
                    {"path": "valid.pdb", "quality": 1},
                ]), encoding="utf-8")
                config = {"scores": str(scores), "metrics": [{"name": "quality", "bins": 2}],
                          "count": 1, "seed": 0, "rmsd": 0.2, "orientation": mode,
                          "target": "polymer.protein", "binder": "polymer.protein"}
                output = root / "collection"
                core.create_collection(config, output)
                manifest = json.loads((output / "01_collection.json").read_text(encoding="utf-8"),
                                      parse_constant=lambda token: self.fail(f"Nonfinite JSON: {token}"))
                self.assertEqual([row["id"] for row in manifest["samples"]], ["valid.pdb"])
                self.assertEqual(Path(manifest["reference"]).name, "valid.pdb")
                self.assertEqual([item["path"] for item in manifest["invalid_structures"]], [str(bad)])
                self.assertIn("非有限", manifest["invalid_structures"][0]["reason"])

                with pymol2.PyMOL() as instance:
                    cmd = instance.cmd
                    cmd.set("max_threads", 1)
                    cmd.load(str(root / "valid.pdb"), "source", quiet=1)
                    coords = cmd.get_coords("source", 1)
                    coords[0, 0] = float("nan")
                    cmd.load_coords(coords, "source", 1)
                    saved = core.coordinate_signature(coords)
                    with self.assertRaisesRegex(ValueError, "非有限"):
                        core.apply_pose(cmd, "source", manifest["samples"][0]["matrix"],
                                        expected_coordinates=saved)
                    self.assertEqual(core.coordinate_signature(cmd.get_coords("source", 1)), saved)

    def test_failed_implicit_reference_does_not_exclude_valid_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "valid.pdb"
            shutil.copyfile(FIXTURE, valid)
            broken = root / "bad_residue_numbering.pdb"
            broken.write_text("".join(
                line[:22] + "   1" + line[26:]
                for line in FIXTURE.read_text(encoding="ascii").splitlines(keepends=True)
                if line.startswith("ATOM") and line[12:16].strip() == "CA"
            ) + "END\n", encoding="ascii")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": broken.name, "quality": 0}, {"path": valid.name, "quality": 1},
            ]), encoding="utf-8")
            for orientation in ("target", "binder"):
                with self.subTest(orientation=orientation):
                    config = {"scores": str(scores), "metrics": [{"name": "quality", "bins": 2}],
                              "seed": 0, "count": 1, "rmsd": 0.2, "coverage": 1.0,
                              "orientation": orientation, "target": "all", "binder": "all"}
                    collection = core.create_collection(config, root / orientation)
                    self.assertEqual([sample["path"] for sample in collection["samples"]], [str(valid)])
                    self.assertEqual(collection["reference"], str(valid))
                    self.assertEqual([entry["path"] for entry in collection["invalid_structures"]],
                                     [str(broken)])

    def test_nonfinite_legacy_bounds_do_not_reach_the_camera(self):
        row = {"path": str(FIXTURE), "matrix": [], "image": "sample.png"}
        legacy = {"bounds": [[float("nan"), -14, -12], [float("nan"), 14, 12]],
                  "samples": [row], "config": {}}
        with self.assertRaisesRegex(ValueError, "结构边界.*重新应用朝向"):
            core.collection_view(legacy, 640, 480)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image = directory / row["image"]
            image.touch()
            core.write_json(directory / "03_export.json",
                            {"width": 640, "height": 480, "results": [{"ok": True, "image": str(image)}]})
            self.assertEqual(core.current_images(legacy, directory), {})

    def test_native_numeric_prefixes_cannot_overflow_coordinates(self):
        pdb = next(line for line in FIXTURE.read_bytes().splitlines() if line.startswith(b"ATOM"))
        cases = ((b"0x1p+128", True), (b"0x1p128z", True), (b"1e40junk", True), (b"0x1p2", False))
        for token, overflows in cases:
            formats = ((core._check_pdb_coordinates, pdb[:30] + token.rjust(8) + pdb[38:]),
                       (core._check_cif_coordinates, b"data_control\n_atom_site.Cartn_x " + token + b"\n"))
            for check, structure in formats:
                with self.subTest(token=token, check=check.__name__):
                    if overflows:
                        with self.assertRaisesRegex(ValueError, "非有限坐标"):
                            check(structure)
                    else:
                        check(structure)

    def test_pdb_exponents_allowing_a_trailing_decimal_point_are_preflighted(self):
        contents = FIXTURE.read_bytes()
        lines = contents.splitlines(keepends=True)
        row = next(i for i, line in enumerate(lines) if line.startswith(b"ATOM"))
        atom = lines[row]
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            for axis, token in enumerate((b"1.e39", b"-1.e39", b"1.e+39")):
                start = 30 + axis * 8
                lines[row] = atom[:start] + token.rjust(8) + atom[start + 8:]
                data = b"".join(lines)
                for suffix in (".pdb", ".pdb.gz"):
                    with self.subTest(axis=axis, token=token, suffix=suffix):
                        path = root / f"overflow{suffix}"
                        path.write_bytes(gzip.compress(data) if suffix.endswith(".gz") else data)
                        with patch.object(cmd, "read_pdbstr",
                                          side_effect=AssertionError("overflow reached the native loader")) as read:
                            with self.assertRaisesRegex(ValueError, "PDB.*非有限坐标"):
                                core.load_structure(cmd, path, "overflow")
                        read.assert_not_called()
            lines[row] = atom[:30] + b"1.e+1".rjust(8) + atom[38:]
            path = root / "finite.pdb"
            path.write_bytes(b"".join(lines))
            core.load_structure(cmd, path, "finite")
            self.assertEqual(float(cmd.get_coords("model finite", 1)[0, 0]), 10.0)

    def test_mmcif_checks_coordinate_columns_before_native_load(self):
        prefix = b"data_limit\n_atom_site.Cartn_x "
        core._check_cif_coordinates(prefix + b"3.4028235e38\n")
        with self.assertRaisesRegex(ValueError, "mmCIF.*非有限坐标"):
            core._check_cif_coordinates(prefix + b"3.4028235677973366e38\n")

        for separator in (b";\n", b";text touching the terminator\n"):
            with self.subTest(separator=separator):
                scalar = (b"data_control\n_struct.details\n;\n"
                          b"_struct.title contains a literal tag\n" + separator
                          + b"_atom_site.Cartn_x 'nan'\n")
                with self.assertRaisesRegex(ValueError, "mmCIF.*非有限坐标"):
                    core._check_cif_coordinates(scalar)

        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "control", quiet=1)
            source = root / "source.cif"
            cmd.save(str(source), "control", state=1, quiet=1)
            control_signature = core.coordinate_signature(cmd.get_coords("control", 1))
            lines = source.read_text(encoding="utf-8").splitlines()
            # Literals in text items are harmless both before and after the coordinate table.
            block = next(i for i, line in enumerate(lines) if line.startswith("data_"))
            lines.insert(block + 1, "_struct.title 'NaN is a text label'")
            lines.extend(("_struct.details", ";", "Infinity is a text label",
                          "_atom_site.Cartn_x NaN", ";"))
            source.write_text("\n".join(lines) + "\n", encoding="utf-8")
            core.load_structure(cmd, source, "valid")
            self.assertEqual(core.coordinate_signature(cmd.get_coords("valid", 1)), control_signature)

            fields = [line.lower() for line in lines if line.startswith("_atom_site.")]
            x_column = fields.index("_atom_site.cartn_x")
            atom = next(i for i, line in enumerate(lines) if line.startswith("ATOM"))
            original = lines[atom].split()
            unsafe_values = (("nan", ".cif"), ("'+inf'", ".cif.gz"), ("1e40", ".cif"),
                             ("1e40junk", ".cif.gz"), ("0x1p+128", ".cif"))
            for token, suffix in unsafe_values:
                with self.subTest(token=token, suffix=suffix):
                    values = original.copy()
                    values[x_column] = token
                    # CIF loop values can wrap independently of their physical lines.
                    lines[atom] = " ".join(values[:x_column]) + "\n" + " ".join(values[x_column:])
                    content = ("\n".join(lines) + "\n").encode("utf-8")
                    invalid = root / f"invalid{suffix}"
                    invalid.write_bytes(gzip.compress(content) if suffix.endswith(".gz") else content)
                    with self.assertRaisesRegex(ValueError, "mmCIF.*非有限坐标"):
                        core.load_structure(cmd, invalid, "invalid")
                    self.assertNotIn("invalid", cmd.get_names("objects"))

    def test_repeated_metric_keeps_independent_strata_and_saved_boundaries(self):
        specs = [{"name": "quality", "bins": 2}, {"name": "quality", "bins": 4}]
        rows = [{"scores": {"quality": score}} for score in range(12)]
        queues, edges = core.make_strata(rows, specs, seed=0)
        self.assertEqual(len(queues), 4)
        self.assertEqual(edges, [[5.5], [2.75, 5.5, 8.25]])
        self.assertEqual(rows[8]["stratum"], [1, 2])

        refreshed = [{"scores": {"quality": score}} for score in (0, 8, 100)]
        _, reused = core.make_strata(refreshed, specs, seed=0, saved_edges=edges)
        self.assertEqual(reused, edges)
        self.assertEqual(refreshed[1]["stratum"], [1, 2])
        core.make_strata(refreshed, specs, seed=0, saved_edges={"quality": edges[1]})
        self.assertEqual(refreshed[1]["stratum"], [2, 2])

    def test_replacement_uses_saved_strata_after_the_score_table_changes(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            coords = cmd.get_coords("fixture", 1)
            for name, scale in (("old", 1.0), ("alternate", 1.5), ("low1", 2.0),
                                ("low2", 2.2), ("low3", 2.4)):
                cmd.load_coords(coords * [scale, 1, 1], "fixture", 1)
                cmd.save(str(root / f"{name}.pdb"), "fixture")
            scores_path = root / "scores.json"
            scores = {"old": 0.92, "alternate": 0.85, "low1": 0.7, "low2": 0.7, "low3": 0.7}

            def write_scores(names):
                scores_path.write_text(json.dumps([
                    {"path": f"{name}.pdb", "quality": scores[name]} for name in names
                ]), encoding="utf-8")

            write_scores(["low1", "old"])
            config = {"scores": str(scores_path), "root": str(root),
                      "metrics": [{"name": "quality", "bins": 2}], "count": 1,
                      "seed": 1, "rmsd": 0.15, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            manifest = core.create_collection(config, root / "collection")
            self.assertEqual(manifest["samples"][0]["id"], "old.pdb")
            self.assertEqual(manifest["samples"][0]["stratum"], [1])
            write_scores(["old", "low1", "alternate", "low2", "low3"])
            path = root / "collection/01_collection.json"
            request = {"action": "replace", "indices": [0]}
            alternate = root / "alternate.pdb"
            before, original_source = path.read_bytes(), alternate.read_bytes()
            changed = False

            def swap_after_selection(_completed, _total):
                nonlocal changed
                if not changed:
                    alternate.write_bytes((root / "low2.pdb").read_bytes())
                    changed = True

            with self.assertRaisesRegex(ValueError, r"alternate\.pdb"):
                core.revise_collection(path, request, progress=swap_after_selection)
            self.assertTrue(changed)
            self.assertEqual(path.read_bytes(), before)
            alternate.write_bytes(original_source)
            edited = core.revise_collection(path, request)
            self.assertEqual(edited["samples"][0]["id"], "alternate.pdb")
            self.assertEqual(edited["samples"][0]["stratum"], [1])
            self.assertEqual(edited["quantile_edges"], manifest["quantile_edges"])

    def test_sample_edits_preserve_changed_retained_source_until_explicit_reorientation(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            coords = cmd.get_coords("fixture", 1)
            cmd.save(str(root / "kept.pdb"), "fixture")
            cmd.load_coords(coords * [1.25, 1.5, 1.8], "fixture", 1)
            cmd.save(str(root / "other.pdb"), "fixture")
            scores = root / "scores.json"
            entries = [{"path": "kept.pdb", "quality": 0.91}, {"path": "other.pdb", "quality": 0.95}]
            scores.write_text(json.dumps(entries), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}], "count": 2,
                      "seed": 7, "rmsd": 0.15, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            collection = root / "collection"
            initial = core.create_collection(config, collection)
            manifest_path = collection / "01_collection.json"
            kept_index = next(index for index, row in enumerate(initial["samples"]) if row["id"] == "kept.pdb")
            pinned = core.revise_collection(manifest_path, {"action": "pin", "indices": [kept_index]})
            saved_coordinate_signature = pinned["samples"][kept_index]["coordinate_signature"]

            center = coords.mean(axis=0)
            cmd.load_coords(center + 0.55 * (coords - center), "fixture", 1)
            cmd.save(str(root / "kept.pdb"), "fixture")
            cmd.load_coords(coords * [0.7, 2.7, 1.05], "fixture", 1)
            cmd.save(str(root / "alternate.pdb"), "fixture")
            scores.write_text(json.dumps([*entries, {"path": "alternate.pdb", "quality": 0.94}]),
                              encoding="utf-8")
            for action in ("remove", "replace"):
                for legacy in (False, True):
                    with self.subTest(action=action, legacy=legacy):
                        copy = root / f"{action}_{legacy}"
                        shutil.copytree(collection, copy)
                        candidate_path, samples_csv = copy / "01_collection.json", copy / "02_samples.csv"
                        if legacy:
                            saved = core.read_json(candidate_path)
                            for row in saved["samples"]:
                                del row["coordinate_signature"]
                            core.write_json(candidate_path, saved)
                        before = (candidate_path.read_bytes(), samples_csv.read_bytes())
                        message = "缺少坐标签名" if legacy else r"kept\.pdb"
                        with self.assertRaisesRegex(ValueError, message):
                            core.revise_collection(candidate_path, {"action": action, "indices": [1 - kept_index]})
                        self.assertEqual((candidate_path.read_bytes(), samples_csv.read_bytes()), before)
            legacy_path = root / "replace_True/01_collection.json"
            refreshed = core.revise_collection(legacy_path, {"action": "orient", "config": {}})
            kept = refreshed["samples"][kept_index]
            self.assertTrue(kept["pinned"])
            self.assertNotEqual(kept["coordinate_signature"], saved_coordinate_signature)
            edited = core.revise_collection(legacy_path, {"action": "remove", "indices": [1 - kept_index]})
            self.assertEqual([row["id"] for row in edited["samples"]], ["kept.pdb"])

    def test_scores_reject_rows_before_file_probes_and_keep_extension_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "retained.pdb")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": "rejected.pdb", "quality": 0.1},
                {"path": "invalid.pdb", "quality": "NaN"},
                {"path": "retained", "quality": 0.9},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality", "min": 0.8}]}
            probes = []
            is_file = Path.is_file

            def inspect(path):
                probes.append(path.name)
                return is_file(path)

            with patch.object(Path, "is_file", inspect):
                rows, skipped = core.prepare_rows(config)
            self.assertEqual(probes, ["retained", "retained.pdb"])
            self.assertEqual(rows[0]["path"], str((root / "retained.pdb").resolve()))
            self.assertEqual([item["row"] for item in skipped], [2, 3])

    def test_multistate_screening_ignores_atoms_absent_from_first_state(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            first_state = "fixture and resi 1-25+61-80"
            cmd.create("source_single", first_state, 1, 1)
            cmd.create("source_ensemble", first_state, 1, 1)
            cmd.create("source_ensemble", "fixture and resi 1-170", 1, 2)
            cmd.create("source_alternate", first_state, 1, 1)
            cmd.load_coords(cmd.get_coords("source_alternate", 1) * [1.3, 1.2, 1.6], "source_alternate", 1)
            for extension in ("pdb", "cif"):
                with self.subTest(extension=extension):
                    single, ensemble = root / f"single.{extension}", root / f"ensemble.{extension}"
                    cmd.save(str(single), "source_single", state=0)
                    cmd.save(str(ensemble), "source_ensemble", state=0)
                    scores = root / f"scores_{extension}.json"
                    config = {"scores": str(scores), "metrics": [{"name": "quality"}], "count": 2,
                              "rmsd": 0.1, "orientation": "target", "diversity": "pose",
                              "target": "polymer.protein", "binder": "polymer.protein",
                              "reference": str(single)}
                    single_row = {"path": single.name, "quality": 0.91}
                    ensemble_row = {"path": ensemble.name, "quality": 0.90}
                    scores.write_text(json.dumps([single_row]), encoding="utf-8")
                    control = core.create_collection(config, root / f"control_{extension}")
                    scores.write_text(json.dumps([ensemble_row]), encoding="utf-8")
                    result = core.create_collection(config, root / f"only_ensemble_{extension}")
                    self.assertEqual(result["invalid_structures"], [])
                    self.assertEqual(result["samples"][0]["matrix"], control["samples"][0]["matrix"])
                    self.assertEqual(result["samples"][0]["coordinate_signature"],
                                     control["samples"][0]["coordinate_signature"])
                    scores.write_text(json.dumps([single_row, ensemble_row]), encoding="utf-8")
                    compared = core.create_collection(config, root / f"compared_{extension}")
                    self.assertEqual(compared["invalid_structures"], [])
                    self.assertEqual(compared["rejected_duplicates"], 1)
                    self.assertEqual(len(compared["samples"]), 1)
                    alternate = root / f"alternate.{extension}"
                    cmd.save(str(alternate), "source_alternate", state=0)
                    del config["reference"]
                    config["count"] = 1
                    scores.write_text(json.dumps([ensemble_row]), encoding="utf-8")
                    implicit = root / f"implicit_{extension}"
                    initial = core.create_collection(config, implicit)
                    self.assertEqual(initial["reference"], str(ensemble))
                    scores.write_text(json.dumps([ensemble_row, {"path": alternate.name, "quality": 0.89}]),
                                      encoding="utf-8")
                    replaced = core.revise_collection(implicit / "01_collection.json",
                                                      {"action": "replace", "indices": [0]})
                    self.assertEqual(replaced["samples"][0]["path"], str(alternate))

    def test_replacement_checks_screening_inputs_behind_unchanged_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            cmd.alter("fixture", "q = 1.0")
            kept, alternate = root / "kept.pdb", root / "alternate.pdb"
            cmd.save(str(kept), "fixture")
            changed = cmd.get_coords("fixture", 1)
            for index, atom in enumerate(cmd.get_model("fixture", 1).atom):
                if 12 <= int(atom.resi) <= 32:
                    changed[index, 0] += 5
            cmd.load_coords(changed, "fixture", 1)
            cmd.save(str(alternate), "fixture")
            scores = root / "scores.json"
            rows = [{"path": kept.name, "quality": 0.80}]
            scores.write_text(json.dumps(rows), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}], "count": 1,
                      "seed": 1, "rmsd": 5, "orientation": "pca", "binder": "polymer.protein",
                      "diversity_selection": "polymer.protein and q > 0.5"}
            output = root / "collection"
            initial = core.create_collection(config, output)
            manifest, sample_table = output / "01_collection.json", output / "02_samples.csv"
            scores.write_text(json.dumps([*rows, {"path": alternate.name, "quality": 0.82}]), encoding="utf-8")
            request = {"action": "replace", "indices": [0]}
            before = (manifest.read_bytes(), sample_table.read_bytes())
            with self.assertRaisesRegex(ValueError, "没有其他满足分层与结构差异要求的候选"):
                core.revise_collection(manifest, request)
            legacy = core.read_json(manifest)
            legacy["samples"][0].pop("screening_signature", None)
            core.write_json(manifest, legacy)
            with self.assertRaisesRegex(ValueError, "缺少结构筛选与朝向签名"):
                core.revise_collection(manifest, request)
            self.assertEqual(core.read_json(manifest), legacy)
            self.assertEqual(sample_table.read_bytes(), before[1])
            manifest.write_bytes(before[0])

            cmd.load(str(kept), "kept")
            cmd.alter("kept and resi 12-32", "q = 0.0")
            cmd.save(str(kept), "kept")
            cmd.delete("kept")
            cmd.load(str(kept), "kept")
            self.assertEqual(core.coordinate_signature(cmd.get_coords("kept", 1)),
                             initial["samples"][0]["coordinate_signature"])
            with self.assertRaisesRegex(ValueError, "结构筛选或朝向输入已变化"):
                core.revise_collection(manifest, request)
            self.assertEqual((manifest.read_bytes(), sample_table.read_bytes()), before)
            core.revise_collection(manifest, {"action": "orient", "config": {}})
            updated = core.revise_collection(manifest, request)
            self.assertEqual(updated["samples"][0]["id"], alternate.name)

    def test_reopened_replacement_uses_same_frame_selections_as_screened_model(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            cmd.alter("fixture", "q = 1.0")
            kept, alternate = root / "kept.pdb", root / "alternate.pdb"
            cmd.save(str(kept), "fixture")
            cmd.load_coords(cmd.get_coords("fixture", 1) * [1.1, 1.6, 1.8], "fixture", 1)
            cmd.save(str(alternate), "fixture")
            scores = root / "scores.json"
            rows = [{"path": kept.name, "quality": 0.80}]
            scores.write_text(json.dumps(rows), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}], "count": 1,
                      "seed": 1, "rmsd": 0.1, "orientation": "pca", "binder": "polymer.protein",
                      "frame_selection": "polymer.protein and q > 0.5"}
            output = root / "collection"
            core.create_collection(config, output)
            manifest, csv_file = output / "01_collection.json", output / "02_samples.csv"
            before = (manifest.read_bytes(), csv_file.read_bytes())
            scores.write_text(json.dumps([*rows, {"path": alternate.name, "quality": 0.82}]), encoding="utf-8")
            choose = core.select_diverse

            def change_frame_after_selection(*args, **kwargs):
                result = choose(*args, **kwargs)
                self.assertEqual(result[0][-1]["id"], alternate.name)
                cmd.alter("fixture and resi 1-12", "q = 0.0")
                cmd.save(str(alternate), "fixture")
                return result

            request = {"action": "replace", "indices": [0]}
            with patch.object(core, "select_diverse", change_frame_after_selection):
                with self.assertRaisesRegex(ValueError, "候选结构在筛选后发生变化"):
                    core.revise_collection(manifest, request)
            self.assertEqual((manifest.read_bytes(), csv_file.read_bytes()), before)
            self.assertEqual(core.revise_collection(manifest, request)["samples"][0]["id"], alternate.name)

    def test_sample_edits_do_not_adopt_a_changed_alignment_reference(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "fixture")
            coords = cmd.get_coords("fixture", 1)
            reference = root / "reference.pdb"
            cmd.save(str(reference), "fixture")
            original_reference = reference.read_bytes()
            for name, scale in (("kept", [1, 1, 1]), ("other", [1.25, 1.5, 1.8]),
                                ("alternate", [0.8, 2.8, 0.95])):
                cmd.load_coords(coords * scale, "fixture", 1)
                cmd.save(str(root / f"{name}.pdb"), "fixture")
            scores = root / "scores.json"
            entries = [{"path": "kept.pdb", "quality": 0.91}, {"path": "other.pdb", "quality": 0.95}]
            scores.write_text(json.dumps(entries), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}], "count": 2,
                      "seed": 7, "rmsd": 0.15, "orientation": "target", "reference": str(reference),
                      "target": "polymer.protein and resi 1-150 and q > 0.5",
                      "binder": "polymer.protein"}
            collection = root / "collection"
            initial = core.create_collection(config, collection)
            index = next(i for i, row in enumerate(initial["samples"]) if row["id"] == "kept.pdb")
            path = collection / "01_collection.json"
            pinned = core.revise_collection(path, {"action": "pin", "indices": [index]})
            expected = pinned["samples"][index]
            removed_manifest = root / "removed/01_collection.json"
            shutil.copytree(collection, removed_manifest.parent)
            legacy = core.read_json(removed_manifest)
            legacy.pop("reference_frame_signature", None)
            core.write_json(removed_manifest, legacy)

            cmd.load_coords(coords * [2.8, 1.1, 0.6], "fixture", 1)
            cmd.save(str(reference), "fixture")
            removed = core.revise_collection(removed_manifest, {"action": "remove", "indices": [1 - index]})
            self.assertEqual(removed["samples"], [expected])
            scores.write_text(json.dumps([*entries, {"path": "alternate.pdb", "quality": 0.94}]),
                              encoding="utf-8")
            before = (path.read_bytes(), (collection / "02_samples.csv").read_bytes())
            request = {"action": "replace", "indices": [1 - index]}
            changes = {
                "coordinates": None,
                "residue numbers": ("fixture", "resi = str(int(resi) + 10)"),
                "occupancy": ("fixture and resi 3-12", "q = 0.0"),
            }
            for label, edit in changes.items():
                with self.subTest(reference_change=label):
                    path.write_bytes(before[0])
                    (collection / "02_samples.csv").write_bytes(before[1])
                    if edit is not None:
                        reference.write_bytes(original_reference)
                        cmd.delete("fixture")
                        cmd.load(str(reference), "fixture")
                        cmd.alter(*edit)
                        cmd.save(str(reference), "fixture")
                        self.assertEqual(cmd.get_coords("fixture", 1).tolist(), coords.tolist())
                    with self.assertRaisesRegex(ValueError, "参考结构.*已变化"):
                        core.revise_collection(path, request)
                    self.assertEqual((path.read_bytes(), (collection / "02_samples.csv").read_bytes()), before)
            path.write_bytes(before[0])
            (collection / "02_samples.csv").write_bytes(before[1])
            cmd.load_coords(coords * [2.8, 1.1, 0.6], "fixture", 1)
            cmd.save(str(reference), "fixture")
            legacy = core.read_json(path)
            del legacy["reference_frame_signature"]
            core.write_json(path, legacy)
            with self.assertRaisesRegex(ValueError, "缺少参考结构朝向签名"):
                core.revise_collection(path, request)
            refreshed = core.revise_collection(path, {"action": "orient", "config": {}})
            revised = core.revise_collection(path, request)
            self.assertEqual(revised["samples"][index], refreshed["samples"][index])
            self.assertNotEqual(revised["samples"][index]["matrix"], expected["matrix"])
            self.assertEqual(revised["samples"][1 - index]["id"], "alternate.pdb")

    @unittest.skipUnless(sys.platform == "win32", "requires Windows file sharing modes")
    def test_collection_save_preserves_both_files_when_publication_fails(self):
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, csv_path = root / "01_collection.json", root / "02_samples.csv"
            manifest = {"config": {"metrics": [{"name": "quality"}]}, "samples": [
                {"id": "old", "path": "old.pdb", "stratum": [0], "nearest_rmsd": None,
                 "scores": {"quality": 0.8}}
            ]}
            core.save_collection(manifest_path, manifest)
            original = (manifest_path.read_bytes(), csv_path.read_bytes())
            manifest["samples"][0]["id"] = "updated"
            for unavailable in (csv_path, manifest_path):
                with self.subTest(unavailable=unavailable.name):
                    # Read-only sharing keeps the file readable but disallows replacing it.
                    handle = api.CreateFileW(str(unavailable), 0x80000000, 1, None, 3, 0, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        with self.assertRaises(PermissionError):
                            core.save_collection(manifest_path, manifest)
                        self.assertEqual((manifest_path.read_bytes(), csv_path.read_bytes()), original)
                    finally:
                        api.CloseHandle(handle)
            core.save_collection(manifest_path, manifest)
            self.assertEqual(core.read_json(manifest_path)["samples"][0]["id"], "updated")
            self.assertEqual(core.read_scores(csv_path)[0]["id"], "updated")

    def test_nonfinite_bounds_are_rejected_before_screening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "candidate.pdb")
            scores = root / "scores.csv"
            scores.write_text("path,quality\ncandidate.pdb,0.5\n", encoding="utf-8")
            for field in ("min", "max"):
                for value in (float("nan"), float("inf"), -float("inf")):
                    with self.subTest(field=field, value=value):
                        metric = {"name": "quality", field: value}
                        with self.assertRaisesRegex(ValueError, "有限"):
                            core.prepare_rows({"scores": str(scores), "metrics": [metric]})

    def test_each_bound_for_the_same_metric_is_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("low.pdb", "high.pdb"):
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.csv"
            scores.write_text("path,quality\nlow.pdb,0.2\nhigh.pdb,0.9\n", encoding="utf-8")
            metrics = [{"name": "quality", "max": 0.5}, {"name": "quality", "min": 0.1}]
            rows, skipped = core.prepare_rows({"scores": str(scores), "metrics": metrics})
            self.assertEqual([row["id"] for row in rows], ["low.pdb"])
            self.assertEqual([item["row"] for item in skipped], [3])

    def test_exported_table_roundtrips_repeated_and_metadata_named_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("low.pdb", "high.pdb"):
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.csv"
            scores.write_text(
                "path,id,quality,nearest_rmsd,score_id\n"
                "low.pdb,17,0.2,0.6,0.75\n"
                "high.pdb,19,0.9,0.3,0.8\n",
                encoding="utf-8",
            )
            metrics = [
                {"name": "quality", "max": 0.5}, {"name": "quality", "min": 0.1},
                {"name": "id"}, {"name": "nearest_rmsd"}, {"name": "score_id"},
            ]
            config = {"scores": str(scores), "metrics": metrics, "count": 1, "seed": 1,
                      "orientation": "pca", "target": "polymer.protein", "binder": "polymer.protein"}
            output = root / "collection"
            manifest = core.create_collection(config, output)
            self.assertEqual(manifest["quantile_edges"], [[] for _ in metrics])
            rows = core.read_scores(output / "02_samples.csv")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["path"], str((root / "low.pdb").resolve()))
            self.assertEqual(rows[0]["id"], "17")
            self.assertEqual(rows[0]["quality"], "0.2")
            self.assertEqual(rows[0]["score_id"], "0.75")
            self.assertEqual(rows[0]["score_score_id"], "17.0")
            self.assertEqual(rows[0]["score_nearest_rmsd"], "0.6")

    def test_legacy_export_repeats_only_identical_metric_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "low.pdb")
            scores = root / "02_samples.csv"
            header = "id,path,stratum,nearest_rmsd,quality,quality\n"
            same = "legacy,low.pdb,1/1,,0.2,0.2\n"
            scores.write_text(header + same, encoding="utf-8-sig")
            rows, skipped = core.prepare_rows({"scores": str(scores), "metrics": [{"name": "quality"}]})
            self.assertEqual([row["id"] for row in rows], ["legacy"])
            self.assertEqual(rows[0]["scores"]["quality"], 0.2)
            self.assertFalse(skipped)

            scores.write_text(header + same + "conflict,low.pdb,1/1,,0.2,0.95\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "重复"):
                core.read_scores(scores)

    def test_duplicate_metric_headers_are_rejected(self):
        fixtures = {
            "scores.csv": "path,quality,quality\nlow.pdb,0.1,0.95\n",
            "scores.tsv": "path\tquality\tquality\nlow.pdb\t0.1\t0.95\n",
            "scores.sc": "SCORE: quality quality description\nSCORE: 0.1 0.95 low\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in fixtures.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(content, encoding="utf-8-sig")
                    with self.assertRaisesRegex(ValueError, "重复"):
                        core.read_scores(path)

    def test_delimited_scores_keep_missing_and_extra_field_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scores.csv"
            path.write_text("path,quality,pae\nfirst\n\nsecond,0.9,4,extra\n", encoding="utf-8-sig")
            self.assertEqual(core.read_scores(path), [
                {"path": "first", "quality": None, "pae": None},
                {"path": "second", "quality": "0.9", "pae": "4", None: ["extra"]},
            ])
            path.write_text("path,quality\nfirst,0.9,extra\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                core.read_scores(path)

    def test_quoted_structure_paths_and_ids_preserve_line_separators(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            intended = root / "a\u2028b.pdb"
            cmd.fragment("ala", "target")
            cmd.fragment("gly", "other")
            cmd.save(str(intended), "target")
            cmd.save(str(root / "ab.pdb"), "other")
            for suffix, delimiter in (("csv", ","), ("tsv", "\t")):
                with self.subTest(suffix=suffix):
                    path = root / f"scores.{suffix}"
                    with path.open("w", newline="", encoding="utf-8-sig") as stream:
                        writer = csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL)
                        writer.writerow(["path", "id", "quality"])
                        writer.writerow([intended.name, "first\r\nsecond\nthird", "0.9"])
                    config = {"scores": str(path), "metrics": [{"name": "quality"}]}
                    rows, skipped = core.prepare_rows(config)
                    self.assertEqual(len(rows), 1)
                    self.assertFalse(skipped)
                    row = rows[0]
                    self.assertEqual(Path(row["path"]), intended)
                    self.assertEqual(row["id"], "first\r\nsecond\nthird")
                    cmd.load(row["path"], "selected")
                    self.assertEqual(cmd.count_atoms("selected"), cmd.count_atoms("target"))
                    cmd.delete("selected")

    def test_json_duplicate_keys_cannot_override_screening_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("low.pdb", "high.pdb"):
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.json"
            config = {"scores": str(scores), "metrics": [{"name": "quality", "max": 0.5}]}
            scores.write_text('[{"path":"low.pdb","quality":0.2},'
                              '{"path":"high.pdb","quality":0.9}]', encoding="utf-8")
            rows, _ = core.prepare_rows(config)
            self.assertEqual([row["id"] for row in rows], ["low.pdb"])
            scores.write_text('[{"path":"low.pdb","quality":0.2,"quality":0.95},'
                              '{"path":"high.pdb","quality":0.9,"quality":0.2}]', encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "quality"):
                core.prepare_rows(config)

    def test_structure_path_prefers_the_exact_name_before_trimming(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            root = Path(temporary)
            cmd = instance.cmd
            cmd.fragment("ala", "intended")
            cmd.fragment("gly", "substitute")
            name = "\u2028model.pdb"
            cmd.save(str(root / name), "intended")
            cmd.save(str(root / "model.pdb"), "substitute")
            cmd.save(str(root / "padded.pdb"), "intended")
            path = root / "scores.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
                writer.writerow(["path", "quality"])
                writer.writerow([name, "0.9"])
                writer.writerow(["  padded.pdb", "0.8"])
            rows, skipped = core.prepare_rows({"scores": str(path), "metrics": [{"name": "quality"}]})
            self.assertFalse(skipped)
            self.assertEqual([row["id"] for row in rows], [name, "padded.pdb"])
            self.assertEqual([Path(row["path"]).name for row in rows], [name, "padded.pdb"])
            cmd.load(rows[0]["path"], "selected")
            self.assertEqual(cmd.count_atoms("selected"), cmd.count_atoms("intended"))

    def test_disappearing_structure_during_path_resolution_does_not_abort_sampling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disappearing = root / "disappearing.pdb"
            valid = root / "valid.pdb"
            shutil.copyfile(FIXTURE, disappearing)
            shutil.copyfile(FIXTURE, valid)
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": disappearing.name, "quality": 0}, {"path": valid.name, "quality": 1},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality", "bins": 2}],
                      "seed": 0, "count": 1, "rmsd": 0.1, "orientation": "pca",
                      "frame_selection": "chain A", "binder": "chain A"}
            lookup = core._resolve_structure_path

            def remove_after_lookup(directory, value):
                found = lookup(directory, value)
                if value == disappearing.name:
                    disappearing.unlink()
                return found

            with patch.object(core, "_resolve_structure_path", remove_after_lookup):
                collection = core.create_collection(config, root / "out")
            self.assertEqual([sample["path"] for sample in collection["samples"]], [str(valid)])
            self.assertEqual(collection["eligible"], 2)
            self.assertEqual([failure["path"] for failure in collection["invalid_structures"]],
                             [str(disappearing)])

    def test_directory_only_score_paths_do_not_select_sibling_structures(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root = directory / "models"
            root.mkdir()
            (root / "nested").mkdir()
            valid = root / "valid.pdb"
            shutil.copyfile(FIXTURE, valid)
            ca_only = "".join(
                line for line in FIXTURE.read_text(encoding="ascii").splitlines(keepends=True)
                if line.startswith("ATOM") and line[12:16].strip() == "CA"
            ) + "END\n"
            (directory / "models.pdb").write_text(ca_only, encoding="ascii")
            (root / "nested.pdb").write_text(ca_only, encoding="ascii")
            scores = directory / "scores.json"
            config = {"scores": str(scores), "root": str(root),
                      "metrics": [{"name": "quality", "bins": 2}], "seed": 0,
                      "count": 1, "rmsd": 0.1, "orientation": "pca",
                      "frame_selection": "chain A", "binder": "chain A"}
            for index, value in enumerate((".", "./", "nested/.", "nested/")):
                with self.subTest(value=value):
                    scores.write_text(json.dumps([
                        {"id": "directory", "path": value, "quality": 0},
                        {"id": "valid", "path": "valid", "quality": 1},
                    ]), encoding="utf-8")
                    manifest = core.create_collection(config, directory / f"out_{index}")
                    self.assertEqual([sample["path"] for sample in manifest["samples"]], [str(valid)])
                    self.assertEqual(manifest["eligible"], 1)
                    self.assertEqual([row["row"] for row in manifest["skipped"]], [2])

    def test_missing_structure_path_cannot_use_a_literal_none_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "None.pdb")
            fixtures = {
                "scores.json": '[{"id":"missing","quality":0.9,"path":null},'
                               '{"id":"explicit","quality":0.8,"path":"None"}]',
                "scores.csv": "id,quality,path\nmissing,0.9\nexplicit,0.8,None\n",
            }
            for name, data in fixtures.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(data, encoding="utf-8")
                    config = {"scores": str(path), "metrics": [{"name": "quality"}]}
                    rows, skipped = core.prepare_rows(config)
                    self.assertEqual([(row["id"], row["scores"]["quality"]) for row in rows],
                                     [("explicit", 0.8)])
                    self.assertEqual([Path(row["path"]).name for row in rows], ["None.pdb"])
                    self.assertEqual([item["row"] for item in skipped], [2])

    def test_out_of_range_json_integer_does_not_abort_screening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(FIXTURE, root / "retained.pdb")
            scores = root / "scores.json"
            scores.write_text(json.dumps([
                {"path": "invalid.pdb", "quality": 10 ** 400},
                {"path": "retained.pdb", "quality": 0.9},
            ]), encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}]}
            rows, skipped = core.prepare_rows(config)
            self.assertEqual([row["id"] for row in rows], ["retained.pdb"])
            self.assertEqual([item["row"] for item in skipped], [2])

    def test_replacement_uses_saved_data_sources_and_recovers_legacy_paths_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as fixture:
            root = Path(temporary)
            original, unrelated = root / "original", root / "unrelated"
            for base in (original, unrelated):
                (base / "models").mkdir(parents=True)
            cmd = fixture.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "source")
            coords = cmd.get_coords("source", 1)
            shutil.copyfile(FIXTURE, original / "models/first.pdb")
            correct = original / "models/alternate.pdb"
            cmd.load_coords(coords * [1.1, 1.6, 1.8], "source", 1)
            cmd.save(str(correct), "source")
            wrong = unrelated / "models/unrelated.pdb"
            cmd.load_coords(coords * [2.0, 1.5, 1.2], "source", 1)
            cmd.save(str(wrong), "source")
            shutil.copyfile(wrong, original / correct.name)
            scores = original / "scores.json"
            scores.write_text(json.dumps([{"path": "first.pdb", "quality": 0.8}]), encoding="utf-8")
            (unrelated / scores.name).write_text(
                json.dumps([{"path": wrong.name, "quality": 0.82}]), encoding="utf-8")
            config = {"scores": scores.name, "root": "models", "metrics": [{"name": "quality"}],
                      "count": 1, "seed": 1, "rmsd": 0.1, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            current_path = root / "current/01_collection.json"
            legacy_path = root / "legacy/01_collection.json"
            legacy_path.parent.mkdir()
            request = {"action": "replace", "indices": [0]}
            cwd = Path.cwd()
            try:
                os.chdir(original)
                saved = core.create_collection(config, current_path.parent)
                self.assertEqual(saved["config"]["scores"], str(scores))
                self.assertEqual(saved["config"]["root"], str(original / "models"))
                legacy = {**saved, "config": {**saved["config"], **config}}
                core.save_collection(legacy_path, legacy)
                scores.write_text(json.dumps([{"path": "first.pdb", "quality": 0.8},
                                              {"path": correct.name, "quality": 0.82}]), encoding="utf-8")
                os.chdir(unrelated)
                changed = core.revise_collection(current_path, request)
                self.assertEqual(changed["samples"][0]["path"], str(correct))
                report = legacy_path.parent / "02_samples.csv"
                before = (legacy_path.read_bytes(), report.read_bytes())
                with self.assertRaisesRegex(ValueError, "绝对路径"):
                    core.revise_collection(legacy_path, request)
                self.assertEqual((legacy_path.read_bytes(), report.read_bytes()), before)
                with self.assertRaisesRegex(ValueError, "绝对路径"):
                    core.revise_collection(legacy_path, {**request,
                        "source_paths": {"scores": str(scores), "root": ""}})
                self.assertEqual((legacy_path.read_bytes(), report.read_bytes()), before)
                rebound = core.revise_collection(
                    legacy_path, {**request, "source_paths": {"scores": str(scores), "root": str(original / "models")}})
                self.assertEqual(rebound["samples"][0], changed["samples"][0])
                self.assertEqual(rebound["config"], changed["config"])
            finally:
                os.chdir(cwd)

    def test_ca_reuse_across_pairs_preserves_decision_after_reloading_candidate(self):
        with pymol2.PyMOL() as instance:
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.load(str(FIXTURE), "col_candidate")
            for index in range(12):
                cmd.create(f"col_keep_{index}", "col_candidate")
            coords = cmd.get_coords("col_candidate", 1)
            cmd.load_coords(coords * [1.8, 1.4, 1.35], "col_candidate", 1)
            selected = [{} for _ in range(12)]
            config = {"diversity": "shape", "diversity_selection": "polymer.protein", "rmsd": 0.1}
            with patch.object(cmd, "get_model", wraps=cmd.get_model) as extraction:
                self.assertEqual(core.check_diversity(cmd, {}, config, selected), "accepted")
            self.assertEqual(extraction.call_count, 13)
            cmd.delete("col_candidate")
            cmd.load(str(FIXTURE), "col_candidate")
            self.assertEqual(core.check_diversity(cmd, {}, config, selected), "duplicate")

    def test_nonfinite_rmsd_cannot_disable_native_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = ["original.pdb", "copy.pdb"]
            for name in names:
                shutil.copyfile(FIXTURE, root / name)
            scores = root / "scores.json"
            scores.write_text(json.dumps([{"path": name, "quality": 0.9} for name in names]),
                              encoding="utf-8")
            config = {"scores": str(scores), "metrics": [{"name": "quality"}], "count": 2,
                      "seed": 8, "rmsd": 0.1, "orientation": "pca",
                      "target": "polymer.protein", "binder": "polymer.protein"}
            normal = core.create_collection(config, root / "valid")
            self.assertEqual(len(normal["samples"]), 1)
            self.assertEqual(normal["rejected_duplicates"], 1)
            for value in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(value=value):
                    config["rmsd"] = value
                    output = root / value
                    with self.assertRaisesRegex(ValueError, "RMSD"):
                        core.create_collection(config, output)
                    self.assertFalse((output / "01_collection.json").exists())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Run against an immutable Git revision")
    options, remaining = parser.parse_known_args()
    if options.revision:
        core = core_at(options.revision)
    unittest.main(argv=[__file__, *remaining], verbosity=2)
