"""An ordered list of review objects and commands, without an execution graph."""

import re
import uuid

from .command_templates import STEP_FIELDS, format_commands
from .scene_groups import delete_owned
from pymolplugins.core.windows_paths import windows_long_path


DEFAULT_STEPS = [{"type": "object", "name": "candidate"}, {"type": "commands"}]


def effective_steps(steps, reference_column=""):
    if steps:
        return steps
    if reference_column:
        return [{"type": "object", "name": "reference", "path_column": reference_column,
                 "fixed": True}] + DEFAULT_STEPS
    return DEFAULT_STEPS


def validate_steps(raw, step_cmd="", columns=None):
    if not isinstance(raw, list):
        raise ValueError("steps must be an ordered list")
    if not raw:
        return []
    result, names = [], set()
    context = {key: "example" for key in STEP_FIELDS - {"object", "selection", "candidate"}}
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Step {index} must be an object")
        kind = item.get("type")
        if kind == "object":
            unknown = set(item) - {"type", "name", "path_column", "fixed", "on_load_cmd"}
            name = item.get("name", "")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)
                    or name in names or name in STEP_FIELDS - {"candidate"}):
                raise ValueError(f"Step {index} needs a unique object name (candidate or a custom name)")
            names.add(name)
            column = item.get("path_column", "")
            if not isinstance(column, str):
                raise ValueError(f"Step {index} path_column must be a string")
            if name != "candidate" and not column:
                raise ValueError(f"Step {index} needs a path_column")
            if name == "candidate" and (column or item.get("fixed", False)):
                raise ValueError("candidate uses the main structure column and cannot be fixed")
            if column and columns is not None and column not in columns:
                raise ValueError(f"Step {index} names unknown table column: {column}")
            if type(item.get("fixed", False)) is not bool:
                raise ValueError(f"Step {index} fixed must be true or false")
            commands = item.get("on_load_cmd", "")
            if not isinstance(commands, str):
                raise ValueError(f"Step {index} on_load_cmd must be a string")
            context[name] = "sa_" + name
            if name == "candidate":
                context["object"] = context["selection"] = context[name]
            format_commands(commands, context)
        elif kind == "commands":
            unknown = set(item) - {"type", "commands"}
            commands = item.get("commands", step_cmd)
            if not isinstance(commands, str):
                raise ValueError(f"Step {index} commands must be a string")
            format_commands(commands, context)
        else:
            raise ValueError(f"Step {index} type must be object or commands")
        if unknown:
            raise ValueError(f"Step {index} has unknown options: " + ", ".join(sorted(unknown)))
        result.append(dict(item))
    if "candidate" not in names:
        raise ValueError("steps must contain exactly one candidate object")
    return result


class ReviewObjects:
    """Own auxiliary scene objects; only changed paths change visibility."""

    def __init__(self):
        self.source = None
        self.fixed = {}
        self.active = {}
        self.owned = set()
        self.definitions = {}
        self.products = {}
        self.parked_products = {}

    def clear(self, api):
        delete_owned(api, self.owned)
        self.fixed.clear()
        self.active.clear()
        self.owned.clear()
        self.definitions.clear()
        self.products.clear()
        self.parked_products.clear()
        self.source = None

    def snapshot(self, api):
        return (self.source, dict(self.fixed), dict(self.active), set(self.owned),
                set(api.get_names("objects", enabled_only=1)) & self.owned, dict(self.definitions),
                {key: set(value) for key, value in self.products.items()})

    def restore(self, api, snapshot):
        source, fixed, active, owned, enabled, definitions, products = snapshot
        delete_owned(api, (self.owned - owned - set(self.parked_products))
                     | set(self.parked_products.values()))
        for parked, original in self.parked_products.items():
            api.set_name(parked, original)
        self.parked_products.clear()
        for name in owned:
            (api.enable if name in enabled else api.disable)(name)
        self.source, self.fixed, self.active, self.owned = source, fixed, active, owned
        self.definitions = definitions
        self.products = products

    def finish(self, api):
        retained = set(self.fixed.values()) | {item[1] for item in self.active.values()}
        self.products = {owner: names for owner, names in self.products.items() if owner in retained}
        retained.update(set().union(*self.products.values()) if self.products else set())
        delete_owned(api, self.owned - retained)
        self.owned = retained
        self.parked_products.clear()

    def begin(self, api, source, steps, assets):
        if source != self.source:
            self.park_products(api, list(self.products))
            for name in self.active.values():
                self.set_enabled(api, name[1], False)
            self.fixed.clear()
            self.active.clear()
            self.definitions.clear()
            self.source = source
        definitions = {item["name"]: dict(item) for item in steps if item["type"] == "object"}
        changed = {name for name in self.definitions if self.definitions[name] != definitions.get(name)}
        self.park_products(api, [name for key, name in self.fixed.items() if key[0] in changed])
        self.fixed = {key: name for key, name in self.fixed.items() if key[0] not in changed}
        self.definitions = definitions
        desired = {item["name"]: (item["name"], assets[item["name"]].path)
                   for item in steps if item["type"] == "object" and item["name"] != "candidate"
                   and item.get("fixed", False) and item["name"] in assets}
        for label, (key, name, fixed) in list(self.active.items()):
            if fixed and desired.get(label) == key and label not in changed:
                continue
            self.park_products(api, [name])
            if fixed:
                self.set_enabled(api, name, False)
            else:
                self.set_enabled(api, name, False)
            del self.active[label]

    def load(self, api, item, materialized):
        label = item["name"]
        key = (label, materialized.path)
        if label in self.active and self.active[label][0] == key:
            return self.active[label][1], False
        fixed = bool(item.get("fixed", False))
        name = self.fixed.get(key) if fixed else None
        loaded = not name or name not in api.get_names("objects")
        if loaded:
            basename = re.split(r"[/\\]", materialized.path)[-1]
            suffix = re.sub(r"[^A-Za-z0-9_]", "_", basename)[:40]
            name = "sa_" + label + "_" + suffix + "_" + uuid.uuid4().hex[:8]
            try:
                api.load(windows_long_path(materialized.path), name, quiet=1, zoom=0)
                if api.count_atoms(name) == 0:
                    raise ValueError(f"Object {label} contains no atoms")
            except Exception:
                api.delete(name)
                raise
            self.owned.add(name)
            if fixed:
                self.fixed[key] = name
        else:
            self.set_enabled(api, name, True)
        self.active[label] = (key, name, fixed)
        return name, loaded

    def set_enabled(self, api, name, enabled):
        action = api.enable if enabled else api.disable
        action(name)
        for product in self.products.get(name, ()):
            action(product)

    def park_products(self, api, owners):
        """Free command-defined names while preserving the previous transaction."""
        if not owners:
            return
        existing = set(api.get_names("objects"))
        for owner in owners:
            for name in list(self.products.get(owner, ())):
                if name not in existing or name in self.parked_products:
                    continue
                parked = "sa_step_tx_" + uuid.uuid4().hex[:12]
                api.set_name(name, parked)
                self.products[owner].remove(name)
                self.products[owner].add(parked)
                self.owned.remove(name)
                self.owned.add(parked)
                self.parked_products[parked] = name
