"""Extended residue tokens share the canonical index and selection transaction."""

import unittest

from pymolplugins.core.sequence import (
    SequenceMatchLimitError, SequenceOccurrenceError, find_sequence_matches,
    format_extended_fasta, index_atom_streams, parse_residue_tokens,
)
from tests.alignment.test_sequence_selection import FakeCmd, load_adapter, make_atoms


def component_atoms(names, *, chain='A', segi='', start=1):
    return [dict(index=start + i, name='C1', chain=chain, segi=segi,
                 resi=str(i + 1), resn=name, hetatm=1) for i, name in enumerate(names)]


class ExtendedSequenceTests(unittest.TestCase):
    def test_export_and_matches_use_same_records(self):
        index = index_atom_streams([('sample', 1, component_atoms(['ALA', 'MSE', 'SEP', 'ATP', 'ZN', 'DA', '5MC']))])
        self.assertEqual(index.tracks[0].tokens, ('A', '(MSE)', '(SEP)', '(ATP)', '(ZN)', 'A', '(5MC)'))
        self.assertEqual(format_extended_fasta(index, 4).splitlines(),
                         ['>sample|state=1|segi=|chain=A', 'A', '(MSE)', '(SEP)', '(ATP)', '(ZN)', 'A', '(5MC)'])
        matches = find_sequence_matches(index, ['A(MSE)(SEP)(ATP)'], residue_tokens=True)
        self.assertEqual(matches[0].residues, index.records[:4])
        self.assertEqual([r.atom_indices for r in matches[0].residues], [(1,), (2,), (3,), (4,)])
        self.assertEqual(len(find_sequence_matches(index, ['(ALA)'], residue_tokens=True)), 1)
        self.assertEqual(len(find_sequence_matches(index, ['A'], residue_tokens=True)), 2)
        self.assertEqual(find_sequence_matches(index, ['M'], residue_tokens=True), ())
        self.assertEqual(len(find_sequence_matches(index, ['M'])), 1)

    def test_tokens_never_cross_track_boundaries(self):
        streams = [(obj, state, component_atoms([name], chain=chain, segi=segi))
                   for obj, state, segi, chain, name in (
                       ('one', 1, '', 'A', 'ATP'), ('two', 1, '', 'A', 'ZN'),
                       ('one', 2, '', 'A', 'ZN'), ('one', 1, 'other', 'A', 'ZN'),
                       ('one', 1, '', 'B', 'ZN'))]
        index = index_atom_streams(streams)
        self.assertEqual(find_sequence_matches(index, ['(ATP)(ZN)'], residue_tokens=True), ())
        self.assertEqual(len(format_extended_fasta(index).split('>')) - 1, 5)

    def test_overlaps_occurrence_and_limits_are_shared(self):
        index = index_atom_streams([('ligands', 1, component_atoms(['ATP'] * 3))])
        matches = find_sequence_matches(index, ['(ATP)(ATP)'], residue_tokens=True)
        self.assertEqual([m.residue_positions for m in matches], [(0, 1), (1, 2)])
        self.assertEqual(len(find_sequence_matches(index, ['X(ATP)'], residue_tokens=True, occurrence='first')), 1)
        with self.assertRaises(SequenceOccurrenceError):
            find_sequence_matches(index, ['(ATP)(ATP)'], residue_tokens=True, occurrence='unique')
        with self.assertRaises(SequenceMatchLimitError):
            find_sequence_matches(index, ['(ATP)'], residue_tokens=True, max_matches=2)

    def test_invalid_tokens_and_bounded_mode_fail_explicitly(self):
        for query in ('()', '(ATP', '((ATP))', '(AT P)', 'A2', 'A.*', 'ATP)', '>header\nA'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_residue_tokens(query)
        self.assertEqual(parse_residue_tokens(' a-(mse) (9uy)'), ('A', '(MSE)', '(9UY)'))
        index = index_atom_streams([('sample', 1, component_atoms(['ATP', 'ZN']))])
        for settings in ({'gap_mode': 'bounded'}, {'max_gap': 1}):
            with self.assertRaisesRegex(ValueError, 'requires gap_mode'):
                find_sequence_matches(index, ['(ATP)(ZN)'], residue_tokens=True, **settings)

    def test_component_names_with_same_number_keep_exact_atom_identity(self):
        atoms = component_atoms(['ATP', 'ZN'])
        atoms[1]['resi'] = atoms[0]['resi']
        index = index_atom_streams([('sample', 1, atoms)])
        matches = find_sequence_matches(index, ['(ZN)'], residue_tokens=True)
        self.assertEqual([m.residues[0].atom_indices for m in matches], [(2,)])

    def test_token_errors_and_transaction_failure_preserve_old_selection(self):
        fake = FakeCmd(make_atoms('AC') + component_atoms(['ATP'], chain='L', start=7))
        adapter = load_adapter(fake)
        created = adapter.pp_select_sequence('protein', '(ATP)', residue_tokens=1, visualize=0)
        self.assertTrue(created['ok'])
        before = {name: set(atoms) for name, atoms in fake.selections.items()}
        for query, settings in (('()', {}), ('(ZN)', {}), ('A', {'gap_mode': 'bounded'})):
            failed = adapter.pp_select_sequence('protein', query, residue_tokens=1, visualize=0, **settings)
            self.assertFalse(failed['ok'])
            self.assertTrue(failed['scene_preserved'])
            self.assertEqual(fake.selections, before)
        fake.fail_set_name_to.add('selected_residues')
        failed = adapter.pp_select_sequence('protein', '(ALA)', residue_tokens=1, visualize=0)
        self.assertEqual(failed['error_code'], 'selection_transaction_failed')
        self.assertEqual(fake.selections, before)


if __name__ == '__main__':
    unittest.main()
