"""内部工作流层：agent loop 织入站点流程的最小扩展点。

新增工作流 = 一个 WorkflowSpec + 注册进 WORKFLOWS（可选再在 registry.yaml
assistant.workflows 下补充展示描述），零框架成本、天然进 git。

安全边界：所有 AI 产物只落库为带 workflow/channel 标记的草稿
（insights 表 llm_generated=1 或 assistant_messages），确定性数据管线不动。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import assistant, db, news


@dataclass
class WorkflowSpec:
    name: str
    description: str
    max_tokens: int = 2000
    build_task: callable = None          # payload -> 任务字符串
    store: callable = None               # (payload, reply) -> None，AI 产物落库

    def __post_init__(self) -> None:
        if self.build_task is None:
            raise ValueError(f"workflow {self.name} 缺少 build_task")


# ---- 工作流 1：新闻周报 AI 摘要 ----

def _digest_task(payload: dict) -> str:
    week = payload.get("week") or _current_week()
    start, end = news.week_range(week)
    with db.conn() as c:
        rows = c.execute(
            "SELECT title, source_name, category, summary FROM news_items"
            " WHERE substr(published_at, 1, 10) BETWEEN ? AND ?"
            " ORDER BY published_at DESC LIMIT 60", (start, end)).fetchall()
    if not rows:
        raise assistant.AssistantError(f"{week} 没有可总结的新闻条目")
    lines = [f"- [{r['category']}] {r['title']}（{r['source_name']}）"
             for r in rows]
    return (
        "以下是本周 AI Agent 领域新闻条目（分类/标题/来源）：\n"
        + "\n".join(lines)
        + "\n\n请用中文写一份 300 字以内的本周领域摘要：概括主要趋势、"
          "值得关注的发布与事件；不逐条罗列，只提炼脉络。"
          "末尾单独一行输出：`AI 生成于 " + week + "`。")


def _digest_store(payload: dict, reply: str) -> None:
    week = payload.get("week") or _current_week()
    with db.conn() as c:
        c.execute(
            "INSERT INTO insights (title, summary, source_url, source_name,"
            " published_at, tags, llm_generated, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, 'draft', datetime('now', 'localtime'))",
            (f"AI 周报摘要 {week}", reply, "", "DSH assistant",
             week, "news-digest,ai"))


def _current_week() -> str:
    from datetime import date
    iso = date.today().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ---- 工作流 2：文案润色（只出草稿，不直接上线） ----

def _polish_task(payload: dict) -> str:
    text = str(payload.get("text", "")).strip()
    if not text:
        raise assistant.AssistantError("润色工作流需要 text 输入")
    return (
        "请润色以下网站文案：保持原意与中文表达习惯，让行文更清晰简洁；"
        "只输出润色后的文本，不要解释。\n\n" + text)


def _polish_store(payload: dict, reply: str) -> None:
    # 润色结果仅存为内部消息草稿，由人工确认后手动采用
    with db.conn() as c:
        db.ensure_assistant_tables(c)
        c.execute(
            "INSERT INTO assistant_messages (session_id, role, content, channel,"
            " workflow, created_at) VALUES (NULL, 'assistant', ?, 'internal',"
            " 'content_polish', datetime('now', 'localtime'))",
            (json.dumps({"input": str(payload.get("text", ""))[:2000],
                         "polished": reply}, ensure_ascii=False),))


# ---- 工作流 3：站点优化建议（只出建议清单，不改任何东西） ----

def _improve_task(payload: dict) -> str:
    notes = []
    status_file = db.DB_PATH.parent / "update_status.json"
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
        notes.append(f"最近一次更新状态: {json.dumps(status, ensure_ascii=False)[:500]}")
    except (OSError, json.JSONDecodeError):
        notes.append("最近一次更新状态: 不可读")
    with db.conn() as c:
        rows = c.execute(
            "SELECT source_key, status, note FROM source_runs"
            " WHERE finished_at = (SELECT MAX(finished_at) FROM source_runs s2"
            " WHERE s2.source_key = source_runs.source_key) LIMIT 20").fetchall() \
            if _has_table("source_runs") else []
    for r in rows:
        notes.append(f"数据源 {r['source_key']}: {r['status']} {r['note'] or ''}")
    return (
        "以下是这个数据站点最近一次更新的运行状态与数据源状态：\n"
        + "\n".join(notes)
        + "\n\n请基于这些信息给出 3-5 条具体的运维/数据质量优化建议，"
          "每条一行、可执行、不空泛；不要建议改变'数据可溯源、无自动调度'的既定原则。")


def _improve_store(payload: dict, reply: str) -> None:
    with db.conn() as c:
        db.ensure_assistant_tables(c)
        c.execute(
            "INSERT INTO assistant_messages (session_id, role, content, channel,"
            " workflow, created_at) VALUES (NULL, 'assistant', ?, 'internal',"
            " 'site_improvement_notes', datetime('now', 'localtime'))", (reply,))


def _has_table(name: str) -> bool:
    with db.conn() as c:
        row = c.execute("SELECT name FROM sqlite_master WHERE type = 'table'"
                        " AND name = ?", (name,)).fetchone()
    return row is not None


WORKFLOWS: dict[str, WorkflowSpec] = {
    "news_digest_summary": WorkflowSpec(
        name="news_digest_summary",
        description="为本周新闻生成 300 字 AI 摘要（草稿，落 insights 表并标记 AI 生成）",
        max_tokens=1200,
        build_task=_digest_task,
        store=_digest_store),
    "content_polish": WorkflowSpec(
        name="content_polish",
        description="润色指定文案（输入 {\"text\": ...}，只出草稿不自动上线）",
        max_tokens=2000,
        build_task=_polish_task,
        store=_polish_store),
    "site_improvement_notes": WorkflowSpec(
        name="site_improvement_notes",
        description="基于更新与数据源状态给出运维优化建议（只出建议）",
        max_tokens=1500,
        build_task=_improve_task,
        store=_improve_store),
}


def get(name: str) -> WorkflowSpec:
    spec = WORKFLOWS.get(name)
    if spec is None:
        raise assistant.AssistantError(f"未知工作流: {name}")
    return spec


def describe() -> list[dict]:
    return [{"name": s.name, "description": s.description}
            for s in WORKFLOWS.values()]
