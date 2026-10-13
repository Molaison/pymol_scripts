"""An ordered list of review objects and commands, without an execution graph."""

import re
import uuid

from .command_templates import STEP_FIELDS, format_commands
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

    def clear(self, api):
        for name in self.owned:
            api.delete(name)
        self.fixed.clear()
        self.active.clear()
        self.owned.clear()
        self.definitions.clear()
        self.source = None

    def snapshot(self, api):
        return (self.source, dict(self.fixed), dict(self.active), set(self.owned),
                set(api.get_names("objects", enabled_only=1)) & self.owned, dict(self.definitions))

    def restore(self, api, snapshot):
        source, fixed, active, owned, enabled, definitions = snapshot
        for name in self.owned - owned:
            api.delete(name)
        for name in owned:
            (api.enable if name in enabled else api.disable)(name)
        self.source, self.fixed, self.active, self.owned = source, fixed, active, owned
        self.definitions = definitions

    def finish(self, api):
        retained = set(self.fixed.values()) | {item[1] for item in self.active.values()}
        for name in self.owned - retained:
            api.delete(name)
        self.owned = retained

    def begin(self, api, source, steps, assets):
        if source != self.source:
            for name in self.active.values():
                api.disable(name[1])
            self.fixed.clear()
            self.active.clear()
            self.definitions.clear()
            self.source = source
        definitions = {item["name"]: dict(item) for item in steps if item["type"] == "object"}
        changed = {name for name in self.definitions if self.definitions[name] != definitions.get(name)}
        self.fixed = {key: name for key, name in self.fixed.items() if key[0] not in changed}
        self.definitions = definitions
        desired = {item["name"]: (item["name"], assets[item["name"]].path)
                   for item in steps if item["type"] == "object" and item["name"] != "candidate"
                   and item.get("fixed", False) and item["name"] in assets}
        for label, (key, name, fixed) in list(self.active.items()):
            if fixed and desired.get(label) == key and label not in changed:
                continue
            if fixed:
                api.disable(name)
            else:
                api.disable(name)
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
            api.enable(name)
        self.active[label] = (key, name, fixed)
        return name, loaded
