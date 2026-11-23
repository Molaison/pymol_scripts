"""Portable, read-only review defaults and separately saved user overrides."""

from dataclasses import dataclass
import base64
from hashlib import sha256
import json
import ntpath
import os
import posixpath
import tempfile

from pymolplugins.core.windows_paths import windows_long_path
from .command_templates import validate_commands
from .filtering import validate_filter_presets
from .review_steps import effective_steps, validate_steps
from .reference_track import validate_reference_track
from pymolplugins.remote.ssh import SshClient, is_ssh_path, parse_ssh_path, ssh_source_root


REVIEW_KEYS = ("path_column", "id_column", "display_columns", "label_column", "reference_column", "step_cmd", "bucket_cmds", "filter_presets", "steps", "filter_text", "sort_column", "sort_descending", "reference_track")
_BUNDLE_TABLE_FIELDS = ("path_column", "display_columns", "label_column", "id_column")
_LEGACY_BUNDLE_STEPS = {
    (
        "/from pathlib import Path",
        "/from runpy import run_path",
        '/bundle_step_path = Path(r"{file}").parent.parent / "pymol" / "acceptance_collection.py"',
        '/run_path(str(bundle_step_path))["acc_bundle_step"]("{object}", r"{file}")',
    ),
    (
        "/from pathlib import Path",
        "/from runpy import run_path",
        '/bundle_source = Path(r"{file}")',
        '/bundle_script = bundle_source.parent.parent / "pymol" / "acceptance_collection.py"',
        '/bundle_renderer = run_path(str(bundle_script))["acc_bundle_step"]',
        '/bundle_renderer("{object}", bundle_source)',
    ),
}

# Historical generated ligand-pocket bridge with membrane interface labels.
# Only these shipped scripts have the same semantics as the native view.
_INTERFACE_BRIDGE_LAST = '/bundle_renderer("{object}", str(bundle_source), "bind_interface.py")'
_INTERFACE_BRIDGE_FILES = {
    "acceptance_bundle.py": "19c375130dda2bb2d0056e480ae4be5e99ef8f9b09afc91e2d232258dbc31ff1",
    "acceptance_recipes.py": "c5a5d854f330d17915e62d03e397c7a30dd4cd8ec4c43534c1627fb2b922b85e",
    "acceptance_pocket_void.py": "17456c3d4b4a4f3e5d7ce430ff1af833225bc07d02da1a304f86aa135129c7c8",
    "acceptance_cavity_runtime.py": "93e0b89286b4880f8680ddbcec2ebe3f089a70e9c450ed608157a540a2a3a808",
    "acceptance_cavity_geometry.py": "db20b8c3100df2137cb2a695cdbaeed0fb3c637e12febd9ef7644771c836c29d",
    "acceptance_collection.py": "8fd95c52855b302fec242288775c16928b9ad000df29b5d33de963b5cc19490b",
    "bind_interface.py": "954f4bf2e947fe522491df3b0a6946a937d9fdad9944b0cd6da393e9eb6194b1",
}


@dataclass(frozen=True)
class ReviewFile:
    config: dict
    signatures: tuple
    layers: tuple = ()


def bundle_table_name(raw):
    if not isinstance(raw, dict) or raw.get("mode") != "bundle":
        return ""
    name = raw.get("scores_file", "sum_scores.csv")
    if not isinstance(name, str) or not name.strip():
        return ""
    name = ntpath.normpath(name)
    return name if ntpath.basename(name) == name else ""


def review_table_layer(path, raw, table_path):
    """Read the table options of a PyMOL skill bundle alongside its declared table."""
    if not isinstance(raw, dict) or ntpath.basename(path) != "review.json":
        return raw
    if raw.get("mode") == "bundle":
        if not bundle_matches_table(raw, table_path):
            return {}
        raw = {name: raw[name] for name in (*_BUNDLE_TABLE_FIELDS, "step_cmd", "reference_track") if name in raw}
        raw.setdefault("step_cmd", "")
    return {**raw, "step_cmd": ""} if is_legacy_bundle_step(raw.get("step_cmd")) else raw


def is_legacy_bundle_step(step_cmd):
    if not isinstance(step_cmd, str):
        return False
    lines = tuple(line.strip() for line in step_cmd.splitlines() if line.strip())
    return lines in _LEGACY_BUNDLE_STEPS


def _uses_bundle_sidecar(step_cmd):
    """Recognize a migrated sidecar when the shared command is disabled."""
    if not isinstance(step_cmd, str):
        return False
    if is_legacy_bundle_step(step_cmd):
        return True
    text_call = '/bundle_renderer("{object}", str(bundle_source))'
    path_call = '/bundle_renderer("{object}", bundle_source)'
    return is_legacy_bundle_step(step_cmd.replace(text_call, path_call))


def bundle_matches_table(raw, table_path):
    source = parse_ssh_path(table_path).path if is_ssh_path(table_path) else table_path
    compare = ntpath.normcase if os.name == "nt" and not is_ssh_path(table_path) else str
    return compare(ntpath.basename(source)) == compare(bundle_table_name(raw))


