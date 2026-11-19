"""PyMOL wizard mode for clicking through local contact environments."""

from traceback import print_exc
from uuid import uuid4

from pymol import cmd
from pymol.wizard import Wizard

from .focus_view import RADIUS, draw_focus, focus_identity, picked_source


class InteractiveFocus(Wizard):
    def __init__(self, api):
        super().__init__(_self=api)
        self.layer = None
        self.previous_view = None
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
            [2, "Clear focus", "cmd.get_wizard().clear()"],
            [2, "Done", "cmd.set_wizard()"],
        ]

    def get_prompt(self):
        if self.error:
            return [self.error, "Click another atom to refocus."]
        if self.layer is None:
            return ["Click a residue or ligand to focus."]
        hbonds, salts, stacks = self.layer.counts
        return [
            self.layer.label,
            f"Teal polar {hbonds}  |  Magenta salt bridges {salts}  |  Violet pi stacks {stacks}",
        ]

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
            self.clear(restore_view=False)

    def do_state(self, _state):
        if self.layer and not self.handling_pick:
            source = self.layer.source
            if source not in self.cmd.get_names("objects") or self.layer.state != self.cmd.get_object_state(source):
                self.clear(restore_view=False)

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
            before = self.cmd.get_view()
            self.layer = draw_focus(self.cmd, self.prefix, source, index, state, residue)
            if previous:
                previous.delete(self.cmd)
            else:
                self.previous_view = before
            self.error = ""
        except Exception as error:
            self.error = f"Focus skipped: {error}"
            print_exc()
        self.cmd.refresh_wizard()

    def clear(self, restore_view=True):
        layer, self.layer = self.layer, None
        view, self.previous_view = self.previous_view, None
        self.error = ""
        if layer:
            layer.delete(self.cmd)
        if view and restore_view:
            self.cmd.set_view(view, animate=0)
        if not self.closed:
            self.cmd.refresh_wizard()

    def cleanup(self):
        self.closed = True
        self.clear()
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
    return "started"
