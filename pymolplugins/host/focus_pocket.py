"""Interactive Focus pocket options and background probe-point rendering."""

from weakref import WeakKeyDictionary, ref

from pymol.Qt import QtCore, QtWidgets
from pymol.gui import get_qtwindow

from pymolplugins.annotation.bundle_view import CandidateCavity, prepare_candidate_cavity
from pymolplugins.annotation.panel_style import apply_style
from pymolplugins.annotation.view_controls import CavityControls
from pymolplugins.annotation.view_jobs import CavityJobs
from pymolplugins.annotation.compare_scene import molecule_signature

from .workspace import _check_settings, window_settings


_DEFAULTS = {"radius": 5.0, "transparency": 0.7, "facing": True}
_LAYERS = {"surface": "Pocket surface", "ligand": "Ligand surface", "voids": "Void points"}
_SHOW_ALL_SETTING = "interactive_focus/show_all_pockets"
_POCKETS = WeakKeyDictionary()


def pocket_for(wizard):
    pocket = _POCKETS.get(wizard)
    if pocket is None:
        pocket = _POCKETS[wizard] = FocusPocket(wizard)
    return pocket


def close_pocket(wizard):
    pocket = _POCKETS.pop(wizard, None)
    if pocket:
        pocket.close()


def _option(key, label, value):
    return [1, label, f"cmd.get_wizard().set_pocket_option({key!r}, {value!r})"]


class PocketLayers(QtWidgets.QWidget):
    def __init__(self, pocket, parent):
        super().__init__(parent)
        self.pocket = pocket
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        self.show_all = QtWidgets.QCheckBox("Show all focused pockets")
        self.show_all.setToolTip("Show the selected pocket layers for every focus in Per object mode.")
        self.show_all.toggled.connect(pocket.set_show_all)
        layout.addWidget(self.show_all)
        self.checks = {}
        for kind, label in _LAYERS.items():
            check = QtWidgets.QCheckBox(label)
            check.toggled.connect(lambda enabled, layer=kind: pocket.set_layer(layer, enabled))
            self.checks[kind] = check
            layout.addWidget(check)
        self.cavities = CavityControls(self, on_toggle=pocket.set_void_layer, compact=True)
        layout.addWidget(self.cavities)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        apply_style(self, "focusPocketLayers")

    def refresh(self):
        pocket = self.pocket
        with QtCore.QSignalBlocker(self.show_all):
            self.show_all.setEnabled(pocket.can_show_all)
            self.show_all.setChecked(pocket.can_show_all and pocket.show_all)
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
        if (visible or pocket.visible["ligand"]) and not any(
            layer.ligand_selection for layer in pocket.displayed.values()
        ):
            notes.append("Click a ligand to show its surface or nearby void points.")
        if visible:
            notes.extend(pocket.void_errors.values())
            if pocket.request is not None and pocket.void_status:
                notes.append(pocket.void_status)
        self.status.setText("\n".join(notes))
        self.status.setVisible(bool(notes))
        self.updateGeometry()


