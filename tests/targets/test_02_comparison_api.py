"""Run with PyMOL's Python: no GUI or finish_launching is used."""

import csv
import json
from math import cos, sin
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import numpy as np
    import pymol2
    from chempy import Atom
    from chempy.models import Indexed
except ImportError:
    pymol2 = None

from pymolplugins.annotation.command_templates import format_commands
from pymolplugins.annotation.review_steps import ReviewObjects
from pymolplugins.targets.batch_review import prepare_comparison_review, write_comparison_review
from pymolplugins.targets.batch_sources import BatchConfig


RESIDUES = ('ALA', 'CYS', 'ASP', 'GLU', 'PHE', 'GLY', 'HIS', 'ILE', 'LYS',
            'LEU', 'MET', 'ASN', 'PRO', 'GLN', 'ARG', 'SER', 'THR', 'VAL', 'TRP', 'TYR')


def protein(chain='A', omit=(), insertion=False):
    model = Indexed()
    for i, resn in enumerate(RESIDUES, 1):
        if i in omit:
            continue
        for atom_name, dy in [('CA', 0), ('CB', 2)]:
            atom = Atom()
            atom.hetatm = 0
            atom.name, atom.symbol, atom.resn = atom_name, 'C', resn
            atom.chain, atom.resi = chain, str(i)
            atom.coord = [i * 5., sin(i) + dy, cos(i)]
            model.add_atom(atom)
        if insertion and i == 10:
            atom = Atom()
            atom.hetatm = 0
            atom.name, atom.symbol, atom.resn = 'CA', 'C', 'GLY'
            atom.chain, atom.resi = chain, '10A'
            atom.coord = [52.5, 0, 0]
            model.add_atom(atom)
    return model


