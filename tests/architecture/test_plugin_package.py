from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from pymolplugins import __version__


ROOT = Path(__file__).resolve().parents[2]


class PluginPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="pymolplugins-install-test-")
        cls.addClassCleanup(temporary.cleanup)
        cls.workspace = Path(temporary.name)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/package_plugin.py"),
             "--output-dir", str(cls.workspace / "dist")],
            cwd=cls.workspace, text=True, capture_output=True, check=True,
        )
        cls.archive = cls.workspace / "dist" / f"PymolPlugins-{__version__}.zip"

    def test_single_plugin_package_contains_complete_runtime(self):
        with ZipFile(self.archive) as archive:
            names = set(archive.namelist())
            self.assertIsNone(archive.testzip())
            self.assertEqual({name.split("/")[0] for name in names}, {"pymol_plugins"})
            bootstrap = archive.read("pymol_plugins/__init__.py").decode("utf-8")
            self.assertTrue(bootstrap.startswith(f"# Version: {__version__}\n"))
            self.assertIn("pymol_plugins/INSTALL.md", names)
            for path in (ROOT / "pymolplugins").rglob("*.py"):
                name = "pymol_plugins/" + path.relative_to(ROOT).as_posix()
                with self.subTest(source=name):
                    self.assertEqual(archive.read(name), path.read_bytes())
            for path in (ROOT / "plugins").glob("*.py"):
                name = "pymol_plugins/" + path.relative_to(ROOT).as_posix()
                self.assertEqual(archive.read(name), path.read_bytes())
            self.assertFalse(any(
                "__pycache__" in name.split("/") or name.endswith((".pyc", ".pyo"))
                for name in names
            ))

    def test_installed_bundle_loads_commands_and_menus_without_repository(self):
        installed = self.workspace / "installed"
        with ZipFile(self.archive) as archive:
            archive.extractall(installed)
        source = r'''
import importlib
from pathlib import Path
import sys
import types

installed = Path(sys.argv[1]).resolve()
repository = Path(sys.argv[2]).resolve()
assert repository not in [Path(entry).resolve() for entry in sys.path]
sys.path.insert(0, str(installed))

class FakeCmd:
    def __init__(self):
        self.keyword = {}

    def extend(self, name, function):
        self.keyword[name] = [function, 0, 0, ",", 11]

pymol = types.ModuleType("pymol")
pymol.__path__ = []
pymol.cmd = FakeCmd()
qt = types.ModuleType("pymol.Qt")
qt.QtCore = None
pymol.Qt = qt
plugins = types.ModuleType("pymol.plugins")
menus = {}
plugins.addmenuitemqt = lambda label, callback: menus.setdefault(label, callback)
sys.modules.update({"pymol": pymol, "pymol.Qt": qt, "pymol.plugins": plugins})

bundle = importlib.import_module("pymol_plugins")
bundle.__init_plugin__()
assert set(menus) == {
    "PymolPlugins: Load Toolkit", "PymolPlugins: Doctor",
    "PymolPlugins: Capabilities", "PymolPlugins: Command Help",
    "PymolPlugins: Structure Annotator",
    "PymolPlugins: Batch Processing",
}, menus
assert all(callable(callback) for callback in menus.values())
loaded = menus["PymolPlugins: Load Toolkit"]()
assert loaded["status"] == "ok", loaded
from pymolplugins.host.manifest import CANONICAL_MODULES, declared_command_names
assert set(loaded["loaded"]) == set(CANONICAL_MODULES), loaded
assert set(declared_command_names()).issubset(pymol.cmd.keyword)
for name, module in list(sys.modules.items()):
    if name == "pymolplugins" or name.startswith("pymolplugins."):
        Path(module.__file__).resolve().relative_to(installed)
assert repository not in [Path(entry).resolve() for entry in sys.path]
'''
        completed = subprocess.run(
            [sys.executable, "-I", "-c", source, str(installed), str(ROOT)],
            cwd=self.workspace, text=True, capture_output=True, check=False,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
