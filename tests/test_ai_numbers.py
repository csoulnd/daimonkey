"""AI 摘要数字可信度：板块口径取数 + 生成后数字对账。

覆盖 2026-10-20 的 119 事故：首页提示词曾用全站 products 计数（133），
模型据此说成"核心产品 119 个"，而 harness_core 最新快照只有 13 条。
本测试只读临时库，不触网、不调真实模型、不写真实 data/。
"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests  # noqa: E402,F401  单测护栏:离线 + 不调真实模型


def seed(db_mod):
    """最小数据集：4 个板块各自有榜，另造 10 个只存在于全站 products 的诱饵。"""
    with db_mod.conn() as c:
        c.executescript(db_mod.DDL)
        db_mod.ensure_news_tables(c)
        db_mod.ensure_ai_tables(c)

        def product(pid, slug, name, category, tier=None, origin="registry",
                    tags=()):
            c.execute(
                "INSERT INTO products (id, slug, name, vendor, category, tier,"
                " origin, status, board_tags, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,'active',?,?,?)",
                (pid, slug, name, "Vendor", category, tier, origin,
                 '["%s"]' % '","'.join(tags) if tags else None,
                 "2026-10-01", "2026-10-01"))

        # 核心榜 3 条（另有更早快照，验证取最新）
        for pid, slug, name in ((1, "alpha", "Alpha 智能体"),
                                (2, "beta", "Beta 智能体"),
                                (3, "gamma", "Gamma 智能体")):
            product(pid, slug, name, "coding", tier="core",
                    tags=("harness_core",))
        # 生态榜 2 条
        for pid, slug, name in ((4, "eco-one", "Eco One"),
                                (5, "eco-two", "Eco Two")):
            product(pid, slug, name, "framework", tier="eco",
                    tags=("harness_eco",))
        # 模型榜 4 条（AA 自动入池）
        for pid in range(6, 10):
            product(pid, f"model-{pid}", f"Model {pid}", "models",
                    origin="aa", tags=("model",))
        # 硬件榜 2 条（MLPerf）
        for pid in range(10, 12):
            product(pid, f"card-{pid}", f"Card {pid}", "infra",
                    origin="mlperf", tags=("nv_infer",))
        # 诱饵：不在任何板块快照里，只让 SELECT COUNT(*) FROM products 变大
        for pid in range(12, 22):
            product(pid, f"decoy-{pid}", f"Decoy {pid}", "coding",
                    tier="core")
        # 观察区：命中派生源榜 coding 但无 coding 排名 -> harness_core 观察区非 0
        product(22, "obs-one", "Obs One", "coding", tier="core",
                tags=("coding",))

        def rank(board, snap, pid, r):
            c.execute(
                "INSERT INTO rankings (board, snapshot_date, rank, product_id,"
                " heat_score, momentum_score, delta_7d, coverage,"
                " methodology_version) VALUES (?,?,?,?,?,?,?,?, 'v1.8')",
                (board, snap, r, pid, 90.0 - r, 60.0, 1.0, 0.9))

        for i, pid in enumerate((1, 2, 3), 1):
            rank("harness_core", "2026-10-20", pid, i)
            rank("harness_core", "2026-10-12", pid, i)   # 旧快照
        for i, pid in enumerate((4, 5), 1):
            rank("harness_eco", "2026-10-20", pid, i)
        for i, pid in enumerate(range(6, 10), 1):
            rank("model", "2026-10-20", pid, i)
        for i, pid in enumerate(range(10, 12), 1):
            rank("nv_infer", "2026-10-20", pid, i)


class AiNumberTests(unittest.TestCase):
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
        importlib.reload(workflows)
        self.ai, self.workflows = ai, workflows

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    # ---- 板块口径 ----

    def test_home_uses_board_snapshot_count_not_global_products(self):
        task = self.workflows.get("page_summary").build_task({"page": "home"})
        self.assertIn("榜单口径：harness_core（核心产品榜）", task)
        self.assertIn("快照 2026-10-20", task)
        self.assertIn("收录 4 个、入榜 3 条（不含其他板块）", task)
        self.assertNotIn("共收录", task)
        self.assertNotIn("21 条", task)      # 全站 products 数不得出现
        # 最新快照的 3 条都在，旧快照/诱饵不在
        for name in ("Alpha 智能体", "Beta 智能体", "Gamma 智能体"):
            self.assertIn(name, task)
        self.assertNotIn("Decoy", task)

    def test_each_board_has_its_own_scope(self):
        task = lambda b: self.workflows.get("page_summary").build_task(
            {"page": "board", "board": b})
        self.assertIn("收录 2 个、入榜 2 条（不含其他板块）", task("harness_eco"))
        self.assertIn("收录 4 个、入榜 4 条（不含其他板块）", task("model"))
        self.assertIn("收录 2 个、入榜 2 条（不含其他板块）", task("nv_infer"))
        self.assertIn("榜单口径：model（模型评测）", task("model"))

    def test_unknown_board_rejected(self):
        from collector import assistant
        with self.assertRaises(assistant.AssistantError):
            self.workflows.get("page_summary").build_task(
                {"page": "board", "board": "nope"})

    # ---- 数字抽取 ----

    def test_extract_summary_numbers(self):
        text = ("本站收录的119个核心产品、13 条记录、第 4 名，"
                "2026-10-12 快照，合计 137 篇。")
        # "第 4 名"会同时命中"名"量词与"第 N"，抽取结果允许重复
        self.assertEqual(sorted(set(self.workflows.extract_summary_numbers(text))),
                         [4, 13, 119, 137])
        self.assertEqual(self.workflows.find_unexplained_numbers(
            text, {13, 4, 137}), [119])

    # ---- 生成后对账（119 拦截） ----

    def test_store_blocks_hallucinated_total_and_falls_back(self):
        store = self.workflows.get("page_summary").store
        with self.assertLogs("daimonkey.ai", level="WARNING") as logs:
            store({"page": "home",
                   "signature": f"{self.ai.SUMMARY_PROMPT_VERSION}:2026-10-20:3"},
                  "本板块汇总本站收录的119个核心产品，前 10 名竞争激烈。")
        self.assertTrue(any("119" in line for line in logs.output))
        with self.db.conn() as c:
            got = self.ai.summary_of(c, "home")
        self.assertNotIn("119", got["content"])
        self.assertIn("收录 4 个、入榜 3 条（不含其他板块）", got["content"])
        self.assertEqual(got["data_signature"],
                         f"{self.ai.SUMMARY_PROMPT_VERSION}:2026-10-20:3")

    def test_store_accepts_scoped_numbers(self):
        store = self.workflows.get("page_summary").store
        reply = ("核心产品榜快照 2026-10-20 收录 4 个、入榜 3 条（不含其他板块），"
                 "观察区 0 个，第 2 名由 Beta 智能体领跑。")
        store({"page": "home", "signature": "s"}, reply)
        with self.db.conn() as c:
            got = self.ai.summary_of(c, "home")
        self.assertEqual(got["content"], reply)

    def test_zero_count_is_not_flagged(self):
        """回归：0 在数量语境下仍合法，不得误降级。"""
        with self.db.conn() as c:
            ctx = self.workflows._page_summary_data(c, "home")
        self.assertIn(0, ctx["allowed"])
        reply = ("核心产品榜快照 2026-10-20 收录 4 个、入榜 3 条（不含其他板块）；"
                 "本次列出前 10 名，0 个条目待补。")
        with self.assertNoLogs("daimonkey.ai", level="WARNING"):
            self.workflows.get("page_summary").store(
                {"page": "home", "signature": "s"}, reply)
        with self.db.conn() as c:
            got = self.ai.summary_of(c, "home")
        self.assertEqual(got["content"], reply)
        # 119 仍必须被拦截（同一允许集合）
        self.assertEqual(self.workflows.find_unexplained_numbers(
            "本站收录 119 个核心产品", ctx["allowed"]), [119])

    def test_store_blocks_wrong_snapshot_date(self):
        store = self.workflows.get("page_summary").store
        with self.assertLogs("daimonkey.ai", level="WARNING") as logs:
            store({"page": "home", "signature": "s"},
                  "核心产品榜快照 2026-10-13 收录 4 个、入榜 3 条（不含其他板块）。")
        self.assertTrue(any("2026-10-13" in line for line in logs.output))
        with self.db.conn() as c:
            got = self.ai.summary_of(c, "home")
        self.assertNotIn("2026-10-13", got["content"])
        self.assertIn("2026-10-20", got["content"])

    def test_find_unexplained_dates(self):
        bad = self.workflows.find_unexplained_dates(
            "快照 2026-10-20，另有 2026年10月13日 与 2026/10/20。",
            {"2026-10-20"})
        self.assertEqual(bad, ["2026-10-13"])

    def test_store_blocks_impossible_rank(self):
        store = self.workflows.get("page_summary").store
        with self.assertLogs("daimonkey.ai", level="WARNING"):
            store({"page": "home", "signature": "s"},
                  "核心产品榜共 3 条，第 99 名是新面孔。")
        with self.db.conn() as c:
            got = self.ai.summary_of(c, "home")
        self.assertNotIn("第 99", got["content"])
        self.assertIn("收录 4 个、入榜 3 条（不含其他板块）", got["content"])

    def test_store_survives_reconcile_failure(self):
        """对账自身异常不得吞掉摘要（也不得阻断管线）。"""
        store = self.workflows.get("page_summary").store
        with patch.object(self.workflows, "_page_summary_data",
                          side_effect=RuntimeError("boom")):
            store({"page": "home", "signature": "s"}, "正常摘要 3 条。")
        with self.db.conn() as c:
            got = self.ai.summary_of(c, "home")
        self.assertEqual(got["content"], "正常摘要 3 条。")


if __name__ == "__main__":
    unittest.main()
