"""Scene bundles and the small, annotation-only manifest for group review."""

import json
import os
import shlex
import tempfile
import uuid

from pymolplugins.core.pymol_groups import direct_children, group_parent_snapshot, is_group


def delete_owned(api, names):
    """Deleting a PyMOL group is recursive: detach borrowed members first."""
    existing = set(api.get_names("objects"))
    names = set(names) & existing
    for name in names:
        if is_group(api, name):
            api.group(name, action="empty")
    for name in names:
        api.delete(name)


def group_parents(api):
    """Take an explicit metadata snapshot; use at import/validation, not switching."""
    return {name: parent for name, parent in group_parent_snapshot(api).items()
            if is_group(api, name)}


def candidates(api, parent, parents=None):
    parents = group_parents(api) if parents is None else parents
    if parent and parent not in parents:
        raise ValueError("Not a scene group: " + parent)
    names = direct_children(parent, parents)
    if not names:
        raise ValueError("No direct child groups in " + (parent or "the scene"))
    return names


def ancestors(name, parents):
    result = []
    while parents.get(name):
        name = parents[name]
        if name in result:
            raise ValueError("Cyclic scene group: " + name)
        result.append(name)
    return result


def write_manifest(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CandidateBundles:
    """Own only groups and command-created objects, never borrowed objects."""

    def __init__(self):
        self.groups = {}
        self.products = {}

    def snapshot(self):
        return dict(self.groups), {key: set(value) for key, value in self.products.items()}

    def check_command_references(self, candidate, commands):
        other_products = {name for owner, names in self.products.items()
                          if owner != candidate for name in names}
        if not other_products:
            return
        for number, command in commands:
            tokens = shlex.shlex(command, posix=True, punctuation_chars=",;()=[]")
            tokens.whitespace_split = True
            tokens.commenters = ""
            conflicts = other_products.intersection(tokens)
            if conflicts:
                raise ValueError(
                    f"Command line {number} references objects owned by another candidate: "
                    + ", ".join(sorted(conflicts))
                    + ". Use {index} or {object} in generated object names."
                )

    def restore(self, api, snapshot):
        groups, products = snapshot
        retained = set(groups.values()).union(*(products.values() or [set()]))
        current = set(self.groups.values()).union(*(self.products.values() or [set()]))
        delete_owned(api, current - retained)
        self.groups, self.products = groups, products

    def attach(self, api, candidate, created=()):
        if candidate not in self.groups:
            self.groups[candidate] = "sa_bundle_" + uuid.uuid4().hex[:12]
            api.group(self.groups[candidate], candidate)
            for name in self.products.get(candidate, ()):
                api.group(self.groups[candidate], name)
        members = set(created)
        self.products.setdefault(candidate, set()).update(members)
        # Existing user objects and fixed references never enter this set.
        for name in members:
            api.group(self.groups[candidate], name)

    def show(self, api, candidate, existing):
        for owner, group in self.groups.items():
            if group in existing:
                (api.enable if owner == candidate else api.disable)(group)
        for owner, products in self.products.items():
            if owner not in self.groups:
                for name in products & existing:
                    (api.enable if owner == candidate else api.disable)(name)

    def remove(self, api, candidates):
        owned = set()
        for candidate in candidates:
            owned.update(self.products.pop(candidate, ()))
            group = self.groups.pop(candidate, None)
            if group:
                owned.add(group)
        delete_owned(api, owned)

    def clear(self, api):
        self.remove(api, set(self.groups) | set(self.products))

    def rename_owner(self, old, new):
        if old in self.groups:
            self.groups[new] = self.groups.pop(old)
        if old in self.products:
            self.products[new] = self.products.pop(old)
        for owner, group in self.groups.items():
            if group == old:
                self.groups[owner] = new
        for members in self.products.values():
            if old in members:
                members.remove(old)
                members.add(new)
