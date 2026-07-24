"""Loopback HTTP worker for PLIP and licensed PyRosetta environments."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import platform
import re
import socket
import sys
import traceback
from typing import Dict, Mapping, Optional
from urllib.parse import urlparse

try:
    from pymolplugins.worker.backends.plip import PlipBackend, PlipBackendError
    from pymolplugins.worker.backends.rosetta import RosettaBackend, RosettaBackendError
    from pymolplugins.worker.staging import (
        FileStagingBackend,
        StagingError,
        parse_stage_roots,
        stage_identity_headers,
        stage_identity_payload,
    )
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.worker.backends.plip import PlipBackend, PlipBackendError  # type: ignore
    from pymolplugins.worker.backends.rosetta import RosettaBackend, RosettaBackendError  # type: ignore
    from pymolplugins.worker.staging import (  # type: ignore
        FileStagingBackend,
        StagingError,
        parse_stage_roots,
        stage_identity_headers,
        stage_identity_payload,
    )


WORKER_SCHEMA = "pymolplugins.worker/1.0"
FORBIDDEN_REQUEST_KEYS = {
    "command",
    "commands",
    "executable",
    "file",
    "filename",
    "path",
    "paths",
    "python",
    "rosettascripts",
    "script",
    "shell",
    "xml",
}


class WorkerApp:
    def __init__(
        self,
        token: str = "",
        max_body_bytes: int = 10 * 1024 * 1024,
        socket_timeout: float = 15.0,
        stage_roots: Optional[Mapping[str, str]] = None,
        max_stage_file_bytes: int = 256 * 1024 * 1024,
        stage_timeout: float = 60.0,
    ):
        self.token = token
        self.max_body_bytes = int(max_body_bytes)
        self.socket_timeout = float(socket_timeout)
        if self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        if self.socket_timeout <= 0:
            raise ValueError("socket_timeout must be positive")
        self.rosetta = RosettaBackend()
        self.plip = PlipBackend()
        self.staging = FileStagingBackend(
            stage_roots,
            max_file_bytes=max_stage_file_bytes,
            max_stream_seconds=stage_timeout,
        )

    def health(self) -> Dict[str, object]:
        return {
            "schema": WORKER_SCHEMA,
            "status": "ok",
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
            "auth_required": bool(self.token),
            "staging": self.staging.capabilities(),
            "backends": {
                "rosetta": self.rosetta.health(),
                "plip": self.plip.health(),
            },
        }

    def capabilities(self) -> Dict[str, object]:
        return {
            "schema": WORKER_SCHEMA,
            "auth_required": bool(self.token),
            "limits": {
                "max_request_body_bytes": self.max_body_bytes,
                "socket_read_timeout_seconds": self.socket_timeout,
            },
            "rosetta": self.rosetta.capabilities(),
            "plip": self.plip.health(),
            "staging": self.staging.capabilities(),
            "endpoints": [
                "GET /v1/health",
                "GET /v1/capabilities",
                "POST /v1/analyze",
                "POST /v1/interactions/plip",
            ] + (
                ["POST /v1/stage/file", "POST /v1/stage/stat"]
                if self.staging.enabled
                else []
            ),
        }

    def authorized(self, header: Optional[str]) -> bool:
        if not self.token:
            return True
        prefix = "Bearer "
        if not header or not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):], self.token)


def make_handler(app: WorkerApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PymolPluginsWorker/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(app.socket_timeout)

        def log_message(self, fmt, *args):
            # Paths/status only; request bodies and structure text are never logged.
            sys.stderr.write("worker: " + (fmt % args) + "\n")

        def _send(self, status: int, payload: Mapping[str, object]):
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, code: str, message: str):
            self._send(
                status,
                {
                    "schema": WORKER_SCHEMA,
                    "error": {"code": code, "message": message},
                },
            )

        def _check_auth(self) -> bool:
            if app.authorized(self.headers.get("Authorization")):
                return True
            self._error(401, "unauthorized", "missing or invalid Bearer token")
            return False

        def _read_json(self):
            value = self._read_json_object()
            _reject_forbidden_fields(value)
            return value

        def _read_json_object(self):
            content_type = self.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise TypeError("Content-Type must be application/json")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length <= 0:
                raise ValueError("request body is empty")
            if length > app.max_body_bytes:
                raise OverflowError(
                    f"request body exceeds {app.max_body_bytes} byte limit"
                )
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError(
                    f"request body ended early: expected {length} bytes, got {len(data)}"
                )
            value = json.loads(data.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON request must be an object")
            if "schema_version" not in value:
                raise ValueError("schema_version is required")
            if value.get("schema_version") != 1:
                raise ValueError("unsupported schema_version")
            return value

        def _read_stage_request(self):
            value = self._read_json_object()
            allowed = {"schema_version", "root_alias", "relative_path"}
            extras = sorted(set(value).difference(allowed))
            if extras:
                raise ValueError("unsupported staging field: %s" % extras[0])
            if "root_alias" not in value or "relative_path" not in value:
                raise ValueError("root_alias and relative_path are required")
            return value

        def _send_stage_file(self, request: Mapping[str, object]):
            with app.staging.open_file(
                request.get("root_alias"), request.get("relative_path")
            ) as opened:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(opened.identity.size))
                self.send_header("Cache-Control", "no-store")
                for name, value in stage_identity_headers(opened).items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    for chunk in app.staging.iter_bytes(opened):
                        self.wfile.write(chunk)
                except (OSError, StagingError):
                    # Headers have already committed the binary response. Closing the
                    # connection makes a short transfer fail client-side; a JSON error
                    # cannot safely be appended to binary structure bytes.
                    self.close_connection = True
                except Exception:
                    traceback.print_exc()
                    self.close_connection = True

        def _send_stage_stat(self, request: Mapping[str, object]):
            with app.staging.open_file(
                request.get("root_alias"), request.get("relative_path")
            ) as opened:
                self._send(200, stage_identity_payload(opened))

        def do_GET(self):
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            if path == "/v1/health":
                self._send(200, app.health())
            elif path == "/v1/capabilities":
                self._send(200, app.capabilities())
            else:
                self._error(404, "not_found", "unknown endpoint")

        def do_POST(self):
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            if path in {"/v1/stage/file", "/v1/stage/stat"}:
                if not app.staging.enabled:
                    self._error(404, "staging_disabled", "file staging is not enabled")
                    return
                try:
                    request = self._read_stage_request()
                    if path == "/v1/stage/file":
                        self._send_stage_file(request)
                    else:
                        self._send_stage_stat(request)
                except OverflowError as exc:
                    self._error(413, "request_too_large", str(exc))
                except TypeError as exc:
                    self._error(415, "unsupported_media_type", str(exc))
                except socket.timeout:
                    self._error(408, "request_timeout", "request body read timed out")
                except (ValueError, json.JSONDecodeError) as exc:
                    self._error(400, "invalid_request", str(exc))
                except StagingError as exc:
                    self._error(exc.http_status, exc.code, str(exc))
                except Exception:
                    traceback.print_exc()
                    self._error(500, "internal_error", "internal staging error")
                return
            if path not in {"/v1/analyze", "/v1/interactions/plip"}:
                self._error(404, "not_found", "unknown endpoint")
                return
            try:
                request = self._read_json()
                if path == "/v1/analyze":
                    self._send(200, app.rosetta.analyze(request))
                elif path == "/v1/interactions/plip":
                    self._send(200, app.plip.analyze(request))
            except OverflowError as exc:
                self._error(413, "request_too_large", str(exc))
            except TypeError as exc:
                self._error(415, "unsupported_media_type", str(exc))
            except socket.timeout:
                self._error(408, "request_timeout", "request body read timed out")
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(400, "invalid_request", str(exc))
            except (RosettaBackendError, PlipBackendError) as exc:
                self._error(422, "backend_error", str(exc))
            except Exception as exc:
                traceback.print_exc()
                self._error(500, "internal_error", str(exc))

    return Handler


def _reject_forbidden_fields(value: object, location: str = "request") -> None:
    """Reject path/command/XML surfaces even though no backend executes them."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            raw_key = str(key).lower()
            normalized = raw_key.replace("_", "").replace("-", "")
            key_tokens = set(re.findall(r"[a-z0-9]+", raw_key))
            if normalized in FORBIDDEN_REQUEST_KEYS or key_tokens.intersection(
                FORBIDDEN_REQUEST_KEYS
            ):
                raise ValueError(f"forbidden field: {location}.{key}")
            _reject_forbidden_fields(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_fields(child, f"{location}[{index}]")


def _is_loopback_host(host: str) -> bool:
    value = str(host).strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _server_class_for_host(host: str):
    try:
        address = ipaddress.ip_address(str(host).strip())
    except ValueError:
        return ThreadingHTTPServer
    return _IPv6ThreadingHTTPServer if address.version == 6 else ThreadingHTTPServer


def _read_token_file(filename: str) -> str:
    path = os.path.abspath(os.path.expanduser(filename))
    with open(path, "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        raise ValueError(f"token file is empty: {path}")
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token",
        default=None,
        help="Bearer token (prefer PYMOLPLUGINS_WORKER_TOKEN or --token-file)",
    )
    token_group.add_argument(
        "--token-file",
        default=os.environ.get("PYMOLPLUGINS_WORKER_TOKEN_FILE", ""),
        help="read the Bearer token from a private file",
    )
    parser.add_argument("--max-body-mb", type=float, default=10.0)
    parser.add_argument("--socket-timeout", type=float, default=15.0)
    parser.add_argument(
        "--stage-root",
        action="append",
        default=[],
        metavar="ALIAS=PATH",
        help="allow staging below PATH using ALIAS (repeatable; absent disables staging)",
    )
    parser.add_argument("--max-stage-mb", type=float, default=256.0)
    parser.add_argument("--stage-timeout", type=float, default=60.0)
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="explicitly permit a non-loopback bind; a Bearer token is also required",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.port < 1 or args.port > 65535:
        raise SystemExit("--port must be in [1, 65535]")
    if args.max_body_mb <= 0:
        raise SystemExit("--max-body-mb must be positive")
    if args.socket_timeout <= 0:
        raise SystemExit("--socket-timeout must be positive")
    if args.max_stage_mb <= 0:
        raise SystemExit("--max-stage-mb must be positive")
    if args.stage_timeout <= 0:
        raise SystemExit("--stage-timeout must be positive")
    try:
        stage_roots = parse_stage_roots(args.stage_root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        token = (
            args.token
            if args.token is not None
            else (
                _read_token_file(args.token_file)
                if args.token_file
                else os.environ.get("PYMOLPLUGINS_WORKER_TOKEN", "").strip()
            )
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.token is not None:
        print(
            "WARNING: --token exposes the secret in the process command line; "
            "prefer the environment or --token-file",
            file=sys.stderr,
        )
    if not _is_loopback_host(args.host):
        if not args.allow_non_loopback:
            raise SystemExit(
                "refusing non-loopback bind without --allow-non-loopback"
            )
        if not token:
            raise SystemExit("a Bearer token is required for non-loopback binding")
        print(
            "WARNING: worker is binding a non-loopback address; also apply firewall rules",
            file=sys.stderr,
        )
    app = WorkerApp(
        token=token,
        max_body_bytes=int(args.max_body_mb * 1024 * 1024),
        socket_timeout=args.socket_timeout,
        stage_roots=stage_roots,
        max_stage_file_bytes=int(args.max_stage_mb * 1024 * 1024),
        stage_timeout=args.stage_timeout,
    )
    server_class = _server_class_for_host(args.host)
    server = server_class((args.host, args.port), make_handler(app))
    print(
        f"PymolPlugins worker listening on http://{args.host}:{args.port} "
        f"(auth_required={bool(token)}, staging_enabled={bool(stage_roots)})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
