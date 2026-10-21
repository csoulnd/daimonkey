"""MLPerf 源测试：解析归一、单卡折算、产品同步与七天缓存（不发真实请求）。"""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collector import db
from collector.sources import mlperf
from collector.sources.mlperf import (
    MlperfError, MlperfSource, parse_summary, normalize_accel, slugify)

NOW = "2026-10-21T00:00:00Z"


def rec(**kw):
    base = {
        "Division": "closed", "SystemType": "datacenter", "Scenario": "Offline",
        "Model": "gpt-oss-120b", "Result": 80000.0, "total_accelerators": 8,
        "accelerator_model_name": "NVIDIA B300-SXM-270GB",
    }
    base.update(kw)
    return base


PAYLOAD = {
    "0": rec(),                                                  # 10000/卡
    "1": rec(**{"accelerator_model_name": "Nvidia B300-SXM-270GB",
                "Result": 88000.0}),                             # 11000/卡，大小写合并取大
    "2": rec(**{"Model": "deepseek-r1", "Result": 64000.0}),     # 8000/卡
    "3": rec(**{"Model": "llama3.1-8b", "Result": 160000.0,
                "accelerator_model_name": "AMD Instinct MI355X 288GB HBM3e"}),  # 20000
    "4": rec(**{"Division": "open"}),                            # 跳过
    "5": rec(**{"SystemType": "edge"}),                          # 跳过
    "6": rec(**{"Scenario": "Server"}),                          # 只取 Offline
    "7": rec(**{"Model": "resnet50"}),                           # 非 LLM
    "8": rec(**{"accelerator_model_name": "A + B"}),             # 混合卡跳过
    "9": rec(**{"accelerator_model_name": "A and B"}),           # 混合卡跳过
}


class TestParse(unittest.TestCase):
    def test_normalizes_and_folds_per_accelerator(self):
        rows = parse_summary(PAYLOAD, 42)
        by_name = {r.name: r for r in rows}
        self.assertIn("NVIDIA B300-SXM-270GB", by_name)      # Nvidia → NVIDIA
        self.assertEqual(len(rows), 2)                        # 混合/非 LLM/非 closed 全跳过
        b300 = by_name["NVIDIA B300-SXM-270GB"]
        self.assertEqual(b300.values["mlperf_gptoss_120b"], 11000.0)
        self.assertEqual(b300.values["mlperf_deepseek_r1"], 8000.0)
        self.assertEqual(b300.vendor, "NVIDIA")
        self.assertEqual(b300.slug, "mlperf-nvidia-b300-sxm-270gb")
        amd = by_name["AMD Instinct MI355X 288GB HBM3e"]
        self.assertEqual(amd.values["mlperf_llama3_8b"], 20000.0)

    def test_single_accelerator_result_is_per_chip(self):
        rows = parse_summary({"0": rec(Result=4000.0, total_accelerators=1)}, 1)
        self.assertEqual(rows[0].values["mlperf_gptoss_120b"], 4000.0)

    def test_vendor_and_alias_normalization(self):
        self.assertEqual(normalize_accel("  nvidia   h100  "), "NVIDIA h100")
        self.assertEqual(normalize_accel("Intel(R) Arc(TM) Pro B70"),
                         "Intel Arc(TM) Pro B70")
        self.assertEqual(normalize_accel("VR200", {"VR200": "NVIDIA Vera Rubin"}),
                         "NVIDIA Vera Rubin")
        self.assertEqual(slugify("AMD Instinct MI355X 288GB HBM3e"),
                         "mlperf-amd-instinct-mi355x-288gb-hbm3e")

    def test_raises_when_nothing_usable(self):
        with self.assertRaises(MlperfError):
            parse_summary({"0": rec(Division="open")}, 1)


