"""Real-PyMOL regressions for residue color undo ownership.

Run with the bundled interpreter:
    D:/Softwares/Pymol3/python.exe -m unittest tests.test_residue_color_lifecycle -v
"""

import unittest

try:
    from pymol import cmd
    from pymolplugins.residues import analysis, commands, presentation
    from pymolplugins.styles import registry as styles_registry
except ImportError:
    cmd = None


def _colors(selection="all"):
    values = {}
    cmd.iterate(selection, "values[(model, index)] = color",
                space={"values": values})
    return values


def _b_values(selection="all"):
    values = {}
    cmd.iterate(selection, "values[(model, index)] = b",
                space={"values": values})
    return values


def _coordinates(selection="all"):
    values = {}
    cmd.iterate_state(1, selection, "values[(model, index)] = (x, y, z)",
                      space={"values": values})
    return values


def _state(selection="all"):
    return _colors(selection), _b_values(selection), _coordinates(selection)


def _make_peptide(name="pep", color="red"):
    cmd.fragment("peptide", name)
    cmd.alter(name, "b = index * 0.125")
    cmd.color(color, name)
    return _state(name)


def _assert_geometry(testcase, expected, selection="all"):
    _, b_values, coordinates = _state(selection)
    testcase.assertEqual(b_values, expected[1])
    testcase.assertEqual(coordinates, expected[2])


@unittest.skipIf(cmd is None, "PyMOL Python is required")
class ResidueColorLifecycleTests(unittest.TestCase):
    def setUp(self):
        cmd.delete("all")
        styles_registry._REGISTRY.snapshots.clear()
        presentation._command_snapshots.clear()
        presentation._selections.clear()
        analysis.clear_cache()

    def test_capture_restore_skips_deleted_and_recreated_molecule(self):
        baseline = _make_peptide(color="yellow")
        report = commands.pp_residues("pep", color=0)
        snapshot = presentation.capture_colors(report)

        cmd.delete("pep")
        cmd.fragment("peptide", "pep")
        cmd.alter("pep", "b = index * 0.5")
        cmd.color("magenta", "pep")
        replacement = _state("pep")

        self.assertEqual(presentation.restore_colors(snapshot), 0)
        self.assertEqual(_colors("pep"), replacement[0])
        self.assertEqual(set(_colors("pep").values()),
                         {cmd.get_color_index("magenta")})
        _assert_geometry(self, replacement, "pep")
        self.assertNotEqual(_colors("pep"), baseline[0])

    def test_newer_cli_block_rejects_older_view_revert_then_unwinds_lifo(self):
        baseline = _make_peptide()
        report = commands.pp_residues("pep", color=0)
        view_id = presentation.apply_colors(report, "layers")
        view_colors = _colors()
        self.assertNotEqual(view_colors, baseline[0])

        commands.pp_residues("pep", mode="contacts")
        cli_colors = _colors()
        self.assertNotEqual(cli_colors, view_colors)
        with self.assertRaisesRegex(ValueError, "LIFO"):
            styles_registry.pp_style_revert(view_id)
        self.assertEqual(_colors(), cli_colors)

        cleared = commands.pp_residue_clear()
        self.assertGreater(cleared["restored_atoms"], 0)
        self.assertEqual(_colors(), view_colors)
        self.assertEqual(styles_registry.pp_style_revert(view_id), int(view_id))
        self.assertEqual(_colors(), baseline[0])
        _assert_geometry(self, baseline)

    def test_cli_block_below_view_is_cleared_only_after_view_revert(self):
        baseline = _make_peptide()
        commands.pp_residues("pep", mode="contacts")
        cli_colors = _colors()
        self.assertNotEqual(cli_colors, baseline[0])

        report = commands.pp_residues("pep", color=0)
        view_id = presentation.apply_colors(report, "layers")
        view_colors = _colors()
        self.assertNotEqual(view_colors, cli_colors)
        with self.assertRaisesRegex(RuntimeError, "more recent style"):
            commands.pp_residue_clear()
        self.assertEqual(_colors(), view_colors)

        self.assertEqual(styles_registry.pp_style_revert(view_id), int(view_id))
        self.assertEqual(_colors(), cli_colors)
        commands.pp_residue_clear()
        self.assertEqual(_colors(), baseline[0])
        _assert_geometry(self, baseline)

    def test_two_cli_blocks_split_by_ordinary_style_recover_in_order(self):
        baseline = _make_peptide()
        commands.pp_residues("pep", mode="layers")
        first_cli = _colors()
        self.assertNotEqual(first_cli, baseline[0])

        style_id = styles_registry.pp_style_apply("macaron_palette", "pep")
        styled = _colors()
        self.assertNotEqual(styled, first_cli)
        commands.pp_residues("pep", mode="contacts")
        second_cli = _colors()
        self.assertNotEqual(second_cli, styled)

        commands.pp_residue_clear()
        self.assertEqual(_colors(), styled)
        self.assertEqual(styles_registry.pp_style_revert(style_id), int(style_id))
        self.assertEqual(_colors(), first_cli)
        commands.pp_residue_clear()
        self.assertEqual(_colors(), baseline[0])
        _assert_geometry(self, baseline)

    def test_ordinary_and_lighting_styles_block_older_view_revert(self):
        for serial, style_name in enumerate(("macaron_palette", "rfd2_lighting")):
            with self.subTest(style=style_name):
                name = "pep%d" % serial
                baseline = _make_peptide(name)
                report = commands.pp_residues(name, color=0)
                view_id = presentation.apply_colors(report, "layers")
                view_colors = _colors(name)
                style_id = styles_registry.pp_style_apply(style_name, name)
                styled = _colors(name)

                with self.assertRaisesRegex(ValueError, "LIFO"):
                    styles_registry.pp_style_revert(view_id)
                self.assertEqual(_colors(name), styled)
                self.assertEqual(styles_registry.pp_style_revert(style_id),
                                 int(style_id))
                self.assertEqual(_colors(name), view_colors)
                self.assertEqual(styles_registry.pp_style_revert(view_id),
                                 int(view_id))
                self.assertEqual(_colors(name), baseline[0])
                _assert_geometry(self, baseline, name)

    def test_clear_skips_reloaded_replacement_after_old_cli_colors(self):
        _make_peptide()
        commands.pp_residues("pep", mode="layers")
        cmd.delete("pep")
        cmd.fragment("peptide", "pep")
        cmd.alter("pep", "b = index * 0.5")
        cmd.color("magenta", "pep")
        replacement = _state("pep")

        cleared = commands.pp_residue_clear()
        self.assertEqual(cleared["restored_atoms"], 0)
        self.assertEqual(presentation._command_snapshots, [])
        self.assertEqual(_colors("pep"), replacement[0])
        _assert_geometry(self, replacement, "pep")


if __name__ == "__main__":
    unittest.main()
