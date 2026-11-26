"""Real-PyMOL acceptance tests for residue analysis and presentation.

Run with the bundled interpreter:
    D:\Softwares\Pymol3\python.exe -m unittest tests.test_residue_analysis -v
"""

import math
import unittest

try:
    from pymol import cmd
    from pymolplugins.residues import analysis, commands, presentation
    from pymolplugins.styles import registry as styles_registry
except ImportError:
    cmd = None


def _iterate(expression, selection="all"):
    values = {}
    cmd.iterate(selection, "values[ID] = %s" % expression, space={"values": values})
    return values


def _color_by_index(selection="all"):
    values = {}
    cmd.iterate(selection, "values[index] = color", space={"values": values})
    return values


def _coordinates(selection="all"):
    values = {}
    cmd.iterate_state(1, selection, "values[ID] = (x, y, z)", space={"values": values})
    return values


def _native_sasa_by_residue(obj):
    copy = cmd.get_unused_name("_native_sasa")
    cmd.create(copy, "model %s and not solvent and not hydro" % obj, 1, 1, zoom=0)
    try:
        cmd.set("dot_solvent", 1, copy)
        cmd.set("dot_density", 3, copy)
        cmd.set("solvent_radius", 1.4, copy)
        cmd.get_area(copy, 1, load_b=1)
        sums = {}
        for atom in cmd.get_model(copy, 1).atom:
            key = (atom.segi, atom.chain, atom.resi, atom.resn)
            sums[key] = sums.get(key, 0.0) + atom.b
        return sums
    finally:
        cmd.delete(copy)


def _make_two_residue_object(name, shift):
    cmd.fragment("ala", name + "first")
    cmd.fragment("ala", name + "second")
    cmd.alter(name + "second", 'resi="5"')
    cmd.translate([3.0 + shift, 0.0, 0.0], name + "second")
    cmd.create(name, name + "first or " + name + "second")
    cmd.delete(name + "first")
    cmd.delete(name + "second")
    cmd.sort(name)


