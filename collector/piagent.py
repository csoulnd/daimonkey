"""PIAgent 适配层:pi 一次性进程模式(pi.dev,包 @earendil-works/pi-coding-agent)。

负载优化专家决策(2026-10-16):站点 agent 一律按需起 pi 子进程、用完即退,
无常驻 runtime。与旧 DSH SDK 相比:空闲零内存(实测任务期间峰值 ~130MB,
结束即释放)、token 用量由事件流精确返回、无守护线程。

安全边界:--no-tools 全禁工具——站点助手只做纯文本对话与工作流,
不接触文件系统与 shell;--no-context-files/--no-skills 等关闭仓库上下文
发现,提示词完全由调用方控制。多轮上下文不从 pi 会话恢复,由调用方
(collector.assistant.run_chat)从 SQLite 消息表重组转写传入。

pi 无 max_tokens CLI 参数:输出预算由 timeout_s 与每日配额双约束,
超时 SIGKILL 终止。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


_REPLY_MAX = 64 * 1024          # 单轮回复保留上限(前端也会二次截断)


class PiAgentError(Exception):
    """pi 子进程执行失败(缺 Key / 超时 / 输出解析失败)。"""


@dataclass
class PiResult:
    reply: str
    finish_reason: str          # stop / length / ...
    tokens_in: int
    tokens_out: int
    peak_rss_mb: float


@dataclass
class PiTurnResult:
    """运维轮(pi RPC + 工具)的结果。字段与 OPS-AGENT.md §4.1 契约一致。"""
    reply: str
    finish_reason: str
    tokens_in: int
    tokens_out: int
    peak_rss_mb: float
    duration_s: float
    session_id: str | None
    tool_calls: list[dict]
    aborted: bool = False


def pi_bin(override: str | None = None) -> str:
    """定位 pi 可执行文件。"""
    if override:
        return override
    found = shutil.which("pi") or "/usr/bin/pi"
    return found


def build_args(task: str, *, model: str, system_prompt: str | None,
               thinking: str | None, image_paths: list[str] | None = None,
               pi_override: str | None = None) -> list[str]:
    """组装 pi 命令行。一次性 + 禁工具 + 禁一切仓库上下文发现。

    image_paths: 附加本地图片(多模态读图,如榜单截图),走 pi 的 @file 语法。
    """
    args = [pi_bin(pi_override), "-p", task, "--mode", "json",
            "--model", model,
            "--no-tools", "--no-session", "--no-extensions", "--no-mcp",
            "--no-skills", "--no-context-files", "--no-prompt-templates"]
    if system_prompt:
        args += ["--system-prompt", system_prompt]
    if thinking:
        args += ["--thinking", thinking]
    if image_paths:
        args += ["--"] + [f"@{p}" for p in image_paths]
    return args


def parse_events(stdout: str) -> dict:
    """解析 pi 的 NDJSON 事件流,取最后一条 assistant message_end。

    返回 {reply, finish_reason, tokens_in, tokens_out}。
    """
    last: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # 非事件行(版本横幅等)跳过
        if event.get("type") == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                last = message
    if last is None:
        raise PiAgentError("pi 事件流中没有 assistant 回复")
    parts = [p.get("text", "") for p in last.get("content", [])
             if isinstance(p, dict) and p.get("type") == "text"]
    reply = "\n".join(t for t in parts if t).strip()
    if not reply:
        raise PiAgentError(
            f"pi 未返回内容(finish_reason={last.get('stopReason')})")
    usage = last.get("usage") or {}
    return {
        "reply": reply,
        "finish_reason": str(last.get("stopReason") or "stop"),
        "tokens_in": int(usage.get("input") or 0),
        "tokens_out": int(usage.get("output") or 0),
    }


def _sample_rss(pid: int, stop: threading.Event, peak: list[float]) -> None:
    """采样子进程 RSS(仅该 pid,200ms 周期);stop 置位即退出,绝不泄漏。"""
    while not stop.is_set():
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS"):
                        peak[0] = max(peak[0], int(line.split()[1]) / 1024)
                        break
        except OSError:
            return  # 进程已退出
        stop.wait(0.2)


def run(task: str, *, model: str, api_key_env: str = "DEEPSEEK_API_KEY",
        system_prompt: str | None = None, thinking: str | None = None,
        image_paths: list[str] | None = None,
        timeout_s: int = 300, cwd: str | None = None,
        pi_override: str | None = None) -> PiResult:
    """执行一次 PIAgent 任务。

    task 应为完整提示词(含多轮转写);api key 从环境变量读取,
    由调用方保证已 load_env_file。image_paths 附加本地图片(多模态)。
    """
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise PiAgentError(f"缺少 {api_key_env}(检查 data/assistant.env)")
    args = build_args(task, model=model, system_prompt=system_prompt,
                      thinking=thinking, image_paths=image_paths,
                      pi_override=pi_override)
    env = dict(os.environ)
    env[api_key_env] = api_key
    peak: list[float] = [0.0]
    stop = threading.Event()
    started = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env,
                            cwd=cwd, start_new_session=True)
    sampler = threading.Thread(target=_sample_rss, args=(proc.pid, stop, peak),
                               daemon=True)
    sampler.start()
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # 超时:杀整个进程组(pi 可能派生子进程)。
            # 注意用 os.killpg,不要用 subprocess.run(["kill", "-9", "-pid"]):
            # 本机实测后者会在某些进程树下卡住(2026-10-20,ops-run_turn 单测踩到)。
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.kill()
            proc.communicate()
            raise PiAgentError(f"执行超时(>{timeout_s}s,已终止)") from None
    finally:
        stop.set()
        sampler.join(timeout=2)
    if proc.returncode != 0:
        detail = (stderr or "").strip().splitlines()
        hint = detail[-1] if detail else f"exit={proc.returncode}"
        raise PiAgentError(f"pi 退出码 {proc.returncode}: {hint[:300]}")
    try:
        parsed = parse_events(stdout)
    except PiAgentError:
        raise
    except Exception as e:
        raise PiAgentError(f"pi 输出解析失败: {type(e).__name__}: {e}") from e
    if not parsed["reply"]:
        raise PiAgentError(
            f"pi 未返回内容(finish_reason={parsed['finish_reason']},"
            f"耗时 {time.monotonic() - started:.0f}s)")
    return PiResult(reply=parsed["reply"],
                    finish_reason=parsed["finish_reason"],
                    tokens_in=parsed["tokens_in"],
                    tokens_out=parsed["tokens_out"],
                    peak_rss_mb=peak[0])


# ---------------------------------------------------------------- 运维轮(RPC)

def build_turn_args(*, model: str, system_prompt: str | None = None,
                    thinking: str | None = None, tools: str | None = None,
                    session_id: str | None = None,
                    session_dir: str | None = None,
                    extension_paths: list[str] | None = None,
                    pi_override: str | None = None) -> list[str]:
    """组装运维轮的 pi RPC 命令行。

    与一次性 ``run()`` 的边界不同:这里**显式**开工具白名单、加载策略扩展、
    用 pi 会话文件续接上下文;仍关闭 MCP/skills/context-files 等仓库上下文发现,
    避免模型绕过调用方给定的系统提示词。
    """
    args = [pi_bin(pi_override), "--mode", "rpc", "--model", model,
            "--no-mcp", "--no-skills", "--no-context-files",
            "--no-prompt-templates", "--no-extensions"]
    if tools:
        args += ["--tools", tools]
    else:
        args += ["--no-tools"]        # 空 allowlist = 全禁，避免误开默认工具
    if thinking:
        args += ["--thinking", thinking]
    if system_prompt:
        args += ["--system-prompt", system_prompt]
    for path in extension_paths or []:
        args += ["-e", path]          # 显式 -e 在 --no-extensions 下仍会加载
    if session_id:
        if session_dir:
            args += ["--session-dir", session_dir]
        args += ["--session-id", session_id]
    else:
        args += ["--no-session"]
    return args


def _tool_text(result) -> str:
    """从 tool_execution_end 的 result 里抽可读文本。"""
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = [str(c.get("text", "")) for c in content
                     if isinstance(c, dict) and c.get("type") == "text"]
            return "\n".join(p for p in parts if p)
        if isinstance(content, str):
            return content
        if isinstance(result.get("output"), str):
            return result["output"]
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


def _write_record(proc: subprocess.Popen, record: dict) -> None:
    proc.stdin.write(json.dumps(record, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def run_turn(task: str, *, model: str, api_key_env: str = "DEEPSEEK_API_KEY",
             system_prompt: str | None = None, thinking: str | None = None,
             tools: str = "read,write,edit,bash,find,grep,ls",
             session_id: str | None = None, session_dir: str | None = None,
             cwd: str | None = None, timeout_s: int = 900,
             approval_timeout_s: int = 180, cancel_event=None,
             on_event=None, on_approval=None,
             extension_paths: list[str] | None = None,
             max_tool_calls: int = 60,
             pi_override: str | None = None) -> PiTurnResult:
    """执行一轮运维任务:pi RPC + 工具 + 流式事件 + 审批 + 取消。

    每轮一个 pi 子进程;``session_id`` 存在时用 pi 会话文件续接上下文(调用方
    负责按站点会话生成稳定 id),否则 ``--no-session``。

    协议依据 pi 1.3 ``docs/rpc.md``:stdin 写 JSONL 命令(``prompt``/``abort``/
    ``extension_ui_response``),stdout 读 JSONL 事件。事件原样转给 ``on_event``,
    由 collector.assistant 决定前端卡片与落库;``on_approval`` 收到
    ``extension_ui_request`` 后返回 True/False(confirm)或字符串/None(select 等)。
    无人应答、回调异常、进程退出都按 fail-closed 处理:拒绝。
    """
    import queue
    from pathlib import Path

    task = (task or "").strip()
    if not task:
        raise PiAgentError("任务为空")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise PiAgentError(f"缺少 {api_key_env}(检查 data/assistant.env)")
    if session_dir:
        Path(session_dir).mkdir(parents=True, exist_ok=True)
    for path in extension_paths or []:
        if not Path(path).is_file():
            raise PiAgentError(f"策略扩展不存在: {path}")

    args = build_turn_args(
        model=model, system_prompt=system_prompt, thinking=thinking,
        tools=tools, session_id=session_id, session_dir=session_dir,
        extension_paths=extension_paths, pi_override=pi_override)
    env = dict(os.environ)
    env[api_key_env] = api_key
    peak: list[float] = [0.0]
    stop = threading.Event()
    started = time.monotonic()
    deadline = started + max(1, int(timeout_s))

    proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, env=env, cwd=cwd,
                            start_new_session=True)
    sampler = threading.Thread(target=_sample_rss, args=(proc.pid, stop, peak),
                               daemon=True)
    sampler.start()

    records: "queue.Queue[str | None]" = queue.Queue()
    stderr_lines: list[str] = []

    def _pump() -> None:
        try:
            for line in proc.stdout:
                records.put(line)
        except (OSError, ValueError):
            pass
        finally:
            records.put(None)

    def _drain_stderr() -> None:
        try:
            for line in proc.stderr:
                stderr_lines.append(line.rstrip())
                if len(stderr_lines) > 40:
                    del stderr_lines[: len(stderr_lines) - 40]
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=_pump, daemon=True)
    err_reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()
    err_reader.start()

    reply = ""
    finish_reason = "stop"
    tokens_in = tokens_out = 0
    aborted = False
    settled = False
    prompt_ok = False
    error: str | None = None
    tool_calls: list[dict] = []
    calls_by_id: dict[str, dict] = {}
    def _emit(event: dict) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                pass  # 前端/落库的异常不影响 pi 进程收尾

    def _kill() -> None:
        # 用 os.killpg:子进程以 start_new_session 启动,pgid == pid,
        # 一次杀掉 pi 及其工具子进程。不要用外部 kill 命令(见 run() 的注释)。
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            pass

    try:
        _write_record(proc, {"id": "prompt-1", "type": "prompt", "message": task})
        prompt_id = "prompt-1"

        while not settled:
            if cancel_event is not None and cancel_event.is_set():
                aborted = True
                try:
                    _write_record(proc, {"id": "abort-1", "type": "abort"})
                except (OSError, ValueError):
                    pass
                # 给 pi 5 秒收尾;仍不结束就杀进程组
                end = time.monotonic() + 5
                while time.monotonic() < end and not settled:
                    try:
                        line = records.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "agent_settled":
                        settled = True
                break
            if time.monotonic() > deadline:
                error = f"执行超时(>{timeout_s}s,已终止)"
                _kill()                      # 立刻停掉正在执行的工具,不留给 finally 5 秒
                break
            try:
                line = records.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None and records.empty():
                    break
                continue
            if line is None:
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # 非协议行(横幅等)忽略
            kind = ev.get("type")

            if kind == "response":
                if ev.get("id") == prompt_id:
                    if not ev.get("success"):
                        error = f"prompt 未接受: {ev.get('error') or '未知错误'}"
                        break
                    disp = (ev.get("data") or {}).get("disposition")
                    if disp == "handled":
                        settled = True
                    else:
                        prompt_ok = True
                continue

            if kind == "extension_ui_request":
                method = ev.get("method")
                if method in ("confirm", "select", "input", "editor"):
                    decision = None
                    if on_approval is not None:
                        try:
                            decision = on_approval(ev)
                        except Exception:
                            decision = None
                    response: dict = {"type": "extension_ui_response",
                                      "id": ev.get("id")}
                    if method == "confirm":
                        response["confirmed"] = decision is True
                    elif method == "select":
                        if isinstance(decision, str) and decision:
                            response["value"] = decision
                        else:
                            response["cancelled"] = True
                    else:
                        if isinstance(decision, str):
                            response["value"] = decision
                        else:
                            response["cancelled"] = True
                    try:
                        _write_record(proc, response)
                    except (OSError, ValueError):
                        pass
                # notify/setStatus 等 fire-and-forget 忽略
                continue

            if kind == "message_update":
                delta = (ev.get("assistantMessageEvent") or {}).get("delta")
                if isinstance(delta, str) and delta:
                    reply = (reply + delta)[-_REPLY_MAX:]
                _emit(ev)
                continue

            if kind == "message_end":
                msg = ev.get("message") or {}
                if msg.get("role") == "assistant":
                    parts = [str(p.get("text", "")) for p in msg.get("content", [])
                             if isinstance(p, dict) and p.get("type") == "text"]
                    text = "\n".join(p for p in parts if p).strip()
                    if text:
                        reply = text[-_REPLY_MAX:]
                    finish_reason = str(msg.get("stopReason") or finish_reason)
                    usage = msg.get("usage") or {}
                    tokens_in = int(usage.get("input") or tokens_in or 0)
                    tokens_out = int(usage.get("output") or tokens_out or 0)
                _emit(ev)
                continue

            if kind == "tool_execution_start":
                name = str(ev.get("toolName") or "?")
                cid = str(ev.get("toolCallId") or "")
                call = {"id": cid, "name": name, "args": ev.get("args"),
                        "status": "running", "output": "", "duration_s": None}
                tool_calls.append(call)
                if cid:
                    calls_by_id[cid] = call
                if len(tool_calls) > max_tool_calls:
                    error = f"工具调用数超过上限({max_tool_calls})"
                    _kill()
                    break
                _emit(ev)
                continue

            if kind == "tool_execution_end":
                cid = str(ev.get("toolCallId") or "")
                call = calls_by_id.get(cid)
                if call is not None:
                    call["status"] = "error" if ev.get("isError") else "ok"
                    call["output"] = _tool_text(ev.get("result"))
                    ms = ev.get("durationMs")
                    if isinstance(ms, (int, float)):
                        call["duration_s"] = round(ms / 1000.0, 3)
                _emit(ev)
                continue

            if kind == "agent_settled":
                if ev.get("aborted"):
                    aborted = True
                if not prompt_ok and not reply and not tool_calls:
                    error = "pi 未开始执行 prompt"
                settled = True
                continue

            if kind == "error":
                error = str(ev.get("message") or ev.get("error") or "pi 运行错误")
                continue

        if error is None and proc.poll() not in (None, 0):
            tail = " / ".join(stderr_lines[-3:])
            error = f"pi 退出码 {proc.returncode}: {tail[:300] or '无 stderr'}"
    finally:
        stop.set()
        sampler.join(timeout=2)
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        try:
            reader.join(timeout=2)
            err_reader.join(timeout=2)
        except RuntimeError:
            pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except (OSError, ValueError):
                pass

    duration = time.monotonic() - started
    if error is not None:
        raise PiAgentError(error)
    if not settled and not aborted:
        raise PiAgentError("pi 会话提前结束（未收到 agent_settled）")
    return PiTurnResult(
        reply=reply.strip(), finish_reason=finish_reason,
        tokens_in=tokens_in, tokens_out=tokens_out,
        peak_rss_mb=peak[0], duration_s=round(duration, 2),
        session_id=session_id, tool_calls=tool_calls, aborted=aborted)
