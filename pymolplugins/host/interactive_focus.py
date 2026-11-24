"""PyMOL wizard mode for clicking through local contact environments."""

from traceback import print_exc
from uuid import uuid4

from pymol import cmd
from pymol.Qt import QtCore
from pymol.wizard import Wizard
from pymolplugins.annotation.compare_scene import molecule_signature
from pymolplugins.core.pymol_display import display_updates

from .focus_pocket import close_pocket, pocket_for
from .focus_view import RADIUS, draw_focus, focus_identity, picked_source
from .workspace import _check_settings, window_settings


_CAMERAS = {"keep": "Keep view", "center": "Center smoothly", "frame": "Frame pocket"}
_SCOPES = {"scene": "Whole scene", "object": "Per object"}
_SCOPE_SETTING = "interactive_focus/scope"
_SOURCE_CHECK_INTERVAL_MS = 400
_CONTACT_LEVELS = {
    "off": ("Off", ()),
    "polar": ("Polar", ("polar_contacts", "buried_unsatisfied_polar")),
    "ionic": ("Polar + salts", ("polar_contacts", "buried_unsatisfied_polar", "salt_bridges")),
    "interface": ("Interface", ("polar_contacts", "buried_unsatisfied_polar", "salt_bridges", "pi_stacks")),
}


