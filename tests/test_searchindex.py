"""站内搜索索引生成测试：临时 sqlite、临时输出目录，不触碰生产 data/ 与 dist/。"""
from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from collector import db, searchindex


class TestSearchIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "test.sqlite3"
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(db.DDL)
        db.ensure_news_tables(self.c)
        self.out = self.root / "dist" / "assets" / "search-index.json"

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    # ---- fixture ---------------------------------------------------------
    def _product(self, slug, name, **kw):
        values = {
            "vendor": "TestVendor", "category": "coding", "tier": "core",
            "origin": "registry", "board_tags": '["coding"]', "status": "active",
            "notes": None,
        }
        values.update(kw)
        cur = self.c.execute(
            "INSERT INTO products(slug,name,vendor,category,tier,origin,board_tags,"
            "status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (slug, name, values["vendor"], values["category"], values["tier"],
             values["origin"], values["board_tags"], values["status"], values["notes"],
             "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        return cur.lastrowid

    def _rank(self, board, date, rank, pid, heat, momentum=0):
        self.c.execute(
            "INSERT INTO rankings(board,snapshot_date,rank,product_id,heat_score,"
            "momentum_score,delta_7d,delta_30d,coverage,methodology_version)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (board, date, rank, pid, heat, momentum, 0.0, 0.0, 1.0, "v1.8"))

    def _news(self, title, category, date, summary="摘要", source="Test News", url=None):
        self.c.execute(
            "INSERT INTO news_items(url_hash,title,url,summary,source_key,source_name,"
            "category,region,published_at,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"h-{title}", title, url or f"https://news.example.org/{abs(hash(title))}",
             summary, "rss", source, category, "global", date, date))

    def _read(self):
        data = json.loads(self.out.read_text(encoding="utf-8"))
        return data, {item["type"]: item for item in data["items"]}.keys()

    # ---- tests -----------------------------------------------------------
    def test_empty_database_is_valid(self):
        stats = searchindex.build_index(self.c, self.out)
        self.assertTrue(self.out.is_file())
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], searchindex.INDEX_VERSION)
        self.assertTrue(data["generated_at"].endswith("Z"))
        self.assertEqual(data["counts"]["product"], 0)
        self.assertEqual(data["counts"]["ranking"], 0)
        self.assertEqual(data["counts"]["news"], 0)
        self.assertGreaterEqual(data["counts"]["page"], 1)
        self.assertEqual(len(data["items"]), data["counts"]["page"])
        self.assertEqual(stats["json_bytes"], self.out.stat().st_size)
        self.assertGreater(len(data["generated_at"]), 10)

    def test_non_ascii_is_preserved_not_escaped(self):
        pid = self._product("dsh", "DSH（DeepSeek Harness）")
        self._rank("harness_core", "2026-10-21", 1, pid, 99.5)
        self.c.commit()
        searchindex.build_index(self.c, self.out)
        raw = self.out.read_text(encoding="utf-8")
        self.assertIn("DSH（DeepSeek Harness）", raw)
        self.assertNotIn("\\u", raw)

    def test_product_fields_board_heat_and_public_rank_preferred(self):
        pid = self._product("claude-code", "Claude Code",
                            vendor="Anthropic", category="coding",
                            notes="终端里的编码智能体")
        # 同一天既有公开核心榜，也有隐藏分类源榜；应选公开榜。
        self._rank("coding", "2026-10-21", 1, pid, 77.0)
        self._rank("harness_core", "2026-10-21", 4, pid, 98.25)
        self.c.commit()

        searchindex.build_index(self.c, self.out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        product = next(i for i in data["items"] if i["type"] == "product")
        self.assertEqual(product["slug"], "claude-code")
        self.assertEqual(product["name"], "Claude Code")
        self.assertEqual(product["tagline"], "终端里的编码智能体")
        self.assertEqual(product["board"], "harness_core")
        self.assertEqual(product["board_name"], "核心产品榜")
        self.assertEqual(product["heat"], 98.2)
        self.assertEqual(product["url"], "/product/claude-code")

    def test_product_without_public_rank_falls_back_to_board_tags(self):
        self._product("lonely", "Lonely Product", board_tags='["domestic"]')
        self.c.commit()
        searchindex.build_index(self.c, self.out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        product = next(i for i in data["items"] if i["type"] == "product")
        self.assertEqual(product["board"], "domestic")
        self.assertIsNone(product["heat"])

    def test_rankings_only_public_boards(self):
        pid = self._product("p1", "P1")
        for i, board in enumerate(("harness_core", "harness_eco", "model",
                                   "nv_infer", "domestic", "domestic_app",
                                   "coding", "general", "claw", "unified")):
            self._rank(board, "2026-10-21", i + 1, pid, 90 - i)
        self.c.commit()

        searchindex.build_index(self.c, self.out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        rankings = [i for i in data["items"] if i["type"] == "ranking"]
        boards = {r["board"] for r in rankings}
        self.assertEqual(boards, {"harness_core", "harness_eco", "model",
                                  "nv_infer", "domestic", "domestic_app"})
        core = next(r for r in rankings if r["board"] == "harness_core")
        self.assertIn("2026-10-21", core["subtitle"])
        self.assertIn("P1", core["subtitle"])
        self.assertEqual(core["url"], "/rankings/harness_core")

    def test_news_direction_tags_and_summary_trim(self):
        long_summary = "智能体与模型的最新进展。" * 50
        self._news("Coding agent 发布", "coding", "2026-10-20T10:00:00Z",
                   summary=long_summary)
        self._news("模型更新", "models", "2026-10-19T10:00:00Z")
        self._news("GPU 推理", "infra", "2026-10-18T10:00:00Z")
        self._news("融资消息", "product", "2026-10-17T10:00:00Z")
        self.c.commit()

        searchindex.build_index(self.c, self.out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        news = {i["title"]: i for i in data["items"] if i["type"] == "news"}
        self.assertEqual(news["Coding agent 发布"]["tag"], "harness")
        self.assertEqual(news["Coding agent 发布"]["direction"], "智能体")
        self.assertEqual(news["模型更新"]["tag"], "fm")
        self.assertEqual(news["GPU 推理"]["tag"], "nv")
        # 产品类新闻不属于任何方向频道，仍需可搜索（tag 回退为分类 key）
        self.assertEqual(news["融资消息"]["tag"], "product")
        self.assertEqual(news["融资消息"]["direction"], "产品与落地")
        self.assertLessEqual(len(news["Coding agent 发布"]["subtitle"]),
                             searchindex.NEWS_SUMMARY_CHARS)
        self.assertTrue(news["Coding agent 发布"]["external"])
        self.assertTrue(news["Coding agent 发布"]["url"].startswith("https://"))

    def test_pages_include_directions_and_never_private_areas(self):
        self.c.commit()
        searchindex.build_index(self.c, self.out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        pages = [i for i in data["items"] if i["type"] == "page"]
        urls = {p["url"] for p in pages}
        self.assertIn("/", urls)
        self.assertIn("/sources", urls)
        self.assertIn("/api", urls)
        self.assertIn("/dir/harness", urls)
        self.assertIn("/dir/fm", urls)
        self.assertIn("/dir/nv", urls)
        for url in urls:
            self.assertFalse(url.startswith(("/reports", "/knowledge", "/clipboard",
                                             "/notes", "/raw")), url)
        titles = {p["title"] for p in pages}
        self.assertIn("智能体频道", titles)

    def test_news_limit_and_stats(self):
        for i in range(60):
            self._news(f"新闻 {i}", "coding", f"2026-10-{1 + i % 20:02d}T00:00:00Z")
        self.c.commit()
        stats = searchindex.build_index(self.c, self.out, max_news=10)
        self.assertEqual(stats["counts"]["news"], 10)
        self.assertEqual(stats["json_bytes"], self.out.stat().st_size)
        self.assertEqual(stats["gzip_bytes"],
                         len(gzip.compress(self.out.read_bytes(), 6, mtime=0)))

    def test_read_only_connection_is_supported(self):
        pid = self._product("p1", "P1")
        self._rank("harness_core", "2026-10-21", 1, pid, 88)
        self._news("新闻", "coding", "2026-10-20T00:00:00Z")
        self.c.commit()
        self.c.close()
        ro = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        try:
            stats = searchindex.build_index(ro, self.out)
            self.assertEqual(stats["counts"]["product"], 1)
            self.assertEqual(stats["counts"]["news"], 1)
        finally:
            ro.close()
        # setUp 的 tearDown 会再次 close，已关闭连接再 close 是安全的
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row

    def test_gzip_budget_under_60kb_with_large_dataset(self):
        """接近生产规模（133 产品 + 数百新闻）时，gzip 后必须 < 60KB。"""
        pids = []
        for i in range(133):
            pids.append(self._product(
                f"product-{i}", f"AI 产品 {i}（智能体工具）",
                vendor=f"Vendor {i % 7}", category="coding",
                notes=f"面向开发者的智能体产品 {i}，支持多步任务编排与工具调用。"))
        for i, pid in enumerate(pids):
            self._rank("harness_core", "2026-10-21", i + 1, pid, 100 - i * 0.5)
        for i in range(700):
            self._news(
                f"AI 新闻 {i}：智能体、模型与推理基础设施更新",
                ("coding", "framework", "models", "research", "infra",
                 "product", "safety")[i % 7],
                f"2026-10-{1 + i % 20:02d}T{i % 24:02d}:00:00Z",
                summary="这是新闻摘要，覆盖智能体、模型、推理基础设施、工具链与产品落地的最新进展。",
                source=f"Source {i % 5}",
                url=f"https://news.example.org/post/{i}")
        self.c.commit()

        stats = searchindex.build_index(self.c, self.out)
        self.assertLess(stats["gzip_bytes"], 60 * 1024,
                        f"搜索索引 gzip {stats['gzip_bytes']} bytes >= 60KB")
        self.assertEqual(stats["counts"]["product"], 133)
        self.assertEqual(stats["counts"]["news"], searchindex.MAX_NEWS)

    def test_atomic_write_leaves_no_tmp_file(self):
        searchindex.build_index(self.c, self.out)
        self.assertFalse(self.out.with_name(self.out.name + ".tmp").exists())
        self.assertTrue(self.out.is_file())


if __name__ == "__main__":
    unittest.main()
