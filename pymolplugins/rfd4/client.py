"""Small, dependency-free client for Studio's versioned PyMOL surface."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ApiError(RuntimeError):
    def __init__(self, message, status=None, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never forward the bearer token to a login redirect.
        return None


class ApiClient:
    def __init__(self, base_url, token="", timeout=45):
        self.base_url = base_url.strip().rstrip("/")
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("服务地址须为 HTTP(S) 根地址，不能包含账号、密码或查询参数。")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("远程连接请使用 HTTPS；SSH 转发请填写 http://127.0.0.1:端口。")
        self.token = token.strip()
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def request(self, method, path, body=None, text=False):
        if not path.startswith("/api/"):
            raise ValueError("Invalid Studio API path")
        headers = {"Accept": "text/plain" if text else "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read(128 * 1024 * 1024 + 1)
                if len(payload) > 128 * 1024 * 1024:
                    raise ApiError("服务响应过大，请减少本批设计数量。")
        except HTTPError as error:
            raw = error.read(64 * 1024)
            try:
                detail = json.loads(raw).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if error.code in (401, 403):
                message = "认证失败，请检查连接设置中的访问令牌。"
            elif error.code == 404:
                message = "接口或任务不存在，可能服务尚未更新或会话已经过期。"
            elif isinstance(detail, dict):
                message = detail.get("message") or json.dumps(detail, ensure_ascii=False)
            elif detail:
                message = str(detail)
            else:
                message = "服务返回 HTTP %s。" % error.code
            raise ApiError(message, error.code, detail) from None
        except (URLError, TimeoutError, OSError) as error:
            raise ApiError("连接中断或超时。已提交任务请用原任务 ID 查询，勿重复提交。"
                           " (%s)" % type(error).__name__) from None
        try:
            decoded = payload.decode("utf-8")
            return decoded if text else json.loads(decoded)
        except (UnicodeError, ValueError):
            raise ApiError("服务未返回有效数据，请确认连接到已启用 PyMOL API 的 Studio。") from None

    def capabilities(self):
        data = self.request("GET", "/api/pymol/capabilities")
        if data.get("protocol") != 1:
            raise ApiError("PyMOL API 版本不兼容，请同步更新插件和服务。")
        return data

    def create_session(self, **payload):
        return self.request("POST", "/api/pymol/sessions", payload)

    def view(self, session_id):
        return self.request("GET", "/api/pymol/sessions/%s/view" % session_id)

    def edit(self, session_id, revision, operation, payload):
        return self.request("POST", "/api/pymol/sessions/%s/edit" % session_id,
                            {"revision": revision, "operation": operation, "payload": payload})

    def cif(self, session_id):
        return self.request("GET", "/api/sessions/%s/cif" % session_id, text=True)

    def submit(self, payload):
        return self.request("POST", "/api/pymol/jobs", payload)

    def job(self, job_id):
        return self.request("GET", "/api/pymol/jobs/%s" % job_id)

    def cancel(self, job_id):
        return self.request("POST", "/api/pymol/jobs/%s/cancel" % job_id, {})
