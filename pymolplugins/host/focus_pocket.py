"""Interactive Focus pocket options and background probe-point rendering."""

from weakref import WeakKeyDictionary

from pymol.Qt import QtCore

from pymolplugins.annotation.bundle_view import CandidateCavity, prepare_candidate_cavity
from pymolplugins.annotation.view_jobs import CavityJobs


_DEFAULTS = {"radius": 5.0, "transparency": 0.7, "facing": True, "ligand": True, "voids": True}
_POCKETS = WeakKeyDictionary()


def pocket_for(wizard):
    pocket = _POCKETS.get(wizard)
    if pocket is None:
        pocket = _POCKETS[wizard] = FocusPocket(wizard.cmd, wizard.session)
    return pocket


def close_pocket(wizard):
    pocket = _POCKETS.pop(wizard, None)
    if pocket and pocket.jobs:
        pocket.jobs.close_cavity()


def _option(key, label, value):
    return [1, label, f"cmd.get_wizard().set_pocket_option({key!r}, {value!r})"]


class FocusPocket:
    def __init__(self, api, session):
        self.api = api
        self.session = session
        self.options = {**_DEFAULTS, **session.get("pocket_options", {})}
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
        for key, title in (("ligand", "Ligand surface"), ("voids", "Void points")):
            rows.append(_option(key, f"{title}: " + ("On" if options[key] else "Off"), not options[key]))
        return rows

    def set_option(self, key, value):
        self.options[key] = value
        self.session["pocket_options"] = dict(self.options)

    def update(self, layer, enabled):
        if layer is not self.layer:
            self._cancel_voids()
            self.layer = layer
        self.notes = []
        if layer is None:
            return
        options = self.options
        for title, draw, arguments in (
            ("Pocket surface", layer.show_surface,
             (enabled, options["radius"], options["transparency"], options["facing"])),
            ("Ligand surface", layer.show_ligand_surface, (enabled and options["ligand"],)),
        ):
            try:
                draw(self.api, *arguments)
            except Exception as error:
                self.notes.append(f"{title}: {error}")
        try:
            self._sync_voids(enabled and options["voids"] and bool(layer.ligand_selection))
        except Exception as error:
            self.notes.append(f"Void points: {error}")

    def _cancel_voids(self):
        if self.jobs:
            self.jobs.cancel_cavity()
        self.request = None
        self.cavity_note = ""

    def _sync_voids(self, visible):
        layer = self.layer
        if not visible:
            self._cancel_voids()
        if layer.void_objects is not None:
            for name in layer.void_objects.values():
                (self.api.enable if visible else self.api.disable)(name)
            if visible:
                self.cavity_note = layer.void_status
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
            layer.void_objects = rendered.objects
            layer.names.extend(rendered.objects.values())
            recess, enclosed = rendered.volumes["recesses"], rendered.volumes["enclosed"]
            layer.void_status = f"Voids A^3  |  Cyan recess {recess:.1f}  |  Violet enclosed {enclosed:.1f}"
            return layer.void_status, rendered.tooltip

        if self.jobs is None:
            self.jobs = CavityJobs(None, self._is_current, self._status)
        self.jobs.identity = layer
        self.jobs.start_cavity(view.title, compute, show)

    def _is_current(self, layer):
        return self.layer is layer and layer.source in self.api.get_names("objects", enabled_only=1)

    def _status(self, message, _tooltip=""):
        self.cavity_note = message
        self.api.refresh_wizard()
