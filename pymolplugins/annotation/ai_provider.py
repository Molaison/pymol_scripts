"""One-request AI transport for Codex or an explicitly configured API."""

import base64
import binascii
import json
import os
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _api_base_url(base_url):
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Enter an HTTP(S) API URL without credentials, query, or fragment")
    for terminal in ("/chat/completions", "/responses"):
        if path.endswith(terminal):
            path = path[:-len(terminal)]
            break
    else:
        if path.endswith("/v1"):
            return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        raise ValueError("API URL must end in /v1 or an API endpoint")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _api_url(base_url, api):
    terminal = "/responses" if api == "responses" else "/chat/completions"
    return _api_base_url(base_url) + terminal


def _custom_connection(connection, require_configured):
    base_url = connection.get("base_url", "").strip()
    model = connection.get("model", "").strip()
    api_key = connection.get("api_key", "").strip()
    api = connection.get("api", "").strip()
    configured = bool(base_url and model and api_key and api in {"responses", "chat_completions"})
    if require_configured and not configured:
        raise ValueError(
            "Custom connection requires base URL, model, API key, and API type "
            "(responses or chat_completions)"
        )
    if base_url and api in {"responses", "chat_completions"}:
        _api_url(base_url, api)
    return {"provider": "custom", "model": model, "api": api,
            "base_url": base_url.rstrip("/"), "api_key": api_key,
            "configured": configured}


def _codex_connection(require_configured):
    import tomllib

    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        if not require_configured:
            return {"provider": "", "model": "", "api": "responses",
                    "base_url": "", "api_key": "", "configured": False}
        raise ValueError("Cannot read Codex config.toml; check CODEX_HOME and Codex setup") from exc

    provider_name = config.get("model_provider", "")
    model = config.get("model", "")
    providers = config.get("model_providers", {})
    provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
    api = provider.get("wire_api", "")
    base_url = provider.get("base_url", "")
    if api and api != "responses":
        raise ValueError("The configured Codex provider must use the Responses API")
    token = provider.get("experimental_bearer_token", "")
    env_key = provider.get("env_key", "")
    if not token and env_key:
        token = os.environ.get(env_key, "")
    if not token:
        try:
            token = json.loads((home / "auth.json").read_text(encoding="utf-8")).get("OPENAI_API_KEY", "")
        except (OSError, json.JSONDecodeError, AttributeError):
            token = ""

    configured = bool(provider_name and model and api == "responses" and base_url and token)
    if require_configured and not configured:
        if not provider_name or not model:
            raise ValueError("Codex config.toml must set model_provider and model")
        if not provider:
            raise ValueError("Codex config.toml has no settings for its selected provider")
        if not base_url:
            raise ValueError("The selected Codex provider has no base_url")
        raise ValueError("No API credential is available for the selected Codex provider")
    if base_url and api == "responses":
        _api_url(base_url, api)
    return {"provider": provider_name, "model": model, "api": "responses",
            "base_url": base_url.rstrip("/"), "api_key": token,
            "configured": configured}


def _connection(request, require_configured):
    connection = request.get("connection") or {}
    mode = connection.get("mode", "codex")
    if mode == "custom":
        return _custom_connection(connection, require_configured)
    if mode == "codex":
        return _codex_connection(require_configured)
    raise ValueError("Connection mode must be codex or custom")


def _describe(connection):
    return {"ok": True, "provider": connection["provider"],
            "model": connection["model"], "api": connection["api"],
            "base_url": connection["base_url"],
            "configured": connection["configured"]}


def _post_json(url, token, payload):
    http_request = Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        print("request_started", file=sys.stderr, flush=True)
        with build_opener(NoRedirect).open(http_request, timeout=120) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Provider response exceeded the size limit")
        return json.loads(raw)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError("Provider HTTP %d; existing editors unchanged" % code) from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Provider network connection failed or timed out; retry when reachable") from None
    except json.JSONDecodeError:
        raise ValueError("Provider returned an invalid JSON envelope") from None


def _get_models(base_url, token):
    request = Request(_api_base_url(base_url) + "/models", method="GET",
                      headers={"Accept": "application/json",
                               "Authorization": "Bearer " + token})
    try:
        with build_opener(NoRedirect).open(request, timeout=120) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Provider response exceeded the size limit")
        result = json.loads(raw)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError("Provider HTTP %d while listing models" % code) from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Provider network connection failed or timed out; retry when reachable") from None
    except json.JSONDecodeError:
        raise ValueError("Provider returned an invalid models envelope") from None
    data = result.get("data", []) if isinstance(result, dict) else []
    if not isinstance(data, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in data):
        raise ValueError("Provider returned an invalid models envelope")
    return {"ok": True, "models": [item["id"] for item in data]}


