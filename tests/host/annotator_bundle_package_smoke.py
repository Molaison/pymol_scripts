"""Exercise Bundle review through the Qt UI of a Plugin Manager installation."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

from annotator_workflow_smoke import wait_for


STEP_PREFIX = (
    '/from pathlib import Path\n'
    '/from runpy import run_path\n'
)
STEP_SOURCE = (
    '/bundle_source = Path(r"{file}")\n'
    '/bundle_script = bundle_source.parent.parent / "pymol" / "acceptance_collection.py"\n'
    '/bundle_renderer = run_path(str(bundle_script))["acc_bundle_step"]\n'
)
LEGACY_FOUR_LINES = STEP_PREFIX + (
    '/bundle_step_path = Path(r"{file}").parent.parent / "pymol" / "acceptance_collection.py"\n'
    '/run_path(str(bundle_step_path))["acc_bundle_step"]("{object}", r"{file}")'
)
LEGACY_SIX_LINES = STEP_PREFIX + STEP_SOURCE + '/bundle_renderer("{object}", bundle_source)'
EXPLICIT_STEP = STEP_PREFIX + STEP_SOURCE + '/bundle_renderer("{object}", str(bundle_source))'


def desktop_name():
    """Confirm the Qt thread is running on the desktop created by the launcher."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    ]
    desktop = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
    name = ctypes.create_unicode_buffer(256)
    if not user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), None):
        raise ctypes.WinError(ctypes.get_last_error())
    expected = os.environ['PP_BUNDLE_SMOKE_DESKTOP']
    assert name.value == expected, (name.value, expected)
    return name.value


def _pdb(ligand_x):
    lines = []
    for index, (resi, x, y, z) in enumerate(((1, 0, 0, -4), (2, 4, 0, 0), (3, 4, 3, 4))):
        for offset, (atom, dx, dy, dz) in enumerate((
            ('N', -1, 0, 0), ('CA', 0, 0, 0), ('C', 1, 0, 0),
            ('O', 1.6, 0, 0.8), ('CB', 0, 1.4, 0),
        )):
            lines.append(f'ATOM  {index * 5 + offset + 1:5d} {atom:^4s} ALA A{resi:4d}    '
                         f'{x + dx:8.3f}{y + dy:8.3f}{z + dz:8.3f}  1.00 20.00          {atom[0]:>2s}')
    for serial, name, dx, element in ((16, 'C1', 0, 'C'), (17, 'O1', 1.3, 'O')):
        lines.append(f'HETATM{serial:5d} {name:^4s} LIG B   1    '
                     f'{ligand_x + dx:8.3f}{3:8.3f}{-4:8.3f}  1.00 20.00          {element:>2s}')
    return '\n'.join([*lines, 'END', ''])


def _bundle(directory, step, explicit, skill_root):
    paths = ['structures/one.pdb', 'structures/two.pdb']
    (directory / 'structures').mkdir(parents=True)
    (directory / paths[0]).write_text(_pdb(0), encoding='ascii')
    (directory / paths[1]).write_text(_pdb(1), encoding='ascii')
    (directory / 'reference.pdb').write_text(_pdb(0), encoding='ascii')
    (directory / 'surface.json').write_text(json.dumps({
        path: {'surface_residues': 'resi 1+2+3'} for path in paths
    }), encoding='utf-8')
    table = directory / 'sum_scores.csv'
    table.write_text('structure_path,score\n' + '\n'.join(f'{path},{i}' for i, path in enumerate(paths)), encoding='ascii')
    view = {
        'mode': 'bundle', 'view': 'ligand_pocket', 'path_column': 'structure_path',
        'display_columns': ['score'], 'protein_selection': 'chain A and polymer.protein',
        'ligand_selection': 'chain B and resn LIG', 'show_cavity': False,
        'membrane': {'reference_path': 'reference.pdb', 'reference_ca': 'chain A',
                     'surface_path': 'surface.json', 'bounds': [-3, 3], 'core_bounds': [-2, 2]},
    }
    (directory / 'bundle_view.json').write_text(json.dumps(view), encoding='utf-8')
    if explicit:
        review = {**view, 'step_cmd': step}
        override = {'steps': [{'type': 'object', 'name': 'candidate'},
                              {'type': 'commands'}, {'type': 'commands'}]}
        shutil.copytree(skill_root, directory / 'pymol')
    else:
        review = {'path_column': 'structure_path', 'step_cmd': step}
        # Earlier releases saved the old template back to this layer.
        override = {'step_cmd': step}
    (directory / 'review.json').write_text(json.dumps(review), encoding='utf-8')
    (directory / 'review.override.json').write_text(json.dumps(override), encoding='utf-8')
    return table


