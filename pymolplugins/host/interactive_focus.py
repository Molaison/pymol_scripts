"""PyMOL wizard mode for clicking through local contact environments."""

from traceback import print_exc
from uuid import uuid4

from pymol import cmd
from pymol.wizard import Wizard

from .focus_pocket import close_pocket, pocket_for
from .focus_view import RADIUS, draw_focus, focus_identity, picked_source


_CAMERAS = {"keep": "Keep view", "center": "Center smoothly", "frame": "Frame pocket"}
_CONTACT_LEVELS = {
    "off": ("Off", ()),
    "polar": ("Polar", ("polar_contacts",)),
    "ionic": ("Polar + salts", ("polar_contacts", "salt_bridges")),
    "interface": ("Interface", ("polar_contacts", "salt_bridges", "pi_stacks")),
}


class InteractiveFocus(Wizard):
    def __init__(self, api):
        super().__init__(_self=api)
        self.layer = None
        self.camera = self.session.get("camera", "keep")
        self.contacts = self.session.get("contacts", "interface")
        self.error = ""
        self.closed = False
        self.handling_pick = False
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

    def start(self):
        for name, (_, value) in self.settings.items():
            self.cmd.set(name, value)
        self.cmd.select(self.capture, "none", enable=1, quiet=1)

    def get_event_mask(self):
        return (Wizard.event_mask_select | Wizard.event_mask_pick |
                Wizard.event_mask_dirty | Wizard.event_mask_state)

    def get_panel(self):
        return [
            [1, f"Interactive focus ({RADIUS:g} A)", ""],
            [3, f"Camera: {_CAMERAS[self.camera]}", "camera"],
            [3, f"Contacts: {_CONTACT_LEVELS[self.contacts][0]}", "contacts"],
            [3, "Pocket options", "pocket"],
            [2, "Clear focus", "cmd.get_wizard().clear()"],
            [2, "Done", "cmd.set_wizard()"],
        ]

    def get_menu(self, tag):
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
        if self.layer:
            self.layer.show_contacts(self.cmd, _CONTACT_LEVELS[level][1])
        self.cmd.refresh_wizard()

    def set_pocket_option(self, key, value):
        pocket = pocket_for(self)
        pocket.set_option(key, value)
        pocket.update(self.layer)
        self.cmd.refresh_wizard()

    def do_select(self, name):
        if self.closed or self.handling_pick or name != self.capture:
            return
        self.handling_pick = True
        try:
            self._focus_pick(f"%{name}")
            self.cmd.select(self.capture, "none", enable=1, quiet=1)
        finally:
            self.handling_pick = False

    def do_pick(self, bond_flag):
        if self.closed or self.handling_pick or bond_flag:
            return
        self.handling_pick = True
        try:
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
        elif self.layer and self.layer.source not in self.cmd.get_names("objects", enabled_only=1):
            self.clear()

    def do_state(self, _state):
        if self.layer and not self.handling_pick:
            source = self.layer.source
            if source not in self.cmd.get_names("objects") or self.layer.state != self.cmd.get_object_state(source):
                self.clear()

    def _focus_pick(self, selection):
        try:
            picked = picked_source(self.cmd, selection, self.layer)
            if picked is None:
                return
            source, index = picked
            state, residue = focus_identity(self.cmd, source, index)
            previous = self.layer
            if previous and (source, state, residue) == (previous.source, previous.state, previous.residue):
                self.clear()
                return
            self.layer = draw_focus(self.cmd, self.prefix, source, index, state, residue)
            if previous:
                previous.delete(self.cmd)
            self.layer.show_contacts(self.cmd, _CONTACT_LEVELS[self.contacts][1])
            pocket_for(self).update(self.layer)
            self.layer.move_camera(self.cmd, self.camera)
            self.error = ""
        except Exception as error:
            self.error = f"Focus skipped: {error}"
            print_exc()
        self.cmd.refresh_wizard()

    def clear(self):
        layer, self.layer = self.layer, None
        self.error = ""
        pocket_for(self).update(None)
        if layer:
            layer.delete(self.cmd)
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
