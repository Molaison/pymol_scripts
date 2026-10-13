"""Install an older real ZIP, update from public GitHub, and reopen in Qt.

python tests/host/03_release_update_linux.py OLD_ZIP --output RESULT.json
Uses a temporary Plugin Manager installation; never touches the user's plugin.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
from annotator_workflow_smoke import wait_for
from workspace_helpers import isolate_workspace_settings


def qt_setup(directory):
    global _APP, _WINDOW, _MENU
    from pymol import plugins
    from pymol.Qt import QtWidgets
    from pmg_qt.mimic_pmg_tk import PmwMenuBar
    _APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    _WINDOW = QtWidgets.QMainWindow()
    _MENU = QtWidgets.QMenu("Plugin", _WINDOW)
    gui = SimpleNamespace(menuBar=PmwMenuBar({"PluginQt": _MENU}))
    isolate_workspace_settings(directory, _WINDOW, mode=None)
    plugins.preferences["instantsave"] = False
    plugins.set_startup_path([str(directory)], autosave=False)
    return plugins, gui


def reopen(directory):
    plugins, gui = qt_setup(directory)
    root = directory / "pymol_plugins"
    info = plugins.PluginInfo("pymol_plugins", str(root / "__init__.py"),
                              plugins.startup.__name__ + ".pymol_plugins")
    plugins.plugins["pymol_plugins"] = info
    with patch.object(plugins, "get_pmgapp", return_value=gui), \
            patch.object(plugins, "HAVE_QT", True):
        assert info.load(gui)
        from pymolplugins.host.updater_widgets import controller
        from pymolplugins import __version__
        control = controller()
        assert control.option("auto_check", True)
        assert not control.option("auto_install", False)
        wait_for(_APP, lambda: control.release is not None and not control.busy, timeout=60)
        assert not control.release["newer"], control.status
        assert __version__ == control.release["version"]
        next(action for action in _MENU.actions() if action.text() == "PymolPlugins: Updates").trigger()
        assert control.dialog.isVisible()
        from pymol.Qt import QtWidgets
        toggle = next(widget for widget in control.dialog.findChildren(QtWidgets.QCheckBox)
                      if widget.text().startswith("Automatically install"))
        toggle.setChecked(True)
        assert control.option("auto_install", False)
        control.dialog.close()
        print("REOPEN_RESULT=" + json.dumps({"version": control.release["version"],
              "startup_check": control.status, "updates_menu": True,
              "automatic_install_option_persisted": True}), flush=True)


def run(args):
    with tempfile.TemporaryDirectory(prefix="pymol-release-update-") as temporary:
        directory = Path(temporary)
        plugins, gui = qt_setup(directory)
        from pymol.plugins import legacysupport
        from pymol.plugins.installation import installPluginFromFile
        def unexpected_prompt(*args, **kwargs):
            raise AssertionError("Unexpected installer prompt: %r" % (args,))
        messages = SimpleNamespace(showinfo=lambda *a, **k: None,
                                   askyesno=unexpected_prompt, askokcancel=unexpected_prompt)
        with patch.object(plugins, "get_pmgapp", return_value=gui), \
                patch.object(plugins, "HAVE_QT", True), \
                patch.object(legacysupport, "tkMessageBox", messages):
            installPluginFromFile(str(args.old_zip.resolve()), parent=_WINDOW, plugdir=temporary)
        info = plugins.plugins["pymol_plugins"]
        assert info.loaded
        import pymolplugins
        old_version = pymolplugins.__version__
        spec = importlib.util.spec_from_file_location("new_release_updater", ROOT / "pymolplugins/host/updater.py")
        updater = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(updater)
        release = updater.check_release(old_version)
        assert release["newer"], release
        settings = Path(os.environ["PYMOLPLUGINS_UI_SETTINGS"])
        before = settings.read_bytes() if settings.exists() else None
        root = directory / "pymol_plugins"
        updated = updater.install_release(release, root / "pymolplugins/host/plugin.py", info.filename)
        assert (settings.read_bytes() if settings.exists() else None) == before
        assert pymolplugins.__version__ == old_version  # No hot reload.
        assert updater._package_version(Path(updated["backup"]) / "pymolplugins/__init__.py") == old_version
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                "--reopen", str(directory)], capture_output=True, text=True, timeout=90)
        assert child.returncode == 0, child.stdout + child.stderr
        report = json.loads(next(line.split("=", 1)[1] for line in child.stdout.splitlines()
                                 if line.startswith("REOPEN_RESULT=")))
        assert report["version"] == release["version"]
        return {"status": "passed", "platform": sys.platform, "old_version": old_version,
                "release": release, "installed": updated, "settings_unchanged": True,
                "running_version_unchanged": True, "fresh_process": report,
                "scope": "Real public GitHub download, Plugin Manager registration and Linux Qt; no Windows check"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_zip", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reopen", type=Path)
    args = parser.parse_args()
    if args.reopen:
        reopen(args.reopen)
    else:
        import traceback
        try:
            result = run(args)
        except Exception:
            result = {"status": "failed", "error": traceback.format_exc()}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        sys.exit(0 if result["status"] == "passed" else 1)
