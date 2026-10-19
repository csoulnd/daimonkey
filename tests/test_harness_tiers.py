"""Harness 收录分层测试：registry tier 完整性 + 核心/生态榜不混排。

使用临时库与合成指标，不触碰生产 data/。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import db, queries, scoring
from collector.config import registry

SNAPSHOT = "2026-10-19"
TS = "2026-10-19T00:00:00Z"


class TestTierConfig(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.products = self.reg["products"]

    def test_harness_products_have_tier(self):
        missing = [p["slug"] for p in self.products
                   if p.get("category") in ("coding", "general", "claw", "framework")
                   and not p.get("tier")]
        self.assertEqual(missing, [])

    def test_category_defaults(self):
        coding = {p["tier"] for p in self.products if p.get("category") == "coding"}
        framework = {p["tier"] for p in self.products if p.get("category") == "framework"}
        self.assertEqual(coding, {"core"})
        self.assertEqual(framework, {"eco"})

    def test_framework_alias_to_harness_eco(self):
        self.assertEqual(self.reg["board_aliases"]["framework"], "harness_eco")

    def test_core_and_eco_boards_are_separated(self):
        boards = self.reg["boards"]
        core = boards["harness_core"]
        eco = boards["harness_eco"]
        self.assertTrue(core.get("derived"))
        self.assertEqual(core.get("tier"), "core")
        self.assertEqual(core.get("derived_from"), ["coding", "general", "claw"])
        self.assertEqual(eco.get("select"), {"tier": "eco"})
        for hidden in ("coding", "general", "claw"):
            self.assertTrue(boards[hidden].get("hidden"))
        self.assertNotIn("unified", boards)


class TestTierScoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self._patch = patch.object(db, "DB_PATH", self.db_path)
        self._patch.start()
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(db.DDL)
        db.ensure_product_columns(self.c)
        db.ensure_ai_tables(self.c)
        self.c.execute(
            "INSERT INTO sources(id, key, name, source_type, enabled)"
            " VALUES(1, 'test', 'Test', 'api', 1)")
        self.c.execute(
            "INSERT INTO raw_payloads(id, source_id, request_url, fetched_at,"
            " body_sha256, body_path, body_bytes, parse_ok)"
            " VALUES(1, 1, 'https://example.test', ?, 'sha', 'raw/test.gz', 10, 1)",
            (TS,))
        self.products = db.upsert_products(self.c, [
            {"slug": "core-agent", "name": "Core Agent", "vendor": "V",
             "category": "coding", "tier": "core", "board_tags": ["coding"]},
            {"slug": "core-unranked", "name": "Core Unranked", "vendor": "V",
             "category": "coding", "tier": "core", "board_tags": ["coding"]},
            {"slug": "eco-tool", "name": "Eco Tool", "vendor": "V",
             "category": "framework", "tier": "eco", "board_tags": ["framework"]},
        ])
        self.by_slug = {p["slug"]: p for p in self.products}
        core_metrics = {
            "github_stars": 1000, "npm_monthly_downloads": 5000,
            "vscode_installs": 100, "github_contributors": 10, "github_forks": 50,
        }
        eco_metrics = {
            "github_stars": 2000, "pypi_monthly_downloads": 3000,
            "npm_monthly_downloads": 1000, "github_contributors": 20,
            "github_forks": 80,
        }
        for key, value in core_metrics.items():
            self._metric(self.by_slug["core-agent"]["id"], "coding", key, value)
        for key, value in eco_metrics.items():
            self._metric(self.by_slug["eco-tool"]["id"], "framework", key, value)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self._patch.stop()
        self.tmp.cleanup()

    def _metric(self, pid, board, key, value):
        unit = "pct" if key.endswith("growth_30d") else "count"
        self.c.execute(
            "INSERT INTO metrics(product_id, board, metric_key, value, unit,"
            " source_id, raw_payload_id, collected_at, granularity)"
            " VALUES(?,?,?,?,?,1,1,?, 'daily')",
            (pid, board, key, value, unit, TS))

    def _pids(self, board):
        return [r["product_id"] for r in self.c.execute(
            "SELECT product_id FROM rankings WHERE board=? AND snapshot_date=?"
            " ORDER BY rank", (board, SNAPSHOT))]

    def test_core_and_eco_never_mix(self):
        summary = scoring.compute_all(self.c, self.products, SNAPSHOT)
        core_id = self.by_slug["core-agent"]["id"]
        eco_id = self.by_slug["eco-tool"]["id"]
        self.assertEqual(self._pids("harness_core"), [core_id])
        self.assertEqual(self._pids("harness_eco"), [eco_id])
        self.assertNotIn(eco_id, self._pids("harness_core"))
        self.assertNotIn(core_id, self._pids("harness_eco"))
        self.assertEqual(summary["harness_core"]["ranked"], 1)
        self.assertEqual(summary["harness_eco"]["ranked"], 1)
        self.assertNotIn("unified", summary)

    def test_observation_respects_tier(self):
        scoring.compute_all(self.c, self.products, SNAPSHOT)
        latest = scoring.load_latest_metrics(self.c, SNAPSHOT)
        obs = queries.observation_items(self.c, "harness_core", SNAPSHOT,
                                        latest, self.products)
        slugs = {o["slug"] for o in obs}
        self.assertIn("core-unranked", slugs)
        self.assertNotIn("eco-tool", slugs)

    def test_api_items_expose_tier(self):
        scoring.compute_all(self.c, self.products, SNAPSHOT)
        latest = scoring.load_latest_metrics(self.c, SNAPSHOT)
        core_items = queries.board_items(self.c, "harness_core", SNAPSHOT, latest)
        eco_items = queries.board_items(self.c, "harness_eco", SNAPSHOT, latest)
        self.assertEqual({it["tier"] for it in core_items}, {"core"})
        self.assertEqual({it["tier"] for it in eco_items}, {"eco"})


if __name__ == "__main__":
    unittest.main()
