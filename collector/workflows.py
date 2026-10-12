"""内部工作流层：agent loop 织入站点流程的最小扩展点。

新增工作流 = 一个 WorkflowSpec + 注册进 WORKFLOWS（可选再在 registry.yaml
assistant.workflows 下补充展示描述），零框架成本、天然进 git。

安全边界：所有 AI 产物只落库为带 workflow/channel 标记的草稿
（insights 表 llm_generated=1 或 assistant_messages），确定性数据管线不动。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import ai as ai_mod
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


def _num(v, fmt: str = ".0f", dash: str = "—") -> str:
    """安全数字格式化：None（如新品无动量历史）显示占位符。"""
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return dash


# ---- 工作流 4：页面 AI 摘要（首页 / 新闻 / 知识库三板块统一格式） ----

def _page_summary_task(payload: dict) -> str:
    page = payload.get("page")
    with db.conn() as c:
        if page == "home":
            rows = c.execute(
                "SELECT r.rank, r.heat_score, r.momentum_score, r.delta_7d,"
                " r.coverage, p.name, p.vendor FROM rankings r"
                " JOIN products p ON p.id = r.product_id"
                " WHERE r.board = 'unified' ORDER BY r.snapshot_date DESC, r.rank"
                " LIMIT 10").fetchall()
            if not rows:
                raise assistant.AssistantError("暂无榜单数据")
            total = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
            obs = c.execute(
                "SELECT COUNT(*) n FROM rankings WHERE board = 'unified'"
                " AND coverage < 0.5 AND snapshot_date ="
                " (SELECT MAX(snapshot_date) FROM rankings WHERE board='unified')"
            ).fetchone()["n"]
            lines = [f"{r['rank']}. {r['name']}（{r['vendor']}）热度{_num(r['heat_score'])}"
                     f" 动量{_num(r['momentum_score'])}"
                     + (f" 7日{_num(r['delta_7d'], '+.1f')}"
                        if r['delta_7d'] is not None else "")
                     for r in rows]
            data = (f"联合榜前 10（共收录 {total} 个产品，观察区 {obs} 个）：\n"
                    + "\n".join(lines))
        elif page == "news":
            rows = c.execute(
                "SELECT title, source_name, category FROM news_items"
                " ORDER BY published_at DESC LIMIT 40").fetchall()
            if not rows:
                raise assistant.AssistantError("暂无新闻数据")
            counts = c.execute("SELECT category, COUNT(*) n FROM news_items"
                               " GROUP BY category ORDER BY n DESC").fetchall()
            lines = [f"- [{r['category']}] {r['title']}（{r['source_name']}）"
                     for r in rows]
            data = ("最近 40 条新闻（分类计数：" +
                    "、".join(f"{r['category']}{r['n']}" for r in counts) + "）：\n"
                    + "\n".join(lines))
        elif page == "knowledge":
            from .knowledge import load_notes
            meta, notes = load_notes()
            if not notes:
                raise assistant.AssistantError("暂无知识库快照")
            groups: dict[str, int] = {}
            for n in notes:
                groups[n["group_name"]] = groups.get(n["group_name"], 0) + 1
            data = (f"知识库共 {meta['count']} 篇文档，快照于 {meta['updated'][:10]}"
                    f"（commit {meta['short_commit']}）；分组："
                    + "、".join(f"{k} {v} 篇" for k, v in sorted(groups.items())))
        else:
            raise assistant.AssistantError(f"未知摘要页: {page}")
    return (
        "以下是本站数据页面的当周概况。\n" + data
        + "\n\n请用中文写一段 150-200 字的页面摘要，面向初次访问的读者："
          "先说这个板块是什么，再概括当前的重点信息与变化趋势；"
          "只输出摘要正文，不要标题、不要解释、不要 Markdown 语法。")


def _page_summary_store(payload: dict, reply: str) -> None:
    page = str(payload.get("page", ""))
    signature = payload.get("signature")
    with db.conn() as c:
        db.ensure_ai_tables(c)
        ai_mod.save_summary(c, page, reply.strip(), str(signature) if signature else None)


# ---- 工作流 5：榜单 AI 分析评分（与规则分平行存储） ----

def _board_scores_task(payload: dict) -> str:
    board = str(payload.get("board", ""))
    date = str(payload.get("snapshot_date", ""))
    top_n = int(ai_mod.cfg()["scoring"].get("top_n", 15))
    with db.conn() as c:
        rows = c.execute(
            "SELECT r.rank, r.heat_score, r.momentum_score, r.delta_7d, r.coverage,"
            " p.name, p.slug, p.vendor, p.category FROM rankings r"
            " JOIN products p ON p.id = r.product_id"
            " WHERE r.board = ? AND r.snapshot_date = ?"
            " ORDER BY r.rank LIMIT ?", (board, date, top_n)).fetchall()
    if not rows:
        raise assistant.AssistantError(f"榜单 {board} 当日无数据")
    lines = [f"{r['rank']}. slug={r['slug']} {r['name']}（{r['vendor']}，"
             f"{r['category']}）规则热度{_num(r['heat_score'])}"
             f" 动量{_num(r['momentum_score'])}"
             + (f" 7日变化{_num(r['delta_7d'], '+.1f')}"
                if r['delta_7d'] is not None else "")
             + f" 数据覆盖率{_num(r['coverage'], '.0%')}"
             for r in rows]
    return (
        f"以下是榜单 {board} 的最新排名数据（{date}）。\n" + "\n".join(lines)
        + "\n\n请为每个产品给出 0-100 的 AI 分析分（综合生态影响力、增长势头、"
          "数据可信度的判断）与一句话理由（≤40 字）。"
          "只输出一个 JSON 数组，不要任何其他文字或代码栅栏，格式："
          '[{"slug": "...", "ai_score": 0-100 的数字, "rationale": "..."}]')


def _board_scores_store(payload: dict, reply: str) -> None:
    board = str(payload.get("board", ""))
    date = str(payload.get("snapshot_date", ""))
    with db.conn() as c:
        db.ensure_ai_tables(c)
        rows = c.execute(
            "SELECT p.id, p.slug FROM rankings r JOIN products p ON p.id = r.product_id"
            " WHERE r.board = ? AND r.snapshot_date = ?", (board, date)).fetchall()
        by_slug = {r["slug"]: r["id"] for r in rows}
        parsed = ai_mod.parse_scores_reply(reply, set(by_slug))
        if not parsed:
            raise ai_mod.AiParseError("AI 评分输出无法解析为 JSON 数组")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        c.execute("DELETE FROM ai_scores WHERE board = ? AND snapshot_date = ?",
                  (board, date))
        c.executemany(
            "INSERT INTO ai_scores (board, snapshot_date, product_id, ai_score,"
            " rationale, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(board, date, by_slug[item["slug"]], item["ai_score"],
              item["rationale"], now) for item in parsed])
        if not c.execute("SELECT COUNT(*) n FROM ai_scores WHERE board = ?"
                         " AND snapshot_date = ?", (board, date)).fetchone()["n"]:
            raise assistant.AssistantError("AI 评分与榜单产品未能匹配")


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
    "page_summary": WorkflowSpec(
        name="page_summary",
        description="三板块页面 AI 摘要（payload: {page: home|news|knowledge}，"
                    "落 ai_summaries 表，管线按数据指纹自动触发）",
        max_tokens=900,
        build_task=_page_summary_task,
        store=_page_summary_store),
    "board_ai_analysis": WorkflowSpec(
        name="board_ai_analysis",
        description="榜单 AI 分析评分（payload: {board, snapshot_date}，"
                    "严格 JSON 输出，落 ai_scores 表，与规则分平行）",
        max_tokens=2500,
        build_task=_board_scores_task,
        store=_board_scores_store),
}


def get(name: str) -> WorkflowSpec:
    spec = WORKFLOWS.get(name)
    if spec is None:
        raise assistant.AssistantError(f"未知工作流: {name}")
    return spec


def describe() -> list[dict]:
    return [{"name": s.name, "description": s.description}
            for s in WORKFLOWS.values()]
