"""云剪贴板:鉴权 401、文本存取删与滚动清理、文件上传/下载/删除、
超限 413、配额 507、用户隔离。不依赖 dist 构建产物。"""
import hashlib
import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import tests  # noqa: E402,F401  单测护栏:离线 + 快速 KDF


spec = importlib.util.spec_from_file_location(
    "clipboard_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

from collector import clipboard as clip  # noqa: E402
from collector import users as acct  # noqa: E402

PASSWORD = "test-password-123"
ALICE, BOB = "alice", "bob"


def post_json(conn, path, body=None, cookie=None, headers=None):
    hs = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hs["Content-Type"] = "application/json"
    if cookie:
        hs["Cookie"] = cookie
    conn.request("POST", path, body=payload, headers=hs)
    response = conn.getresponse()
    data = response.read()
    cookies = [v for k, v in response.getheaders() if k.lower() == "set-cookie"]
    return response.status, (json.loads(data) if data else {}), cookies


def get_json(conn, path, cookie=None):
    hs = {"Cookie": cookie} if cookie else {}
    conn.request("GET", path, headers=hs)
    response = conn.getresponse()
    data = response.read()
    return response.status, (json.loads(data) if data else {})


def login(conn, username=ALICE, password=PASSWORD):
    status, _, cookies = post_json(conn, "/api/v1/assistant/login",
                                   {"username": username, "password": password})
    assert status == 200, f"login failed: {status}"
    return [c.split(";")[0] for c in cookies
            if c.startswith("assistant_session=")][0]


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.password_file = Path(self._tmp.name) / "password.sha256"
        self.password_file.write_text(hashlib.sha256(PASSWORD.encode()).hexdigest())
        self._secret_patch = patch.object(server, "PASSWORD_FILE", self.password_file)
        self._secret_patch.start()
        self._secret_patch2 = patch.object(
            server, "ASSISTANT_SECRET_FILE", Path(self._tmp.name) / "assistant_secret")
        self._secret_patch2.start()
        # 数据隔离:临时库 + 临时文件目录(clipboard 模块两处路径常量)
        from collector import db as _db
        self._db_patch = patch.object(_db, "DB_PATH",
                                      Path(self._tmp.name) / "test.sqlite3")
        self._db_patch.start()
        self._dir_patch = patch.object(clip, "DATA_DIR", Path(self._tmp.name))
        self._dir_patch.start()
        self._cdir_patch = patch.object(clip, "CLIPBOARD_DIR",
                                        Path(self._tmp.name) / "clipboard")
        self._cdir_patch.start()
        with _db.conn() as c:
            _db.ensure_assistant_tables(c)
            _db.ensure_clipboard_tables(c)
        self.assertTrue(acct.create_user(ALICE, PASSWORD))
        self.assertTrue(acct.create_user(BOB, PASSWORD))
        server._login_guard.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._secret_patch.stop()
        self._secret_patch2.stop()
        self._db_patch.stop()
        self._dir_patch.stop()
        self._cdir_patch.stop()
        self._tmp.cleanup()

    def _post_file(self, conn, cookie, name, data: bytes, content_type="application/octet-stream"):
        from urllib.parse import quote
        conn.request("POST", "/api/v1/clipboard/files", body=data, headers={
            "Cookie": cookie,
            "Content-Type": content_type,
            "X-Filename": quote(name),
        })
        response = conn.getresponse()
        body = response.read()
        return response.status, (json.loads(body) if body else {}), response

    # ---- 鉴权 ----

    def test_anonymous_gets_401_everywhere(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        self.assertEqual(get_json(conn, "/api/v1/clipboard/items")[0], 401)
        self.assertEqual(get_json(conn, "/api/v1/clipboard/usage")[0], 401)
        self.assertEqual(get_json(conn, "/api/v1/clipboard/files/1")[0], 401)
        status, body, _ = post_json(conn, "/api/v1/clipboard/items", {"content": "x"})
        self.assertEqual(status, 401)
        status, body, _ = self._post_file(conn, "assistant_session=bogus", "a.txt", b"xx")
        self.assertEqual(status, 401)
        conn.close()

    def test_page_gate_anonymous(self):
        """静态页 /clipboard 未登录返回 401 登录提示(不依赖 dist 内容)。"""
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        conn.request("GET", "/clipboard")
        response = conn.getresponse()
        self.assertEqual(response.status, 401)
        self.assertIn("需要登录", response.read().decode("utf-8"))
        conn.close()

    # ---- 文本 ----

    def test_text_roundtrip_dedupe_delete(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn)
        status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                    {"content": "hello 剪贴板"}, cookie=alice)
        self.assertEqual(status, 201, body)
        first_id = body["item"]["id"]
        # 连续相同内容去重:复用同一条
        status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                    {"content": "hello 剪贴板"}, cookie=alice)
        self.assertEqual(status, 201)
        self.assertEqual(body["item"]["id"], first_id)
        # 新内容新条目
        status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                    {"content": "second"}, cookie=alice)
        second_id = body["item"]["id"]
        self.assertNotEqual(second_id, first_id)
        status, body = get_json(conn, "/api/v1/clipboard/items", alice)
        self.assertEqual(status, 200)
        self.assertEqual([i["content"] for i in body["items"]], ["second", "hello 剪贴板"])
        # 空内容 400
        status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                    {"content": "   "}, cookie=alice)
        self.assertEqual(status, 400)
        # 删除
        conn.request("DELETE", f"/api/v1/clipboard/items/{first_id}",
                     headers={"Cookie": alice})
        self.assertEqual(conn.getresponse().status, 200)
        status, body = get_json(conn, "/api/v1/clipboard/items", alice)
        self.assertEqual([i["id"] for i in body["items"]], [second_id])
        conn.close()

    def test_text_too_large_413(self):
        """超过 max_text_bytes 的文本 413;超过请求体上限(64KB)的更早被 400 拒绝。"""
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn)
        with patch.object(clip, "cfg", lambda: {
                "max_text_bytes": 10, "max_file_bytes": 1024,
                "quota_mb": 10, "keep_text_items": 100}):
            status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                        {"content": "x" * 20}, cookie=alice)
            self.assertEqual(status, 413)
        status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                    {"content": "x" * 70000}, cookie=alice)
        self.assertEqual(status, 400)
        conn.close()

    def test_text_rolling_cleanup(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn)
        with patch.object(clip, "cfg", lambda: {
                "max_text_bytes": 65536, "max_file_bytes": 1024,
                "quota_mb": 10, "keep_text_items": 3}):
            for i in range(5):
                post_json(conn, "/api/v1/clipboard/items",
                          {"content": f"clip-{i}"}, cookie=alice)
            status, body = get_json(conn, "/api/v1/clipboard/items", alice)
            contents = [i["content"] for i in body["items"]]
        self.assertEqual(contents, ["clip-4", "clip-3", "clip-2"])
        conn.close()

    # ---- 文件 ----

    def test_file_upload_download_delete(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn)
        data = "中文内容 binary-safe \x00\x01".encode("utf-8") * 100
        status, body, _ = self._post_file(conn, alice, "笔记 2026.md", data)
        self.assertEqual(status, 201, body)
        item = body["item"]
        self.assertEqual(item["file_name"], "笔记 2026.md")
        self.assertEqual(item["file_bytes"], len(data))
        fid = item["id"]
        # 落盘在临时目录的 owner 子目录内(不逃逸)
        files = list((Path(self._tmp.name) / "clipboard").rglob("*"))
        self.assertTrue(any(f.is_file() and not f.name.startswith(".tmp")
                            for f in files))
        # 下载:字节一致 + 附件 disposition 带原始文件名
        conn.request("GET", f"/api/v1/clipboard/files/{fid}",
                     headers={"Cookie": alice})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), data)
        disposition = response.getheader("Content-Disposition")
        self.assertIn("attachment", disposition)
        self.assertIn("%E7%AC%94%E8%AE%B0", disposition)  # RFC5987 编码的「笔记」
        # usage 计数
        status, body = get_json(conn, "/api/v1/clipboard/usage", alice)
        self.assertEqual(body["usage"]["file_count"], 1)
        self.assertEqual(body["usage"]["file_bytes"], len(data))
        # 删除后 404
        conn.request("DELETE", f"/api/v1/clipboard/items/{fid}",
                     headers={"Cookie": alice})
        self.assertEqual(conn.getresponse().status, 200)
        conn.request("GET", f"/api/v1/clipboard/files/{fid}",
                     headers={"Cookie": alice})
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()

    def test_file_name_traversal_sanitized(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn)
        status, body, _ = self._post_file(
            conn, alice, "../../etc/passwd", b"data")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["item"]["file_name"], "passwd")  # 路径成分被剥离
        # 文件确实只在 owner 目录内
        top = list(Path(self._tmp.name).glob("etc"))
        self.assertEqual(top, [])
        conn.close()

    def test_file_oversize_413_and_quota_507(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn)
        with patch.object(clip, "cfg", lambda: {
                "max_text_bytes": 65536, "max_file_bytes": 2048,
                "quota_mb": 0.002, "keep_text_items": 100}):   # ≈2097B 总配额
            # 单文件超上限(声明 4KB > 2KB)
            status, body, _ = self._post_file(conn, alice, "big.bin", b"x" * 4096)
            self.assertEqual(status, 413)
            # 两个 1200B 文件合计 2400B > 2097B:第二个 → 507
            status, body, _ = self._post_file(conn, alice, "a.bin", b"a" * 1200)
            self.assertEqual(status, 201, body)
            status, body, _ = self._post_file(conn, alice, "b.bin", b"b" * 1200)
            self.assertEqual(status, 507)
        conn.close()

    # ---- 用户隔离 ----

    def test_users_isolated(self):
        conn = http.client.HTTPConnection(*self.httpd.server_address)
        alice = login(conn, ALICE)
        bob = login(conn, BOB)
        status, body, _ = post_json(conn, "/api/v1/clipboard/items",
                                    {"content": "alice 私有"}, cookie=alice)
        aid = body["item"]["id"]
        status, body, _ = self._post_file(conn, alice, "a.txt", b"alice-file")
        fid = body["item"]["id"]
        # bob 看不到 alice 的条目
        status, body = get_json(conn, "/api/v1/clipboard/items", bob)
        self.assertEqual(body["items"], [])
        # bob 下载/删除 alice 的文件与文本均 404
        conn.request("GET", f"/api/v1/clipboard/files/{fid}", headers={"Cookie": bob})
        self.assertEqual(conn.getresponse().status, 404)
        conn.request("DELETE", f"/api/v1/clipboard/items/{aid}", headers={"Cookie": bob})
        self.assertEqual(conn.getresponse().status, 404)
        # alice 自己的数据完好
        status, body = get_json(conn, "/api/v1/clipboard/items", alice)
        self.assertEqual(len(body["items"]), 2)
        conn.close()


if __name__ == "__main__":
    unittest.main()
