"""PIAgent 适配层:参数组装、NDJSON 事件流解析、错误路径。子进程全部 mock。"""
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import piagent  # noqa: E402


def _assistant_end(text: str, *, stop: str = "stop", tin: int = 10,
                   tout: int = 5) -> str:
    message = {
        "role": "assistant",
        "content": [{"type": "thinking", "thinking": "..."},
                    {"type": "text", "text": text}],
        "usage": {"input": tin, "output": tout, "totalTokens": tin + tout},
        "stopReason": stop,
    }
    return json.dumps({"type": "message_end", "message": message},
                      ensure_ascii=False)


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout_text, self.stderr_text = stdout, stderr
        self.returncode = returncode
        self.pid = 99999999  # 不存在的 pid,RSS 采样线程立即退出

    def communicate(self, timeout=None):
        return self.stdout_text, self.stderr_text

    def kill(self):
        pass


class ParseEventsTests(unittest.TestCase):
    def test_takes_last_assistant_text(self):
        stdout = "\n".join([
            json.dumps({"type": "session", "id": "x"}),
            json.dumps({"type": "agent_start"}),
            _assistant_end("第一条回复"),
            _assistant_end("第二条回复", stop="length", tin=100, tout=50),
            "not-json-line",
        ])
        parsed = piagent.parse_events(stdout)
        self.assertEqual(parsed["reply"], "第二条回复")
        self.assertEqual(parsed["finish_reason"], "length")
        self.assertEqual(parsed["tokens_in"], 100)
        self.assertEqual(parsed["tokens_out"], 50)

    def test_joins_multiple_text_parts(self):
        message = {"role": "assistant",
                   "content": [{"type": "text", "text": "段落一"},
                               {"type": "text", "text": "段落二"}],
                   "usage": {"input": 1, "output": 2}, "stopReason": "stop"}
        parsed = piagent.parse_events(
            json.dumps({"type": "message_end", "message": message}))
        self.assertEqual(parsed["reply"], "段落一\n段落二")

    def test_no_assistant_reply_raises(self):
        with self.assertRaises(piagent.PiAgentError):
            piagent.parse_events(json.dumps({"type": "agent_start"}))

    def test_empty_reply_raises(self):
        message = {"role": "assistant", "content": [], "usage": {},
                   "stopReason": "stop"}
        with self.assertRaises(piagent.PiAgentError):
            piagent.parse_events(
                json.dumps({"type": "message_end", "message": message}))


class BuildArgsTests(unittest.TestCase):
    def test_safety_flags_present(self):
        args = piagent.build_args("任务", model="deepseek/deepseek-flash",
                                  system_prompt="系统", thinking="low",
                                  pi_override="/usr/bin/pi")
        self.assertEqual(args[0], "/usr/bin/pi")
        joined = " ".join(args)
        for flag in ("--no-tools", "--no-session", "--no-extensions",
                     "--no-mcp", "--no-skills", "--no-context-files",
                     "--no-prompt-templates", "--mode json", "-p"):
            self.assertIn(flag, joined)
        self.assertIn("deepseek/deepseek-flash", args)
        self.assertIn("系统", args)


class RunTests(unittest.TestCase):
    def test_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(piagent.PiAgentError) as ctx:
                piagent.run("任务", model="deepseek/deepseek-flash",
                            api_key_env="NO_SUCH_KEY")
            self.assertIn("NO_SUCH_KEY", str(ctx.exception))

    def test_success_returns_pi_result(self):
        stdout = _assistant_end("正常回复", tin=460, tout=65)
        fake = _FakeProc(stdout=stdout)
        with patch.dict("os.environ", {"SPIKE_KEY": "sk-test"}):
            with patch.object(piagent.subprocess, "Popen", return_value=fake):
                result = piagent.run("任务", model="deepseek/deepseek-flash",
                                     api_key_env="SPIKE_KEY")
        self.assertEqual(result.reply, "正常回复")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.tokens_in, 460)
        self.assertEqual(result.tokens_out, 65)

    def test_nonzero_exit_raises_with_stderr_hint(self):
        fake = _FakeProc(stdout="", stderr="warn1\nboom", returncode=1)
        with patch.dict("os.environ", {"SPIKE_KEY": "sk-test"}):
            with patch.object(piagent.subprocess, "Popen", return_value=fake):
                with self.assertRaises(piagent.PiAgentError) as ctx:
                    piagent.run("任务", model="deepseek/deepseek-flash",
                                api_key_env="SPIKE_KEY")
        self.assertIn("boom", str(ctx.exception))


class ChatTaskTests(unittest.TestCase):
    """assistant._chat_task:多轮历史重组为单次提示词。"""

    def test_history_composition_and_budget(self):
        import collector.assistant as assistant
        importlib.reload(assistant)
        history = [{"role": "user", "content": "第一问"},
                   {"role": "assistant", "content": "第一答"}]
        task = assistant._chat_task("第二问", history)
        self.assertIn("第一问", task)
        self.assertIn("第一答", task)
        self.assertIn("第二问", task)
        self.assertIn("对话记录", task)
        # 空历史:直接透传消息
        self.assertEqual(assistant._chat_task("只有一条", []), "只有一条")
        # 预算截断:超长历史保留尾部
        long_history = [{"role": "user", "content": "x" * 2000}
                        for _ in range(30)]
        task = assistant._chat_task("最新", long_history)
        self.assertLessEqual(len(task), 17000)
        self.assertTrue(task.endswith("用户: 最新"))


if __name__ == "__main__":
    unittest.main()
