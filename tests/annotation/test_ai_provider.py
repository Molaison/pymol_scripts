import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pymolplugins.annotation import ai_provider


SCHEMA = {"type": "object", "properties": {"steps": {"type": "array"}},
          "required": ["steps"], "additionalProperties": False}
DOCUMENT = '{"steps":[]}'


class ProviderHandler(BaseHTTPRequestHandler):
    requests = []
    model_requests = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        type(self).requests.append((self.path, dict(self.headers), body))
        if self.path == "/fail/responses":
            response = {"error": "rejected secret-key-from-body"}
            status = 401
        elif self.path.endswith("/chat/completions"):
            response = {"id": "chat-1", "model": "chat-returned", "usage": {"total_tokens": 7},
                        "choices": [{"finish_reason": "stop",
                                     "message": {"role": "assistant", "content": DOCUMENT}}]}
            status = 200
        else:
            response = {"id": "resp-1", "model": "responses-returned", "status": "completed",
                        "usage": {"total_tokens": 9},
                        "output": [{"type": "message", "content": [
                            {"type": "output_text", "text": DOCUMENT}]}]}
            status = 200
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        type(self).model_requests.append((self.path, dict(self.headers)))
        if self.path == "/fail/models":
            response = {"error": "models secret-key-from-body"}
            status = 403
        else:
            response = {"data": [{"id": "model-z"}, {"id": "model-a"}]}
            status = 200
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


class AiProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ProviderHandler.requests = []
        ProviderHandler.model_requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, connection):
        return {"instructions": "Return the schema", "input": "draft commands",
                "schema": SCHEMA, "connection": connection}

    def test_custom_responses_uses_v1_base_and_strict_schema(self):
        result = ai_provider.request_commands(self.request({
            "mode": "custom", "base_url": self.origin + "/v1", "model": "response-model",
            "api_key": "response-secret", "api": "responses"}))

        path, headers, payload = ProviderHandler.requests[-1]
        self.assertEqual(path, "/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer response-secret")
        self.assertEqual(payload["model"], "response-model")
        self.assertEqual(payload["text"]["format"]["schema"], SCHEMA)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(result["text"], DOCUMENT)
        self.assertEqual(result["response_status"], "completed")

    def test_custom_chat_uses_exact_endpoint_and_json_schema(self):
        endpoint = self.origin + "/v1/chat/completions"
        result = ai_provider.request_commands(connection={
            "mode": "custom", "base_url": endpoint, "model": "chat-model",
            "api_key": "chat-secret", "api": "chat_completions"},
            instructions="Return the schema", input="draft commands", schema=SCHEMA)

        path, headers, payload = ProviderHandler.requests[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer chat-secret")
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "Return the schema"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "draft commands"})
        self.assertEqual(payload["response_format"], {
            "type": "json_schema", "json_schema": {
                "name": "pymol_command_draft", "strict": True, "schema": SCHEMA}})
        self.assertEqual(result["provider"], "custom")
        self.assertEqual(result["model"], "chat-model")
        self.assertEqual(result["text"], DOCUMENT)

    def test_custom_http_error_does_not_expose_key_url_or_body(self):
        secret = "never-print-this-key"
        with self.assertRaises(ValueError) as raised:
            ai_provider.request_commands(self.request({
                "mode": "custom", "base_url": self.origin + "/fail/responses",
                "model": "response-model", "api_key": secret, "api": "responses"}))
        message = str(raised.exception)
        self.assertEqual(message, "Provider HTTP 401; existing editors unchanged")
        self.assertNotIn(secret, message)
        self.assertNotIn(self.origin, message)
        self.assertNotIn("secret-key-from-body", message)

    def test_describe_returns_metadata_without_network_or_key(self):
        before = len(ProviderHandler.requests)
        models_before = len(ProviderHandler.model_requests)
        result = ai_provider.request_commands(action="describe", connection={
            "mode": "custom", "base_url": self.origin + "/v1", "model": "visible-model",
            "api_key": "hidden-key", "api": "responses"})
        self.assertEqual(result, {"ok": True, "provider": "custom", "model": "visible-model",
                                  "api": "responses", "base_url": self.origin + "/v1",
                                  "configured": True})
        self.assertEqual(len(ProviderHandler.requests), before)
        self.assertEqual(len(ProviderHandler.model_requests), models_before)
        self.assertNotIn("hidden-key", json.dumps(result))

    def test_models_uses_normalized_terminal_url_without_requiring_model(self):
        result = ai_provider.request_commands(action="models", connection={
            "mode": "custom", "base_url": self.origin + "/v1/chat/completions",
            "model": "", "api_key": "models-secret", "api": "chat_completions"})

        path, headers = ProviderHandler.model_requests[-1]
        self.assertEqual(path, "/v1/models")
        self.assertEqual(headers["Authorization"], "Bearer models-secret")
        self.assertEqual(result, {"ok": True, "models": ["model-z", "model-a"]})

    def test_models_http_error_is_redacted(self):
        secret = "models-never-print"
        with self.assertRaises(ValueError) as raised:
            ai_provider.request_commands(action="models", connection={
                "mode": "custom", "base_url": self.origin + "/fail/responses",
                "model": "", "api_key": secret, "api": "responses"})
        message = str(raised.exception)
        self.assertEqual(message, "Provider HTTP 403 while listing models")
        self.assertNotIn(secret, message)
        self.assertNotIn(self.origin, message)
        self.assertNotIn("secret-key-from-body", message)

    def test_empty_custom_connection_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "requires base URL, model, API key, and API type"):
            ai_provider.request_commands(self.request({"mode": "custom"}))

    def test_default_codex_mode_reads_selected_provider_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "config.toml").write_text(
                'model_provider = "local-provider"\nmodel = "configured-model"\n'
                '[model_providers.local-provider]\nwire_api = "responses"\n'
                'base_url = "%s/v1"\nenv_key = "AI_PROVIDER_TEST_TOKEN"\n' % self.origin,
                encoding="utf-8")
            old_home = os.environ.get("CODEX_HOME")
            old_token = os.environ.get("AI_PROVIDER_TEST_TOKEN")
            os.environ["CODEX_HOME"] = directory
            os.environ["AI_PROVIDER_TEST_TOKEN"] = "codex-secret"
            try:
                description = ai_provider.request_commands(action="describe")
                result = ai_provider.request_commands(self.request({"mode": "codex"}))
            finally:
                if old_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_home
                if old_token is None:
                    os.environ.pop("AI_PROVIDER_TEST_TOKEN", None)
                else:
                    os.environ["AI_PROVIDER_TEST_TOKEN"] = old_token

        self.assertEqual(description, {"ok": True, "provider": "local-provider",
                                       "model": "configured-model", "api": "responses",
                                       "base_url": self.origin + "/v1", "configured": True})
        self.assertEqual(result["provider"], "local-provider")
        self.assertEqual(result["model"], "configured-model")
        self.assertEqual(ProviderHandler.requests[-1][0], "/v1/responses")

    @unittest.skipIf(os.name == "nt", "non-Windows contract")
    def test_non_windows_refuses_persistent_api_key_storage(self):
        with self.assertRaisesRegex(OSError, "only for the current Windows user"):
            ai_provider.protect_api_key("session-key")
        with self.assertRaisesRegex(OSError, "only for the current Windows user"):
            ai_provider.unprotect_api_key("c2Vzc2lvbi1rZXk=")


if __name__ == "__main__":
    unittest.main()
