"""助手核心逻辑：配额、会话 CRUD、工作流注册、用量记账。DSH 调用全部 mock。"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class AssistantCoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # 独立 SQLite：把 db.DB_PATH 指到临时库，再重载 assistant 拿到干净状态
        import collector.config as config
        import collector.db as db
        self._db_patch = patch.object(db, "DB_PATH",
                                      Path(self._tmp.name) / "test.sqlite3")
        self._db_patch.start()
        with db.conn() as c:
            db.ensure_assistant_tables(c)
        import collector.assistant as assistant
        importlib.reload(assistant)
        self.assistant = assistant

    def tearDown(self):
        self._db_patch.stop()
        self._tmp.cleanup()

    def test_session_crud(self):
        a = self.assistant
        sid = a.create_session("测试会话")
        self.assertIsInstance(sid, int)
        sessions = a.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["title"], "测试会话")
        # 重命名：成功 / 空标题拒绝 / 不存在的会话
        self.assertTrue(a.rename_session(sid, "新名字"))
        self.assertEqual(a.list_sessions()[0]["title"], "新名字")
        self.assertFalse(a.rename_session(sid, "   "))
        self.assertFalse(a.rename_session(99999, "不存在"))
        # 超长标题截断到 80 字符
        self.assertTrue(a.rename_session(sid, "长" * 200))
        self.assertEqual(len(a.list_sessions()[0]["title"]), 80)
        # 消息追加（直接走内部 API，绕过 DSH）
        import collector.db as db
        with db.conn() as c:
            a._append_message(c, sid, "user", "你好", "interactive", None)
        messages = a.session_messages(sid)
        self.assertEqual(messages[0]["content"], "你好")
        self.assertTrue(a.delete_session(sid))
        self.assertEqual(a.list_sessions(), [])
        self.assertFalse(a.delete_session(sid))

    def test_list_sessions_active_filter(self):
        """会话下拉只显示近 N 天活跃会话；过期的保留在库但不再列出。"""
        a = self.assistant
        import collector.db as db
        from datetime import datetime, timedelta, timezone

        def local_iso(days_ago: float) -> str:
            return (datetime.now(timezone.utc).astimezone()
                    - timedelta(days=days_ago)).isoformat(timespec="seconds")

        fresh = a.create_session("活跃会话")
        stale = a.create_session("过期会话")
        with db.conn() as c:
            c.execute("UPDATE assistant_sessions SET updated_at = ? WHERE id = ?",
                      (local_iso(30), stale))
        titles = [s["title"] for s in a.list_sessions()]
        self.assertIn("活跃会话", titles)
        self.assertNotIn("过期会话", titles)
        # 过期会话仍在库中，消息可正常读取
        self.assertTrue(a.session_messages(stale) == [])

    def test_quota_enforced_per_channel(self):
        a = self.assistant
        # 灌满 interactive 当日请求数（limit=100）
        import collector.db as db
        with db.conn() as c:
            for _ in range(100):
                a._record_usage(c, "interactive", 10, 10)
        with self.assertRaises(a.AssistantQuota):
            a.check_quota("interactive")
        # internal 通道不受影响
        a.check_quota("internal")
        # token 硬顶同样触发
        with db.conn() as c:
            a._record_usage(c, "internal", 1_000_000, 0)
        with self.assertRaises(a.AssistantQuota):
            a.check_quota("internal")

    def test_usage_summary_shape(self):
        summary = self.assistant.usage_summary()
        self.assertIn("interactive", summary)
        self.assertIn("internal", summary)
        self.assertEqual(summary["interactive"]["requests"], 0)
        self.assertEqual(summary["interactive"]["limit"], 100)

    def test_workflows_registered_and_unknown_rejected(self):
        from collector import workflows
        names = [w["name"] for w in workflows.describe()]
        self.assertIn("news_digest_summary", names)
        self.assertIn("content_polish", names)
        self.assertIn("site_improvement_notes", names)
        spec = workflows.get("content_polish")
        task = spec.build_task({"text": "这是一段文案"})
        self.assertIn("这是一段文案", task)
        with self.assertRaises(self.assistant.AssistantError):
            workflows.get("no-such-workflow")
        # 润色工作流缺输入时报业务错误
        with self.assertRaises(self.assistant.AssistantError):
            spec.build_task({})

    def test_run_chat_rejects_blank_and_oversized(self):
        a = self.assistant
        sid = a.create_session("")
        with self.assertRaises(a.AssistantError):
            a.run_chat(sid, "   ")
        with self.assertRaises(a.AssistantError) as ctx:
            a.run_chat(sid, "x" * 8001)
        self.assertIn("过长", str(ctx.exception))

    def test_execution_lock_is_reentrant_across_processes(self):
        a = self.assistant
        # 锁文件互斥：持锁期间再次获取应报 AssistantBusy
        with patch.object(a, "LOCK_PATH", Path(self._tmp.name) / "assistant.lock"):
            with a._execution_lock():
                with self.assertRaises(a.AssistantBusy):
                    with a._execution_lock():
                        pass


if __name__ == "__main__":
    unittest.main()
