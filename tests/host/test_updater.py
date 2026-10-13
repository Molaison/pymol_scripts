"""Release and transactional installation contracts, without PyMOL or Qt."""

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

from pymolplugins.host import updater


def package_bytes(version="0.15.0", extra=None):
    output = io.BytesIO()
    files = {"__init__.py": "# Version: %s\n" % version,
             "pymolplugins/__init__.py": '__version__ = "%s"\n' % version,
             "pymolplugins/host/plugin.py": "# plugin\n",
             "pymolplugins/host/updater.py": "# updater\n",
             "plugins/pymol_plugins.py": "# bootstrap\n"}
    with zipfile.ZipFile(output, "w") as archive:
        for name, source in files.items():
            archive.writestr("pymol_plugins/" + name, source)
        for name, source in (extra or {}).items():
            archive.writestr(name, source)
    return output.getvalue()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        with zipfile.ZipFile(io.BytesIO(package_bytes("0.14.1"))) as archive:
            archive.extractall(self.parent)
        self.root = self.parent / "pymol_plugins"
        self.module = self.root / "pymolplugins/host/updater.py"
        self.registered = self.root / "__init__.py"
        self.release = dict(version="0.15.0", newer=True, name="PymolPlugins-0.15.0.zip",
                            archive_url=updater.DOWNLOAD_PREFIX + "v0.15.0/PymolPlugins-0.15.0.zip",
                            checksum_url=updater.DOWNLOAD_PREFIX + "v0.15.0/SHA256SUMS")
        self.archive = package_bytes()

    def responses(self, url, *args):
        if url == self.release["archive_url"]:
            return io.BytesIO(self.archive)
        if url == self.release["checksum_url"]:
            line = hashlib.sha256(self.archive).hexdigest() + "  ./" + self.release["name"] + "\n"
            return io.BytesIO(line.encode())
        raise AssertionError(url)

    def install(self):
        with patch.object(updater, "_open", side_effect=self.responses):
            return updater.install_release(self.release, self.module, self.registered)

    def test_install_preserves_settings_and_retains_previous_package(self):
        settings = self.parent / "user-settings.ini"
        settings.write_text("unchanged")
        result = self.install()
        self.assertTrue(result["restart_required"])
        self.assertEqual(updater._package_version(self.root / "pymolplugins/__init__.py"), "0.15.0")
        backup = Path(result["backup"])
        self.assertEqual(updater._package_version(backup / "pymolplugins/__init__.py"), "0.14.1")
        self.assertEqual(settings.read_text(), "unchanged")
        self.assertFalse(list(self.parent.glob(".pymol_plugins-update-*")))
        with self.assertRaisesRegex(ValueError, "already installed"):
            self.install()

    def test_failed_replacement_restores_previous_installation(self):
        replace = os.replace
        def fail_stage(source, target):
            if Path(source).parent.name.startswith(".pymol_plugins-update-"):
                raise OSError("injected replacement failure")
            return replace(source, target)
        with patch.object(updater.os, "replace", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "injected replacement"):
                self.install()
        self.assertEqual(updater._package_version(self.root / "pymolplugins/__init__.py"), "0.14.1")
        self.assertFalse((self.parent / ".pymol_plugins-backup").exists())

    def test_bad_checksum_never_changes_installed_files(self):
        def corrupt(url, *args):
            if url == self.release["checksum_url"]:
                return io.BytesIO(("0" * 64 + "  " + self.release["name"]).encode())
            return self.responses(url)
        with patch.object(updater, "_open", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "SHA256 verification"):
                updater.install_release(self.release, self.module, self.registered)
        self.assertEqual(updater._package_version(self.root / "pymolplugins/__init__.py"), "0.14.1")

    def test_wrong_version_and_unsafe_zip_paths_are_rejected_before_replacement(self):
        for bad in (package_bytes("0.16.0"),
                    package_bytes(extra={"pymol_plugins/../outside": "bad"}),
                    package_bytes(extra={"/outside": "bad"}),
                    package_bytes(extra={"pymol_plugins/C:/outside": "bad"}),
                    package_bytes(extra={"pymol_plugins/pymolplugins/__INIT__.py": "duplicate"})):
            with self.subTest():
                self.archive = bad
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(updater._package_version(self.root / "pymolplugins/__init__.py"), "0.14.1")
                self.assertFalse((self.parent / "outside").exists())

    def test_checkout_or_unregistered_package_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            updater.installation_root(self.module, "")
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "development checkouts"):
            updater.installation_root(self.module, self.registered)

    def test_release_identity_stable_version_and_asset_contract(self):
        payload = dict(tag_name="v0.15.0", draft=False, prerelease=False,
                       assets=[dict(name=self.release["name"], browser_download_url=self.release["archive_url"]),
                               dict(name="SHA256SUMS", browser_download_url=self.release["checksum_url"])])
        def fetch(*args):
            return io.BytesIO(json.dumps(payload).encode())
        with patch.object(updater, "_open", side_effect=fetch):
            self.assertTrue(updater.check_release("0.14.1")["newer"])
            self.assertFalse(updater.check_release("0.15.0")["newer"])
            payload["prerelease"] = True
            with self.assertRaisesRegex(ValueError, "stable"):
                updater.check_release("0.14.1")
            payload["prerelease"] = False
            payload["assets"][0]["browser_download_url"] = "https://example.org/update.zip"
            with self.assertRaisesRegex(ValueError, "does not belong"):
                updater.check_release("0.14.1")

    def test_download_redirects_stay_on_github_https(self):
        handler = updater.ReleaseRedirects()
        request = urllib.request.Request(self.release["archive_url"])
        redirected = handler.redirect_request(request, None, 302, "Found", {},
                                               "https://release-assets.githubusercontent.com/release.zip")
        self.assertEqual(redirected.host, "release-assets.githubusercontent.com")
        for url in ("http://github.com/file", "https://example.org/file"):
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()
