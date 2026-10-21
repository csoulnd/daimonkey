"""三方向频道测试:registry 配置驱动、方向新闻过滤、方向周总结查询、旧路由 301。

全部使用临时库/内存库,不读写生产 data/;服务测试只走重定向分支,不依赖 dist。
"""
import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from collector import db, queries
from collector.config import registry


def load_server():
    spec = importlib.util.spec_from_file_location(
        "directions_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDirectionsConfig(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.dirs = self.reg["directions"]

    def test_three_directions_in_order(self):
        self.assertEqual(list(self.dirs), ["harness", "fm", "nv"])

    def test_required_fields(self):
        for key, d in self.dirs.items():
            self.assertTrue(d.get("name"), key)
            self.assertTrue(d.get("icon"), key)
            self.assertIn("news_tag", d, key)
            self.assertIn("boards", d, key)

    def test_board_references_exist(self):
        boards = self.reg["boards"]
        for key, d in self.dirs.items():
            for board in d.get("boards") or []:
                self.assertIn(board, boards, f"{key} -> {board}")

    def test_news_categories_exist(self):
        known = set(self.reg["news"]["categories"])
        for key, d in self.dirs.items():
            for cat in d.get("news_categories") or []:
                self.assertIn(cat, known, f"{key} -> {cat}")

    def test_nv_channel_is_live_with_mlperf_board(self):
        nv = self.dirs["nv"]
        self.assertFalse(nv.get("placeholder"))
        self.assertEqual(nv.get("boards"), ["nv_infer"])
        self.assertIn("nv_infer", self.reg["boards"])
        self.assertEqual(nv.get("news_categories"), ["infra"])
        source_keys = {s["key"] for s in self.reg["sources"]}
        self.assertIn("mlperf", source_keys)
        self.assertTrue(nv.get("planned_sources"))
        for src in nv["planned_sources"]:
            self.assertIn("name", src)
            self.assertIn("status", src)


class TestDirectionQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self._patch = patch.object(db, "DB_PATH", self.db_path)
        self._patch.start()
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(db.DDL)
        db.ensure_news_tables(self.c)

    def tearDown(self):
        self.c.close()
        self._patch.stop()
        self.tmp.cleanup()

    def _news(self, title, category, published_at, url=None):
        self.c.execute(
            "INSERT INTO news_items(url_hash, title, url, source_key, source_name,"
            " category, region, published_at, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (f"h-{title}", title, url or f"https://e.test/{title}", "rss", "Test",
             category, "global", published_at, published_at))
        self.c.commit()

    def test_news_filter_by_categories(self):
        self._news("coding one", "coding", "2026-10-18T00:00:00Z")
        self._news("framework one", "framework", "2026-10-17T00:00:00Z")
        self._news("model one", "models", "2026-10-16T00:00:00Z")
        self._news("product one", "product", "2026-10-15T00:00:00Z")
        rows = queries.news_for_categories(self.c, ["coding", "framework"])
        self.assertEqual([r["title"] for r in rows], ["coding one", "framework one"])
        self.assertEqual(queries.news_count_for_categories(
            self.c, ["coding", "framework"]), 2)

    def test_empty_categories_returns_empty(self):
        self._news("coding one", "coding", "2026-10-18T00:00:00Z")
        self.assertEqual(queries.news_for_categories(self.c, []), [])
        self.assertEqual(queries.news_count_for_categories(self.c, []), 0)

    def _digest(self, summary, week, tags, status="published"):
        self.c.execute(
            "INSERT INTO insights(title, summary, source_url, source_name, published_at,"
            " tags, llm_generated, status, created_at)"
            " VALUES(?,?,?,?,?,?,1,?,?)",
            (f"摘要 {week}", summary, "", "DSH assistant", week, tags, status,
             "2026-10-18T00:00:00Z"))
        self.c.commit()

    def test_latest_direction_digest_only_published_and_tagged(self):
        self._digest("harness published", "2026-W42",
                     "news-digest,ai,direction:harness")
        self._digest("harness draft newer", "2026-W43",
                     "news-digest,ai,direction:harness", status="draft")
        self._digest("fm published", "2026-W43",
                     "news-digest,ai,direction:fm")
        got = queries.latest_direction_digest(self.c, "harness")
        self.assertEqual(got["summary"], "harness published")
        self.assertEqual(got["week_start"], "2026-10-12")
        self.assertEqual(got["week_end"], "2026-10-18")

    def test_latest_direction_digest_none(self):
        self.assertIsNone(queries.latest_direction_digest(self.c, "nv"))


class TestLegacyRedirect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_unified_redirects_to_harness_channel(self):
        conn = HTTPConnection(*self.httpd.server_address)
        conn.request("GET", "/rankings/unified")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 301)
        self.assertEqual(response.getheader("Location"), "/dir/harness")

    def test_rankings_root_redirects_to_harness_channel(self):
        conn = HTTPConnection(*self.httpd.server_address)
        conn.request("GET", "/rankings")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 301)
        self.assertEqual(response.getheader("Location"), "/dir/harness")


if __name__ == "__main__":
    unittest.main()
