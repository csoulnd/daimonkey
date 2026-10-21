"""serve.py HTTP/1.1 行为测试:响应行、keep-alive、healthz、404、Cookie、304。

全部起临时实例(127.0.0.1:0),使用临时 dist 与临时库,
不连接 8080 生产服务,也不读写生产 data/ 与 dist/。
"""
import http.client
import importlib.util
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

MARKER_404 = b"<!doctype html><title>page-not-found-marker</title>"


def load_server():
    spec = importlib.util.spec_from_file_location(
        "serve_http_server",
        Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_server()


class _QuietHandler(SERVER.Handler):
    """静音请求日志,保持测试输出干净。"""

    def log_message(self, fmt, *args):
        pass


@contextmanager
def secure_cookies(value):
    """临时设置 DM_SECURE_COOKIES;value=None 表示确保未设置。"""
    with patch.dict(os.environ, {}, clear=False):
        if value is None:
            os.environ.pop("DM_SECURE_COOKIES", None)
        else:
            os.environ["DM_SECURE_COOKIES"] = value
        yield


class ServeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dist = Path(self.tmp.name) / "dist"
        self.dist.mkdir()
        self.index_body = b"<h1>home-marker</h1>"
        (self.dist / "index.html").write_bytes(self.index_body)
        (self.dist / "404.html").write_bytes(MARKER_404)
        from collector import db as collector_db
        self.db = collector_db
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        sqlite3.connect(self.db_path).close()   # 空库:健康探活要求库文件存在
        self._patches = [
            patch.object(SERVER, "DIST", self.dist),
            # 测试必须隔离 DB,严禁碰生产库(HANDOVER 事故教训)
            patch.object(collector_db, "DB_PATH", self.db_path),
            # 签名密钥也落临时目录,避免首次使用时写生产 data/
            patch.object(SERVER, "ASSISTANT_SECRET_FILE",
                         Path(self.tmp.name) / "assistant_secret"),
        ]
        for item in self._patches:
            item.start()
        SERVER._login_guard.clear()
        with SERVER._rate_lock:
            SERVER._rate_buckets.clear()   # 限流桶按用例复位,避免互相挤占

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self.tmp.cleanup()

    # ---- 工具 ----

    def connect(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        self.addCleanup(conn.close)
        return conn

    def request(self, conn, path, method="GET", headers=None, body=None):
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response, response.read()

    def _login(self, conn):
        """走过渡期"仅密码"登录分支(账号校验被 mock,不碰真实账号库)。"""
        with patch.object(SERVER, "password_valid", return_value=True), \
                patch.object(SERVER.users, "has_any_user", return_value=False):
            return self.request(
                conn, "/api/v1/assistant/login", method="POST",
                body=json.dumps({"username": "", "password": "test-pass"}),
                headers={"Content-Type": "application/json"})

    @staticmethod
    def _read_raw_response(sock):
        """读一个完整响应(头 + 按 Content-Length 的体),返回头部字节。"""
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise AssertionError("连接在响应头前关闭")
            buf += chunk
        head, _, body = buf.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        while len(body) < length:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
        return head

    # ---- HTTP/1.1 与 keep-alive ----

    def test_http11_status_line(self):
        sock = socket.create_connection(self.httpd.server_address, timeout=10)
        try:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n"
                         b"Connection: close\r\n\r\n")
            handle = sock.makefile("rb")
            try:
                status_line = handle.readline()
            finally:
                handle.close()
        finally:
            sock.close()
        self.assertTrue(status_line.startswith(b"HTTP/1.1 "), status_line)

    def test_keepalive_reuses_one_connection(self):
        conn = self.connect()
        first, body1 = self.request(conn, "/healthz")
        self.assertEqual(first.status, 200)
        self.assertEqual(first.version, 11)
        self.assertEqual(int(first.getheader("Content-Length")), len(body1))
        sock = conn.sock
        second, body2 = self.request(conn, "/")
        self.assertEqual(second.status, 200)
        self.assertIs(conn.sock, sock)   # 同一条 TCP 连接复用
        self.assertEqual(int(second.getheader("Content-Length")), len(body2))
        self.assertNotIn("close", (second.getheader("Connection") or "").lower())

    def test_head_has_length_but_no_body(self):
        conn = self.connect()
        response, body = self.request(conn, "/index.html", method="HEAD")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(response.getheader("Content-Length")),
                         len(self.index_body))

    def test_not_modified_has_length_and_no_body(self):
        conn = self.connect()
        response, _ = self.request(conn, "/index.html")
        etag = response.getheader("ETag")
        self.assertTrue(etag)
        response, body = self.request(conn, "/index.html",
                                      headers={"If-None-Match": etag})
        self.assertEqual(response.status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(int(response.getheader("Content-Length")),
                         len(self.index_body))
        # 304 之后连接仍可复用(keep-alive 未被打断)
        response, _ = self.request(conn, "/index.html")
        self.assertEqual(response.status, 200)

    def test_post_with_unread_body_closes_connection(self):
        """请求体未读的提前返回(404/413/429/503)必须声明关闭,避免协议错位。"""
        conn = self.connect()
        response, body = self.request(
            conn, "/api/v1/definitely-not-there", method="POST",
            body=json.dumps({"x": 1}),
            headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 404)
        self.assertEqual(json.loads(body)["error"], "not found")
        self.assertEqual((response.getheader("Connection") or "").lower(), "close")

    def test_idle_keepalive_connection_closed_by_timeout(self):
        """空闲 keep-alive 连接必须被服务端按 timeout 关闭(释放线程)。"""
        # 防回退:每个空闲连接占 1 线程,空闲超时保持收紧后的 10s
        self.assertEqual(SERVER.Handler.timeout, 10)
        with patch.object(SERVER.Handler, "timeout", 0.3):
            sock = socket.create_connection(self.httpd.server_address, timeout=5)
            try:
                sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: test\r\n\r\n")
                head = self._read_raw_response(sock)
                self.assertIn(b"HTTP/1.1 200", head)
                time.sleep(0.8)          # 超过被缩短的 0.3s 空闲超时
                sock.settimeout(3)
                self.assertEqual(sock.recv(1), b"")   # EOF:服务端已关闭连接
            finally:
                sock.close()

    def test_server_uses_daemon_threads(self):
        """keep-alive 线程必须是 daemon,退出时不被连接拖住。"""
        self.assertTrue(self.httpd.daemon_threads)

    # ---- /healthz ----

    def test_healthz_ok_payload(self):
        conn = self.connect()
        response, body = self.request(conn, "/healthz")
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("X-Robots-Tag"))  # 免登录,不是 /api/*
        data = json.loads(body)
        self.assertEqual(set(data), {"ok", "uptime_s", "db", "dist",
                                     "version", "time"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["db"], "ok")
        self.assertEqual(data["dist"], "ok")
        self.assertIsInstance(data["uptime_s"], int)
        self.assertGreaterEqual(data["uptime_s"], 0)
        self.assertTrue(data["time"].endswith("Z"))
        self.assertTrue(data["version"])

    def test_healthz_503_when_dist_missing(self):
        empty = Path(self.tmp.name) / "empty-dist"
        empty.mkdir()
        conn = self.connect()
        with patch.object(SERVER, "DIST", empty):
            response, body = self.request(conn, "/healthz")
        self.assertEqual(response.status, 503)
        data = json.loads(body)
        self.assertFalse(data["ok"])
        self.assertEqual(data["dist"], "missing")
        self.assertEqual(data["db"], "ok")

    def test_healthz_503_when_db_unavailable(self):
        conn = self.connect()
        with patch.object(self.db, "conn",
                          side_effect=sqlite3.OperationalError("db down")):
            response, body = self.request(conn, "/healthz")
        self.assertEqual(response.status, 503)
        data = json.loads(body)
        self.assertFalse(data["ok"])
        self.assertEqual(data["db"], "error")
        self.assertEqual(data["dist"], "ok")
        # 库文件不存在时直接判 error,不能靠 connect 顺手建空库
        missing = Path(self.tmp.name) / "missing.sqlite3"
        with patch.object(self.db, "DB_PATH", missing):
            response, body = self.request(conn, "/healthz")
        self.assertEqual(response.status, 503)
        self.assertEqual(json.loads(body)["db"], "error")
        self.assertFalse(missing.exists())

    # ---- 404 ----

    def test_html_404_uses_page_with_404_status(self):
        conn = self.connect()
        response, body = self.request(conn, "/no-such-page")
        self.assertEqual(response.status, 404)   # 不是 200
        self.assertTrue(response.getheader("Content-Type").startswith("text/html"))
        self.assertEqual(body, MARKER_404)
        self.assertEqual(int(response.getheader("Content-Length")),
                         len(MARKER_404))
        self.assertEqual(response.getheader("X-Frame-Options"), "SAMEORIGIN")

    def test_non_api_404_for_post_and_delete_is_html(self):
        conn = self.connect()
        for method in ("POST", "DELETE"):
            response, body = self.request(conn, "/no-such-page", method=method)
            self.assertEqual(response.status, 404, method)
            self.assertTrue(response.getheader("Content-Type")
                            .startswith("text/html"), method)
            self.assertEqual(body, MARKER_404, method)

    def test_api_404_stays_json(self):
        conn = self.connect()
        response, body = self.request(conn, "/api/v1/no-such-endpoint")
        self.assertEqual(response.status, 404)
        self.assertTrue(response.getheader("Content-Type")
                        .startswith("application/json"))
        self.assertEqual(json.loads(body)["error"], "not found")

    def test_html_404_falls_back_to_json_without_page(self):
        (self.dist / "404.html").unlink()
        conn = self.connect()
        response, body = self.request(conn, "/no-such-page")
        self.assertEqual(response.status, 404)
        self.assertTrue(response.getheader("Content-Type")
                        .startswith("application/json"))
        self.assertEqual(json.loads(body)["error"], "not found")

    # ---- 旧路由 301 ----

    def test_legacy_redirects_301_and_location(self):
        conn = self.connect()
        expected = {
            "/rankings/unified": "/dir/harness",
            "/dir/harness/board": "/dir/harness",
            "/dir/harness/board.html": "/dir/harness",
            "/dir/fm/news.html": "/dir/fm/news",
            "/dir/fm/news/": "/dir/fm/news",
            "/dir/nv/digest.html": "/dir/nv/digest",
            "/dir/nv/digest/": "/dir/nv/digest",
        }
        for old, new in expected.items():
            response, body = self.request(conn, old)
            self.assertEqual(response.status, 301, old)
            self.assertEqual(response.getheader("Location"), new, old)
            self.assertEqual(int(response.getheader("Content-Length")),
                             len(body), old)

    def test_single_digest_week_page_is_not_redirected(self):
        week = self.dist / "dir" / "harness" / "digest" / "2026-W42.html"
        week.parent.mkdir(parents=True)
        week_body = "<h1>weekly-report</h1>".encode("utf-8")
        week.write_bytes(week_body)
        conn = self.connect()
        response, body = self.request(conn, "/dir/harness/digest/2026-W42.html")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, week_body)

    # ---- 安全头与版本隐藏 ----

    def test_html_security_headers_and_hidden_version(self):
        conn = self.connect()
        response, _ = self.request(conn, "/")
        self.assertEqual(response.getheader("Referrer-Policy"),
                         "strict-origin-when-cross-origin")
        self.assertEqual(response.getheader("Permissions-Policy"),
                         "geolocation=(), microphone=(), camera=()")
        self.assertEqual(response.getheader("X-Frame-Options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'",
                      response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("Server"), "daimonkey")

    # ---- Cookie Secure 开关 ----

    def test_cookie_secure_toggle(self):
        conn = self.connect()
        with secure_cookies(None):
            response, _ = self.request(conn, "/api/v1/assistant/logout",
                                       method="POST")
            self.assertEqual(response.status, 200)
            cookie = response.getheader("Set-Cookie")
            self.assertIn("Max-Age=0", cookie)
            self.assertNotIn("Secure", cookie)
        with secure_cookies("1"):
            response, _ = self._login(conn)
            self.assertEqual(response.status, 200)
            self.assertIn("Secure", response.getheader("Set-Cookie"))
            response, _ = self.request(conn, "/api/v1/assistant/logout",
                                       method="POST")
            self.assertIn("Secure", response.getheader("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()
