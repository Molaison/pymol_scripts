"""Portable, read-only review defaults and separately saved user overrides."""

from dataclasses import dataclass
import base64
import json
import ntpath
import os
import posixpath
import tempfile

from pymolplugins.core.windows_paths import windows_long_path
from .command_templates import validate_commands
from .filtering import validate_filter_presets
from pymolplugins.remote.ssh import SshClient, is_ssh_path, parse_ssh_path, ssh_source_root


REVIEW_KEYS = ("path_column", "display_columns", "label_column", "reference_column", "step_cmd", "bucket_cmds", "filter_presets")


@dataclass(frozen=True)
class ReviewFile:
    config: dict
    signatures: tuple


def review_paths(table_path):
    """Return adjacent generated and user-owned paths in the table's syntax."""
    if is_ssh_path(table_path):
        return tuple(os.path.join(ssh_source_root(table_path), name) for name in ("review.json", "review.override.json"))
    module = ntpath if ntpath.splitdrive(str(table_path))[0] else os.path
    parent = module.dirname(str(table_path))
    return tuple(module.join(parent, name) for name in ("review.json", "review.override.json"))


def validate_review_config(raw):
    """Validate exact column names and the shared PyMOL command template."""
    if not isinstance(raw, dict):
        raise ValueError("Review configuration must be a JSON object")
    unknown = set(raw) - set(REVIEW_KEYS)
    if unknown:
        raise ValueError("Unknown review options: " + ", ".join(sorted(unknown)))
    result = dict(raw)
    for name in ("path_column", "label_column", "reference_column", "step_cmd"):
        if name in raw and not isinstance(raw[name], str):
            raise ValueError(name + " must be a string")
    if "path_column" in raw and not raw["path_column"].strip():
        raise ValueError("path_column must name a table column")
    if "display_columns" in raw:
        columns = raw["display_columns"]
        if not isinstance(columns, list) or any(not isinstance(name, str) or not name for name in columns):
            raise ValueError("display_columns must be an ordered list of column names")
        if len(set(columns)) != len(columns):
            raise ValueError("display_columns must not contain duplicates")
        result["display_columns"] = list(columns)
    buckets = raw.get("bucket_cmds", {})
    if not isinstance(buckets, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                            for k, v in buckets.items()):
        raise ValueError("bucket_cmds must map label values to command strings")
    validate_commands(raw.get("step_cmd", ""), buckets)
    if "filter_presets" in raw:
        result["filter_presets"] = validate_filter_presets(raw["filter_presets"])
    return result


def validate_review_columns(config, columns):
    """Reject stale named columns before any structures or commands are loaded."""
    selected = list(config.get("display_columns", []))
    selected += [config[key] for key in ("path_column", "label_column", "reference_column") if config.get(key)]
    missing = [name for name in selected if name not in columns]
    if missing:
        raise ValueError("Review configuration names unknown table columns: " + ", ".join(dict.fromkeys(missing)))
    validate_filter_presets(config.get("filter_presets", []), columns)


def _signature(path):
    try:
        value = os.stat(windows_long_path(path))
    except FileNotFoundError:
        return None
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _remote_review_paths(table_path):
    remote = parse_ssh_path(table_path)
    parent = posixpath.dirname(remote.path)
    return remote, [posixpath.join(parent, name) for name in ("review.json", "review.override.json")]


def _remote_signature(value):
    return {key: item for key, item in value.items() if key != "content"}


def read_review_file(table_path, *, cancel_event=None):
    """Merge generated defaults then user overrides; absence is optional."""
    config, signatures = {}, []
    paths = review_paths(table_path)
    if is_ssh_path(table_path):
        remote, remote_paths = _remote_review_paths(table_path)
        values = SshClient(remote).read_small_files(remote_paths, cancel_event=cancel_event)
        for path, value in zip(remote_paths, values):
            signatures.append(_remote_signature(value))
            if value["status"] != "missing":
                try:
                    raw = json.loads(base64.b64decode(value["content"]).decode("utf-8-sig"))
                    config.update(validate_review_config(raw))
                except (UnicodeError, ValueError) as exc:
                    raise ValueError(f"Cannot load remote {path}: {exc}") from exc
        paths = paths[1:]
    for path in paths:
        before = _signature(path)
        signatures.append(before)
        if before is None:
            continue
        try:
            with open(windows_long_path(path), encoding="utf-8-sig") as handle:
                raw = json.load(handle)
            config.update(validate_review_config(raw))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"Cannot load {path}: {exc}") from exc
        if _signature(path) != before:
            raise RuntimeError(f"Review configuration changed while reading: {path}")
    return ReviewFile(config, tuple(signatures))


def check_review_unchanged(table_path, review, *, cancel_event=None):
    paths, signatures = review_paths(table_path), []
    if is_ssh_path(table_path):
        remote, remote_paths = _remote_review_paths(table_path)
        signatures.extend(SshClient(remote).stat_many(remote_paths, cancel_event=cancel_event))
        paths = paths[1:]
    signatures.extend(_signature(path) for path in paths)
    if tuple(signatures) != review.signatures:
        raise RuntimeError("Review configuration changed during import; open the table again")


def write_review_override(table_path, config):
    """Atomically save user configuration without ever writing review.json."""
    payload = validate_review_config(config)
    destination = windows_long_path(review_paths(table_path)[1])
    if is_ssh_path(table_path):
        os.makedirs(os.path.dirname(destination), exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # Cursor/settings saves often carry the same override. Replacing it again
    # invalidates an in-flight import's source signature without any edit.
    try:
        with open(destination, encoding="utf-8") as handle:
            if handle.read() == serialized:
                return destination
    except FileNotFoundError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=".review.override.", suffix=".tmp", dir=os.path.dirname(destination))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return destination
