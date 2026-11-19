"""Normal-profile PyMOL acceptance of the installed RFD4 menu, without inference.

Run PyMOL -r this_file.py with PYMOLPLUGINS_RFD4_ACCEPTANCE set to an output
directory and PYMOLPLUGINS_RFD4_CHECK_NAME to a unique launch name. This checks
the user's already configured, authenticated real API; it never submits a job.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

from pymol import cmd, plugins
from pymol.gui import get_qtwindow
from pymol.Qt import QtCore, QtGui, QtWidgets

OUTPUT = Path(os.environ["PYMOLPLUGINS_RFD4_ACCEPTANCE"])
NAME = os.environ["PYMOLPLUGINS_RFD4_CHECK_NAME"]
RESULT = {"status": "failed", "name": NAME}


def wait_for(predicate, timeout=55):
    deadline = time.monotonic() + timeout
    while not predicate():
        QtWidgets.QApplication.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("RFD4 GUI acceptance timed out")
        time.sleep(0.03)
    QtWidgets.QApplication.processEvents()


def check():
    try:
        import pymolplugins
        assert pymolplugins.__version__ == plugins.plugins["pymol_plugins"].get_version()
        assert "rfd4_pymol" not in plugins.plugins
        assert "rfd4_studio" not in cmd.keyword
        assert "pymolplugins.rfd4.panel" not in sys.modules
        window = get_qtwindow()
        actions = [a for a in window.menudict["PluginQt|PymolPlugins"].actions() if "RFD4" in a.text()]
        assert len(actions) == 1 and actions[0].text() == "RFD4 · Proteina"
        actions[0].trigger()
        from pymolplugins import rfd4
        from pymolplugins.rfd4 import integrations
        dock = rfd4._DOCK
        panel = dock.widget()
        wait_for(lambda: panel.api is not None and not panel._busy)
        assert panel.last_error is None, panel.last_error
        assert panel.backend.currentData() == "f102"
        assert panel.capabilities["backend_modes"]["f102"] == "native"
        assert {"candidate_rounds", "trajectories", "condition_assessment"} <= set(panel.capabilities["features"])
        assert dock.parent() is window and not dock.isFloating()
        assert panel.tabs.isEnabled() and panel.tabs.count() == 4
        window.resize(1440, 980)
        window.move(20, 20)
        index = panel.example_combo.findData("partial_diffusion")
        assert index >= 0
        panel.example_combo.setCurrentIndex(index)
        panel.import_example()
        wait_for(lambda: panel.current is not None and not panel._busy)
        assert panel.last_error is None, panel.last_error
        obj = panel.bridge.assert_current(panel.current)
        assert cmd.count_atoms("model " + obj) > 0 and panel.generate_button.isEnabled()
        snapshot = integrations.apply_style("minimal_light", obj)
        integrations.revert_style(snapshot)
        sequence = "".join(line for line in cmd.get_fastastr(obj).splitlines()
                           if not line.startswith(">"))[:5]
        selection, _ = integrations.select_sequence(obj, sequence)
        assert cmd.count_atoms(selection) > 0
        chains = cmd.get_chains(obj)
        assert len(chains) >= 2
        group, contacts = integrations.contacts("interchain_basic", obj,
                                               "chain " + chains[0], "chain " + chains[1])
        assert not isinstance(contacts, dict) or not contacts.get("errors"), contacts
        integrations.clear_contacts(group)
        panel.bridge.assert_current(panel.current)
        panel.open_tools()
        assert not any(button.text() == "依赖下载页"
                       for button in panel._tools.findChildren(QtWidgets.QPushButton))
        panel._tools.close()
        dock.hide()
        actions[0].trigger()
        assert rfd4._DOCK is dock and dock.isVisible()
        assert len([d for d in window.findChildren(QtWidgets.QDockWidget)
                    if d.objectName() == "RFD4ProteinaDock"]) == 1
        cmd.zoom("model " + obj, animate=0)
        cmd.refresh()
        package_root = Path(pymolplugins.__file__).resolve().parent
        for name, module in list(sys.modules.items()):
            if name.startswith("pymolplugins.rfd4"):
                Path(module.__file__).resolve().relative_to(package_root)
        RESULT.update({
            "status": "passed", "version": pymolplugins.__version__,
            "pymol": cmd.get_version()[0], "qt": QtCore.qVersion(),
            "python": sys.executable, "normal_user_startup": True,
            "menu": actions[0].text(), "menu_action_count": 1,
            "native_dock": True, "singleton_reopen": True,
            "legacy_plugin_absent": True, "lazy_panel_import": True,
            "automatic_authenticated_connection": True, "url": panel.api.base_url,
            "default_backend": panel.backend.currentData(), "atoms": cmd.count_atoms(obj),
            "features": panel.capabilities["features"], "new_inference_jobs": 0,
            "session": panel.current, "style_sequence_contacts": True,
            "package_root": str(package_root),
            "files": {str(p.relative_to(package_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (package_root / "rfd4").rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts},
        })
    except Exception:
        RESULT["error"] = traceback.format_exc()
    QtCore.QTimer.singleShot(1500, finish)


def finish():
    try:
        if RESULT["status"] == "passed":
            window = get_qtwindow()
            widget = window.pymolwidget
            grab = getattr(widget, "grabFrameBuffer", None) or widget.grabFramebuffer
            gl = grab()
            assert not gl.isNull()
            # Qt's Windows window grabs omit OpenGL. Preserve both native
            # buffers, and compose them only at the widget's actual geometry.
            gl.save(str(OUTPUT / (NAME + "_structure.png")))
            base = window.grab()
            painter = QtGui.QPainter(base)
            painter.drawImage(QtCore.QRect(widget.mapTo(window, QtCore.QPoint()), widget.size()), gl)
            painter.end()
            base.save(str(OUTPUT / (NAME + ".png")))
            RESULT["capture"] = "Qt widget and native OpenGL buffers at actual widget geometry"
    except Exception:
        RESULT.update(status="failed", error=traceback.format_exc())
    (OUTPUT / (NAME + ".json")).write_text(json.dumps(RESULT, indent=2) + "\n", encoding="utf-8")
    cmd.quit(0 if RESULT["status"] == "passed" else 1)


QtCore.QTimer.singleShot(1800, check)