def _interface_bundle_config(table_path, step_cmd, dataset, selected):
    lines = tuple(line.strip() for line in step_cmd.splitlines() if line.strip())
    if (not lines or lines[-1] != _INTERFACE_BRIDGE_LAST or is_ssh_path(table_path)
            or dataset is None or selected is None):
        return None
    stock = '\n'.join((*lines[:-1], '/bundle_renderer("{object}", bundle_source)'))
    if not is_legacy_bundle_step(stock.replace('.absolute()', '')):
        return None
    root = os.path.dirname(os.path.abspath(table_path))
    for entry in dataset.entries:
        parts = entry.source_path.split("/")
        if len(parts) != 2 or parts[0] != "structures" or ntpath.basename(parts[1]) != parts[1]:
            return None
    expected = os.path.normcase(os.path.normpath(os.path.join(root, selected.source_path)))
    if expected != os.path.normcase(os.path.normpath(selected.resolved_path)):
        return None
    try:
        for name, fingerprint in _INTERFACE_BRIDGE_FILES.items():
            with open(windows_long_path(os.path.join(root, "pymol", name)), "rb") as handle:
                if sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest() != fingerprint:
                    return None
        with open(windows_long_path(os.path.join(root, "bundle_view.json")), encoding="utf-8-sig") as handle:
            config = json.load(handle)
    except (OSError, UnicodeError, ValueError):
        return None
    if (bundle_matches_table(config, table_path) and config.get("view") == "ligand_pocket"
            and config.get("path_column", "structure_path") == dataset.path_column and config.get("membrane")):
        return {**config, "legacy_interface_step": step_cmd.strip()}
    return None


def bundle_view_config(review, table_path, *, step_cmd="", dataset=None, selected=None):
    """Choose the built-in view after merging the effective shared command."""
    if step_cmd.strip() and not is_legacy_bundle_step(step_cmd):
        return _interface_bundle_config(table_path, step_cmd, dataset, selected)
    for path, raw in review.layers:
        if ntpath.basename(path) != "review.json":
            continue
        if bundle_table_name(raw) and bundle_matches_table(raw, table_path):
            return raw
        if isinstance(raw, dict) and not bundle_table_name(raw) and _uses_bundle_sidecar(raw.get("step_cmd")):
            return {"view_sidecar": "bundle_view.json", "path_column": raw.get("path_column", "structure_path")}
    return None


def review_paths(table_path):
    """Return adjacent generated and user-owned paths in the table's syntax."""
    if is_ssh_path(table_path):
        return tuple(os.path.join(ssh_source_root(table_path), name) for name in ("review.json", "review.override.json"))
    module = ntpath if ntpath.splitdrive(str(table_path))[0] else os.path
    parent = module.dirname(str(table_path))
    return tuple(module.join(parent, name) for name in ("review.json", "review.override.json"))


def validate_review_config(raw, *, validate_order=True):
    """Validate exact column names and the shared PyMOL command template."""
    if not isinstance(raw, dict):
        raise ValueError("Review configuration must be a JSON object")
    unknown = set(raw) - set(REVIEW_KEYS)
    if unknown:
        raise ValueError("Unknown review options: " + ", ".join(sorted(unknown)))
    result = dict(raw)
    for name in ("path_column", "id_column", "label_column", "reference_column", "step_cmd", "filter_text", "sort_column"):
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
    if "sort_descending" in raw and type(raw["sort_descending"]) is not bool:
        raise ValueError("sort_descending must be true or false")
    if validate_order:
        steps = effective_steps(raw.get("steps", []), raw.get("reference_column", ""))
        validate_steps(steps, raw.get("step_cmd", ""))
        names = [item["name"] for item in steps if item["type"] == "object"]
        validate_commands(raw.get("step_cmd", ""), buckets, names)
    if "filter_presets" in raw:
        result["filter_presets"] = validate_filter_presets(raw["filter_presets"])
    if "reference_track" in raw:
        result["reference_track"] = validate_reference_track(raw["reference_track"])
    return result


def validate_review_columns(config, columns):
    """Reject stale named columns before any structures or commands are loaded."""
    selected = list(config.get("display_columns", []))
    selected += [config[key] for key in ("path_column", "id_column", "label_column", "reference_column") if config.get(key)]
    missing = [name for name in selected if name not in columns]
    if missing:
        raise ValueError("Review configuration names unknown table columns: " + ", ".join(dict.fromkeys(missing)))
    validate_filter_presets(config.get("filter_presets", []), columns)
    validate_steps(effective_steps(config.get("steps", []), config.get("reference_column", "")),
                   config.get("step_cmd", ""), columns)
    if config.get("sort_column") and config["sort_column"] not in columns:
        raise ValueError("Review configuration names unknown sort column: " + config["sort_column"])


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


def read_review_file(table_path, *, cancel_event=None, validate=True):
    """Merge generated defaults then user overrides; absence is optional."""
    config, signatures, layers = {}, [], []
    paths = review_paths(table_path)
    if is_ssh_path(table_path):
        remote, remote_paths = _remote_review_paths(table_path)
        from pymolplugins.remote.import_cache import recent_review_files
        values = recent_review_files(remote, remote_paths, cancel_event=cancel_event)
        for path, value in zip(remote_paths, values):
            signatures.append(_remote_signature(value))
            if value["status"] != "missing":
                try:
                    raw = json.loads(base64.b64decode(value["content"]).decode("utf-8-sig"))
                    layers.append((path, raw))
                    raw = review_table_layer(path, raw, table_path)
                    if validate:
                        raw = validate_review_config(raw, validate_order=False)
                    if isinstance(raw, dict):
                        config.update(raw)
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
            layers.append((path, raw))
            raw = review_table_layer(path, raw, table_path)
            if validate:
                raw = validate_review_config(raw, validate_order=False)
            if isinstance(raw, dict):
                config.update(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"Cannot load {path}: {exc}") from exc
        if _signature(path) != before:
            raise RuntimeError(f"Review configuration changed while reading: {path}")
    return ReviewFile(validate_review_config(config) if validate else config,
                      tuple(signatures), tuple(layers))


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
