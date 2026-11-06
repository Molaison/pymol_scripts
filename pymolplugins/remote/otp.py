"""Session-only or DPAPI-protected TOTP settings and OpenSSH askpass broker."""

import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import struct
import sys
import tempfile
import threading
import time


DEFAULT_OTP_PROMPT = r"(?i)(verification\s*code|one[ -]?time|\botp\b|passcode|验证码|动态口令|动态密码|一次性密码)"
_NON_OTP_PROMPT = re.compile(r"(?i)(passphrase|private\s*key|password|authenticity|fingerprint|yes/no|密码)")
_settings_lock = threading.RLock()
_session_settings = {}
_slot_lock = threading.Lock()
_last_slots = {}


def validate_otp_secret(secret):
    """Return canonical Base32; invalid input is never included in errors."""
    value = re.sub(r"\s+", "", str(secret)).upper().rstrip("=")
    try:
        if not value or not re.fullmatch(r"[A-Z2-7]+", value):
            raise ValueError
        decoded = base64.b32decode(value + "=" * (-len(value) % 8))
        if not decoded or base64.b32encode(decoded).decode("ascii").rstrip("=") != value:
            raise ValueError
    except (ValueError, binascii.Error):
        raise ValueError("OTP secret must be a valid Base32 key.") from None
    return value


def _prompt_pattern(prompt):
    value = str(prompt or DEFAULT_OTP_PROMPT)
    try:
        re.compile(value)
    except re.error:
        raise ValueError("OTP prompt must be a valid regular expression.") from None
    return value


def _store_path():
    value = os.environ.get("PYMOLPLUGINS_SSH_OTP_STORE", "")
    return Path(value).expanduser().absolute() if value else Path.home() / ".pymolplugins" / "ssh_otp.json"


def _settings_key(profile, config):
    filename = os.path.normcase(os.path.abspath(os.path.expanduser(config or "~/.ssh/config")))
    return json.dumps([str(profile), filename], ensure_ascii=True)


def _read_store(path):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError):
        raise ValueError("SSH OTP settings file is invalid.") from None


