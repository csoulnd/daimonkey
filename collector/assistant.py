"""智能助手核心：PIAgent 一次性进程模式的双通道封装。

两个通道共用一个执行引擎，配额相互隔离：
- interactive: /assistant 登录后的对话窗口（serve.py 进程调用）
- internal:    站点内部按需调用（周报总结 / 文案润色 / 优化建议，collector 进程调用）

执行约束（registry.yaml → assistant）：
- 全局并发 = 1：data/assistant.lock 文件锁跨进程互斥（serve.py 与 collector 都会起 pi 子进程）
- 每次任务一个 pi 一次性进程（collector/piagent.py），用完即退，内存归还操作系统
- 每日请求数 / token 数按通道独立限额
- 多轮上下文由本模块从 assistant_messages 表重组转写传入，不依赖外部会话状态

安全边界：AI 产物在 assistant_messages 带 channel/workflow 标记，与确定性数据完全隔离；
pi 子进程 --no-tools 全禁工具（纯文本对话），密钥经环境变量注入。
"""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, registry
from . import db, piagent

LOCK_PATH = DATA_DIR / "assistant.lock"
ENV_FILE = DATA_DIR / "assistant.env"

DEFAULTS: dict = {
    "enabled": True,
    "provider": "deepseek",
    "model": "deepseek/deepseek-flash",
    "api_key_env": "DEEPSEEK_API_KEY",
    "thinking": "low",           # 推理档位:off/minimal/low/medium/high;低档省 token
    "execution": {"max_concurrent": 1, "timeout_s": 300},
    "internal": {"daily_limit": 50, "daily_tokens": 1000000, "queue_depth": 3},
    "interactive": {"daily_limit": 100, "daily_tokens": 2000000,
                    "max_message_chars": 8000, "max_output_tokens": 4000,
                    "history_messages": 16, "history_char_budget": 16000},
    "login": {"max_failures": 5, "lockout_minutes": 10},
}

SITE_SYSTEM_PROMPT = (
    "你是「InferTech 观察站」网站的内嵌助手,用中文与用户对话。"
    "这个站点以推理技术为重心,提供智能体热度榜单、AI 新闻、报告厅与个人知识库。"
    "回答保持简洁、直接、可操作;不确定时如实说明,不要编造站点里不存在的数据;"
    "涉及站点数据更新等问题时,提示用户使用页面右上角的更新按钮。"
)


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


# ---- token 计量：pi 事件流精确返回 usage(input/output)，无需估算 ----


# ---- 会话（对外通道;owner = 登录用户名,历史 NULL 会话对全体管理员可读） ----

def create_session(title: str, owner: str | None = None) -> int:
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO assistant_sessions (title, owner, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)", (title[:80] or "新会话", owner, _now(), _now()))
        return int(cur.lastrowid)


def list_sessions(owner: str | None = None) -> list[dict]:
    """会话下拉数据：只列该用户名下的活跃会话（默认近 7 天有使用，registry 可调）。

    历史会话不删除（可溯源），只是不再出现在切换列表里;
    用户系统上线前的 NULL-owner 旧会话不进任何人的列表。
    """
    active_days = int(cfg().get("sessions", {}).get("active_days", 7))
    cutoff = (datetime.now(timezone.utc).astimezone()
              - timedelta(days=active_days)).isoformat(timespec="seconds")
    with _connect() as c:
        rows = c.execute(
            "SELECT s.id, s.title, s.updated_at,"
            " (SELECT COUNT(*) FROM assistant_messages m WHERE m.session_id = s.id)"
            " AS messages FROM assistant_sessions s"
            " WHERE s.updated_at >= ? AND s.owner IS ?"
            " ORDER BY s.updated_at DESC LIMIT 100",
            (cutoff, owner)).fetchall()
    return [dict(r) for r in rows]


def _session_owned(c, session_id: int, owner: str | None) -> bool:
    """owner 非空时会话须属于该用户(NULL 旧会话放行);owner 为 None 不限制。"""
    if owner is None:
        return True
    row = c.execute("SELECT owner FROM assistant_sessions WHERE id = ?",
                    (session_id,)).fetchone()
    return row is not None and (row["owner"] == owner or row["owner"] is None)


def session_owned(session_id: int, owner: str | None) -> bool:
    """会话是否归属该用户(NULL 旧会话对全体管理员放行);供 serve 层做 chat 前置校验。"""
    with _connect() as c:
        return _session_owned(c, session_id, owner)


