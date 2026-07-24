"""Allowlisted, bounded file staging for a WSL worker.

The worker accepts only a configured root alias plus a POSIX relative path.
Absolute server paths are never part of the wire contract.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import PurePosixPath
import re
import stat
import time
from typing import BinaryIO, Dict, Iterator, Mapping, Optional


STAGE_RESPONSE_SCHEMA = "pymolplugins.stage-file/1.0"
STAGE_STAT_SCHEMA = "pymolplugins.stage-stat/1.0"
STAGE_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class StagingError(RuntimeError):
    """A safe-to-return staging failure."""

    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = int(http_status)


@dataclass(frozen=True)
class StageFileIdentity:
    """Filesystem identity captured from the already-open file descriptor."""

    device: int
    inode: int
    size: int
    mtime_ns: int
    mode: int

    @property
    def token(self) -> str:
        raw = "%d:%d:%d:%d:%d" % (
            self.device,
            self.inode,
            self.size,
            self.mtime_ns,
            self.mode,
        )
        return "v1-" + hashlib.sha256(raw.encode("ascii")).hexdigest()


@dataclass
class OpenStageFile:
    """Validated open file plus the metadata sent before its bytes."""

    alias: str
    handle: BinaryIO
    identity: StageFileIdentity
    opened_at: float


def parse_stage_root(value: str) -> tuple[str, str]:
    """Parse one ``ALIAS=PATH`` CLI value without expanding the alias."""
    raw = str(value)
    if "=" not in raw:
        raise ValueError("--stage-root must use ALIAS=PATH")
    alias, path = raw.split("=", 1)
    alias = alias.strip()
    path = path.strip()
    if not STAGE_ALIAS_RE.fullmatch(alias):
        raise ValueError(
            "stage root alias must start with a letter and contain only "
            "letters, digits, dot, underscore, or hyphen"
        )
    if not path:
        raise ValueError("stage root path is empty")
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isdir(resolved):
        raise ValueError("stage root is not an existing directory: %s" % alias)
    return alias, resolved


def parse_stage_roots(values) -> Dict[str, str]:
    """Parse repeated CLI roots and reject duplicate aliases."""
    roots: Dict[str, str] = {}
    for value in values or ():
        alias, path = parse_stage_root(value)
        if alias in roots:
            raise ValueError("duplicate stage root alias: %s" % alias)
        roots[alias] = path
    return roots


def validate_relative_path(value: object) -> str:
    """Return a canonical relative POSIX path or raise a safe request error."""
    if not isinstance(value, str) or not value:
        raise StagingError(
            "relative_path must be a non-empty string",
            code="invalid_stage_path",
            http_status=400,
        )
    if "\x00" in value or "\\" in value:
        raise StagingError(
            "relative_path must use relative POSIX path syntax",
            code="invalid_stage_path",
            http_status=400,
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StagingError(
            "relative_path must not be absolute or contain dot segments",
            code="invalid_stage_path",
            http_status=400,
        )
    canonical = path.as_posix()
    if canonical != value:
        raise StagingError(
            "relative_path must be canonical POSIX syntax",
            code="invalid_stage_path",
            http_status=400,
        )
    return canonical


def _is_within(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath((root, candidate)) == root
    except ValueError:
        return False


class FileStagingBackend:
    """Resolve and stream regular files below explicitly configured roots."""

    def __init__(
        self,
        roots: Optional[Mapping[str, str]] = None,
        *,
        max_file_bytes: int = 256 * 1024 * 1024,
        max_stream_seconds: float = 60.0,
        chunk_bytes: int = 256 * 1024,
        clock=time.monotonic,
    ):
        self.roots = {
            str(alias): os.path.realpath(os.path.abspath(str(path)))
            for alias, path in dict(roots or {}).items()
        }
        self.max_file_bytes = int(max_file_bytes)
        self.max_stream_seconds = float(max_stream_seconds)
        self.chunk_bytes = int(chunk_bytes)
        self._clock = clock
        if self.max_file_bytes < 1 or self.chunk_bytes < 1:
            raise ValueError("staging byte limits must be positive")
        if self.max_stream_seconds <= 0:
            raise ValueError("max_stream_seconds must be positive")
        for alias, path in self.roots.items():
            if not STAGE_ALIAS_RE.fullmatch(alias):
                raise ValueError("invalid stage root alias: %s" % alias)
            if not os.path.isdir(path):
                raise ValueError("stage root is not an existing directory: %s" % alias)

    @property
    def enabled(self) -> bool:
        return bool(self.roots)

    def capabilities(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "root_aliases": sorted(self.roots),
            "max_file_bytes": self.max_file_bytes,
            "max_stream_seconds": self.max_stream_seconds,
        }

    def _root(self, alias: object) -> str:
        if not isinstance(alias, str) or not STAGE_ALIAS_RE.fullmatch(alias):
            raise StagingError(
                "root_alias is invalid",
                code="invalid_root_alias",
                http_status=400,
            )
        try:
            return self.roots[alias]
        except KeyError as exc:
            raise StagingError(
                "unknown stage root alias",
                code="unknown_stage_root",
                http_status=404,
            ) from exc

    def _resolved_candidate(self, root: str, relative_path: str) -> str:
        candidate = os.path.realpath(os.path.join(root, *relative_path.split("/")))
        if not _is_within(root, candidate):
            raise StagingError(
                "relative_path resolves outside the configured root",
                code="stage_path_forbidden",
                http_status=403,
            )
        return candidate

    def _open_descriptor(self, candidate: str) -> int:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(candidate, flags)
        except FileNotFoundError as exc:
            raise StagingError(
                "staged file does not exist",
                code="stage_file_not_found",
                http_status=404,
            ) from exc
        except OSError as exc:
            raise StagingError(
                "staged file could not be opened",
                code="stage_file_unreadable",
                http_status=403,
            ) from exc

    def _verify_descriptor(self, root: str, descriptor: int) -> StageFileIdentity:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StagingError(
                "staging supports regular files only",
                code="stage_not_regular_file",
                http_status=400,
            )
        fd_path = "/proc/self/fd/%d" % descriptor
        if os.path.exists(fd_path):
            opened_path = os.path.realpath(fd_path)
            if not _is_within(root, opened_path):
                raise StagingError(
                    "opened file resolves outside the configured root",
                    code="stage_path_forbidden",
                    http_status=403,
                )
        if metadata.st_size > self.max_file_bytes:
            raise StagingError(
                "staged file exceeds the configured size limit",
                code="stage_file_too_large",
                http_status=413,
            )
        return StageFileIdentity(
            device=int(metadata.st_dev),
            inode=int(metadata.st_ino),
            size=int(metadata.st_size),
            mtime_ns=int(metadata.st_mtime_ns),
            mode=int(metadata.st_mode),
        )

    @contextmanager
    def open_file(self, alias: object, relative_path: object) -> Iterator[OpenStageFile]:
        """Open one allowlisted file and retain the descriptor while streaming."""
        started = self._clock()
        root = self._root(alias)
        relative = validate_relative_path(relative_path)
        candidate = self._resolved_candidate(root, relative)
        descriptor = self._open_descriptor(candidate)
        try:
            identity = self._verify_descriptor(root, descriptor)
            if self._clock() - started > self.max_stream_seconds:
                raise StagingError(
                    "staged file open exceeded the time limit",
                    code="stage_timeout",
                    http_status=504,
                )
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                yield OpenStageFile(str(alias), handle, identity, started)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def iter_bytes(self, opened: OpenStageFile) -> Iterator[bytes]:
        """Yield bounded chunks and enforce one wall-clock stream deadline."""
        sent = 0
        while sent < opened.identity.size:
            if self._clock() - opened.opened_at > self.max_stream_seconds:
                raise StagingError(
                    "staged file transfer exceeded the time limit",
                    code="stage_timeout",
                    http_status=504,
                )
            chunk = opened.handle.read(
                min(self.chunk_bytes, opened.identity.size - sent)
            )
            if self._clock() - opened.opened_at > self.max_stream_seconds:
                raise StagingError(
                    "staged file transfer exceeded the time limit",
                    code="stage_timeout",
                    http_status=504,
                )
            if not chunk:
                raise StagingError(
                    "staged file changed while it was being read",
                    code="stage_file_changed",
                    http_status=409,
                )
            sent += len(chunk)
            yield chunk


def stage_identity_headers(opened: OpenStageFile) -> Dict[str, str]:
    """Build path-free source identity headers for a binary response."""
    identity = opened.identity
    return {
        "X-PymolPlugins-Stage-Schema": STAGE_RESPONSE_SCHEMA,
        "X-PymolPlugins-Stage-Root": opened.alias,
        "X-PymolPlugins-Source-Identity": identity.token,
        "X-PymolPlugins-Source-Device": str(identity.device),
        "X-PymolPlugins-Source-Inode": str(identity.inode),
        "X-PymolPlugins-Source-Size": str(identity.size),
        "X-PymolPlugins-Source-Mtime-Ns": str(identity.mtime_ns),
        "X-PymolPlugins-Source-Mode": str(identity.mode),
    }


def stage_identity_payload(opened: OpenStageFile) -> Dict[str, object]:
    """Build the JSON metadata response without exposing a filesystem path."""
    identity = opened.identity
    return {
        "schema": STAGE_STAT_SCHEMA,
        "root_alias": opened.alias,
        "source": {
            "token": identity.token,
            "device": identity.device,
            "inode": identity.inode,
            "size": identity.size,
            "mtime_ns": identity.mtime_ns,
            "mode": identity.mode,
        },
    }
