"""共享查询层：export（静态 API）与 sitegen（页面）用同一套查询，口径永远一致。"""
from __future__ import annotations

import json

from . import db
from .config import normalize_board_tags, registry
from .scoring import board_members, load_latest_metrics, weighted_coverage


def boards_order(include_hidden: bool = False) -> list[str]:
    """页面/门户可见榜单（registry 顺序）；hidden 源榜默认不列出。"""
    return [k for k, v in registry()["boards"].items()
            if v.get("enabled", True)
            and (include_hidden or not v.get("hidden"))]


def snapshot_dates(c) -> dict[str, str]:
    return {r["board"]: r["d"] for r in c.execute(
        "SELECT board, MAX(snapshot_date) AS d FROM rankings GROUP BY board")}


def all_products(c) -> list[dict]:
    out = []
    for r in c.execute("SELECT * FROM products"):
        p = dict(r)
        p["boards"] = normalize_board_tags(json.loads(p["board_tags"] or "[]"))
        p["repo_aliases_list"] = json.loads(p["repo_aliases"] or "[]")
        out.append(p)
    return out


def metric_details(pm: dict) -> dict:
    """{metric_key: {value, unit, source{key,name}, collected_at, raw_payload_id}}"""
    return {k: {
        "value": m["value"], "unit": m["unit"],
        "source": {"key": m["source_key"], "name": m["source_name"]},
        "collected_at": m["collected_at"], "raw_payload_id": m["raw_payload_id"],
        "censored_below": 200 if k == "appstore_rank" and m["value"] >= 201 else None,
    } for k, m in pm.items()}


def board_items(c, board: str, snap_date: str, latest: dict) -> list[dict]:
    rows = c.execute(
        "SELECT r.product_id, r.rank, r.heat_score, r.momentum_score, r.delta_7d,"
        " r.delta_30d, r.coverage, p.slug, p.name, p.vendor, p.category,"
        " p.tier, p.status, a.ai_score, a.rationale AS ai_rationale"
        " FROM rankings r JOIN products p ON p.id = r.product_id"
        " LEFT JOIN ai_scores a ON a.board = r.board"
        "  AND a.snapshot_date = r.snapshot_date AND a.product_id = r.product_id"
        " WHERE r.board=? AND r.snapshot_date=? ORDER BY r.rank",
        (board, snap_date)).fetchall()
    items = []
    for r in rows:
        origin = r["category"]
        items.append({
            "rank": r["rank"], "slug": r["slug"], "name": r["name"],
            "vendor": r["vendor"], "category": r["category"],
            "tier": r["tier"], "status": r["status"],
            "board": origin,
            "heat_score": r["heat_score"], "momentum_score": r["momentum_score"],
            "delta_7d": r["delta_7d"], "delta_30d": r["delta_30d"],
            "coverage": r["coverage"],
            "ai_score": r["ai_score"], "ai_rationale": r["ai_rationale"],
            "metrics": metric_details(latest.get(r["product_id"], {})),
            "product_id": r["product_id"],
        })
    return items


def observation_items(c, board: str, snap_date: str, latest: dict,
                      products: list[dict]) -> list[dict]:
    cfg = registry()["boards"].get(board, {})
    if cfg.get("derived"):
        # 派生榜观察区 = 各源榜观察区并集（只保留该派生榜要求的 tier）
        tier = cfg.get("tier")
        obs, seen = [], set()
        for bkey in cfg.get("derived_from") or []:
            for o in observation_items(c, bkey, snap_date, latest, products):
                if o["slug"] in seen or (tier and o.get("tier") != tier):
                    continue
                seen.add(o["slug"])
                obs.append(o)
        return sorted(obs, key=lambda x: -(x["coverage"] or 0))
    heat_cfg = cfg.get("heat", {})
    members = board_members(products, board)
    member_ids = {p["id"] for p in members}
    active = {k: w for k, w in heat_cfg.items()
              if any(latest.get(p["id"], {}).get(k, {}).get("value") is not None
                     for p in members)}
    ranked_ids = {r["product_id"] for r in c.execute(
        "SELECT product_id FROM rankings WHERE board=? AND snapshot_date=?",
        (board, snap_date))}
    obs = []
    for p in products:
        if p["id"] not in member_ids or p["id"] in ranked_ids:
            continue
        pm = latest.get(p["id"], {})
        obs.append({
            "product_id": p["id"],
            "slug": p["slug"], "name": p["name"], "vendor": p["vendor"],
            "tier": p.get("tier"),
            "coverage": weighted_coverage(heat_cfg, pm, active) if heat_cfg else None,
            "available": [k for k in heat_cfg if pm.get(k, {}).get("value") is not None],
        })
    return sorted(obs, key=lambda x: -(x["coverage"] or 0))


