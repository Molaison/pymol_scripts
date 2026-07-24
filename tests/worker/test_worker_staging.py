import json
from http.server import HTTPServer
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pymolplugins.worker.app import WorkerApp, make_handler
from pymolplugins.worker.staging import (
    FileStagingBackend,
    StagingError,
    parse_stage_root,
    parse_stage_roots,
)


class WorkerStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.temporary.name, "root")
        self.outside = os.path.join(self.temporary.name, "outside")
        os.makedirs(os.path.join(self.root, "nested"))
        os.mkdir(self.outside)
        self.contents = b"HEADER staged\nATOM      1\nEND\n"
        self.source = os.path.join(self.root, "nested", "sample.pdb")
        with open(self.source, "wb") as handle:
            handle.write(self.contents)
        with open(os.path.join(self.outside, "secret.pdb"), "wb") as handle:
            handle.write(b"secret")
        self.app = WorkerApp(
            token="secret",
            max_body_bytes=1024,
            stage_roots={"dataset": self.root},
            max_stage_file_bytes=1024,
            stage_timeout=2.0,
        )
        handler = make_handler(self.app)
        handler.log_message = lambda *args: None
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(
        self,
        relative_path,
        *,
        alias="dataset",
        token="secret",
        extra=None,
        endpoint="/v1/stage/file",
    ):
        payload = {
            "schema_version": 1,
            "root_alias": alias,
            "relative_path": relative_path,
        }
        payload.update(extra or {})
        headers = {
            "Accept": "application/octet-stream",
            "Content-Type": "application/json",
        }
        if token is not None:
            headers["Authorization"] = "Bearer %s" % token
        request = Request(
            self.base + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return urlopen(request, timeout=2)

    def error(self, relative_path, **kwargs):
        with self.assertRaises(HTTPError) as captured:
            self.request(relative_path, **kwargs)
        payload = json.loads(captured.exception.read())
        return captured.exception.code, payload["error"]["code"]

    def test_binary_response_has_stat_identity_without_server_path(self):
        with self.request("nested/sample.pdb") as response:
            body = response.read()
            headers = response.headers
        self.assertEqual(body, self.contents)
        self.assertEqual(headers.get_content_type(), "application/octet-stream")
        self.assertEqual(int(headers["Content-Length"]), len(self.contents))
        self.assertEqual(headers["X-PymolPlugins-Stage-Root"], "dataset")
        self.assertEqual(
            headers["X-PymolPlugins-Stage-Schema"],
            "pymolplugins.stage-file/1.0",
        )
        self.assertEqual(
            int(headers["X-PymolPlugins-Source-Size"]), len(self.contents)
        )
        self.assertRegex(
            headers["X-PymolPlugins-Source-Identity"], r"^v1-[0-9a-f]{64}$"
        )
        self.assertNotIn(self.root, str(headers))

    def test_stat_identity_matches_file_response_without_transferring_content(self):
        with self.request("nested/sample.pdb") as response:
            response.read()
            headers = response.headers
        with self.request(
            "nested/sample.pdb", endpoint="/v1/stage/stat"
        ) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            payload = json.loads(response.read())
        self.assertEqual(payload["schema"], "pymolplugins.stage-stat/1.0")
        self.assertEqual(payload["root_alias"], "dataset")
        source = payload["source"]
        self.assertEqual(
            source["token"], headers["X-PymolPlugins-Source-Identity"]
        )
        self.assertEqual(source["size"], int(headers["X-PymolPlugins-Source-Size"]))
        self.assertEqual(
            source["mtime_ns"], int(headers["X-PymolPlugins-Source-Mtime-Ns"])
        )
        self.assertNotIn(self.root, json.dumps(payload))

    def test_capabilities_expose_alias_and_bounds_but_not_root_path(self):
        request = Request(
            self.base + "/v1/capabilities",
            headers={"Authorization": "Bearer secret"},
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["staging"]["enabled"])
        self.assertEqual(payload["staging"]["root_aliases"], ["dataset"])
        self.assertEqual(payload["staging"]["max_file_bytes"], 1024)
        self.assertIn("POST /v1/stage/file", payload["endpoints"])
        self.assertIn("POST /v1/stage/stat", payload["endpoints"])
        self.assertNotIn(self.root, json.dumps(payload))

    def test_bearer_token_is_required_for_staging(self):
        for endpoint in ("/v1/stage/file", "/v1/stage/stat"):
            with self.subTest(endpoint=endpoint):
                status, code = self.error(
                    "nested/sample.pdb", token=None, endpoint=endpoint
                )
                self.assertEqual((status, code), (401, "unauthorized"))

    def test_unknown_alias_missing_file_directory_and_size_are_bounded(self):
        self.assertEqual(
            self.error("nested/sample.pdb", alias="missing"),
            (404, "unknown_stage_root"),
        )
        self.assertEqual(
            self.error("nested/missing.pdb"),
            (404, "stage_file_not_found"),
        )
        self.assertEqual(self.error("nested"), (400, "stage_not_regular_file"))
        with open(os.path.join(self.root, "large.pdb"), "wb") as handle:
            handle.write(b"x" * 1025)
        self.assertEqual(
            self.error("large.pdb"),
            (413, "stage_file_too_large"),
        )

    def test_absolute_dot_noncanonical_and_windows_paths_are_rejected(self):
        invalid = (
            "",
            "/etc/passwd",
            "../outside/secret.pdb",
            "nested/../sample.pdb",
            "nested/./sample.pdb",
            "nested//sample.pdb",
            r"nested\sample.pdb",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.error(value), (400, "invalid_stage_path"))

    def test_symlink_escape_is_rejected_but_internal_symlink_is_allowed(self):
        external_link = os.path.join(self.root, "escape")
        internal_link = os.path.join(self.root, "inside")
        try:
            os.symlink(self.outside, external_link)
            os.symlink(os.path.join(self.root, "nested"), internal_link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        self.assertEqual(
            self.error("escape/secret.pdb"),
            (403, "stage_path_forbidden"),
        )
        self.assertEqual(
            self.error("escape/secret.pdb", endpoint="/v1/stage/stat"),
            (403, "stage_path_forbidden"),
        )
        with self.request("inside/sample.pdb") as response:
            self.assertEqual(response.read(), self.contents)

    def test_staging_parser_has_an_exact_field_allowlist(self):
        self.assertEqual(
            self.error("nested/sample.pdb", extra={"options": {}}),
            (400, "invalid_request"),
        )

    def test_analyze_endpoint_still_rejects_path_fields(self):
        payload = {
            "schema_version": 1,
            "structure": {"format": "pdb", "text": "END\n"},
            "relative_path": "nested/sample.pdb",
        }
        request = Request(
            self.base + "/v1/analyze",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=2)
        self.assertEqual(captured.exception.code, 400)
        result = json.loads(captured.exception.read())
        self.assertEqual(result["error"]["code"], "invalid_request")


class DisabledAndBackendStagingTests(unittest.TestCase):
    def test_no_roots_means_endpoint_is_not_enabled(self):
        app = WorkerApp(token="secret")
        self.assertFalse(app.staging.enabled)
        self.assertNotIn("POST /v1/stage/file", app.capabilities()["endpoints"])
        self.assertNotIn("POST /v1/stage/stat", app.capabilities()["endpoints"])
        handler = make_handler(app)
        handler.log_message = lambda *args: None
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for endpoint in ("file", "stat"):
                request = Request(
                    "http://127.0.0.1:%d/v1/stage/%s"
                    % (server.server_port, endpoint),
                    data=json.dumps(
                        {
                            "schema_version": 1,
                            "root_alias": "root",
                            "relative_path": "sample.pdb",
                        }
                    ).encode(),
                    headers={
                        "Authorization": "Bearer secret",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as captured:
                    urlopen(request, timeout=2)
                self.assertEqual(captured.exception.code, 404)
                payload = json.loads(captured.exception.read())
                self.assertEqual(payload["error"]["code"], "staging_disabled")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cli_roots_require_explicit_valid_unique_aliases(self):
        with tempfile.TemporaryDirectory() as root:
            alias, resolved = parse_stage_root("structures=%s" % root)
            self.assertEqual(alias, "structures")
            self.assertEqual(resolved, os.path.realpath(root))
            self.assertEqual(parse_stage_roots(["structures=%s" % root]), {
                "structures": os.path.realpath(root)
            })
            with self.assertRaisesRegex(ValueError, "duplicate"):
                parse_stage_roots(
                    ["structures=%s" % root, "structures=%s" % root]
                )
        with self.assertRaisesRegex(ValueError, "ALIAS=PATH"):
            parse_stage_root("missing-separator")
        with self.assertRaisesRegex(ValueError, "not an existing"):
            parse_stage_root("structures=/definitely/not/present")

    def test_backend_enforces_total_stream_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            filename = os.path.join(root, "sample.pdb")
            with open(filename, "wb") as handle:
                handle.write(b"END\n")
            ticks = iter((0.0, 0.0, 2.0))
            backend = FileStagingBackend(
                {"root": root}, max_stream_seconds=1.0, clock=lambda: next(ticks)
            )
            with backend.open_file("root", "sample.pdb") as opened:
                with self.assertRaises(StagingError) as captured:
                    list(backend.iter_bytes(opened))
            self.assertEqual(captured.exception.code, "stage_timeout")


if __name__ == "__main__":
    unittest.main()
