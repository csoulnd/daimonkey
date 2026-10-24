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
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR, ROOT, registry
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
    # 运维 agent(2026-10-20):启用工具 + 持久 pi 会话 + 前端审批。
    # 能力越大越要收口:仅 admin 账号、仅会话 mode=ops、每轮一个 RPC 子进程。
    "ops": {"enabled": True,
            "model": "deepseek/deepseek-v4-pro",
            "thinking": "high",
            "tools": "read,write,edit,bash,find,grep,ls",
            "timeout_s": 900,
            "approval_timeout_s": 180,
            "max_tool_calls": 60,
            "session_dir": "data/pi-sessions",
            "audit_dir": "data/ops-audit",
            "extension": "deploy/pi-ext/ops-guard.ts",
            "daily_limit": 60,
            "daily_tokens": 4000000},
    "login": {"max_failures": 5, "lockout_minutes": 10},
}

SITE_SYSTEM_PROMPT = (
    "你是「InferTech 观察站」网站的内嵌助手,用中文与用户对话。"
    "这个站点以推理技术为重心的技术观察站。"
    "回答保持简洁、直接、可操作;不确定时如实说明,不要编造站点里不存在的数据;"
    "涉及站点数据更新等问题时,提示用户到首页右上角使用「全量更新」或「仅更新新闻」按钮。"
)

OPS_SYSTEM_PROMPT = (
    "你是「InferTech 观察站」的站内运维 agent,通过网页对话完成网站运维。"
    "工作目录固定为站点安装目录(仓库根),服务由 systemd 单元 daimonkey 托管,"
    "当前进程以非 root 专用用户运行——没有 root 权限,不要尝试提权或越权访问仓库外文件。"
    "可用工具:read/write/edit/bash/find/grep/ls。执行原则:"
    "1) 先看后改:git status、读代码、看日志/状态,再动手;小步修改、每步验证。"
    "2) 改 templates/ 或 static/ 后必须运行 `.venv/bin/python -m collector --build`;"
    "改 Python 后先跑相关 `.venv/bin/python -m unittest tests.xxx -v`;"
    "交付前跑 `.venv/bin/python scripts/verify.py`。"
    "3) 重启站点只能通过 `.venv/bin/python scripts/opsctl.py restart --in 3`(延时重启,会短暂断线),"
    "不要直接 systemctl restart,以免中断当前会话。"
    "4) 禁止读取 data/ 下的密钥(assistant.env/assistant_secret/update_password),禁止修改 git 历史或强推。"
    "5) 危险或不可逆操作(删除文件、重启、改部署/数据目录、提交/推送)会触发用户审批;被拒绝就停下来说明。"
    "6) 不擅自处理用户未提交的改动:先 git status,保留别人的修改;除非用户明确要求,不要 commit/push。"
    "7) 回答用中文,简洁报告:做了什么、命令与关键输出、结果、下一步;长输出用摘要,不要贴整屏日志。"
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
        for channel in ("interactive", "internal", "ops"):
            row = _usage_row(c, channel) or {"requests": 0, "tokens_in": 0, "tokens_out": 0}
            limit = (conf.get(channel) or {}).get("daily_limit", 0)
            token_limit = (conf.get(channel) or {}).get("daily_tokens", 0)
            out[channel] = {
                "requests": row["requests"], "limit": limit,
                "tokens": row["tokens_in"] + row["tokens_out"],
                "tokens_limit": token_limit,
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
                peak_rss_mb: float, tokens_in: int, tokens_out: int,
                run_id: str | None = None, mode: str | None = None,
                reply: str | None = None, tool_count: int = 0) -> None:
    c.execute(
        "INSERT INTO assistant_runs (channel, workflow, session_id, status, error,"
        " duration_s, peak_rss_mb, tokens_in, tokens_out, created_at,"
        " run_id, mode, reply, tool_count)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (channel, workflow, session_id, status, error, round(duration_s, 2),
         round(peak_rss_mb, 1), tokens_in, tokens_out, _now(),
         run_id, mode, reply, tool_count))