@unittest.skipIf(cmd is None, "PyMOL Python is required")
class ResidueAnalysisTests(unittest.TestCase):
    def setUp(self):
        cmd.delete("all")
        cmd.set("dot_solvent", 0)
        cmd.set("dot_density", 3)
        cmd.set("solvent_radius", 1.4)
        analysis.clear_cache()
        presentation._command_snapshots.clear()
        styles_registry._REGISTRY.snapshots.clear()
        presentation._selections.clear()

    def test_whole_residue_sasa_matches_native_get_area(self):
        cmd.fragment("peptide", "pep")
        report = commands.pp_residues("pep", color=0)
        self.assertEqual(len(report["residues"]), 22)

        native = _native_sasa_by_residue("pep")
        for record in report["residues"]:
            key = (record["segi"], record["chain"], record["resi"], record["resn"])
            self.assertIn(key, native)
            self.assertGreater(record["sasa"], 0.0)
            self.assertAlmostEqual(record["sasa"], native[key], places=3)

    def test_analysis_preserves_source_b_color_coordinates_and_globals(self):
        cmd.fragment("peptide", "pep")
        cmd.alter("pep", "b = index * 0.25")
        cmd.color("red", "pep")
        colors, b_values, coords = _iterate("color"), _iterate("b"), _coordinates()

        cmd.set("dot_solvent", 1)
        cmd.set("dot_density", 4)
        cmd.set("solvent_radius", 2.2)
        report = commands.pp_residues("pep", color=0)

        self.assertEqual(len(report["residues"]), 22)
        self.assertEqual(presentation._command_snapshots, [])
        self.assertEqual(_iterate("color"), colors)
        self.assertEqual(_iterate("b"), b_values)
        self.assertEqual(_coordinates(), coords)
        self.assertEqual(cmd.get_setting_float("dot_solvent"), 1.0)
        self.assertEqual(cmd.get_setting_float("dot_density"), 4.0)
        self.assertEqual(cmd.get_setting_float("solvent_radius"), 2.2)

    def test_heavy_atom_occludes_but_water_and_hydrogen_do_not(self):
        cmd.fragment("peptide", "pep")
        baseline = commands.pp_residues("pep", color=0)
        target = next(r for r in baseline["residues"] if r["resn"] == "ALA")
        self.assertGreater(target["sasa"], 50.0)
        cb = cmd.get_model("pep and resi %s and name CB" % target["resi"]).atom[0]
        position = [cb.coord[0] + 2.5, cb.coord[1], cb.coord[2]]

        cmd.pseudoatom("pep", name="LX", resn="LIG", elem="C", pos=position)
        occluded = commands.pp_residues("pep", color=0)
        occluded_target = next(r for r in occluded["residues"] if r["resi"] == target["resi"])
        self.assertEqual(len(occluded["residues"]), 22)
        self.assertFalse(any(r["resn"] == "LIG" for r in occluded["residues"]))
        self.assertLess(occluded_target["sasa"], target["sasa"] - 5.0)

        cmd.remove("pep and name LX")
        cmd.pseudoatom("pep", name="WX", resn="HOH", elem="O", pos=position)
        water = commands.pp_residues("pep", color=0)
        water_target = next(r for r in water["residues"] if r["resi"] == target["resi"])
        self.assertAlmostEqual(water_target["sasa"], target["sasa"], places=4)

        cmd.remove("pep and name WX")
        cmd.pseudoatom("pep", name="HX", resn="PSD", elem="H", pos=position)
        hydrogen = commands.pp_residues("pep", color=0)
        hydrogen_target = next(r for r in hydrogen["residues"] if r["resi"] == target["resi"])
        self.assertAlmostEqual(hydrogen_target["sasa"], target["sasa"], places=4)

    def test_cache_refreshes_after_coordinate_move_and_renumbering(self):
        cmd.fragment("peptide", "pep")
        baseline = commands.pp_residues("pep", color=0)
        target = next(r for r in baseline["residues"] if r["resn"] == "ALA")
        cb = cmd.get_model("pep and resi %s and name CB" % target["resi"]).atom[0]
        cb_x = cb.coord[0]

        cmd.pseudoatom("pep", name="LX", resn="LIG", elem="C",
                       pos=[cb_x + 8.5, cb.coord[1], cb.coord[2]])
        far = commands.pp_residues("pep", color=0)
        far_target = next(r for r in far["residues"] if r["resi"] == target["resi"])
        self.assertAlmostEqual(far_target["sasa"], target["sasa"], places=3)

        cmd.translate([-6.0, 0.0, 0.0], "pep and name LX")
        near = commands.pp_residues("pep", color=0)
        near_target = next(r for r in near["residues"] if r["resi"] == target["resi"])
        self.assertLess(near_target["sasa"], far_target["sasa"] - 5.0)

        before = next(r for r in near["residues"] if r["resi"] == "3")
        cmd.alter("pep and resi 3", 'resi="99"')
        renumbered = commands.pp_residues("pep", color=0)
        rows = {r["resi"]: r for r in renumbered["residues"]}
        self.assertIn("99", rows)
        self.assertNotIn("3", rows)
        self.assertEqual(rows["99"]["indices"], before["indices"])
        self.assertAlmostEqual(rows["99"]["sasa"], before["sasa"], places=3)
        for actual, expected in zip(rows["99"]["ca"], before["ca"]):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_states_are_independent_and_missing_state_raises(self):
        cmd.fragment("peptide", "st")
        cmd.create("st", "st", 1, 2)
        cmd.translate([10.0, 0.0, 0.0], "st", state=2)

        first = commands.pp_residues("st", state=1, color=0)
        second = commands.pp_residues("st", state=2, color=0)
        self.assertEqual(first["states"], {"st": 1})
        self.assertEqual(second["states"], {"st": 2})
        shift = second["residues"][0]["ca"][0] - first["residues"][0]["ca"][0]
        self.assertAlmostEqual(shift, 10.0, places=4)
        for left, right in zip(first["residues"], second["residues"]):
            self.assertAlmostEqual(left["sasa"], right["sasa"], places=3)

        with self.assertRaisesRegex(ValueError, "State 3 is unavailable"):
            commands.pp_residues("st", state=3, color=0)

    def test_selection_expands_by_residue_but_keeps_object_environment(self):
        cmd.fragment("peptide", "pep")
        full = commands.pp_residues("pep", color=0)
        target = full["residues"][5]
        selection = "pep and name CA and resi %s" % target["resi"]
        self.assertEqual(cmd.count_atoms(selection), 1)

        subset = commands.pp_residues(selection, color=0)
        self.assertEqual(len(subset["residues"]), 1)
        record = subset["residues"][0]
        self.assertEqual(record["object"], "pep")
        self.assertEqual(record["indices"], target["indices"])
        self.assertAlmostEqual(record["sasa"], target["sasa"], places=4)

    def test_multiple_objects_do_not_occlude_or_contact_each_other(self):
        _make_two_residue_object("left", 0.0)
        _make_two_residue_object("right", 1.5)
        left = commands.pp_residues("left", color=0, gap=0)
        right = commands.pp_residues("right", color=0, gap=0)
        self.assertEqual(len(left["pairs"]), 1)
        self.assertEqual([r["contacts"] for r in left["residues"]], [1, 1])
        self.assertEqual([r["contacts"] for r in right["residues"]], [1, 1])
        cross = math.dist(left["residues"][0]["ca"], right["residues"][0]["ca"])
        self.assertLess(cross, 8.0)

        combined = commands.pp_residues("left or right", color=0, gap=0)
        self.assertEqual(len(combined["residues"]), 4)
        self.assertEqual(len(combined["pairs"]), 2)
        for pair in combined["pairs"]:
            self.assertEqual(combined["residues"][pair["i"]]["object"],
                             combined["residues"][pair["j"]]["object"])
        for name, alone in (("left", left), ("right", right)):
            in_combined = [r for r in combined["residues"] if r["object"] == name]
            self.assertEqual(len(in_combined), 2)
            for expected, actual in zip(alone["residues"], in_combined):
                self.assertAlmostEqual(expected["sasa"], actual["sasa"], places=4)

    def test_export_qualification_and_rosetta_warnings(self):
        cmd.fragment("peptide", "pep")
        cmd.alter("pep and resi 2", 'resi="-2"')
        cmd.alter("pep and resi 3", 'resi="52A"')
        cmd.alter("pep and resi 4", 'segi="S1"; chain="A"; resi="60"')
        cmd.alter("pep and resi 5", 'segi="S2"; chain="A"; resi="60"; resn="GLU"')
        cmd.alter("pep and resi 6", 'chain="1"')
        report = commands.pp_residues("pep", color=0)
        keys = {(r["segi"], r["chain"], r["resi"], r["resn"]) for r in report["residues"]}
        self.assertIn(("", "", "-2", "ALA"), keys)
        self.assertIn(("", "", "52A", "ARG"), keys)
        self.assertIn(("S1", "A", "60", "ASP"), keys)
        self.assertIn(("S2", "A", "60", "GLU"), keys)
        by_key = {(r["segi"], r["chain"], r["resi"], r["resn"]): r for r in report["residues"]}
        first, second = by_key[("S1", "A", "60", "ASP")], by_key[("S2", "A", "60", "GLU")]
        self.assertTrue(first["rosetta_ambiguous"])
        self.assertTrue(second["rosetta_ambiguous"])
        self.assertFalse(by_key[("", "", "-2", "ALA")]["rosetta_ambiguous"])
        numeric = next(r for r in report["residues"] if r["chain"] == "1")
        self.assertFalse(numeric["rosetta_ambiguous"])

        text, warnings = presentation.format_layers(report, "pymol", "exp")
        self.assertEqual(warnings, [])
        for line in text.splitlines():
            self.assertTrue(line.startswith("select exp_"))
            if not line.endswith(", none"):
                self.assertIn('model "pep"', line)
        surface = next(line for line in text.splitlines() if line.startswith("select exp_surface"))
        self.assertIn("resi \\-2", surface)
        self.assertIn('segi "S1"', surface)
        self.assertIn('segi "S2"', surface)

        cmd.fragment("peptide", "other")
        combined = commands.pp_residues("pep or other", color=0)
        combined_text, _ = presentation.format_layers(combined, "pymol", "both")
        self.assertIn('model "pep"', combined_text)
        self.assertIn('model "other"', combined_text)

        rosetta, rosetta_warnings = presentation.format_layers(report, "rosetta", "exp")
        joined = " ".join(rosetta_warnings)
        self.assertIn("duplicate chain/resi in different segments", joined)
        self.assertIn("-2 (no chain)", joined)
        self.assertIn("52A (no chain)", joined)
        self.assertIn("%s (chain 1)" % numeric["resi"], joined)
        for line in rosetta.splitlines():
            self.assertTrue(line.startswith("pep / "))

        subset = commands.pp_residues("pep and segi S1 and resi 60", color=0)
        self.assertEqual(len(subset["residues"]), 1)
        self.assertTrue(subset["residues"][0]["rosetta_ambiguous"])
        subset_text, subset_warnings = presentation.format_layers(subset, "rosetta", "sub")
        self.assertIn("duplicate chain/resi", " ".join(subset_warnings))
        self.assertNotIn("60A", subset_text)

        cut = max(first["sasa"], second["sasa"])
        split = commands.pp_residues("pep", color=0, core=cut, boundary=cut)
        split_layers = {r["layer"] for r in split["residues"] if r["resi"] == "60"}
        self.assertEqual(split_layers, {"core", "surface"})
        split_text, split_warnings = presentation.format_layers(split, "rosetta", "split")
        self.assertGreaterEqual(sum("duplicate chain/resi" in w for w in split_warnings), 2)
        for line in split_text.splitlines():
            content = line.split(": ", 1)[1]
            self.assertNotIn("60", content)

    def test_coloring_restores_colors_and_guards_selection_collisions(self):
        cmd.fragment("peptide", "pep")
        cmd.alter("pep", "b = index * 0.125")
        cmd.color("red", "pep")
        colors, b_values, coords = _iterate("color"), _iterate("b"), _coordinates()
        names = set(cmd.get_names("all"))

        report = commands.pp_residues("pep", color=1, mode="layers")
        colored = _iterate("color")
        changed = [index for index in colors if colored[index] != colors[index]]
        self.assertGreater(len(changed), 50)
        blue = cmd.get_color_index("blue")
        self.assertTrue(all(colored[index] == blue for index in changed))
        self.assertEqual(set(cmd.get_names("all")), names)
        saved = styles_registry._REGISTRY.snapshots[-1].state["pep"]
        expected = {index for record in report["residues"] for index in record["indices"]}
        carbons = {atom.index for atom in cmd.get_model("pep").atom if atom.symbol == "C"}
        self.assertEqual(set(saved), expected & carbons)
        self.assertLess(len(saved), len(carbons))

        cleared = commands.pp_residue_clear()
        self.assertGreater(cleared["restored_atoms"], 0)
        self.assertEqual(_iterate("color"), colors)
        self.assertEqual(_iterate("b"), b_values)
        self.assertEqual(_coordinates(), coords)

        cmd.select("pp_residues_core", "pep and resi 2")
        count = cmd.count_atoms("pp_residues_core")
        names = set(cmd.get_names("all"))
        with self.assertRaisesRegex(ValueError, "Selection names already in use"):
            commands.pp_residues("pep", color=0, selections=1)
        self.assertEqual(set(cmd.get_names("all")), names)
        self.assertEqual(cmd.count_atoms("pp_residues_core"), count)

    def test_ui_color_snapshot_composes_with_style_revert(self):
        cmd.fragment("peptide", "pep")
        cmd.alter("pep", "b = index * 0.125")
        cmd.color("yellow", "pep")
        report = commands.pp_residues("pep", color=0)
        source = _color_by_index()
        b_values, coords = _iterate("b"), _coordinates()

        snapshot = presentation.capture_colors(report)
        expected = {index for record in report["residues"] for index in record["indices"]}
        carbons = {atom.index for atom in cmd.get_model("pep").atom if atom.symbol == "C"}
        self.assertEqual(set(snapshot["pep"]), expected & carbons)
        self.assertLess(len(snapshot["pep"]), len(carbons))
        for index, (identity, color) in snapshot["pep"].items():
            self.assertEqual(color, source[index])
            self.assertEqual(len(identity), 7)

        presentation.color_report(report, "layers", remember=False)
        self.assertEqual(presentation._command_snapshots, [])
        ui_colors = _color_by_index()
        changed = {index for index in set(snapshot["pep"])
                   if ui_colors[index] != source[index]}
        self.assertEqual(changed, set(snapshot["pep"]))

        style_id = styles_registry.pp_style_apply("macaron_palette", "pep")
        self.assertIsNotNone(style_id)
        styled = _color_by_index()
        self.assertTrue(any(styled[index] != ui_colors[index] for index in ui_colors))
        self.assertEqual(styles_registry.pp_style_revert(style_id), int(style_id))
        self.assertEqual(_color_by_index(), ui_colors)

        restored = presentation.restore_colors(snapshot)
        self.assertEqual(restored, len(snapshot["pep"]))
        self.assertEqual(_color_by_index(), source)
        self.assertEqual(presentation._command_snapshots, [])
        self.assertEqual(_iterate("b"), b_values)
        self.assertEqual(_coordinates(), coords)


if __name__ == "__main__":
    unittest.main()
