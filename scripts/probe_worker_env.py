#!/usr/bin/env python3
"""Inspect a WSL worker environment without installing or initializing backends.

The default optional-backend probe uses module discovery only; the core worker
module is imported to verify its entry point. ``--import-optional`` imports PLIP
and PyRosetta in isolated child processes and never calls ``pyrosetta.init``.
``--check-worker`` performs one authenticated GET of ``/v1/health``.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SCHEMA = "pymolplugins.worker_probe/1.0"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKER_URL = "http://127.0.0.1:8765"
MAX_HEALTH_RESPONSE_BYTES = 2 * 1024 * 1024

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _module_spec(name: str) -> Dict[str, Any]:
    try:
        spec = importlib.util.find_spec(name)
    except Exception as exc:
        return {"discoverable": False, "error": str(exc)}
    if spec is None:
        return {"discoverable": False, "origin": None}
    return {
        "discoverable": True,
        "origin": str(spec.origin) if spec.origin else None,
    }


def _import_optional(name: str, timeout: float) -> Dict[str, Any]:
    code = (
        "import importlib, json, sys; "
        "m = importlib.import_module(sys.argv[1]); "
        "print(json.dumps({'version': str(getattr(m, '__version__', 'unknown'))}))"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code, name],
            cwd=str(REPOSITORY_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"importable": False, "error": f"import exceeded {timeout:g} seconds"}
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    result: Dict[str, Any] = {
        "importable": completed.returncode == 0,
        "returncode": completed.returncode,
    }
    if completed.returncode == 0:
        try:
            result.update(json.loads(stdout.splitlines()[-1]))
        except Exception:
            result["version"] = "unknown"
            if stdout:
                result["stdout_tail"] = stdout[-1000:]
    else:
        result["error"] = (stderr or stdout or "import process failed")[-2000:]
    return result


def _wsl_info() -> Dict[str, Any]:
    release = platform.release()
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in release.lower()
    return {
        "detected": is_wsl,
        "distro": os.environ.get("WSL_DISTRO_NAME"),
        "interop": bool(os.environ.get("WSL_INTEROP")),
        "kernel_release": release,
    }


def _worker_module() -> Dict[str, Any]:
    try:
        module = importlib.import_module("pymolplugins.worker.app")
        return {
            "importable": True,
            "schema": getattr(module, "WORKER_SCHEMA", "unknown"),
            "path": str(Path(module.__file__).resolve()),
        }
    except Exception as exc:
        return {"importable": False, "error": str(exc)}


def _check_worker(url: str, token: str, timeout: float) -> Dict[str, Any]:
    endpoint = url.rstrip("/") + "/v1/health"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(endpoint, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_HEALTH_RESPONSE_BYTES + 1)
            if len(data) > MAX_HEALTH_RESPONSE_BYTES:
                raise ValueError(
                    f"health response exceeds {MAX_HEALTH_RESPONSE_BYTES} byte limit"
                )
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("health response is not a JSON object")
            return {
                "reachable": True,
                "status_code": response.status,
                "endpoint": endpoint,
                "response": payload,
            }
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        return {
            "reachable": False,
            "status_code": exc.code,
            "endpoint": endpoint,
            "error": detail or str(exc),
        }
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"reachable": False, "endpoint": endpoint, "error": str(exc)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--import-optional",
        action="store_true",
        help="Import PLIP/PyRosetta in isolated children (never calls pyrosetta.init).",
    )
    parser.add_argument(
        "--import-timeout",
        type=float,
        default=20.0,
        help="Per-module isolated import timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--check-worker",
        action="store_true",
        help="GET /v1/health using the configured URL/token.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PYMOLPLUGINS_WORKER_URL", DEFAULT_WORKER_URL),
        help="Worker base URL (default: env PYMOLPLUGINS_WORKER_URL or loopback).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Health-request timeout in seconds (default: 3).",
    )
    parser.add_argument(
        "--require-token",
        action="store_true",
        help="Exit nonzero when PYMOLPLUGINS_WORKER_TOKEN is empty.",
    )
    parser.add_argument(
        "--require-backend",
        choices=("plip", "pyrosetta"),
        action="append",
        default=[],
        help="Exit nonzero unless this backend is discoverable/importable; repeatable.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser


def probe(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    if args.import_timeout <= 0:
        raise ValueError("--import-timeout must be positive")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    parsed_url = urlparse(args.url)
    if parsed_url.scheme != "http" or not parsed_url.hostname:
        raise ValueError("--url must be an http:// URL with a host")

    token = os.environ.get("PYMOLPLUGINS_WORKER_TOKEN", "")
    optional: Dict[str, Dict[str, Any]] = {}
    for label, module_name in (("plip", "plip"), ("pyrosetta", "pyrosetta")):
        status = _module_spec(module_name)
        if args.import_optional and status.get("discoverable"):
            status["import_probe"] = _import_optional(module_name, args.import_timeout)
        optional[label] = status

    result: Dict[str, Any] = {
        "schema": SCHEMA,
        "repository_root": str(REPOSITORY_ROOT),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "prefix": sys.prefix,
        },
        "platform": platform.platform(),
        "wsl": _wsl_info(),
        "worker_module": _worker_module(),
        "configuration": {
            "worker_url": args.url,
            "token_configured": bool(token),
            "token_length": len(token) if token else 0,
            "pyrosetta_flags_configured": bool(
                os.environ.get("PYMOLPLUGINS_PYROSETTA_FLAGS")
            ),
        },
        "optional_backends": optional,
        "notes": [
            "No package was installed or downloaded.",
            "Module discovery alone does not prove binary/API compatibility.",
            "PyRosetta import probing never calls pyrosetta.init.",
        ],
    }

    exit_code = 0
    if not result["worker_module"].get("importable"):
        exit_code = 1
    if args.require_token and not token:
        result.setdefault("requirements_failed", []).append("worker token is empty")
        exit_code = 1

    for backend in args.require_backend:
        status = optional[backend]
        available = bool(status.get("discoverable"))
        if args.import_optional:
            available = available and bool(status.get("import_probe", {}).get("importable"))
        if not available:
            result.setdefault("requirements_failed", []).append(
                f"required backend is unavailable: {backend}"
            )
            exit_code = 1

    if args.check_worker:
        health = _check_worker(args.url, token, args.timeout)
        result["worker_health"] = health
        if not health.get("reachable"):
            exit_code = 1

    result["status"] = "ok" if exit_code == 0 else "failed"
    return result, exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, exit_code = probe(args)
    except Exception as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            result,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
