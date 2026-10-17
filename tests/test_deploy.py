"""部署可搬迁性测试：相对路径写入/读取、绝对路径迁移、目录搬迁后溯源可用。"""
import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import collector.db as db

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "migrate_relpath", ROOT / "scripts" / "migrate_relpath.py")
migrate_relpath = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_relpath)

PAYLOAD = b'{"ok":true,"note":"relpath"}'


class TestRelPathMigration(unittest.TestCase):
    def setUp(self):
        self._orig = (db.DATA_DIR, db.DB_PATH, db.RAW_DIR)
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        db.DATA_DIR = self.data
        db.DB_PATH = self.data / "t.sqlite"
        db.RAW_DIR = self.data / "raw"
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
            self.rid = db.save_raw(self.c, sid, "npm", pid, "http://x/a", 200,
                                   PAYLOAD, "application/json", True)

    def tearDown(self):
        self.c.close()
        db.DATA_DIR, db.DB_PATH, db.RAW_DIR = self._orig
        self.tmp.cleanup()

    def _row_path(self) -> str:
        return self.c.execute("SELECT body_path FROM raw_payloads WHERE id=?",
                              (self.rid,)).fetchone()["body_path"]

    def test_save_raw_stores_relative_path(self):
        stored = self._row_path()
        self.assertFalse(stored.startswith("/"))
        self.assertTrue(stored.startswith("raw/npm/"))
        self.assertTrue((self.data / stored).is_file())

    def test_legacy_absolute_path_migrated_and_idempotent(self):
        rel = self._row_path()
        with self.c:
            self.c.execute("UPDATE raw_payloads SET body_path=? WHERE id=?",
                           (str(self.data / rel), self.rid))
        s = migrate_relpath.migrate(self.data / "t.sqlite", self.data)
        self.assertEqual(s["converted"], 1)
        self.assertEqual(self._row_path(), rel)
        # 再跑一次不重复改写
        s2 = migrate_relpath.migrate(self.data / "t.sqlite", self.data)
        self.assertEqual(s2["converted"], 0)
        self.assertEqual(s2["already_relative"], 1)
        self.assertEqual(db.load_body(self.c, self.rid), PAYLOAD)

    def test_old_install_root_migrated(self):
        """旧机器路径不在当前 DATA_DIR 下时，按最后一个 data/ 段切分。"""
        rel = self._row_path()
        with self.c:
            self.c.execute("UPDATE raw_payloads SET body_path=? WHERE id=?",
                           (f"/old/server/data/{rel}", self.rid))
        s = migrate_relpath.migrate(self.data / "t.sqlite", self.data)
        self.assertEqual(s["converted"], 1)
        self.assertEqual(self._row_path(), rel)

    def test_dry_run_does_not_write(self):
        rel = self._row_path()
        with self.c:
            self.c.execute("UPDATE raw_payloads SET body_path=? WHERE id=?",
                           (str(self.data / rel), self.rid))
        s = migrate_relpath.migrate(self.data / "t.sqlite", self.data,
                                    dry_run=True)
        self.assertEqual(s["converted"], 1)
        self.assertTrue(self._row_path().startswith("/"))
        self.assertEqual(s["missing_file"], 0)

    def test_trace_survives_directory_move(self):
        """整目录搬迁后，相对路径仍能在新 DATA_DIR 下找到响应体。"""
        self.c.close()
        moved = Path(self.tmp.name) / "moved"
        shutil.copytree(self.data, moved)
        db.DATA_DIR = moved
        db.DB_PATH = moved / "t.sqlite"
        db.RAW_DIR = moved / "raw"
        c2 = db.conn()
        try:
            self.assertEqual(db.load_body(c2, self.rid), PAYLOAD)
        finally:
            c2.close()
        # tearDown 再次 close 原连接会抛错，重置为已关闭的连接
        self.c = c2

    def test_missing_body_returns_none(self):
        (self.data / self._row_path()).unlink()
        self.assertIsNone(db.load_body(self.c, self.rid))


if __name__ == "__main__":
    unittest.main()
