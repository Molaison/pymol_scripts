import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pymolplugins.worker.app import (
    WorkerApp,
    _backend_error_contract,
    make_handler,
)
from pymolplugins.worker.backends.plip import (
    PlipBackend,
    PlipBackendError,
    PlipRequestError,
    PlipScientificContractError,
    PlipUnavailableError,
)
from pymolplugins.worker.backends.rosetta import (
    RosettaBackendError,
    RosettaRequestError,
    RosettaScientificContractError,
    RosettaUnavailableError,
)


class _ScientificFailureBackend:
    def analyze(self, request):
        raise RosettaScientificContractError("synthetic non-finite Rosetta metric")


class _NeverCalledBackend:
    def analyze(self, request):
        raise AssertionError("strict JSON parsing must reject before backend dispatch")


class _UnavailableBackend:
    def analyze(self, request):
        raise RosettaUnavailableError("PyRosetta is unavailable: fixture")


def _bare_handler(app):
    handler_class = make_handler(app)
    handler = handler_class.__new__(handler_class)
    handler.path = "/v1/analyze"
    request = json.dumps(
        {
            "schema_version": 1,
            "structure": {"format": "pdb", "text": "END\n"},
            "partners": {"target_chains": ["A"], "binder_chains": ["B"]},
            "metrics": ["cms"],
        }
    ).encode("utf-8")
    handler.headers = {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
        "Content-Length": str(len(request)),
    }
    handler.rfile = io.BytesIO(request)
    handler.wfile = io.BytesIO()
    handler.sent_status = None
    handler.sent_headers = []
    handler.send_response = lambda status: setattr(handler, "sent_status", status)
    handler.send_header = lambda name, value: handler.sent_headers.append((name, value))
    handler.end_headers = lambda: None
    return handler


class WorkerJsonContractTests(unittest.TestCase):
    def test_plip_unavailable_health_and_exception_are_actionable_and_typed(self):
        backend = PlipBackend.__new__(PlipBackend)
        backend._classes = None
        backend._version = None
        backend._error = ImportError("fixture missing")
        health = backend.health()

        self.assertFalse(health["available"])
        self.assertIn("install", health["action"].lower())
        with patch(
            "pymolplugins.worker.backends.plip.validate_plip_request",
            return_value=SimpleNamespace(),
        ):
            with self.assertRaises(PlipUnavailableError):
                backend.analyze({})

        with self.assertRaises(PlipRequestError):
            backend.analyze({})

    def test_backend_error_taxonomy_distinguishes_request_unavailable_and_contract(self):
        cases = (
            (RosettaRequestError("bad request"), 400, "invalid_request"),
            (PlipRequestError("bad request"), 400, "invalid_request"),
            (RosettaUnavailableError("not installed"), 422, "backend_unavailable"),
            (PlipUnavailableError("not installed"), 422, "backend_unavailable"),
            (RosettaBackendError("compute failed"), 422, "backend_error"),
            (PlipBackendError("compute failed"), 422, "backend_error"),
            (
                RosettaScientificContractError("non-finite"),
                500,
                "scientific_contract_error",
            ),
            (
                PlipScientificContractError("invalid backend result"),
                500,
                "scientific_contract_error",
            ),
        )
        for error, expected_status, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                status, code, message = _backend_error_contract(error)
                self.assertEqual((status, code), (expected_status, expected_code))
                self.assertEqual(message, str(error))

    def test_request_parser_rejects_nonfinite_json_before_backend_dispatch(self):
        requests = (
            (b'{"schema_version":NaN}', "JSON constant"),
            (
                b'{"schema_version":1,"schema_version":1}',
                "duplicate JSON field",
            ),
        )
        for request, message in requests:
            with self.subTest(request=request):
                app = WorkerApp(token="secret")
                app.rosetta = _NeverCalledBackend()
                handler = _bare_handler(app)
                handler.rfile = io.BytesIO(request)
                handler.headers["Content-Length"] = str(len(request))

                handler.do_POST()

                self.assertEqual(handler.sent_status, 400)
                payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
                self.assertEqual(payload["error"]["code"], "invalid_request")
                self.assertIn(message, payload["error"]["message"])

    def test_handler_maps_backend_request_and_unavailable_errors_without_socket(self):
        app = WorkerApp(token="secret")
        handler = _bare_handler(app)
        request = json.dumps(
            {
                "schema_version": 1,
                "structure": {"format": "pdb", "text": "END\n"},
                "partners": {"target_chains": ["A"], "binder_chains": ["B"]},
                "metrics": ["cms"],
                "options": {"unexpected": True},
            }
        ).encode("utf-8")
        handler.rfile = io.BytesIO(request)
        handler.headers["Content-Length"] = str(len(request))

        handler.do_POST()

        self.assertEqual(handler.sent_status, 400)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "invalid_request")

        app.rosetta = _UnavailableBackend()
        unavailable = _bare_handler(app)
        unavailable.do_POST()
        self.assertEqual(unavailable.sent_status, 422)
        payload = json.loads(unavailable.wfile.getvalue().decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "backend_unavailable")

    def test_send_replaces_nonfinite_payload_with_typed_contract_error(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                app = WorkerApp(token="secret")
                handler = _bare_handler(app)

                handler._send(200, {"schema": "fixture/1", "metric": value})

                self.assertEqual(handler.sent_status, 500)
                text = handler.wfile.getvalue().decode("utf-8")
                payload = json.loads(text)
                self.assertEqual(payload["schema"], "pymolplugins.worker/1.0")
                self.assertEqual(
                    payload["error"]["code"], "scientific_contract_error"
                )
                self.assertNotIn("NaN", text)
                self.assertNotIn("Infinity", text)

    def test_rosetta_contract_failure_has_a_typed_http_error(self):
        app = WorkerApp(token="secret")
        app.rosetta = _ScientificFailureBackend()
        handler = _bare_handler(app)

        handler.do_POST()

        self.assertEqual(handler.sent_status, 500)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(
            payload["error"]["code"], "scientific_contract_error"
        )


if __name__ == "__main__":
    unittest.main()
