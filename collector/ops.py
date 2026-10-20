"""站内运维 agent 的策略层（Python 侧）。

与 pi 扩展 ``deploy/pi-ext/ops-guard.ts`` 共用同一份策略文件
``deploy/pi-ext/ops-policy.json``：

- 策略文件是单一事实源（deny / safe / 审批命令、敏感路径、可写目录、工具上限）；
- 本模块负责加载策略、给路径与命令分级、写 JSONL 审计；
- TS 扩展在 pi 进程内做实际拦截，Python 侧供服务端二次校验与离线单测使用。

安全原则：fail-closed。解析失败、路径越界、未知情况一律不自动放行
（``classify_path`` 返回 deny/ask，``classify_bash`` 返回 ask）。
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# ---------------------------------------------------------------- 默认路径

# collector/ops.py -> 仓库根
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = _REPO_ROOT / "deploy" / "pi-ext" / "ops-policy.json"
DEFAULT_AUDIT_DIR = _REPO_ROOT / "data" / "ops-audit"

_POLICY_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_AUDIT_LOCK = threading.Lock()

# 策略中必须存在的列表字段
_LIST_KEYS = (
    "secret_paths",
    "writable_dirs",
    "approval_dirs",
    "deny",
    "safe",
    "approval_required_always",
)


# ---------------------------------------------------------------- 策略加载


def _validate_policy(data: Any, source: Path) -> None:
    """校验策略结构；不合格直接抛 ValueError（不要带着坏策略继续跑）。"""
    if not isinstance(data, dict):
        raise ValueError(f"策略必须是 JSON 对象: {source}")
    if not isinstance(data.get("version"), int) or data["version"] < 1:
        raise ValueError(f"策略 version 非法: {source}")
    if not isinstance(data.get("repo_root"), str) or not data["repo_root"].strip():
        raise ValueError(f"策略 repo_root 缺失或非字符串: {source}")
    # repo_root 可为相对路径（"."）：运行时按调用方 cwd / 仓库根解析，便于换机。
    max_calls = data.get("max_tool_calls")
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls < 1:
        raise ValueError(f"策略 max_tool_calls 必须是正整数: {source}")
    for key in _LIST_KEYS:
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            raise ValueError(f"策略字段 {key} 必须是非空字符串列表: {source}")

    # 拒绝规则必须是可编译的正则；坏正则意味着拦截失效，必须尽早失败。
    for pattern in data["deny"]:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"策略 deny 正则非法 {pattern!r}: {exc}") from exc


def load_policy(path: str | os.PathLike[str] | None = None, *, reload: bool = False) -> dict[str, Any]:
    """加载策略 JSON（默认 ``deploy/pi-ext/ops-policy.json``）。

    - 结果按 ``(路径, mtime, size)`` 缓存，避免每次工具调用都读盘；
    - ``reload=True`` 强制重读（测试/热更新用）；
    - 环境变量 ``DAIMONKEY_OPS_POLICY`` 可作为默认路径的覆盖；
    - 返回深拷贝，调用方随便改也不会污染缓存。
    """
    env_path = os.environ.get("DAIMONKEY_OPS_POLICY")
    raw_path = path if path is not None else (env_path or DEFAULT_POLICY_PATH)
    source = Path(raw_path).expanduser().resolve()

    try:
        st = source.stat()
    except OSError as exc:
        raise FileNotFoundError(f"策略文件不存在或不可读: {source}") from exc
    if not source.is_file():
        raise FileNotFoundError(f"策略路径不是文件: {source}")

    cache_key = (str(source), st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        if not reload and cache_key in _POLICY_CACHE:
            return copy.deepcopy(_POLICY_CACHE[cache_key])

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"策略 JSON 解析失败: {source}: {exc}") from exc
    _validate_policy(payload, source)

    with _CACHE_LOCK:
        # 同一路径只保留最新版本，避免缓存无限增长
        for key in [k for k in _POLICY_CACHE if k[0] == str(source)]:
            _POLICY_CACHE.pop(key, None)
        _POLICY_CACHE[cache_key] = copy.deepcopy(payload)
    return copy.deepcopy(payload)


# ---------------------------------------------------------------- 路径工具


def _normalize_entry(entry: str) -> str:
    """把策略里的相对路径条目规范成 ``a/b/c`` 形式。"""
    text = str(entry).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _entry_parts(entry: str) -> tuple[str, ...]:
    text = _normalize_entry(entry)
    return tuple(part for part in text.split("/") if part)


def _rel_parts(rel: str) -> tuple[str, ...]:
    return tuple(part for part in rel.split("/") if part and part != ".")


def _within(base: str, target: str) -> bool:
    """target 是否等于 base 或位于 base 之内（纯词法，不解析符号链接）。"""
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:  # 不同盘符/异常路径
        return False


def _resolve_path(path: Any, root: str | os.PathLike[str]) -> tuple[str | None, str | None, str]:
    """把工具给的路径解析成仓库内相对路径。

    返回 ``(rel, abs, reason)``：失败时 rel/abs 为 None，reason 说明原因。
    同时做词法检查（拦住 ``..`` 穿越）和 realpath 检查（拦住符号链接逃逸）。
    """
    if not isinstance(path, (str, os.PathLike)):
        return None, None, "路径类型非法"
    try:
        raw = os.fspath(path).strip()
    except TypeError:
        return None, None, "路径无法解析"
    if not raw:
        return None, None, "路径为空"

    root_abs = os.path.abspath(os.fspath(root))
    root_real = os.path.realpath(root_abs)
    target = raw if os.path.isabs(raw) else os.path.join(root_abs, raw)

    lexical = os.path.abspath(target)
    if not _within(root_abs, lexical):
        return None, None, "路径越界（.. 穿越仓库根）"

    real = os.path.realpath(target)
    if not _within(root_real, real):
        return None, None, "路径越界（符号链接指向仓库根之外）"

    rel = os.path.relpath(real, root_real)
    if rel == ".":
        rel = ""
    rel = rel.replace(os.sep, "/")
    return rel, real, ""


def _secret_matches(rel: str, policy: Mapping[str, Any]) -> str | None:
    """相对路径是否命中任一敏感路径；命中则返回该条目。

    采用「连续路径片段」匹配：``.ssh`` 能命中 ``foo/.ssh/id_rsa``，
    ``data/assistant.env`` 也能命中其子路径（宁可多拦，不可漏拦）。
    敏感路径比较不区分大小写。
    """
    rel_lower = tuple(part.lower() for part in _rel_parts(rel))
    for entry in policy.get("secret_paths", ()):  # type: ignore[union-attr]
        needle = tuple(part.lower() for part in _entry_parts(str(entry)))
        if not needle or len(needle) > len(rel_lower):
            continue
        for idx in range(len(rel_lower) - len(needle) + 1):
            if rel_lower[idx : idx + len(needle)] == needle:
                return str(entry)
    return None


def is_secret_path(path: Any, root: str | os.PathLike[str]) -> bool:
    """路径是否属于策略中的敏感路径（相对 root 判定，仓库外一律 False）。"""
    rel, _abs, _reason = _resolve_path(path, root)
    if rel is None:
        return False
    return _secret_matches(rel, load_policy()) is not None


def _best_dir_match(rel: str, dirs: list[str]) -> tuple[int, str]:
    """返回 ``(深度, 条目)``：命中的最长前缀；深度 0 表示未命中。"""
    best_depth = 0
    best_entry = ""
    for raw in dirs:
        entry = _normalize_entry(raw)
        if not entry:
            continue
        if rel == entry or rel.startswith(entry + "/"):
            depth = len(_entry_parts(entry))
            if depth > best_depth:
                best_depth, best_entry = depth, entry
    return best_depth, best_entry


# ---------------------------------------------------------------- 路径分级


def classify_path(path: Any, root: str | os.PathLike[str], mode: str) -> tuple[str, str]:
    """按读/写语义给路径分级。

    - 仓库外（含 ``..`` 穿越、符号链接逃逸）→ deny；
    - 敏感路径 → deny（读写都禁）；
    - read：仓库内其余路径 → allow；
    - write：最长前缀优先。可写目录比审批目录更具体 → allow，否则 → ask；
      不在任何白名单 → ask（仓库根内默认需审批）。
    """
    if mode not in ("read", "write"):
        return "deny", f"未知模式: {mode!r}"

    policy = load_policy()
    rel, _abs, reason = _resolve_path(path, root)
    if rel is None:
        return "deny", reason

    hit = _secret_matches(rel, policy)
    if hit is not None:
        return "deny", f"敏感路径禁止访问: {hit}"

    if mode == "read":
        return "allow", "仓库内只读访问"

    approval_depth, approval_entry = _best_dir_match(rel, policy["approval_dirs"])
    writable_depth, writable_entry = _best_dir_match(rel, policy["writable_dirs"])
    if writable_depth and writable_depth > approval_depth:
        return "allow", f"可写目录: {writable_entry}"
    if approval_depth:
        return "ask", f"审批目录: {approval_entry}"
    if writable_depth:
        return "allow", f"可写目录: {writable_entry}"
    return "ask", "仓库内非白名单目录，需审批"


# ---------------------------------------------------------------- 命令分级


def _squash(text: str) -> str:
    """折叠空白，便于做命令前缀比较。"""
    return re.sub(r"\s+", " ", text).strip()


def _cmd_prefix(segment: str, item: str) -> bool:
    """命令段是否以策略条目开头（按词边界，避免 ``lsfoo`` 命中 ``ls``）。"""
    seg = _squash(segment)
    needle = _squash(item)
    if not needle:
        return False
    return seg == needle or seg.startswith(needle + " ")


def _split_command(command: str) -> tuple[list[str], bool]:
    """按未引用的 ``; && || |`` 与换行拆分复合命令。

    同时检测写入/替换/后台执行特征（``>``、``$(``、反引号、``<(...)``、
    单个 ``&``）。单引号内视为字面量，双引号内 ``$(`` 与反引号仍会展开，
    因此仍标记为特殊。
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    special = False
    idx = 0
    length = len(command)
    while idx < length:
        ch = command[idx]
        nxt = command[idx + 1] if idx + 1 < length else ""
        if quote == "'":
            buf.append(ch)
            if ch == "'":
                quote = None
            idx += 1
            continue
        if quote == '"':
            if ch == '"':
                quote = None
                buf.append(ch)
                idx += 1
                continue
            if ch == "\\" and nxt:
                buf.append(ch + nxt)
                idx += 2
                continue
            if (ch == "$" and nxt == "(") or ch == "`":
                special = True
            buf.append(ch)
            idx += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            idx += 1
            continue
        if ch == "\\" and nxt:
            buf.append(ch + nxt)
            idx += 2
            continue
        if ch == "$" and nxt == "(":
            special = True
            buf.append(ch + nxt)
            idx += 2
            continue
        if ch == "`":
            special = True
            buf.append(ch)
            idx += 1
            continue
        if ch == ">":
            special = True
            buf.append(ch)
            idx += 1
            continue
        if ch == "<" and nxt == "(":
            special = True
            buf.append(ch)
            idx += 1
            continue
        if ch == "&":
            if nxt == "&":
                segments.append("".join(buf))
                buf = []
                idx += 2
                continue
            special = True
            buf.append(ch)
            idx += 1
            continue
        if ch == "|":
            segments.append("".join(buf))
            buf = []
            idx += 2 if nxt == "|" else 1
            continue
        if ch in (";", "\n"):
            segments.append("".join(buf))
            buf = []
            idx += 1
            continue
        buf.append(ch)
        idx += 1

    segments.append("".join(buf))
    return [seg.strip() for seg in segments if seg.strip()], special


def _has_tee(segment: str) -> bool:
    """命令段里是否出现 ``tee``（保守：参数/路径中出现也算）。"""
    for token in re.split(r"\s+", _squash(segment)):
        cleaned = token.strip("'\"").rstrip("/")
        if not cleaned:
            continue
        if cleaned.rsplit("/", 1)[-1].lower() == "tee":
            return True
    return False


def classify_bash(command: Any, root: str | os.PathLike[str] | None = None) -> tuple[str, str]:
    """给 bash 命令分级：deny > 永久审批 > safe 前缀 > 其余 ask。

    ``root`` 保留在签名里与 ``classify_path`` 对称；命令本身的路径约束
    由 deny 正则与审批规则覆盖。
    """
    del root  # 命令策略与仓库根无关，保留参数只为接口一致
    if not isinstance(command, str) or not command.strip():
        return "ask", "空命令或非字符串，需人工确认"

    policy = load_policy()

    # 1) 硬拒绝：整条命令做正则扫描（最保守，引号也拦）。
    for pattern in policy["deny"]:
        try:
            match = re.search(pattern, command, re.IGNORECASE)
        except re.error:  # load_policy 已校验；这里再兜底一次
            continue
        if match:
            return "deny", f"命中拒绝规则: {pattern}（匹配 {match.group(0)[:80]!r}）"

    segments, special = _split_command(command)
    if not segments:
        return "ask", "命令无法解析，需人工确认"

    # 2) 永久审批：任一段以审批条目开头。
    for segment in segments:
        for item in policy["approval_required_always"]:
            if _cmd_prefix(segment, item):
                return "ask", f"需审批命令: {item}"

    # 3) 写入/替换/后台特征：绝不自动放行。
    if special:
        return "ask", "包含写入/替换/后台执行特征，需人工确认"

    # 4) 所有分段都必须是 safe 前缀，才整体放行。
    for segment in segments:
        if _has_tee(segment):
            return "ask", "命令包含 tee/输出写入，需人工确认"
        if not any(_cmd_prefix(segment, item) for item in policy["safe"]):
            return "ask", f"非安全命令: {segment[:120]}"

    return "allow", "全部为安全命令"


# ---------------------------------------------------------------- 审计


def audit_path(
    dir: str | os.PathLike[str] | None = None,
    day: str | date | datetime | None = None,
) -> Path:
    """返回当日审计文件路径：``<dir>/YYYYMMDD.jsonl``。

    默认目录取 ``DAIMONKEY_OPS_AUDIT``，否则仓库内 ``data/ops-audit``。
    """
    base = Path(dir) if dir is not None else Path(
        os.environ.get("DAIMONKEY_OPS_AUDIT") or DEFAULT_AUDIT_DIR
    )
    if day is None:
        stamp = time.strftime("%Y%m%d")
    elif isinstance(day, datetime):
        stamp = day.strftime("%Y%m%d")
    elif isinstance(day, date):
        stamp = day.strftime("%Y%m%d")
    else:
        stamp = re.sub(r"[^0-9]", "", str(day))
        if len(stamp) != 8:
            raise ValueError(f"day 必须是日期或 YYYYMMDD: {day!r}")
    return base / f"{stamp}.jsonl"


def append_audit(entry: Mapping[str, Any], dir: str | os.PathLike[str] | None = None) -> bool:
    """向当日 JSONL 追加一条审计（O_APPEND，文件权限 0600）。

    审计是旁路：任何失败都只返回 False，绝不打断工具调用。
    """
    try:
        record = dict(entry)
        record.setdefault(
            "ts",
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        target = audit_path(dir)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        data = line.encode("utf-8")

        with _AUDIT_LOCK:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            try:
                os.chmod(target, 0o600)  # 已有文件也收紧权限
            except OSError:
                pass
        return True
    except Exception:  # noqa: BLE001 - 审计失败不得影响主流程
        return False
