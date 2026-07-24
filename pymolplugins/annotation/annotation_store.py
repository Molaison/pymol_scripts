"""Durable row-level annotations for CSV-backed Structure Annotator datasets.

The store is intentionally independent from PyMOL and Qt.  Each mutation is a
single JSONL event keyed by ``(entry_id, label)`` and is flushed and fsynced
before the call returns.  A final line without a newline is treated as a torn
write: readers ignore it and the next writer truncates it before appending.

``compact()`` atomically replaces the event history with ``snapshot_set``
baseline events.  The current mapping is preserved, but the compacted history
is no longer undoable; callers that need a permanent audit trail should export
it before compacting.
"""

from __future__ import annotations

import datetime
import json
import ntpath
import os
import re
import tempfile
import threading
from typing import Dict, List, Mapping, Optional, Tuple

from pymolplugins.annotation.state_store import (
    csv_dataset_id,
    dataset_state_directory,
    mark_hidden_on_windows,
)
from pymolplugins.core.windows_paths import windows_long_path


ANNOTATION_SCHEMA = "pymolplugins.structure-annotator-annotations/1.0"
ANNOTATION_DIRECTORY = ".pymolplugins"
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_ENTRY_ID_LENGTH = 4096
MAX_LABEL_LENGTH = 1024
MAX_VALUE_LENGTH = 256 * 1024
MAX_HISTORY_RESULTS = 100000

_COMMON_FIELDS = {"schema", "seq", "timestamp_utc", "op"}
_EVENT_FIELDS = {
    "set": _COMMON_FIELDS | {"entry_id", "label", "value"},
    "remove": _COMMON_FIELDS | {"entry_id", "label"},
    "undo": _COMMON_FIELDS | {"entry_id", "label", "target_seq"},
    "snapshot_set": _COMMON_FIELDS | {"entry_id", "label", "value"},
}
_MISSING = object()
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: Dict[str, threading.RLock] = {}


class AnnotationStoreError(ValueError):
    """Raised when an annotation log is invalid, unsafe, or over a bound."""