class TestCache(unittest.TestCase):
    def test_fresh_and_stale(self):
        now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        fresh = {"fetched_at": "2026-10-18T00:00:00Z"}
        stale = {"fetched_at": "2026-09-01T00:00:00Z"}
        self.assertTrue(mlperf.cache_fresh(fresh, 7, now))
        self.assertFalse(mlperf.cache_fresh(stale, 7, now))
        self.assertFalse(mlperf.cache_fresh(None, 7, now))


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp.name)
        self.db_path = self.cache_dir / "test.sqlite3"
        self._cache_patch = patch.object(mlperf, "CACHE_DIR", self.cache_dir)
        self._cache_patch.start()
        self._db_patch = patch.object(db, "DB_PATH", self.db_path)
        self._db_patch.start()
        self._db_patch2 = patch.object(db, "RAW_DIR", self.cache_dir / "raw")
        self._db_patch2.start()
        self._db_patch3 = patch.object(db, "DATA_DIR", self.cache_dir)
        self._db_patch3.start()
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(db.DDL)
        db.ensure_product_columns(self.c)
        self.c.execute("INSERT INTO sources(id, key, name, source_type)"
                       " VALUES(1, 'mlperf', 'MLPerf', 'api')")
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self._db_patch3.stop()
        self._db_patch2.stop()
        self._db_patch.stop()
        self._cache_patch.stop()
        self.tmp.cleanup()

    def _seed_cache(self, raw_id=77):
        rows = parse_summary(PAYLOAD, raw_id)
        mlperf._save_cache(rows, raw_id)
        return rows

    def _ctx(self):
        http = SimpleNamespace(fetch=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("缓存有效时不应请求")))
        return SimpleNamespace(conn=self.c, products=[], source_ids={"mlperf": 1},
                               now=NOW, http=http)

    def test_cache_hit_syncs_products_and_metrics(self):
        self._seed_cache()
        ctx = self._ctx()
        metrics, notes = MlperfSource().collect(ctx)
        self.assertTrue(any("缓存有效" in n for n in notes))
        self.assertTrue(metrics)
        self.assertTrue(all(m.raw_payload_id == 77 for m in metrics))
        rows = [dict(r) for r in self.c.execute(
            "SELECT slug,origin,status,board_tags FROM products ORDER BY slug")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["origin"] == "mlperf" and r["status"] == "active"
                            for r in rows))
        self.assertTrue(all(json.loads(r["board_tags"]) == ["nv_infer"] for r in rows))

    def test_stale_products_are_archived(self):
        self._seed_cache()
        self.c.execute(
            "INSERT INTO products(slug,name,vendor,category,origin,status,"
            " board_tags,created_at,updated_at)"
            " VALUES('mlperf-old','Old GPU','V','nv','mlperf','active','[\"nv_infer\"]',?,?)",
            (NOW, NOW))
        MlperfSource().collect(self._ctx())
        old = self.c.execute(
            "SELECT status FROM products WHERE slug='mlperf-old'").fetchone()
        self.assertEqual(old["status"], "archived")

    def test_stale_cache_refetches_and_saves_raw(self):
        (self.cache_dir / "mlperf_summary.json").write_text(
            json.dumps({"fetched_at": "2020-01-01T00:00:00Z"}), encoding="utf-8")
        body = json.dumps(PAYLOAD).encode()
        http = SimpleNamespace(fetch=lambda *a, **k: SimpleNamespace(
            status_code=200, content=body, json=lambda: PAYLOAD))
        ctx = SimpleNamespace(conn=self.c, products=[], source_ids={"mlperf": 1},
                              now=NOW, http=http)
        metrics, notes = MlperfSource().collect(ctx)
        self.assertTrue(any("已归档" in n for n in notes))
        raw = self.c.execute("SELECT body_path,http_status FROM raw_payloads").fetchone()
        self.assertIsNotNone(raw)
        self.assertEqual(raw["http_status"], 200)
        self.assertTrue(metrics)

    def test_error_on_bad_payload(self):
        body = b"not json"
        http = SimpleNamespace(fetch=lambda *a, **k: SimpleNamespace(
            status_code=200, content=body,
            json=lambda: (_ for _ in ()).throw(ValueError("bad"))))
        ctx = SimpleNamespace(conn=self.c, products=[], source_ids={"mlperf": 1},
                              now=NOW, http=http)
        with self.assertRaises(MlperfError):
            MlperfSource().collect(ctx)


if __name__ == "__main__":
    unittest.main()
