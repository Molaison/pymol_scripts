"""Watch scene membership during an explicit comparison session."""

from collections import deque

from pymol import cmd
from pymol.Qt import QtCore

from .review_view_presets import loaded_molecules
from .reference_track import molecule_identity


_active = None
_POLL_INTERVAL_MS = 250
_SCAN_INTERVAL_MS = 8
_TEMPORARY = ("_pp_", "_sa_", "sa_compare_", "sa_pocket_", "sa_reference_", "sa_tx_")


def molecule_signature(api, name):
    state = api.get_object_state(name)
    selection = f"%{name}"
    geometry, bonds = _geometry_signature(api, selection, state)
    first = api.get_coords(selection, 1) if state != 1 else None
    return (molecule_identity(api, name), state, geometry, bonds,
            first.tobytes() if first is not None else None)


def _geometry_signature(api, selection, state):
    get_bonds = getattr(api, "get_bonds", None)
    if get_bonds is not None:
        atoms = []
        api.iterate_state(
            state, selection, "record((segi, chain, resi, resn, name, elem, alt, formal_charge, vdw))",
            space={"record": atoms.append},
        )
        coords = api.get_coords(selection, state)
        geometry = (tuple(atoms), coords.tobytes() if coords is not None else None)
        return geometry, tuple(get_bonds(selection, state))

    model = api.get_model(selection, state)
    atoms = tuple((a.segi, a.chain, a.resi, a.resn, a.name, a.symbol, a.alt, a.formal_charge,
                   a.vdw, tuple(a.coord)) for a in model.atom)
    bonds = tuple((tuple(bond.index), bond.order) for bond in model.bond)
    return atoms, bonds


class CompareScene:
    def __init__(self, parent, changed, leave):
        self.changed = changed
        self.leave = leave
        self.names = set()
        self.molecules = {}
        self._scan = None
        self.timer = QtCore.QTimer(parent)
        self.timer.setInterval(_POLL_INTERVAL_MS)
        self.timer.timeout.connect(self._poll)

    @property
    def active(self):
        return _active is self

    def start(self):
        global _active
        if self.active:
            return
        if _active is not None:
            _active.leave()
        _active = self
        self.names = set(cmd.get_names("objects"))
        self.molecules = self._molecules()
        self.timer.start(_POLL_INTERVAL_MS)

    def stop(self):
        global _active
        if self.active:
            _active = None
        self.timer.stop()
        self._scan = None
        self.names.clear()
        self.molecules.clear()

    def _sources(self):
        return [name for name in loaded_molecules(cmd) if not name.startswith(_TEMPORARY)]

    def _molecules(self):
        return {name: molecule_signature(cmd, name) for name in self._sources()}

    def _poll(self):
        current = set(cmd.get_names("objects"))
        if self._scan is None:
            self._scan = current, deque(self._sources()), {}
        names, pending, molecules = self._scan
        if pending:
            name = pending.popleft()
            if name in current:
                molecules[name] = molecule_signature(cmd, name)
        if pending:
            # Yield to the viewport between source snapshots.
            if self.timer.interval() != _SCAN_INTERVAL_MS:
                self.timer.setInterval(_SCAN_INTERVAL_MS)
            return
        self._scan = None
        if self.timer.interval() != _POLL_INTERVAL_MS:
            self.timer.setInterval(_POLL_INTERVAL_MS)
        # Objects loaded mid-scan are picked up by the next complete pass.
        names.intersection_update(current)
        molecules = {name: signature for name, signature in molecules.items() if name in names}
        new, removed = names - self.names, self.names - names
        modified = {name for name in molecules.keys() & self.molecules.keys()
                    if molecules[name] != self.molecules[name]}
        new |= modified
        removed |= modified
        self.names = names
        self.molecules = molecules
        if new or removed:
            added = [name for name in molecules if name in new]
            self.changed(added, removed)