def session_messages(session_id: int, owner: str | None = None) -> list[dict]:
    with _connect() as c:
        if not _session_owned(c, session_id, owner):
            return []
        rows = c.execute(
            "SELECT role, content, workflow, created_at FROM assistant_messages"
            " WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: int, owner: str | None = None) -> bool:
    with _connect() as c:
        if not _session_owned(c, session_id, owner):
            return False
        cur = c.execute("DELETE FROM assistant_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def rename_session(session_id: int, title: str,
                   owner: str | None = None) -> bool:
    """会话改名（截断 80 字符，空标题拒绝改为空串）。"""
    title = (title or "").strip()
    if not title:
        return False
    with _connect() as c:
        if not _session_owned(c, session_id, owner):
            return False
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


def _chat_task(message: str, history: list[dict]) -> str:
    """把 SQLite 里的历史消息重组为单次提示词(多轮上下文自管)。"""
    if not history:
        return message
    conf = cfg()["interactive"]
    keep = history[-int(conf.get("history_messages", 16)):]
    lines, budget = [], int(conf.get("history_char_budget", 16000))
    for m in keep:
        role = "用户" if m["role"] == "user" else "助手"
        text = (m["content"] or "")[:2000]
        lines.append(f"{role}: {text}")
    transcript = "\n\n".join(lines)
    if len(transcript) > budget:
        transcript = transcript[-budget:]
    return (f"以下是你与用户此前的对话记录(按时间序,可能截断):\n\n"
            f"{transcript}\n\n请直接回复用户的最新一条消息,不要复述历史。"
            f"\n\n最新消息:\n用户: {message}")


# ---- 执行引擎（两通道共用） ----

def _execute(channel: str, task: str, *, session_id: int | None,
             workflow: str | None, system_prompt: str | None = None) -> dict:
    """配额检查 + 加锁执行 + 落库（消息 / 用量 / 审计）。

    每次任务一个 pi 一次性进程(piagent.run),用完即退、内存即时归还。
    """
    check_quota(channel)
    conf = cfg()
    timeout_s = int(conf["execution"]["timeout_s"])
    started = time.monotonic()
    tokens_in = tokens_out = 0
    peak_rss = 0.0
    error: str | None = None
    reply = ""
    load_env_file()
    try:
        with _execution_lock():
            result = piagent.run(
                task, model=conf["model"], api_key_env=conf["api_key_env"],
                system_prompt=system_prompt, thinking=conf.get("thinking"),
                timeout_s=timeout_s)
            reply, tokens_in, tokens_out = (
                result.reply, result.tokens_in, result.tokens_out)
            peak_rss = result.peak_rss_mb
    except AssistantBusy:
        raise
    except piagent.PiAgentError as e:
        error = str(e)
    with _connect() as c:
        if error is None:
            _append_message(c, session_id, "assistant", reply, channel, workflow)
        _record_usage(c, channel, tokens_in, tokens_out)
        _record_run(c, channel=channel, workflow=workflow, session_id=session_id,
                    status="failed" if error else "success", error=error,
                    duration_s=time.monotonic() - started,
                    peak_rss_mb=peak_rss, tokens_in=tokens_in, tokens_out=tokens_out)
    if error:
        raise AssistantError(error)
    return {"reply": reply, "tokens_in": tokens_in, "tokens_out": tokens_out}


def run_chat(session_id: int, message: str) -> dict:
    """对外助手窗口：一次用户输入 → PIAgent 续聊 → 回复落库。"""
    conf = cfg()["interactive"]
    message = (message or "").strip()
    if not message:
        raise AssistantError("消息为空")
    if len(message) > conf["max_message_chars"]:
        raise AssistantError(f"消息过长（上限 {conf['max_message_chars']} 字符）")
    with _connect() as c:
        history = session_messages(session_id)
        _append_message(c, session_id, "user", message, "interactive", None)
    return _execute("interactive", _chat_task(message, history),
                    session_id=session_id, workflow=None,
                    system_prompt=SITE_SYSTEM_PROMPT)


def run_internal(workflow_name: str, payload: dict | None = None,
                 channel: str = "internal",
                 max_tokens: int | None = None) -> dict:
    """内部通道：执行登记在 collector.workflows 的工作流。

    channel: collector 进程调用走 internal（站点内部额度）；
    从 /assistant 页面手动触发时 serve.py 传 interactive（用户额度）。
    max_tokens: 仅为兼容保留——pi 无 max_tokens 概念,输出预算由
    timeout_s 与每日 token 配额约束(见 piagent 模块说明)。
    """
    spec = get_workflow(workflow_name)
    task = spec.build_task(payload or {})
    result = _execute(channel, task, session_id=None, workflow=workflow_name)
    spec.store(payload or {}, result["reply"])
    return result


def get_workflow(name: str):
    """转发到 workflows 模块（延迟导入，避免循环依赖）。"""
    from . import workflows
    return workflows.get(name)