def _inspect_visit(cmd, annotation, row, expected, actual, previous):
    from pymolplugins.core.pymol_groups import group_parent_snapshot
    from pymolplugins.annotation.scene_groups import ancestors

    owner = annotation.STATE.current_object
    assert annotation.STATE.index == row and owner
    assert actual == [(expected, owner)], (row, actual)
    assert cmd.count_atoms(f'%{owner} and chain B') == 2
    enabled = set(cmd.get_names('objects', enabled_only=1))
    parents = group_parent_snapshot(cmd)
    visible = {name for name in enabled if set(ancestors(name, parents)).issubset(enabled)}
    for role in ('membrane_lower', 'membrane_upper', 'membrane_lower_rim', 'membrane_upper_rim', 'membrane_core'):
        objects = [name for name in visible if name.startswith(owner) and name.endswith(f'__{role}')]
        assert len(objects) == 1, (role, enabled)
    if previous and previous != owner:
        assert not any(name.startswith(previous) for name in visible), enabled
    return owner


def check_bundles(annotation, directory, menu):
    from pymol import cmd
    from pymol.Qt import QtWidgets
    from pymolplugins.annotation import bundle_view

    app = QtWidgets.QApplication.instance()
    action = next(action for action in menu.actions() if action.text() == 'PymolPlugins: Structure Annotator')
    action.trigger()
    app.processEvents()
    panel = annotation._panel
    real_step = annotation._run_pymol_commands
    real_draw = bundle_view._draw_bundle
    calls = []
    results = {}

    def track_step(template, context, **kwargs):
        if 'acc_bundle_step' in template:
            calls.append(('shared', context['object']))
        return real_step(template, context, **kwargs)

    def track_draw(api, owner, prepared):
        calls.append(('built-in', owner))
        return real_draw(api, owner, prepared)

    def settled(table, index):
        if panel.error_label.isVisible():
            raise AssertionError(panel.error_label.text())
        return (annotation.STATE.csv_path == str(table) and annotation.STATE.index == index
                and not panel._csv_task_id and panel._pending_navigation is None
                and not panel._view_jobs.timer.isActive())

    with patch.object(annotation, '_run_pymol_commands', track_step), patch.object(bundle_view, '_draw_bundle', track_draw):
        skill_root = Path(bundle_view.__file__).with_name('_bundle_skill')
        for name, step, explicit in (('legacy_four_lines', LEGACY_FOUR_LINES, False),
                                     ('legacy_six_lines', LEGACY_SIX_LINES, False),
                                     ('explicit_shared', EXPLICIT_STEP, True)):
            table = _bundle(directory / name, step, explicit, skill_root)
            calls.clear()
            panel.source_path_edit.setText(str(table))
            panel.source_open_button.click()
            expected = 'shared' if explicit else 'built-in'
            previous = None
            visits = []
            for visit, index in enumerate((0, 1, 0)):
                if visit:
                    panel._request_navigation(index)
                wait_for(app, lambda: settled(table, index))
                previous = _inspect_visit(cmd, annotation, index, expected, calls, previous)
                visits.append({'row': index + 1, 'renderer': expected, 'calls': len(calls)})
                calls.clear()
            results[name] = visits
    return {'desktop': desktop_name(), 'cases': results}
