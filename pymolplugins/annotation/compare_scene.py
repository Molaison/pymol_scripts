"""Watch scene membership during an explicit comparison session."""

from pymol import cmd
from pymol.Qt import QtCore

from .review_view_presets import loaded_molecules
from .reference_track import molecule_identity


_active = None
_TEMPORARY = ("_pp_", "_sa_", "sa_compare_", "sa_pocket_", "sa_reference_", "sa_tx_")


def molecule_signature(api, name):
    state = api.get_object_state(name)
    model = api.get_model(f"%{name}", state)
    first = api.get_coords(f"%{name}", 1) if state != 1 else None
    return (molecule_identity(api, name), state,
            tuple((a.segi, a.chain, a.resi, a.resn, a.name, a.symbol, a.alt, a.formal_charge,
                   a.vdw, tuple(a.coord)) for a in model.atom),
            tuple((tuple(bond.index), bond.order) for bond in model.bond),
            first.tobytes() if first is not None else None)


class CompareScene:
    def __init__(self, parent, changed, leave):
        self.changed = changed
        self.leave = leave
        self.names = set()
        self.molecules = {}
        self.timer = QtCore.QTimer(parent)
        self.timer.setInterval(250)
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
        self.timer.start()

    def stop(self):
        global _active
        if self.active:
            _active = None
        self.timer.stop()
        self.names.clear()
        self.molecules.clear()

    def _molecules(self):
        return {name: molecule_signature(cmd, name) for name in loaded_molecules(cmd)
                if not name.startswith(_TEMPORARY)}

    def _poll(self):
        names = set(cmd.get_names("objects"))
        molecules = self._molecules()
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