class _DuplicateKeyError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _shared_lock(path: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _validate_identifier(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise AnnotationStoreError(f"{field} must be a string")
    if not value:
        raise AnnotationStoreError(f"{field} must not be empty")
    if len(value) > maximum:
        raise AnnotationStoreError(f"{field} exceeds {maximum} characters")
    if any(ord(character) < 32 for character in value):
        raise AnnotationStoreError(f"{field} contains a control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AnnotationStoreError(f"{field} is not valid UTF-8 text") from exc
    return value


def _validate_value(value: object) -> str:
    if not isinstance(value, str):
        raise AnnotationStoreError("value must be a string")
    if len(value) > MAX_VALUE_LENGTH:
        raise AnnotationStoreError(
            f"value exceeds {MAX_VALUE_LENGTH} characters"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AnnotationStoreError("value is not valid UTF-8 text") from exc
    return value


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AnnotationStoreError("timestamp_utc must be a bounded string")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnnotationStoreError("timestamp_utc is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise AnnotationStoreError("timestamp_utc must include a timezone")
    return value


def _validate_event(raw: object) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise AnnotationStoreError("event must be a JSON object")
    if raw.get("schema") != ANNOTATION_SCHEMA:
        raise AnnotationStoreError(
            "unsupported annotation schema: {!r}".format(raw.get("schema"))
        )
    operation = raw.get("op")
    if not isinstance(operation, str) or operation not in _EVENT_FIELDS:
        raise AnnotationStoreError(f"unsupported annotation operation: {operation!r}")
    expected = _EVENT_FIELDS[operation]
    present = set(raw)
    if present != expected:
        missing = sorted(expected - present)
        unknown = sorted(present - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise AnnotationStoreError("invalid event fields: " + "; ".join(details))

    sequence = raw.get("seq")
    if type(sequence) is not int or sequence < 1:
        raise AnnotationStoreError("event seq must be a positive integer")
    _validate_timestamp(raw.get("timestamp_utc"))
    _validate_identifier(raw.get("entry_id"), "entry_id", MAX_ENTRY_ID_LENGTH)
    _validate_identifier(raw.get("label"), "label", MAX_LABEL_LENGTH)
    if operation in {"set", "snapshot_set"}:
        _validate_value(raw.get("value"))
    if operation == "undo":
        target = raw.get("target_seq")
        if type(target) is not int or target < 1:
            raise AnnotationStoreError("undo target_seq must be a positive integer")
    return dict(raw)


def _object_without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _safe_dataset_id(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not dataset_id:
        raise AnnotationStoreError("dataset_id must be a non-empty string")
    if len(dataset_id) > 128:
        raise AnnotationStoreError("dataset_id exceeds 128 characters")
    if not re.fullmatch(r"\w[\w.-]*", dataset_id, flags=re.UNICODE):
        raise AnnotationStoreError(
            "dataset_id may contain only letters, digits, dot, underscore, and dash"
        )
    if dataset_id.endswith("."):
        raise AnnotationStoreError("dataset_id must not end with a dot")
    return dataset_id


def annotation_sidecar_path(csv_path: str, dataset_id: Optional[str] = None) -> str:
    """Return the deterministic hidden JSONL sidecar path for one CSV file.

    The default uses :func:`state_store.csv_dataset_id`, so state and
    annotation sidecars retain one identity across K:, UNC, and WSL aliases.
    """

    if not str(csv_path or "").strip():
        raise AnnotationStoreError("csv_path must not be empty")
    if dataset_id is None:
        dataset_id = csv_dataset_id(str(csv_path))
    clean_id = _safe_dataset_id(dataset_id)
    state_directory = dataset_state_directory(
        str(csv_path), source_mode="csv"
    )
    path_module = (
        ntpath
        if ntpath.splitdrive(state_directory)[0]
        or state_directory.startswith(("\\\\", "\\\\?\\"))
        else os.path
    )
    return path_module.join(
        state_directory,
        f"structure_annotator.{clean_id}.annotations.jsonl",
    )


class RowAnnotationStore:
    """Thread-safe append-only annotations keyed by CSV entry ID and label.

    Thread safety covers all :class:`RowAnnotationStore` instances for the same
    normalized path in this Python process.  This class intentionally does not
    claim cross-process locking.
    """

    def __init__(
        self,
        path: str,
        max_log_bytes: int = MAX_LOG_BYTES,
        max_event_bytes: int = MAX_EVENT_BYTES,
    ) -> None:
        if not str(path or "").strip():
            raise AnnotationStoreError("annotation path must not be empty")
        if type(max_log_bytes) is not int or max_log_bytes < 1:
            raise AnnotationStoreError("max_log_bytes must be a positive integer")
        if type(max_event_bytes) is not int or max_event_bytes < 1:
            raise AnnotationStoreError("max_event_bytes must be a positive integer")
        if max_log_bytes > MAX_LOG_BYTES:
            raise AnnotationStoreError(
                f"max_log_bytes must not exceed {MAX_LOG_BYTES}"
            )
        if max_event_bytes > MAX_EVENT_BYTES:
            raise AnnotationStoreError(
                f"max_event_bytes must not exceed {MAX_EVENT_BYTES}"
            )
        if max_event_bytes > max_log_bytes:
            raise AnnotationStoreError(
                "max_event_bytes must not exceed max_log_bytes"
            )
        self.path = os.path.abspath(os.path.expanduser(str(path)))
        self.max_log_bytes = max_log_bytes
        self.max_event_bytes = max_event_bytes
        self._lock = _shared_lock(self.path)
        self._events: List[Dict[str, object]] = []
        self._history: List[Dict[str, object]] = []
        self._state: Dict[Tuple[str, str], str] = {}
        self._reversible: Dict[int, Tuple[Tuple[str, str], object]] = {}
        self._reversible_order: List[int] = []
        self._undone = set()
        self._complete_size = 0
        self._actual_size = 0
        self._last_seq = 0
        with self._lock:
            self._load_unlocked()

    def _read_events_unlocked(self) -> Tuple[List[Dict[str, object]], int, int]:
        resolved = windows_long_path(self.path)
        if not os.path.isfile(resolved):
            return [], 0, 0
        try:
            actual_size = os.path.getsize(resolved)
        except OSError as exc:
            raise AnnotationStoreError(f"cannot inspect annotation log: {exc}") from exc
        if actual_size > self.max_log_bytes:
            raise AnnotationStoreError(
                f"annotation log exceeds {self.max_log_bytes} bytes: {self.path}"
            )
        try:
            with open(resolved, "rb") as handle:
                payload = handle.read(self.max_log_bytes + 1)
        except OSError as exc:
            raise AnnotationStoreError(f"cannot read annotation log: {exc}") from exc
        if len(payload) > self.max_log_bytes:
            raise AnnotationStoreError(
                f"annotation log exceeds {self.max_log_bytes} bytes: {self.path}"
            )

        # Only newline-terminated records are committed.  Preserve the byte
        # offset so a writer can remove a torn tail before its next append.
        complete_size = len(payload)
        if payload and not payload.endswith(b"\n"):
            complete_size = payload.rfind(b"\n") + 1
        complete = payload[:complete_size]
        lines = complete.split(b"\n")[:-1] if complete else []
        events: List[Dict[str, object]] = []
        previous_seq = 0
        for line_number, encoded in enumerate(lines, 1):
            if len(encoded) + 1 > self.max_event_bytes:
                raise AnnotationStoreError(
                    f"annotation event on line {line_number} exceeds "
                    f"{self.max_event_bytes} bytes"
                )
            if not encoded:
                raise AnnotationStoreError(
                    f"annotation log corruption on line {line_number}: empty event"
                )
            try:
                text = encoded.decode("utf-8")
                raw = json.loads(
                    text, object_pairs_hook=_object_without_duplicate_keys
                )
                event = _validate_event(raw)
            except (UnicodeError, json.JSONDecodeError, _DuplicateKeyError, AnnotationStoreError) as exc:
                raise AnnotationStoreError(
                    f"annotation log corruption on line {line_number}: {exc}"
                ) from exc
            sequence = int(event["seq"])
            if sequence <= previous_seq:
                raise AnnotationStoreError(
                    f"annotation log corruption on line {line_number}: "
                    "event seq is not strictly increasing"
                )
            previous_seq = sequence
            events.append(event)
        return events, complete_size, actual_size

    def _replay_unlocked(self, events: List[Dict[str, object]]) -> None:
        state: Dict[Tuple[str, str], str] = {}
        reversible: Dict[int, Tuple[Tuple[str, str], object]] = {}
        reversible_order: List[int] = []
        undone = set()
        history: List[Dict[str, object]] = []
        history_by_seq: Dict[int, Dict[str, object]] = {}
        baseline_keys = set()
        saw_mutation = False

        for line_number, event in enumerate(events, 1):
            sequence = int(event["seq"])
            operation = str(event["op"])
            key = (str(event["entry_id"]), str(event["label"]))
            before = state.get(key, _MISSING)
            enriched = dict(event)
            enriched["had_previous"] = before is not _MISSING
            enriched["previous"] = None if before is _MISSING else before
            enriched["baseline"] = operation == "snapshot_set"
            enriched["undone"] = False

            if operation in {"set", "snapshot_set"}:
                if operation == "snapshot_set":
                    if saw_mutation:
                        raise AnnotationStoreError(
                            f"annotation log corruption on line {line_number}: "
                            "snapshot baseline appears after mutable history"
                        )
                    if key in baseline_keys:
                        raise AnnotationStoreError(
                            f"annotation log corruption on line {line_number}: "
                            "duplicate key in snapshot baseline"
                        )
                    baseline_keys.add(key)
                else:
                    saw_mutation = True
                value = str(event["value"])
                state[key] = value
                enriched["current"] = value
                if operation == "set":
                    reversible[sequence] = (key, before)
                    reversible_order.append(sequence)
            elif operation == "remove":
                saw_mutation = True
                state.pop(key, None)
                enriched["current"] = None
                reversible[sequence] = (key, before)
                reversible_order.append(sequence)
            elif operation == "undo":
                saw_mutation = True
                target_seq = int(event["target_seq"])
                target = reversible.get(target_seq)
                if target is None:
                    raise AnnotationStoreError(
                        f"annotation log corruption on line {line_number}: "
                        f"undo target {target_seq} is not a reversible event"
                    )
                if target_seq in undone:
                    raise AnnotationStoreError(
                        f"annotation log corruption on line {line_number}: "
                        f"undo target {target_seq} was already undone"
                    )
                target_key, restored = target
                if key != target_key:
                    raise AnnotationStoreError(
                        f"annotation log corruption on line {line_number}: "
                        "undo key does not match its target"
                    )
                newest_for_key = next(
                    (
                        candidate
                        for candidate in reversed(reversible_order)
                        if candidate not in undone
                        and reversible[candidate][0] == key
                    ),
                    None,
                )
                if target_seq != newest_for_key:
                    raise AnnotationStoreError(
                        f"annotation log corruption on line {line_number}: "
                        "undo target is not the newest active event for its key"
                    )
                if restored is _MISSING:
                    state.pop(key, None)
                    enriched["current"] = None
                else:
                    state[key] = str(restored)
                    enriched["current"] = restored
                undone.add(target_seq)
                target_history = history_by_seq.get(target_seq)
                if target_history is not None:
                    target_history["undone"] = True
            history.append(enriched)
            history_by_seq[sequence] = enriched

        self._events = [dict(event) for event in events]
        self._history = history
        self._state = state
        self._reversible = reversible
        self._reversible_order = reversible_order
        self._undone = undone
        self._last_seq = int(events[-1]["seq"]) if events else 0

    def _load_unlocked(self) -> None:
        events, complete_size, actual_size = self._read_events_unlocked()
        self._replay_unlocked(events)
        self._complete_size = complete_size
        self._actual_size = actual_size

    def _ensure_parent_unlocked(self) -> str:
        directory = os.path.dirname(self.path)
        resolved_directory = windows_long_path(directory)
        try:
            os.makedirs(resolved_directory, exist_ok=True)
        except OSError as exc:
            raise AnnotationStoreError(
                f"cannot create annotation directory: {exc}"
            ) from exc
        if os.path.basename(directory).startswith("."):
            mark_hidden_on_windows(directory)
        return resolved_directory

    def _repair_torn_tail_unlocked(self) -> None:
        if self._actual_size == self._complete_size:
            return
        resolved = windows_long_path(self.path)
        try:
            with open(resolved, "r+b") as handle:
                handle.truncate(self._complete_size)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise AnnotationStoreError(
                f"cannot remove truncated annotation event: {exc}"
            ) from exc
        self._actual_size = self._complete_size

    def _serialize_event(self, event: Mapping[str, object]) -> bytes:
        clean = _validate_event(dict(event))
        encoded = (
            json.dumps(
                clean,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > self.max_event_bytes:
            raise AnnotationStoreError(
                f"annotation event exceeds {self.max_event_bytes} bytes"
            )
        return encoded

    def _append_event_unlocked(self, event: Mapping[str, object]) -> Dict[str, object]:
        encoded = self._serialize_event(event)
        if self._complete_size + len(encoded) > self.max_log_bytes:
            raise AnnotationStoreError(
                f"annotation log would exceed {self.max_log_bytes} bytes; "
                "compact or export it first"
            )
        self._ensure_parent_unlocked()
        self._repair_torn_tail_unlocked()
        resolved = windows_long_path(self.path)
        try:
            with open(resolved, "ab") as handle:
                written = handle.write(encoded)
                if written != len(encoded):
                    raise OSError("short write")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise AnnotationStoreError(
                f"cannot append annotation event: {exc}"
            ) from exc
        self._load_unlocked()
        for item in reversed(self._history):
            if item["seq"] == event["seq"]:
                result = dict(item)
                result["appended"] = True
                return result
        raise AnnotationStoreError("appended annotation event could not be reloaded")

    def _new_event(self, operation: str, **fields: object) -> Dict[str, object]:
        event: Dict[str, object] = {
            "schema": ANNOTATION_SCHEMA,
            "seq": self._last_seq + 1,
            "timestamp_utc": _utc_now(),
            "op": operation,
        }
        event.update(fields)
        return event

    def set(self, entry_id: str, label: str, value: str) -> Dict[str, object]:
        """Set one string annotation and return its persisted change record."""

        clean_entry = _validate_identifier(
            entry_id, "entry_id", MAX_ENTRY_ID_LENGTH
        )
        clean_label = _validate_identifier(label, "label", MAX_LABEL_LENGTH)
        clean_value = _validate_value(value)
        with self._lock:
            self._load_unlocked()
            event = self._new_event(
                "set", entry_id=clean_entry, label=clean_label, value=clean_value
            )
            return self._append_event_unlocked(event)

    def remove(self, entry_id: str, label: str) -> Dict[str, object]:
        """Remove one annotation and return previous/current information.

        Removing a missing key is still persisted as a user action and can be
        undone (as a no-op), which keeps the event history faithful.
        """

        clean_entry = _validate_identifier(
            entry_id, "entry_id", MAX_ENTRY_ID_LENGTH
        )
        clean_label = _validate_identifier(label, "label", MAX_LABEL_LENGTH)
        with self._lock:
            self._load_unlocked()
            event = self._new_event(
                "remove", entry_id=clean_entry, label=clean_label
            )
            return self._append_event_unlocked(event)

    def undo(
        self, entry_id: Optional[str] = None, label: Optional[str] = None
    ) -> Optional[Dict[str, object]]:
        """Undo the newest active mutation, optionally restricted by key.

        Undo itself is durable, but is not redoable.  ``None`` means there is
        no matching mutation since the last compaction.
        """

        clean_entry = None
        clean_label = None
        if entry_id is not None:
            clean_entry = _validate_identifier(
                entry_id, "entry_id", MAX_ENTRY_ID_LENGTH
            )
        if label is not None:
            clean_label = _validate_identifier(label, "label", MAX_LABEL_LENGTH)
        with self._lock:
            self._load_unlocked()
            target_seq = None
            target_key = None
            for candidate in reversed(self._reversible_order):
                if candidate in self._undone:
                    continue
                key, _restored = self._reversible[candidate]
                if clean_entry is not None and key[0] != clean_entry:
                    continue
                if clean_label is not None and key[1] != clean_label:
                    continue
                target_seq = candidate
                target_key = key
                break
            if target_seq is None or target_key is None:
                return None
            event = self._new_event(
                "undo",
                entry_id=target_key[0],
                label=target_key[1],
                target_seq=target_seq,
            )
            return self._append_event_unlocked(event)

    def reload(self) -> Dict[str, Dict[str, str]]:
        """Reload the log from disk and return a defensive current snapshot."""

        with self._lock:
            self._load_unlocked()
            return self._snapshot_unlocked()

    def get(
        self, entry_id: str, label: str, default: Optional[str] = None
    ) -> Optional[str]:
        """Return one current value without exposing mutable store state."""

        clean_entry = _validate_identifier(
            entry_id, "entry_id", MAX_ENTRY_ID_LENGTH
        )
        clean_label = _validate_identifier(label, "label", MAX_LABEL_LENGTH)
        with self._lock:
            self._load_unlocked()
            return self._state.get((clean_entry, clean_label), default)

    def annotations_for(self, entry_id: str) -> Dict[str, str]:
        """Return all current labels for one CSV entry."""

        clean_entry = _validate_identifier(
            entry_id, "entry_id", MAX_ENTRY_ID_LENGTH
        )
        with self._lock:
            self._load_unlocked()
            return {
                label: value
                for (candidate, label), value in sorted(self._state.items())
                if candidate == clean_entry
            }

    def _snapshot_unlocked(self) -> Dict[str, Dict[str, str]]:
        result: Dict[str, Dict[str, str]] = {}
        for (entry_id, label), value in sorted(self._state.items()):
            result.setdefault(entry_id, {})[label] = value
        return result

    def snapshot(self) -> Dict[str, Dict[str, str]]:
        """Return ``{entry_id: {label: value}}`` for UI and export use."""

        with self._lock:
            self._load_unlocked()
            return self._snapshot_unlocked()

    def export_mapping(self) -> Dict[str, Dict[str, str]]:
        """Return the current row-level mapping for a CSV export adapter."""

        return self.snapshot()

    def history(
        self,
        entry_id: Optional[str] = None,
        label: Optional[str] = None,
        limit: Optional[int] = None,
        include_baseline: bool = False,
    ) -> List[Dict[str, object]]:
        """Return chronological, defensive event records.

        ``limit`` selects the newest N matching records while retaining
        chronological order.  Compaction baselines are hidden by default.
        """

        clean_entry = None
        clean_label = None
        if entry_id is not None:
            clean_entry = _validate_identifier(
                entry_id, "entry_id", MAX_ENTRY_ID_LENGTH
            )
        if label is not None:
            clean_label = _validate_identifier(label, "label", MAX_LABEL_LENGTH)
        if limit is not None:
            if type(limit) is not int or not 0 <= limit <= MAX_HISTORY_RESULTS:
                raise AnnotationStoreError(
                    f"limit must be an integer in 0..{MAX_HISTORY_RESULTS}"
                )
        if type(include_baseline) is not bool:
            raise AnnotationStoreError("include_baseline must be a boolean")
        with self._lock:
            self._load_unlocked()
            matches = []
            for event in self._history:
                if not include_baseline and event["op"] == "snapshot_set":
                    continue
                if clean_entry is not None and event["entry_id"] != clean_entry:
                    continue
                if clean_label is not None and event["label"] != clean_label:
                    continue
                matches.append(dict(event))
            if limit is not None:
                matches = matches[-limit:] if limit else []
            return matches

    @staticmethod
    def _fsync_directory(directory: str) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def compact(self) -> Dict[str, object]:
        """Atomically replace history with non-undoable current baselines."""

        with self._lock:
            self._load_unlocked()
            previous_events = len(self._events)
            timestamp = _utc_now()
            lines: List[bytes] = []
            for sequence, ((entry_id, label), value) in enumerate(
                sorted(self._state.items()), 1
            ):
                lines.append(
                    self._serialize_event(
                        {
                            "schema": ANNOTATION_SCHEMA,
                            "seq": sequence,
                            "timestamp_utc": timestamp,
                            "op": "snapshot_set",
                            "entry_id": entry_id,
                            "label": label,
                            "value": value,
                        }
                    )
                )
            encoded_size = sum(len(line) for line in lines)
            if encoded_size > self.max_log_bytes:
                raise AnnotationStoreError(
                    f"compacted annotation log exceeds {self.max_log_bytes} bytes"
                )

            resolved_directory = self._ensure_parent_unlocked()
            resolved_path = windows_long_path(self.path)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".structure_annotator_annotations.",
                suffix=".tmp",
                dir=resolved_directory,
            )
            replaced = False
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    for line in lines:
                        written = handle.write(line)
                        if written != len(line):
                            raise OSError("short write")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, resolved_path)
                replaced = True
                self._fsync_directory(resolved_directory)
            except OSError as exc:
                if not replaced:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                raise AnnotationStoreError(
                    f"cannot compact annotation log: {exc}"
                ) from exc

            self._load_unlocked()
            return {
                "path": self.path,
                "entry_count": len({key[0] for key in self._state}),
                "annotation_count": len(self._state),
                "previous_events": previous_events,
                "events": len(self._events),
                "bytes": self._complete_size,
                "undo_history_cleared": True,
            }


# Shorter alias for callers that do not need to expose the row-level detail in
# their own naming.  Keep RowAnnotationStore as the canonical documented name.
AnnotationStore = RowAnnotationStore


__all__ = [
    "ANNOTATION_DIRECTORY",
    "ANNOTATION_SCHEMA",
    "MAX_ENTRY_ID_LENGTH",
    "MAX_EVENT_BYTES",
    "MAX_HISTORY_RESULTS",
    "MAX_LABEL_LENGTH",
    "MAX_LOG_BYTES",
    "MAX_VALUE_LENGTH",
    "AnnotationStore",
    "AnnotationStoreError",
    "RowAnnotationStore",
    "annotation_sidecar_path",
]
