"""Versioned, atomic per-dataset state storage for Structure Annotator."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from typing import Dict, Optional

from pymolplugins.core.windows_paths import windows_long_path


STATE_SCHEMA = "pymolplugins.structure-annotator-state/1.0"
STATE_DIRECTORY = ".pymolplugins"
STATE_FILENAME = "structure_annotator.json"
MAX_STATE_BYTES = 1024 * 1024


class StateStoreError(ValueError):
    """Raised when a persisted state document is invalid or unsupported."""


def dataset_state_directory(dataset_dir: str) -> str:
    """Return the hidden state directory for *dataset_dir*."""

    root = os.path.abspath(os.path.expanduser(str(dataset_dir)))
    return os.path.join(root, STATE_DIRECTORY)


def dataset_state_path(dataset_dir: str) -> str:
    """Return the Structure Annotator state file for *dataset_dir*."""

    return os.path.join(dataset_state_directory(dataset_dir), STATE_FILENAME)


def mark_hidden_on_windows(path: str) -> None:
    """Best-effort Windows hidden attribute in addition to the dot prefix."""

    if os.name != "nt":
        return
    try:
        get_attributes = ctypes.windll.kernel32.GetFileAttributesW
        set_attributes = ctypes.windll.kernel32.SetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int
        attributes = int(get_attributes(windows_long_path(path)))
        if attributes != 0xFFFFFFFF:
            set_attributes(windows_long_path(path), attributes | 0x2)
    except Exception:
        pass


def read_dataset_state(dataset_dir: str) -> Optional[Dict[str, object]]:
    """Read and minimally validate a dataset state document.

    ``None`` means no state file exists. Invalid JSON, an unsupported schema,
    or an oversized document raises :class:`StateStoreError` so the caller can
    warn without confusing the condition with a first-time dataset.
    """

    path = dataset_state_path(dataset_dir)
    resolved = windows_long_path(path)
    if not os.path.isfile(resolved):
        return None
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        raise StateStoreError(f"cannot inspect state file: {exc}") from exc
    if size > MAX_STATE_BYTES:
        raise StateStoreError(
            f"state file exceeds {MAX_STATE_BYTES} bytes: {path}"
        )
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateStoreError(f"cannot read state file: {exc}") from exc
    if not isinstance(payload, dict):
        raise StateStoreError("state document must be a JSON object")
    if payload.get("schema") != STATE_SCHEMA:
        raise StateStoreError(
            "unsupported state schema: {!r}".format(payload.get("schema"))
        )
    if not isinstance(payload.get("config"), dict):
        raise StateStoreError("state config must be a JSON object")
    if not isinstance(payload.get("position"), dict):
        raise StateStoreError("state position must be a JSON object")
    return payload


def write_dataset_state(dataset_dir: str, payload: Dict[str, object]) -> str:
    """Atomically write one versioned dataset state document."""

    root = os.path.abspath(os.path.expanduser(str(dataset_dir)))
    if not os.path.isdir(windows_long_path(root)):
        raise StateStoreError(f"dataset directory does not exist: {root}")

    state_dir = dataset_state_directory(root)
    resolved_dir = windows_long_path(state_dir)
    os.makedirs(resolved_dir, exist_ok=True)
    mark_hidden_on_windows(state_dir)

    document = dict(payload)
    document["schema"] = STATE_SCHEMA
    serialized = json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    encoded_size = len(serialized.encode("utf-8"))
    if encoded_size > MAX_STATE_BYTES:
        raise StateStoreError(
            f"state document exceeds {MAX_STATE_BYTES} bytes: {encoded_size}"
        )
    destination = dataset_state_path(root)
    resolved_destination = windows_long_path(destination)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".structure_annotator.", suffix=".tmp", dir=resolved_dir
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved_destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return destination


__all__ = [
    "MAX_STATE_BYTES",
    "STATE_DIRECTORY",
    "STATE_FILENAME",
    "STATE_SCHEMA",
    "StateStoreError",
    "dataset_state_directory",
    "dataset_state_path",
    "mark_hidden_on_windows",
    "read_dataset_state",
    "write_dataset_state",
]
