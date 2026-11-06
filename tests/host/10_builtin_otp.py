"""Native Windows OpenSSH OTP acceptance against a disposable loopback server.

Run with system Python (Paramiko is a test-only server dependency). The client
runs in PyMOL's bundled Python without Paramiko or any external OTP wrapper.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def expected_code(secret):
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)


def client_case(fixture_path):
    from pymolplugins.remote.otp import configure_otp
    from pymolplugins.remote.ssh import SshCancelled, SshClient, SshError, SshLocation, close_ssh_sessions
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    os.environ["PYMOLPLUGINS_SSH_OTP_STORE"] = str(fixture_path.with_suffix(".store"))
    secret, mode = fixture["secret"], fixture["mode"]
    configure_otp("f101-fixture", secret, fixture["config"])
    location = SshLocation("f101-fixture", fixture["table"], fixture["config"])
    client = SshClient(location, timeout=15)
    assert Path(client.executable).name.casefold() == "ssh.exe", client.executable
    assert secret not in location.uri and secret not in " ".join(client.command())
    cancelled = threading.Event()
    started = time.monotonic()
    timer = threading.Timer(0.7, cancelled.set) if mode == "cancel" else None
    if timer:
        timer.start()
    try:
        if mode in {"success", "custom"}:
            if mode == "custom":
                client = SshClient(location, timeout=15, otp_prompt=r"(?i)lab challenge")
            header = client.read_header()
            assert list(header["columns"]) == ["structure_path", "score"], header
            assert client.stat()["file"]
            # The second operation uses the same authenticated SSH session.
            return {"status": "passed", "columns": list(header["columns"]), "native_ssh": True,
                    "seconds": round(time.monotonic() - started, 3)}
        try:
            client.read_header(cancel_event=cancelled)
        except SshCancelled:
            assert mode == "cancel"
        except SshError as exc:
            assert mode in {"reject", "password"}, str(exc)
            assert secret not in str(exc)
        else:
            raise AssertionError("Authentication unexpectedly succeeded")
        return {"status": "passed", "seconds": round(time.monotonic() - started, 3)}
    finally:
        if timer:
            timer.cancel()
        close_ssh_sessions()


def server_case(directory, mode, python):
    import paramiko
    from pymolplugins.remote.ssh import _REMOTE_SCRIPT
    root = directory / mode
    root.mkdir()
    secret = base64.b32encode(hashlib.sha256(("fixture-only-" + mode).encode()).digest()[:20]).decode()
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(20)
    port = listener.getsockname()[1]
    known = root / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} {key.get_name()} {key.get_base64()}\n", encoding="utf-8")
    config = root / "ssh_config"
    config.write_text(f'Host f101-fixture\n HostName 127.0.0.1\n Port {port}\n User test-only\n'
                      f' UserKnownHostsFile "{known.as_posix()}"\n StrictHostKeyChecking yes\n'
                      ' PubkeyAuthentication no\n PreferredAuthentications keyboard-interactive\n', encoding="utf-8")
    table = root / "scores.csv"
    table.write_text("structure_path,score\nmodel.pdb,0.9\n", encoding="utf-8")
    # Windows accepts a root-relative path on the server process's current drive.
    fixture = {"secret": secret, "mode": mode, "config": str(config), "table": table.as_posix()[2:]}
    fixture_path = root / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    events = {"challenges": 0, "nonempty_answers": 0, "worker_started": False}
    errors = []

    class Server(paramiko.ServerInterface):
        def __init__(self):
            self.command = None
            self.ready = threading.Event()

        def get_allowed_auths(self, username):
            return "keyboard-interactive"

        def check_auth_interactive(self, username, submethods):
            events["challenges"] += 1
            if mode == "cancel":
                time.sleep(2)
            prompt = {"password": "Password:", "custom": "Lab challenge:"}.get(mode, "Verification code:")
            return paramiko.InteractiveQuery("", "", (prompt, False))

        def check_auth_interactive_response(self, responses):
            events["nonempty_answers"] += sum(bool(item) for item in responses)
            if mode not in {"reject", "password", "cancel"} and responses == [expected_code(secret)]:
                return paramiko.AUTH_SUCCESSFUL
            return paramiko.AUTH_FAILED

        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            self.command = command.decode("utf-8")
            self.ready.set()
            return True

    def serve():
        process = transport = None
        try:
            connection, _address = listener.accept()
            transport = paramiko.Transport(connection)
            transport.add_server_key(key)
            server = Server()
            transport.start_server(server=server)
            channel = transport.accept(15)
            if channel is None:
                return
            assert server.ready.wait(5)
            import shlex
            assert shlex.split(server.command) == ["python3", "-c", _REMOTE_SCRIPT]
            events["worker_started"] = True
            process = subprocess.Popen([python, "-I", "-u", "-c", _REMOTE_SCRIPT],
                                       cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW)

            def input_pipe():
                try:
                    while data := channel.recv(65536):
                        process.stdin.write(data)
                        process.stdin.flush()
                except (OSError, ValueError):
                    pass
                finally:
                    process.stdin.close()

            input_thread = threading.Thread(target=input_pipe, daemon=True)
            input_thread.start()
            while data := os.read(process.stdout.fileno(), 65536):
                channel.sendall(data)
            process.wait(timeout=5)
            channel.close()
            input_thread.join(2)
        except (EOFError, ConnectionResetError):
            if mode in {"success", "custom"}:
                errors.append(traceback.format_exc())
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            if process:
                if process.poll() is None:
                    process.kill()
                if process.stdin.closed:
                    process.stdin = None
                process.communicate(timeout=5)
            if transport:
                transport.close()
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    completed = subprocess.run([python, "-I", "-u", str(Path(__file__).resolve()), "--client", str(fixture_path)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    thread.join(18)
    assert not thread.is_alive(), "Fixture server did not stop"
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert not errors, errors
    assert events["challenges"] == 1, events
    assert events["nonempty_answers"] == (1 if mode in {"success", "custom", "reject"} else 0), events
    assert events["worker_started"] == (mode in {"success", "custom"}), events
    return dict(result, **events)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", type=Path)
    parser.add_argument("--python", default="D:/Softwares/Pymol3.1/python.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "output/10_otp_native.json")
    args = parser.parse_args()
    if args.client:
        print(json.dumps(client_case(args.client)))
        return
    results = {}
    with tempfile.TemporaryDirectory(prefix="pymol-otp-native-") as temporary:
        try:
            for mode in ("success", "custom", "reject", "password", "cancel"):
                results[mode] = server_case(Path(temporary), mode, args.python)
            results["status"] = "passed"
        except Exception:
            results.update(status="failed", traceback=traceback.format_exc())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    raise SystemExit(0 if results["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
