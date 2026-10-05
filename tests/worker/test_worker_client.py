import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest
from unittest.mock import Mock, patch

import pymolplugins.remote.client as worker_client
from pymolplugins.remote.client import WorkerRequestError, worker_request


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        if self.path == "/large":
            body = b'"' + (b"x" * 1000) + b'"'
        elif self.path == "/wrong-type":
            body = b"plain"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            body = json.dumps({"schema": "fixture/1", "status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps(
            {"schema": "fixture/1", "authorization": self.headers.get("Authorization")}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WorkerClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_schema_and_token(self):
        result = worker_request(
            "/echo",
            {"schema_version": 1},
            url=self.url,
            token="secret",
            expected_schema="fixture/1",
        )
        self.assertEqual(result["authorization"], "Bearer secret")

    def test_request_and_response_limits(self):
        with self.assertRaises(WorkerRequestError) as request_error:
            worker_request("/echo", {"text": "x" * 100}, url=self.url, max_request_bytes=20)
        self.assertEqual(request_error.exception.code, "request_too_large")
        with self.assertRaises(WorkerRequestError) as response_error:
            worker_request("/large", url=self.url, max_response_bytes=20)
        self.assertEqual(response_error.exception.code, "response_too_large")

    def test_redirect_is_not_followed(self):
        with self.assertRaises(WorkerRequestError) as captured:
            worker_request("/redirect", url=self.url)
        self.assertEqual(captured.exception.http_status, 302)

    def test_invalid_content_type_and_schema_are_typed(self):
        with self.assertRaises(WorkerRequestError) as wrong_type:
            worker_request("/wrong-type", url=self.url)
        self.assertEqual(wrong_type.exception.code, "invalid_response")
        with self.assertRaises(WorkerRequestError) as wrong_schema:
            worker_request("/ok", url=self.url, expected_schema="other/1")
        self.assertEqual(wrong_schema.exception.code, "schema_mismatch")


class _MemoryResponse:
    def __init__(self, body):
        self.body = body
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class WorkerClientNoNetworkTests(unittest.TestCase):
    def test_http_error_retry_policy_respects_scientific_contracts(self):
        self.assertTrue(worker_client._http_error_retriable("request_timeout", 408))
        self.assertTrue(worker_client._http_error_retriable("internal_error", 500))
        self.assertFalse(
            worker_client._http_error_retriable(
                "scientific_contract_error", 500
            )
        )
        self.assertFalse(
            worker_client._http_error_retriable("backend_unavailable", 422)
        )

    def test_nonfinite_request_is_rejected_before_opening_http(self):
        with patch.object(worker_client, "build_opener") as opener:
            with self.assertRaises(WorkerRequestError) as captured:
                worker_request("/v1/analyze", {"option": float("nan")})

        self.assertEqual(captured.exception.code, "invalid_request")
        opener.assert_not_called()

    def test_timeout_must_be_finite_and_positive_before_opening_http(self):
        for value in (False, 0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), patch.object(
                worker_client, "build_opener"
            ) as opener:
                with self.assertRaisesRegex(ValueError, "timeout"):
                    worker_request("/v1/health", timeout=value)
                opener.assert_not_called()

    def test_nonfinite_response_is_rejected_as_non_strict_json(self):
        responses = (
            b'{"schema":"fixture/1","metric":NaN}',
            b'{"schema":"fixture/1","schema":"other/1"}',
        )
        for body in responses:
            with self.subTest(body=body):
                opener = Mock()
                opener.open.return_value = _MemoryResponse(body)
                with patch.object(
                    worker_client, "build_opener", return_value=opener
                ):
                    with self.assertRaises(WorkerRequestError) as captured:
                        worker_request("/v1/health")

                self.assertEqual(captured.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
