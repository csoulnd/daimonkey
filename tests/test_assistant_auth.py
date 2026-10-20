"""账号登录鉴权:签名 Cookie 携带用户名、未登录 401、按用户名限流、多用户会话隔离。
不调用真实 pi。"""
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

import tests  # noqa: E402,F401  单测护栏:离线 + 快速 KDF


spec = importlib.util.spec_from_file_location(
    "assistant_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

from collector import users as acct  # noqa: E402

PASSWORD = "test-password-123"
ALICE, BOB = "alice", "bob"


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


def get_json(conn, path, cookie=None):
    headers = {"Cookie": cookie} if cookie else {}
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    data = response.read()
    return response.status, (json.loads(data) if data else {})


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
        # 数据隔离:临时库 + 建两个真实账号
        from collector import db as _db
        self._db_patch = patch.object(_db, "DB_PATH",
                                      Path(self._tmp.name) / "test.sqlite3")
        self._db_patch.start()
        with _db.conn() as c:
            _db.ensure_assistant_tables(c)
        self.assertTrue(acct.create_user(ALICE, PASSWORD))
        self.assertTrue(acct.create_user(BOB, PASSWORD))
        # 重置登录限流与任务状态(模块级可变量)
        server._login_guard.clear()
        server._chat_state.update(state="idle", error=None, session_id=None,
                                  workflow=None, started_at=None, reply=None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._secret_patch.stop()
        self._secret_patch2.stop()
        self._db_patch.stop()
        self._tmp.cleanup()

    def _login(self, conn, username=ALICE, password=PASSWORD):
        status, body, cookies = post_json(conn, "/api/v1/assistant/login",
                                          {"username": username,
                                           "password": password})
        self.assertEqual(status, 200, body)
        value = [c.split(";")[0] for c in cookies
                 if c.startswith("assistant_session=")][0]
        return value

    def test_login_issues_cookie_and_protects_endpoints(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        # 未登录:助手数据端点 401
        conn.request("GET", "/api/v1/assistant/sessions")
        self.assertEqual(conn.getresponse().status, 401)
        # 登录状态查询公开,且返回用户名
        status, body = get_json(conn, "/api/v1/assistant/session")
        self.assertFalse(body["authed"])
        self.assertIsNone(body["user"])
        # 错误密码 401
        status, body, _ = post_json(conn, "/api/v1/assistant/login",
                                    {"username": ALICE, "password": "wrong"})
        self.assertEqual(status, 401)
        # 正确账号 200 + Set-Cookie(HttpOnly)
        status, body, cookies = post_json(conn, "/api/v1/assistant/login",
                                          {"username": ALICE, "password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertEqual(body["user"], ALICE)
        self.assertTrue(any("assistant_session=" in c for c in cookies))
        self.assertTrue(any("HttpOnly" in c for c in cookies))
        cookie_value = cookies and [c.split(";")[0] for c in cookies
                                    if c.startswith("assistant_session=")][0]
        # 带 Cookie 访问受保护端点 → 200,且 Cookie 携带用户名
        status, body = get_json(conn, "/api/v1/assistant/sessions", cookie_value)
        self.assertEqual(status, 200)
        self.assertEqual(body["sessions"], [])
        self.assertEqual(server.session_user(cookie_value.split("=", 1)[1]), ALICE)
        # 伪造 Cookie 401
        conn.request("GET", "/api/v1/assistant/sessions",
                     headers={"Cookie": "assistant_session=alice.999999.deadbeef"})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()

    def test_disabled_user_cookie_rejected(self):
        """Cookie 签名有效但账号已禁用/删除 → 立即失效（不等 7 天）。"""
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        cookie = self._login(conn)
        self.assertTrue(acct.set_enabled(ALICE, False))
        status, body = get_json(conn, "/api/v1/assistant/session", cookie)
        self.assertFalse(body["authed"])
        conn.request("GET", "/api/v1/assistant/sessions",
                     headers={"Cookie": cookie})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()

    def test_login_lockout_is_per_username(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        for _ in range(5):
            status, _, _ = post_json(conn, "/api/v1/assistant/login",
                                     {"username": ALICE, "password": "wrong"})
            self.assertEqual(status, 401)
        # 第 6 次:alice 即使密码正确也被锁定
        status, body, _ = post_json(conn, "/api/v1/assistant/login",
                                    {"username": ALICE, "password": PASSWORD})
        self.assertEqual(status, 429)
        # bob 不受 alice 的锁定影响
        status, body, _ = post_json(conn, "/api/v1/assistant/login",
                                    {"username": BOB, "password": PASSWORD})
        self.assertEqual(status, 200)
        conn.close()

    def test_cookie_signature_and_expiry(self):
        expires = int(time.time()) + 60
        value = f"{ALICE}.{expires}.{server._sign_session(ALICE, expires)}"
        self.assertEqual(server.session_user(value), ALICE)
        self.assertIsNone(server.session_user("not-a-cookie"))
        self.assertIsNone(server.session_user(f"{ALICE}.{expires}.bad"))
        # 签名不覆盖用户名 = 可替换身份,必须失败
        bob_sig = server._sign_session(BOB, expires)
        self.assertIsNone(server.session_user(f"{ALICE}.{expires}.{bob_sig}"))
        expired = int(time.time()) - 10
        self.assertIsNone(server.session_user(
            f"{ALICE}.{expired}.{server._sign_session(ALICE, expired)}"))

    def test_sessions_isolated_per_user(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = self._login(conn, ALICE)
        bob = self._login(conn, BOB)
        # alice 建会话
        conn.request("POST", "/api/v1/assistant/sessions",
                     body=json.dumps({"title": "爱丽丝的会话"}).encode(),
                     headers={"Content-Type": "application/json", "Cookie": alice})
        response = conn.getresponse()
        status, body = response.status, json.loads(response.read())
        self.assertEqual(status, 201)
        sid = body["session_id"]
        # bob 的会话列表看不到 alice 的会话
        status, body = get_json(conn, "/api/v1/assistant/sessions", bob)
        self.assertEqual(body["sessions"], [])
        # bob 读不到 alice 会话的消息
        status, body = get_json(conn, f"/api/v1/assistant/sessions/{sid}/messages", bob)
        self.assertEqual(body["messages"], [])
        # bob 改名/删除 alice 的会话失败
        conn.request("POST", f"/api/v1/assistant/sessions/{sid}/rename",
                     body=json.dumps({"title": "抢注"}).encode(),
                     headers={"Content-Type": "application/json", "Cookie": bob})
        self.assertEqual(conn.getresponse().status, 404)
        conn.request("DELETE", f"/api/v1/assistant/sessions/{sid}",
                     headers={"Cookie": bob})
        self.assertEqual(conn.getresponse().status, 404)
        # alice 自己能看到并删除
        status, body = get_json(conn, "/api/v1/assistant/sessions", alice)
        self.assertEqual(len(body["sessions"]), 1)
        conn.request("DELETE", f"/api/v1/assistant/sessions/{sid}",
                     headers={"Cookie": alice})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_legacy_password_transition(self):
        """无账号期间旧单一密码可登录(legacy);建号后旧密码立即失效。"""
        # 临时清空账号:换一个空库
        from collector import db as _db
        empty = Path(self._tmp.name) / "empty.sqlite3"
        with patch.object(_db, "DB_PATH", empty):
            with _db.conn() as c:
                _db.ensure_assistant_tables(c)
            self.assertFalse(acct.has_any_user())
            conn = http.client.HTTPConnection(*self.httpd.server_address)
            status, body, cookies = post_json(conn, "/api/v1/assistant/login",
                                              {"password": PASSWORD})
            self.assertEqual(status, 200)
            self.assertEqual(body["user"], "legacy")
            # 错误旧密码仍拒绝
            status, _, _ = post_json(conn, "/api/v1/assistant/login",
                                     {"password": "wrong"})
            self.assertEqual(status, 401)
            conn.close()
        # 主库(已有账号):仅密码登录不再放行
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        status, body, _ = post_json(conn, "/api/v1/assistant/login",
                                    {"password": PASSWORD})
        self.assertEqual(status, 401)
        conn.close()


if __name__ == "__main__":
    unittest.main()
