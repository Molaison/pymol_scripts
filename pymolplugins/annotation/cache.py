"""Persistent cache primitives with process-local concurrency protection.

The cache deliberately contains no PyMOL calls.  It is therefore safe to use
from the annotator's background I/O executor; callers remain responsible for
loading the returned path on PyMOL's main thread.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Mapping, Optional, Set, Union


CACHE_SCHEMA = "pymolplugins.structure-cache/1.0"
CACHE_STATUS_SCHEMA = "pymolplugins.structure-cache-status/1.0"
CACHE_DIRECTORY_ENVIRONMENT_VARIABLE = "PYMOLPLUGINS_STRUCTURE_CACHE_DIR"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_MAX_ITEMS = 5000
MANIFEST_FILENAME = "manifest.json"
OBJECTS_DIRECTORY = "objects"

_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SUFFIX_PATTERN = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


class StructureCacheError(RuntimeError):
    """Base exception for persistent structure-cache failures."""


class InvalidSourceError(StructureCacheError):
    """Raised when a source cannot be identified as a regular file."""


class CacheWriteError(StructureCacheError):
    """Raised when a concurrent cache writer failed."""


def default_structure_cache_dir(
    *,
    environment: Optional[Mapping[str, str]] = None,
    platform_name: Optional[str] = None,
) -> str:
    """Return the persistent Host-local cache directory.

    Windows uses ``LOCALAPPDATA`` so a Windows PyMOL never has to warm-cache
    through ``\\\\wsl$``.  Other platforms honor ``XDG_CACHE_HOME`` and fall
    back to ``~/.cache``.  The environment override is useful for portable
    deployments and tests.
    """

    env = os.environ if environment is None else environment
    platform_value = sys.platform if platform_name is None else platform_name
    windows = platform_value == "win32"
    path_module = ntpath if windows else posixpath

    override = str(env.get(CACHE_DIRECTORY_ENVIRONMENT_VARIABLE, "")).strip()
    if override:
        if override.startswith("~"):
            home = str(
                env.get("USERPROFILE" if windows else "HOME", "")
            ).strip()
            if home:
                override = home + override[1:]
            else:
                override = os.path.expanduser(override)
        return path_module.normpath(override)

    if windows:
        base = str(env.get("LOCALAPPDATA", "")).strip()
        if not base:
            profile = str(env.get("USERPROFILE", "")).strip()
            base = (
                ntpath.join(profile, "AppData", "Local")
                if profile
                else os.path.expanduser("~")
            )
        return ntpath.normpath(
            ntpath.join(base, "PymolPlugins", "structure-cache", "v1")
        )

    base = str(env.get("XDG_CACHE_HOME", "")).strip()
    if not base:
        home = str(env.get("HOME", "")).strip() or os.path.expanduser("~")
        base = posixpath.join(home, ".cache")
    return posixpath.normpath(
        posixpath.join(base, "pymolplugins", "structure-cache", "v1")
    )


def _canonical_source(value: str, *, resolve_local: bool) -> str:
    text = os.fspath(value)
    if not text or "\x00" in text:
        raise InvalidSourceError("source path must be a non-empty path")
    if _URI_PATTERN.match(text):
        return text
    if ntpath.splitdrive(text)[0] or text.startswith("\\\\"):
        return ntpath.normcase(ntpath.normpath(text))
    if resolve_local:
        return os.path.normcase(
            os.path.realpath(os.path.abspath(os.path.expanduser(text)))
        )
    # An explicit canonical source may be a remote POSIX path or the literal
    # remote-home alias ``~/``.  Do not expand it against the Host account.
    return posixpath.normpath(text)


def _mtime_ns(stat_result: os.stat_result) -> int:
    return int(
        getattr(
            stat_result,
            "st_mtime_ns",
            int(float(stat_result.st_mtime) * 1_000_000_000),
        )
    )


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Return a streaming SHA-256 digest without loading the file into RAM."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceIdentity:
    """Stable cache identity for one observed source-file version."""

    provider: str
    canonical_source: str
    size: int
    mtime_ns: int
    sha256: Optional[str] = None
    version_token: Optional[str] = None

    def __post_init__(self) -> None:
        provider = str(self.provider).strip().lower()
        if not provider:
            raise InvalidSourceError("source provider must not be empty")
        if type(self.size) is not int or self.size < 0:
            raise InvalidSourceError("source size must be a non-negative integer")
        if type(self.mtime_ns) is not int:
            raise InvalidSourceError("source mtime_ns must be an integer")
        canonical = _canonical_source(
            str(self.canonical_source), resolve_local=False
        )
        digest = self.sha256
        if digest is not None:
            digest = str(digest).lower()
            if not _KEY_PATTERN.fullmatch(digest):
                raise InvalidSourceError("source sha256 must contain 64 hex digits")
        version = self.version_token
        if version is not None:
            version = str(version)
            if (
                not version
                or len(version) > 512
                or any(character in version for character in "\x00\r\n")
            ):
                raise InvalidSourceError(
                    "source version_token must be a bounded single-line string"
                )
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "canonical_source", canonical)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "version_token", version)

    @classmethod
    def from_path(
        cls,
        path: str,
        *,
        provider: str = "file",
        canonical_source: Optional[str] = None,
        include_sha256: bool = False,
        version_token: Optional[str] = None,
    ) -> "SourceIdentity":
        """Inspect *path* and construct its cache identity.

        ``canonical_source`` lets a mapped Host path retain the identity of the
        remote path from the CSV.  When omitted, the physical local path is
        resolved and canonicalized.
        """

        physical = os.path.abspath(os.path.expanduser(os.fspath(path)))
        try:
            stat_result = os.stat(physical)
        except OSError as exc:
            raise InvalidSourceError(f"cannot inspect source file {path!r}: {exc}") from exc
        if not os.path.isfile(physical):
            raise InvalidSourceError(f"source is not a regular file: {path}")
        provider_value = str(provider).strip().lower()
        if not provider_value:
            raise InvalidSourceError("source provider must not be empty")
        canonical_value = _canonical_source(
            physical if canonical_source is None else os.fspath(canonical_source),
            resolve_local=canonical_source is None,
        )
        digest = sha256_file(physical) if include_sha256 else None
        if include_sha256:
            try:
                after_hash = os.stat(physical)
            except OSError as exc:
                raise InvalidSourceError(
                    f"source disappeared while hashing {path!r}: {exc}"
                ) from exc
            if (
                int(after_hash.st_size) != int(stat_result.st_size)
                or _mtime_ns(after_hash) != _mtime_ns(stat_result)
            ):
                raise InvalidSourceError(
                    f"source changed while hashing: {path}"
                )
        return cls(
            provider=provider_value,
            canonical_source=canonical_value,
            size=int(stat_result.st_size),
            mtime_ns=_mtime_ns(stat_result),
            sha256=digest,
            version_token=version_token,
        )

    @property
    def cache_key(self) -> str:
        """Return the required provider/source/size/mtime content key."""

        payload = {
            "canonical_source": self.canonical_source,
            "mtime_ns": int(self.mtime_ns),
            "provider": self.provider,
            "size": int(self.size),
        }
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.version_token is not None:
            payload["version_token"] = self.version_token
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "provider": self.provider,
            "canonical_source": self.canonical_source,
            "size": int(self.size),
            "mtime_ns": int(self.mtime_ns),
        }
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.version_token is not None:
            payload["version_token"] = self.version_token
        return payload


@dataclass(frozen=True)
class CacheResult:
    """Result returned by a persistent-cache lookup or creation."""

    key: str
    path: str
    size: int
    cache_hit: bool


@dataclass
class _CacheEntry:
    key: str
    relative_path: str
    size: int
    created_ns: int
    accessed_ns: int
    source_identity: Optional[Dict[str, object]] = None

    def as_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "relative_path": self.relative_path,
            "size": int(self.size),
            "created_ns": int(self.created_ns),
            "accessed_ns": int(self.accessed_ns),
        }
        if self.source_identity is not None:
            payload["source_identity"] = self.source_identity
        return payload


@dataclass
class _InflightWrite:
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[CacheResult] = None
    error: Optional[BaseException] = None


CacheReference = Union[str, SourceIdentity, CacheResult]


class PersistentStructureCache:
    """Persistent atomic structure cache with process-local write deduplication."""

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
        max_items: Optional[int] = DEFAULT_MAX_ITEMS,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if max_bytes is not None and int(max_bytes) < 1:
            raise ValueError("max_bytes must be positive or None")
        if max_items is not None and int(max_items) < 1:
            raise ValueError("max_items must be positive or None")
        self.root = os.path.abspath(
            os.path.expanduser(root or default_structure_cache_dir())
        )
        self.objects_directory = os.path.join(self.root, OBJECTS_DIRECTORY)
        self.manifest_path = os.path.join(self.root, MANIFEST_FILENAME)
        self.max_bytes = None if max_bytes is None else int(max_bytes)
        self.max_items = None if max_items is None else int(max_items)
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._entries: Dict[str, _CacheEntry] = {}
        self._pins: Dict[str, Set[str]] = {}
        self._inflight: Dict[str, _InflightWrite] = {}
        self._manifest_rebuilt = False

        os.makedirs(self.objects_directory, exist_ok=True)
        with self._lock:
            self._cleanup_partial_files_locked()
            self._load_or_rebuild_index_locked()
            self._enforce_limits_locked()
            self._write_manifest_locked()

    @staticmethod
    def key_for(identity: SourceIdentity) -> str:
        return identity.cache_key

    @staticmethod
    def _safe_suffix(suffix: str) -> str:
        value = str(suffix or "").strip().lower()
        if not value.startswith("."):
            value = "." + value if value else ".bin"
        return value if _SUFFIX_PATTERN.match(value) else ".bin"

    @staticmethod
    def _coerce_key(reference: CacheReference) -> str:
        if isinstance(reference, SourceIdentity):
            key = reference.cache_key
        elif isinstance(reference, CacheResult):
            key = reference.key
        elif isinstance(getattr(reference, "cache_key", None), str):
            # Accept MaterializedStructure without importing materializer.py
            # (which intentionally depends on this module).
            key = getattr(reference, "cache_key")
        else:
            key = str(reference)
        if not _KEY_PATTERN.match(key):
            raise ValueError(f"invalid structure cache key: {key!r}")
        return key

    def _entry_path(self, entry: _CacheEntry) -> str:
        return os.path.join(self.root, entry.relative_path)

    def _destination(self, key: str, suffix: str) -> str:
        directory = os.path.join(self.objects_directory, key[:2])
        return os.path.join(directory, key + self._safe_suffix(suffix))

    def _cleanup_partial_files_locked(self) -> None:
        for directory, _, filenames in os.walk(self.root):
            for filename in filenames:
                if not filename.endswith(".part"):
                    continue
                try:
                    os.unlink(os.path.join(directory, filename))
                except OSError:
                    pass

    def _manifest_entry_locked(
        self, key: str, raw: object
    ) -> Optional[_CacheEntry]:
        if not _KEY_PATTERN.match(key) or not isinstance(raw, dict):
            return None
        relative = raw.get("relative_path")
        if not isinstance(relative, str) or not relative:
            return None
        candidate = os.path.abspath(os.path.join(self.root, relative))
        try:
            if os.path.commonpath([self.objects_directory, candidate]) != os.path.abspath(
                self.objects_directory
            ):
                return None
        except ValueError:
            return None
        filename_match = re.match(
            r"^([0-9a-f]{64})(?:\..+)?$", os.path.basename(candidate)
        )
        if filename_match is None or filename_match.group(1) != key:
            return None
        try:
            actual_size = os.path.getsize(candidate)
        except OSError:
            return None
        expected_size = raw.get("size")
        if type(expected_size) is not int or expected_size != actual_size:
            try:
                os.unlink(candidate)
            except OSError:
                pass
            return None
        created_ns = raw.get("created_ns", 0)
        accessed_ns = raw.get("accessed_ns", created_ns)
        if type(created_ns) is not int or type(accessed_ns) is not int:
            return None
        source_identity = raw.get("source_identity")
        if source_identity is not None and not isinstance(source_identity, dict):
            source_identity = None
        return _CacheEntry(
            key=key,
            relative_path=os.path.relpath(candidate, self.root),
            size=actual_size,
            created_ns=created_ns,
            accessed_ns=accessed_ns,
            source_identity=dict(source_identity) if source_identity else None,
        )

    def _load_or_rebuild_index_locked(self) -> None:
        loaded_manifest = False
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, dict) or document.get("schema") != CACHE_SCHEMA:
                raise ValueError("unsupported cache manifest")
            raw_entries = document.get("entries")
            if not isinstance(raw_entries, dict):
                raise ValueError("cache manifest entries must be an object")
            for key, raw in raw_entries.items():
                entry = self._manifest_entry_locked(str(key), raw)
                if entry is not None:
                    self._entries[entry.key] = entry
            loaded_manifest = True
        except FileNotFoundError:
            self._manifest_rebuilt = True
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            self._manifest_rebuilt = True

        # A filename contains the complete cache key, so missing/corrupt
        # manifests and interrupted manifest updates are recoverable.
        for directory, _, filenames in os.walk(self.objects_directory):
            for filename in sorted(filenames):
                match = re.match(r"^([0-9a-f]{64})(?:\..+)?$", filename)
                if match is None:
                    continue
                key = match.group(1)
                path = os.path.join(directory, filename)
                if key in self._entries:
                    if os.path.abspath(path) != os.path.abspath(
                        self._entry_path(self._entries[key])
                    ):
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                    continue
                try:
                    stat_result = os.stat(path)
                except OSError:
                    continue
                timestamp = int(
                    getattr(stat_result, "st_mtime_ns", _mtime_ns(stat_result))
                )
                self._entries[key] = _CacheEntry(
                    key=key,
                    relative_path=os.path.relpath(path, self.root),
                    size=int(stat_result.st_size),
                    created_ns=timestamp,
                    accessed_ns=timestamp,
                    source_identity=None,
                )
                if loaded_manifest:
                    self._manifest_rebuilt = True

    def _write_manifest_locked(self) -> None:
        document = {
            "schema": CACHE_SCHEMA,
            "entries": {
                key: self._entries[key].as_dict() for key in sorted(self._entries)
            },
        }
        serialized = json.dumps(
            document, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        temporary = os.path.join(
            self.root, f".{MANIFEST_FILENAME}.{uuid.uuid4().hex}.part"
        )
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def _pinned_keys_locked(self) -> Set[str]:
        pinned: Set[str] = set()
        for keys in self._pins.values():
            pinned.update(keys)
        return pinned

    def _remove_entry_locked(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return False
        path = self._entry_path(entry)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        self._entries.pop(key, None)
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass
        return True

    def _over_limits_locked(self) -> bool:
        return self._limits_exceeded_locked(
            len(self._entries), sum(entry.size for entry in self._entries.values())
        )

    def _limits_exceeded_locked(self, item_count: int, total_bytes: int) -> bool:
        return bool(
            (self.max_items is not None and item_count > self.max_items)
            or (self.max_bytes is not None and total_bytes > self.max_bytes)
        )

    def _enforce_limits_locked(self, protected: Iterable[str] = ()) -> int:
        protected_keys = set(protected)
        unavailable = protected_keys.union(self._pinned_keys_locked())
        total_bytes = sum(entry.size for entry in self._entries.values())
        item_count = len(self._entries)
        removed = 0
        candidates = sorted(
            (
                entry
                for key, entry in self._entries.items()
                if key not in unavailable
            ),
            key=lambda item: (item.accessed_ns, item.created_ns, item.key),
        )
        for oldest in candidates:
            if not self._limits_exceeded_locked(item_count, total_bytes):
                break
            if self._remove_entry_locked(oldest.key):
                removed += 1
                item_count -= 1
                total_bytes -= oldest.size
        return removed

    def _lookup_locked(
        self, identity: SourceIdentity, *, touch: bool
    ) -> Optional[CacheResult]:
        key = identity.cache_key
        entry = self._entries.get(key)
        if entry is None:
            return None
        path = self._entry_path(entry)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        if size != entry.size:
            self._remove_entry_locked(key)
            self._write_manifest_locked()
            return None
        if touch:
            entry.accessed_ns = int(self._clock_ns())
            self._write_manifest_locked()
        return CacheResult(key=key, path=path, size=entry.size, cache_hit=True)

    def lookup(
        self,
        identity: SourceIdentity,
        *,
        touch: bool = True,
        pin_owner: Optional[str] = None,
    ) -> Optional[CacheResult]:
        """Return a valid cache entry, or ``None`` for a cold/stale miss."""

        with self._lock:
            result = self._lookup_locked(identity, touch=touch)
            if result is not None and pin_owner:
                self._pins.setdefault(str(pin_owner), set()).add(result.key)
            return result

    def get_or_create(
        self,
        identity: SourceIdentity,
        *,
        suffix: str,
        writer: Callable[[str], None],
        pin_owner: Optional[str] = None,
    ) -> CacheResult:
        """Atomically create a cache entry, deduplicating concurrent writers.

        ``writer`` receives a unique path ending in ``.part``.  It must write
        the complete output and return; this method then publishes it with
        :func:`os.replace`.
        """

        key = identity.cache_key
        with self._lock:
            existing = self._lookup_locked(identity, touch=True)
            if existing is not None:
                if pin_owner:
                    self._pins.setdefault(str(pin_owner), set()).add(key)
                return existing
            if pin_owner:
                # Pin before waiting so a concurrently completed item cannot be
                # evicted between publication and this caller waking up.
                self._pins.setdefault(str(pin_owner), set()).add(key)
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = _InflightWrite()
                self._inflight[key] = inflight
                leader = True
            else:
                leader = False

        if not leader:
            inflight.event.wait()
            if inflight.error is not None:
                raise CacheWriteError(
                    f"concurrent cache creation failed for {key}"
                ) from inflight.error
            if inflight.result is None:
                raise CacheWriteError(f"cache creation produced no result for {key}")
            return CacheResult(
                key=inflight.result.key,
                path=inflight.result.path,
                size=inflight.result.size,
                cache_hit=True,
            )

        destination = self._destination(key, suffix)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        temporary = destination + f".{uuid.uuid4().hex}.part"
        try:
            writer(temporary)
            if not os.path.isfile(temporary):
                raise CacheWriteError("cache writer did not create a regular file")
            size = os.path.getsize(temporary)
            os.replace(temporary, destination)
            now = int(self._clock_ns())
            with self._lock:
                self._entries[key] = _CacheEntry(
                    key=key,
                    relative_path=os.path.relpath(destination, self.root),
                    size=size,
                    created_ns=now,
                    accessed_ns=now,
                    source_identity=identity.as_dict(),
                )
                self._enforce_limits_locked(protected={key})
                self._write_manifest_locked()
                result = CacheResult(
                    key=key,
                    path=destination,
                    size=size,
                    cache_hit=False,
                )
                inflight.result = result
                self._inflight.pop(key, None)
                inflight.event.set()
            return result
        except BaseException as exc:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            with self._lock:
                inflight.error = exc
                self._inflight.pop(key, None)
                inflight.event.set()
            raise

    def pin(self, reference: CacheReference, *, owner: str = "manual") -> str:
        """Protect a key from LRU eviction for the lifetime of this instance."""

        key = self._coerce_key(reference)
        owner_value = str(owner).strip() or "manual"
        with self._lock:
            self._pins.setdefault(owner_value, set()).add(key)
        return key

    def unpin(
        self,
        reference: CacheReference,
        *,
        owner: Optional[str] = None,
    ) -> None:
        """Release one owner's pin, or all owners' pins when owner is omitted."""

        key = self._coerce_key(reference)
        with self._lock:
            owners = list(self._pins) if owner is None else [str(owner)]
            for owner_value in owners:
                keys = self._pins.get(owner_value)
                if keys is None:
                    continue
                keys.discard(key)
                if not keys:
                    self._pins.pop(owner_value, None)
            changed = self._enforce_limits_locked()
            if changed:
                self._write_manifest_locked()

    def set_navigation_pins(
        self,
        *,
        current: Optional[CacheReference] = None,
        preload: Iterable[CacheReference] = (),
    ) -> None:
        """Replace the special ``current`` and ``preload`` pin sets."""

        current_keys = set() if current is None else {self._coerce_key(current)}
        preload_keys = {self._coerce_key(item) for item in preload}
        with self._lock:
            if current_keys:
                self._pins["current"] = current_keys
            else:
                self._pins.pop("current", None)
            if preload_keys:
                self._pins["preload"] = preload_keys
            else:
                self._pins.pop("preload", None)
            changed = self._enforce_limits_locked()
            if changed:
                self._write_manifest_locked()

    def prune(self) -> Dict[str, int]:
        """Apply LRU limits now and return the number of evicted entries."""

        with self._lock:
            before_bytes = sum(entry.size for entry in self._entries.values())
            removed = self._enforce_limits_locked()
            after_bytes = sum(entry.size for entry in self._entries.values())
            if removed:
                self._write_manifest_locked()
            return {"removed_items": removed, "removed_bytes": before_bytes - after_bytes}

    def clear(self, *, force: bool = False) -> Dict[str, int]:
        """Clear unpinned entries, or every entry when ``force=True``."""

        with self._lock:
            pinned = set() if force else self._pinned_keys_locked()
            removed_items = 0
            removed_bytes = 0
            for key, entry in list(self._entries.items()):
                if key in pinned:
                    continue
                if self._remove_entry_locked(key):
                    removed_items += 1
                    removed_bytes += entry.size
            if force:
                self._pins.clear()
            self._cleanup_partial_files_locked()
            self._write_manifest_locked()
            return {
                "removed_items": removed_items,
                "removed_bytes": removed_bytes,
                "retained_items": len(self._entries),
            }

    def status(self, *, include_entries: bool = False) -> Dict[str, object]:
        """Return a JSON-serializable cache status snapshot."""

        with self._lock:
            pinned = self._pinned_keys_locked()
            total_bytes = sum(entry.size for entry in self._entries.values())
            result: Dict[str, object] = {
                "schema": CACHE_STATUS_SCHEMA,
                "root": self.root,
                "manifest": self.manifest_path,
                "entry_count": len(self._entries),
                "total_bytes": total_bytes,
                "max_items": self.max_items,
                "max_bytes": self.max_bytes,
                "pinned_count": len(pinned.intersection(self._entries)),
                "pinned_keys": sorted(pinned),
                "pins_by_owner": {
                    owner: sorted(keys) for owner, keys in sorted(self._pins.items())
                },
                "inflight_count": len(self._inflight),
                "over_limit": self._over_limits_locked(),
                "manifest_rebuilt": self._manifest_rebuilt,
            }
            if include_entries:
                result["entries"] = [
                    {
                        "key": entry.key,
                        "path": self._entry_path(entry),
                        "size": entry.size,
                        "created_ns": entry.created_ns,
                        "accessed_ns": entry.accessed_ns,
                        "pinned": entry.key in pinned,
                    }
                    for entry in sorted(
                        self._entries.values(),
                        key=lambda item: (item.accessed_ns, item.key),
                        reverse=True,
                    )
                ]
            return result


__all__ = [
    "CACHE_DIRECTORY_ENVIRONMENT_VARIABLE",
    "CACHE_SCHEMA",
    "CACHE_STATUS_SCHEMA",
    "CacheResult",
    "CacheWriteError",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_ITEMS",
    "InvalidSourceError",
    "PersistentStructureCache",
    "SourceIdentity",
    "StructureCacheError",
    "default_structure_cache_dir",
    "sha256_file",
]