def history_series(c, board: str, days: int = 90) -> list[dict]:
    rows = c.execute(
        "SELECT r.snapshot_date, r.rank, r.heat_score, p.slug FROM rankings r"
        " JOIN products p ON p.id = r.product_id"
        " WHERE r.board=? AND r.snapshot_date >= date('now', ?)"
        " ORDER BY r.snapshot_date, r.rank", (board, f"-{days} day")).fetchall()
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r["snapshot_date"], []).append(
            {"slug": r["slug"], "rank": r["rank"], "heat_score": r["heat_score"]})
    return [{"date": d, "items": items} for d, items in sorted(series.items())]


def product_detail(c, slug: str) -> dict | None:
    row = c.execute("SELECT * FROM products WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    p = dict(row)
    p["boards"] = normalize_board_tags(json.loads(p["board_tags"] or "[]"))
    p["repo_aliases_list"] = json.loads(p["repo_aliases"] or "[]")
    # 采集期发现的仓库迁移别名（product_aliases 表）合并展示
    for a in c.execute("SELECT alias_value FROM product_aliases WHERE product_id=?"
                       " AND alias_type='repo'", (p["id"],)):
        if a["alias_value"] not in p["repo_aliases_list"]:
            p["repo_aliases_list"].append(a["alias_value"])
    integ = c.execute("SELECT * FROM integration_profile WHERE product_id=?",
                      (p["id"],)).fetchone()
    p["integration"] = dict(integ) if integ else None
    if p["integration"]:
        p["integration"]["protocol_list"] = json.loads(p["integration"].get("protocol") or "[]")
    series: dict[str, list] = {}
    for r in c.execute(
            "SELECT m.metric_key, m.value, m.unit, m.collected_at, m.raw_payload_id,"
            " s.key AS source_key, s.name AS source_name FROM metrics m"
            " JOIN sources s ON s.id = m.source_id WHERE m.product_id=?"
            " ORDER BY m.collected_at", (p["id"],)):
        series.setdefault(r["metric_key"], []).append({
            "date": r["collected_at"][:10], "value": r["value"], "unit": r["unit"],
            "source": {"key": r["source_key"], "name": r["source_name"]},
            "raw_payload_id": r["raw_payload_id"],
        })
    p["metrics_series"] = series
    p["ranks"] = [dict(r) for r in c.execute(
        "SELECT board, rank, heat_score, momentum_score, coverage, snapshot_date"
        " FROM rankings WHERE product_id=? ORDER BY snapshot_date DESC, board",
        (p["id"],))]
    p["ai_scores"] = [dict(r) for r in c.execute(
        "SELECT board, snapshot_date, ai_score, rationale FROM ai_scores"
        " WHERE product_id=? ORDER BY snapshot_date DESC, board",
        (p["id"],))]
    # 各榜最新一条 AI 分（产品页徽章用）
    latest_ai: dict[str, dict] = {}
    for row in p["ai_scores"]:
        latest_ai.setdefault(row["board"], row)
    p["latest_ai"] = latest_ai
    p["raw_ids"] = [r["id"] for r in c.execute(
        "SELECT DISTINCT id FROM raw_payloads WHERE product_id=? ORDER BY id DESC",
        (p["id"],))]
    return p


def raw_detail(c, rid: int) -> dict | None:
    r = c.execute(
        "SELECT rp.*, s.key AS source_key, s.name AS source_name FROM raw_payloads rp"
        " JOIN sources s ON s.id = rp.source_id WHERE rp.id=?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["evicted"] = bool(d["body_evicted_at"])
    d["body"] = None
    if not d["evicted"]:
        body = db.load_body(c, rid)
        if body is not None:
            d["body_size"] = len(body)
            try:
                d["body"] = json.loads(body)
            except Exception:
                d["body"] = body[:200_000].decode("utf-8", "replace")
    d["extracted"] = [dict(x) for x in c.execute(
        "SELECT m.metric_key, m.value, m.unit, p.slug FROM metrics m"
        " LEFT JOIN products p ON p.id = m.product_id WHERE m.raw_payload_id=?",
        (rid,))]
    return d


def sources_health(c) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT key, name, source_type, base_url, auth_required, rate_limit,"
        " enabled, reachable, last_success_at, failure_count, notes"
        " FROM sources ORDER BY enabled DESC, key")]


# ---- AI 新闻（export 与 sitegen 共用口径） ----

def _product_ids_of(row) -> list[int]:
    try:
        return json.loads(row["product_ids"] or "[]")
    except (TypeError, ValueError):
        return []


def _attach_products(c, rows: list) -> list[dict]:
    names = {r["id"]: dict(r) for r in c.execute(
        "SELECT id, slug, name, category FROM products")}
    out = []
    for r in rows:
        d = dict(r)
        d["products"] = [names[i] for i in _product_ids_of(r) if i in names]
        out.append(d)
    return out


