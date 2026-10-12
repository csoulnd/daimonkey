"""智能助手核心：DSH agent loop 的双通道封装。

两个通道共用一个执行引擎，配额相互隔离：
- interactive: /assistant 登录后的对话窗口（serve.py 进程调用）
- internal:    站点内部按需调用（周报总结 / 文案润色 / 优化建议，collector 进程调用）

执行约束（registry.yaml → assistant）：
- 全局并发 = 1：data/assistant.lock 文件锁跨进程互斥（serve.py 与 collector 都会起 DSH 子进程）
- 每请求一个 SDK 上下文，用完即退，内存归还操作系统
- RSS 守护线程监控 DSH 子进程，超 memory_rss_mb 直接 SIGKILL
- 每日请求数 / token 数按通道独立限额

安全边界：AI 产物在 assistant_messages 带 channel/workflow 标记，与确定性数据完全隔离；
deepseek_harness SDK 延迟导入，避免拖慢 serve.py 启动。
"""
from __future__ import annotations

import fcntl
import math
import os
import signal
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATA_DIR, registry
from . import db

DSH_HOME = DATA_DIR / "dsh-home"
LOCK_PATH = DATA_DIR / "assistant.lock"
ENV_FILE = DATA_DIR / "assistant.env"

DEFAULTS: dict = {
    "enabled": True,
    "profile": "sdk-minimal",
    "provider": "deepseek-official",
    "model": "deepseek-v4-flash",
    "api_key_env": "DEEPSEEK_API_KEY",
    "execution": {"max_concurrent": 1, "memory_rss_mb": 1200, "timeout_s": 300},
    "internal": {"daily_limit": 50, "daily_tokens": 1000000, "queue_depth": 3},
    "interactive": {"daily_limit": 100, "daily_tokens": 2000000,
                    "max_message_chars": 8000, "max_output_tokens": 4000},
    "login": {"max_failures": 5, "lockout_minutes": 10},
}


class AssistantError(Exception):
    """助手执行失败（超时 / 内存超限 / SDK 或网络错误）。"""


class AssistantBusy(AssistantError):
    """已有一个 DSH 任务在执行（跨进程互斥）。"""


class AssistantQuota(AssistantError):
    """当日配额（请求数或 token 数）已用尽。"""


def cfg() -> dict:
    """registry.yaml → assistant 块，与默认值合并。"""
    merged = dict(DEFAULTS)
    for key, value in (registry().get("assistant") or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merged[key] | value
        else:
            merged[key] = value
    return merged


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def load_env_file() -> None:
    """读入 data/assistant.env（KEY=VALUE，600 权限），不覆盖已有环境变量。"""
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _connect():
    c = db.conn()
    db.ensure_assistant_tables(c)
    return c


# ---- 跨进程执行锁（并发 = 1） ----

@contextmanager
def _execution_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AssistantBusy("已有一个助手任务在执行，请稍后再试") from None
        yield
    finally:
        fh.close()


# ---- RSS 守护：监控 SDK 子进程，超限击杀 ----

def _child_pids() -> list[int]:
    me = os.getpid()
    pids: list[int] = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            if int(parts[1]) == me:
                pids.append(int(pid))
        except (OSError, ValueError, IndexError):
            continue
    return pids


def _rss_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


class _RssGuard(threading.Thread):
    """每 2 秒采样直系子进程 RSS；超限 SIGKILL，记录峰值。"""

    def __init__(self, limit_mb: float):
        super().__init__(daemon=True)
        self.limit_mb = limit_mb
        self.peak_mb = 0.0
        self.killed = False

    def run(self) -> None:
        while not self.killed:
            pids = _child_pids()
            total = sum(_rss_mb(p) for p in pids)
            self.peak_mb = max(self.peak_mb, total)
            if total > self.limit_mb:
                self.killed = True
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                return
            time.sleep(2)


# ---- 用量与配额 ----

def _usage_row(c, channel: str):
    return c.execute(
        "SELECT requests, tokens_in, tokens_out FROM assistant_usage"
        " WHERE date = ? AND channel = ?", (_today(), channel)).fetchone()


def check_quota(channel: str) -> None:
    """超限抛 AssistantQuota；通过则静默返回。"""
    conf = cfg()[channel]
    with _connect() as c:
        row = _usage_row(c, channel)
    if row is None:
        return
    if row["requests"] >= conf["daily_limit"]:
        raise AssistantQuota(f"当日 {channel} 通道请求数已达上限 {conf['daily_limit']}")
    if row["tokens_in"] + row["tokens_out"] >= conf["daily_tokens"]:
        raise AssistantQuota(f"当日 {channel} 通道 token 已达上限 {conf['daily_tokens']}")


def usage_summary() -> dict:
    conf = cfg()
    out = {}
    with _connect() as c:
        for channel in ("interactive", "internal"):
            row = _usage_row(c, channel) or {"requests": 0, "tokens_in": 0, "tokens_out": 0}
            out[channel] = {
                "requests": row["requests"], "limit": conf[channel]["daily_limit"],
                "tokens": row["tokens_in"] + row["tokens_out"],
                "tokens_limit": conf[channel]["daily_tokens"],
            }
    return out


def _record_usage(c, channel: str, tokens_in: int, tokens_out: int) -> None:
    c.execute(
        "INSERT INTO assistant_usage (date, channel, requests, tokens_in, tokens_out)"
        " VALUES (?, ?, 1, ?, ?)"
        " ON CONFLICT(date, channel) DO UPDATE SET"
        " requests = requests + 1, tokens_in = tokens_in + excluded.tokens_in,"
        " tokens_out = tokens_out + excluded.tokens_out",
        (_today(), channel, tokens_in, tokens_out))


def _record_run(c, *, channel: str, workflow: str | None, session_id: int | None,
                status: str, error: str | None, duration_s: float,
                peak_rss_mb: float, tokens_in: int, tokens_out: int) -> None:
    c.execute(
        "INSERT INTO assistant_runs (channel, workflow, session_id, status, error,"
        " duration_s, peak_rss_mb, tokens_in, tokens_out, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (channel, workflow, session_id, status, error, round(duration_s, 2),
         round(peak_rss_mb, 1), tokens_in, tokens_out, _now()))


