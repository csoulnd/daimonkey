"""Artificial Analysis 源测试：解析、去重入池、产品同步、七天缓存（不发真实请求）。"""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collector import db
from collector.sources import aa
from collector.sources.aa import AARow, AASource

NOW = "2026-10-19T00:00:00Z"

PAGE = {
    "tier": "free",
    "intelligence_index_version": 4.3,
    "pagination": {"page": 1, "page_size": 200, "total_pages": 1, "has_more": False},
    "data": [
        {
            "slug": "model-a-max",
            "name": "Model A (Max, Default Fallback)",
            "model_creator": {"name": "Vendor A"},
            "release_date": "2026-01-01",
            "evaluations": {
                "artificial_analysis_intelligence_index": 60.0,
                "artificial_analysis_coding_index": 50.0,
                "artificial_analysis_agentic_index": 40.0,
            },
        },
        {
            "slug": "model-a-low",
            "name": "Model A (Low)",
            "model_creator": {"name": "Vendor A"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 40.0,
                "artificial_analysis_coding_index": 30.0,
            },
        },
        {
            "slug": "model-b",
            "name": "Model B (High)",
            "model_creator": {"name": "Vendor B"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 55.0,
                "artificial_analysis_coding_index": None,
                "artificial_analysis_agentic_index": 35.0,
            },
        },
        {
            "slug": "model-c",
            "name": "Model C",
            "model_creator": {"name": "Vendor C"},
            "evaluations": {"artificial_analysis_intelligence_index": None},
        },
    ],
}


class TestParsingSelect(unittest.TestCase):
    def test_parse_captures_creator_and_version(self):
        rows, version = aa.parse_pages([PAGE], [77])
        self.assertEqual(version, 4.3)
        self.assertEqual(rows[0].creator, "Vendor A")
        self.assertEqual(rows[0].release_date, "2026-01-01")
        self.assertEqual(rows[0].raw_id, 77)

    def test_family_key_and_clean_name(self):
        self.assertEqual(aa.family_key("Model A (Max, Default Fallback)"),
                         aa.family_key("Model A (Low)"))
        self.assertEqual(aa.clean_name("Model A (Max, Default Fallback)"),
                         "Model A (Max)")
        self.assertEqual(aa.clean_name("Claude Fable 5 (Max, Opus 4.8 Fallback)"),
                         "Claude Fable 5 (Max)")

    def test_select_universe_dedupes_family_and_takes_top_n(self):
        rows, _ = aa.parse_pages([PAGE], [77])
        selected = aa.select_universe(rows, top_n=2)
        self.assertEqual([r.slug for r in selected], ["model-a-max", "model-b"])
        self.assertEqual(selected[0].name, "Model A (Max)")

    def test_metrics_mapping_skips_nulls(self):
        rows, _ = aa.parse_pages([PAGE], [77])
        metrics = aa.metrics_for_rows(rows[:2], {"model-a-max": 10, "model-a-low": 11}, NOW)
        by_key = {m.metric_key: m for m in metrics if m.product_id == 10}
        self.assertEqual(by_key["aa_intelligence"].value, 60.0)
        self.assertEqual(by_key["aa_coding"].value, 50.0)
        self.assertIn("aa_agentic", by_key)
        self.assertTrue(all(m.raw_payload_id == 77 for m in metrics))


class AaDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "aa_models.json"
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self._cache_patch = patch.object(aa, "CACHE_FILE", self.cache)
        self._cache_patch.start()
        self._db_patch = patch.object(db, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(db.DDL)
        db.ensure_product_columns(self.c)

    def tearDown(self):
        self.c.close()
        self._db_patch.stop()
        self._cache_patch.stop()
        self.tmp.cleanup()

    def _ctx(self, products=None):
        return SimpleNamespace(conn=self.c, products=products or [],
                               source_ids={"aa": 1}, now=NOW, http=None)

    def _fresh_cache(self):
        rows, _ = aa.parse_pages([PAGE], [77])
        aa._save_cache(rows, 4.3)

    def test_cache_hit_syncs_products_and_replays_metrics(self):
        self._fresh_cache()
        ctx = self._ctx()
        with patch.object(aa, "_fetch_all",
                          side_effect=AssertionError("不应请求 AA")):
            metrics, notes = AASource().collect(ctx)
        self.assertTrue(metrics)
        self.assertTrue(any("缓存有效" in n for n in notes))
        self.c.commit()
        rows = [dict(r) for r in self.c.execute(
            "SELECT slug,name,origin,status,board_tags FROM products ORDER BY slug")]
        self.assertEqual(len(rows), 2)  # Model A 去重 + Model C 无总评分
        self.assertTrue(all(r["origin"] == "aa" and r["status"] == "active"
                            for r in rows))
        self.assertTrue(all(json.loads(r["board_tags"]) == ["model"] for r in rows))

    def test_stale_cache_refetches_and_archives_old_models(self):
        self.cache.write_text(json.dumps(
            {"fetched_at": "2020-01-01T00:00:00Z", "rows": []}), encoding="utf-8")
        # 旧 AA 模型与旧 SuperCLUE 模型都应被归档
        self.c.execute(
            "INSERT INTO products(slug,name,vendor,category,origin,status,"
            " board_tags,created_at,updated_at)"
            " VALUES('aa-old','Old AA','V','model','aa','active','[\"model\"]',?,?)",
            (NOW, NOW))
        self.c.execute(
            "INSERT INTO products(slug,name,vendor,category,origin,status,"
            " board_tags,created_at,updated_at)"
            " VALUES('old-superclue','Old SC','V','model','registry','active',"
            " '[\"model\"]',?,?)", (NOW, NOW))
        self.c.commit()
        rows, version = aa.parse_pages([PAGE], [77])
        ctx = self._ctx()
        with patch.object(aa, "_env_key", return_value="test-key"), \
                patch.object(aa, "_fetch_all",
                             return_value=(rows, version, ["1 页已归档"])):
            metrics, notes = AASource().collect(ctx)
        self.assertTrue(metrics)
        self.c.commit()
        status = {r["slug"]: r["status"] for r in self.c.execute(
            "SELECT slug,status FROM products")}
        self.assertEqual(status["aa-old"], "archived")
        self.assertEqual(status["old-superclue"], "archived")
        self.assertEqual(status["aa-model-a-max"], "active")
        self.assertTrue(aa.cache_fresh(aa._load_cache(), 7))

    def test_registry_product_reused_by_aa_slug(self):
        self.c.execute(
            "INSERT INTO products(slug,name,vendor,category,origin,aa_slug,status,"
            " board_tags,created_at,updated_at)"
            " VALUES('registry-a','Old Name','Vendor A','model','registry',"
            " 'model-a-max','active','[\"model\"]',?,?)", (NOW, NOW))
        self.c.commit()
        rows, _ = aa.parse_pages([PAGE], [77])
        selected = aa.select_universe(rows, 2)
        ids, _ = aa.sync_products(
            self.c, selected,
            [{"id": 1, "slug": "registry-a", "category": "model",
              "aa_slug": "model-a-max", "vendor": "Vendor A"}])
        self.c.commit()
        row = self.c.execute("SELECT * FROM products WHERE id=1").fetchone()
        self.assertEqual(row["slug"], "registry-a")
        self.assertEqual(row["origin"], "aa")
        self.assertEqual(row["name"], "Model A (Max)")

    def test_missing_key_and_stale_cache_raises(self):
        self.cache.write_text(json.dumps(
            {"fetched_at": "2020-01-01T00:00:00Z", "rows": []}), encoding="utf-8")
        with patch.object(aa, "_env_key", return_value=None):
            with self.assertRaises(aa.AAError):
                AASource().collect(self._ctx())


class TestAaRegistry(unittest.TestCase):
    def test_source_and_model_board_use_aa(self):
        from collector.sources import SOURCE_REGISTRY
        self.assertIn(AASource, SOURCE_REGISTRY)
        reg = aa.registry()
        src = next(s for s in reg["sources"] if s["key"] == "aa")
        self.assertTrue(src["enabled"])
        self.assertEqual(src.get("top_n"), 40)
        self.assertEqual(src.get("cache_days"), 7)
        self.assertFalse(next(s for s in reg["sources"]
                              if s["key"] == "superclue")["enabled"])
        board = reg["boards"]["model"]
        self.assertEqual(board["select"], {"origin": "aa", "status": "active"})
        self.assertEqual(board["heat"], {"aa_intelligence": 1.0})
        self.assertIn("aa_intelligence", board["display_metrics"])
        self.assertNotIn("sc_total", board["display_metrics"])


class TestCacheFreshness(unittest.TestCase):
    def test_window(self):
        now = datetime.now(timezone.utc)
        fresh = {"fetched_at": (now - timedelta(days=1)).isoformat()}
        stale = {"fetched_at": (now - timedelta(days=8)).isoformat()}
        self.assertTrue(aa.cache_fresh(fresh, 7))
        self.assertFalse(aa.cache_fresh(stale, 7))
        self.assertFalse(aa.cache_fresh(None, 7))


if __name__ == "__main__":
    unittest.main()