def published_digest_summary(c, week: str) -> dict | None:
    """某 ISO 周的已发布 AI 周报总结（insights 表 llm_generated=1 且 status=published）。

    草稿态（draft）不展示——AI 产物需人工确认发布后才上线。
    """
    row = c.execute(
        "SELECT summary, created_at FROM insights WHERE llm_generated = 1"
        " AND status = 'published' AND tags LIKE '%news-digest%'"
        " AND published_at = ? ORDER BY id DESC LIMIT 1", (week,)).fetchone()
    return dict(row) if row else None


def news_meta(c) -> dict:
    row = c.execute("SELECT COUNT(*) n, MAX(fetched_at) t FROM news_items").fetchone()
    return {
        "total": row["n"] if row else 0,
        "last_fetched_at": row["t"] if row else None,
        "by_category": {r["category"]: r["n"] for r in c.execute(
            "SELECT category, COUNT(*) n FROM news_items GROUP BY category")},
        "by_source": {r["source_key"]: r["n"] for r in c.execute(
            "SELECT source_key, COUNT(*) n FROM news_items GROUP BY source_key")},
    }


def news_recent(c, limit: int = 60) -> list[dict]:
    rows = c.execute(
        "SELECT * FROM news_items"
        " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
        (limit,)).fetchall()
    return _attach_products(c, rows)


def _category_filter(categories: list[str] | None) -> tuple[str, list[str]]:
    cats = [str(x) for x in (categories or []) if x]
    if not cats:
        return "0", []
    return ",".join("?" * len(cats)), cats


def news_for_categories(c, categories: list[str] | None,
                        limit: int = 12) -> list[dict]:
    """方向频道新闻流：按 news_categories 过滤；空配置返回空列表（NV 空页）。"""
    clause, params = _category_filter(categories)
    if not params:
        return []
    rows = c.execute(
        "SELECT * FROM news_items WHERE category IN (" + clause + ")"
        " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
        (*params, limit)).fetchall()
    return _attach_products(c, rows)


def news_count_for_categories(c, categories: list[str] | None) -> int:
    clause, params = _category_filter(categories)
    if not params:
        return 0
    return c.execute(
        "SELECT COUNT(*) n FROM news_items WHERE category IN (" + clause + ")",
        params).fetchone()["n"]


def latest_direction_digest(c, direction_tag: str) -> dict | None:
    """方向每周总结：已发布的 AI 产物，优先当周，回退最近一期。"""
    from datetime import datetime, timezone
    from . import news as newslib
    tag = f"%direction:{direction_tag}%"
    row = c.execute(
        "SELECT summary, published_at, created_at FROM insights"
        " WHERE llm_generated = 1 AND status = 'published' AND tags LIKE ?"
        " ORDER BY published_at DESC, id DESC LIMIT 1", (tag,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["week"] = d.get("published_at")
    if d.get("week"):
        start, end = newslib.week_range(d["week"])
        d["week_start"], d["week_end"] = start, end
    current = newslib.week_key(datetime.now(timezone.utc).isoformat())
    d["current"] = d.get("week") == current
    return d


def news_by_product(c, limit_per_product: int = 8) -> dict[int, list[dict]]:
    """产品页「相关新闻」索引：{product_id: [item...]}，全量在内存分组。"""
    rows = c.execute("SELECT * FROM news_items"
                     " ORDER BY COALESCE(published_at, fetched_at) DESC").fetchall()
    index: dict[int, list] = {}
    for r in rows:
        for pid in _product_ids_of(r):
            index.setdefault(pid, []).append(dict(r))
    return {k: v[:limit_per_product] for k, v in index.items()}


def news_digests(c, weeks: int = 8) -> list[dict]:
    """按 ISO 周分组（published_at 缺失回退 fetched_at），最近 N 周。"""
    from . import news as newslib
    rows = c.execute("SELECT * FROM news_items").fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        key = newslib.week_key(r["published_at"] or r["fetched_at"])
        grouped.setdefault(key, []).append(dict(r))
    return [{"week": wk, "items": items}
            for wk, items in sorted(grouped.items(), reverse=True)[:weeks]]


def meta_info(c) -> dict:
    reg = registry()
    row = c.execute("SELECT MAX(collected_at) AS t FROM metrics").fetchone()
    last_collected = row["t"] if row else None
    run = c.execute("SELECT * FROM collect_runs ORDER BY id DESC LIMIT 1").fetchone()
    counts = {
        "products": c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"],
        "metrics_rows": c.execute("SELECT COUNT(*) n FROM metrics").fetchone()["n"],
        "raw_payloads": c.execute("SELECT COUNT(*) n FROM raw_payloads").fetchone()["n"],
        "news_items": c.execute("SELECT COUNT(*) n FROM news_items").fetchone()["n"],
    }
    return {
        "schema_version": "1.0",
        "methodology_version": reg["methodology"]["version"],
        "last_collected_at": last_collected,
        "last_run": dict(run) if run else None,
        "sources": sources_health(c),
        "retention": reg.get("retention", {}),
        "counts": counts,
        "update_mode": "manual",
    }
