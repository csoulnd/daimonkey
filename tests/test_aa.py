"""Artificial Analysis 源测试：解析、模型匹配、指标映射、七天缓存（不发真实请求）。"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collector.config import now_iso
from collector.sources import aa
from collector.sources.aa import AARow, AASource

NOW = "2026-10-19T00:00:00Z"

PAYLOAD = {
    "tier": "free",
    "intelligence_index_version": 4.3,
    "pagination": {"page": 1, "page_size": 200, "total_pages": 1, "has_more": False},
    "data": [
        {
            "slug": "gpt-5",
            "name": "GPT-5 (High)",
            "evaluations": {
                "artificial_analysis_intelligence_index": 23.0,
                "artificial_analysis_coding_index": 20.5,
                "artificial_analysis_agentic_index": None,
                "artificial_analysis_engineering_index": 18.2,
            },
        },
        {
            "slug": "gemini-2-5-pro",
            "name": "Gemini 2.5 Pro",
            "evaluations": {
                "artificial_analysis_intelligence_index": 16.1,
                "artificial_analysis_coding_index": None,
                "artificial_analysis_agentic_index": 12.0,
            },
        },
        {
            "slug": "unrelated-model",
            "name": "Unrelated Model",
            "evaluations": {"artificial_analysis_intelligence_index": 5.0},
        },
    ],
}

PRODUCTS = [
    {"id": 1, "name": "GPT-5(high)", "category": "model", "aa_slug": "gpt-5"},
    {"id": 2, "name": "Gemini-2.5-Pro", "category": "model"},
    {"id": 3, "name": "Unrelated Model", "category": "model"},
    {"id": 4, "name": "Unmatched", "category": "model"},
    {"id": 5, "name": "GPT-5 (High)", "category": "coding"},
]


class TestParsingMatching(unittest.TestCase):
    def test_parse_pages_flat_rows_and_version(self):
        rows, version = aa.parse_pages([PAYLOAD], [77])
        self.assertEqual(version, 4.3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].slug, "gpt-5")
        self.assertEqual(rows[0].raw_id, 77)

    def test_match_prefers_aa_slug_and_unique_name(self):
        rows, _ = aa.parse_pages([PAYLOAD], [77])
        matches = aa.match_models(rows, PRODUCTS)
        by_id = {p["id"]: row.slug for p, row in matches}
        self.assertEqual(by_id[1], "gpt-5")       # aa_slug 精确
        self.assertEqual(by_id[2], "gemini-2-5-pro")  # 规范化名称唯一
        self.assertEqual(by_id[3], "unrelated-model")
        self.assertNotIn(4, by_id)                # 无匹配
        self.assertNotIn(5, by_id)                # 非 model 分类不参与

    def test_metrics_mapping_skips_nulls(self):
        rows, _ = aa.parse_pages([PAYLOAD], [77])
        metrics = aa.metrics_from_matches(aa.match_models(rows, PRODUCTS), NOW)
        gpt = {m.metric_key: m for m in metrics if m.product_id == 1}
        self.assertEqual(gpt["aa_intelligence"].value, 23.0)
        self.assertEqual(gpt["aa_coding"].value, 20.5)
        self.assertNotIn("aa_agentic", gpt)       # null 不产出行
        self.assertEqual(gpt["aa_engineering"].value, 18.2)
        self.assertTrue(all(m.raw_payload_id == 77 for m in metrics))


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "aa_models.json"
        self._cache_patch = patch.object(aa, "CACHE_FILE", self.cache)
        self._cache_patch.start()

    def tearDown(self):
        self._cache_patch.stop()
        self.tmp.cleanup()

    def test_freshness_window(self):
        now = datetime.now(timezone.utc)
        fresh = {"fetched_at": (now - timedelta(days=1)).isoformat()}
        stale = {"fetched_at": (now - timedelta(days=8)).isoformat()}
        self.assertTrue(aa.cache_fresh(fresh, 7))
        self.assertFalse(aa.cache_fresh(stale, 7))
        self.assertFalse(aa.cache_fresh(None, 7))

    def test_collect_uses_fresh_cache_without_fetch(self):
        rows, _ = aa.parse_pages([PAYLOAD], [77])
        aa._save_cache(rows, 4.3)
        ctx = SimpleNamespace(products=PRODUCTS, conn=None, source_ids={"aa": 1},
                              now=NOW)
        with patch.object(aa, "_fetch_all",
                          side_effect=AssertionError("不应请求 AA")):
            metrics, notes = AASource().collect(ctx)
        self.assertEqual(metrics, [])
        self.assertTrue(any("缓存有效" in n for n in notes))

    def test_collect_refetches_when_stale_and_rewrites_cache(self):
        self.cache.write_text(json.dumps(
            {"fetched_at": "2020-01-01T00:00:00Z", "index_version": 4.2,
             "rows": []}), encoding="utf-8")
        rows, version = aa.parse_pages([PAYLOAD], [77])
        ctx = SimpleNamespace(products=PRODUCTS, conn=None, source_ids={"aa": 1},
                              now=NOW)
        with patch.object(aa, "_env_key", return_value="test-key"), \
                patch.object(aa, "_fetch_all",
                             return_value=(rows, version, ["2 页已归档"])):
            metrics, notes = AASource().collect(ctx)
        self.assertTrue(metrics)
        self.assertTrue(any("匹配 3/4" in n for n in notes))
        cache = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(cache["index_version"], 4.3)
        self.assertTrue(aa.cache_fresh(cache, 7))

    def test_missing_key_and_stale_cache_raises(self):
        self.cache.write_text(json.dumps(
            {"fetched_at": "2020-01-01T00:00:00Z", "rows": []}), encoding="utf-8")
        ctx = SimpleNamespace(products=PRODUCTS, conn=None, source_ids={"aa": 1},
                              now=NOW)
        with patch.object(aa, "_env_key", return_value=None):
            with self.assertRaises(aa.AAError):
                AASource().collect(ctx)


class TestAaRegistry(unittest.TestCase):
    def test_source_registered_and_registry_entry(self):
        from collector.sources import SOURCE_REGISTRY
        self.assertIn(AASource, SOURCE_REGISTRY)
        src = next(s for s in aa.registry()["sources"] if s["key"] == "aa")
        self.assertTrue(src["enabled"])
        self.assertEqual(src.get("cache_days"), 7)
        self.assertEqual(src.get("auth"), "required")

    def test_model_board_has_aa_columns(self):
        board = aa.registry()["boards"]["model"]
        self.assertIn("aa_intelligence", board["display_metrics"])
        self.assertIn("aa_coding", board["display_metrics"])


if __name__ == "__main__":
    unittest.main()
