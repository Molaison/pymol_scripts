"""User-local Windows clustering tools and the upstream MMseqs2 runtime."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

from .cluster_engine import _run


_MMSEQS_BUILD = "ae60ef6964f3de689139bcfe290d9fad18f70b06"
_MMSEQS_SHA256 = "56d79e866cbd0080f17fb51693c750f210ee315f96774b6c1b2eb89924b52540"
_MMSEQS_URL = f"https://mmseqs.com/archive/{_MMSEQS_BUILD}/mmseqs-win64.zip"


def tools_root():
    data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return data / "PymolPlugins" / "clustering"


def native_paths():
    root = tools_root()
    binaries = root / "mmseqs" / "bin"
    if (binaries / "mmseqs.exe").is_file() and (binaries / "bash.exe").is_file():
        binaries = str(binaries)
    else:
        binaries = ""
    python = root / f"gemmi-{sys.version_info.major}.{sys.version_info.minor}"
    return binaries, python


def console_python():
    path = Path(sys.executable)
    return str(path.with_name("python.exe") if path.name.lower() == "pythonw.exe" else path)


def _install_mmseqs(root, stage, check_cancel):
    target = root / "mmseqs"
    if target.exists():
        raise RuntimeError(f"Incomplete Windows MMseqs2 installation at {target}.")
    archive = stage / "mmseqs.zip"
    digest = hashlib.sha256()
    with urlopen(_MMSEQS_URL, timeout=20) as response, archive.open("wb") as handle:
        while True:
            check_cancel()
            data = response.read(1024 * 1024)
            if not data:
                break
            digest.update(data)
            handle.write(data)
    if digest.hexdigest() != _MMSEQS_SHA256:
        raise RuntimeError("The Windows MMseqs2 download failed SHA256 validation.")
    with ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename
            if (not name.startswith("mmseqs/") or ".." in name.split("/")
                    or chr(92) in name or ":" in name or stat.S_ISLNK(member.external_attr >> 16)):
                raise RuntimeError("The MMseqs2 archive contains an unexpected path.")
        bundle.extractall(stage)
    tools = stage / "mmseqs" / "bin"
    _run([str(tools / "busybox.exe"), "--install", str(tools)], stage, check_cancel)
    _run([str(tools / "mmseqs.exe"), "version"], stage, check_cancel)
    check_cancel()
    (stage / "mmseqs").rename(target)


def install_windows(missing, check_cancel):
    root = tools_root()
    root.mkdir(parents=True, exist_ok=True)
    _, python = native_paths()
    with tempfile.TemporaryDirectory(prefix="setup-", dir=root) as temporary:
        stage = Path(temporary)
        if "mmseqs" in missing:
            _install_mmseqs(root, stage, check_cancel)
        if "gemmi" in missing and not python.is_dir():
            destination = stage / "gemmi"
            _run([console_python(), "-m", "pip", "--isolated", "install", "--no-deps", "--only-binary=:all:",
                  "--index-url", "https://pypi.org/simple", "--target", str(destination), "gemmi==0.7.4"],
                 stage, check_cancel)
            check_cancel()
            destination.rename(python)
