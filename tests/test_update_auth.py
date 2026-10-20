"""验证更新接口的账号鉴权(X-Update-Password 已移除,登录 Cookie 完全权限);不启动真实采集。"""
import importlib.util
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import tests  # noqa: E402,F401  单测护栏:离线 + 快速 KDF


spec = importlib.util.spec_from_file_location(
    "update_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

USER = "updator"
PASSWORD = "test-password"


def _account_env(directory: str):
    """临时库 + 一个真实账号,返回 db patch 上下文。"""
    from collector import db as _db
    patcher = patch.object(_db, "DB_PATH", Path(directory) / "test.sqlite3")
    return patcher


class UpdateAuthTests(unittest.TestCase):
    def test_password_header_removed_and_cookie_required(self):
        """X-Update-Password 路径已死:任何密码头都 401;只有合法账号 Cookie 放行。"""
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "assistant_secret"
            running = Mock()
            running.poll.return_value = None
            db_patch = _account_env(directory)
            db_patch.start()
            try:
                from collector import db as _db, users as _users
                with _db.conn() as c:
                    _db.ensure_assistant_tables(c)
                self.assertTrue(_users.create_user(USER, PASSWORD))
                with patch.object(server, "ASSISTANT_SECRET_FILE", secret_file), \
                        patch.dict(server._proc, {"running": running}), \
                        patch.object(server.subprocess, "Popen") as spawn:
                    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                    thread.start()
                    try:
                        cookie = (f"assistant_session="
                                  f"{server.make_cookie_value(USER)}")
                        cases = [
                            ("/api/v1/update", {}, 401),
                            ("/api/v1/update", {"X-Update-Password": PASSWORD}, 401),
                            ("/api/v1/update", {"X-Update-Password": "wrong"}, 401),
                            ("/api/v1/update", {"Cookie": cookie}, 409),
                        ]
                        for path, headers, expected in cases:
                            with self.subTest(headers=headers, path=path):
                                conn = HTTPConnection(*httpd.server_address)
                                conn.request("POST", path, headers=headers)
                                response = conn.getresponse()
                                self.assertEqual(response.status, expected)
                                response.read()
                                conn.close()
                        spawn.assert_not_called()
                    finally:
                        httpd.shutdown()
                        httpd.server_close()
                        thread.join()
            finally:
                db_patch.stop()

    def test_knowledge_update_spawns_sync_script(self):
        """知识库按钮:202 且子进程命令指向 scripts/sync_knowledge.py。"""
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "assistant_secret"
            fake_root = Path(directory) / "root"
            db_patch = _account_env(directory)
            db_patch.start()
            try:
                from collector import db as _db, users as _users
                with _db.conn() as c:
                    _db.ensure_assistant_tables(c)
                self.assertTrue(_users.create_user(USER, PASSWORD))
                with patch.object(server, "ASSISTANT_SECRET_FILE", secret_file), \
                        patch.object(server, "ROOT", fake_root), \
                        patch.dict(server._proc, {"running": None, "kind": None}), \
                        patch.object(server.subprocess, "Popen") as spawn:
                    spawn.return_value = Mock()
                    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                    thread.start()
                    try:
                        conn = HTTPConnection(*httpd.server_address)
                        conn.request("POST", "/api/v1/update/knowledge",
                                     headers={"Cookie":
                                              f"assistant_session={server.make_cookie_value(USER)}"})
                        response = conn.getresponse()
                        self.assertEqual(response.status, 202)
                        response.read()
                        conn.close()
                    finally:
                        httpd.shutdown()
                        httpd.server_close()
                        thread.join()
                    spawn.assert_called_once()
                    cmd = spawn.call_args[0][0]
                    self.assertEqual(cmd[0], sys.executable)
                    self.assertTrue(str(cmd[1]).endswith("sync_knowledge.py"),
                                    f"应按知识库脚本执行: {cmd}")
            finally:
                db_patch.stop()

    def test_status_note_by_kind(self):
        """运行中状态按 kind 标注（知识库/新闻/全量各自的提示语）。"""
        running = Mock()
        running.poll.return_value = None
        cases = [("knowledge", "知识库"), ("news", "新闻"), ("full", "采集"), (None, "采集")]
        for kind, expect in cases:
            with self.subTest(kind=kind), \
                    patch.dict(server._proc, {"running": running, "kind": kind}):
                s = server.status_payload()
                self.assertEqual(s["state"], "running")
                self.assertIn(expect, s["note"])

    def test_notes_page_requires_login(self):
        """讲稿页（/notes/）不登录 401 提示页；合法账号 Cookie 才可读。"""
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "assistant_secret"
            fake_dist = Path(directory) / "dist"
            (fake_dist / "notes").mkdir(parents=True)
            (fake_dist / "notes" / "demo.html").write_text(
                "<html>讲稿正文</html>", encoding="utf-8")
            db_patch = _account_env(directory)
            db_patch.start()
            try:
                from collector import db as _db, users as _users
                with _db.conn() as c:
                    _db.ensure_assistant_tables(c)
                self.assertTrue(_users.create_user(USER, PASSWORD))
                with patch.object(server, "ASSISTANT_SECRET_FILE", secret_file), \
                        patch.object(server, "DIST", fake_dist):
                    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                    thread.start()
                    try:
                        # 未登录 → 401 提示页（含 noindex，不含讲稿内容）
                        conn = HTTPConnection(*httpd.server_address)
                        conn.request("GET", "/notes/demo")
                        response = conn.getresponse()
                        body = response.read().decode("utf-8")
                        self.assertEqual(response.status, 401)
                        self.assertIn("需要登录", body)
                        self.assertNotIn("讲稿正文", body)
                        self.assertEqual(response.getheader("X-Robots-Tag"), "noindex")
                        conn.close()
                        # 合法账号 Cookie → 200 内容 + noindex
                        cookie = (f"assistant_session="
                                  f"{server.make_cookie_value(USER)}")
                        conn = HTTPConnection(*httpd.server_address)
                        conn.request("GET", "/notes/demo", headers={"Cookie": cookie})
                        response = conn.getresponse()
                        body = response.read().decode("utf-8")
                        self.assertEqual(response.status, 200)
                        self.assertIn("讲稿正文", body)
                        self.assertEqual(response.getheader("X-Robots-Tag"), "noindex")
                        conn.close()
                    finally:
                        httpd.shutdown()
                        httpd.server_close()
                        thread.join()
            finally:
                db_patch.stop()

    def test_assistant_session_cookie_grants_update(self):
        """一次登录完全权限:合法账号 Cookie 可直接触发更新(免密码)。"""
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "assistant_secret"
            running = Mock()
            running.poll.return_value = None
            db_patch = _account_env(directory)
            db_patch.start()
            try:
                from collector import db as _db, users as _users
                with _db.conn() as c:
                    _db.ensure_assistant_tables(c)
                self.assertTrue(_users.create_user(USER, PASSWORD))
                with patch.object(server, "ASSISTANT_SECRET_FILE", secret_file), \
                        patch.dict(server._proc, {"running": running}), \
                        patch.object(server.subprocess, "Popen") as spawn:
                    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                    thread.start()
                    try:
                        cookie = (f"assistant_session="
                                  f"{server.make_cookie_value(USER)}")
                        # 合法 Cookie、无密码头 -> 通过鉴权（因 running mock 返回 409）
                        conn = HTTPConnection(*httpd.server_address)
                        conn.request("POST", "/api/v1/update",
                                     headers={"Cookie": cookie})
                        self.assertEqual(conn.getresponse().status, 409)
                        conn.close()
                        # 伪造 Cookie(改用户名) -> 401
                        conn = HTTPConnection(*httpd.server_address)
                        conn.request("POST", "/api/v1/update",
                                     headers={"Cookie": "assistant_session=admin.1.bad"})
                        self.assertEqual(conn.getresponse().status, 401)
                        conn.close()
                        spawn.assert_not_called()
                    finally:
                        httpd.shutdown()
                        httpd.server_close()
                        thread.join()
            finally:
                db_patch.stop()


if __name__ == "__main__":
    unittest.main()
