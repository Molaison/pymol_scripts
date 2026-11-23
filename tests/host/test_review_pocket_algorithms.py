"""Headless behavioral checks; run with the PyMOL installation's Python."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymol2
from chempy import Atom, Bond
from chempy.models import Indexed

from pymolplugins.annotation import comparison, compare_scene, pocket_jobs
from pymolplugins.annotation.antibody_cdrs import AntibodyCartoon, AntibodyRequest, capture_chains, number_chains
from pymolplugins.annotation.pocket_analysis import PocketAnalysis, _atom_lookup, capture_snapshot
from pymolplugins.annotation.pocket_reference_review import compare_ligand_pockets
from pymolplugins.annotation.pocket_variants import PocketTally
from pymolplugins.annotation.reference_track import ReferenceTrack, molecule_identity


def molecule(extra=None):
    model = Indexed()
    for i, coord in enumerate(((0, 0, 0), (0, 6, 0), (6, 0, 0))):
        atom = Atom()
        atom.name, atom.symbol, atom.resn, atom.resi, atom.chain = 'CA', 'C', 'ALA', str(i + 1), 'A'
        atom.hetatm = 0
        atom.coord = list(coord)
        model.add_atom(atom)
    ligand = [('C1', (0, 0, 2)), ('C2', (1, 0, 2)), ('C3', (0, 1, 2))]
    if extra:
        ligand.append(extra)
    for name, coord in ligand:
        atom = Atom()
        atom.name, atom.symbol, atom.resn, atom.resi, atom.chain = name, 'C', 'LIG', '10', 'L'
        atom.hetatm = 1
        atom.coord = list(coord)
        model.add_atom(atom)
    for i in range(4, len(model.atom)):
        bond = Bond()
        bond.index, bond.order = [3, i], 1
        model.add_bond(bond)
    return model


class PocketBehavior(unittest.TestCase):
    def setUp(self):
        self.engine = pymol2.PyMOL()
        self.engine.start()
        self.api = self.engine.cmd
        self.addCleanup(self.engine.stop)

    def load(self, name='reference', extra=None):
        self.api.load_model(molecule(extra), name, zoom=0)
        return name

    def snapshot(self, name, atom_sites=False):
        return capture_snapshot(self.api, f'%{name} and chain A', f'%{name} and chain L',
                                3.0, ligand_sites=atom_sites)

    def test_contacts_use_world_coordinates_in_first_state(self):
        self.load()
        self.api.create('reference', 'reference', 1, 2)
        self.api.translate([0, 0, 50], 'reference', state=2, camera=0)
        self.api.translate([10, -2, 3], object='reference', camera=0)
        self.api.frame(2)
        snapshot = self.snapshot('reference')
        lookup = _atom_lookup(self.api, 'reference')
        expected = self.api.get_model('reference', 1).atom
        for atom in expected:
            np.testing.assert_allclose(lookup['reference', atom.index].point, atom.coord)
        pose = snapshot.contacts[('A', '1'), 'Heavy-atom contact', ()]
        np.testing.assert_allclose(pose.points[0], [10, -2, 3])
        np.testing.assert_allclose(pose.points[1], [10, -2, 5])

    def test_heterogeneous_ligand_keeps_contact_statistics(self):
        self.load()
        self.load('candidate', ('C4', (6, 0, 2)))
        reference, candidate = self.snapshot('reference'), self.snapshot('candidate')
        tally = PocketTally(reference.protein, {('A', '1')})
        analysis = PocketAnalysis(reference, tally.sites)
        alignment, _ = tally.prepare(candidate.protein)
        contribution = analysis.prepare('candidate', 'candidate', candidate, *alignment)
        analysis.add(contribution)
        self.assertIsNone(contribution.preview.geometry.ligand)
        observed = {(c.site, c.change) for c in analysis.interactions()}
        self.assertIn((('A', '1'), 'Retained'), observed)
        self.assertIn((('A', '3'), 'Gained'), observed)
        self.assertTrue(contribution.preview.changes)

    def test_reference_contacts_include_added_and_removed_ligand_atoms(self):
        self.load(extra=('C4', (6, 0, 2)))
        self.load('candidate', ('C5', (0, 6, 2)))
        self.api.translate([12, 3, -2], object='candidate', camera=0)
        rmsd, count, _, contacts = compare_ligand_pockets(
            self.snapshot('reference', True), self.snapshot('candidate', True))
        self.assertLess(rmsd, 1e-5)
        self.assertEqual(count, 3)
        changes = {(item.shown.pose.ligand_atoms[0].name, item.shown.change) for item in contacts}
        self.assertIn(('C4', 'Lost'), changes)
        self.assertIn(('C5', 'Gained'), changes)
        self.assertIn(('C1', 'Retained'), changes)

    def test_reference_both_directions_restore_molecules_and_cgo(self):
        from pymol.cgo import COLOR, SPHERE

        self.load('candidate')
        self.api.set_object_ttt('candidate', [0, -1, 0, 12, 1, 0, 0, 3,
                                              0, 0, 1, -2, -4, 5, 0, 1])
        self.api.load_cgo([COLOR, 1, 0, 0, SPHERE, 0, 0, 0, 1], 'product')
        original = self.api.get_coords('candidate', 1).copy()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            self.load()
            path = str(Path(folder) / 'reference.pdb')
            self.api.save(path, 'reference', state=1)
            track = ReferenceTrack()
            track.load(self.api, 'key', path, 'chain L')
            for direction in ('row_to_reference', 'reference_to_row'):
                result = track.apply(self.api, 'candidate',
                                     dict(mode='ligand', row_selection='chain L', direction=direction), ['product'])
                self.assertIn('RMSD 0.000', result)
                np.testing.assert_allclose(self.api.get_coords('candidate and chain L', 1),
                                           self.api.get_coords(track.name, 1), atol=1e-5)
                applied = self.api.get_object_ttt('product')
                track.move_products(self.api, 'candidate', ['product'])
                self.assertEqual(applied, self.api.get_object_ttt('product'))
                track.suspend(self.api)
                np.testing.assert_allclose(self.api.get_coords('candidate', 1), original, atol=1e-5)
                np.testing.assert_allclose(self.api.get_object_ttt('product'), np.eye(4).ravel())
            track.clear(self.api)

    def test_reference_undo_does_not_move_reloaded_name(self):
        self.load('candidate')
        track = ReferenceTrack()
        self.api.translate([10, 0, 0], object='candidate', camera=0)
        track.owner = 'candidate'
        track.delta = (1, 0, 0, -10, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        track.move_products(self.api, 'candidate', ['candidate'])
        self.api.delete('candidate')
        self.load('candidate')
        self.api.set_object_ttt('candidate', np.eye(4).ravel().tolist())
        before = self.api.get_coords('candidate').copy()
        track.suspend(self.api)
        np.testing.assert_allclose(self.api.get_coords('candidate'), before)

    def test_reference_product_refresh_keeps_user_transform(self):
        self.load('candidate')
        track = ReferenceTrack()
        track.owner = 'candidate'
        track.delta = (1, 0, 0, 10, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        track.move_products(self.api, 'candidate', ['candidate'])
        self.api.translate([2, 0, 0], object='candidate', camera=0)
        before = self.api.get_coords('candidate').copy()
        track.move_products(self.api, 'candidate', ['candidate'])
        track.suspend(self.api)
        np.testing.assert_allclose(self.api.get_coords('candidate'), before)

    def test_compare_poll_detects_replacement_and_pose_change_without_style_noise(self):
        self.load()
        calls = []
        scene = compare_scene.CompareScene(None, lambda *args: calls.append(args), lambda: None)
        with patch.object(compare_scene, 'cmd', self.api):
            scene.names = set(self.api.get_names('objects'))
            scene.molecules = scene._molecules()
            self.api.color('red', 'reference')
            self.api.disable('reference')
            scene._poll()
            self.assertEqual(calls, [])
            self.api.translate([1, 0, 0], object='reference', camera=0)
            scene._poll()
            self.assertEqual(calls[-1], (['reference'], {'reference'}))
            self.api.delete('reference')
            self.load()
            scene._poll()
            self.assertEqual(len(calls), 2)
            self.api.unbond('reference and name C1', 'reference and name C2')
            scene._poll()
            self.assertEqual(len(calls), 3)
            self.api.create('reference', 'reference', 1, 2)
            self.api.frame(2)
            scene._poll()
            self.api.translate([0, 1, 0], 'reference', state=1, camera=0)
            scene._poll()
            self.assertEqual(len(calls), 5)
            refreshed = []
            track = SimpleNamespace(active=True, reference='sa_pocket_reference',
                                    source_objects={'reference'}, identity=('row', 'reference'))
            track.follow = lambda: refreshed.append(track.identity)
            controller = SimpleNamespace(pocket=SimpleNamespace(track=track))
            scene.changed = lambda added, removed: comparison.ReviewComparison._scene_changed(controller, added, removed)
            self.api.translate([1, 0, 0], object='reference', camera=0)
            scene._poll()
            self.assertEqual(refreshed, [None])

    def test_alignment_uses_first_state_and_preserves_original(self):
        self.load('reference')
        self.api.rotate('z', 30, object='reference', camera=0)
        self.api.translate([3, 6, -1], object='reference', camera=0)
        self.load('mobile')
        self.api.create('mobile', 'mobile', 1, 2)
        self.api.translate([20, 0, 0], 'mobile', state=2, camera=0)
        self.api.translate([10, -3, 2], object='mobile', camera=0)
        before = self.api.get_coords('mobile', 1).copy()
        self.api.select('_align1', 'reference and name C1', enable=0)
        selection = self.api.index('_align1')
        with patch.object(comparison, 'cmd', self.api):
            for method in ('align', 'fit', 'pair_fit'):
                self.api.create('overlay', 'mobile')
                overlay = comparison.Overlay('mobile', 'marine', 'overlay')
                rmsd = comparison._align_copy(overlay, 'reference', method, 'name CA', 'name CA')
                self.assertLess(rmsd, 1e-4)
                np.testing.assert_allclose(self.api.get_coords(overlay.name, 1),
                                           self.api.get_coords('reference', 1), atol=1e-4)
                self.assertEqual(self.api.get_names('selections'), ['_align1'])
                self.assertEqual(self.api.index('_align1'), selection)
                self.assertNotIn('_align1', self.api.get_names('selections', enabled_only=1))
                self.api.delete(overlay.name)
        np.testing.assert_array_equal(self.api.get_coords('mobile', 1), before)

    def test_cdr_restores_colors_but_leaves_reloaded_object_alone(self):
        from pymolplugins.annotation.antibody_cdrs import ObservedChain, _cartoon_colors

        self.load()
        ca = self.api.get_model('reference and name CA').atom
        def chain():
            return ObservedChain('reference', '', 'A', 'AAA',
                                 tuple((a.index, a.resn, a.resi) for a in ca),
                                 molecule_identity(self.api, 'reference'))
        view = AntibodyCartoon()
        self.api.set('cartoon_color', 'red', 'reference and name CA')
        view.draw(self.api, [chain()], {'AAA': [(0, 'H', ['27', '56', '105'])]})
        self.api.set('cartoon_color', 'green', 'reference and resi 2')
        view.clear(self.api)
        colors = _cartoon_colors(self.api, 'reference and name CA')
        self.assertEqual(list(colors.values()), [self.api.get_color_index(c) for c in ('red', 'green', 'red')])
        old = chain()
        view.draw(self.api, [old], {'AAA': [(0, 'H', ['27', '56', '105'])]})
        self.api.delete('reference')
        self.load()
        self.api.set('cartoon_color', 'marine', 'reference and name CA')
        view.clear(self.api)
        self.assertEqual(set(_cartoon_colors(self.api, 'reference and name CA').values()),
                         {self.api.get_color_index('marine')})
        view.draw(self.api, [old], {'AAA': [(0, 'H', ['27', '56', '105'])]})
        self.assertEqual(view.rows, [])

    def test_file_worker_native_result_errors_and_cancellation(self):
        self.load()
        reference = self.snapshot('reference')
        tally = PocketTally(reference.protein, {('A', '1')})
        request = SimpleNamespace(cancel=Event(), reference=reference, tally=tally,
                                  protein='chain A', ligand='chain L', radius=3.0,
                                  reader_settings=pocket_jobs.reader_settings(self.api), protein_partner=False)
        candidate = SimpleNamespace(id='row', display_name='row')
        workers = pocket_jobs.FileWorkers()
        self.addCleanup(workers.close)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            path = str(Path(folder) / 'candidate.pdb')
            self.api.save(path, 'reference', state=1)
            observations, contribution = workers.prepare(request, candidate, path)
            self.assertEqual(len(observations), 1)
            self.assertLess(contribution.preview.geometry.ligand, 1e-4)
            with self.assertRaisesRegex(ValueError, 'failed to open file'):
                workers.prepare(request, candidate, str(Path(folder) / 'missing.pdb'))
            entered, resume = Event(), Event()
            exchange = pocket_jobs._exchange

            def gated_exchange(process, value):
                if len(value) == 3:
                    entered.set()
                    if not resume.wait(5):
                        raise TimeoutError('Cancellation test did not resume')
                return exchange(process, value)

            with ThreadPoolExecutor(max_workers=1) as executor, patch.object(pocket_jobs, '_exchange', gated_exchange):
                future = executor.submit(workers.prepare, request, candidate, path)
                self.assertTrue(entered.wait(5), 'File worker did not start')
                request.cancel.set()
                workers.close()
                resume.set()
                with self.assertRaises(CancelledError):
                    future.result(timeout=5)

    def test_focus_copy_cannot_target_reloaded_source(self):
        from pymolplugins.host.focus_view import draw_focus, focus_identity, picked_source

        self.load()
        index = self.api.index('reference and name C1')[0][1]
        state, residue = focus_identity(self.api, 'reference', index)
        layer = draw_focus(self.api, '_pp_focus_test', 'reference', index, state, residue)
        picked = f'%{layer.copies[0]} and index 1'
        self.assertIsNotNone(picked_source(self.api, picked, [layer]))
        self.api.delete('reference')
        self.load()
        self.assertIsNone(picked_source(self.api, picked, [layer]))
        layer.delete(self.api)

    def test_host_comparison_restores_only_original_sources(self):
        from pymolplugins.host import compare_structures

        self.load('reference')
        self.load('candidate')
        self.api.translate([3, 5, 2], object='reference', camera=0)
        before = self.api.get_coords('reference').copy()
        controller = SimpleNamespace(overlays={}, original_visibility={}, rmsds={})
        with patch.object(compare_structures, 'cmd', self.api), patch.object(comparison, 'cmd', self.api):
            staged, _, _ = compare_structures._stage_comparison(
                ['reference', 'candidate'], 'reference', 'align', 'name CA', 'name CA', True)
            compare_structures.CompareStructuresDialog._show_overlays(controller, staged)
            self.assertNotIn('candidate', self.api.get_names('objects', enabled_only=1))
            self.api.delete('candidate')
            self.load('candidate')
            self.api.disable('candidate')
            for source in ('candidate', 'reference'):
                compare_structures.CompareStructuresDialog._remove_overlay(controller, source)
        self.assertNotIn('candidate', self.api.get_names('objects', enabled_only=1))
        self.assertIn('reference', self.api.get_names('objects', enabled_only=1))
        np.testing.assert_array_equal(self.api.get_coords('reference'), before)
        self.assertFalse(any(name.startswith('sa_compare_') for name in self.api.get_names('objects')))

    def test_protein_reference_fit_preserves_user_selections(self):
        self.load('candidate')
        self.load()
        self.api.rotate('z', 40, object='candidate', camera=0)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            path = str(Path(folder) / 'reference.pdb')
            self.api.save(path, 'reference', state=1)
            track = ReferenceTrack()
            track.load(self.api, 'protein', path, 'chain A')
            self.api.select('_align2', 'reference and name C1', enable=0)
            before = self.api.index('_align2')
            track.apply(self.api, 'candidate',
                        dict(mode='protein', row_selection='chain A', direction='row_to_reference'), [])
            np.testing.assert_allclose(self.api.get_coords('candidate and name CA', 1),
                                       self.api.get_coords(track.name, 1), atol=1e-5)
            self.assertEqual(self.api.get_names('selections'), ['_align2'])
            self.assertEqual(self.api.index('_align2'), before)
            track.clear(self.api)

    @unittest.skipUnless((Path.home() / '.pymolplugins/antibody-numbering/Scripts/python.exe').is_file(),
                         'Optional local ANARCII environment is absent')
    def test_real_imgt_numbering_maps_tandem_domains_to_observed_residues(self):
        sequence = ('EVQLVESGGGLVQPGGSLRLSCAASGFNIKDTYIHWVRQAPGKGLEWVARIYPTNGYTRYADSVKGRF'
                    'TISADTSKNTAYLQMNSLRAEDTAVYYCSRWGGDGFYAMDYWGQGTLVTVSS')
        sequence += 'GGGGSGGGGS' + sequence
        names = dict(zip('ACDEFGHIKLMNPQRSTVWY',
                         'ALA CYS ASP GLU PHE GLY HIS ILE LYS LEU MET ASN PRO GLN ARG SER THR VAL TRP TYR'.split()))
        model = Indexed()
        for i, aa in enumerate(sequence):
            atom = Atom()
            atom.name, atom.symbol, atom.resn, atom.resi, atom.chain = 'CA', 'C', names[aa], str(i + 1), 'H'
            atom.hetatm = 0
            atom.coord = [i * 3.8, 0, 0]
            model.add_atom(atom)
        self.api.load_model(model, 'antibody', zoom=0)
        chains = capture_chains(self.api, 'antibody', AntibodyRequest(('H',), ()))
        self.assertEqual(chains[0].sequence, sequence)
        numbered = number_chains(chains)
        self.assertEqual([domain[0] for domain in numbered[sequence]], [0, 130])
        view = AntibodyCartoon()
        view.draw(self.api, chains, numbered)
        self.assertEqual(len(view.rows), 240)
        for domain in ('1', '2'):
            self.assertEqual({row[-1] for row in view.rows if row[5] == domain} - {''},
                             {'CDR1', 'CDR2', 'CDR3'})
        view.clear(self.api)


if __name__ == '__main__':
    unittest.main()
