"""智能助手登录鉴权：签名 Cookie、未登录 401、登录失败限流。不调用真实 DSH。"""
import hashlib
import http.client
import importlib.util
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "assistant_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

PASSWORD = "test-password"


def post_json(conn, path, body=None):
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request("POST", path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    cookies = [v for k, v in response.getheaders() if k.lower() == "set-cookie"]
    return response.status, (json.loads(data) if data else {}), cookies


class AssistantAuthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.password_file = Path(self._tmp.name) / "password.sha256"
        self.password_file.write_text(hashlib.sha256(PASSWORD.encode()).hexdigest())
        self.secret_file = Path(self._tmp.name) / "assistant_secret"
        self._secret_patch = patch.object(server, "PASSWORD_FILE", self.password_file)
        self._secret_patch.start()
        self._secret_patch2 = patch.object(server, "ASSISTANT_SECRET_FILE", self.secret_file)
        self._secret_patch2.start()
        # 重置登录限流与任务状态（模块级可变量）
        server._login_guard.update(failures=0, locked_until=0.0)
        server._chat_state.update(state="idle", error=None, session_id=None,
                                  workflow=None, started_at=None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._secret_patch.stop()
        self._secret_patch2.stop()
        self._tmp.cleanup()

    def test_login_issues_cookie_and_protects_endpoints(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        # 未登录：助手数据端点 401
        conn.request("GET", "/api/v1/assistant/sessions")
        self.assertEqual(conn.getresponse().status, 401)
        # 登录状态查询是公开的
        conn.request("GET", "/api/v1/assistant/session")
        body = json.loads(conn.getresponse().read())
        self.assertFalse(body["authed"])
        # 错误密码 401
        status, body, _ = post_json(conn, "/api/v1/assistant/login", {"password": "wrong"})
        self.assertEqual(status, 401)
        # 正确密码 200 + Set-Cookie
        status, body, cookies = post_json(conn, "/api/v1/assistant/login",
                                          {"password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertTrue(any("assistant_session=" in c for c in cookies))
        self.assertTrue(any("HttpOnly" in c for c in cookies))
        cookie_value = [c.split(";")[0] for c in cookies
                        if c.startswith("assistant_session=")][0]
        # 带 Cookie 访问受保护端点 → 200（非 401）
        conn.request("GET", "/api/v1/assistant/sessions",
                     headers={"Cookie": cookie_value})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["sessions"], [])
        # 伪造 Cookie 401
        conn.request("GET", "/api/v1/assistant/sessions",
                     headers={"Cookie": "assistant_session=999999.deadbeef"})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()

    def test_login_lockout_after_failures(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        for _ in range(5):
            status, _, _ = post_json(conn, "/api/v1/assistant/login",
                                     {"password": "wrong"})
            self.assertEqual(status, 401)
        # 第 6 次：即使密码正确也应被锁定（429）
        status, body, _ = post_json(conn, "/api/v1/assistant/login",
                                    {"password": PASSWORD})
        self.assertEqual(status, 429)
        conn.close()

    def test_cookie_signature_and_expiry(self):
        expires = int(time.time()) + 60
        value = f"{expires}.{server._sign_session(expires)}"
        self.assertTrue(server.session_cookie_valid(value))
        self.assertFalse(server.session_cookie_valid("not-a-cookie"))
        self.assertFalse(server.session_cookie_valid(f"{expires}.bad"))
        expired = int(time.time()) - 10
        self.assertFalse(server.session_cookie_valid(
            f"{expired}.{server._sign_session(expired)}"))


if __name__ == "__main__":
    unittest.main()