@unittest.skipIf(pymol2 is None, 'Requires PyMOL Python with numpy and pymol2')
class ComparisonApiTests(unittest.TestCase):
    def setUp(self):
        from pymolplugins.targets.comparison import compare
        self.compare = compare
        self.pymol = pymol2.PyMOL()
        self.pymol.start()
        self.addCleanup(self.pymol.stop)
        self.cmd = self.pymol.cmd
        self.cmd.set('auto_zoom', 0)
        from pymolplugins.targets import commands
        command_binding = patch.object(commands, 'cmd', self.cmd)
        command_binding.start()
        self.addCleanup(command_binding.stop)
        commands.register_commands(self.cmd)
        self.execute = commands._execute_checked

    def load_pair(self, candidate=None):
        self.cmd.load_model(protein('R'), 'reference')
        self.cmd.load_model(candidate or protein('A'), 'candidate')

    def test_first_frame_ttt_moves_only_candidate_and_retains_camera(self):
        self.load_pair()
        c = self.cmd
        c.create('candidate', 'candidate', 1, 2)
        c.create('reference', 'reference', 1, 2)
        c.rotate('z', 37, object='candidate')
        c.translate([20, 30, 40], object='candidate')
        c.rotate('y', -23, object='reference')
        c.translate([3, 4, 5], object='reference')
        c.frame(2)
        reference = c.get_coords('reference', 1).copy()
        matrix = c.get_object_matrix('reference')
        other_state = c.get_coords('candidate', 2).copy()
        view = c.get_view()
        result = self.compare(c, 'candidate', 'reference', 'overlay')
        self.assertEqual(result['paired_ca'], 20)
        self.assertLess(result['rmsd'], 1e-4)
        np.testing.assert_allclose(c.get_coords('candidate', 1), reference, atol=2e-5)
        np.testing.assert_array_equal(c.get_coords('reference', 1), reference)
        np.testing.assert_array_equal(c.get_coords('candidate', 2), other_state)
        self.assertEqual(c.get_object_matrix('reference'), matrix)
        self.assertEqual(c.get_view(), view)
        self.assertEqual(set(c.get_names()), {'candidate', 'reference'})

    def test_sequence_changes_use_alignment_across_chain_names_and_insertion_codes(self):
        for omit, insertion, expected_insertions, expected_deletions in (
                ((), True, [('', 'A', '10A')], []),
                ((10,), False, [], [('', 'R', '10')])):
            with self.subTest(omit=omit, insertion=insertion):
                self.cmd.delete('all')
                self.load_pair(protein('A', omit=omit, insertion=insertion))
                self.cmd.alter('candidate and resi 4', 'resn="ASN"')
                result = self.compare(self.cmd, 'candidate', 'reference', 'mutations')
                self.assertEqual(result['substitutions'], [dict(reference=('', 'R', '4'),
                    candidate=('', 'A', '4'), **{'from': 'E', 'to': 'N'})])
                self.assertEqual(result['insertions'], expected_insertions)
                self.assertEqual(result['deletions'], expected_deletions)

    def test_partial_fit_does_not_turn_the_rest_of_the_chain_into_indels(self):
        self.load_pair()
        result = self.compare(self.cmd, 'candidate', 'reference', 'mutations',
                              mobile_fit='resi 1-10', parent_fit='resi 1-10')
        self.assertEqual(result['paired_ca'], 10)
        self.assertEqual(result['substitutions'], [])
        self.assertEqual(result['insertions'], [])
        self.assertEqual(result['deletions'], [])

    def contact_pair(self):
        self.load_pair()
        c = self.cmd
        c.alter('candidate and resi 4', 'resn="ASN"')
        site = c.get_coords('reference and resi 4 and name CB', 1)[0]
        for model in ('candidate', 'reference'):
            for resi, near in (('101', model == 'candidate'), ('102', model == 'reference')):
                c.pseudoatom(model, name='C1', resn='LIG', chain='L', resi=resi,
                             elem='C', hetatm=1, pos=(site + ([0, 1, 0] if near else [0, 20, 0])).tolist())

    def test_gained_lost_are_geometry_and_distances_use_first_frame(self):
        self.contact_pair()
        c = self.cmd
        for name in ('candidate', 'reference'):
            c.create(name, name, 1, 2)
            c.translate([0, 100, 0], f'{name} and chain L', state=2)
        c.frame(2)
        distances = []
        native_distance = c.distance

        def measure(*args, **kwargs):
            value = native_distance(*args, **kwargs)
            distances.append(value)
            return value

        with patch.object(c, 'distance', measure):
            result = self.compare(c, 'candidate', 'reference', 'contacts')
        self.assertEqual((result['gained_contacts'], result['lost_contacts']), (1, 1))
        self.assertEqual(len(distances), 2)
        np.testing.assert_allclose(distances, [1, 1], atol=1e-4)

    def test_failure_removes_only_its_generated_objects(self):
        self.contact_pair()
        c = self.cmd
        c.pseudoatom('compare_gained_user')
        before = set(c.get_names())
        native = c.distance

        def draw_then_fail(*args, **kwargs):
            native(*args, **kwargs)
            raise RuntimeError('distance output failed')

        with patch.object(c, 'distance', draw_then_fail):
            with self.assertRaisesRegex(RuntimeError, 'output failed'):
                self.compare(c, 'candidate', 'reference', 'contacts')
        self.assertEqual(set(c.get_names()), before)
        with self.assertRaisesRegex(ValueError, 'three protein'):
            self.compare(c, 'candidate', 'reference', mobile_fit='resi 1')
        self.assertEqual(set(c.get_names()), before)

    def test_lineage_uses_fixed_anchor_across_rounds_and_revisits(self):
        c = self.cmd
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, shift in [('parent', 0), ('child1', 25), ('child2', 60)]:
                c.load_model(protein(), name)
                c.translate([shift, -shift, shift], name)
                c.save(str(root / (name + '.pdb')), name, state=1)
            table = root / 'pairs.csv'
            table.write_text('id,candidate,parent\n1,child1.pdb,parent.pdb\n'
                             '2,child2.pdb,child1.pdb\n', encoding='utf-8')
            review = prepare_comparison_review(BatchConfig(source='csv', mode='paired',
                a=str(table), a_column='candidate', b='parent', id_column='id'), 'lineage')
            output = write_comparison_review(review, root)
            config = json.loads(output.with_name('review.json').read_text())
            with output.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            objects = ReviewObjects()
            views = []
            references = {}
            for index in (0, 1, 0):
                row = rows[index]
                assets = {step['name']: SimpleNamespace(path=row[step['path_column']])
                          for step in config['steps'] if step.get('path_column')}
                objects.begin(c, str(output), config['steps'], assets)
                context = {}
                for step in config['steps']:
                    if step['type'] == 'commands':
                        # A loader or an "enable all" action may re-enable a cached anchor.
                        c.enable(context['anchor'])
                        for _, command in format_commands(config['step_cmd'], context):
                            self.execute(c, command)
                    elif step['name'] == 'candidate':
                        c.delete('current_candidate')
                        c.load(row['Candidate file'], 'current_candidate', zoom=0)
                        context['candidate'] = 'current_candidate'
                    else:
                        name, loaded = objects.load(c, step, assets[step['name']])
                        context[step['name']] = name
                        if loaded:
                            for _, command in format_commands(step.get('on_load_cmd', ''), context):
                                self.execute(c, command)
                objects.finish(c)
                self.assertNotIn(context['anchor'], c.get_names('objects', enabled_only=1))
                np.testing.assert_allclose(c.get_coords(context['parent'], 1),
                                           c.get_coords(context['anchor'], 1), atol=1e-4)
                np.testing.assert_allclose(c.get_coords('current_candidate', 1),
                                           c.get_coords(context['anchor'], 1), atol=1e-4)
                if context['parent'] in references:
                    np.testing.assert_array_equal(c.get_coords(context['parent'], 1), references[context['parent']])
                references[context['parent']] = c.get_coords(context['parent'], 1).copy()
                views.append(c.get_view())
            self.assertEqual(views[0], views[1])
            self.assertEqual(views[0], views[2])


if __name__ == '__main__':
    unittest.main()
