"""运维 agent(ops)后端测试：策略分级 + pi RPC 轮次协议。

不联网、不调真实模型：run_turn 的 pi 子进程用临时目录里的假 pi 脚本替换，
只验证 JSONL 协议、事件转发、工具卡片、审批回写、取消与超时。
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import ops, piagent

FAKE_PI = r'''#!/usr/bin/env python3
import json, os, sys, time

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

mode = os.environ.get("FAKE_PI_MODE", "happy")
for line in sys.stdin:
    try:
        rec = json.loads(line)
    except Exception:
        continue
    if rec.get("type") == "abort":
        if mode == "abortable":
            send({"type": "agent_settled", "aborted": True})
            break
        continue
    if rec.get("type") != "prompt":
        continue
    send({"id": rec.get("id"), "type": "response", "command": "prompt",
          "success": True, "data": {"disposition": "started"}})
    if mode == "happy":
        send({"type": "message_update",
              "assistantMessageEvent": {"type": "text_delta", "delta": "你好，"}})
        send({"type": "message_update",
              "assistantMessageEvent": {"type": "text_delta", "delta": "世界"}})
        send({"type": "tool_execution_start", "toolCallId": "c1",
              "toolName": "bash", "args": {"command": "git status"}})
        send({"type": "tool_execution_end", "toolCallId": "c1",
              "toolName": "bash", "isError": False, "durationMs": 12,
              "result": {"content": [{"type": "text", "text": "clean"}]}})
        send({"type": "message_end", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "你好，世界"}],
            "stopReason": "stop", "usage": {"input": 11, "output": 5}}})
        send({"type": "agent_settled", "aborted": False})
        break
    if mode == "approval":
        send({"type": "extension_ui_request", "id": "ui-1",
              "method": "confirm", "title": "允许 bash？",
              "message": "git push origin main"})
        resp = sys.stdin.readline()
        with open(os.environ["FAKE_PI_RESP"], "w", encoding="utf-8") as f:
            f.write(resp)
        send({"type": "message_end", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "ok"}],
            "stopReason": "stop", "usage": {"input": 3, "output": 1}}})
        send({"type": "agent_settled", "aborted": False})
        break
    if mode == "abortable":
        send({"type": "message_update",
              "assistantMessageEvent": {"type": "text_delta", "delta": "开始"}})
        continue
    if mode == "hang":
        time.sleep(30)
        break
'''


class FakePiMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "pi"
        self.bin.write_text(FAKE_PI, encoding="utf-8")
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IXUSR)
        self.key_env = "FAKE_DEEPSEEK_KEY"
        os.environ[self.key_env] = "test-key"

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop(self.key_env, None)

    def run_turn(self, mode="happy", **kw):
        os.environ["FAKE_PI_MODE"] = mode
        kw.setdefault("session_id", "dm-test")
        kw.setdefault("session_dir", str(self.dir / "sessions"))
        kw.setdefault("cwd", str(self.dir))
        return piagent.run_turn(
            "跑一下体检", model="deepseek/deepseek-v4-pro",
            api_key_env=self.key_env, pi_override=str(self.bin),
            timeout_s=kw.pop("timeout_s", 20), **kw)


class TestTurnProtocol(FakePiMixin):
    def test_happy_path_events_and_tool_calls(self):
        events = []
        result = self.run_turn(on_event=events.append)
        self.assertEqual(result.reply, "你好，世界")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual((result.tokens_in, result.tokens_out), (11, 5))
        self.assertEqual(result.session_id, "dm-test")
        self.assertFalse(result.aborted)
        self.assertEqual(len(result.tool_calls), 1)
        call = result.tool_calls[0]
        self.assertEqual(call["name"], "bash")
        self.assertEqual(call["status"], "ok")
        self.assertEqual(call["output"], "clean")
        self.assertAlmostEqual(call["duration_s"], 0.012, places=3)
        kinds = [e.get("type") for e in events]
        self.assertIn("message_update", kinds)
        self.assertIn("tool_execution_start", kinds)
        self.assertIn("tool_execution_end", kinds)

    def test_approval_response_written(self):
        resp_path = self.dir / "response.json"
        os.environ["FAKE_PI_RESP"] = str(resp_path)
        seen = []
        try:
            result = self.run_turn(
                mode="approval",
                on_approval=lambda req: seen.append(req) or True,
            )
        finally:
            os.environ.pop("FAKE_PI_RESP", None)
        self.assertEqual(result.reply, "ok")
        self.assertEqual(seen[0]["method"], "confirm")
        resp = json.loads(resp_path.read_text(encoding="utf-8"))
        self.assertEqual(resp, {"type": "extension_ui_response",
                                "id": "ui-1", "confirmed": True})

    def test_approval_denied_fail_closed(self):
        resp_path = self.dir / "response.json"
        os.environ["FAKE_PI_RESP"] = str(resp_path)
        try:
            self.run_turn(mode="approval", on_approval=lambda req: False)
        finally:
            os.environ.pop("FAKE_PI_RESP", None)
        resp = json.loads(resp_path.read_text(encoding="utf-8"))
        self.assertFalse(resp["confirmed"])

    def test_cancel_sends_abort_and_returns_partial(self):
        cancel = threading.Event()
        result = []

        def worker():
            try:
                result.append(self.run_turn(mode="abortable", cancel_event=cancel,
                                            timeout_s=10))
            except Exception as exc:  # pragma: no cover - 失败时给出可读信息
                result.append(exc)

        th = threading.Thread(target=worker)
        th.start()
        time.sleep(0.4)
        cancel.set()
        th.join(timeout=10)
        self.assertFalse(isinstance(result[0], Exception), result[0])
        turn = result[0]
        self.assertTrue(turn.aborted)
        self.assertIn("开始", turn.reply)

    def test_timeout_raises_and_kills(self):
        started = time.monotonic()
        with self.assertRaises(piagent.PiAgentError) as ctx:
            self.run_turn(mode="hang", timeout_s=1)
        self.assertIn("超时", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 10)

    def test_build_turn_args_boundaries(self):
        args = piagent.build_turn_args(
            model="deepseek/deepseek-v4-pro", tools="read,bash",
            thinking="high", system_prompt="x",
            session_id="dm1", session_dir="/tmp/s",
            extension_paths=["/repo/deploy/pi-ext/ops-guard.ts"],
            pi_override="/usr/bin/pi")
        joined = " ".join(args)
        self.assertIn("--mode rpc", joined)
        self.assertIn("--tools read,bash", joined)
        self.assertIn("--thinking high", joined)
        self.assertIn("--session-id dm1", joined)
        self.assertIn("--session-dir /tmp/s", joined)
        self.assertIn("-e /repo/deploy/pi-ext/ops-guard.ts", joined)
        self.assertIn("--no-mcp", joined)
        self.assertNotIn("--no-session", joined)

    def test_missing_extension_fails_before_spawn(self):
        with self.assertRaises(piagent.PiAgentError):
            self.run_turn(extension_paths=[str(self.dir / "nope.ts")])


class TestPolicy(unittest.TestCase):
    def test_secret_paths_denied(self):
        for p in ("data/assistant.env", "data/assistant_secret",
                  ".ssh/id_ed25519", "data/update_password.sha256"):
            decision, _ = ops.classify_path(p, ops._REPO_ROOT, "read")
            self.assertEqual(decision, "deny", p)

    def test_path_escape_denied(self):
        for p in ("/etc/passwd", "../etc/passwd"):
            decision, _ = ops.classify_path(p, ops._REPO_ROOT, "read")
            self.assertEqual(decision, "deny", p)

    def test_write_allow_and_ask(self):
        self.assertEqual(ops.classify_path(
            "collector/export.py", ops._REPO_ROOT, "write")[0], "allow")
        self.assertEqual(ops.classify_path(
            "deploy/daimonkey.service", ops._REPO_ROOT, "write")[0], "ask")
        self.assertEqual(ops.classify_path(
            "data/cache/x.json", ops._REPO_ROOT, "write")[0], "allow")

    def test_bash_classification(self):
        self.assertEqual(ops.classify_bash("rm -rf /")[0], "deny")
        self.assertEqual(ops.classify_bash("cat data/assistant.env")[0], "deny")
        self.assertEqual(ops.classify_bash("git status")[0], "allow")
        self.assertEqual(ops.classify_bash(
            "systemctl restart daimonkey")[0], "ask")
        self.assertEqual(ops.classify_bash(
            "git status && rm -rf x")[0], "ask")
        self.assertEqual(ops.classify_bash("git status > /tmp/x")[0], "ask")

    def test_policy_file_is_valid(self):
        policy = ops.load_policy()
        self.assertGreaterEqual(policy["version"], 1)
        self.assertTrue(policy["secret_paths"])
        self.assertTrue(str(policy["repo_root"]).strip())


if __name__ == "__main__":
    unittest.main()


class TestAssistantOpsIntegration(unittest.TestCase):
    """assistant.start_ops_turn → 后台线程 → DB/事件/审批 全链路（pi 用桩）。"""

    def setUp(self):
        import tests  # noqa: F401  单测护栏
        from collector import assistant, db, users
        self.assistant = assistant
        self.users = users
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._p_db = patch.object(db, "DB_PATH",
                                                self.dir / "test.sqlite")
        self._p_db.start()
        db.init_db()
        users.create_user("alice", "test-password-123")
        assistant._runs.clear()
        assistant._ops_active["run_id"] = None
        self.sid = assistant.create_session("t", owner="alice", mode="ops")
        self.conf = {
            "enabled": True, "model": "fake/model", "thinking": "off",
            "tools": "bash", "timeout_s": 10, "approval_timeout_s": 5,
            "max_tool_calls": 10,
            "session_dir": str(self.dir / "sessions"),
            "audit_dir": str(self.dir / "audit"),
            "extension": None, "daily_limit": 60, "daily_tokens": 10 ** 6,
        }
        self._p_conf = patch.object(
            assistant, "ops_conf", lambda: dict(self.conf))
        self._p_conf.start()
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

    def tearDown(self):
        for rid in list(self.assistant._runs):
            try:
                self.assistant.abort_run(rid)
            except Exception:
                pass
        time.sleep(0.1)
        self._p_conf.stop()
        self._p_db.stop()
        self.tmp.cleanup()
        self.assistant._runs.clear()
        self.assistant._ops_active["run_id"] = None

    def _wait(self, run_id, timeout=6):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.assistant.run_status(run_id, owner="alice")
            if st and st["state"] != "running":
                return st
            time.sleep(0.05)
        self.fail("run 未在时限内结束")

    def test_turn_persists_message_usage_and_tool_calls(self):
        from collector import piagent

        def stub(task, **kw):
            kw["on_event"]({"type": "message_update",
                            "assistantMessageEvent": {"type": "text_delta",
                                                      "delta": "好了"}})
            kw["on_event"]({"type": "tool_execution_start", "toolName": "bash",
                            "args": {"command": "echo hi"}})
            kw["on_event"]({"type": "tool_execution_end", "toolName": "bash",
                            "isError": False, "durationMs": 5,
                            "result": {"content": [{"type": "text", "text": "hi"}]}})
            return piagent.PiTurnResult(
                reply="好了", finish_reason="stop", tokens_in=7, tokens_out=2,
                peak_rss_mb=42.0, duration_s=0.01, session_id="s1",
                tool_calls=[{"name": "bash", "args": {"command": "echo hi"},
                             "status": "ok", "output": "hi", "duration_s": 0.005}],
                aborted=False)

        with patch.object(piagent, "run_turn", stub):
            run_id = self.assistant.start_ops_turn(self.sid, "跑一下", "alice")
            st = self._wait(run_id)

        self.assertEqual(st["state"], "success", st)
        self.assertEqual(st["text"], "好了")
        self.assertEqual(st["tool_count"], 1)
        with self.db.conn() as c:
            msg = c.execute(
                "SELECT content FROM assistant_messages WHERE session_id = ?"
                " AND role = 'assistant'", (self.sid,)).fetchone()
            self.assertEqual(msg["content"], "好了")
            call = c.execute(
                "SELECT tool, status, output FROM assistant_tool_calls"
                " WHERE run_id = ?", (run_id,)).fetchone()
            self.assertEqual((call["tool"], call["status"], call["output"]),
                             ("bash", "ok", "hi"))
            run = c.execute("SELECT mode, status, tool_count, reply"
                            " FROM assistant_runs WHERE run_id = ?",
                            (run_id,)).fetchone()
            self.assertEqual((run["mode"], run["status"], run["tool_count"],
                              run["reply"]), ("ops", "success", 1, "好了"))
            usage = c.execute("SELECT requests FROM assistant_usage"
                              " WHERE channel = 'ops'").fetchone()
            self.assertGreaterEqual(usage["requests"], 1)

    def test_approval_wakes_runner_and_audits_decision(self):
        from collector import piagent
        seen = {}

        def stub(task, **kw):
            seen["allow"] = kw["on_approval"](
                {"id": "a1", "method": "confirm", "title": "允许 bash？",
                 "message": "git push"})
            return piagent.PiTurnResult(
                reply="ok", finish_reason="stop", tokens_in=1, tokens_out=1,
                peak_rss_mb=1.0, duration_s=0.1, session_id="s1",
                tool_calls=[], aborted=False)

        with patch.object(piagent, "run_turn", stub):
            run_id = self.assistant.start_ops_turn(self.sid, "推一下", "alice")
            deadline = time.time() + 5
            while time.time() < deadline:
                st = self.assistant.run_status(run_id, owner="alice")
                if st and st.get("pending"):
                    break
                time.sleep(0.05)
            else:
                self.fail("未出现待审批项")
            approval_id = st["pending"]["id"]
            self.assertTrue(self.assistant.resolve_approval(
                run_id, approval_id, True, owner="alice"))
            st = self._wait(run_id)

        self.assertEqual(st["state"], "success")
        self.assertTrue(seen.get("allow"))
        with self.db.conn() as c:
            row = c.execute("SELECT status FROM assistant_approvals"
                            " WHERE run_id = ?", (run_id,)).fetchone()
            self.assertEqual(row["status"], "allowed")

    def test_member_cannot_start_ops(self):
        from collector import assistant
        self.users.create_user("bob", "test-password-123")
        self.users.set_role("bob", "member")
        sid = assistant.create_session("b", owner="bob", mode="chat")
        with self.assertRaises(assistant.AssistantPermission):
            assistant.start_ops_turn(sid, "x", "bob")
