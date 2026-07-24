"""I/O-only structure materializers for direct and cached Host access."""

from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from pymolplugins.annotation.cache import (
    CacheResult,
    InvalidSourceError,
    PersistentStructureCache,
    SourceIdentity,
    _mtime_ns,
)


COPY_BUFFER_BYTES = 1024 * 1024


class MaterializationError(RuntimeError):
    """Base exception for structure materialization failures."""


class SourceChangedError(MaterializationError):
    """Raised when a source changes while it is being copied."""


@dataclass(frozen=True)
class MaterializedStructure:
    """A path that is ready for the Host PyMOL process to load."""

    source_path: str
    path: str
    identity: SourceIdentity
    mode: str
    cache_key: Optional[str]
    cache_hit: bool


def _physical_path(source_path: str) -> str:
    path = os.path.abspath(os.path.expanduser(os.fspath(source_path)))
    if not os.path.isfile(path):
        raise InvalidSourceError(f"source is not a regular file: {source_path}")
    return path


def _output_suffix(source_path: str, decompress_gzip: bool) -> str:
    name = os.path.basename(source_path)
    if decompress_gzip and name.lower().endswith(".gz"):
        name = name[:-3]
    _, suffix = os.path.splitext(name)
    return suffix or ".bin"


class DirectMaterializer:
    """Return an existing Host-readable file without copying it."""

    def materialize(
        self,
        source_path: str,
        *,
        provider: str = "direct",
        canonical_source: Optional[str] = None,
        include_sha256: bool = False,
        version_token: Optional[str] = None,
    ) -> MaterializedStructure:
        physical = _physical_path(source_path)
        identity = SourceIdentity.from_path(
            physical,
            provider=provider,
            canonical_source=canonical_source,
            include_sha256=include_sha256,
            version_token=version_token,
        )
        return MaterializedStructure(
            source_path=physical,
            path=physical,
            identity=identity,
            mode="direct",
            cache_key=None,
            cache_hit=False,
        )


class CopyMaterializer:
    """Copy or decompress a source into a persistent Host-local cache."""

    def __init__(
        self,
        cache: PersistentStructureCache,
        *,
        copy_buffer_bytes: int = COPY_BUFFER_BYTES,
    ) -> None:
        if int(copy_buffer_bytes) < 1:
            raise ValueError("copy_buffer_bytes must be positive")
        self.cache = cache
        self.copy_buffer_bytes = int(copy_buffer_bytes)

    def materialize(
        self,
        source_path: str,
        *,
        provider: str = "file",
        canonical_source: Optional[str] = None,
        include_sha256: bool = False,
        version_token: Optional[str] = None,
        pin_owner: Optional[str] = None,
        source_identity: Optional[SourceIdentity] = None,
        logical_path: Optional[str] = None,
    ) -> MaterializedStructure:
        """Materialize *source_path* and return its persistent Host path.

        ``source_identity`` is intended for an already-validated staging
        download.  It keeps the remote size/mtime identity while the staged
        Host file's own stat is used solely to detect changes during this copy.
        When omitted, identity is inspected directly from ``source_path``.
        ``logical_path`` preserves the original suffix when a staging download
        itself has an opaque temporary filename.
        """

        physical = _physical_path(source_path)
        try:
            observed_before = os.stat(physical)
        except OSError as exc:
            raise InvalidSourceError(
                f"cannot inspect source file {physical!r}: {exc}"
            ) from exc
        if source_identity is None:
            identity = SourceIdentity.from_path(
                physical,
                provider=provider,
                canonical_source=canonical_source,
                include_sha256=include_sha256,
                version_token=version_token,
            )
        else:
            identity = source_identity
            if int(observed_before.st_size) != identity.size:
                raise MaterializationError(
                    "staged file size does not match its remote source identity"
                )
        logical = str(logical_path or identity.canonical_source or physical)
        is_gzip = physical.lower().endswith(".gz") or logical.lower().endswith(
            ".gz"
        )
        suffix = _output_suffix(logical, is_gzip)

        def _writer(temporary: str) -> None:
            opener = gzip.open if is_gzip else open
            with opener(physical, "rb") as source, open(temporary, "wb") as output:
                shutil.copyfileobj(
                    source, output, length=self.copy_buffer_bytes
                )
                output.flush()
                os.fsync(output.fileno())
            # The identity must describe the exact source version copied.  A
            # changed source is retried by the caller under a new identity.
            try:
                after = os.stat(physical)
            except OSError as exc:
                raise SourceChangedError(
                    f"source disappeared while materializing: {physical}"
                ) from exc
            if (
                int(after.st_size) != int(observed_before.st_size)
                or _mtime_ns(after) != _mtime_ns(observed_before)
            ):
                raise SourceChangedError(
                    f"source changed while materializing: {physical}"
                )

        cached: CacheResult = self.cache.get_or_create(
            identity,
            suffix=suffix,
            writer=_writer,
            pin_owner=pin_owner,
        )
        return MaterializedStructure(
            source_path=physical,
            path=cached.path,
            identity=identity,
            mode="decompress" if is_gzip else "copy",
            cache_key=cached.key,
            cache_hit=cached.cache_hit,
        )


__all__ = [
    "COPY_BUFFER_BYTES",
    "CopyMaterializer",
    "DirectMaterializer",
    "MaterializationError",
    "MaterializedStructure",
    "SourceChangedError",
]