# ---- token 计量：pi 事件流精确返回 usage(input/output)，无需估算 ----


# ---- 会话（对外通道;owner = 登录用户名,历史 NULL 会话对全体管理员可读） ----

def create_session(title: str, owner: str | None = None,
                   mode: str = "chat") -> int:
    if mode not in ("chat", "ops"):
        mode = "chat"
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO assistant_sessions (title, mode, owner, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (title[:80] or "新会话", mode, owner, _now(), _now()))
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
            "SELECT s.id, s.title, s.mode, s.updated_at,"
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
    pi_session = None
    with _connect() as c:
        if not _session_owned(c, session_id, owner):
            return False
        row = c.execute("SELECT pi_session FROM assistant_sessions WHERE id = ?",
                        (session_id,)).fetchone()
        pi_session = row["pi_session"] if row else None
        cur = c.execute("DELETE FROM assistant_sessions WHERE id = ?", (session_id,))
        deleted = cur.rowcount > 0
    if deleted and pi_session:
        # pi 会话文件(<时间戳>_<id>.jsonl):删会话即删磁盘上下文,避免残留可被续接
        for path in _ops_session_dir().rglob(f"*_{pi_session}.jsonl"):
            try:
                path.unlink()
            except OSError:
                pass
    return deleted


def get_session_mode(session_id: int) -> str | None:
    with _connect() as c:
        row = c.execute("SELECT mode FROM assistant_sessions WHERE id = ?",
                        (session_id,)).fetchone()
    return (row["mode"] or "chat") if row else None


def set_session_mode(session_id: int, mode: str,
                     owner: str | None = None) -> bool:
    """切换会话模式:chat(纯对话) / ops(可调用工具)。权限由 serve 层校验。"""
    if mode not in ("chat", "ops"):
        return False
    with _connect() as c:
        if not _session_owned(c, session_id, owner):
            return False
        cur = c.execute(
            "UPDATE assistant_sessions SET mode = ?, updated_at = ? WHERE id = ?",
            (mode, _now(), session_id))
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


# ============================================================================
# 运维 agent(ops):工具 + 持久 pi 会话 + 流式事件 + 前端审批
#
# 与对话通道的区别:
# - 每轮仍是一个 pi 一次性子进程,但走 RPC 模式,用人可读的 pi 会话文件续接;
# - 工具由 pi 执行(read/write/edit/bash/find/grep/ls),危险动作由
#   deploy/pi-ext/ops-guard.ts 拦截并请求前端审批;
# - 事件只放内存给前端轮询(不落库),工具调用/审批结果落库审计。
# 并发仍为全局 1:执行锁与对话/工作流共用 data/assistant.lock。
# ============================================================================

class AssistantPermission(AssistantError):
    """当前账号/会话没有该能力(非管理员、未启用、会话模式不符)。"""


_RUN_TTL_S = 600            # 结束后保留多久供前端轮询
_EVENT_MAX = 500            # 单轮事件上限
_TEXT_MAX = 64 * 1024       # 流式文本上限
_TOOL_OUTPUT_MAX = 4000     # 单条工具输出入库/下发上限

_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()
_ops_active: dict = {"run_id": None}


def ops_conf() -> dict:
    return cfg().get("ops") or {}


def ops_enabled() -> bool:
    return bool(ops_conf().get("enabled"))


