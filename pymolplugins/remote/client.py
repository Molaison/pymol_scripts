"""Bounded, typed standard-library client for the WSL worker."""

import json
import os
import socket
from typing import Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_WORKER_URL = "http://127.0.0.1:8765"
DEFAULT_MAX_REQUEST_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class WorkerRequestError(RuntimeError):
    """Typed client failure safe to display without request bodies or tokens."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        endpoint: str,
        http_status: Optional[int] = None,
        retriable: bool = False,
        detail: Optional[Mapping[str, object]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.endpoint = endpoint
        self.http_status = http_status
        self.retriable = bool(retriable)
        self.detail = dict(detail or {})

    def to_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "endpoint": self.endpoint,
            "http_status": self.http_status,
            "retriable": self.retriable,
            "detail": dict(self.detail),
        }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value: str = "") -> str:
    url = (
        str(value).strip()
        or os.environ.get("PYMOLPLUGINS_WORKER_URL", "").strip()
        or DEFAULT_WORKER_URL
    ).rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("worker URL must be an http:// host[:port] URL")
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise ValueError("worker URL must not contain credentials or a path")
    if parsed.query or parsed.fragment:
        raise ValueError("worker URL must not contain query or fragment components")
    return url


def _endpoint_path(endpoint: str) -> str:
    value = str(endpoint).strip()
    parsed = urlsplit(value)
    if not value.startswith("/") or parsed.scheme or parsed.netloc:
        raise ValueError("worker endpoint must be an absolute path on the configured worker")
    if parsed.query or parsed.fragment:
        raise ValueError("worker endpoint must not contain query or fragment components")
    return parsed.path


def _headers(has_body: bool, token: str = "") -> Dict[str, str]:
    values = {"Accept": "application/json"}
    if has_body:
        values["Content-Type"] = "application/json"
    secret = token.strip() or os.environ.get("PYMOLPLUGINS_WORKER_TOKEN", "").strip()
    if secret:
        values["Authorization"] = f"Bearer {secret}"
    return values


def _read_bounded(response, limit: int, endpoint: str) -> bytes:
    header = response.headers.get("Content-Length")
    if header:
        try:
            declared = int(header)
        except ValueError:
            declared = -1
        if declared > limit:
            raise WorkerRequestError(
                f"worker response exceeds {limit} byte limit",
                code="response_too_large",
                endpoint=endpoint,
                retriable=False,
            )
    data = response.read(limit + 1)
    if len(data) > limit:
        raise WorkerRequestError(
            f"worker response exceeds {limit} byte limit",
            code="response_too_large",
            endpoint=endpoint,
            retriable=False,
        )
    return data


def worker_request(
    endpoint: str,
    payload: Optional[Mapping[str, object]] = None,
    *,
    url: str = "",
    timeout: float = 60.0,
    token: str = "",
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    expected_schema: str = "",
) -> Dict[str, object]:
    path = _endpoint_path(endpoint)
    request_limit = int(max_request_bytes)
    response_limit = int(max_response_bytes)
    if request_limit < 1 or response_limit < 1:
        raise ValueError("request and response limits must be positive")
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        if len(data) > request_limit:
            raise WorkerRequestError(
                f"worker request exceeds {request_limit} byte limit",
                code="request_too_large",
                endpoint=path,
            )
    request = Request(
        _base_url(url) + path,
        data=data,
        headers=_headers(data is not None, token),
        method="POST" if data is not None else "GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            content_type = response.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise WorkerRequestError(
                    "worker returned a non-JSON Content-Type",
                    code="invalid_response",
                    endpoint=path,
                )
            raw = _read_bounded(response, response_limit, path)
    except WorkerRequestError:
        raise
    except HTTPError as exc:
        try:
            raw = _read_bounded(exc, response_limit, path)
            error_payload = json.loads(raw.decode("utf-8"))
        except WorkerRequestError:
            raise
        except Exception:
            error_payload = {}
        error = error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
        code = str(error.get("code") or f"http_{exc.code}")
        message = str(error.get("message") or f"worker HTTP {exc.code}")
        raise WorkerRequestError(
            message,
            code=code,
            endpoint=path,
            http_status=exc.code,
            retriable=exc.code >= 500,
            detail=error if isinstance(error, dict) else {},
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise WorkerRequestError(
            f"worker request timed out after {float(timeout):g} seconds",
            code="timeout",
            endpoint=path,
            retriable=True,
        ) from exc
    except (URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            code, message = "timeout", f"worker request timed out after {float(timeout):g} seconds"
        else:
            code, message = "unreachable", f"worker is unreachable: {reason}"
        raise WorkerRequestError(
            message,
            code=code,
            endpoint=path,
            retriable=True,
        ) from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerRequestError(
            "worker returned invalid JSON",
            code="invalid_response",
            endpoint=path,
        ) from exc
    if not isinstance(result, dict):
        raise WorkerRequestError(
            "worker returned a non-object JSON response",
            code="invalid_response",
            endpoint=path,
        )
    if expected_schema and result.get("schema") != expected_schema:
        raise WorkerRequestError(
            f"unexpected result schema: {result.get('schema')!r}",
            code="schema_mismatch",
            endpoint=path,
            detail={"expected_schema": expected_schema, "actual_schema": result.get("schema")},
        )
    return result
