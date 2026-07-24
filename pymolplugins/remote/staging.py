"""Streaming Windows Host client for the worker file-staging endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import IncompleteRead, RemoteDisconnected
import json
import os
import re
import socket
import tempfile
import time
from typing import Dict, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .client import WorkerRequestError, _NoRedirect, _base_url, worker_request


STAGE_ENDPOINT = "/v1/stage/file"
STAGE_STAT_ENDPOINT = "/v1/stage/stat"
STAGE_RESPONSE_SCHEMA = "pymolplugins.stage-file/1.0"
STAGE_STAT_SCHEMA = "pymolplugins.stage-stat/1.0"
DEFAULT_MAX_STAGE_BYTES = 256 * 1024 * 1024
_IDENTITY_RE = re.compile(r"^v1-[0-9a-f]{64}$")


@dataclass(frozen=True)
class StagedSourceIdentity:
    """Source stat identity supplied by the WSL worker."""

    token: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    mode: int


@dataclass(frozen=True)
class StageDownloadResult:
    """Result of an atomically completed binary staging download."""

    destination: str
    root_alias: str
    relative_path: str
    bytes_written: int
    source: StagedSourceIdentity

    def to_dict(self) -> Dict[str, object]:
        return {
            "destination": self.destination,
            "root_alias": self.root_alias,
            "relative_path": self.relative_path,
            "bytes_written": self.bytes_written,
            "source": {
                "token": self.source.token,
                "device": self.source.device,
                "inode": self.source.inode,
                "size": self.source.size,
                "mtime_ns": self.source.mtime_ns,
                "mode": self.source.mode,
            },
        }


def _request_headers(token: str) -> Dict[str, str]:
    headers = {
        "Accept": "application/octet-stream, application/json",
        "Content-Type": "application/json",
    }
    secret = token.strip() or os.environ.get("PYMOLPLUGINS_WORKER_TOKEN", "").strip()
    if secret:
        headers["Authorization"] = "Bearer %s" % secret
    return headers


def _integer_header(headers, name: str, *, allow_negative: bool = False) -> int:
    value = headers.get(name)
    if value is None:
        raise WorkerRequestError(
            "worker returned invalid staging identity headers",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkerRequestError(
            "worker returned invalid staging identity headers",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        ) from exc
    if number < 0 and not allow_negative:
        raise WorkerRequestError(
            "worker returned invalid staging identity headers",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        )
    return number


def _response_identity(headers, alias: str, limit: int) -> StagedSourceIdentity:
    content_type = headers.get("Content-Type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/octet-stream":
        raise WorkerRequestError(
            "worker returned a non-binary staging response",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        )
    if headers.get("X-PymolPlugins-Stage-Schema") != STAGE_RESPONSE_SCHEMA:
        raise WorkerRequestError(
            "worker returned an unexpected staging schema",
            code="schema_mismatch",
            endpoint=STAGE_ENDPOINT,
        )
    if headers.get("X-PymolPlugins-Stage-Root") != alias:
        raise WorkerRequestError(
            "worker returned a mismatched staging root alias",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        )
    content_length = _integer_header(headers, "Content-Length")
    size = _integer_header(headers, "X-PymolPlugins-Source-Size")
    if size != content_length:
        raise WorkerRequestError(
            "worker returned inconsistent staging size headers",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        )
    if size > limit:
        raise WorkerRequestError(
            "worker staging response exceeds %d byte limit" % limit,
            code="response_too_large",
            endpoint=STAGE_ENDPOINT,
        )
    token = headers.get("X-PymolPlugins-Source-Identity", "")
    if not _IDENTITY_RE.fullmatch(token):
        raise WorkerRequestError(
            "worker returned an invalid staging identity token",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
        )
    return StagedSourceIdentity(
        token=token,
        device=_integer_header(headers, "X-PymolPlugins-Source-Device"),
        inode=_integer_header(headers, "X-PymolPlugins-Source-Inode"),
        size=size,
        mtime_ns=_integer_header(
            headers, "X-PymolPlugins-Source-Mtime-Ns", allow_negative=True
        ),
        mode=_integer_header(headers, "X-PymolPlugins-Source-Mode"),
    )


def _mapping_integer(source: Mapping[str, object], name: str, *, signed=False) -> int:
    value = source.get(name)
    if type(value) is not int or (value < 0 and not signed):
        raise WorkerRequestError(
            "worker returned invalid staging identity metadata",
            code="invalid_response",
            endpoint=STAGE_STAT_ENDPOINT,
        )
    return value


def _mapping_identity(value: object, limit: int) -> StagedSourceIdentity:
    if not isinstance(value, Mapping):
        raise WorkerRequestError(
            "worker returned missing staging identity metadata",
            code="invalid_response",
            endpoint=STAGE_STAT_ENDPOINT,
        )
    token = value.get("token")
    if not isinstance(token, str) or not _IDENTITY_RE.fullmatch(token):
        raise WorkerRequestError(
            "worker returned an invalid staging identity token",
            code="invalid_response",
            endpoint=STAGE_STAT_ENDPOINT,
        )
    size = _mapping_integer(value, "size")
    if size > limit:
        raise WorkerRequestError(
            "worker staged file exceeds %d byte limit" % limit,
            code="response_too_large",
            endpoint=STAGE_STAT_ENDPOINT,
        )
    return StagedSourceIdentity(
        token=token,
        device=_mapping_integer(value, "device"),
        inode=_mapping_integer(value, "inode"),
        size=size,
        mtime_ns=_mapping_integer(value, "mtime_ns", signed=True),
        mode=_mapping_integer(value, "mode"),
    )


def stat_staged_file(
    root_alias: str,
    relative_path: str,
    *,
    url: str = "",
    timeout: float = 15.0,
    token: str = "",
    max_file_bytes: int = DEFAULT_MAX_STAGE_BYTES,
) -> StagedSourceIdentity:
    """Return validated remote identity without transferring structure bytes."""
    limit = int(max_file_bytes)
    if limit < 1 or float(timeout) <= 0:
        raise ValueError("staging file and timeout limits must be positive")
    if not isinstance(root_alias, str) or not root_alias:
        raise ValueError("root_alias must be a non-empty string")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("relative_path must be a non-empty string")
    result = worker_request(
        STAGE_STAT_ENDPOINT,
        {
            "schema_version": 1,
            "root_alias": root_alias,
            "relative_path": relative_path,
        },
        url=url,
        timeout=float(timeout),
        token=token,
        max_request_bytes=64 * 1024,
        max_response_bytes=64 * 1024,
        expected_schema=STAGE_STAT_SCHEMA,
    )
    if result.get("root_alias") != root_alias:
        raise WorkerRequestError(
            "worker returned a mismatched staging root alias",
            code="invalid_response",
            endpoint=STAGE_STAT_ENDPOINT,
        )
    return _mapping_identity(result.get("source"), limit)


def _http_error(exc: HTTPError) -> WorkerRequestError:
    try:
        raw = exc.read(64 * 1024 + 1)
        payload = json.loads(raw.decode("utf-8")) if len(raw) <= 64 * 1024 else {}
    except Exception:
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, Mapping) else {}
    code = str(error.get("code") or "http_%d" % exc.code)
    message = str(error.get("message") or "worker HTTP %d" % exc.code)
    return WorkerRequestError(
        message,
        code=code,
        endpoint=STAGE_ENDPOINT,
        http_status=exc.code,
        retriable=exc.code >= 500,
        detail=error if isinstance(error, Mapping) else {},
    )


def _stream_atomic(
    response, destination: str, size: int, deadline: float, chunk: int
) -> int:
    parent = os.path.dirname(destination) or os.curdir
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % (os.path.basename(destination) or "stage"),
        suffix=".part",
        dir=parent,
    )
    written = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            while written < size:
                if time.monotonic() >= deadline:
                    raise WorkerRequestError(
                        "worker staging request exceeded its total time limit",
                        code="timeout",
                        endpoint=STAGE_ENDPOINT,
                        retriable=True,
                    )
                data = response.read(min(chunk, size - written))
                if time.monotonic() >= deadline:
                    raise WorkerRequestError(
                        "worker staging request exceeded its total time limit",
                        code="timeout",
                        endpoint=STAGE_ENDPOINT,
                        retriable=True,
                    )
                if not data:
                    raise WorkerRequestError(
                        "worker staging response ended before Content-Length",
                        code="invalid_response",
                        endpoint=STAGE_ENDPOINT,
                        retriable=True,
                    )
                handle.write(data)
                written += len(data)
            handle.flush()
            os.fsync(handle.fileno())
        if time.monotonic() >= deadline:
            raise WorkerRequestError(
                "worker staging request exceeded its total time limit",
                code="timeout",
                endpoint=STAGE_ENDPOINT,
                retriable=True,
            )
        os.replace(temporary, destination)
        temporary = ""
        return written
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def download_staged_file(
    root_alias: str,
    relative_path: str,
    destination,
    *,
    url: str = "",
    timeout: float = 60.0,
    token: str = "",
    max_response_bytes: int = DEFAULT_MAX_STAGE_BYTES,
    chunk_bytes: int = 256 * 1024,
) -> StageDownloadResult:
    """Stream one allowlisted WSL file into an atomically replaced Host path."""
    limit = int(max_response_bytes)
    chunk = int(chunk_bytes)
    seconds = float(timeout)
    if limit < 1 or chunk < 1 or seconds <= 0:
        raise ValueError("staging size, chunk, and timeout limits must be positive")
    if not isinstance(root_alias, str) or not root_alias:
        raise ValueError("root_alias must be a non-empty string")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("relative_path must be a non-empty string")
    target = os.path.abspath(os.path.expanduser(os.fspath(destination)))
    payload = json.dumps(
        {
            "schema_version": 1,
            "root_alias": root_alias,
            "relative_path": relative_path,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(payload) > 64 * 1024:
        raise WorkerRequestError(
            "staging request exceeds 65536 byte limit",
            code="request_too_large",
            endpoint=STAGE_ENDPOINT,
        )
    request = Request(
        _base_url(url) + STAGE_ENDPOINT,
        data=payload,
        headers=_request_headers(token),
        method="POST",
    )
    deadline = time.monotonic() + seconds
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=seconds) as response:
            if getattr(response, "status", 200) != 200:
                raise WorkerRequestError(
                    "worker returned an unexpected staging status",
                    code="invalid_response",
                    endpoint=STAGE_ENDPOINT,
                )
            source = _response_identity(response.headers, root_alias, limit)
            written = _stream_atomic(response, target, source.size, deadline, chunk)
    except WorkerRequestError:
        raise
    except HTTPError as exc:
        raise _http_error(exc) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise WorkerRequestError(
            "worker staging request timed out after %g seconds" % seconds,
            code="timeout",
            endpoint=STAGE_ENDPOINT,
            retriable=True,
        ) from exc
    except (IncompleteRead, RemoteDisconnected, ConnectionError) as exc:
        raise WorkerRequestError(
            "worker returned an incomplete staging response",
            code="invalid_response",
            endpoint=STAGE_ENDPOINT,
            retriable=True,
        ) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            code = "timeout"
            message = "worker staging request timed out after %g seconds" % seconds
        else:
            code = "unreachable"
            message = "worker is unreachable: %s" % reason
        raise WorkerRequestError(
            message,
            code=code,
            endpoint=STAGE_ENDPOINT,
            retriable=True,
        ) from exc
    except OSError as exc:
        raise WorkerRequestError(
            "could not write the staged file: %s" % exc,
            code="local_io_error",
            endpoint=STAGE_ENDPOINT,
        ) from exc
    return StageDownloadResult(
        destination=target,
        root_alias=root_alias,
        relative_path=relative_path,
        bytes_written=written,
        source=source,
    )


__all__ = [
    "DEFAULT_MAX_STAGE_BYTES",
    "STAGE_ENDPOINT",
    "STAGE_RESPONSE_SCHEMA",
    "STAGE_STAT_ENDPOINT",
    "STAGE_STAT_SCHEMA",
    "StageDownloadResult",
    "StagedSourceIdentity",
    "download_staged_file",
    "stat_staged_file",
]
