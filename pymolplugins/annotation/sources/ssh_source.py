"""Resolve remote table rows and directories without rewriting source paths."""

from dataclasses import dataclass, replace
import hashlib
import re

from pymolplugins.remote.ssh import SshClient, SshCancelled, parse_ssh_path
from ..model import AnnotationDataset, AnnotationEntry
from ..path_resolver import PathResolution
from .directory import DEFAULT_STRUCTURE_PATTERN


@dataclass(frozen=True)
class SshPathResolution(PathResolution):
    observed: object = None


def resolve_ssh_dataset(dataset, *, require_exists=False, cancel_event=None):
    remote = parse_ssh_path(dataset.source_path)
    entries, locations = [], {}
    for entry in dataset.entries:
        if cancel_event is not None and cancel_event.is_set():
            raise SshCancelled("SSH path preparation cancelled.")
        try:
            if not entry.source_path.strip():
                raise ValueError("Structure path is empty.")
            location = remote.resolve(entry.source_path, entry.source_base)
            locations[entry.id] = location
            resolution = SshPathResolution(entry.source_path, location.uri, "unchecked", "ssh", "ssh-profile")
        except ValueError as exc:
            resolution = SshPathResolution(entry.source_path, status="invalid", provider="ssh", diagnostics=(str(exc),))
        entries.append(entry.with_resolution(resolution))
    if require_exists and locations:
        paths = list(dict.fromkeys(location.path for location in locations.values()))
        values = {}
        client = SshClient(remote)
        for start in range(0, len(paths), 1000):
            batch = paths[start:start + 1000]
            result = client.stat_many(batch, cancel_event=cancel_event)
            if len(result) != len(batch):
                raise RuntimeError("SSH returned an incomplete file-status response.")
            values.update(zip(batch, result))
        entries = [_checked_entry(entry, values[locations[entry.id].path]) if entry.id in locations else entry for entry in entries]
    return replace(dataset, entries=tuple(entries))


def _checked_entry(entry, value):
    status = value["status"]
    diagnostic = value.get("error", "")
    if status == "available" and not value["file"]:
        status, diagnostic = "invalid", "Remote structure path is not a regular file."
    resolution = replace(entry.path_resolution, status=status, observed=value,
                         diagnostics=(diagnostic,) if diagnostic else ())
    return entry.with_resolution(resolution)


def load_ssh_directory(path, pattern=DEFAULT_STRUCTURE_PATTERN, *, prefer_gz=True, cancel_event=None):
    remote = parse_ssh_path(path)
    pattern = re.compile(pattern or DEFAULT_STRUCTURE_PATTERN, re.IGNORECASE)
    listing = SshClient(remote).list_directory(cancel_event=cancel_event)
    selected = {}
    for value in listing["entries"]:
        if not pattern.match(value["name"]) or not value.get("file"):
            continue
        name = value["name"]
        stem = re.sub(r"\.(pdb|cif)(\.gz)?$", "", name, flags=re.IGNORECASE)
        old = selected.get(stem)
        if old is None or (name.lower().endswith(".gz") == prefer_gz and old["name"].lower().endswith(".gz") != prefer_gz):
            selected[stem] = value
    entries = []
    for ordinal, value in enumerate(sorted(selected.values(), key=lambda item: item["name"])):
        name, source = value["name"], value["path"]
        identity = "directory:" + hashlib.sha256(name.encode("utf-8")).hexdigest()
        metadata = {"structure_path": source, "name": name, "relative_path": name}
        resolution = SshPathResolution(source, remote.at(source).uri, value["status"], "ssh", "ssh-profile",
                                       diagnostics=(value["error"],) if value.get("error") else (), observed=value)
        entries.append(AnnotationEntry(identity, ordinal, name, source, "directory", remote.uri,
                                       metadata=metadata, raw_values=tuple(metadata.values())).with_resolution(resolution))
    return AnnotationDataset("directory", remote.uri, listing["path"], tuple(entries),
                             columns=("structure_path", "name", "relative_path"), path_column="structure_path")
