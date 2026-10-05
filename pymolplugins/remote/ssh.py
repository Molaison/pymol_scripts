"""OpenSSH profile access and stable source identities, without mounting files."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait
from dataclasses import dataclass
import atexit
import glob
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import struct
import sys
import tempfile
import threading
import time
import zlib
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit


MAX_TRANSFER_BYTES = 512 * 1024 * 1024
_REMOTE_SCRIPT = Path(__file__).with_name("ssh_worker.py").read_text(encoding="utf-8")


class SshError(RuntimeError):
    """A visible SSH, authentication, protocol or remote file failure."""


class SshCancelled(SshError):
    """The caller cancelled an active SSH operation."""


class SshRemoteError(SshError):
    """A complete remote error response; the authenticated session is reusable."""


_sessions = {}
_sessions_lock = threading.Lock()
_progress_context = threading.local()


@contextmanager
def ssh_progress(callback):
    """Report SSH work to this caller's worker, never directly to Qt widgets."""
    previous = getattr(_progress_context, "callback", None)
    _progress_context.callback = callback
    try:
        yield
    finally:
        _progress_context.callback = previous


def close_ssh_sessions():
    """Close only SSH sessions owned by this Python process."""
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        session.close()


atexit.register(close_ssh_sessions)


def is_ssh_path(path):
    return str(path).startswith("ssh://")


@dataclass(frozen=True)
class SshLocation:
    profile: str
    path: str
    config: str = ""
    program: str = ""

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", self.profile):
            raise ValueError("Choose a concrete SSH Host profile (without spaces or wildcards).")
        if not self.path or "\0" in self.path or "\n" in self.path or "\r" in self.path:
            raise ValueError("Enter a remote file or folder path.")
        path = self.path if self.path.startswith(("/", "~")) else "~/" + self.path
        object.__setattr__(self, "path", posixpath.normpath(path))
        if self.config:
            object.__setattr__(self, "config", os.path.abspath(os.path.expanduser(self.config)))
        if self.program:
            object.__setattr__(self, "program", os.path.abspath(os.path.expanduser(self.program)))

    @property
    def uri(self):
        path = self.path if self.path.startswith("/") else "/" + self.path
        options = {key: value for key, value in (("config", self.config), ("program", self.program)) if value}
        query = "?" + urlencode(options) if options else ""
        return "ssh://" + quote(self.profile, safe="") + quote(path, safe="/~") + query

    def at(self, path):
        return SshLocation(self.profile, path, self.config, self.program)

    def resolve(self, path, base=""):
        if is_ssh_path(path):
            remote = parse_ssh_path(path)
            if (remote.profile, remote.config, remote.program) != (self.profile, self.config, self.program):
                raise ValueError("A table row refers to a different SSH profile.")
            return remote
        if str(path).startswith(("/", "~")):
            return self.at(str(path))
        parent = base or posixpath.dirname(self.path)
        if is_ssh_path(parent):
            parent = self.resolve(parent).path
        return self.at(posixpath.join(parent, str(path)))


def parse_ssh_path(path):
    parsed = urlsplit(str(path))
    if parsed.scheme != "ssh" or not parsed.netloc or parsed.fragment:
        raise ValueError("Invalid SSH source path.")
    query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
    if set(query) - {"config", "program"} or any(len(value) != 1 for value in query.values()):
        raise ValueError("Invalid SSH source options.")
    remote_path = unquote(parsed.path)
    if remote_path.startswith("/~"):
        remote_path = remote_path[1:]
    return SshLocation(unquote(parsed.netloc), remote_path, query.get("config", [""])[0], query.get("program", [""])[0])


def normalize_source_path(path):
    return parse_ssh_path(path).uri if is_ssh_path(path) else os.path.abspath(os.path.expanduser(str(path)))


def ssh_source_root(path):
    """Return local metadata storage for exactly one remote source, without I/O."""
    identity = hashlib.sha256(parse_ssh_path(path).uri.encode("utf-8")).hexdigest()
    root = os.environ.get("PYMOLPLUGINS_SSH_STATE_ROOT", "").strip()
    root = root or os.path.join(os.path.expanduser("~"), ".pymolplugins", "ssh_sources")
    return os.path.join(os.path.abspath(os.path.expanduser(root)), identity)


