"""内部工作流层：agent loop 织入站点流程的最小扩展点。

新增工作流 = 一个 WorkflowSpec + 注册进 WORKFLOWS（可选再在 registry.yaml
assistant.workflows 下补充展示描述），零框架成本、天然进 git。

安全边界：所有 AI 产物只落库为带 workflow/channel 标记的草稿
（insights 表 llm_generated=1 或 assistant_messages），确定性数据管线不动。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from . import ai as ai_mod
from . import assistant, db, news, queries
from .config import registry, today_utc
from .scoring import load_latest_metrics


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


# ---- 工作流 1：新闻周报 AI 摘要（全站 / 按方向参数化） ----

def _direction_conf(key: str) -> dict:
    directions = registry().get("directions") or {}
    conf = directions.get(key)
    if not conf:
        raise assistant.AssistantError(f"未知方向: {key}")
    return conf


def _digest_task(payload: dict) -> str:
    week = payload.get("week") or _current_week()
    start, end = news.week_range(week)
    direction = payload.get("direction")
    scope = "AI Agent 领域"
    params: list = [start, end]
    sql = ("SELECT title, source_name, category, summary FROM news_items"
           " WHERE substr(published_at, 1, 10) BETWEEN ? AND ?")
    if direction:
        conf = _direction_conf(direction)
        cats = conf.get("news_categories") or []
        if not cats:
            raise assistant.AssistantError(f"{conf['name']}方向暂无新闻范围")
        sql += " AND category IN (" + ",".join("?" * len(cats)) + ")"
        params += cats
        scope = f"{conf['name']}方向"
    sql += " ORDER BY published_at DESC LIMIT 60"
    with db.conn() as c:
        rows = c.execute(sql, params).fetchall()
    if not rows:
        raise assistant.AssistantError(f"{week} 没有可总结的{scope}新闻条目")
    lines = [f"- [{r['category']}] {r['title']}（{r['source_name']}）"
             for r in rows]
    return (
        f"以下是本周{scope}新闻条目（分类/标题/来源）：\n"
        + "\n".join(lines)
        + f"\n\n请用中文写一份 300 字以内的本周{scope}摘要：概括主要趋势、"
          "值得关注的发布与事件；不逐条罗列，只提炼脉络。"
          "末尾单独一行输出：`AI 生成于 " + week + "`。")


def _digest_store(payload: dict, reply: str) -> None:
    week = payload.get("week") or _current_week()
    direction = payload.get("direction")
    if direction:
        conf = _direction_conf(direction)
        digest_conf = ai_mod.cfg().get("direction_digests", {})
        status = "published" if digest_conf.get("publish", True) else "draft"
        title = f"{conf['name']}方向周总结 {week}"
        tags = f"news-digest,ai,direction:{direction}"
    else:
        status, title, tags = "draft", f"AI 周报摘要 {week}", "news-digest,ai"
    with db.conn() as c:
        c.execute(
            "INSERT INTO insights (title, summary, source_url, source_name,"
            " published_at, tags, llm_generated, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, ?, datetime('now', 'localtime'))",
            (title, reply, "", "daimonkey 助手", week, tags, status))


def _current_week() -> str:
    from datetime import date
    iso = date.today().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _latest_news_week(c, categories: list[str]) -> str | None:
    clause = ",".join("?" * len(categories))
    row = c.execute(
        "SELECT COALESCE(published_at, fetched_at) t FROM news_items"
        " WHERE category IN (" + clause + ")"
        " ORDER BY t DESC LIMIT 1", categories).fetchone()
    return news.week_key(row["t"]) if row and row["t"] else None


def generate_direction_digests(week: str | None = None,
                               direction: str | None = None,
                               force: bool = False) -> list[str]:
    """按方向生成每周总结（AI 草稿/直接发布由配置决定）。

    不指定 week 时取该方向最近有新闻的 ISO 周（数据滞后时仍能生成“最近一期”，
    频道页会标注“本周总结待更新”）。已有该方向该周的总结则不重复调用 AI。
    任何异常只记 notes，绝不阻断建站。
    """
    conf = ai_mod.cfg().get("direction_digests", {})
    if not conf.get("enabled", True) or not assistant.cfg().get("enabled", True):
        return []
    all_dirs = registry().get("directions") or {}
    keys = [direction] if direction else list(all_dirs)
    notes: list[str] = []
    for key in keys:
        d = all_dirs.get(key)
        if not d:
            notes.append(f"[dir:{key}] 未知方向，跳过")
            continue
        cats = d.get("news_categories") or []
        if not cats:
            notes.append(f"[dir:{key}] 无新闻范围，跳过")
            continue
        with db.conn() as c:
            wk = week or _latest_news_week(c, cats)
            if not wk:
                notes.append(f"[dir:{key}] 暂无新闻，跳过")
                continue
            start, end = news.week_range(wk)
            n = c.execute(
                "SELECT COUNT(*) n FROM news_items WHERE category IN ("
                + ",".join("?" * len(cats)) + ")"
                " AND substr(COALESCE(published_at, fetched_at), 1, 10)"
                " BETWEEN ? AND ?", (*cats, start, end)).fetchone()["n"]
            if not n:
                notes.append(f"[dir:{key}] {wk} 无新闻，跳过")
                continue
            if not force:
                exists = c.execute(
                    "SELECT COUNT(*) n FROM insights WHERE llm_generated=1"
                    " AND tags LIKE ? AND published_at=?",
                    (f"%direction:{key}%", wk)).fetchone()["n"]
                if exists:
                    notes.append(f"[dir:{key}] {wk} 总结已存在，跳过")
                    continue
        try:
            result = assistant.run_internal(
                "news_digest_summary", {"direction": key, "week": wk},
                channel="internal", max_tokens=int(conf.get("max_tokens", 1200)))
            notes.append(f"[dir:{key}] {wk} 总结已生成（~{result['tokens_in']}+"
                         f"{result['tokens_out']} tok）")
        except Exception as e:  # AI 失败不阻断建站
            notes.append(f"[dir:{key}] 周总结跳过: {type(e).__name__}: {e}")
    return notes


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
    """安全数字格式化：None（如指标缺失）显示占位符。"""
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return dash


# ---- 工作流 4：页面 AI 摘要（首页 / 新闻 / 知识库三板块统一格式） ----

log = logging.getLogger("daimonkey.ai")

# 摘要数字对账：只抽取与"数量/名次"相关的数字。
# 日期（2026-10-12）不带这些量词，因此不会被误判；"2026 年"也不匹配。
_UNIT_NUMBER_RE = re.compile(r"(\d{1,6})\s*(?:个|条|名|篇|位|家|项|款|种)")
_RANK_NUMBER_RE = re.compile(r"第\s*(\d{1,4})")
_DATE_RE = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})")


def _pair_sums(values: list[int]) -> set[int]:
    """允许"两项合计"这类可解释的派生数字（如分类计数 69+68=137）。"""
    vals = sorted({int(v) for v in values})
    out = set(vals)
    for i, a in enumerate(vals):
        for b in vals[i + 1:]:
            out.add(a + b)
    return out


def _board_scope(c, board: str = "harness_core") -> dict:
    """某榜单最新快照的"板块口径"（与页面渲染同一套查询）。

    口径规则：total = 榜单收录成员数（与页面/KPI 一致）；ranked = 最新快照
    已入榜条数。绝不使用 products 全站计数（那会把 AA 模型、MLPerf 卡等算进来）。
    """
    boards = registry().get("boards") or {}
    if board not in boards:
        raise assistant.AssistantError(f"未知榜单: {board}")
    name = boards[board].get("name") or board
    snap = c.execute("SELECT MAX(snapshot_date) d FROM rankings WHERE board=?",
                     (board,)).fetchone()["d"]
    if not snap:
        raise assistant.AssistantError(f"{name}暂无榜单数据")
    latest = load_latest_metrics(c, today_utc())
    display = queries.display_board_items(
        c, board, queries.all_products(c), latest)
    items = display["rows"]
    if not items:
        raise assistant.AssistantError(f"{name}最新快照为空")
    ranked = c.execute(
        "SELECT COUNT(*) n FROM rankings WHERE board=? AND snapshot_date=?",
        (board, snap)).fetchone()["n"]
    sources = {m["source"]["key"] for it in items
               for m in it.get("metrics", {}).values()
               if (m.get("source") or {}).get("key")}
    columns = display.get("columns") or [dict(col) for col in
                                         (boards[board].get("columns") or [])]
    return {"key": board, "name": name, "snapshot": snap, "items": items,
            "total": len(items), "ranked": ranked, "sources": sources,
            "columns": columns}


def _scope_line(scope: dict) -> str:
    """提示词里的显式口径行，防止模型把别的板块也算进来。"""
    return (f"榜单口径：{scope['key']}（{scope['name']}），快照 {scope['snapshot']}，"
            f"收录 {scope['total']} 个、入榜 {scope['ranked']} 条（不含其他板块）")


def _base_allowed() -> set[int]:
    """数量对账的公共合法数字：0 在"个/条/名"语境下恒可解释。"""
    return {0}


def _summary_allowed(scope: dict, top_n: int,
                     rows: list[dict] | None = None) -> set[int]:
    """本板块内可解释的数字集合：条数、TOP N、数据源数、已有名次与原始指标。"""
    allowed = _base_allowed() | {scope["total"], scope["ranked"], top_n,
                                 len(scope["sources"])}
    allowed |= set(range(1, scope["total"] + 1))   # 第 N 名必须是该榜真实名次
    for it in (rows if rows is not None else scope["items"]):
        for value in (it.get("metric_values") or {}).values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                allowed.add(int(round(value)))
    return allowed


def _prompt_metric_columns(scope: dict) -> list[dict]:
    return [c for c in (scope.get("columns") or [])
            if c.get("type") == "metric"]


def _prompt_metric_value(col: dict, value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        key = str(col.get("key") or "")
        fmt = ",.1f" if key.startswith(("aa_", "mlperf_")) else ",.0f"
        return _num(value, fmt)
    return _num(value)


def _prompt_row(it: dict, cols: list[dict]) -> str:
    bits = []
    values = it.get("metric_values") or {}
    for col in cols:
        value = values.get(col.get("key"))
        if value is None:
            continue
        bits.append(f"{col.get('label')} {_prompt_metric_value(col, value)}")
    tail = " ".join(bits) if bits else "指标待采集"
    return f"{it['rank']}. {it['name']}（{it.get('vendor') or '—'}）{tail}"


def _prompt_brief(it: dict, cols: list[dict]) -> str:
    values = it.get("metric_values") or {}
    for col in cols:
        value = values.get(col.get("key"))
        if value is not None:
            return (f"{it['name']}（{col.get('label')} "
                    f"{_prompt_metric_value(col, value)}）")
    return str(it.get("name") or "")


def _page_summary_data(c, page: str, board: str | None = None) -> dict:
    """组装提示词数据 + 对账允许集合 + 纯数据模板（AI 不合格时降级用）。"""
    if page in ("home", "board"):
        key = "harness_core" if page == "home" else (board or "harness_core")
        top_n = 10 if page == "home" else 15
        scope = _board_scope(c, key)
        rows = scope["items"][:top_n]
        cols = _prompt_metric_columns(scope)
        allowed = _summary_allowed(scope, top_n, rows)
        lines = [_prompt_row(r, cols) for r in rows]
        data = (f"{_scope_line(scope)}；本次列出前 {len(rows)} 条"
                f"，本榜数据源 {len(scope['sources'])} 个：\n"
                + "\n".join(lines))
        shown = rows[:5]
        allowed.add(len(shown))
        template = (
            f"{scope['name']}（{scope['key']}）最新快照 {scope['snapshot']} "
            f"收录 {scope['total']} 个、入榜 {scope['ranked']} 条（不含其他板块）。"
            f"前 {len(shown)} 名："
            + "、".join(_prompt_brief(r, cols) for r in shown)
            + "。")
        return {"data": data, "allowed": allowed, "template": template,
                "allowed_dates": {scope["snapshot"]}}

    if page == "news":
        rows = c.execute(
            "SELECT title, source_name, category,"
            " COALESCE(published_at, fetched_at) AS published_at FROM news_items"
            " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 40").fetchall()
        if not rows:
            raise assistant.AssistantError("暂无新闻数据")
        total = c.execute("SELECT COUNT(*) n FROM news_items").fetchone()["n"]
        counts = c.execute("SELECT category, COUNT(*) n FROM news_items"
                           " GROUP BY category ORDER BY n DESC").fetchall()
        counts_n = [r["n"] for r in counts]
        lines = [f"- [{r['category']}] {r['title']}（{r['source_name']}）"
                 for r in rows]
        data = (f"新闻口径：最近 {len(rows)} 条（全库共 {total} 条；分类计数："
                + "、".join(f"{r['category']}{r['n']}" for r in counts) + "）：\n"
                + "\n".join(lines))
        allowed = _base_allowed() | {len(rows), total, len(counts)} | _pair_sums(counts_n)
        allowed_dates = {r["published_at"][:10] for r in rows if r["published_at"]}
        template = (f"本期新闻最近 {len(rows)} 条，全库共 {total} 条。分类分布："
                    + "、".join(f"{r['category']} {r['n']} 条" for r in counts)
                    + "。")
        return {"data": data, "allowed": allowed, "template": template,
                "allowed_dates": allowed_dates}

    if page == "knowledge":
        from .knowledge import load_notes
        meta, notes = load_notes()
        if not notes:
            raise assistant.AssistantError("暂无知识库快照")
        groups: dict[str, int] = {}
        for n in notes:
            groups[n["group_name"]] = groups.get(n["group_name"], 0) + 1
        group_n = list(groups.values())
        data = (f"知识库口径：共 {meta['count']} 篇文档，快照于 {meta['updated'][:10]}"
                f"（commit {meta['short_commit']}）；分组："
                + "、".join(f"{k} {v} 篇" for k, v in sorted(groups.items())))
        allowed = _base_allowed() | {meta["count"], len(groups)} | _pair_sums(group_n)
        template = (f"知识库共 {meta['count']} 篇文档，快照于 {meta['updated'][:10]}"
                    f"（commit {meta['short_commit']}）。分组："
                    + "、".join(f"{k} {v} 篇" for k, v in sorted(groups.items()))
                    + "。")
        return {"data": data, "allowed": allowed, "template": template,
                "allowed_dates": {meta["updated"][:10]}}

    raise assistant.AssistantError(f"未知摘要页: {page}")


def extract_summary_numbers(text: str) -> list[int]:
    """抽取摘要中可被对数校验的数字（N 个/条/名/篇/位/家/项/款/种、第 N）。"""
    nums = [int(m.group(1)) for m in _UNIT_NUMBER_RE.finditer(text or "")]
    nums += [int(m.group(1)) for m in _RANK_NUMBER_RE.finditer(text or "")]
    return nums


def find_unexplained_numbers(text: str, allowed: set[int]) -> list[int]:
    """返回摘要里无法用本板块数据解释的数字（去重、升序）。"""
    bad = {n for n in extract_summary_numbers(text) if n not in allowed}
    return sorted(bad)


def find_unexplained_dates(text: str, allowed_dates: set[str]) -> list[str]:
    """返回摘要中与允许日期（本板块快照/样本日期）不符的日期串。"""
    bad: set[str] = set()
    for m in _DATE_RE.finditer(text or ""):
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if date not in allowed_dates:
            bad.add(date)
    return sorted(bad)


def _page_summary_task(payload: dict) -> str:
    page = payload.get("page")
    with db.conn() as c:
        ctx = _page_summary_data(c, page, payload.get("board"))
    return (
        "以下是本站数据页面的当周概况。\n" + ctx["data"]
        + "\n\n请用中文写一段 150-200 字的页面摘要，面向初次访问的读者："
          "先说这个板块是什么，再概括当前的重点信息与位置；"
          "涉及数量时只能引用上文给出的板块口径数字，不要估算、不要累加其他板块；"
          "只输出摘要正文，不要标题、不要解释、不要 Markdown 语法。")


def _page_summary_store(payload: dict, reply: str) -> None:
    """落库前做数字对账：无法解释的数字一律降级为纯数据模板并记日志。

    119 事故的根因是提示词给了全站 products 计数，模型顺手把它说成
    "核心产品"。这里从生成侧之后再加一道闸：任何不在本板块允许集合里的
    "N 个/条/第 N" 都视为幻觉，宁可牺牲文采也不用不可溯源的数字。
    """
    page = str(payload.get("page", ""))
    signature = payload.get("signature")
    text = (reply or "").strip()
    try:
        with db.conn() as c:
            ctx = _page_summary_data(c, page, payload.get("board"))
        bad = find_unexplained_numbers(text, ctx["allowed"])
        bad_dates = find_unexplained_dates(text, ctx.get("allowed_dates") or set())
    except Exception as e:  # 对账自身失败不能拖垮 AI 步骤，保留模型原文
        log.warning("AI 摘要数字对账无法执行 page=%s: %s: %s；保留原文",
                    page, type(e).__name__, e)
    else:
        if bad or bad_dates:
            log.warning("AI 摘要数字对账失败 page=%s 未解释数字=%s 日期=%s；"
                        "降级模板摘要", page, bad, bad_dates)
            text = ctx["template"]
    with db.conn() as c:
        db.ensure_ai_tables(c)
        ai_mod.save_summary(c, page, text, str(signature) if signature else None)


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
             f"{r['category']}）公开采纳指标{_num(r['heat_score'])}"
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
