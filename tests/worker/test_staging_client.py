from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from pymolplugins.remote.client import WorkerRequestError
from pymolplugins.remote import staging
from pymolplugins.remote.staging import (
    StagedSourceIdentity,
    StagedStatResult,
    download_staged_file,
    stat_staged_file,
    stat_staged_files,
)
from pymolplugins.worker.app import WorkerApp, make_handler


class StagingClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source_root = os.path.join(self.temporary.name, "source")
        self.destination_root = os.path.join(self.temporary.name, "destination")
        os.mkdir(self.source_root)
        self.contents = b"HEADER integration\nEND\n"
        with open(os.path.join(self.source_root, "sample.pdb"), "wb") as handle:
            handle.write(self.contents)
        app = WorkerApp(token="secret", stage_roots={"structures": self.source_root})
        handler = make_handler(app)
        handler.log_message = lambda *args: None
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_streams_binary_to_new_directory_and_returns_source_identity(self):
        destination = os.path.join(self.destination_root, "cache", "sample.pdb")
        result = download_staged_file(
            "structures",
            "sample.pdb",
            destination,
            url=self.url,
            token="secret",
        )
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), self.contents)
        self.assertEqual(result.destination, os.path.abspath(destination))
        self.assertEqual(result.bytes_written, len(self.contents))
        self.assertEqual(result.source.size, len(self.contents))
        self.assertRegex(result.source.token, r"^v1-[0-9a-f]{64}$")
        self.assertEqual(result.to_dict()["root_alias"], "structures")
        self.assertEqual(
            [name for name in os.listdir(os.path.dirname(destination)) if name.endswith(".part")],
            [],
        )

    def test_stat_supports_warm_cache_lookup_and_matches_download_identity(self):
        before = stat_staged_file(
            "structures", "sample.pdb", url=self.url, token="secret"
        )
        destination = os.path.join(self.destination_root, "sample.pdb")
        downloaded = download_staged_file(
            "structures",
            "sample.pdb",
            destination,
            url=self.url,
            token="secret",
        )
        after = stat_staged_file(
            "structures", "sample.pdb", url=self.url, token="secret"
        )
        self.assertEqual(before, downloaded.source)
        self.assertEqual(after, downloaded.source)

    def test_batch_stat_deduplicates_and_preserves_per_path_errors(self):
        results = stat_staged_files(
            "structures",
            ["sample.pdb", "missing.pdb", "sample.pdb"],
            url=self.url,
            token="secret",
        )
        self.assertEqual(
            [result.relative_path for result in results],
            ["sample.pdb", "missing.pdb"],
        )
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].source.size, len(self.contents))
        self.assertFalse(results[1].ok)
        self.assertEqual(results[1].error.code, "stage_file_not_found")
        self.assertEqual(results[1].error.http_status, 404)

    def test_batch_stat_chunks_more_than_endpoint_item_limit(self):
        paths = ["path-%04d.pdb" % index for index in range(600)]
        identity = StagedSourceIdentity(
            token="v1-" + "a" * 64,
            device=1,
            inode=2,
            size=3,
            mtime_ns=4,
            mode=0o100644,
        )

        def fake_chunk(_alias, chunk, **_kwargs):
            return tuple(
                StagedStatResult(path, source=identity) for path in chunk
            )

        with mock.patch.object(
            staging, "_stat_batch_chunk", side_effect=fake_chunk
        ) as batch_call:
            results = stat_staged_files("structures", paths)

        self.assertEqual(len(results), 600)
        self.assertEqual(
            [len(call.args[1]) for call in batch_call.call_args_list],
            [256, 256, 88],
        )

    def test_batch_stat_chunks_by_encoded_request_size(self):
        paths = [
            ("nested/" + ("x" * 400) + "-%03d.pdb" % index)
            for index in range(400)
        ]
        identity = StagedSourceIdentity(
            token="v1-" + "a" * 64,
            device=1,
            inode=2,
            size=3,
            mtime_ns=4,
            mode=0o100644,
        )

        def fake_chunk(alias, chunk, **_kwargs):
            encoded = json.dumps(
                {
                    "schema_version": 1,
                    "root_alias": alias,
                    "relative_paths": list(chunk),
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self.assertLessEqual(
                len(encoded), staging.MAX_STAGE_STAT_BATCH_REQUEST_BYTES
            )
            return tuple(
                StagedStatResult(path, source=identity) for path in chunk
            )

        with mock.patch.object(
            staging, "_stat_batch_chunk", side_effect=fake_chunk
        ) as batch_call:
            results = stat_staged_files("structures", paths)

        self.assertEqual(len(results), len(paths))
        self.assertGreater(batch_call.call_count, 2)
        self.assertTrue(
            all(len(call.args[1]) < 256 for call in batch_call.call_args_list)
        )

    def test_batch_stat_falls_back_only_for_old_worker_not_found(self):
        identity = StagedSourceIdentity(
            token="v1-" + "a" * 64,
            device=1,
            inode=2,
            size=3,
            mtime_ns=4,
            mode=0o100644,
        )
        old_worker = WorkerRequestError(
            "unknown endpoint",
            code="not_found",
            endpoint="/v1/stage/stat-batch",
            http_status=404,
        )
        missing = WorkerRequestError(
            "missing",
            code="stage_file_not_found",
            endpoint="/v1/stage/stat",
            http_status=404,
        )
        with mock.patch.object(
            staging, "_stat_batch_chunk", side_effect=old_worker
        ), mock.patch.object(
            staging, "stat_staged_file", side_effect=(identity, missing)
        ) as single_call:
            results = stat_staged_files("root", ["a.pdb", "b.pdb"])
        self.assertEqual(single_call.call_count, 2)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[1].error.code, "stage_file_not_found")

        disabled = WorkerRequestError(
            "disabled",
            code="staging_disabled",
            endpoint="/v1/stage/stat-batch",
            http_status=404,
        )
        with mock.patch.object(
            staging, "_stat_batch_chunk", side_effect=disabled
        ), mock.patch.object(staging, "stat_staged_file") as no_fallback:
            with self.assertRaises(WorkerRequestError) as captured:
                stat_staged_files("root", ["a.pdb", "b.pdb"])
        self.assertEqual(captured.exception.code, "staging_disabled")
        no_fallback.assert_not_called()

    def test_stat_http_error_is_typed(self):
        with self.assertRaises(WorkerRequestError) as captured:
            stat_staged_file(
                "structures", "missing.pdb", url=self.url, token="secret"
            )
        self.assertEqual(captured.exception.code, "stage_file_not_found")
        self.assertEqual(captured.exception.http_status, 404)

    def test_http_error_is_typed_and_existing_destination_is_preserved(self):
        destination = os.path.join(self.temporary.name, "existing.pdb")
        with open(destination, "wb") as handle:
            handle.write(b"keep")
        with self.assertRaises(WorkerRequestError) as captured:
            download_staged_file(
                "structures",
                "missing.pdb",
                destination,
                url=self.url,
                token="secret",
            )
        self.assertEqual(captured.exception.code, "stage_file_not_found")
        self.assertEqual(captured.exception.http_status, 404)
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"keep")

    def test_client_limit_rejects_before_local_write(self):
        destination = os.path.join(self.temporary.name, "too-large.pdb")
        with self.assertRaises(WorkerRequestError) as captured:
            download_staged_file(
                "structures",
                "sample.pdb",
                destination,
                url=self.url,
                token="secret",
                max_response_bytes=1,
            )
        self.assertEqual(captured.exception.code, "response_too_large")
        self.assertFalse(os.path.exists(destination))

    def test_auth_failure_does_not_create_destination(self):
        destination = os.path.join(self.temporary.name, "unauthorized.pdb")
        with self.assertRaises(WorkerRequestError) as captured:
            download_staged_file(
                "structures", "sample.pdb", destination, url=self.url, token="wrong"
            )
        self.assertEqual(captured.exception.code, "unauthorized")
        self.assertFalse(os.path.exists(destination))


