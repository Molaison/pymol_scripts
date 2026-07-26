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
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .client import WorkerRequestError, _NoRedirect, _base_url, worker_request


STAGE_ENDPOINT = "/v1/stage/file"
STAGE_STAT_ENDPOINT = "/v1/stage/stat"
STAGE_STAT_BATCH_ENDPOINT = "/v1/stage/stat-batch"
STAGE_RESPONSE_SCHEMA = "pymolplugins.stage-file/1.0"
STAGE_STAT_SCHEMA = "pymolplugins.stage-stat/1.0"
STAGE_STAT_BATCH_SCHEMA = "pymolplugins.stage-stat-batch/1.0"
DEFAULT_MAX_STAGE_BYTES = 256 * 1024 * 1024
MAX_STAGE_STAT_BATCH_ITEMS = 256
MAX_STAGE_STAT_BATCH_REQUEST_BYTES = 64 * 1024
MAX_STAGE_STAT_BATCH_RESPONSE_BYTES = 2 * 1024 * 1024
_IDENTITY_RE = re.compile(r"^v1-[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BATCH_ITEM_ERROR_STATUS = {
    "invalid_stage_path": 400,
    "stage_not_regular_file": 400,
    "stage_path_forbidden": 403,
    "stage_file_unreadable": 403,
    "stage_file_not_found": 404,
    "stage_file_too_large": 413,
}


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
class StagedStatResult:
    """One ordered batch-stat result with exactly one source or error."""

    relative_path: str
    source: Optional[StagedSourceIdentity] = None
    error: Optional[WorkerRequestError] = None

    def __post_init__(self) -> None:
        if bool(self.source is not None) == bool(self.error is not None):
            raise ValueError("batch stat result requires exactly one source or error")

    @property
    def ok(self) -> bool:
        return self.source is not None


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


def _mapping_integer(
    source: Mapping[str, object],
    name: str,
    *,
    signed: bool = False,
    endpoint: str = STAGE_STAT_ENDPOINT,
) -> int:
    value = source.get(name)
    if type(value) is not int or (value < 0 and not signed):
        raise WorkerRequestError(
            "worker returned invalid staging identity metadata",
            code="invalid_response",
            endpoint=endpoint,
        )
    return value