def _ops_session_dir() -> Path:
    """pi 会话文件目录(默认 data/pi-sessions);相对路径按仓库根解析。"""
    raw = ops_conf().get("session_dir") or "data/pi-sessions"
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def ops_audit_dir() -> Path:
    raw = ops_conf().get("audit_dir") or "data/ops-audit"
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def _brief(value, limit: int = 300) -> str:
    """工具参数/输出摘要:压成单行,超长截断(前端卡片与库内审计共用)。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    value = value.replace("\r", "").strip()
    if len(value) > limit:
        value = value[:limit] + "…"
    return value


def _tool_output(result) -> str:
    """从 pi tool_execution_end 的 result 里抽文本输出。"""
    if not isinstance(result, dict):
        return _brief(result)
    content = result.get("content")
    if isinstance(content, list):
        parts = [str(c.get("text", "")) for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        return _brief("\n".join(p for p in parts if p), _TOOL_OUTPUT_MAX)
    if isinstance(content, str):
        return _brief(content, _TOOL_OUTPUT_MAX)
    return _brief(result, _TOOL_OUTPUT_MAX)


def _emit(run_id: str, type_: str, **fields) -> None:
    """向 run 事件缓冲追加一条事件(供前端增量轮询)。"""
    run = _runs.get(run_id)
    if run is None:
        return
    with _runs_lock:
        run["seq"] += 1
        event = {"seq": run["seq"], "type": type_}
        event.update(fields)
        run["events"].append(event)
        if len(run["events"]) > _EVENT_MAX:
            del run["events"][: len(run["events"]) - _EVENT_MAX]


def _prune_runs() -> None:
    now = time.time()
    with _runs_lock:
        for rid, run in list(_runs.items()):
            finished = run.get("finished_at")
            if finished and now - finished > _RUN_TTL_S:
                _runs.pop(rid, None)


def _on_pi_event(run_id: str, event: dict) -> None:
    """pi RPC 事件 → 前端事件:文本增量 / 工具开始与结束。"""
    kind = event.get("type")
    run = _runs.get(run_id)
    if run is None:
        return
    if kind == "message_update":
        delta = (event.get("assistantMessageEvent") or {}).get("delta")
        if delta:
            with _runs_lock:
                run["text"] = (run["text"] + str(delta))[-_TEXT_MAX:]
    elif kind == "tool_execution_start":
        _emit(run_id, "tool_start", tool=event.get("toolName") or "?",
              args=_brief(event.get("args") if event.get("args") is not None
                          else event.get("input")))
    elif kind == "tool_execution_end":
        _emit(run_id, "tool_end", tool=event.get("toolName") or "?",
              status="error" if event.get("isError") else "ok",
              output=_tool_output(event.get("result")),
              duration_s=event.get("durationMs"))


def _approval(run_id: str, session_id: int, owner: str | None,
              request: dict) -> bool:
    """pi 扩展的 ctx.ui.confirm → 前端审批;超时/无人应答一律拒绝(fail-closed)。"""
    run = _runs.get(run_id)
    if run is None:
        return False
    conf = ops_conf()
    timeout = int(conf.get("approval_timeout_s", 180))
    approval_id = uuid.uuid4().hex[:16]
    title = str(request.get("title") or "需要你确认")
    command = _brief(request.get("message") or request.get("command") or "", 800)
    tool = str(request.get("tool") or "bash")
    status = "timeout"
    allowed = False
    with _connect() as c:
        c.execute(
            "INSERT INTO assistant_approvals (id, run_id, session_id, owner, tool,"
            " command, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (approval_id, run_id, session_id, owner, tool, command, _now()))
    with _runs_lock:
        run["pending"] = {"id": approval_id, "tool": tool, "title": title,
                          "command": command}
        waiter = threading.Event()
        run["approvals"][approval_id] = {"event": waiter, "allow": None}
    _emit(run_id, "approval", id=approval_id, tool=tool, title=title,
          command=command, status="pending")
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if run["cancel"].is_set():
                break
            if waiter.wait(0.5):
                allowed = run["approvals"][approval_id]["allow"] is True
                status = "allowed" if allowed else "denied"
                break
    finally:
        with _runs_lock:
            run["approvals"].pop(approval_id, None)
            run["pending"] = None
        with _connect() as c:
            c.execute(
                "UPDATE assistant_approvals SET status = ?, decided_at = ? WHERE id = ?",
                (status, _now(), approval_id))
        _emit(run_id, "approval_resolved", id=approval_id, allowed=allowed,
              status=status)
    return allowed


def resolve_approval(run_id: str, approval_id: str, allow: bool,
                     owner: str | None = None) -> bool:
    """前端点击允许/拒绝:唤醒等待中的 pi 轮次。"""
    run = _runs.get(run_id)
    if run is None:
        return False
    if owner is not None and run.get("owner") != owner:
        return False
    with _runs_lock:
        pending = run.get("pending") or {}
        if pending.get("id") != approval_id:
            return False
        entry = run["approvals"].get(approval_id)
        if entry is None:
            return False
        entry["allow"] = bool(allow)
    entry["event"].set()
    return True


def _ops_task(message: str) -> str:
    """运维轮提示词:正文交给 pi 会话自己续接,不再拼历史(会话文件即上下文)。"""
    return message


def _ops_worker(run_id: str, session_id: int, message: str, user: str,
                pi_session: str) -> None:
    """后台执行一轮运维任务:加锁 → pi RPC → 落库 → 收尾。"""
    run = _runs[run_id]
    conf = ops_conf()
    load_env_file()          # ops 与 chat 一样从 data/assistant.env 取 Key
    started = time.monotonic()
    reply = ""
    error = None
    tokens_in = tokens_out = 0
    peak_rss = 0.0
    aborted = False
    tool_calls: list[dict] = []
    try:
        with _execution_lock():
            result = piagent.run_turn(
                _ops_task(message),
                model=conf["model"], api_key_env=cfg()["api_key_env"],
                system_prompt=OPS_SYSTEM_PROMPT,
                thinking=conf.get("thinking"),
                tools=conf.get("tools") or "read,write,edit,bash,find,grep,ls",
                session_id=pi_session,
                session_dir=str(_ops_session_dir()),
                cwd=str(ROOT),
                timeout_s=int(conf.get("timeout_s", 900)),
                approval_timeout_s=int(conf.get("approval_timeout_s", 180)),
                cancel_event=run["cancel"],
                on_event=lambda event: _on_pi_event(run_id, event),
                on_approval=lambda request: _approval(run_id, session_id, user, request),
                extension_paths=([str(ROOT / conf["extension"])]
                                 if conf.get("extension") else None),
                max_tool_calls=int(conf.get("max_tool_calls", 60)),
            )
            reply = result.reply
            tokens_in, tokens_out = result.tokens_in, result.tokens_out
            peak_rss = result.peak_rss_mb
            aborted = bool(getattr(result, "aborted", False))
            tool_calls = result.tool_calls or []
    except AssistantBusy:
        error = "已有一个任务在执行，请稍后再试"
    except piagent.PiAgentError as e:
        error = str(e)
    except Exception as e:  # 线程兜底:任何异常都落失败,避免 run 永久卡 running
        error = f"{type(e).__name__}: {e}"
    finally:
        with _runs_lock:
            if _ops_active.get("run_id") == run_id:
                _ops_active["run_id"] = None
        _ops_active["run_id"] = None
    duration = time.monotonic() - started
    try:
        with _connect() as c:
            if error is None and reply:
                _append_message(c, session_id, "assistant", reply, "ops", None)
            _record_usage(c, "ops", tokens_in, tokens_out)
            for call in tool_calls:
                c.execute(
                    "INSERT INTO assistant_tool_calls (run_id, session_id, tool,"
                    " args_json, decision, status, output, duration_s, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, session_id, str(call.get("name") or ""),
                     _brief(call.get("args"), 2000), "allow",
                     str(call.get("status") or "ok"),
                     _brief(call.get("output"), _TOOL_OUTPUT_MAX),
                     call.get("duration_s"), _now()))
            _record_run(c, channel="ops", workflow=None, session_id=session_id,
                        status="failed" if error else "success", error=error,
                        duration_s=duration, peak_rss_mb=peak_rss,
                        tokens_in=tokens_in, tokens_out=tokens_out,
                        run_id=run_id, mode="ops", reply=reply,
                        tool_count=len(tool_calls))
    except Exception as e:
        error = error or f"结果落库失败: {type(e).__name__}: {e}"
    with _runs_lock:
        run["state"] = "failed" if error else ("aborted" if aborted else "success")
        run["aborted"] = aborted
        run["error"] = error
        run["tokens_in"] = tokens_in
        run["tokens_out"] = tokens_out
        run["tool_calls"] = tool_calls
        if reply:
            run["text"] = reply
        run["finished_at"] = time.time()
    _emit(run_id, "done", reply=reply, tokens_in=tokens_in, tokens_out=tokens_out,
          error=error, aborted=aborted)


def start_ops_turn(session_id: int, message: str, user: str) -> str:
    """启动一轮运维任务,立即返回 run_id;执行在后台线程(全局并发 1)。"""
    from . import users as users_mod
    if not ops_enabled():
        raise AssistantPermission("运维模式未启用")
    if not users_mod.is_admin(user):
        raise AssistantPermission("仅管理员可使用运维模式")
    conf = cfg()
    message = (message or "").strip()
    if not message:
        raise AssistantError("消息为空")
    limit = int(conf["interactive"].get("max_message_chars", 8000))
    if len(message) > limit:
        raise AssistantError(f"消息过长（上限 {limit} 字符）")
    check_quota("ops")
    _prune_runs()
    run_id = uuid.uuid4().hex
    with _runs_lock:
        if _ops_active.get("run_id"):
            raise AssistantBusy("已有一个运维任务在执行，请稍后再试")
        _ops_active["run_id"] = run_id
    try:
        with _connect() as c:
            row = c.execute(
                "SELECT mode, pi_session FROM assistant_sessions WHERE id = ?",
                (session_id,)).fetchone()
            if row is None or not _session_owned(c, session_id, user):
                raise AssistantError("会话不存在")
            if (row["mode"] or "chat") != "ops":
                raise AssistantPermission("该会话不是运维模式")
            pi_session = row["pi_session"]
            if not pi_session:
                pi_session = "dm" + secrets.token_hex(8)
                c.execute("UPDATE assistant_sessions SET pi_session = ? WHERE id = ?",
                          (pi_session, session_id))
            _append_message(c, session_id, "user", message, "ops", None)
    except Exception:
        with _runs_lock:
            if _ops_active.get("run_id") == run_id:
                _ops_active["run_id"] = None
        raise
    with _runs_lock:
        _runs[run_id] = {
            "run_id": run_id, "session_id": session_id, "owner": user,
            "state": "running", "text": "", "events": [], "seq": 0,
            "error": None, "pending": None, "approvals": {},
            "cancel": threading.Event(), "tokens_in": 0, "tokens_out": 0,
            "tool_calls": [], "started_at": time.time(), "finished_at": None,
        }
    threading.Thread(target=_ops_worker,
                     args=(run_id, session_id, message, user, pi_session),
                     daemon=True).start()
    return run_id


def run_status(run_id: str, owner: str | None = None) -> dict | None:
    """前端轮询:返回 run 快照(不含内部锁/审批 Event)。"""
    run = _runs.get(run_id)
    if run is None:
        return None
    if owner is not None and run.get("owner") != owner:
        return None
    with _runs_lock:
        return {
            "run_id": run_id,
            "session_id": run["session_id"],
            "state": run["state"],
            "text": run["text"],
            "error": run["error"],
            "aborted": bool(run.get("aborted")),
            "events": list(run["events"]),
            "pending": dict(run["pending"]) if run.get("pending") else None,
            "tokens_in": run["tokens_in"],
            "tokens_out": run["tokens_out"],
            "tool_count": len(run.get("tool_calls") or []),
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        }


def abort_run(run_id: str, owner: str | None = None) -> bool:
    """中止当前轮:置取消位(piagent 杀进程组),并拒绝挂起的审批。"""
    run = _runs.get(run_id)
    if run is None or (owner is not None and run.get("owner") != owner):
        return False
    run["cancel"].set()
    with _runs_lock:
        pending = run.get("pending") or {}
        entry = run["approvals"].get(pending.get("id") or "")
        if entry is not None:
            entry["allow"] = False
            entry["event"].set()
    return True


def ops_usage() -> dict:
    return usage_summary().get("ops", {})
