"""Real PyMOL contract checks, without a GUI or the user's active scene."""

from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

try:
    import pymol2
except ImportError:
    pymol2 = None


@unittest.skipIf(pymol2 is None, 'Requires the PyMOL installation Python')
class ExtendedSequencePyMOLTests(unittest.TestCase):
    def setUp(self):
        from pymolplugins.alignment import sequence

        self.engine = pymol2.PyMOL()
        self.engine.start()
        self.api = self.engine.cmd
        self.adapter = sequence
        self.addCleanup(self.engine.stop)
        binding = patch.object(sequence, 'cmd', self.api)
        binding.start()
        self.addCleanup(binding.stop)
        ownership = patch.dict(sequence._SELECTION_OWNERS, {}, clear=True)
        ownership.start()
        self.addCleanup(ownership.stop)

    def load(self, name, residues, *, state=1, discrete=0):
        from chempy import Atom
        from chempy.models import Indexed

        model = Indexed()
        for i, (resn, resi, segi, chain) in enumerate(residues):
            for j, atom_name in enumerate(('N', 'CA') if resn in ('ALA', 'MSE', 'SEP') else ('C1', 'C2')):
                atom = Atom()
                atom.resn, atom.resi, atom.segi, atom.chain = resn, resi, segi, chain
                atom.name, atom.symbol, atom.hetatm = atom_name, 'C', int(resn != 'ALA')
                atom.coord = [i * 5, j, state]
                model.add_atom(atom)
        self.api.load_model(model, name, state=state, discrete=discrete, zoom=0)

    def select(self, query, **kwargs):
        with redirect_stdout(io.StringIO()):
            return self.adapter.pp_select_sequence('all', query, visualize=0, residue_tokens=1, **kwargs)

    def test_read_only_export_scope_solvent_tokens_width_and_quiet(self):
        self.load('mol_one', [('ALA', '-1', '', 'A'), ('MSE', '2A', '', 'A'),
                            ('LONGCMP', '3', '', 'A'), ('HOH', '4', '', 'A'),
                            ('DA', '1', 'seg', ''), ('5MC', '2', 'seg', '')])
        self.load('disabled', [('ATP', '1', '', 'A')])
        self.api.disable('disabled')
        self.api.select('user_selection', 'mol_one and index 1')
        before = (self.api.get_names('all'), self.api.get_vis(), self.api.get_view(), self.api.index('user_selection'))
        output = io.StringIO()
        with redirect_stdout(output):
            fasta = self.adapter.pp_fasta(width=4, quiet=1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('>mol_one|state=1|segi=|chain=A\nA\n(MSE)\n(LONGCMP)', fasta)
        self.assertIn('>mol_one|state=1|segi=seg|chain=\nA\n(5MC)', fasta)
        self.assertNotIn('HOH', fasta)
        self.assertNotIn('disabled', fasta)
        self.assertIn('(HOH)', self.adapter.pp_fasta('mol_one', include_solvent=1, quiet=1))
        self.assertEqual(self.adapter.pp_fasta('mol_one and resn MSE', quiet=1).splitlines()[1], '(MSE)')
        after = (self.api.get_names('all'), self.api.get_vis(), self.api.get_view(), self.api.index('user_selection'))
        self.assertEqual(before, after)
        with self.assertRaisesRegex(ValueError, 'width'):
            self.adapter.pp_fasta(width=0)

    def test_state_tracks_and_overlapping_exact_atom_selection(self):
        self.load('ensemble', [('ATP', '1', '', 'A')], state=1, discrete=1)
        self.load('ensemble', [('ZN', '1', '', 'A')], state=2, discrete=1)
        self.api.frame(2)
        current = self.adapter.pp_fasta('ensemble', quiet=1)
        self.assertEqual(current, '>ensemble|state=2|segi=|chain=A\n(ZN)')
        both = self.adapter.pp_fasta('ensemble', state=0, quiet=1)
        self.assertEqual(both, '>ensemble|state=1|segi=|chain=A\n(ATP)\n>ensemble|state=2|segi=|chain=A\n(ZN)')
        result = self.select('(ATP)(ZN)', state=0, selection_name='')
        self.assertTrue(result['ok'])
        self.assertEqual(result['match_count'], 0)
        self.load('repeated', [('ATP', str(i), '', 'L') for i in range(1, 4)])
        result = self.select('(ATP)(ATP)', selection_name='motif')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['match_count'], 2)
        self.assertEqual(result['selected_atom_count'], 6)
        self.assertEqual(self.api.index('motif'), self.api.index('repeated'))
        self.api.create('repeated', 'repeated', 1, 2)
        result = self.select('(ATP)(ATP)', state=0, selection_name='motif')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['match_count'], 4)
        self.assertEqual(result['selected_atom_count'], 6)
        self.assertEqual(self.api.index('motif'), self.api.index('repeated'))
        result = self.select('(ZN)', state=-1, selection_name='current_match')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['matches'][0]['state'], 2)
        self.assertEqual(self.api.index('current_match'),
                         [('ensemble', atom.index) for atom in self.api.get_model('ensemble', 2).atom])

    def test_objects_segments_chains_and_component_identity_remain_separate(self):
        self.load('mol_one', [('ATP', '1', '', 'A'), ('ZN', '2', 'seg', 'A'), ('MSE', '3', '', 'B')])
        self.load('second', [('ZN', '1', '', 'A')])
        result = self.select('(ATP)(ZN)', selection_name='')
        self.assertEqual(result['match_count'], 0)
        result = self.select('(ZN)', selection_name='zinc')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['match_count'], 2)
        self.assertEqual(self.api.index('zinc'), self.api.index('resn ZN'))
        with redirect_stdout(io.StringIO()):
            default = self.adapter.pp_select_sequence('mol_one', 'M', selection_name='default_protein', visualize=0)
        self.assertTrue(default['ok'], default)
        self.assertEqual(self.api.index('default_protein'), self.api.index('resn MSE'))
        result = self.select('M', selection_name='')
        self.assertEqual(result['match_count'], 0)
        self.load('collision', [('ATP', '7', '', 'L'), ('ZN', '7', '', 'L')])
        result = self.select('(ZN)', selection_name='zinc')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.api.index('zinc'), self.api.index('resn ZN'))

    def test_bad_queries_collisions_and_failed_commit_preserve_selection(self):
        self.load('sample', [('ALA', '1', '', 'A'), ('ATP', '2', '', 'A')])
        result = self.select('(ATP)', selection_name='owned')
        self.assertTrue(result['ok'], result)
        before = self.api.index('owned')
        for query, options in (('()', {}), ('(ATP', {}), ('(ZN)', {}), ('A', {'gap_mode': 'bounded'})):
            result = self.select(query, selection_name='owned', **options)
            self.assertFalse(result['ok'])
            self.assertTrue(result['scene_preserved'])
            self.assertEqual(self.api.index('owned'), before)
        self.api.select('user_selection', 'sample and resn ALA')
        user_before = self.api.index('user_selection')
        result = self.select('(ATP)', selection_name='user_selection')
        self.assertEqual(result['error_code'], 'selection_name_collision')
        self.assertEqual(self.api.index('user_selection'), user_before)
        rename = self.api.set_name
        failed = False

        def fail_install(old, new):
            nonlocal failed
            if new == 'owned' and not failed:
                failed = True
                raise RuntimeError('injected install failure')
            return rename(old, new)

        with patch.object(self.api, 'set_name', fail_install):
            result = self.select('(ALA)', selection_name='owned')
        self.assertEqual(result['error_code'], 'selection_transaction_failed')
        self.assertTrue(result['scene_preserved'])
        self.assertEqual(self.api.index('owned'), before)
        self.assertEqual(set(self.api.get_names('selections')), {'owned', 'user_selection'})


if __name__ == '__main__':
    unittest.main()