def ssh_profiles(config=""):
    """List concrete Host aliases and Includes; OpenSSH resolves their settings."""
    filename = os.path.abspath(os.path.expanduser(config or "~/.ssh/config"))
    if not config and not os.path.exists(filename):
        return []
    pending, visited, result = [filename], set(), []
    while pending:
        current = os.path.realpath(pending.pop(0))
        if current in visited:
            continue
        visited.add(current)
        with open(current, encoding="utf-8-sig") as handle:
            for line in handle:
                directive = re.match(r"^\s*(Host|Include)(?:\s+|=)", line, re.IGNORECASE)
                if directive is None:
                    continue
                parts = shlex.split(re.sub(r"^(\s*\w+)\s*=", r"\1 ", line), comments=True)
                if not parts:
                    continue
                if parts[0].casefold() == "host":
                    result.extend(value for value in parts[1:] if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", value))
                elif parts[0].casefold() == "include":
                    for pattern in parts[1:]:
                        pattern = os.path.expanduser(pattern)
                        if not os.path.isabs(pattern):
                            pattern = os.path.join(os.path.expanduser("~/.ssh"), pattern)
                        pending.extend(sorted(glob.glob(pattern)))
    return list(dict.fromkeys(result))


def default_ssh_program():
    """Use an installed SSH login wrapper before the bare system executable."""
    configured = os.environ.get("PYMOLPLUGINS_SSH_PROGRAM", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    login_script = os.path.join(os.path.expanduser("~"), ".ssh", "ssh_otp", "ssh_totp.py")
    if os.path.isfile(login_script):
        return login_script
    return shutil.which("ssh") or ""


class _SshSession:
    """One authenticated, serialized read channel for one SSH command/profile."""

    def __init__(self, client, command):
        self.client, self.command = client, command
        self.lock = threading.Lock()
        self.closed = False
        self.process = None
        self.future = None
        self.phase = "Connecting / authenticating"
        self.transferred = self.total = self.wire_bytes = 0
        self.transfer_started = None
        self.diagnostics = tempfile.TemporaryFile()
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ssh-response")

    def _start(self):
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.diagnostics, **options)

    def _read_exact(self, size, *, transfer=False):
        parts, remaining = [], size
        while remaining:
            data = self.process.stdout.read1(min(remaining, 64 * 1024))
            if not data:
                raise SshError("SSH connection ended during a file transfer.")
            parts.append(data)
            remaining -= len(data)
            if transfer:
                self.wire_bytes += len(data)
        return b"".join(parts)

    def _read_json(self):
        line = self.process.stdout.readline(32 * 1024 * 1024 + 1)
        if not line:
            self.diagnostics.seek(0)
            detail = self.diagnostics.read().decode("utf-8", errors="replace").strip()
            raise SshError(f"SSH {self.client.location.profile}: {detail or 'connection ended'}")
        if len(line) > 32 * 1024 * 1024:
            raise SshError("SSH response exceeds the metadata limit.")
        try:
            value = json.loads(line)
        except (UnicodeError, ValueError) as exc:
            raise SshError("SSH returned invalid data; check remote shell startup output and Python 3.") from exc
        if not isinstance(value, dict) or type(value.get("ok")) is not bool:
            raise SshError("SSH returned an invalid response frame.")
        if not value["ok"]:
            raise SshRemoteError(f"SSH {self.client.location.profile}: {value.get('error', 'remote read failed')}\n{value.get('traceback', '')}")
        return value

    def _read_response(self, output, starting=False):
        if starting:
            ready = self._read_json()
            if ready.get("result") != {"ready": True}:
                raise SshError("SSH worker did not send its ready response.")
        self.phase = "Reading remote metadata"
        header = self._read_json()
        if header.get("stream"):
            self.phase = "Downloading"
            self.total = header["result"]["size"]
            self.transfer_started = time.monotonic()
            compressed = header.get("compression") == "zlib"
            total = 0
            while True:
                length = struct.unpack("!I", self._read_exact(4))[0]
                if not length:
                    break
                if length > 1024 * 1024:
                    raise SshError("SSH file response exceeds the transfer limit.")
                data = self._read_exact(length, transfer=True)
                if compressed:
                    decoder = zlib.decompressobj()
                    data = decoder.decompress(data, 1024 * 1024 + 1)
                    if not decoder.eof or decoder.unused_data or len(data) > 1024 * 1024:
                        raise SshError("SSH returned an invalid compressed block.")
                total += len(data)
                if total > MAX_TRANSFER_BYTES:
                    raise SshError("SSH file response exceeds the transfer limit.")
                output.write(data)
                self.transferred = total
            self._read_json()
            if total != self.total:
                raise SshError("SSH download was incomplete.")
        output.seek(0)
        return header["result"]

    def _report(self, callback, started, *, queued=False):
        if callback is None:
            return
        now = time.monotonic()
        phase = "Waiting for active SSH request" if queued else self.phase
        message = f"SSH {self.client.location.profile}: {phase} · {now - started:.1f}s"
        if not queued and self.phase == "Connecting / authenticating":
            message += " · waiting for the configured login program / OTP; Cancel is available"
        elif not queued and self.transfer_started is not None:
            rate = self.wire_bytes / max(now - self.transfer_started, 0.001) / (1024 * 1024)
            message += (f" · {self.transferred / (1024 * 1024):.2f} / {self.total / (1024 * 1024):.2f} MiB"
                        f" · {rate:.2f} MiB/s received")
        callback(message)

    def request(self, request, output, cancel_event, timeout, progress=None):
        started = time.monotonic()
        deadline = started + timeout
        while not self.lock.acquire(timeout=0.1):
            self._report(progress, started, queued=True)
            self._check_wait(cancel_event, deadline)
        try:
            self._check_wait(cancel_event, deadline)
            if self.closed:
                raise SshError("SSH connection closed; retry the requested operation.")
            starting = self.process is None
            self.phase = "Connecting / authenticating" if starting else "Reading remote metadata"
            self.transferred = self.total = self.wire_bytes = 0
            self.transfer_started = None
            self._report(progress, started)
            if starting:
                self._start()
            payload = json.dumps(request, ensure_ascii=True).encode("ascii") + b"\n"
            self.process.stdin.write(payload)
            self.process.stdin.flush()
            self.future = self.reader.submit(self._read_response, output, starting)
            while True:
                self._check_wait(cancel_event, deadline)
                self._report(progress, started)
                try:
                    result = self.future.result(timeout=0.1)
                    self._report(progress, started)
                    return result
                except FutureTimeout:
                    continue
        except SshRemoteError:
            raise
        except BaseException:
            self.close()
            raise
        finally:
            self.lock.release()

    def _check_wait(self, cancel_event, deadline):
        if cancel_event is not None and cancel_event.is_set():
            raise SshCancelled("SSH operation cancelled.")
        if time.monotonic() >= deadline:
            raise SshError(f"SSH {self.client.location.profile}: {self.phase} timed out. "
                           "Retry the operation; check the login program / OTP if authentication did not finish.")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process is not None:
            self.client._stop_process(self.process, drain=False)
        if self.future is not None:
            _done, pending = wait([self.future], timeout=5)
            if pending:
                raise SshError("SSH response reader did not stop after the connection closed.")
        self.reader.shutdown(wait=False)
        if self.process is not None:
            self.process.stdin.close()
            self.process.stdout.close()
        self.diagnostics.close()


class SshClient:
    """Invoke the user's OpenSSH configuration with cancellable file operations."""

    def __init__(self, location, *, timeout=120.0, executable=None):
        self.location = parse_ssh_path(location) if isinstance(location, str) else location
        self.timeout = float(timeout)
        self.executable = str(executable or self.location.program or default_ssh_program())
        if not self.executable:
            raise SshError("OpenSSH is not installed or ssh is not on PATH.")
        if not os.path.isfile(self.executable) and not shutil.which(self.executable):
            raise SshError("SSH program does not exist: " + self.executable)
        if self.executable.lower().endswith((".cmd", ".bat")):
            raise SshError("Choose ssh.exe or the Python login script used by your SSH launcher.")

    def command(self):
        if self.executable.lower().endswith(".py"):
            interpreter = sys.executable
            if os.path.basename(interpreter).casefold() == "pythonw.exe":
                # pythonw loses inherited standard handles in the login script's
                # SSH child. Use the console interpreter from the same install;
                # CREATE_NO_WINDOW still keeps the plugin-owned process hidden.
                interpreter = os.path.join(os.path.dirname(interpreter), "python.exe")
                if not os.path.isfile(interpreter):
                    raise SshError("SSH login scripts require the matching console Python: " + interpreter)
            command = [interpreter, self.executable]
        else:
            command = [self.executable]
        command += ["-T", "-o", "ConnectTimeout=10",
                   "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2"]
        if self.location.config:
            command += ["-F", self.location.config]
        # Profiles are validated against option-like names. Keep the target
        # after all flags so login wrappers can insert their authentication flags.
        command += [self.location.profile, "python3 -c " + shlex.quote(_REMOTE_SCRIPT)]
        return command

    def _stop_process(self, process, *, drain=True):
        if process.poll() is not None:
            return
        if os.name == "nt" and self.executable.lower().endswith(".py"):
            stopped = subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            if stopped.returncode and process.poll() is None:
                raise SshError("Could not stop the SSH login process: " + stopped.stderr.decode(errors="replace"))
        elif os.name == "nt":
            process.kill()
        elif process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        if drain:
            process.communicate(timeout=5)

    @contextmanager
    def _response(self, request, cancel_event=None, *, output=None):
        if cancel_event is not None and cancel_event.is_set():
            raise SshCancelled("SSH operation cancelled.")
        command = tuple(self.command())
        key = (self.location.profile, self.location.config, command)
        with _sessions_lock:
            session = _sessions.get(key)
            if session is None or session.closed:
                session = _SshSession(self, command)
                _sessions[key] = session
        progress = getattr(_progress_context, "callback", None)
        if output is not None:
            value = session.request(request, output, cancel_event, self.timeout, progress)
            yield value, output
        else:
            with tempfile.TemporaryFile() as temporary:
                value = session.request(request, temporary, cancel_event, self.timeout, progress)
                yield value, temporary

    def stat_many(self, paths, *, cancel_event=None):
        with self._response({"operation": "stat", "paths": list(paths)}, cancel_event) as (values, _):
            return values

    def stat(self, path=None, *, cancel_event=None):
        value = self.stat_many([path or self.location.path], cancel_event=cancel_event)[0]
        if value["status"] != "available":
            raise SshError(value["error"])
        return value

    def list_directory(self, path=None, *, cancel_event=None):
        with self._response({"operation": "list", "path": path or self.location.path,
                             "maximum": 100000}, cancel_event) as (value, _):
            return value

    def read_small_files(self, paths, *, cancel_event=None):
        with self._response({"operation": "small_files", "paths": list(paths),
                             "maximum": 1024 * 1024}, cancel_event) as (values, _):
            return values

    def read_header(self, *, encoding="utf-8-sig", delimiter="auto", cancel_event=None):
        request = {"operation": "header", "path": self.location.path,
                   "encoding": encoding, "delimiter": delimiter}
        with self._response(request, cancel_event) as (value, _):
            return value

    def download(self, destination, *, path=None, cancel_event=None):
        remote_path = path or self.location.path
        request = {"operation": "read", "path": remote_path, "maximum": MAX_TRANSFER_BYTES,
                   "compression": "none" if remote_path.lower().endswith(".gz") else "zlib"}
        destination = os.path.abspath(os.fspath(destination))
        descriptor, temporary = tempfile.mkstemp(prefix=".ssh-", suffix=".part",
                                                  dir=os.path.dirname(destination))
        try:
            with os.fdopen(descriptor, "w+b") as output:
                with self._response(request, cancel_event, output=output) as (value, _):
                    if cancel_event is not None and cancel_event.is_set():
                        raise SshCancelled("SSH download cancelled.")
            os.replace(temporary, destination)
            return value
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


@contextmanager
def local_source_file(path, *, cancel_event=None):
    """Yield a readable local snapshot; remote path identity remains the caller's."""
    if not is_ssh_path(path):
        yield path
        return
    location = parse_ssh_path(path)
    with tempfile.TemporaryDirectory(prefix="pymol_ssh_source_") as directory:
        destination = os.path.join(directory, "source" + posixpath.splitext(location.path)[1])
        SshClient(location).download(destination, cancel_event=cancel_event)
        yield destination
