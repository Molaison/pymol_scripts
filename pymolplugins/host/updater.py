"""Public GitHub release updates for Plugin Manager installations only."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


REPOSITORY = "xcflab/pymol_scripts"
RELEASE_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
DOWNLOAD_PREFIX = "https://github.com/" + REPOSITORY + "/releases/download/"
REQUIRED = {"__init__.py", "pymolplugins/__init__.py", "plugins/pymol_plugins.py",
            "pymolplugins/host/plugin.py"}


def version_tuple(version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(version)):
        raise ValueError("Expected a stable major.minor.patch version")
    return tuple(int(part) for part in version.split("."))


class ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != "https" or url.hostname not in {
            "github.com", "api.github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        }:
            raise ValueError("Release download redirected outside GitHub HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, accept="application/octet-stream"):
    request = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": "PymolPlugins-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        return urllib.request.build_opener(ReleaseRedirects()).open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("No public stable release is available yet") from None
        if exc.code == 403:
            raise RuntimeError("GitHub refused this request or its API rate limit was reached; try later") from None
        raise RuntimeError("GitHub update request failed (HTTP %s)" % exc.code) from None


def check_release(current_version):
    with _open(RELEASE_URL, "application/vnd.github+json") as response:
        payload = json.load(response)
    if payload.get("draft") or payload.get("prerelease"):
        raise ValueError("The update is not a published stable release")
    tag = str(payload.get("tag_name", ""))
    version = tag[1:] if tag.startswith("v") else tag
    if tag != "v" + version:
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    newer = version_tuple(version) > version_tuple(current_version)
    name = "PymolPlugins-%s.zip" % version
    assets = {asset["name"]: asset for asset in payload.get("assets", [])}
    result = {"version": version, "newer": newer, "name": name}
    for key, asset_name in (("archive_url", name), ("checksum_url", "SHA256SUMS")):
        if asset_name not in assets:
            raise ValueError("Release is missing " + asset_name)
        url = str(assets[asset_name].get("browser_download_url", ""))
        if url != DOWNLOAD_PREFIX + tag + "/" + asset_name:
            raise ValueError("Release asset does not belong to " + REPOSITORY)
        result[key] = url
    return result


def installation_root(module_file, registered_init):
    """Match the running code to PyMOL's registered PluginInfo.filename."""
    root = Path(module_file).absolute().parents[2]
    if root.name != "pymol_plugins" or root.is_symlink() or (root / ".git").exists():
        raise ValueError("Updates require a Plugin Manager installation; development checkouts are not replaced")
    if not registered_init or Path(registered_init).resolve() != (root / "__init__.py").resolve():
        raise ValueError("This copy is not registered in PyMOL Plugin Manager")
    if not all((root / name).is_file() for name in REQUIRED):
        raise ValueError("The installed plugin package is incomplete")
    return root.resolve()


def _package_version(path):
    source = Path(path).read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*[\'"]([0-9]+\.[0-9]+\.[0-9]+)[\'"]', source, re.MULTILINE)
    if not match:
        raise ValueError("Package has no stable version")
    return match[1]


def _extract_verified(archive, checksum_text, expected_version, staging):
    name = "PymolPlugins-%s.zip" % expected_version
    checksums = []
    for line in checksum_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*").removeprefix("./") == name:
            checksums.append(parts[0].lower())
    if len(checksums) != 1 or not re.fullmatch(r"[0-9a-f]{64}", checksums[0]):
        raise ValueError("SHA256SUMS must contain exactly one checksum for " + name)
    digest = hashlib.sha256()
    with open(archive, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != checksums[0]:
        raise ValueError("Downloaded ZIP failed SHA256 verification")
    with zipfile.ZipFile(archive) as package:
        names = set()
        files = set()
        for item in package.infolist():
            path = PurePosixPath(item.filename)
            if (not path.parts or path.parts[0] != "pymol_plugins"
                    or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or path.is_absolute() or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("ZIP contains an unsafe package path")
            normalized = str(path).casefold()
            if normalized in names:
                raise ValueError("ZIP contains duplicate package paths")
            names.add(normalized)
            if not item.is_dir():
                files.add(str(path.relative_to("pymol_plugins")))
        if not REQUIRED.issubset(files):
            raise ValueError("ZIP is not a complete PymolPlugins installation")
        package.extractall(staging)
    root = Path(staging) / "pymol_plugins"
    if _package_version(root / "pymolplugins/__init__.py") != expected_version:
        raise ValueError("ZIP version does not match its release")
    if not (root / "__init__.py").read_text(encoding="utf-8").startswith("# Version: " + expected_version + "\n"):
        raise ValueError("ZIP bootstrap version does not match its release")
    return root


def install_release(release, module_file, registered_init):
    """Download, validate and replace one installed plugin, rolling back on failure."""
    root = installation_root(module_file, registered_init)
    version = release["version"]
    if version_tuple(version) <= version_tuple(_package_version(root / "pymolplugins/__init__.py")):
        raise ValueError("This version is already installed; restart PyMOL if an update just finished")
    # Both stage and backup share the installation volume so renames stay local.
    backup = root.with_name(".pymol_plugins-backup")
    with tempfile.TemporaryDirectory(prefix=".pymol_plugins-update-", dir=root.parent) as temporary:
        staging = Path(temporary)
        archive = staging / release["name"]
        with _open(release["checksum_url"]) as response:
            checksum = response.read().decode("utf-8")
        with _open(release["archive_url"]) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        prepared = _extract_verified(archive, checksum, version, staging)
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(root, backup)
        try:
            os.replace(prepared, root)
        except OSError:
            os.replace(backup, root)
            raise
    return {"version": version, "backup": str(backup), "restart_required": True}
