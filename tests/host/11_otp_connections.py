"""Real Qt Connections settings and Windows DPAPI acceptance, no remote login."""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
APPLICATION = None


def run(output):
    global APPLICATION
    from pymol.Qt import QtCore, QtWidgets
    from PyQt5.QtTest import QTest
    from pymolplugins.remote import otp
    from pymolplugins.annotation.ssh_widgets import SourceLocationControls
    APPLICATION = app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory(prefix="pymol-otp-ui-") as temporary:
        root = Path(temporary)
        store = root / "otp.json"
        os.environ["PYMOLPLUGINS_SSH_OTP_STORE"] = str(store)
        config = root / "config"
        config.write_text("Host f101-fixture\n HostName example.invalid\nHost other-host\n HostName example.invalid\n")
        controls = SourceLocationControls(None)
        controls.config.setText(str(config))
        controls._reload_profiles()
        controls.connection.setCurrentText("f101-fixture")
        controls.show()
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # Public RFC fixture only.
        errors = []

        def dialog_action(action):
            def invoke():
                try:
                    action()
                except Exception:
                    errors.append(traceback.format_exc())
                    controls.settings_dialog.reject()
            QtCore.QTimer.singleShot(60, invoke)
            controls._configure()
            assert not errors, errors

        def save():
            assert controls.otp_host.currentText() == "f101-fixture"
            assert not controls.otp_enabled.isChecked()
            QTest.mouseClick(controls.otp_enabled, QtCore.Qt.LeftButton,
                             pos=QtCore.QPoint(8, controls.otp_enabled.height() // 2))
            assert controls.otp_secret.isEnabled()
            assert controls.otp_secret.echoMode() == QtWidgets.QLineEdit.Password
            controls.otp_secret.setText(secret)
            controls.otp_remember.setChecked(True)
            app.processEvents()
            controls.settings_dialog.grab().save(str(output.with_suffix(".png")))
            controls._accept_settings()
            assert controls.settings_dialog.result() == QtWidgets.QDialog.Accepted

        dialog_action(save)
        assert controls.otp_secret.text() == ""
        assert secret not in store.read_text()
        assert secret not in controls.source("~/review/sum_scores.csv")
        otp._session_settings.clear()  # Reopen from actual DPAPI persistence.
        restored = otp.get_otp_settings("f101-fixture", str(config))
        assert restored["secret"] == secret and restored["remember"]

        def cancel():
            assert controls.otp_enabled.isChecked()
            assert controls.otp_secret.text() == secret
            controls.otp_secret.setText("JBSWY3DPEHPK3PXP")
            controls.otp_remember.setChecked(False)
            controls.settings_dialog.reject()

        dialog_action(cancel)
        assert otp.get_otp_settings("f101-fixture", str(config))["secret"] == secret

        def invalid_then_disable():
            controls.otp_secret.setText("123456")
            controls._accept_settings()
            assert controls.dialog_error.isVisible()
            assert otp.get_otp_settings("f101-fixture", str(config))["secret"] == secret
            controls.otp_prompt.setText("[")
            controls.otp_enabled.setChecked(False)
            controls._accept_settings()

        dialog_action(invalid_then_disable)
        assert not otp.get_otp_settings("f101-fixture", str(config))["enabled"]
        assert json.loads(store.read_text()) == {}

        def other_host():
            controls.otp_host.setCurrentText("other-host")
            controls.otp_enabled.setChecked(True)
            controls.otp_secret.setText(secret)
            controls.otp_remember.setChecked(False)
            controls._accept_settings()

        dialog_action(other_host)
        assert controls.connection.currentText() == "other-host"
        assert otp.get_otp_settings("other-host", str(config))["secret"] == secret
        assert json.loads(store.read_text()) == {}, "Session-only key was persisted"
        controls.close()
        return {"status": "passed", "masked_input": True, "dpapi_roundtrip": True,
                "cancel_keeps_saved_key": True, "invalid_key_preserves_settings": True,
                "disable_removes_saved_key": True, "session_only_not_persisted": True,
                "per_host_settings": True, "screenshot": str(output.with_suffix(".png"))}


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/11_otp_connections.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(output)
    except Exception:
        result = {"status": "failed", "traceback": traceback.format_exc()}
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), file=sys.__stdout__, flush=True)
    os._exit(0 if result["status"] == "passed" else 1)