class FocusPocket:
    def __init__(self, wizard):
        self.api = wizard.cmd
        self.session = wizard.session
        self.wizard_ref = ref(wizard)
        saved = self.session.get("pocket_options", {})
        self.options = {key: saved.get(key, value) for key, value in _DEFAULTS.items()}
        self.show_all = window_settings().value(_SHOW_ALL_SETTING, False, type=bool)
        self.visible = dict.fromkeys(_LAYERS, False)
        self.void_layers = {"recesses": True, "enclosed": True}
        self.panel = None
        self.host_wizard = None
        self.fallback_dock = None
        self.displayed = {}
        self.jobs = None
        self.request = None
        self.notes = []
        self.void_errors = {}
        self.void_status = ""

    @property
    def layer(self):
        wizard = self.wizard_ref()
        return wizard.layer if wizard else None

    @property
    def can_show_all(self):
        wizard = self.wizard_ref()
        return wizard is not None and wizard.scope == "object"

    @property
    def cavity_note(self):
        if self.void_status:
            return self.void_status
        if self.visible["voids"] and self.layer and self.layer.void_result is not None:
            return self._void_status(self.layer.void_result)
        return ""

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

    def attach_layers(self, wizard):
        window = get_qtwindow()
        if not isinstance(window, QtWidgets.QMainWindow):
            return
        contents = getattr(window, "contentsPanelFrame", None)
        self.host_wizard = getattr(contents, "wizard_dialog", None)
        if self.host_wizard is not None:
            self.panel = PocketLayers(self, self.host_wizard)
            self.host_wizard.wizardShownRequested.connect(self._insert_layers)
            self.host_wizard.wizardHiddenRequested.connect(self.panel.hide)
            self._insert_layers()
        else:
            dock = self.fallback_dock = QtWidgets.QDockWidget("Pocket layers", window)
            dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
            self.panel = PocketLayers(self, dock)
            dock.setWidget(self.panel)
            window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
            self.panel.refresh()
            dock.show()

    def _insert_layers(self):
        native = self.host_wizard
        if native.wizard is not self.wizard_ref():
            return
        layout = native.layout
        if layout.indexOf(self.panel) < 0:
            # PyMOL detaches the controls when it rebuilds the native wizard.
            layout.insertWidget(max(0, layout.count() - 2), self.panel)
        self.panel.refresh()
        self.panel.show()

    def close(self):
        if self.jobs:
            self.jobs.close_cavity()
        if self.host_wizard is not None:
            self.host_wizard.wizardShownRequested.disconnect(self._insert_layers)
            self.host_wizard.wizardHiddenRequested.disconnect(self.panel.hide)
        if self.panel is not None:
            self.panel.hide()
            self.panel.deleteLater()
        if self.fallback_dock is not None:
            self.fallback_dock.hide()
            self.fallback_dock.deleteLater()

    def set_layer(self, kind, enabled):
        if kind == "voids" and enabled and not self.visible[kind]:
            self.void_layers.update(recesses=True, enclosed=True)
        self.visible[kind] = enabled
        self.update()
        self.api.refresh_wizard()

    def set_show_all(self, enabled):
        settings = window_settings()
        settings.setValue(_SHOW_ALL_SETTING, enabled)
        settings.sync()
        _check_settings(settings)
        self.show_all = enabled
        self.update()
        self.api.refresh_wizard()

    def set_void_layer(self, kind, enabled):
        self.void_layers[kind] = enabled
        for layer in self.displayed.values():
            self._show_void_layers(layer)

    def set_option(self, key, value):
        self.options[key] = value
        self.session["pocket_options"] = dict(self.options)

    def update(self):
        active = self.layer
        if self.show_all and self.can_show_all:
            layers = dict(self.wizard_ref().layers)
        else:
            layers = {active.source: active} if active else {}
        self._hide_unselected(layers)
        self.displayed = layers
        if self.request is not None and not self._is_current(self.request):
            self._cancel_voids()
        if not self.visible["voids"]:
            self.void_errors.clear()
        self.notes = []
        for layer in layers.values():
            self._draw_surfaces(layer)
            self._show_void_layers(layer)
        self._next_void()
        self._refresh_layers()

    def _hide_unselected(self, layers):
        names = set()
        for source, layer in self.displayed.items():
            if layers.get(source) is layer:
                continue
            self.void_errors.pop(source, None)
            names.update((layer.surface, layer.ligand_surface))
            if layer.void_result is not None:
                names.update(layer.void_result.objects.values())
        if names:
            for name in names.intersection(self.api.get_names("objects", enabled_only=1)):
                self.api.disable(name)

    def _draw_surfaces(self, layer):
        options = self.options
        for title, draw, arguments in (
            ("Pocket surface", layer.show_surface,
             (self.visible["surface"], options["radius"], options["transparency"], options["facing"])),
            ("Ligand surface", layer.show_ligand_surface, (self.visible["ligand"],)),
        ):
            try:
                draw(self.api, *arguments)
            except Exception as error:
                self.notes.append(f"{layer.source} · {title}: {error}")

    def _refresh_layers(self):
        if self.panel is not None:
            self.panel.refresh()

    def _cancel_voids(self):
        if self.jobs:
            self.jobs.cancel_cavity()
        self.request = None
        self.void_status = ""

    def _next_void(self):
        if not self.visible["voids"] or self.request is not None:
            return
        for layer in reversed(self.displayed.values()):
            if (layer.ligand_selection and layer.void_result is None
                    and layer.source not in self.void_errors and self._is_current(layer)):
                try:
                    self._start_voids(layer)
                except Exception as error:
                    self.void_errors[layer.source] = f"{layer.source} · Void points: {error}"
                else:
                    return

    def _start_voids(self, layer):
        if QtCore.QCoreApplication.instance() is None:
            raise RuntimeError("Void points require the Qt viewport.")
        title = f"{layer.source} · Void points" if self.show_all and self.can_show_all else "Void points"
        view = CandidateCavity(
            title, layer.copies[0],
            lambda renderer: renderer.capture_cavity(
                self.api, f"%{layer.source} and polymer", layer.ligand_selection, layer.state
            ),
        )
        compute, draw = prepare_candidate_cavity(self.api, view)

        def show(result):
            rendered = draw(result)
            layer.void_result = rendered
            layer.names.extend(rendered.objects.values())
            self._show_void_layers(layer)
            return "", ""

        if self.jobs is None:
            self.jobs = CavityJobs(None, self._is_current, self._record_status, on_finished=self._finish_void)
        self.jobs.identity = layer
        self.jobs.start_cavity(view.title, compute, show)
        self.request = layer

    def _is_current(self, layer):
        return (self.visible["voids"] and self.displayed.get(layer.source) is layer
                and layer.source in self.api.get_names("objects", enabled_only=1)
                and molecule_signature(self.api, layer.source) == layer.source_signature)

    def _show_void_layers(self, layer):
        if layer.void_result is not None:
            for kind, name in layer.void_result.objects.items():
                visible = self.visible["voids"] and self.void_layers[kind]
                (self.api.enable if visible else self.api.disable)(name)

    @staticmethod
    def _void_status(rendered):
        recess, enclosed = rendered.volumes["recesses"], rendered.volumes["enclosed"]
        return f"Voids A^3  |  Cyan recess {recess:.1f}  |  Violet enclosed {enclosed:.1f}"

    def _record_status(self, message, _tooltip=""):
        self.void_status = message

    def _finish_void(self):
        layer, self.request = self.request, None
        if self._is_current(layer) and layer.void_result is None:
            self.void_errors[layer.source] = self.void_status
        self.void_status = ""
        self._next_void()
        self._refresh_layers()
        self.api.refresh_wizard()
