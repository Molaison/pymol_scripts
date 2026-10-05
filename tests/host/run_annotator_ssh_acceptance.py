"""Run Windows PyMOL against an isolated local OpenSSH server; no user keys."""

import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import tempfile
import time

from annotator_workflow_smoke import fixture


ROOT = Path(__file__).resolve().parents[2]


def windows_path(path):
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def make_source(directory):
    table = fixture(directory, 8, 8)
    with table.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    rows[0].append("interface quality")
    for index, row in enumerate(rows[1:]):
        if index % 2:
            row[0] = str(directory / row[0])
        row.append("high")
    with table.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    (directory / "review.json").write_text(json.dumps({"path_column": "structure_file", "display_columns": ["AC.iptm", "score_0"]}), encoding="utf-8")
    with gzip.open(directory / "model 2.pdb.gz", "wb") as handle:
        handle.write((directory / "model 2.pdb").read_bytes())
    return table


def hashes(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


def run(args):
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    setup_python = str(Path(args.python).with_name("python.exe")) if Path(args.python).name.lower() == "pythonw.exe" else args.python
    windows_temp = subprocess.check_output([setup_python, "-I", "-c", "import tempfile; print(tempfile.gettempdir())"],
                                          cwd="/mnt/c", text=True).strip()
    mounted_temp = subprocess.check_output(["wslpath", "-u", windows_temp], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="pymol-ssh-") as local, tempfile.TemporaryDirectory(prefix="PymolPlugins-ssh-", dir=mounted_temp) as native:
        local, native = Path(local), Path(native)
        source = local / "remote structures"
        source.mkdir()
        table = make_source(source)
        originals = hashes(source)
        host_key = local / "host_key"
        subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True)
        client_key = native / "client_key"
        subprocess.run(["/mnt/c/Windows/System32/OpenSSH/ssh-keygen.exe", "-q", "-t", "ed25519", "-N", "", "-f", windows_path(client_key)], cwd="/mnt/c", check=True)
        authorized = local / "authorized_keys"
        authorized.write_bytes(client_key.with_suffix(".pub").read_bytes())
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        user = pwd.getpwuid(os.getuid()).pw_name
        config = local / "sshd_config"
        config.write_text(f"Port {port}\nListenAddress 127.0.0.1\nHostKey {host_key}\nPidFile {local / 'sshd.pid'}\nAuthorizedKeysFile {authorized}\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nStrictModes no\nAllowUsers {user}\nLogLevel VERBOSE\n", encoding="utf-8")
        subprocess.run(["/usr/sbin/sshd", "-t", "-f", str(config)], check=True)
        public = host_key.with_suffix(".pub").read_text().split()
        known_hosts = native / "known_hosts"
        known_hosts.write_text(f"[127.0.0.1]:{port} {public[0]} {public[1]}\n", encoding="utf-8")
        client_config = native / "ssh_config"
        client_config.write_text(
            f'Host pymol-ssh-test\n HostName 127.0.0.1\n Port {port}\n User {user}\n IdentityFile "{windows_path(client_key).replace(chr(92), "/")}"\n UserKnownHostsFile "{windows_path(known_hosts).replace(chr(92), "/")}"\n StrictHostKeyChecking yes\n IdentitiesOnly yes\nHost pymol-unreachable\n HostName 127.0.0.1\n Port 1\n BatchMode yes\n', encoding="utf-8")
        with output.with_suffix(".sshd.log").open("w") as server_log:
            server = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config)], stdout=server_log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 5
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Temporary sshd failed; inspect " + str(output.with_suffix(".sshd.log")))
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except ConnectionRefusedError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                command = [args.python, "-I", windows_path(ROOT / "tests/host/annotator_ssh_fuzzy_smoke.py"),
                           "--output", windows_path(output), "--ssh-config", windows_path(client_config),
                           "--remote-table", str(table)]
                if args.ssh_ux_only:
                    command.append("--ssh-ux-only")
                with output.with_suffix(".log").open("w") as log:
                    result = subprocess.run(command, cwd="/mnt/c", stdout=log, stderr=subprocess.STDOUT, timeout=180)
                if result.returncode:
                    raise RuntimeError(f"Windows SSH acceptance exited {result.returncode}; inspect {output.with_suffix('.log')}")
                report = json.loads(output.read_text(encoding="utf-8"))
                if report["status"] != "passed":
                    raise RuntimeError("Windows SSH acceptance did not pass")
                if hashes(source) != originals or any(path.is_dir() for path in source.iterdir()):
                    raise AssertionError("Remote source files or directory contents changed")
                report["real_ssh_server"] = "local OpenSSH with an ephemeral profile and key"
                report["remote_input_hashes_unchanged"] = len(originals)
                output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, indent=2), flush=True)
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default="/mnt/d/Softwares/Pymol3.1/python.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "output/annotation-ssh-fuzzy.json")
    parser.add_argument("--ssh-ux-only", action="store_true", help="Only the SSH import, progress, cache and cancellation boundary")
    run(parser.parse_args())