# ---- token 计量：优先取事件流里的 usage，取不到则按字符估算 ----

def _find_usage(obj) -> dict | None:
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, dict):
            return usage
        for value in obj.values():
            found = _find_usage(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_usage(item)
            if found:
                return found
    return None


def _tokens(result, prompt: str, reply: str) -> tuple[int, int, bool]:
    usage = _find_usage(getattr(result, "events", None) or [])
    if usage:
        tin = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        tout = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        if tin or tout:
            return tin, tout, False
    return math.ceil(len(prompt) / 2), math.ceil(len(reply) / 2), True


# ---- 会话（对外通道） ----

def create_session(title: str) -> int:
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO assistant_sessions (title, created_at, updated_at)"
            " VALUES (?, ?, ?)", (title[:80] or "新会话", _now(), _now()))
        return int(cur.lastrowid)


def list_sessions() -> list[dict]:
    with _connect() as c:
        rows = c.execute(
            "SELECT s.id, s.title, s.dsh_session, s.updated_at,"
            " (SELECT COUNT(*) FROM assistant_messages m WHERE m.session_id = s.id)"
            " AS messages FROM assistant_sessions s"
            " ORDER BY s.updated_at DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def session_messages(session_id: int) -> list[dict]:
    with _connect() as c:
        rows = c.execute(
            "SELECT role, content, workflow, created_at FROM assistant_messages"
            " WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: int) -> bool:
    with _connect() as c:
        cur = c.execute("DELETE FROM assistant_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def rename_session(session_id: int, title: str) -> bool:
    """会话改名（截断 80 字符，空标题拒绝改为空串）。"""
    title = (title or "").strip()
    if not title:
        return False
    with _connect() as c:
        cur = c.execute(
            "UPDATE assistant_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title[:80], _now(), session_id))
        return cur.rowcount > 0


def _append_message(c, session_id: int | None, role: str, content: str,
                    channel: str, workflow: str | None = None) -> None:
    c.execute(
        "INSERT INTO assistant_messages (session_id, role, content, channel,"
        " workflow, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, role, content, channel, workflow, _now()))
    if session_id is not None:
        c.execute("UPDATE assistant_sessions SET updated_at = ? WHERE id = ?",
                  (_now(), session_id))


def _dsh_session_of(c, session_id: int) -> str | None:
    row = c.execute("SELECT dsh_session FROM assistant_sessions WHERE id = ?",
                    (session_id,)).fetchone()
    return row["dsh_session"] if row else None


# ---- 执行引擎（两通道共用） ----

def _run_sdk(task: str, *, dsh_session: str | None, max_tokens: int,
             timeout_s: int) -> tuple[str, str | None, int, int, float]:
    """起一个 SDK 上下文执行任务。

    返回 (final_response, dsh_session_id, tokens_in, tokens_out, peak_rss_mb)；
    异常统一包装为 AssistantError，超时/内存击杀会注明原因。
    """
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig  # 延迟导入

    conf = cfg()
    load_env_file()
    api_key = os.environ.get(conf["api_key_env"]) or None
    if not api_key:
        raise AssistantError(f"缺少 {conf['api_key_env']}（检查 data/assistant.env）")
    DSH_HOME.mkdir(parents=True, exist_ok=True)

    config = DeepSeekHarnessConfig(
        provider=conf["provider"], model=conf["model"], api_key=api_key,
        profile=conf["profile"], dsh_home=str(DSH_HOME),
        max_tokens=max_tokens,
        request_timeout_seconds=timeout_s,
        initialize_timeout_seconds=30,
        env={"DSH_TELEMETRY_MODE": "DISABLED"},
    )
    guard = _RssGuard(float(conf["execution"]["memory_rss_mb"]))
    guard.start()
    started = time.monotonic()
    try:
        with DeepSeekHarness(config) as harness:
            result = harness.run(task, session_id=dsh_session)
        reply = (result.final_response or "").strip()
        if not reply:
            raise AssistantError(f"DSH 未返回内容（finish_reason={result.finish_reason}）")
        if guard.killed:
            raise AssistantError(f"内存超限被终止（上限 {guard.limit_mb:.0f}MB）")
        if time.monotonic() - started > timeout_s:
            raise AssistantError("执行超时")
        tokens_in, tokens_out, _ = _tokens(result, task, reply)
        return reply, result.session_id, tokens_in, tokens_out, guard.peak_mb
    except AssistantError:
        raise
    except Exception as e:  # SDK / 网络错误统一归类
        if guard.killed:
            raise AssistantError(f"内存超限被终止（上限 {guard.limit_mb:.0f}MB）") from e
        if time.monotonic() - started > timeout_s:
            raise AssistantError("执行超时") from e
        raise AssistantError(f"{type(e).__name__}: {e}") from e


def _execute(channel: str, task: str, *, session_id: int | None,
             dsh_session: str | None, workflow: str | None,
             max_tokens: int) -> dict:
    """配额检查 + 加锁执行 + 落库（消息 / 用量 / 审计）。"""
    check_quota(channel)
    conf = cfg()
    timeout_s = int(conf["execution"]["timeout_s"])
    started = time.monotonic()
    tokens_in = tokens_out = 0
    peak_rss = 0.0
    error: str | None = None
    reply = ""
    new_dsh: str | None = None
    try:
        with _execution_lock():
            reply, new_dsh, tokens_in, tokens_out, peak_rss = _run_sdk(
                task, dsh_session=dsh_session, max_tokens=max_tokens,
                timeout_s=timeout_s)
    except AssistantBusy:
        raise
    except AssistantError as e:
        error = str(e)
    with _connect() as c:
        if error is None:
            _append_message(c, session_id, "assistant", reply, channel, workflow)
            if new_dsh and session_id is not None:
                c.execute("UPDATE assistant_sessions SET dsh_session = ? WHERE id = ?",
                          (new_dsh, session_id))
        _record_usage(c, channel, tokens_in, tokens_out)
        _record_run(c, channel=channel, workflow=workflow, session_id=session_id,
                    status="failed" if error else "success", error=error,
                    duration_s=time.monotonic() - started,
                    peak_rss_mb=peak_rss, tokens_in=tokens_in, tokens_out=tokens_out)
    if error:
        raise AssistantError(error)
    return {"reply": reply, "tokens_in": tokens_in, "tokens_out": tokens_out}


def run_chat(session_id: int, message: str) -> dict:
    """对外助手窗口：一次用户输入 → DSH 续聊 → 回复落库。"""
    conf = cfg()["interactive"]
    message = (message or "").strip()
    if not message:
        raise AssistantError("消息为空")
    if len(message) > conf["max_message_chars"]:
        raise AssistantError(f"消息过长（上限 {conf['max_message_chars']} 字符）")
    with _connect() as c:
        dsh = _dsh_session_of(c, session_id)
        _append_message(c, session_id, "user", message, "interactive", None)
    return _execute("interactive", message, session_id=session_id,
                    dsh_session=dsh, workflow=None,
                    max_tokens=int(conf["max_output_tokens"]))


def run_internal(workflow_name: str, payload: dict | None = None,
                 channel: str = "internal") -> dict:
    """内部通道：执行登记在 collector.workflows 的工作流。

    channel: collector 进程调用走 internal（站点内部额度）；
    从 /assistant 页面手动触发时 serve.py 传 interactive（用户额度）。
    """
    from . import workflows
    spec = get_workflow(workflow_name)
    task = spec.build_task(payload or {})
    result = _execute(channel, task, session_id=None, dsh_session=None,
                      workflow=workflow_name, max_tokens=int(spec.max_tokens))
    spec.store(payload or {}, result["reply"])
    return result


def get_workflow(name: str):
    """转发到 workflows 模块（延迟导入，避免循环依赖）。"""
    from . import workflows
    return workflows.get(name)
