"""Reversible leases for PyMOL 2.x keyboard mappings."""

from collections.abc import Mapping, MutableMapping
from typing import Dict, Tuple


class KeyBindingLease:
    """Install callbacks and later restore the exact previous mappings."""

    def __init__(self, cmd_api, callbacks: Mapping[str, object], owner: str = "plugin"):
        self.cmd = cmd_api
        self.callbacks = dict(callbacks)
        self.owner = str(owner)
        self.previous: Dict[str, Tuple[bool, object]] = {}
        self.installed: Dict[str, object] = {}
        self.active = False

    def acquire(self) -> None:
        if self.active:
            return
        mappings = getattr(self.cmd, "key_mappings", None)
        if not isinstance(mappings, MutableMapping):
            raise RuntimeError(
                "this PyMOL build does not expose cmd.key_mappings; refusing "
                "to overwrite shortcuts that cannot be restored"
            )
        self.previous = {
            key: (key in mappings, mappings.get(key)) for key in self.callbacks
        }
        try:
            for key, callback in self.callbacks.items():
                self.cmd.set_key(key, callback)
                self.installed[key] = mappings.get(key)
        except Exception:
            self.release(preserve_external=False)
            raise
        self.active = True

    def release(self, preserve_external: bool = True) -> None:
        mappings = getattr(self.cmd, "key_mappings", None)
        if not isinstance(mappings, MutableMapping):
            raise RuntimeError("cmd.key_mappings disappeared; shortcuts cannot be restored")
        for key, (existed, previous) in self.previous.items():
            installed = self.installed.get(key)
            if preserve_external and mappings.get(key) != installed:
                print(
                    f"{self.owner}: shortcut '{key}' changed outside this lease; "
                    "leaving the newer binding untouched"
                )
                continue
            if existed:
                mappings[key] = previous
            else:
                mappings.pop(key, None)
        self.previous.clear()
        self.installed.clear()
        self.active = False
