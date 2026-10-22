"""AI 工作流与降级：摘要取数、评分 JSON 容错、指纹去重、失败不阻断管线。

全部 mock assistant.run_internal，不触网、不调真实模型、不写真实 data/。
"""
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests  # noqa: E402,F401  单测护栏:离线 + 不调真实模型


def seed(db_mod):
    """临时库中造最小数据集：2 产品 + 核心产品榜 + 1 条新闻。"""
    with db_mod.conn() as c:
        c.executescript(db_mod.DDL)
        db_mod.ensure_news_tables(c)
        db_mod.ensure_ai_tables(c)
        for pid, slug, name in ((1, "alpha", "Alpha 智能体"), (2, "beta", "Beta 框架")):
            c.execute(
                "INSERT INTO products (id, slug, name, vendor, category, tier,"
                " board_tags, created_at, updated_at)"
                " VALUES (?,?,?,?,?,'core','[\"coding\"]',?,?)",
                (pid, slug, name, "Vendor", "coding",
                 "2026-10-01", "2026-10-01"))
            c.execute(
                "INSERT INTO rankings (board, snapshot_date, rank, product_id,"
                " heat_score, momentum_score, delta_7d, coverage, methodology_version)"
                " VALUES ('harness_core','2026-10-12',?,?,?,?,?,?, 'v1.7')",
                (pid, pid, 90.0 - pid, 60.0, 1.0, 0.9))
        c.execute(
            "INSERT INTO news_items (url_hash, title, url, source_key, source_name,"
            " category, fetched_at, published_at) VALUES ('h1','某模型发布','http://x',"
            " 's','S','models','2026-10-12T08:00:00','2026-10-12')")


def ok_result(*_a, **_k):
    return {"reply": "x", "tokens_in": 1, "tokens_out": 1}


class AiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import collector.db as db
        self.db = db
        self._patch = patch.object(db, "DB_PATH", Path(self._tmp.name) / "t.sqlite3")
        self._patch.start()
        seed(db)
        import collector.ai as ai
        import collector.workflows as workflows
        importlib.reload(ai)
        self.ai, self.workflows = ai, workflows

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    # ---- 摘要取数与落库 ----

    def test_page_summary_home_assembles_data(self):
        spec = self.workflows.get("page_summary")
        task = spec.build_task({"page": "home"})
        self.assertIn("Alpha 智能体", task)
        self.assertIn("150-200 字", task)
        spec2 = self.workflows.get("page_summary")
        news_task = spec2.build_task({"page": "news"})
        self.assertIn("某模型发布", news_task)
        with self.db.conn() as c:
            self.ai.save_summary(c, "home", "测试摘要", "2026-10-12:2")
            got = self.ai.summary_of(c, "home")
        self.assertEqual(got["content"], "测试摘要")

    def test_page_summary_empty_data_raises(self):
        from collector import assistant
        spec = self.workflows.get("page_summary")
        with self.db.conn() as c:
            c.execute("DELETE FROM rankings")
        with self.assertRaises(assistant.AssistantError):
            spec.build_task({"page": "home"})

    def test_refresh_skips_when_signature_unchanged(self):
        with self.db.conn() as c:
            sig = self.ai.data_signature(c, "home")
            self.ai.save_summary(c, "home", "旧摘要", sig)
        calls = []

        def fake(name, payload=None, channel="internal", **kwargs):
            calls.append((name, payload))
            return ok_result()

        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal", side_effect=fake):
            self.ai.refresh_summaries_if_stale(c)
        pages = [p.get("page") for n, p in calls if n == "page_summary"]
        self.assertNotIn("home", pages)  # 指纹一致，未重复生成

    def test_refresh_failure_degrades_to_notes(self):
        from collector import assistant
        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal",
                             side_effect=assistant.AssistantError("缺少 API Key")):
            notes = self.ai.refresh_summaries_if_stale(c)
        self.assertTrue(any("摘要跳过" in n and "API Key" in n for n in notes))

    # ---- 评分 JSON 容错 ----

    def test_parse_scores_tolerance(self):
        valid = {"alpha", "beta"}
        ok = self.ai.parse_scores_reply(
            '[{"slug":"alpha","ai_score":88.5,"rationale":"领先"},'
            '{"slug":"ghost","ai_score":10,"rationale":"不在榜"}]', valid)
        self.assertEqual(len(ok), 1)
        self.assertEqual(ok[0]["slug"], "alpha")
        fenced = self.ai.parse_scores_reply(
            '```json\n[{"slug":"beta","ai_score":150,"rationale":"x"}]\n```', valid)
        self.assertEqual(fenced[0]["ai_score"], 100.0)  # 裁剪到 100
        self.assertIsNone(self.ai.parse_scores_reply("抱歉，我无法完成", valid))
        self.assertIsNone(self.ai.parse_scores_reply('[{"slug":"beta"}]', valid))

    def test_score_boards_parse_failure_retries_once(self):
        calls = {"n": 0}

        def flaky(*_a, **_k):
            calls["n"] += 1
            raise self.ai.AiParseError("无法解析")

        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal", side_effect=flaky):
            notes = self.ai.score_boards(c, "2026-10-12")
        self.assertEqual(calls["n"], 2)  # 解析失败重试一次
        self.assertTrue(any("评分跳过" in n for n in notes))

    def test_score_boards_no_retry_on_quota(self):
        from collector import assistant
        calls = {"n": 0}

        def quota(*_a, **_k):
            calls["n"] += 1
            raise assistant.AssistantQuota("配额用尽")

        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal", side_effect=quota):
            notes = self.ai.score_boards(c, "2026-10-12")
        self.assertEqual(calls["n"], 1)  # 配额错误不重试
        self.assertTrue(any("配额" in n for n in notes))

    def test_board_task_handles_none_momentum(self):
        """回归：momentum_score 为 None（新品无历史）不得抛 TypeError。"""
        with self.db.conn() as c:
            c.execute("UPDATE rankings SET momentum_score = NULL, delta_7d = NULL,"
                      " coverage = NULL WHERE product_id = 2")
        spec = self.workflows.get("board_ai_analysis")
        task = spec.build_task({"board": "harness_core", "snapshot_date": "2026-10-12"})
        self.assertIn("Beta 框架", task)
        self.assertIn("—", task)  # None 值以占位符呈现
        home_task = self.workflows.get("page_summary").build_task({"page": "home"})
        self.assertIn("Beta 框架", home_task)

    def test_score_boards_survives_unexpected_exception(self):
        """回归：非 AssistantError 的意外异常（如格式化 bug）也必须降级。"""
        def boom(*_a, **_k):
            raise TypeError("unsupported format string passed to NoneType")

        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal", side_effect=boom):
            notes = self.ai.score_boards(c, "2026-10-12")
        self.assertTrue(any("评分跳过" in n and "TypeError" in n for n in notes))

    def test_score_boards_success_writes_and_skips_next_run(self):
        def ok(*_a, **_k):
            with self.db.conn() as c:
                c.execute(
                    "INSERT INTO ai_scores (board, snapshot_date, product_id,"
                    " ai_score, rationale, created_at) VALUES"
                    " ('harness_core','2026-10-12',1,91,'理由1','now'),"
                    " ('harness_core','2026-10-12',2,77,'理由2','now')")
            return ok_result()

        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal", side_effect=ok):
            notes = self.ai.score_boards(c, "2026-10-12")
        self.assertTrue(any("已生成" in n for n in notes))
        calls = []
        with self.db.conn() as c, \
                patch.object(self.ai.assistant, "run_internal",
                             side_effect=lambda *a, **k: calls.append(a)):
            self.ai.score_boards(c, "2026-10-12")
        self.assertEqual(calls, [])  # 已评分覆盖全部产品 → 不再调用