def _write_store(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".ssh-otp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_otp_settings(profile, config=""):
    """Return secret, remember, prompt and enabled for one SSH profile/config."""
    path, key = _store_path(), _settings_key(profile, config)
    with _settings_lock:
        identity = (str(path), key)
        if identity in _session_settings:
            return dict(_session_settings[identity])
        entry = _read_store(path).get(key)
        if entry is None:
            return dict(secret="", remember=False, prompt=DEFAULT_OTP_PROMPT, enabled=False)
        from ..annotation.ai_provider import unprotect_api_key
        try:
            secret = validate_otp_secret(unprotect_api_key(entry["protected_secret"]))
            prompt = _prompt_pattern(entry.get("prompt"))
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("Cannot decrypt SSH OTP settings for the current Windows user.") from None
        return dict(secret=secret, remember=True, prompt=prompt, enabled=True)


def configure_otp(profile, secret="", config="", remember=False, prompt=DEFAULT_OTP_PROMPT):
    """Set session settings; remember explicitly opts into Windows DPAPI storage."""
    secret = validate_otp_secret(secret) if str(secret).strip() else ""
    prompt = _prompt_pattern(prompt)
    settings = dict(secret=secret, remember=bool(remember and secret), prompt=prompt, enabled=bool(secret))
    path, key = _store_path(), _settings_key(profile, config)
    with _settings_lock:
        data = _read_store(path)
        if settings["remember"]:
            if os.name != "nt":
                raise OSError("Remembering SSH OTP is supported only with Windows DPAPI; use session-only OTP here.")
            from ..annotation.ai_provider import protect_api_key
            data[key] = dict(protected_secret=protect_api_key(secret), prompt=prompt)
            _write_store(path, data)
        elif key in data:
            del data[key]
            _write_store(path, data)
        _session_settings[(str(path), key)] = settings
    return dict(settings)


def totp(secret, timestamp=None, digits=6):
    """Generate standard SHA1 TOTP (30-second interval); digits aids RFC tests."""
    secret = validate_otp_secret(secret)
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int(time.time() if timestamp is None else timestamp) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(number % (10 ** digits)).zfill(digits)


def otp_fingerprint(secret, prompt):
    return hashlib.sha256((secret + "\0" + prompt).encode("utf-8")).hexdigest() if secret else ""


def _next_code(secret, cancelled):
    identity = hashlib.sha256(secret.encode("ascii")).digest()
    while not cancelled.is_set():
        now = time.time()
        slot = int(now) // 30
        with _slot_lock:
            previous = _last_slots.get(identity, -1)
            if slot > previous and (slot + 1) * 30 - now >= 5:
                _last_slots[identity] = slot
                return totp(secret, now)
        if cancelled.wait(0.2):
            break
    return ""


def console_python():
    executable = sys.executable
    if os.path.basename(executable).casefold() == "pythonw.exe":
        executable = str(Path(executable).with_name("python.exe"))
    if not os.path.isfile(executable):
        raise OSError("SSH OTP requires the matching console Python interpreter.")
    return executable


class OtpBroker:
    """One SSH connection, one permitted OTP answer, no credentials in argv."""

    def __init__(self, secret, prompt=DEFAULT_OTP_PROMPT):
        self.secret = validate_otp_secret(secret)
        self.pattern = re.compile(_prompt_pattern(prompt))
        self.token = secrets.token_urlsafe(32)
        self.cancelled = threading.Event()
        self.used = False
        self.code = ""
        self.error = ""
        self.temporary = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.2)
        self.thread = threading.Thread(target=self._serve, name="ssh-otp", daemon=True)
        self.thread.start()

    def environment(self):
        environment = os.environ.copy()
        helper = Path(__file__).with_name("otp_askpass.py")
        if os.name == "nt":
            launcher = helper.with_name("askpass") / "ssh_askpass.exe"
            if not launcher.is_file():
                raise OSError("The packaged SSH OTP askpass launcher is missing.")
        else:
            self.temporary = tempfile.TemporaryDirectory(prefix="pymol-askpass-")
            launcher = Path(self.temporary.name) / "askpass"
            launcher.write_text("#!/bin/sh\nexec " + shlex.quote(console_python()) + " " +
                                shlex.quote(str(helper)) + ' "$@"\n', encoding="utf-8")
            launcher.chmod(0o700)
        environment.update(SSH_ASKPASS=str(launcher), SSH_ASKPASS_REQUIRE="force",
                           DISPLAY=environment.get("DISPLAY") or "pymol-otp",
                           PYMOLPLUGINS_OTP_PYTHON=console_python(),
                           PYMOLPLUGINS_OTP_HELPER=str(helper),
                           PYMOLPLUGINS_OTP_PORT=str(self.listener.getsockname()[1]),
                           PYMOLPLUGINS_OTP_TOKEN=self.token)
        return environment

    def _answer(self, request):
        token = request.get("token", "")
        if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
            return ""
        prompt = request.get("prompt", "")
        ordinary_prompt = re.sub(r"(?i)((?:one[ -]?time|\botp)\s+password|动态密码|一次性密码)", "", prompt) if isinstance(prompt, str) else ""
        if (self.used or not isinstance(prompt, str) or not self.pattern.search(prompt)
                or _NON_OTP_PROMPT.search(ordinary_prompt)):
            self.error = "OTP askpass refused a repeated or non-OTP authentication prompt."
            return ""
        self.used = True
        self.code = _next_code(self.secret, self.cancelled)
        return self.code

    def _serve(self):
        while not self.cancelled.is_set():
            try:
                connection, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                try:
                    connection.settimeout(1)
                    with connection.makefile("rb") as stream:
                        line = stream.readline(16385)
                    if len(line) > 16384:
                        continue
                    request = json.loads(line)
                    answer = self._answer(request) if isinstance(request, dict) else ""
                    connection.sendall(json.dumps({"answer": answer}).encode("ascii") + b"\n")
                except (OSError, ValueError, TypeError):
                    continue

    def redact(self, text):
        for value in (self.secret, self.code):
            if value:
                text = text.replace(value, "[redacted]")
        return text

    def close(self):
        self.cancelled.set()
        self.listener.close()
        self.thread.join(timeout=2)
        self.secret = self.code = self.token = ""
        if self.temporary is not None:
            self.temporary.cleanup()

