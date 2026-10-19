"""方向周总结测试：按 news_tag 过滤、AI 工作流落库、幂等生成、人工编辑 CLI。

全部使用临时库；AI 调用全部 mock，不发真实请求、不碰生产 data/。
"""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import db, queries, workflows

WEEK = "2026-W42"
TS = "2026-10-17T08:00:00Z"


def load_digestctl():
    spec = importlib.util.spec_from_file_location(
        "digestctl", Path(__file__).resolve().parents[1] / "scripts" / "digestctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DigestTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self._patch = patch.object(db, "DB_PATH", self.db_path)
        self._patch.start()
        with db.conn() as c:
            c.executescript(db.DDL)
            db.ensure_news_tables(c)
        with db.conn() as c:
            self._news(c, "Claude Code 发布新功能", "coding")
            self._news(c, "MCP 生态更新", "framework")
            self._news(c, "GPT-6 发布", "models")

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def _news(self, c, title, category):
        c.execute(
            "INSERT INTO news_items(url_hash, title, url, source_key, source_name,"
            " category, region, published_at, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (f"h-{title}", title, f"https://e.test/{title}", "rss", "Test",
             category, "global", TS, TS))

    def _digest(self, c, summary, tags, status="published", week=WEEK):
        c.execute(
            "INSERT INTO insights(title, summary, source_url, source_name,"
            " published_at, tags, llm_generated, status, created_at)"
            " VALUES(?,?,?,?,?,?,1,?,?)",
            (f"摘要 {tags}", summary, "", "DSH assistant", week, tags, status,
             "2026-10-18T00:00:00Z"))


class TestDigestTask(DigestTestCase):
    def test_direction_task_filters_news_categories(self):
        task = workflows._digest_task({"direction": "harness", "week": WEEK})
        self.assertIn("Claude Code 发布新功能", task)
        self.assertIn("MCP 生态更新", task)
        self.assertNotIn("GPT-6 发布", task)
        self.assertIn("智能体方向", task)

    def test_global_task_keeps_all_news(self):
        task = workflows._digest_task({"week": WEEK})
        for title in ("Claude Code 发布新功能", "MCP 生态更新", "GPT-6 发布"):
            self.assertIn(title, task)

    def test_empty_direction_scope_raises(self):
        with self.assertRaises(Exception):
            workflows._digest_task({"direction": "nv", "week": WEEK})

    def test_store_direction_publishes_with_direction_tag(self):
        workflows._digest_store({"direction": "harness", "week": WEEK}, "总结正文")
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM insights WHERE tags LIKE '%direction:harness%'"
            ).fetchone()
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["summary"], "总结正文")
        self.assertEqual(row["published_at"], WEEK)

    def test_store_global_remains_draft(self):
        workflows._digest_store({"week": WEEK}, "全站总结")
        with db.conn() as c:
            row = c.execute("SELECT * FROM insights WHERE tags = 'news-digest,ai'"
                            ).fetchone()
        self.assertEqual(row["status"], "draft")


class TestGeneration(DigestTestCase):
    def test_generates_only_directions_with_news(self):
        calls = []

        def fake(name, payload, channel="internal", max_tokens=None):
            calls.append((name, payload))
            return {"reply": "x", "tokens_in": 1, "tokens_out": 1}

        with patch.object(workflows.assistant, "run_internal", side_effect=fake):
            notes = workflows.generate_direction_digests()
        self.assertEqual({p["direction"] for _, p in calls}, {"harness", "fm"})
        self.assertTrue(all(p["week"] == WEEK for _, p in calls))
        self.assertTrue(any("已生成" in n for n in notes))
        self.assertTrue(any("无新闻范围" in n for n in notes))  # nv

    def test_existing_week_is_not_regenerated(self):
        with db.conn() as c:
            self._digest(c, "已有总结", "news-digest,ai,direction:harness")
        calls = []

        def fake(name, payload, channel="internal", max_tokens=None):
            calls.append(payload["direction"])
            return {"reply": "x", "tokens_in": 1, "tokens_out": 1}

        with patch.object(workflows.assistant, "run_internal", side_effect=fake):
            notes = workflows.generate_direction_digests()
        self.assertNotIn("harness", calls)
        self.assertIn("fm", calls)
        self.assertTrue(any("已存在" in n for n in notes))

    def test_force_regenerates_existing(self):
        with db.conn() as c:
            self._digest(c, "已有总结", "news-digest,ai,direction:harness")
        calls = []
        with patch.object(workflows.assistant, "run_internal",
                          side_effect=lambda n, p, **k: calls.append(p["direction"])
                          or {"reply": "x", "tokens_in": 1, "tokens_out": 1}):
            workflows.generate_direction_digests(direction="harness", force=True)
        self.assertEqual(calls, ["harness"])


class TestQueries(DigestTestCase):
    def test_news_digests_filter_by_categories(self):
        all_weeks = queries.news_digests(self._conn(), 8)
        self.assertEqual(all_weeks[0]["week"], WEEK)
        self.assertEqual(len(all_weeks[0]["items"]), 3)
        harness = queries.news_digests(self._conn(), 8, ["coding", "framework"])
        self.assertEqual(len(harness[0]["items"]), 2)
        fm = queries.news_digests(self._conn(), 8, ["models"])
        self.assertEqual(len(fm[0]["items"]), 1)

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        self.addCleanup(c.close)
        return c

    def test_global_summary_excludes_direction_rows(self):
        with db.conn() as c:
            self._digest(c, "全站版", "news-digest,ai")
            self._digest(c, "智能体版", "news-digest,ai,direction:harness")
        with db.conn() as c:
            self.assertEqual(queries.published_digest_summary(c, WEEK)["summary"],
                             "全站版")
            self.assertEqual(queries.published_digest_summary(
                c, WEEK, direction="harness")["summary"], "智能体版")


class TestDigestCtl(DigestTestCase):
    def setUp(self):
        super().setUp()
        self.ctl = load_digestctl()
        with db.conn() as c:
            self._digest(c, "草稿", "news-digest,ai,direction:harness",
                         status="draft")
            self._digest(c, "全站草稿", "news-digest,ai", status="draft")

    def test_list_filters_direction(self):
        with db.conn() as c:
            rows = self.ctl.list_digests(c, direction="harness")
        self.assertEqual(len(rows), 1)
        self.assertIn("direction:harness", rows[0]["tags"])

    def test_edit_and_publish(self):
        with db.conn() as c:
            row = self.ctl.list_digests(c, direction="harness")[0]
            updated = self.ctl.edit_digest(c, row["id"], "人工修订版", publish=True)
        self.assertEqual(updated["summary"], "人工修订版")
        self.assertEqual(updated["status"], "published")

    def test_publish_only_ai_digest(self):
        with db.conn() as c:
            draft = self.ctl.list_digests(c, direction="harness")[0]
            self.ctl.publish_digest(c, draft["id"])
            self.assertEqual(self.ctl.list_digests(c, direction="harness")[0]["status"],
                             "published")
            with self.assertRaises(ValueError):
                self.ctl.publish_digest(c, 99999)

    def test_unknown_direction_rejected(self):
        with db.conn() as c:
            with self.assertRaises(ValueError):
                self.ctl.list_digests(c, direction="nope")


if __name__ == "__main__":
    unittest.main()
