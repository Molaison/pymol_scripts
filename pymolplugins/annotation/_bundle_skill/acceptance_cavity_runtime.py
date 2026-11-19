"""Run the shared cavity calculator in an isolated Python child and cache results."""

from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4


_ENGINE = Path(__file__).with_name("acceptance_cavity_geometry.py")
_INSTALL_LOCK = Lock()
_DEPENDENCIES = ("numpy>=1.26,<3", "scipy>=1.11,<2")


def cavity_cache_directory():
    override = os.environ.get("PYMOLPLUGINS_STRUCTURE_CACHE_DIR")
    if override:
        return Path(override).expanduser() / "cavity"
    default = "LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME"
    return Path(os.environ.get(default) or Path.home() / ".cache") / "pymolplugins" / "cavity"


def _python():
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return str(executable)


def _run(arguments, *, timeout):
    return subprocess.run(
        arguments, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _install(parent):
    tag = f"{sys.implementation.cache_tag}-{platform.system()}-{platform.machine()}"
    target = parent / tag
    with _INSTALL_LOCK:
        if target.is_dir():
            return target
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="setup-", dir=parent) as directory:
            temporary = Path(directory)
            python = _python()
            if _run([python, "-I", "-m", "pip", "--version"], timeout=10).returncode:
                env = temporary / "bootstrap"
                built = _run([python, "-I", "-m", "venv", str(env)], timeout=35)
                if built.returncode:
                    raise RuntimeError("Could not prepare pip for the cavity worker.")
                python = str(env / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
            staging = temporary / "wheels"
            downloaded = _run([
                python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                "--no-input", "--retries", "1", "--timeout", "15", "install",
                "--index-url", "https://pypi.org/simple", "--only-binary=:all:",
                "--target", str(staging), *_DEPENDENCIES,
            ], timeout=150)
            if downloaded.returncode:
                error = downloaded.stderr.strip().splitlines()
                raise RuntimeError(f"Could not install cavity dependencies. {error[-1] if error else ''}".strip())
            try:
                staging.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
    return target


def _read_result(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data["recesses"], list) and isinstance(data["enclosed"], list):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def _write_result(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _trim(folder):
    limits = 2048
    files = list(folder.glob("*.json"))
    if len(files) > limits:
        oldest = sorted((path.stat().st_mtime, path) for path in files if path.exists())
        for _, path in oldest[:-limits]:
            path.unlink(missing_ok=True)


def calculate_cavity(snapshot):
    """Reuse successful results, including empty ones. Errors can be retried."""
    cache = cavity_cache_directory()
    payload = json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode("utf-8")
    key = sha256(_ENGINE.read_bytes() + payload).hexdigest()
    entry = cache / "results" / f"{key}.json"
    result = _read_result(entry)
    if result is not None:
        try:
            entry.touch(exist_ok=True)
        except OSError:
            pass
        return result, True

    with TemporaryDirectory(prefix="pymol-cavity-") as directory:
        source, destination = (Path(directory) / name for name in ("atoms.json", "result.json"))
        source.write_bytes(payload)
        library = ""
        runtime = cache / "runtime"
        tag = f"{sys.implementation.cache_tag}-{platform.system()}-{platform.machine()}"
        if (runtime / tag).is_dir():
            library = str(runtime / tag)
        arguments = [_python(), "-I", "-u", str(_ENGINE), library, str(source), str(destination)]
        completed = _run(arguments, timeout=45)
        if completed.returncode == 42 and not library:
            arguments[4] = str(_install(runtime))
            completed = _run(arguments, timeout=45)
        if completed.returncode:
            detail = completed.stderr.strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "Cavity worker failed.")
        result = _read_result(destination)
        if result is None:
            raise RuntimeError("Cavity worker returned no usable data.")
    try:
        _write_result(entry, result)
        _trim(entry.parent)
    except OSError as exc:
        print(f"PyMOL cavity cache: {exc}")
    return result, False