def _responses_payload(request, model):
    return {"model": model, "instructions": request["instructions"],
            "input": request["input"], "store": False, "stream": False,
            "max_output_tokens": 5000,
            "text": {"format": {"type": "json_schema", "name": "pymol_command_draft",
                                "strict": True, "schema": request["schema"]}}}


def _chat_payload(request, model):
    return {"model": model,
            "messages": [{"role": "system", "content": request["instructions"]},
                         {"role": "user", "content": request["input"]}],
            "stream": False, "max_tokens": 5000,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "pymol_command_draft",
                                                "strict": True,
                                                "schema": request["schema"]}}}


def _responses_result(result, connection):
    if not isinstance(result, dict):
        raise ValueError("Provider returned an invalid Responses envelope")
    if result.get("status") != "completed":
        raise ValueError("Provider response not completed (failed or truncated); no draft accepted")
    contents = [part for item in result.get("output", []) if item.get("type") == "message"
                for part in item.get("content", [])]
    if any(part.get("type") == "refusal" for part in contents):
        raise ValueError("Provider declined the request; no draft accepted")
    texts = [part["text"] for part in contents if part.get("type") == "output_text"]
    if len(texts) != 1:
        raise ValueError("Expected one complete command document from provider")
    return {"ok": True, "provider": connection["provider"], "model": connection["model"],
            "text": texts[0],
            "response_status": result["status"], "response_model": result.get("model"),
            "response_id": result.get("id"), "usage": result.get("usage", {})}


def _chat_result(result, connection):
    choices = result.get("choices", []) if isinstance(result, dict) else []
    if len(choices) != 1:
        raise ValueError("Expected one complete command document from provider")
    choice = choices[0]
    message = choice.get("message", {})
    if message.get("refusal"):
        raise ValueError("Provider declined the request; no draft accepted")
    if choice.get("finish_reason") != "stop" or not isinstance(message.get("content"), str):
        raise ValueError("Provider response not completed (failed or truncated); no draft accepted")
    return {"ok": True, "provider": connection["provider"], "model": connection["model"],
            "text": message["content"], "response_status": "completed",
            "response_model": result.get("model"), "response_id": result.get("id"),
            "usage": result.get("usage", {})}


def request_commands(request=None, **kwargs):
    """Run or describe a request; accepts the legacy request dict and keywords."""
    request = dict(request or {})
    request.update(kwargs)
    action = request.get("action", "request")
    if action == "describe":
        return _describe(_connection(request, require_configured=False))
    if action == "models":
        connection_data = request.get("connection") or {}
        if connection_data.get("mode", "codex") == "custom":
            connection = _custom_connection(connection_data, require_configured=False)
            if not (connection["base_url"] and connection["api_key"]
                    and connection["api"] in {"responses", "chat_completions"}):
                raise ValueError("Model refresh requires base URL, API key, and API type")
        else:
            connection = _connection(request, require_configured=True)
        return _get_models(connection["base_url"], connection["api_key"])
    if action != "request":
        raise ValueError("Action must be request, describe, or models")
    connection = _connection(request, require_configured=True)
    url = _api_url(connection["base_url"], connection["api"])
    if connection["api"] == "responses":
        result = _post_json(url, connection["api_key"],
                            _responses_payload(request, connection["model"]))
        return _responses_result(result, connection)
    result = _post_json(url, connection["api_key"],
                        _chat_payload(request, connection["model"]))
    return _chat_result(result, connection)


def _require_windows_dpapi():
    if os.name != "nt":
        raise OSError("Persistent API key storage is supported only for the current Windows user")


def _crypt_api_key(data, protect):
    _require_windows_dpapi()
    import ctypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32),
                    ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_wchar_p,
                         ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(DataBlob)]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def protect_api_key(secret):
    """Protect a key for the current Windows user and return portable text."""
    encrypted = _crypt_api_key(secret.encode("utf-8"), protect=True)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect_api_key(blob):
    """Restore a key protected for the current Windows user."""
    try:
        encrypted = base64.b64decode(blob.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        raise ValueError("Stored API key is not valid protected data") from None
    try:
        return _crypt_api_key(encrypted, protect=False).decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Stored API key is not valid protected data") from None


def main():
    try:
        request = json.loads(sys.stdin.readline())
        def watch_owner():
            os.read(sys.stdin.fileno(), 1)
            os._exit(0)
        threading.Thread(target=watch_owner, daemon=True).start()
        result = request_commands(request)
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception:
        result = {"ok": False, "error": "AI request failed; check the Python/provider setup"}
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
