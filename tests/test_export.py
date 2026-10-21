"""dist/api/v1/raw 导出窗口：只导出最近 raw_api_days 天 + 全量 index.json 清单。

验证 2026-10-21 瘦身方案：窗口外 payload 不再写 JSON（旧文件也会被清理），
但 index.json 仍列出全部 payload 的 id/来源/时间/大小与溯源页地址。
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests  # noqa: E402,F401  单测护栏:离线 + 不调真实模型


def iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def seed(db_mod, tmp: Path):
    """最小可导出库：2 产品 + 核心榜 + 1 旧/1 新 raw payload。"""
    db_mod.DATA_DIR = tmp
    db_mod.RAW_DIR = tmp / "raw"
    with db_mod.conn() as c:
        c.executescript(db_mod.DDL)
        db_mod.ensure_news_tables(c)
        db_mod.ensure_ai_tables(c)
        sid = c.execute(
            "INSERT INTO sources(key,name,source_type) VALUES('github','GitHub','api')"
        ).lastrowid
        for pid, slug, name in ((1, "alpha", "Alpha"), (2, "beta", "Beta")):
            c.execute(
                "INSERT INTO products(id,slug,name,vendor,category,tier,origin,"
                " status,board_tags,created_at,updated_at) VALUES"
                " (?,?,?,?,?,?,?,'active',?,?,?)",
                (pid, slug, name, "Vendor", "coding", "core", "registry",
                 '["harness_core","coding"]', "2026-10-01", "2026-10-01"))
            c.execute(
                "INSERT INTO rankings(board,snapshot_date,rank,product_id,"
                " heat_score,coverage,methodology_version)"
                " VALUES('harness_core','2026-10-20',?,?,?,0.9,'v1.8')",
                (pid, pid, 90.0 - pid))
        old = db_mod.save_raw(c, sid, "github", 1, "http://x/old", 200,
                              b'{"old":true}', "application/json", True)
        c.execute("UPDATE raw_payloads SET fetched_at=? WHERE id=?",
                  (iso_days_ago(40), old))
        recent = db_mod.save_raw(c, sid, "github", 2, "http://x/recent", 200,
                                 b'{"recent":true}', "application/json", True)
    return old, recent


class ExportRawWindowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        import collector.db as db
        import collector.export as export
        self.db, self.export = db, export
        self._patches = [
            patch.object(db, "DB_PATH", self.tmp / "t.sqlite3"),
            patch.object(db, "DATA_DIR", self.tmp),
            patch.object(db, "RAW_DIR", self.tmp / "raw"),
            patch.object(export, "DIST_DIR", self.tmp / "dist"),
            patch.object(export, "SNAPSHOT_DIR", self.tmp / "snapshots"),
            patch.object(export, "ensure_dirs", lambda: None),
        ]
        for p in self._patches:
            p.start()
        self.old, self.recent = seed(db, self.tmp)
        self.raw_dir = self.tmp / "dist" / "api" / "v1" / "raw"

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _run(self, raw_api_days: int) -> dict:
        from collector.config import registry
        reg = copy.deepcopy(registry())
        reg["retention"]["raw_api_days"] = raw_api_days
        with patch.object(self.export, "registry", lambda: reg):
            self.export.export_all()
        return json.loads((self.raw_dir / "index.json").read_text("utf-8"))

    def test_window_exports_only_recent_payload(self):
        index = self._run(14)
        self.assertTrue((self.raw_dir / f"{self.recent}.json").is_file())
        self.assertFalse((self.raw_dir / f"{self.old}.json").exists())
        self.assertEqual(index["meta"]["window_days"], 14)
        self.assertEqual(index["meta"]["total"], 2)
        self.assertEqual(index["meta"]["exported"], 1)
        self.assertTrue(index["meta"]["cutoff"])

    def test_index_lists_all_with_source_time_size(self):
        index = self._run(14)
        by_id = {it["id"]: it for it in index["items"]}
        old, recent = by_id[self.old], by_id[self.recent]
        for it in (old, recent):
            self.assertEqual(it["source"]["key"], "github")
            self.assertEqual(it["source"]["name"], "GitHub")
            self.assertTrue(it["fetched_at"])
            self.assertGreater(it["size_bytes"], 0)
            self.assertEqual(it["trace_url"], f"/raw/{it['id']}.html")
        self.assertFalse(old["exported"])
        self.assertIsNone(old["api_path"])
        self.assertTrue(recent["exported"])
        self.assertEqual(recent["api_path"], f"raw/{self.recent}.json")
        self.assertTrue(old["fetched_at"] < recent["fetched_at"])

    def test_stale_files_from_previous_build_are_pruned(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        stale = self.raw_dir / "999.json"
        stale.write_text("{}", encoding="utf-8")
        (self.raw_dir / "999.json.gz").write_bytes(b"x")
        self._run(14)
        self.assertFalse(stale.exists())
        self.assertFalse((self.raw_dir / "999.json.gz").exists())

    def test_zero_days_means_no_window(self):
        index = self._run(0)
        self.assertTrue((self.raw_dir / f"{self.old}.json").is_file())
        self.assertTrue((self.raw_dir / f"{self.recent}.json").is_file())
        self.assertEqual(index["meta"]["window_days"], 0)
        self.assertIsNone(index["meta"]["cutoff"])
        self.assertEqual(index["meta"]["exported"], 2)


if __name__ == "__main__":
    unittest.main()