class InteractiveFocus(Wizard):
    def __init__(self, api):
        super().__init__(_self=api)
        self.layers = {}
        saved_scope = window_settings().value(_SCOPE_SETTING, "scene", type=str)
        self.scope = saved_scope if saved_scope in _SCOPES else "scene"
        self.camera = self.session.get("camera", "keep")
        self.contacts = self.session.get("contacts", "interface")
        self.error = ""
        self.closed = False
        self.handling_pick = False
        self._source_check_timer = None
        self.prefix = f"_pp_focus_{uuid4().hex[:8]}"
        self.capture = f"{self.prefix}_pick"
        self.enabled_selections = tuple(api.get_names("selections", enabled_only=1))
        self.settings = {
            name: (api.get_setting_int(name), value) for name, value in (
                ("mouse_selection_mode", 0),
                ("auto_hide_selections", 0),
                ("auto_show_selections", 1),
            )
        }

    def __getstate__(self):
        state = super().__getstate__()
        state.pop("_source_check_timer", None)
        return state

    @property
    def layer(self):
        return next(reversed(self.layers.values()), None)

    def start(self):
        for name, (_, value) in self.settings.items():
            self.cmd.set(name, value)
        self.cmd.select(self.capture, "none", enable=1, quiet=1)

    def get_event_mask(self):
        return (Wizard.event_mask_select | Wizard.event_mask_pick |
                Wizard.event_mask_dirty | Wizard.event_mask_state | Wizard.event_mask_view)

    def get_panel(self):
        clear = [[2, "Clear focus", "cmd.get_wizard().clear_active()"]]
        if self.scope == "object":
            clear.append([2, "Clear all focuses", "cmd.get_wizard().clear()"])
        return [
            [1, f"Interactive focus ({RADIUS:g} A)", ""],
            [3, f"Scope: {_SCOPES[self.scope]}", "scope"],
            [3, f"Camera: {_CAMERAS[self.camera]}", "camera"],
            [3, f"Contacts: {_CONTACT_LEVELS[self.contacts][0]}", "contacts"],
            [3, "Pocket options", "pocket"],
            *clear,
            [2, "Done", "cmd.set_wizard()"],
        ]

    def get_menu(self, tag):
        if tag == "scope":
            return [[2, "Focus scope", ""]] + [
                [1, label, f"cmd.get_wizard().set_scope({key!r})"] for key, label in _SCOPES.items()
            ]
        if tag == "pocket":
            return pocket_for(self).menu()
        if tag == "camera":
            return [[2, "Camera on click", ""]] + [
                [1, label, f"cmd.get_wizard().set_camera({key!r})"] for key, label in _CAMERAS.items()
            ] + [[1, "Frame this pocket once", "cmd.get_wizard().frame_once()"]]
        if tag == "contacts":
            return [[2, "Show contacts", ""]] + [
                [1, label, f"cmd.get_wizard().set_contacts({key!r})"]
                for key, (label, _kinds) in _CONTACT_LEVELS.items()
            ]
        return super().get_menu(tag)

    def get_prompt(self):
        if self.error:
            return [self.error, "Click another atom to refocus."]
        if self.layer is None:
            return ["Click a residue or ligand to focus."]
        hbonds, salts, stacks = self.layer.counts
        labels = (
            ("polar_contacts", f"Teal polar {hbonds}"),
            ("salt_bridges", f"Magenta salt bridges {salts}"),
            ("pi_stacks", f"Violet pi stacks {stacks}"),
        )
        kinds = _CONTACT_LEVELS[self.contacts][1]
        prompt = [self.layer.label]
        if kinds:
            prompt.append("  |  ".join(label for kind, label in labels if kind in kinds))
        if self.layer.buried_count and "buried_unsatisfied_polar" in kinds:
            prompt.append(f"Orange spheres: buried unsatisfied polar {self.layer.buried_count}")
        pocket = pocket_for(self)
        prompt.extend(pocket.notes)
        if pocket.cavity_note:
            prompt.append(pocket.cavity_note)
        return prompt

    def set_camera(self, mode):
        self.camera = self.session["camera"] = mode
        if self.layer:
            self.layer.move_camera(self.cmd, mode)
        self.cmd.refresh_wizard()

    def frame_once(self):
        if self.layer:
            self.layer.move_camera(self.cmd, "frame")

    def set_contacts(self, level):
        self.contacts = self.session["contacts"] = level
        for layer in self.layers.values():
            layer.show_contacts(self.cmd, _CONTACT_LEVELS[level][1])
        self.cmd.refresh_wizard()

    def set_scope(self, scope):
        if scope not in _SCOPES:
            raise ValueError(f"Unknown focus scope: {scope}")
        settings = window_settings()
        settings.setValue(_SCOPE_SETTING, scope)
        settings.sync()
        _check_settings(settings)
        self.scope = scope
        if scope == "scene" and self.layer:
            self._remove_layers([source for source in self.layers if source != self.layer.source])
        else:
            pocket_for(self).update()
        self.cmd.refresh_wizard()

    def set_pocket_option(self, key, value):
        pocket = pocket_for(self)
        pocket.set_option(key, value)
        pocket.update()
        self.cmd.refresh_wizard()

    def do_select(self, name):
        if self.closed or self.handling_pick or name != self.capture:
            return
        self.handling_pick = True
        try:
            with display_updates(self.cmd):
                self._focus_pick(f"%{name}")
                self.cmd.select(self.capture, "none", enable=1, quiet=1)
        finally:
            self.handling_pick = False

    def do_pick(self, bond_flag):
        if self.closed or self.handling_pick or bond_flag:
            return
        self.handling_pick = True
        try:
            with display_updates(self.cmd):
                self._focus_pick("?pk1")
                self.cmd.unpick()
        finally:
            self.handling_pick = False

    def do_dirty(self):
        if self.closed or self.handling_pick:
            return
        if self.capture not in self.cmd.get_names("selections", enabled_only=1):
            self.clear()
            self.cmd.select(self.capture, "none", enable=1, quiet=1)
        elif self.layers:
            enabled = set(self.cmd.get_names("objects", enabled_only=1))
            stale = [source for source in self.layers if source not in enabled]
            if stale:
                self._remove_layers(stale)
                self.cmd.refresh_wizard()
            if self.layers and self._source_check_timer is None:
                self._check_source_signatures()

    def do_view(self):
        timer = self._source_check_timer
        if timer is not None and timer.isActive():
            timer.start()

    def _watch_sources(self):
        if self._source_check_timer is None:
            if QtCore.QCoreApplication.instance() is None:
                return
            # Native edits such as alter_state can bypass the wizard's dirty event.
            timer = self._source_check_timer = QtCore.QTimer()
            timer.setInterval(_SOURCE_CHECK_INTERVAL_MS)
            timer.timeout.connect(self._check_source_signatures)
        if not self._source_check_timer.isActive():
            self._source_check_timer.start()

    def _check_source_signatures(self):
        if self.closed or self.handling_pick or not self.layers:
            return
        enabled = set(self.cmd.get_names("objects", enabled_only=1))
        stale = [source for source, layer in self.layers.items()
                 if source not in enabled or molecule_signature(self.cmd, source) != layer.source_signature]
        if stale:
            self._remove_layers(stale)
            self.cmd.refresh_wizard()

    def do_state(self, _state):
        if self.closed or self.handling_pick or not self.layers:
            return
        objects = set(self.cmd.get_names("objects", enabled_only=1))
        stale = [source for source, layer in self.layers.items()
                 if source not in objects or layer.state != self.cmd.get_object_state(source)]
        if stale:
            self._remove_layers(stale)
            self.cmd.refresh_wizard()

    def _focus_pick(self, selection):
        try:
            picked = picked_source(self.cmd, selection, self.layers.values())
            if picked is None:
                return
            source, index = picked
            state, residue = focus_identity(self.cmd, source, index)
            previous = self.layers.get(source)
            if previous and (state, residue) == (previous.state, previous.residue):
                self._remove_layers([source])
            else:
                layer = draw_focus(self.cmd, self.prefix, source, index, state, residue)
                if self.scope == "scene":
                    replaced = tuple(self.layers.values())
                    self.layers.clear()
                else:
                    replaced = (self.layers.pop(source),) if previous else ()
                self.layers[source] = layer
                self._watch_sources()
                for old in replaced:
                    old.delete(self.cmd)
                layer.show_contacts(self.cmd, _CONTACT_LEVELS[self.contacts][1])
                pocket_for(self).update()
                layer.move_camera(self.cmd, self.camera)
            self.error = ""
        except Exception as error:
            self.error = f"Focus skipped: {error}"
            print_exc()
        self.cmd.refresh_wizard()

    def _remove_layers(self, sources):
        removed = [self.layers.pop(source) for source in sources]
        if not self.layers and self._source_check_timer is not None:
            self._source_check_timer.stop()
        with display_updates(self.cmd):
            for layer in removed:
                layer.delete(self.cmd)
            pocket_for(self).update()

    def clear_active(self):
        if self.layer:
            self._remove_layers([self.layer.source])
        self.error = ""
        self.cmd.refresh_wizard()

    def clear(self):
        self._remove_layers(tuple(self.layers))
        self.error = ""
        if not self.closed:
            self.cmd.refresh_wizard()

    def cleanup(self):
        from .toolbar import set_focus_active
        set_focus_active(False)
        self.closed = True
        self.clear()
        close_pocket(self)
        self.cmd.delete(self.capture)
        existing = set(self.cmd.get_names("selections"))
        for name in self.enabled_selections:
            if name in existing:
                self.cmd.enable(name)
        for name, (previous, temporary) in self.settings.items():
            if self.cmd.get_setting_int(name) == temporary:
                self.cmd.set(name, previous)


def toggle_focus(api=cmd):
    """Start or stop interactive focus in the active PyMOL view."""
    stack = api.get_wizard_stack()
    active = next((wizard for wizard in stack if isinstance(wizard, InteractiveFocus)), None)
    if active is not None:
        if api.get_wizard() is active:
            api.set_wizard()
        else:
            active.cleanup()
            api.set_wizard_stack([wizard for wizard in stack if wizard is not active])
        return "stopped"
    wizard = InteractiveFocus(api)
    try:
        wizard.start()
        api.set_wizard(wizard)
    except Exception:
        wizard.cleanup()
        raise
    pocket_for(wizard).attach_layers(wizard)
    from .toolbar import set_focus_active
    set_focus_active(True)
    return "started"
