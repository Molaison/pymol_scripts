import json
import os
import socket
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pymolplugins.worker.app import (
    WorkerApp,
    _is_loopback_host,
    _server_class_for_host,
    main,
    make_handler,
)
from http.server import HTTPServer


class WorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.app = WorkerApp(token="secret", max_body_bytes=1024)
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, token="secret", data=None):
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        else:
            body = None
        req = Request(self.base + path, data=body, headers=headers)
        with urlopen(req, timeout=2) as response:
            return response.status, json.loads(response.read())

    def raw_request(self, path, body, content_type=None, token="secret"):
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if content_type is not None:
            headers["Content-Type"] = content_type
        req = Request(self.base + path, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_health_reports_unavailable_optional_backends_without_crashing(self):
        status, result = self.request("/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(result["schema"], "pymolplugins.worker/1.0")
        self.assertIn("rosetta", result["backends"])
        self.assertIn("plip", result["backends"])

    def test_capabilities_publish_effective_transport_limits(self):
        status, result = self.request("/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertTrue(result["auth_required"])
        self.assertEqual(result["limits"]["max_request_body_bytes"], 1024)
        self.assertEqual(
            result["limits"]["socket_read_timeout_seconds"],
            self.app.socket_timeout,
        )

    def test_loopback_and_ipv6_server_policy(self):
        self.assertTrue(_is_loopback_host("localhost"))
        self.assertTrue(_is_loopback_host("127.0.0.1"))
        self.assertTrue(_is_loopback_host("::1"))
        self.assertFalse(_is_loopback_host("0.0.0.0"))
        self.assertFalse(_is_loopback_host("192.0.2.1"))
        self.assertEqual(
            _server_class_for_host("::1").address_family,
            socket.AF_INET6,
        )

    def test_non_loopback_cli_requires_opt_in_and_token(self):
        with patch.dict(os.environ, {"PYMOLPLUGINS_WORKER_TOKEN": ""}):
            with self.assertRaisesRegex(SystemExit, "allow-non-loopback"):
                main(["--host", "192.0.2.1", "--port", "18766"])
            with self.assertRaisesRegex(SystemExit, "Bearer token"):
                main(
                    [
                        "--host",
                        "192.0.2.1",
                        "--port",
                        "18766",
                        "--allow-non-loopback",
                    ]
                )

    def test_auth_is_required(self):
        with self.assertRaises(HTTPError) as captured:
            self.request("/v1/health", token=None)
        self.assertEqual(captured.exception.code, 401)

    def test_unknown_endpoint_is_404(self):
        with self.assertRaises(HTTPError) as captured:
            self.request("/missing")
        self.assertEqual(captured.exception.code, 404)

    def test_invalid_schema_is_400(self):
        with self.assertRaises(HTTPError) as captured:
            self.request("/v1/analyze", data={"schema_version": 999})
        self.assertEqual(captured.exception.code, 400)

    def test_schema_version_is_required(self):
        with self.assertRaises(HTTPError) as captured:
            self.request("/v1/analyze", data={"structure": {"format": "pdb", "text": "END\n"}})
        self.assertEqual(captured.exception.code, 400)

    def test_post_requires_json_content_type(self):
        with self.assertRaises(HTTPError) as captured:
            self.raw_request(
                "/v1/analyze",
                json.dumps({"schema_version": 1}).encode(),
                content_type="text/plain",
            )
        self.assertEqual(captured.exception.code, 415)

    def test_forbidden_path_command_and_xml_fields_are_rejected(self):
        for field in ("path", "command", "rosetta_scripts_xml"):
            with self.subTest(field=field), self.assertRaises(HTTPError) as captured:
                self.request(
                    "/v1/analyze",
                    data={"schema_version": 1, "options": {field: "untrusted"}},
                )
            self.assertEqual(captured.exception.code, 400)

    def test_body_limit_returns_413(self):
        with self.assertRaises(HTTPError) as captured:
            self.raw_request(
                "/v1/analyze",
                b"{" + (b"x" * 2048) + b"}",
                content_type="application/json",
            )
        self.assertEqual(captured.exception.code, 413)

    def test_rosetta_unavailable_is_structured_422(self):
        if self.app.rosetta.health()["available"]:
            self.skipTest("PyRosetta is installed in this test environment")
        request = {
            "schema_version": 1,
            "structure": {"format": "pdb", "text": "END\n"},
            "partners": {"target_chains": ["A"], "binder_chains": ["B"]},
            "metrics": ["cms"],
        }
        with self.assertRaises(HTTPError) as captured:
            self.request("/v1/analyze", data=request)
        self.assertEqual(captured.exception.code, 422)
        payload = json.loads(captured.exception.read())
        self.assertEqual(payload["error"]["code"], "backend_error")

    def test_plip_unavailable_is_structured_422_for_valid_atom_map(self):
        if self.app.plip.health()["available"]:
            self.skipTest("PLIP is installed in this test environment")
        pdb_text = (
            "HETATM    1 C1   LIG A   1       0.000   0.000   0.000"
            "  1.00 20.00           C  \nEND\n"
        )
        request = {
            "schema_version": 1,
            "run_id": "worker-plip-1",
            "preset": "plip_overview",
            "structure": {"format": "pdb", "text": pdb_text},
            "source": {"object": "complex", "state": 1, "sha256": "0" * 64},
            "atom_map": [
                {
                    "export_serial": 1,
                    "source_index": 1,
                    "object": "complex",
                    "name": "C1",
                    "element": "C",
                    "alt": "",
                    "residue": {
                        "resn": "LIG",
                        "chain": "A",
                        "segi": "",
                        "resi": "1",
                        "insertion": "",
                    },
                }
            ],
        }
        with self.assertRaises(HTTPError) as captured:
            self.request("/v1/interactions/plip", data=request)
        self.assertEqual(captured.exception.code, 422)
        payload = json.loads(captured.exception.read())
        self.assertEqual(payload["error"]["code"], "backend_error")


if __name__ == "__main__":
    unittest.main()