class MalformedStagingHandler(BaseHTTPRequestHandler):
    mode = "truncated"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        if self.path == "/v1/stage/stat":
            source = {
                "token": "v1-" + ("a" * 64),
                "device": 1,
                "inode": 2,
                "size": True if self.mode == "stat_bool_size" else 5,
                "mtime_ns": 3,
                "mode": 33188,
            }
            body = json.dumps(
                {
                    "schema": "pymolplugins.stage-stat/1.0",
                    "root_alias": (
                        "different"
                        if self.mode == "stat_wrong_alias"
                        else request["root_alias"]
                    ),
                    "source": source,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/stage/stat-batch":
            source = {
                "token": "v1-" + ("a" * 64),
                "device": 1,
                "inode": 2,
                "size": True if self.mode == "batch_bool_size" else 5,
                "mtime_ns": 3,
                "mode": 33188,
            }
            paths = list(request["relative_paths"])
            results = [
                {"relative_path": path, "source": dict(source)} for path in paths
            ]
            root_alias = request["root_alias"]
            if self.mode == "batch_wrong_alias":
                root_alias = "different"
            elif self.mode == "batch_wrong_cardinality":
                results = results[:-1]
            elif self.mode == "batch_wrong_order":
                results = list(reversed(results))
            elif self.mode == "batch_both":
                results[0]["error"] = {
                    "code": "stage_file_not_found",
                    "message": "missing",
                    "http_status": 404,
                }
            elif self.mode == "batch_neither":
                results[0].pop("source")
            elif self.mode == "batch_source_extra":
                results[0]["source"]["server_path"] = "/should/not/appear"
            elif self.mode in {
                "batch_error_status_mismatch",
                "batch_request_level_item_error",
            }:
                results[0] = {
                    "relative_path": paths[0],
                    "error": {
                        "code": (
                            "stage_batch_timeout"
                            if self.mode == "batch_request_level_item_error"
                            else "stage_file_not_found"
                        ),
                        "message": "synthetic item error",
                        "http_status": 504,
                    },
                }
            payload = {
                "schema": "pymolplugins.stage-stat-batch/1.0",
                "root_alias": root_alias,
                "results": results,
            }
            if self.mode == "batch_response_large":
                payload["padding"] = "x" * 1024
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
            return
        body = b"short"
        declared = len(body) + 10 if self.mode == "truncated" else len(body)
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain" if self.mode == "wrong_type" else "application/octet-stream",
        )
        self.send_header("Content-Length", str(declared))
        self.send_header("X-PymolPlugins-Stage-Schema", "pymolplugins.stage-file/1.0")
        self.send_header("X-PymolPlugins-Stage-Root", request["root_alias"])
        self.send_header("X-PymolPlugins-Source-Identity", "v1-" + ("a" * 64))
        self.send_header("X-PymolPlugins-Source-Device", "1")
        self.send_header("X-PymolPlugins-Source-Inode", "2")
        self.send_header("X-PymolPlugins-Source-Size", str(declared))
        self.send_header("X-PymolPlugins-Source-Mtime-Ns", "3")
        self.send_header("X-PymolPlugins-Source-Mode", "33188")
        self.end_headers()
        self.wfile.write(body)


class MalformedStagingClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MalformedStagingHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temporary.cleanup()

    def run_mode(self, mode):
        MalformedStagingHandler.mode = mode
        destination = os.path.join(self.temporary.name, "result.pdb")
        with open(destination, "wb") as handle:
            handle.write(b"original")
        with self.assertRaises(WorkerRequestError) as captured:
            download_staged_file("root", "file.pdb", destination, url=self.url)
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"original")
        self.assertEqual(
            [name for name in os.listdir(self.temporary.name) if name.endswith(".part")],
            [],
        )
        return captured.exception

    def run_stat_mode(self, mode):
        MalformedStagingHandler.mode = mode
        with self.assertRaises(WorkerRequestError) as captured:
            stat_staged_file("root", "file.pdb", url=self.url)
        return captured.exception

    def run_batch_mode(self, mode):
        MalformedStagingHandler.mode = mode
        with self.assertRaises(WorkerRequestError) as captured:
            stat_staged_files("root", ["a.pdb", "b.pdb"], url=self.url)
        return captured.exception

    def test_truncated_response_is_rejected_and_partial_file_is_removed(self):
        error = self.run_mode("truncated")
        self.assertEqual(error.code, "invalid_response")

    def test_non_binary_response_is_rejected_without_touching_destination(self):
        error = self.run_mode("wrong_type")
        self.assertEqual(error.code, "invalid_response")

    def test_redirect_is_not_followed(self):
        error = self.run_mode("redirect")
        self.assertEqual(error.http_status, 302)

    def test_stat_rejects_mismatched_alias(self):
        error = self.run_stat_mode("stat_wrong_alias")
        self.assertEqual(error.code, "invalid_response")

    def test_stat_rejects_boolean_numeric_identity_fields(self):
        error = self.run_stat_mode("stat_bool_size")
        self.assertEqual(error.code, "invalid_response")

    def test_batch_rejects_mismatched_alias_cardinality_order_and_item_shape(self):
        for mode in (
            "batch_wrong_alias",
            "batch_wrong_cardinality",
            "batch_wrong_order",
            "batch_both",
            "batch_neither",
            "batch_source_extra",
            "batch_error_status_mismatch",
            "batch_request_level_item_error",
        ):
            with self.subTest(mode=mode):
                error = self.run_batch_mode(mode)
                self.assertEqual(error.code, "invalid_response")

    def test_batch_rejects_boolean_numeric_identity_fields(self):
        error = self.run_batch_mode("batch_bool_size")
        self.assertEqual(error.code, "invalid_response")

    def test_batch_response_is_bounded(self):
        with mock.patch.object(
            staging, "MAX_STAGE_STAT_BATCH_RESPONSE_BYTES", 128
        ):
            error = self.run_batch_mode("batch_response_large")
        self.assertEqual(error.code, "response_too_large")


if __name__ == "__main__":
    unittest.main()
