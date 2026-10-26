"""LRU 保留策略测试：响应体到期清理（元数据保留）、指标/快照按窗口删除、缓存过期。"""
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import collector.db as db
import collector.retention as retention


def iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "t.sqlite"
        db.DATA_DIR = Path(self.tmp.name)
        db.RAW_DIR = db.DATA_DIR / "raw"
        retention.DATA_DIR = db.DATA_DIR
        retention.CACHE_DIR = Path(self.tmp.name) / "cache"
        retention.CACHE_DIR.mkdir(parents=True)
        db.init_db()
        self.c = db.conn()
        with self.c:
            sid = self.c.execute(
                "INSERT INTO sources(key,name,source_type) VALUES('npm','npm','api')"
            ).lastrowid
            pid = self.c.execute(
                "INSERT INTO products(slug,name,category,created_at,updated_at)"
                " VALUES('x','X','coding','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
            ).lastrowid
            # 40 天前的响应（应被清理 body，保留行）与今天的响应（保留全部）
            self.raw_old = db.save_raw(self.c, sid, "npm", pid, "http://x/old",
                                       200, b'{"old":1}', "application/json", True)
            self.c.execute("UPDATE raw_payloads SET fetched_at=? WHERE id=?",
                           (iso_days_ago(40), self.raw_old))
            self.raw_new = db.save_raw(self.c, sid, "npm", pid, "http://x/new",
                                       200, b'{"new":2}', "application/json", True)
            self.c.execute(
                "INSERT INTO metrics(product_id,board,metric_key,value,unit,source_id,"
                " raw_payload_id,collected_at,granularity)"
                " VALUES(?,?,?,?,?,?,?,?, 'daily')",
                (pid, "coding", "m", 1, "count", sid, self.raw_old,
                 iso_days_ago(200)))       # 超过 metrics_days=180 -> 删
            self.c.execute(
                "INSERT INTO metrics(product_id,board,metric_key,value,unit,source_id,"
                " raw_payload_id,collected_at,granularity)"
                " VALUES(?,?,?,?,?,?,?,?, 'daily')",
                (pid, "coding", "m", 2, "count", sid, self.raw_new,
                 iso_days_ago(1)))          # 保留
            self.c.execute(
                "INSERT INTO rankings(board,snapshot_date,rank,product_id,"
                " methodology_version) VALUES('coding','2025-08-01',1,?, 'v1.0')",
                (pid,))                     # 超过 rankings_days=365 -> 删
            # 过期缓存文件（100h > 48h）：GitHub 用 32 位哈希命名
            stale = retention.CACHE_DIR / ("a" * 32 + ".json")
            stale.write_text("{}")
            old = time.time() - 100 * 3600
            os.utime(stale, (old, old))
            (retention.CACHE_DIR / ("b" * 32 + ".json")).write_text("{}")
            # 源级缓存（七天 TTL）即使超过 48h 也不能被删（2026-10-26 回归）
            for name in ("aa_models.json", "mlperf_summary.json"):
                src = retention.CACHE_DIR / name
                src.write_text("{}")
                os.utime(src, (old, old))
        self.counts = retention.apply(self.c)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_raw_body_evicted_but_row_kept(self):
        row = self.c.execute("SELECT * FROM raw_payloads WHERE id=?",
                             (self.raw_old,)).fetchone()
        self.assertIsNone(row["body_path"])
        self.assertIsNotNone(row["body_evicted_at"])
        self.assertIsNotNone(row["body_sha256"])   # 溯源链不断
        self.assertEqual(self.counts["raw_bodies_evicted"], 1)

    def test_recent_raw_untouched(self):
        row = self.c.execute("SELECT * FROM raw_payloads WHERE id=?",
                             (self.raw_new,)).fetchone()
        self.assertIsNotNone(row["body_path"])
        self.assertIsNone(row["body_evicted_at"])

    def test_raw_body_path_is_relative(self):
        """body_path 只存相对 DATA_DIR 的路径，换目录不断链。"""
        row = self.c.execute("SELECT body_path FROM raw_payloads WHERE id=?",
                             (self.raw_new,)).fetchone()
        self.assertFalse(row["body_path"].startswith("/"))
        self.assertTrue(row["body_path"].startswith("raw/"))
        self.assertTrue((db.DATA_DIR / row["body_path"]).is_file())
        self.assertEqual(db.load_body(self.c, self.raw_new), b'{"new":2}')

    def test_old_metrics_deleted_recent_kept(self):
        n = self.c.execute("SELECT COUNT(*) n FROM metrics").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_old_rankings_deleted(self):
        n = self.c.execute("SELECT COUNT(*) n FROM rankings").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_cache_files(self):
        self.assertFalse((retention.CACHE_DIR / ("a" * 32 + ".json")).exists())
        self.assertTrue((retention.CACHE_DIR / ("b" * 32 + ".json")).exists())

    def test_source_level_caches_survive_github_cleanup(self):
        """AA/mlperf 的七天缓存不能被 48h 的 GitHub 缓存清理误删。"""
        for name in ("aa_models.json", "mlperf_summary.json"):
            self.assertTrue((retention.CACHE_DIR / name).exists(), name)
        self.assertEqual(self.counts["cache_files_deleted"], 1)


if __name__ == "__main__":
    unittest.main()