def _mapping_identity(
    value: object,
    limit: int,
    *,
    endpoint: str = STAGE_STAT_ENDPOINT,
    strict_fields: bool = False,
) -> StagedSourceIdentity:
    if not isinstance(value, Mapping):
        raise WorkerRequestError(
            "worker returned missing staging identity metadata",
            code="invalid_response",
            endpoint=endpoint,
        )
    expected_fields = {"token", "device", "inode", "size", "mtime_ns", "mode"}
    if strict_fields and set(value) != expected_fields:
        raise WorkerRequestError(
            "worker returned invalid staging identity fields",
            code="invalid_response",
            endpoint=endpoint,
        )
    token = value.get("token")
    if not isinstance(token, str) or not _IDENTITY_RE.fullmatch(token):
        raise WorkerRequestError(
            "worker returned an invalid staging identity token",
            code="invalid_response",
            endpoint=endpoint,
        )
    size = _mapping_integer(value, "size", endpoint=endpoint)
    if size > limit:
        raise WorkerRequestError(
            "worker staged file exceeds %d byte limit" % limit,
            code="response_too_large",
            endpoint=endpoint,
        )
    return StagedSourceIdentity(
        token=token,
        device=_mapping_integer(value, "device", endpoint=endpoint),
        inode=_mapping_integer(value, "inode", endpoint=endpoint),
        size=size,
        mtime_ns=_mapping_integer(
            value, "mtime_ns", signed=True, endpoint=endpoint
        ),
        mode=_mapping_integer(value, "mode", endpoint=endpoint),
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


def _batch_error(value: object, relative_path: str) -> WorkerRequestError:
    if not isinstance(value, Mapping) or set(value) != {
        "code",
        "message",
        "http_status",
    }:
        raise WorkerRequestError(
            "worker returned invalid staging batch error metadata",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    code = value.get("code")
    message = value.get("message")
    http_status = value.get("http_status")
    if not isinstance(code, str) or not _ERROR_CODE_RE.fullmatch(code):
        raise WorkerRequestError(
            "worker returned invalid staging batch error code",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    expected_status = _BATCH_ITEM_ERROR_STATUS.get(code)
    if expected_status is None:
        raise WorkerRequestError(
            "worker returned an unsupported staging batch item error",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    if (
        not isinstance(message, str)
        or not message
        or len(message) > 4096
        or any(character in message for character in "\x00\r\n")
    ):
        raise WorkerRequestError(
            "worker returned invalid staging batch error message",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    if type(http_status) is not int or http_status != expected_status:
        raise WorkerRequestError(
            "worker returned mismatched staging batch error status",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    return WorkerRequestError(
        message,
        code=code,
        endpoint=STAGE_STAT_BATCH_ENDPOINT,
        http_status=http_status,
        retriable=http_status >= 500,
        detail={"relative_path": relative_path},
    )


def _stat_batch_chunk(
    root_alias: str,
    relative_paths: Sequence[str],
    *,
    url: str,
    timeout: float,
    token: str,
    max_file_bytes: int,
) -> Tuple[StagedStatResult, ...]:
    result = worker_request(
        STAGE_STAT_BATCH_ENDPOINT,
        {
            "schema_version": 1,
            "root_alias": root_alias,
            "relative_paths": list(relative_paths),
        },
        url=url,
        timeout=timeout,
        token=token,
        max_request_bytes=MAX_STAGE_STAT_BATCH_REQUEST_BYTES,
        max_response_bytes=MAX_STAGE_STAT_BATCH_RESPONSE_BYTES,
        expected_schema=STAGE_STAT_BATCH_SCHEMA,
    )
    if set(result) != {"schema", "root_alias", "results"}:
        raise WorkerRequestError(
            "worker returned invalid staging batch fields",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    if result.get("root_alias") != root_alias:
        raise WorkerRequestError(
            "worker returned a mismatched staging root alias",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )
    raw_results = result.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != len(relative_paths):
        raise WorkerRequestError(
            "worker returned mismatched staging batch cardinality",
            code="invalid_response",
            endpoint=STAGE_STAT_BATCH_ENDPOINT,
        )

    parsed: List[StagedStatResult] = []
    for expected_path, raw in zip(relative_paths, raw_results):
        if not isinstance(raw, Mapping) or raw.get("relative_path") != expected_path:
            raise WorkerRequestError(
                "worker returned a mismatched staging batch path",
                code="invalid_response",
                endpoint=STAGE_STAT_BATCH_ENDPOINT,
            )
        fields = set(raw)
        if fields == {"relative_path", "source"}:
            parsed.append(
                StagedStatResult(
                    relative_path=expected_path,
                    source=_mapping_identity(
                        raw.get("source"),
                        max_file_bytes,
                        endpoint=STAGE_STAT_BATCH_ENDPOINT,
                        strict_fields=True,
                    ),
                )
            )
        elif fields == {"relative_path", "error"}:
            parsed.append(
                StagedStatResult(
                    relative_path=expected_path,
                    error=_batch_error(raw.get("error"), expected_path),
                )
            )
        else:
            raise WorkerRequestError(
                "worker staging batch item must contain source or error",
                code="invalid_response",
                endpoint=STAGE_STAT_BATCH_ENDPOINT,
            )
    return tuple(parsed)


def _batch_chunks(root_alias: str, relative_paths: Sequence[str]):
    chunk: List[str] = []
    for relative_path in relative_paths:
        candidate = chunk + [relative_path]
        payload = {
            "schema_version": 1,
            "root_alias": root_alias,
            "relative_paths": candidate,
        }
        size = len(
            json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        )
        if (
            len(candidate) > MAX_STAGE_STAT_BATCH_ITEMS
            or size > MAX_STAGE_STAT_BATCH_REQUEST_BYTES
        ):
            if not chunk:
                raise WorkerRequestError(
                    "staging batch path exceeds request size limit",
                    code="request_too_large",
                    endpoint=STAGE_STAT_BATCH_ENDPOINT,
                )
            yield tuple(chunk)
            chunk = [relative_path]
            single_size = len(
                json.dumps(
                    {
                        "schema_version": 1,
                        "root_alias": root_alias,
                        "relative_paths": chunk,
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            if single_size > MAX_STAGE_STAT_BATCH_REQUEST_BYTES:
                raise WorkerRequestError(
                    "staging batch path exceeds request size limit",
                    code="request_too_large",
                    endpoint=STAGE_STAT_BATCH_ENDPOINT,
                )
        else:
            chunk = candidate
    if chunk:
        yield tuple(chunk)


def _single_stat_results(
    root_alias: str,
    relative_paths: Sequence[str],
    *,
    url: str,
    timeout: float,
    token: str,
    max_file_bytes: int,
) -> Tuple[StagedStatResult, ...]:
    results = []
    for relative_path in relative_paths:
        try:
            source = stat_staged_file(
                root_alias,
                relative_path,
                url=url,
                timeout=timeout,
                token=token,
                max_file_bytes=max_file_bytes,
            )
            results.append(StagedStatResult(relative_path, source=source))
        except WorkerRequestError as exc:
            results.append(StagedStatResult(relative_path, error=exc))
    return tuple(results)


def stat_staged_files(
    root_alias: str,
    relative_paths: Sequence[str],
    *,
    url: str = "",
    timeout: float = 60.0,
    token: str = "",
    max_file_bytes: int = DEFAULT_MAX_STAGE_BYTES,
    fallback_single: bool = True,
) -> Tuple[StagedStatResult, ...]:
    """Batch-stat unique paths, chunking requests and supporting old workers."""

    limit = int(max_file_bytes)
    seconds = float(timeout)
    if limit < 1 or seconds <= 0:
        raise ValueError("staging file and timeout limits must be positive")
    if not isinstance(root_alias, str) or not root_alias:
        raise ValueError("root_alias must be a non-empty string")
    if isinstance(relative_paths, (str, bytes)) or not isinstance(
        relative_paths, Sequence
    ):
        raise ValueError("relative_paths must be a sequence of strings")

    unique = []
    seen = set()
    for value in relative_paths:
        if not isinstance(value, str) or not value:
            raise ValueError("relative_paths entries must be non-empty strings")
        if value not in seen:
            seen.add(value)
            unique.append(value)
    if not unique:
        return ()

    parsed: List[StagedStatResult] = []
    for chunk_index, chunk in enumerate(_batch_chunks(root_alias, unique)):
        try:
            parsed.extend(
                _stat_batch_chunk(
                    root_alias,
                    chunk,
                    url=url,
                    timeout=seconds,
                    token=token,
                    max_file_bytes=limit,
                )
            )
        except WorkerRequestError as exc:
            old_worker = (
                fallback_single
                and chunk_index == 0
                and exc.http_status == 404
                and exc.code == "not_found"
            )
            if old_worker:
                return _single_stat_results(
                    root_alias,
                    unique,
                    url=url,
                    timeout=seconds,
                    token=token,
                    max_file_bytes=limit,
                )
            raise
    return tuple(parsed)


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
    "STAGE_STAT_BATCH_ENDPOINT",
    "STAGE_STAT_BATCH_SCHEMA",
    "STAGE_STAT_ENDPOINT",
    "STAGE_STAT_SCHEMA",
    "StageDownloadResult",
    "StagedSourceIdentity",
    "StagedStatResult",
    "download_staged_file",
    "stat_staged_file",
    "stat_staged_files",
]