class AiPipelineTests(unittest.TestCase):
    """管线降级：AI 步骤失败不影响状态与建站；不写真实 data/。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import collector.db as db
        import collector.pipeline as pipeline
        self.db, self.pipeline = db, pipeline
        self._patches = [
            patch.object(db, "DB_PATH", Path(self._tmp.name) / "t.sqlite3"),
            patch.object(pipeline, "STATUS_FILE", Path(self._tmp.name) / "status.json"),
        ]
        for p in self._patches:
            p.start()
        seed(db)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_build_survives_ai_failure(self):
        from collector import ai as ai_mod, export, sitegen, workflows
        with patch.object(workflows, "generate_direction_digests",
                          side_effect=RuntimeError("boom")), \
                patch.object(ai_mod, "refresh_summaries_if_stale",
                             side_effect=RuntimeError("boom")), \
                patch.object(export, "export_all"), \
                patch.object(sitegen, "build"):
            notes = self.pipeline.run_build()
        # 两个 AI 步骤都要降级:周总结失败只记 note,摘要失败也只记 note
        self.assertTrue(any("direction-digest" in n and "boom" in n for n in notes))
        self.assertTrue(any("摘要步骤异常" in n for n in notes))

    def test_status_has_ai_field(self):
        result = {"run_id": 1, "metrics": 0, "sources_ok": 1, "sources_failed": 0,
                  "status": "success", "notes": [], "ai": ["[ai:home] ok"],
                  "peak_mem_mb": 1.0, "http_requests": 0}
        with patch.object(self.pipeline, "run_build", return_value=[]):
            out = self.pipeline._update_with(result)
        self.assertEqual(out["state"], "success")
        status = json.loads(self.pipeline.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(status["ai"], ["[ai:home] ok"])


class LightweightGuardTests(unittest.TestCase):
    """护栏自检:漏 mock 时快速失败,而不是起 pi 子进程或触网。"""

    def test_guards_installed(self):
        import socket
        from collector import assistant
        self.assertTrue(getattr(assistant.run_internal, "_daimon_guarded", False))
        self.assertTrue(getattr(socket.socket, "_daimon_guarded", False))


if __name__ == "__main__":
    unittest.main()
