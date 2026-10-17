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
import subprocess
import threading
import time
from dataclasses import dataclass


class PiAgentError(Exception):
    """pi 子进程执行失败(缺 Key / 超时 / 输出解析失败)。"""


@dataclass
class PiResult:
    reply: str
    finish_reason: str          # stop / length / ...
    tokens_in: int
    tokens_out: int
    peak_rss_mb: float


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
            # 超时:杀整个进程组(pi 可能派生子进程)
            subprocess.run(["kill", "-9", f"-{proc.pid}"], check=False,
                           capture_output=True)
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
