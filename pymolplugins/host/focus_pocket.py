"""Interactive Focus pocket options and background probe-point rendering."""

from weakref import WeakKeyDictionary

from pymol.Qt import QtCore, QtWidgets
from pymol.gui import get_qtwindow

from pymolplugins.annotation.bundle_view import CandidateCavity, prepare_candidate_cavity
from pymolplugins.annotation.panel_style import apply_style
from pymolplugins.annotation.view_controls import CavityControls
from pymolplugins.annotation.view_jobs import CavityJobs


_DEFAULTS = {"radius": 5.0, "transparency": 0.7, "facing": True}
_LAYERS = {"surface": "Pocket surface", "ligand": "Ligand surface", "voids": "Void points"}
_POCKETS = WeakKeyDictionary()


def pocket_for(wizard):
    pocket = _POCKETS.get(wizard)
    if pocket is None:
        pocket = _POCKETS[wizard] = FocusPocket(wizard.cmd, wizard.session)
    return pocket


def close_pocket(wizard):
    pocket = _POCKETS.pop(wizard, None)
    if pocket:
        if pocket.jobs:
            pocket.jobs.close_cavity()
        if pocket.dialog:
            dialog, pocket.dialog = pocket.dialog, None
            dialog.close()
            dialog.deleteLater()


def _option(key, label, value):
    return [1, label, f"cmd.get_wizard().set_pocket_option({key!r}, {value!r})"]


class PocketLayersDialog(QtWidgets.QDialog):
    def __init__(self, pocket):
        super().__init__(get_qtwindow(), QtCore.Qt.Tool)
        self.pocket = pocket
        self.setWindowTitle("Pocket layers")
        self.setMinimumWidth(330)
        layout = QtWidgets.QVBoxLayout(self)
        self.checks = {}
        for kind, label in _LAYERS.items():
            check = QtWidgets.QCheckBox(label)
            check.toggled.connect(lambda enabled, layer=kind: pocket.set_layer(layer, enabled))
            self.checks[kind] = check
            layout.addWidget(check)
        self.cavities = CavityControls(self, on_toggle=pocket.set_void_layer)
        layout.addWidget(self.cavities)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        apply_style(self, "focusPocketLayers")

    def refresh(self):
        pocket = self.pocket
        for kind, check in self.checks.items():
            with QtCore.QSignalBlocker(check):
                check.setChecked(pocket.visible[kind])
        for kind, check in self.cavities.checks.items():
            with QtCore.QSignalBlocker(check):
                check.setChecked(pocket.void_layers[kind])
        visible = pocket.visible["voids"]
        result = pocket.layer.void_result if pocket.layer else None
        self.cavities.reset(visible)
        if visible and result is not None:
            self.cavities.show_result(result)
        notes = list(pocket.notes)
        if (visible or pocket.visible["ligand"]) and (not pocket.layer or not pocket.layer.ligand_selection):
            notes.append("Click a ligand to show its surface or nearby void points.")
        if visible and result is None and pocket.cavity_note:
            notes.append(pocket.cavity_note)
        self.status.setText("\n".join(notes))
        self.status.setVisible(bool(notes))
        self.adjustSize()


