"""OTP settings, one-shot authentication, and the bundled native askpass bridge."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pymolplugins.remote import otp, ssh, ssh_worker


SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 public test key


class OtpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = self.root / "settings.json"
        environment = patch.dict(os.environ, {"PYMOLPLUGINS_SSH_OTP_STORE": str(self.store)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(ssh.close_ssh_sessions)
        otp._last_slots.clear()

    def test_rfc6238_sha1_vectors_and_invalid_secret_redaction(self):
        for timestamp, expected in [(59, "94287082"), (1111111109, "07081804"),
                                    (1111111111, "14050471"), (1234567890, "89005924"),
                                    (2000000000, "69279037"), (20000000000, "65353130")]:
            self.assertEqual(otp.totp(SEED, timestamp, digits=8), expected)
            self.assertEqual(otp.totp(SEED, timestamp), expected[-6:])
        self.assertEqual(otp.validate_otp_secret("mzxw 6===\n"), "MZXW6")
        for invalid in ("", "not-a-secret-PRIVATE", "M", "MZ", "123!PRIVATE"):
            with self.assertRaises(ValueError) as caught:
                otp.validate_otp_secret(invalid)
            if invalid:
                self.assertNotIn(invalid, str(caught.exception))

    def test_settings_are_session_only_and_profile_config_scoped(self):
        configured = otp.configure_otp("test", SEED, config=str(self.root / "config"))
        self.assertFalse(configured["remember"])
        self.assertTrue(configured["enabled"])
        self.assertFalse(self.store.exists())
        self.assertEqual(otp.get_otp_settings("test", str(self.root / "config")), configured)
        self.assertFalse(otp.get_otp_settings("other", str(self.root / "config"))["enabled"])
        self.assertFalse(otp.get_otp_settings("test", str(self.root / "other"))["enabled"])
        otp.configure_otp("test", config=str(self.root / "config"))
        self.assertFalse(otp.get_otp_settings("test", str(self.root / "config"))["enabled"])

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_remember_encrypts_and_forgetting_removes_persisted_secret(self):
        otp.configure_otp("test", SEED, remember=True)
        self.assertNotIn(SEED, self.store.read_text())
        otp._session_settings.pop((str(self.store), otp._settings_key("test", "")))
        settings = otp.get_otp_settings("test")
        self.assertEqual(settings["secret"], SEED)
        self.assertTrue(settings["remember"])
        otp.configure_otp("test", SEED, remember=False)
        self.assertEqual(json.loads(self.store.read_text()), {})

    def test_no_plaintext_remember_on_non_windows(self):
        with patch.object(otp, "_store_path", return_value=self.store), \
                patch.object(otp.os, "name", "posix"), self.assertRaisesRegex(OSError, "Windows DPAPI"):
            otp.configure_otp("test", SEED, remember=True)
        self.assertFalse(self.store.exists())

    def test_prompt_allowlist_and_one_answer_only(self):
        broker = otp.OtpBroker(SEED)
        self.addCleanup(broker.close)
        with patch.object(otp, "_next_code", return_value="123456") as next_code:
            for prompt in ("Password:", "Enter passphrase for key:", "OTP unavailable. Password:",
                           "Continue connecting (yes/no/fingerprint)?", "What is your name?"):
                self.assertEqual(broker._answer(dict(token=broker.token, prompt=prompt)), "")
            self.assertEqual(broker._answer(dict(token="wrong", prompt="OTP:")), "")
            next_code.assert_not_called()
            self.assertEqual(broker._answer(dict(token=broker.token, prompt="Verification code:")), "123456")
            self.assertEqual(broker._answer(dict(token=broker.token, prompt="OTP:")), "")
            next_code.assert_called_once()

    def test_custom_prompt_and_explicit_one_time_password(self):
        for pattern, prompt in [("^F101 token:$", "F101 token:"),
                                (otp.DEFAULT_OTP_PROMPT, "One-time password:"),
                                (otp.DEFAULT_OTP_PROMPT, "OTP password:"),
                                (otp.DEFAULT_OTP_PROMPT, "动态密码:")]:
            with self.subTest(prompt=prompt):
                broker = otp.OtpBroker(SEED, pattern)
                try:
                    with patch.object(otp, "_next_code", return_value="123456"):
                        self.assertEqual(broker._answer(dict(token=broker.token, prompt=prompt)), "123456")
                finally:
                    broker.close()
        broker = otp.OtpBroker(SEED, ".*")
        self.addCleanup(broker.close)
        with patch.object(otp, "_next_code") as next_code:
            for prompt in ("Password:", "Enter passphrase for key:", "Continue (yes/no)?"):
                self.assertEqual(broker._answer(dict(token=broker.token, prompt=prompt)), "")
            next_code.assert_not_called()

    def test_failed_authentication_sends_once_and_closes_broker(self):
        fake_ssh = self.root / "rejected_auth.py"
        fake_ssh.write_text(
            "import os, subprocess, sys\n"
            "command = [os.environ['PYMOLPLUGINS_OTP_PYTHON'], os.environ['PYMOLPLUGINS_OTP_HELPER'], 'OTP:']\n"
            "first = subprocess.run(command, stdout=subprocess.PIPE)\n"
            "second = subprocess.run(command, stdout=subprocess.PIPE)\n"
            "assert first.returncode == 0 and second.returncode == 1\n"
            "sys.stderr.write('Authentication rejected')\n"
            "sys.exit(1)\n")
        client = ssh.SshClient(ssh.SshLocation("test", "/"), otp_secret=SEED)
        with patch.object(ssh.SshClient, "command", return_value=[sys.executable, str(fake_ssh)]), \
                patch.object(otp, "_next_code", return_value="123456") as next_code:
            with self.assertRaisesRegex(ssh.SshError, "Authentication rejected"):
                client.stat()
            next_code.assert_called_once()
        session = next(iter(ssh._sessions.values()))
        self.assertTrue(session.closed)
        self.assertIsNone(session.otp_broker)
        self.assertIsNotNone(session.process.poll())

    def test_same_seed_concurrency_waits_for_new_slot_and_cancels(self):
        cancelled = threading.Event()
        clock = [60.0]
        with patch.object(otp.time, "time", side_effect=lambda: clock[0]), ThreadPoolExecutor(2) as pool:
            first = pool.submit(otp._next_code, SEED, cancelled)
            self.assertEqual(first.result(timeout=2), otp.totp(SEED, 60))
            second = pool.submit(otp._next_code, SEED, cancelled)
            time.sleep(0.25)
            self.assertFalse(second.done())
            clock[0] = 90.0
            self.assertEqual(second.result(timeout=2), otp.totp(SEED, 90))
            third = pool.submit(otp._next_code, SEED, cancelled)
            cancelled.set()
            self.assertEqual(third.result(timeout=2), "")

    def test_near_expiry_waits_without_consuming_slot_and_cancel_is_final(self):
        broker = otp.OtpBroker(SEED)
        self.addCleanup(broker.close)
        with patch.object(otp.time, "time", return_value=89.0), ThreadPoolExecutor(1) as pool:
            answer = pool.submit(broker._answer, dict(token=broker.token, prompt="OTP:"))
            time.sleep(0.25)
            self.assertFalse(answer.done())
            self.assertFalse(otp._last_slots)
            broker.close()
            self.assertEqual(answer.result(timeout=2), "")
            self.assertTrue(broker.used)

    def test_builtin_selects_system_ssh_and_explicit_wrappers_remain_opt_in(self):
        wrapper = self.root / "login.py"
        wrapper.write_text("pass\n")
        with patch.dict(os.environ, {"PYMOLPLUGINS_SSH_PROGRAM": str(wrapper)}):
            self.assertEqual(ssh.default_ssh_program(), str(wrapper))
            self.assertEqual(ssh.default_ssh_program(builtin_otp=True), shutil.which("ssh"))
            client = ssh.SshClient(ssh.SshLocation("test", "/"), otp_secret=SEED)
            self.assertEqual(client.executable, shutil.which("ssh"))
            self.assertIn("BatchMode=no", client.command())
            self.assertIn("NumberOfPasswordPrompts=1", client.command())
            self.assertFalse(any(SEED in arg for arg in client.command()))
            explicit = ssh.SshLocation("test", "/", program=str(wrapper))
            with self.assertRaisesRegex(ssh.SshError, "native OpenSSH"):
                ssh.SshClient(explicit, otp_secret=SEED)
            self.assertEqual(ssh.SshClient(explicit, otp_secret="").executable, str(wrapper))

    def test_saved_settings_none_and_explicit_disable(self):
        otp.configure_otp("test", SEED)
        location = ssh.SshLocation("test", "/")
        self.assertEqual(ssh.SshClient(location).otp_secret, SEED)
        self.assertEqual(ssh.SshClient(location, otp_secret="").otp_secret, "")

    def test_session_cache_separates_secret_prompt_and_disabled_auth(self):
        location = ssh.SshLocation("test", str(self.root))
        with patch.object(ssh.SshClient, "command", return_value=[sys.executable, ssh_worker.__file__]):
            for secret, prompt in [(SEED, "OTP"), ("JBSWY3DPEHPK3PXP", "OTP"), (SEED, "token"), ("", "OTP")]:
                client = ssh.SshClient(location, executable=sys.executable, otp_secret=secret, otp_prompt=prompt)
                self.assertEqual(client.stat(str(self.root))["status"], "available")
            self.assertEqual(len(ssh._sessions), 4)
            self.assertNotIn(SEED, repr(list(ssh._sessions)))
            self.assertTrue(all(session.otp_broker is None for session in ssh._sessions.values()))

    @unittest.skipUnless(os.name == "nt", "bundled Windows launcher")
    def test_native_launcher_unicode_quotes_and_shell_metacharacters(self):
        helper = self.root / "helper with spaces.py"
        helper.write_text("import json, sys\nprint(json.dumps(sys.argv[1:], ensure_ascii=True))\n")
        launcher = Path(otp.__file__).with_name("askpass") / "ssh_askpass.exe"
        prompt = '验证码 "quoted" \\ tail\\ & echo PWNED > forbidden | %PATH% $(whoami)'
        environment = dict(os.environ, PYMOLPLUGINS_OTP_PYTHON=sys.executable,
                           PYMOLPLUGINS_OTP_HELPER=str(helper))
        result = subprocess.run([str(launcher), prompt], env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [prompt])
        self.assertFalse((self.root / "forbidden").exists())

    @unittest.skipUnless(os.name == "nt", "native Windows OpenSSH canary")
    def test_native_openssh_calls_launcher_and_broker(self):
        keygen = shutil.which("ssh-keygen")
        key = self.root / "synthetic key"
        flags = dict(creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "synthetic-canary-only", "-f", str(key)], check=True, **flags)
        broker = otp.OtpBroker(SEED)
        self.addCleanup(broker.close)
        # Private-key passphrases must be refused even if the launcher works.
        result = subprocess.run([keygen, "-y", "-f", str(key)], env=broker.environment(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-OTP", broker.error)
        self.assertFalse(broker.used)
        # An independent synthetic helper verifies inherited stdout end to end.
        helper = self.root / "synthetic helper.py"
        helper.write_text("print('synthetic-canary-only')\n")
        environment = broker.environment()
        environment["PYMOLPLUGINS_OTP_HELPER"] = str(helper)
        result = subprocess.run([keygen, "-y", "-f", str(key)], env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(b"ssh-ed25519 "))


if __name__ == "__main__":
    unittest.main()
