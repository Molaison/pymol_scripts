"""Source contracts with the actual read-only worker running in local Python.

These tests do not stand in for the separate OpenSSH/Windows host acceptance.
"""

import csv
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pymolplugins.remote import ssh_worker
from pymolplugins.remote.ssh import (
    SshCancelled, SshClient, SshError, SshLocation, parse_ssh_path,
    close_ssh_sessions, ssh_profiles, ssh_source_root, ssh_progress,
)
from pymolplugins.annotation.annotation_store import RowAnnotationStore, annotation_sidecar_path
from pymolplugins.annotation.history_store import latest_config_snapshot, record_config_history
from pymolplugins.annotation.review_file import read_review_file, check_review_unchanged, write_review_override
from pymolplugins.annotation.sources.csv_source import CsvDatasetSource, read_csv_header
from pymolplugins.annotation.sources.ssh_source import load_ssh_directory, resolve_ssh_dataset
from pymolplugins.annotation.state_store import csv_dataset_id, dataset_state_path, read_dataset_state, write_dataset_state


class SshSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote input"
        self.remote.mkdir()
        self.table = self.remote / "scores.tsv"
        self.location = SshLocation("test-profile", str(self.table))
        self.environment = patch.dict(os.environ, {
            "PYMOLPLUGINS_SSH_STATE_ROOT": str(self.root / "local states"),
            "PYMOLPLUGINS_ANNOTATOR_HISTORY": str(self.root / "history.json"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.original_command = SshClient.command
        worker_command = patch.object(SshClient, "command", lambda _self: [sys.executable, ssh_worker.__file__])
        worker_command.start()
        self.addCleanup(worker_command.stop)
        self.addCleanup(close_ssh_sessions)

    def write_table(self, paths):
        with self.table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["structure_path", "AC.iptm"])
            writer.writerows((path, "0.9") for path in paths)

    def test_uri_roundtrip_home_relative_and_config(self):
        self.assertEqual(parse_ssh_path("ssh://cluster-one/data/scores.csv"), SshLocation("cluster-one", "/data/scores.csv"))
        source = SshLocation("cluster-one", "~/folder with spaces/模型.pdb", str(self.root / "ssh config"))
        self.assertEqual(parse_ssh_path(source.uri), source)
        self.assertEqual(SshLocation("cluster-one", "folder/model.pdb").path, "~/folder/model.pdb")
        self.assertEqual(source.resolve("../other.pdb").path, "~/other.pdb")
        self.assertIn("config=", source.uri)
        for profile in ("-oProxyCommand=x", "host name", "*", "$(id)"):
            with self.assertRaises(ValueError):
                SshLocation(profile, "/data")

    def test_python_login_program_is_preserved_without_forcing_batch_authentication(self):
        program = self.root / "ssh login.py"
        program.write_text("raise SystemExit(0)\n")
        location = SshLocation("test-profile", "/data/scores.csv", program=str(program))
        self.assertEqual(parse_ssh_path(location.uri), location)
        self.assertEqual(location.resolve("model.pdb").program, str(program))
        client = SshClient(location)
        with patch.object(SshClient, "command", self.original_command):
            command = client.command()
        self.assertEqual(command[:2], [sys.executable, str(program)])
        self.assertNotIn("BatchMode=yes", command)
        self.assertNotIn("--", command)
        self.assertEqual(command[-2], "test-profile")

    def test_explicit_missing_login_program_fails_without_using_system_ssh(self):
        location = SshLocation("test-profile", "/data", program=str(self.root / "missing.py"))
        with self.assertRaisesRegex(SshError, "does not exist"):
            SshClient(location)

    def test_gui_python_uses_its_matching_console_interpreter_for_login(self):
        program = self.root / "ssh login.py"
        program.write_text("raise SystemExit(0)\n")
        console = self.root / "python.exe"
        console.write_bytes(b"interpreter path selection only")
        client = SshClient(SshLocation("test-profile", "/data", program=str(program)))
        with patch.object(sys, "executable", str(self.root / "pythonw.exe")), \
                patch.object(SshClient, "command", self.original_command):
            command = client.command()
        self.assertEqual(command[:2], [str(console), str(program)])
        self.assertNotIn("BatchMode=yes", command)
        self.assertEqual(command[-2], "test-profile")

    def test_gui_python_missing_console_fails_without_changing_auth_client(self):
        program = self.root / "ssh login.py"
        program.write_text("raise SystemExit(0)\n")
        client = SshClient(SshLocation("test-profile", "/data", program=str(program)))
        with patch.object(sys, "executable", str(self.root / "pythonw.exe")), \
                patch.object(SshClient, "command", self.original_command):
            with self.assertRaisesRegex(SshError, "matching console Python"):
                client.command()

    def test_profile_includes_aliases_and_patterns_without_executing_commands(self):
        include = self.root / "included config"
        include.write_text("Host gpu-a gpu-b\n HostName example.invalid\nHost * !skip\n", encoding="utf-8")
        config = self.root / "config"
        config.write_text(f'Include "{include}"\nHost main\nMatch exec "touch never-created"\nHost main\n', encoding="utf-8")
        self.assertEqual(ssh_profiles(str(config)), ["main", "gpu-a", "gpu-b"])
        self.assertFalse((self.root / "never-created").exists())

    def test_table_paths_remain_raw_and_resolve_remotely(self):
        quoted = "model ' ; $(touch forbidden).pdb"
        (self.remote / quoted).write_bytes(b"ATOM remote\n")
        self.write_table([quoted, str(self.remote / quoted), "missing.pdb"])
        before = self.table.read_bytes()
        self.assertEqual(read_csv_header(self.location.uri)["columns"], ("structure_path", "AC.iptm"))
        dataset = CsvDatasetSource(self.location.uri, "structure_path").load()
        resolved = resolve_ssh_dataset(dataset, require_exists=True)
        self.assertEqual(resolved.source_path, self.location.uri)
        self.assertEqual(resolved.entries[0].source_path, quoted)
        self.assertEqual(parse_ssh_path(resolved.entries[0].resolved_path).path, str(self.remote / quoted))
        self.assertEqual([entry.status for entry in resolved.entries], ["available", "available", "missing"])
        self.assertEqual(before, self.table.read_bytes())
        destination = self.root / "downloaded.pdb"
        SshClient(resolved.entries[0].resolved_path).download(destination)
        self.assertEqual(destination.read_bytes(), b"ATOM remote\n")
        self.assertFalse((self.remote / "forbidden").exists())

    def test_headers_do_not_download_rows_and_handle_ragged_tsv(self):
        self.table.write_text('structure_path\t"interface quality"\nmodel.pdb\textra\textra\n', encoding="utf-8")
        with patch.object(SshClient, "download", side_effect=AssertionError("Header reading downloaded the table")):
            result = read_csv_header(self.location.uri)
        self.assertEqual(result["columns"], ("structure_path", "interface quality"))
        self.assertEqual(result["delimiter"], "\t")

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "Requires Unix file permissions for an unprivileged worker")
    def test_preview_marks_existing_but_unreadable_structures(self):
        structure = self.remote / "unreadable.pdb"
        structure.write_text("ATOM\n")
        self.write_table([structure.name])
        structure.chmod(0)
        try:
            dataset = CsvDatasetSource(self.location.uri, "structure_path").load()
            entry = resolve_ssh_dataset(dataset, require_exists=True).entries[0]
            self.assertEqual(entry.status, "unreadable")
            self.assertFalse(entry.is_navigable)
            self.assertIn("not readable", entry.error)
            folder = load_ssh_directory(self.location.at(str(self.remote)).uri)
            self.assertEqual(folder.entries[0].status, "unreadable")
            self.assertFalse(folder.entries[0].is_navigable)
        finally:
            structure.chmod(0o600)

    def test_directory_names_are_case_sensitive_and_gzip_preference_is_retained(self):
        for name in ("A.pdb", "a.pdb", "A.pdb.gz", "not-structure.txt"):
            (self.remote / name).write_text("data", encoding="utf-8")
        location = self.location.at(str(self.remote))
        dataset = load_ssh_directory(location.uri)
        self.assertEqual([entry.display_name for entry in dataset.entries], ["A.pdb.gz", "a.pdb"])
        self.assertEqual(len({entry.id for entry in dataset.entries}), 2)
        plain = load_ssh_directory(location.uri, prefer_gz=False)
        self.assertEqual([entry.display_name for entry in plain.entries], ["A.pdb", "a.pdb"])

    def test_local_state_and_annotations_are_isolated_by_host_and_remote_case(self):
        locations = [self.location, self.location.at(str(self.remote / "Scores.tsv")),
                     SshLocation("another-host", self.location.path)]
        self.assertEqual(len({csv_dataset_id(value.uri) for value in locations}), 3)
        for value in locations:
            path = write_dataset_state(value.uri, {"config": {"label_name": "quality"}, "position": {"index": 0}}, source_mode="csv")
            self.assertTrue(path.startswith(str(self.root / "local states")))
            self.assertEqual(read_dataset_state(value.uri, source_mode="csv")["source"]["csv_path"], value.uri)
            store = RowAnnotationStore(annotation_sidecar_path(value.uri))
            store.set_many([{"entry_id": "row", "label": "quality", "value": value.profile}])
            self.assertEqual(store.annotations_for("row")["quality"], value.profile)
        self.assertFalse((self.remote / ".pymolplugins").exists())
        self.assertEqual(len({dataset_state_path(value.uri, source_mode="csv") for value in locations}), 3)

    def test_remote_review_defaults_and_local_override_never_modify_source(self):
        generated = self.remote / "review.json"
        generated.write_text(json.dumps({"path_column": "structure_path", "display_columns": ["AC.iptm"]}))
        override = self.remote / "review.override.json"
        override.write_text(json.dumps({"label_column": "name"}))
        source_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (generated, override)]
        review = read_review_file(self.location.uri)
        self.assertEqual(review.config["label_column"], "name")
        check_review_unchanged(self.location.uri, review)
        destination = write_review_override(self.location.uri, {"display_columns": []})
        self.assertTrue(destination.startswith(ssh_source_root(self.location.uri)))
        self.assertEqual(read_review_file(self.location.uri).config["display_columns"], [])
        with self.assertRaisesRegex(RuntimeError, "changed"):
            check_review_unchanged(self.location.uri, review)
        self.assertEqual(source_hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in (generated, override)])

    def test_remote_history_keeps_the_profile_and_original_path(self):
        record_config_history(self.location.at(str(self.remote)).uri,
                              {"source_mode": "csv", "csv_path": self.location.uri})
        snapshot = latest_config_snapshot()
        self.assertEqual(snapshot["csv_path"], self.location.uri)
        self.assertEqual(snapshot["dataset_dir"], self.location.at(str(self.remote)).uri)

    def test_missing_files_are_visible_and_cancellation_terminates_the_child(self):
        with self.assertRaisesRegex(SshError, "No such file"):
            SshClient(self.location).download(self.root / "missing")
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        with patch.object(SshClient, "command", return_value=[sys.executable, "-c", "import time; time.sleep(10)"]):
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(SshCancelled):
                    SshClient(self.location).stat(cancel_event=cancelled)
                self.assertLess(time.monotonic() - started, 2)
            finally:
                timer.join()

    def test_cancellation_stops_a_login_wrapper_and_its_child(self):
        program = self.root / "login.py"
        program.write_text("import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'import time; time.sleep(8)'])\n")
        client = SshClient(SshLocation("test-profile", "/data", program=str(program)))
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        with patch.object(SshClient, "command", return_value=[sys.executable, str(program)]):
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(SshCancelled):
                    client.stat(cancel_event=cancelled)
                self.assertLess(time.monotonic() - started, 2)
            finally:
                timer.join()

    def test_background_login_process_cannot_hold_a_completed_response_open(self):
        program = self.root / "login.py"
        program.write_text(
            "import json, subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)'], stdout=subprocess.DEVNULL)\n"
            "print(json.dumps({'ok': True, 'result': {'ready': True}, 'stream': False}), flush=True)\n"
            "print(json.dumps({'ok': True, 'result': [{'status': 'available', 'child_pid': child.pid}], 'stream': False}), flush=True)\n")
        client = SshClient(SshLocation("test-profile", "/data", program=str(program)))
        with patch.object(SshClient, "command", return_value=[sys.executable, str(program)]):
            started = time.monotonic()
            value = client.stat()
            try:
                self.assertLess(time.monotonic() - started, 2)
            finally:
                os.kill(value["child_pid"], signal.SIGTERM)

    def test_one_connection_serves_binary_reads_and_recovers_from_a_remote_file_error(self):
        binary = self.remote / "binary.pdb"
        content = b"a\x00\n" * 400000
        binary.write_bytes(content)
        real_popen = subprocess.Popen
        with patch("pymolplugins.remote.ssh.subprocess.Popen", wraps=real_popen) as started:
            client = SshClient(self.location.at(str(binary)))
            self.assertEqual(client.stat()["size"], len(content))
            with self.assertRaises(SshError):
                SshClient(self.location.at(str(self.remote / "missing.pdb"))).download(self.root / "missing")
            destination = self.root / "copy.pdb"
            client.download(destination)
            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(client.stat()["size"], len(content))
            self.assertEqual(started.call_count, 1, "Each read started another authentication")

    def test_concurrent_reads_keep_their_requested_file_identities(self):
        paths = [self.remote / f"structure-{index}.pdb" for index in range(8)]
        for index, path in enumerate(paths):
            path.write_bytes(b"x" * (index + 1))
        def read(path):
            return SshClient(self.location.at(str(path))).stat()
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(read, paths))
        self.assertEqual([value["path"] for value in values], [str(path) for path in paths])
        self.assertEqual([value["size"] for value in values], list(range(1, 9)))

    def test_streamed_compression_preserves_bytes_without_a_second_copy(self):
        from pymolplugins.remote import ssh
        content = (b"ATOM      1  CA  ALA A   1      12.345  23.456  34.567  1.00 20.00           C\n" * 40000)
        source = self.remote / "large.pdb"
        source.write_bytes(content)
        destination = self.root / "large.pdb"
        messages = []
        caller = threading.get_ident()
        def progress(message):
            self.assertEqual(threading.get_ident(), caller)
            messages.append(message)
        with patch.object(ssh.shutil, "copyfileobj", side_effect=AssertionError("download copied a spool file")):
            with ssh_progress(progress):
                SshClient(self.location.at(str(source))).download(destination)
        self.assertEqual(destination.read_bytes(), content)
        session = next(iter(ssh._sessions.values()))
        self.assertLess(session.wire_bytes, len(content) // 4)
        self.assertEqual(session.transferred, len(content))
        self.assertTrue(any("authenticating" in message for message in messages))
        self.assertTrue(any("Downloading" in message and "MiB/s" in message for message in messages))
        self.assertFalse(list(self.root.glob(".ssh-*.part")))

    def test_failed_stream_preserves_destination_and_removes_partial_file(self):
        destination = self.root / "existing.pdb"
        destination.write_bytes(b"keep the existing structure")
        command = [sys.executable, "-u", "-c",
                   "import json,struct,sys; "
                   "print(json.dumps({'ok':True,'result':{'ready':True},'stream':False})); "
                   "sys.stdin.readline(); "
                   "print(json.dumps({'ok':True,'result':{'size':10},'stream':True})); "
                   "sys.stdout.buffer.write(struct.pack('!I',10)+b'partial');sys.stdout.flush()"]
        with patch.object(SshClient, "command", return_value=command):
            with self.assertRaisesRegex(SshError, "connection ended"):
                SshClient(self.location).download(destination)
        self.assertEqual(destination.read_bytes(), b"keep the existing structure")
        self.assertFalse(list(self.root.glob(".ssh-*.part")))

    def test_authentication_wait_reports_elapsed_and_cancels_without_publish(self):
        destination = self.root / "existing.pdb"
        destination.write_bytes(b"original")
        messages, cancelled = [], threading.Event()
        timer = threading.Timer(0.35, cancelled.set)
        with patch.object(SshClient, "command", return_value=[sys.executable, "-c", "import time;time.sleep(10)"]):
            timer.start()
            try:
                with ssh_progress(messages.append), self.assertRaises(SshCancelled):
                    SshClient(self.location).download(destination, cancel_event=cancelled)
            finally:
                timer.join()
        self.assertGreaterEqual(len(messages), 3)
        self.assertTrue(all("authenticating" in message and "OTP" in message for message in messages))
        self.assertNotEqual(messages[0], messages[-1])
        self.assertEqual(destination.read_bytes(), b"original")
        self.assertFalse(list(self.root.glob(".ssh-*.part")))

    def test_cancelling_a_queued_request_keeps_the_active_connection(self):
        self.write_table(["model.pdb"])
        command = [sys.executable, "-u", "-c",
                   "import time,runpy;time.sleep(0.6);runpy.run_path(" + repr(ssh_worker.__file__) + ",run_name='__main__')"]
        active = threading.Event()
        def read():
            with ssh_progress(lambda message: active.set()):
                return SshClient(self.location).stat()
        messages, cancelled = [], threading.Event()
        with patch.object(SshClient, "command", return_value=command), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read)
            self.assertTrue(active.wait(2))
            timer = threading.Timer(0.25, cancelled.set)
            timer.start()
            try:
                with ssh_progress(messages.append), self.assertRaises(SshCancelled):
                    SshClient(self.location).stat(cancel_event=cancelled)
            finally:
                timer.join()
            self.assertEqual(future.result(timeout=3)["size"], self.table.stat().st_size)
            self.assertEqual(SshClient(self.location).stat()["size"], self.table.stat().st_size)
        self.assertTrue(messages and all("Waiting for active SSH request" in message for message in messages))

    def test_gzip_download_is_not_compressed_twice(self):
        import gzip
        from pymolplugins.remote import ssh
        content = gzip.compress(b"ATOM\n" * 10000)
        source = self.remote / "model.pdb.gz"
        source.write_bytes(content)
        destination = self.root / "copy.pdb.gz"
        SshClient(self.location.at(str(source))).download(destination)
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(next(iter(ssh._sessions.values())).wire_bytes, len(content))


if __name__ == "__main__":
    unittest.main()