class FocusPocket:
    def __init__(self, api, session):
        self.api = api
        self.session = session
        saved = session.get("pocket_options", {})
        self.options = {key: saved.get(key, value) for key, value in _DEFAULTS.items()}
        self.visible = dict.fromkeys(_LAYERS, False)
        self.void_layers = {"recesses": True, "enclosed": True}
        self.dialog = None
        self.layer = None
        self.jobs = None
        self.request = None
        self.notes = []
        self.cavity_note = ""

    def menu(self):
        options = self.options
        rows = [[2, "Pocket options", ""]]
        for key, title, choices in (
            ("radius", f"Radius: {options['radius']:g} A",
             [(f"{value} A", value) for value in (3, 4, 5, 6, 8, 10)]),
            ("transparency", f"Transparency: {options['transparency']:.0%}",
             [(f"{value:.0%}", value) for value in (0.4, 0.55, 0.7, 0.8, 0.9)]),
            ("facing", "Wall: " + ("Faces ligand" if options["facing"] else "Full pocket"),
             [("Ligand-facing wall", True), ("Full local surface", False)]),
        ):
            rows.append([1, title, [_option(key, label, value) for label, value in choices]])
        return rows

    def show_layers(self):
        if not isinstance(QtWidgets.QApplication.instance(), QtWidgets.QApplication):
            raise RuntimeError("Pocket layers needs the PyMOL Qt window.")
        if self.dialog is None:
            self.dialog = PocketLayersDialog(self)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()

    def set_layer(self, kind, enabled):
        if kind == "voids" and enabled and not self.visible[kind]:
            self.void_layers.update(recesses=True, enclosed=True)
        self.visible[kind] = enabled
        self.update(self.layer)
        self.api.refresh_wizard()

    def set_void_layer(self, kind, enabled):
        self.void_layers[kind] = enabled
        self._show_void_layers()

    def set_option(self, key, value):
        self.options[key] = value
        self.session["pocket_options"] = dict(self.options)

    def update(self, layer):
        if layer is not self.layer:
            self._cancel_voids()
            self.layer = layer
        self.notes = []
        if layer is None:
            self._refresh_layers()
            return
        options = self.options
        for title, draw, arguments in (
            ("Pocket surface", layer.show_surface,
             (self.visible["surface"], options["radius"], options["transparency"], options["facing"])),
            ("Ligand surface", layer.show_ligand_surface, (self.visible["ligand"],)),
        ):
            try:
                draw(self.api, *arguments)
            except Exception as error:
                self.notes.append(f"{title}: {error}")
        try:
            self._sync_voids(self.visible["voids"] and bool(layer.ligand_selection))
        except Exception as error:
            self.notes.append(f"Void points: {error}")
        self._refresh_layers()

    def _refresh_layers(self):
        if self.dialog is not None:
            self.dialog.refresh()

    def _cancel_voids(self):
        if self.jobs:
            self.jobs.cancel_cavity()
        self.request = None
        self.cavity_note = ""

    def _sync_voids(self, visible):
        layer = self.layer
        if not visible:
            self._cancel_voids()
        if layer.void_result is not None:
            self._show_void_layers()
            if visible:
                self.cavity_note = self._void_status(layer.void_result)
        elif visible and self.request is not layer:
            self._start_voids(layer)

    def _start_voids(self, layer):
        self.request = layer
        if QtCore.QCoreApplication.instance() is None:
            self._status("Void points require the Qt viewport.")
            return
        view = CandidateCavity(
            "Void points", layer.copies[0],
            lambda renderer: renderer.capture_cavity(
                self.api, f"%{layer.source} and polymer", layer.ligand_selection, layer.state
            ),
        )
        try:
            compute, draw = prepare_candidate_cavity(self.api, view)
        except Exception as error:
            self._status(f"Void points: {error}")
            return

        def show(result):
            rendered = draw(result)
            layer.void_result = rendered
            layer.names.extend(rendered.objects.values())
            self._show_void_layers()
            return self._void_status(rendered), rendered.tooltip

        if self.jobs is None:
            self.jobs = CavityJobs(None, self._is_current, self._status)
        self.jobs.identity = layer
        self.jobs.start_cavity(view.title, compute, show)

    def _is_current(self, layer):
        return self.layer is layer and layer.source in self.api.get_names("objects", enabled_only=1)

    def _show_void_layers(self):
        if self.layer and self.layer.void_result is not None:
            for kind, name in self.layer.void_result.objects.items():
                visible = self.visible["voids"] and self.void_layers[kind]
                (self.api.enable if visible else self.api.disable)(name)

    @staticmethod
    def _void_status(rendered):
        recess, enclosed = rendered.volumes["recesses"], rendered.volumes["enclosed"]
        return f"Voids A^3  |  Cyan recess {recess:.1f}  |  Violet enclosed {enclosed:.1f}"

    def _status(self, message, _tooltip=""):
        self.cavity_note = message
        self._refresh_layers()
        self.api.refresh_wizard()
