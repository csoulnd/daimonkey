"""AI 接入改造：页面 AI 摘要 + 榜单 AI 分析评分的编排与降级。

原则：
- 规则分（rankings 表）永不改写；AI 产物只落 ai_summaries / ai_scores，
  页面上显著标注"AI 生成"（方法论 v1.4）。
- 所有 AI 调用失败（缺 Key / 配额 / 超时 / 解析失败）都只降级为跳过并记
  notes，不影响更新状态，旧摘要/旧 AI 分继续渲染。
- 摘要带数据指纹：同一份数据反复 build 不重复烧配额。
"""
from __future__ import annotations

import json
import re

from . import assistant, db

class AiParseError(assistant.AssistantError):
    """AI 输出无法解析（可安全重试一次：配额与执行均已正常）。"""


AI_DEFAULTS: dict = {
    # max_tokens 是"推理 + 输出"的总预算：推理模型在大任务上会先消耗
    # 大量推理 token，预算过小会出现 finish_reason=max-tokens 的空回复。
    "summaries": {"enabled": True, "pages": ["home", "news", "knowledge"],
                  "max_tokens": 3000},
    "scoring": {"enabled": True, "top_n": 15, "ai_weight": 0.0,
                "max_tokens": 4000},
}


def cfg() -> dict:
    conf = assistant.cfg().get("ai") or {}
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in AI_DEFAULTS.items()}
    for key, value in conf.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merged[key] | value
        else:
            merged[key] = value
    return merged


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---- 数据指纹：摘要是否需要重新生成的判断依据 ----

def data_signature(c, page: str) -> str | None:
    """各页面当前数据的指纹；无数据时返回 None（跳过生成）。"""
    if page == "home":
        row = c.execute(
            "SELECT snapshot_date, COUNT(*) n FROM rankings"
            " WHERE board = 'unified' GROUP BY snapshot_date"
            " ORDER BY snapshot_date DESC LIMIT 1").fetchone()
        return f"{row['snapshot_date']}:{row['n']}" if row else None
    if page == "news":
        row = c.execute("SELECT MAX(fetched_at) t, COUNT(*) n FROM news_items"
                        ).fetchone()
        return f"{row['t']}:{row['n']}" if row and row["t"] else None
    if page == "knowledge":
        from .knowledge import load_notes
        meta, notes = load_notes()
        if not meta.get("commit"):
            return None
        return f"{meta['commit']}:{len(notes)}"
    return None


def summary_of(c, page: str) -> dict | None:
    row = c.execute("SELECT page, content, data_signature, created_at"
                    " FROM ai_summaries WHERE page = ?", (page,)).fetchone()
    return dict(row) if row else None


def save_summary(c, page: str, content: str, signature: str | None) -> None:
    c.execute(
        "INSERT INTO ai_summaries (page, content, data_signature, created_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(page) DO UPDATE SET content = excluded.content,"
        " data_signature = excluded.data_signature, created_at = excluded.created_at",
        (page, content, signature, _now()))


# ---- 编排入口（管线调用，全部容错） ----

def refresh_summaries_if_stale(c) -> list[str]:
    """按需刷新页面 AI 摘要（数据刷新 / 首次启用两条路径都覆盖）。

    返回 notes 列表；任何失败只降级不抛出（含非预期异常——AI 步骤
    绝不允许阻断采集/建站，实战教训：None 格式化曾穿透护栏打挂更新）。
    """
    conf = cfg()["summaries"]
    if not conf.get("enabled", True) or not assistant.cfg().get("enabled", True):
        return []
    notes: list[str] = []
    for page in conf.get("pages", []):
        try:
            signature = data_signature(c, page)
            if signature is None:
                notes.append(f"[ai:{page}] 暂无数据，跳过摘要")
                continue
            existing = summary_of(c, page)
            if existing and existing["data_signature"] == signature:
                continue  # 同一份数据，不重复生成
            result = assistant.run_internal("page_summary", {"page": page,
                                                             "signature": signature},
                                            max_tokens=int(conf.get("max_tokens", 3000)))
            notes.append(f"[ai:{page}] 摘要已生成（~{result['tokens_in']}+"
                         f"{result['tokens_out']} tok）")
        except Exception as e:
            notes.append(f"[ai:{page}] 摘要跳过: {type(e).__name__}: {e}")
    return notes


def score_boards(c, snapshot_date: str) -> list[str]:
    """全量更新后为配置的榜单生成 AI 分析分（逐榜容错）。"""
    conf = cfg()["scoring"]
    if not conf.get("enabled", True) or not assistant.cfg().get("enabled", True):
        return []
    boards = conf.get("boards")
    if not boards:
        boards = [r[0] for r in c.execute(
            "SELECT DISTINCT board FROM rankings WHERE snapshot_date = ?"
            " ORDER BY board", (snapshot_date,))]
    notes: list[str] = []
    for board in boards:
        try:
            n = c.execute("SELECT COUNT(*) n FROM rankings WHERE board = ?"
                          " AND snapshot_date = ?",
                          (board, snapshot_date)).fetchone()["n"]
            if not n:
                notes.append(f"[ai:{board}] 当日无榜单数据，跳过评分")
                continue
            already = c.execute(
                "SELECT COUNT(*) n FROM ai_scores WHERE board = ?"
                " AND snapshot_date = ?", (board, snapshot_date)).fetchone()["n"]
            if already >= n:
                continue  # 已评分，不重复
            last_error: Exception | None = None
            for _attempt in (1, 2):  # 解析失败重试一次；配额/忙/缺 Key 不重试
                try:
                    assistant.run_internal(
                        "board_ai_analysis",
                        {"board": board, "snapshot_date": snapshot_date},
                        max_tokens=int(conf.get("max_tokens", 4000)))
                    last_error = None
                    break
                except AiParseError as e:
                    last_error = e
                except (assistant.AssistantBusy, assistant.AssistantQuota):
                    raise
            if last_error is not None:
                raise last_error
            notes.append(f"[ai:{board}] AI 分析分已生成")
        except Exception as e:  # 含非预期异常：AI 步骤绝不允许阻断采集
            notes.append(f"[ai:{board}] AI 评分跳过: {type(e).__name__}: {e}")
    return notes


# ---- JSON 提取（board_ai_analysis 的模型输出解析） ----

def parse_scores_reply(reply: str, valid_slugs: set[str]) -> list[dict] | None:
    """从模型回复中提取 [{slug, ai_score, rationale}]；解析失败返回 None。

    容错：剥 markdown 代码栅栏、取首个 JSON 数组、slug 不在名单的丢弃、
    分数裁剪到 [0, 100]。
    """
    text = reply.strip()
    fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence:
        text = fence.group(1)
    else:
        bracket = re.search(r"\[[\s\S]*?\]", text)
        if bracket:
            text = bracket.group(0)
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list):
        return None
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug", "")).strip()
        if slug not in valid_slugs:
            continue
        try:
            score = float(item.get("ai_score"))
        except (TypeError, ValueError):
            continue
        out.append({"slug": slug, "ai_score": max(0.0, min(100.0, score)),
                    "rationale": str(item.get("rationale", ""))[:60]})
    return out or None
