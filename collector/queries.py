"""共享查询层：export（静态 API）与 sitegen（页面）用同一套查询，口径永远一致。"""
from __future__ import annotations

import json

from . import db
from .config import normalize_board_tags, now_iso, registry
from .scoring import board_members, load_latest_metrics, weighted_coverage

# 硬件天梯（`/rankings/nv_infer` 页面与 `/api/v1/hardware/ladder.json` 共用）：
# 成员 = board_tags 命中该榜且 active 的产品；按 FP16 稠密算力降序，缺值最后。
# 页面与 API 都只调用 `hardware_ladder()`，不得再复制一份成员/排序逻辑。
LADDER_BOARD = "nv_infer"
LADDER_NUMERIC_FIELDS = (
    "fp16_tflops", "fp8_tflops", "int8_tops", "bf16_tflops",
    "hbm_gb", "hbm_bw_gbs", "interconnect_bw_gbs", "tdp_w",
)
# 规格口径 API 的固定字段与顺序（export 只做投影，不再重排/重算）。
LADDER_API_FIELDS = (
    "rank", "slug", "name", "vendor",
    "fp16_tflops", "fp8_tflops", "int8_tops", "bf16_tflops",
    "lowp_label", "hbm_gb", "hbm_bw_gbs", "interconnect",
    "interconnect_bw_gbs", "tdp_w", "official_docs", "product_url",
)


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


def _fmt_count(value) -> str:
    """派生指标 title 里的分项数值：整数加千分位，小数保留 1 位。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return f"{int(f):,}"
    return f"{f:,.1f}"


def derived_metrics(metrics: dict) -> dict:
    """按 registry.derived_metrics 计算展示层派生指标（不写库）。

    只对可用分项求和；全部缺失时返回空 dict（模板显示 —）。
    `downloads` = npm 月下载 + VS Code 安装 + App Store 评分人数。
    """
    out: dict[str, dict] = {}
    for key, cfg in (registry().get("derived_metrics") or {}).items():
        if not isinstance(cfg, dict):
            continue
        labels = cfg.get("component_labels") or {}
        parts: list[dict] = []
        for component in cfg.get("components") or []:
            meta = metrics.get(component) or {}
            value = meta.get("value")
            if value is None:
                continue
            parts.append({
                "key": component,
                "label": labels.get(component, component),
                "value": value,
            })
        if not parts:
            continue
        total = sum(float(p["value"]) for p in parts)
        if total.is_integer():
            total = int(total)
        formula = " + ".join(
            f"{p['label']} {_fmt_count(p['value'])}" for p in parts)
        out[key] = {
            "value": total,
            "unit": cfg.get("unit") or "count",
            "title": f"{cfg.get('label') or key} = {formula}",
            "components": parts,
        }
    return out


def board_items(c, board: str, snap_date: str, latest: dict) -> list[dict]:
    rows = c.execute(
        "SELECT r.product_id, r.rank, r.heat_score, r.momentum_score, r.delta_7d,"
        " r.delta_30d, r.coverage, p.slug, p.name, p.vendor, p.category,"
        " p.tier, p.origin, p.aa_slug, p.status,"
        " a.ai_score, a.rationale AS ai_rationale"
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
            "tier": r["tier"], "origin": r["origin"],
            "aa_slug": r["aa_slug"], "status": r["status"],
            "board": origin,
            "heat_score": r["heat_score"], "momentum_score": r["momentum_score"],
            "delta_7d": r["delta_7d"], "delta_30d": r["delta_30d"],
            "coverage": r["coverage"],
            "ai_score": r["ai_score"], "ai_rationale": r["ai_rationale"],
            "metrics": metric_details(latest.get(r["product_id"], {})),
            "product_id": r["product_id"],
        })
    return items


def _display_members(products: list[dict], board: str,
                     ranked_ids: set[int] | None = None) -> list[dict]:
    """榜单展示成员：包含尚无快照行的在榜产品（不再用观察区收纳）。"""
    cfg = registry()["boards"].get(board, {})
    ranked_ids = ranked_ids or set()
    if cfg.get("derived"):
        cats = set(cfg.get("derived_from") or [])
        tier = cfg.get("tier")
        out = []
        for p in products:
            if p.get("status") != "active" or p.get("category") not in cats:
                continue
            if tier and p.get("tier") != tier:
                continue
            # 派生榜成员：源分类标签命中，或已出现在该派生榜快照中。
            if (set(p.get("boards") or []) & cats) or p.get("id") in ranked_ids:
                out.append(p)
        return out
    select = cfg.get("select") or {}
    if select:
        return [p for p in products
                if p.get("status") == "active"
                and all(p.get(k) == v for k, v in select.items())]
    return [p for p in products
            if p.get("status") == "active" and board in p.get("boards", [])]


def board_sort_keys(board: str) -> list[dict]:
    cfg = registry()["boards"].get(board, {})
    return list((cfg.get("sort") or {}).get("keys") or [])


def _sort_key(item: dict, keys: list[dict]) -> tuple:
    """缺失值排最后；direction=asc 用于商店排名这类“越小越好”的指标。"""
    parts: list[tuple] = []
    values = item.get("metric_values") or {}
    for spec in keys:
        value = values.get(spec.get("metric"))
        if value is None:
            parts.append((1, 0.0))
        elif spec.get("direction") == "asc":
            parts.append((0, float(value)))
        else:
            parts.append((0, -float(value)))
    parts.append((0, str(item.get("name") or "")))
    return tuple(parts)


def sort_items_for_board(items: list[dict], board: str) -> list[dict]:
    """按 registry.boards.<key>.sort.keys 排序（数据层完成，模板不做排序）。"""
    keys = board_sort_keys(board)
    if not keys:
        return sorted(items, key=lambda it: str(it.get("name") or ""))
    return sorted(items, key=lambda it: _sort_key(it, keys))


def _column_cell(col: dict, product: dict, metrics: dict, spec: dict | None) -> dict | None:
    typ = col.get("type")
    if typ == "product":
        return {"slug": product.get("slug"), "name": product.get("name"),
                "vendor": product.get("vendor"), "status": product.get("status")}
    if typ == "vendor":
        return {"value": product.get("vendor") or None}
    if typ == "tier":
        value = product.get("tier")
        labels = col.get("labels") or {}
        return ({"value": value, "label": labels.get(value, value)}
                if value else None)
    if typ == "metric":
        return metrics.get(col.get("key"))
    if typ == "derived":
        return metrics.get(col.get("key"))
    if typ == "precision":
        if not spec:
            return None
        parts = []
        for part in col.get("parts") or []:
            value = spec.get(part.get("key"))
            if value is None:
                continue
            parts.append({"key": part.get("key"),
                          "label": part.get("label") or part.get("key"),
                          "unit": part.get("unit") or "",
                          "value": value})
        return {"parts": parts} if parts else None
    if typ == "hw":
        if not spec:
            return None
        value = spec.get(col.get("key"))
        return {"value": value} if value is not None else None
    if typ == "doc":
        docs = (spec or {}).get("official_docs") or []
        return dict(docs[0]) if docs else None
    if typ == "text":
        value = (spec or {}).get(col.get("key"))
        return {"value": value} if value not in (None, "") else None
    return None


def _is_spec_board(cfg: dict) -> bool:
    """榜单是否以规格列为主口径（硬件天梯）：由 registry 列类型推导，不硬编码榜名。"""
    return any(col.get("type") == "hw" for col in (cfg.get("columns") or []))


def _hw_spec_for(product: dict) -> dict | None:
    """产品 → hw_specs.json 条目；规格表损坏时降级 None（页面照常出榜）。"""
    try:
        from . import hw as hwlib
        return hwlib.by_model(str(product.get("name") or ""))
    except Exception:  # pragma: no cover - 规格文件损坏时仍应能出榜
        return None


def _ladder_num(value):
    """规格数值：只有数字才返回，None/空串一律 None，不用 0 占位。"""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _num_text(value) -> str:
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _ladder_row(product: dict, spec: dict | None) -> dict:
    """单张加速卡的天梯行（页面与 API 共用的唯一行结构）。"""
    spec = spec or {}
    values = {key: _ladder_num(spec.get(key)) for key in LADDER_NUMERIC_FIELDS}
    interconnect = spec.get("interconnect")
    if not isinstance(interconnect, str) or not interconnect.strip():
        interconnect = None
    lowp = []
    if values["fp8_tflops"] is not None:
        lowp.append(f"FP8 {_num_text(values['fp8_tflops'])}")
    if values["int8_tops"] is not None:
        lowp.append(f"INT8 {_num_text(values['int8_tops'])} TOPS")
    if values["bf16_tflops"] is not None:
        lowp.append(f"BF16 {_num_text(values['bf16_tflops'])}")
    docs = [{"title": str(d.get("title") or ""), "url": d.get("url")}
            for d in (spec.get("official_docs") or [])
            if isinstance(d, dict) and d.get("url")]
    return {
        "rank": None,
        "slug": product.get("slug"),
        "name": product.get("name"),
        "vendor": product.get("vendor"),
        **values,
        "lowp_label": " · ".join(lowp) or None,
        "interconnect": interconnect,
        "official_docs": docs,
        "product_url": f"/product/{product.get('slug')}",
        "hw_spec": spec,
    }


def hardware_ladder(c, board: str = LADDER_BOARD,
                    products: list[dict] | None = None) -> dict:
    """硬件天梯行（`/rankings/nv_infer` 页面与规格 API 共用的唯一数据源）。

    成员：`board_tags` 含 board 且 active 的产品；排序：fp16_tflops 降序，
    缺值最后，并列按名称升序（与旧页面口径一致）。返回 {meta, items}；
    items 保留完整 `hw_spec` 供页面渲染，API 导出按 LADDER_API_FIELDS 投影。
    """
    from . import hw as hwlib

    cfg = registry()["boards"].get(board, {})
    products = all_products(c) if products is None else products
    rows = [_ladder_row(p, _hw_spec_for(p))
            for p in _display_members(products, board)]
    rows.sort(key=lambda row: (row["fp16_tflops"] is None,
                               -(row["fp16_tflops"] or 0.0),
                               str(row.get("name") or "")))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return {
        "meta": {
            "generated_at": now_iso(),
            "criteria": cfg.get("criteria") or "",
            "total": len(rows),
            "specs_updated_at": hwlib.updated_at(),
            "source": "collector/hw_specs.json + MLPerf 成员",
        },
        "items": rows,
    }


def display_board_items(c, board: str, products: list[dict], latest: dict) -> dict:
    """榜单页数据层：配置化列 + 数据层排序 + 稳定名次。

    硬件天梯（含 hw 列）的成员与顺序直接取 `hardware_ladder()`，与规格 API
    同源；其余榜单仍走 registry.sort.keys。返回
    {rows, columns, sort_note, criteria, subtitle, snapshot_date?}。
    """
    cfg = registry()["boards"].get(board, {})
    columns = [dict(c) for c in (cfg.get("columns") or [])]
    ladder = None
    if _is_spec_board(cfg):
        try:
            ladder = hardware_ladder(c, board, products=products)
        except Exception:  # pragma: no cover - 规格表异常时退回通用渲染
            ladder = None
    ladder_rank: dict[str, int] = {}
    ladder_spec: dict[str, dict | None] = {}
    if ladder is not None:
        by_slug = {p["slug"]: p for p in products}
        members = [by_slug[it["slug"]] for it in ladder["items"]
                   if it["slug"] in by_slug]
        ladder_rank = {it["slug"]: it["rank"] for it in ladder["items"]}
        ladder_spec = {it["slug"]: it.get("hw_spec") for it in ladder["items"]}
    else:
        ranked_ids = {r[0] for r in c.execute(
            "SELECT DISTINCT product_id FROM rankings WHERE board=?", (board,))}
        members = _display_members(products, board, ranked_ids)
    rows: list[dict] = []
    for product in members:
        pm = latest.get(product["id"], {})
        metrics = metric_details(pm)
        metrics.update(derived_metrics(metrics))
        spec = (ladder_spec.get(product["slug"]) if ladder is not None
                else _hw_spec_for(product))
        metric_values = {k: v.get("value") for k, v in metrics.items()}
        # 硬件列的值来自规格表：并入排序值，才能按 FP16 等规格列排序。
        for col in columns:
            if col.get("type") != "hw":
                continue
            value = (spec or {}).get(col.get("key"))
            if value is not None:
                metric_values.setdefault(col.get("key"), value)
        item = {
            "slug": product.get("slug"), "name": product.get("name"),
            "vendor": product.get("vendor"), "tier": product.get("tier"),
            "category": product.get("category"), "status": product.get("status"),
            "repo": product.get("repo"),
            "metrics": metrics,
            "metric_values": metric_values,
            "hw_spec": spec,
        }
        item["cells"] = {col.get("key"): _column_cell(col, product, metrics, spec)
                         for col in columns}
        rows.append(item)
    if ladder is None:
        rows = sort_items_for_board(rows, board)
    else:
        rows.sort(key=lambda it: ladder_rank[it["slug"]])
    for i, item in enumerate(rows, 1):
        item["rank"] = ladder_rank.get(item["slug"], i)
        if "rank" in item["cells"]:
            item["cells"]["rank"] = item["rank"]
    sort_keys = board_sort_keys(board)
    primary_key = sort_keys[0].get("metric") if sort_keys else None
    primary_col = next((c for c in columns if c.get("key") == primary_key), None)
    for item in rows:
        item["primary_key"] = primary_key
        item["primary_label"] = (cfg.get("primary_label")
                                 or (primary_col or {}).get("label") or "")
        primary = item["metrics"].get(primary_key)
        if primary is None and primary_key and item.get("hw_spec"):
            value = item["hw_spec"].get(primary_key)
            if value is not None:
                primary = {"value": value}
        item["primary"] = primary
    return {
        "key": board,
        "rows": rows,
        "columns": columns,
        "sort_note": (cfg.get("sort") or {}).get("note") or "",
        "criteria": cfg.get("criteria") or "",
        "subtitle": cfg.get("subtitle") or "",
    }


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


def published_digest_summary(c, week: str, direction: str | None = None) -> dict | None:
    """某 ISO 周的已发布 AI 周报总结（insights，llm_generated=1/status=published）。

    全站周报排除带 direction 标记的行；方向周报只看对应 direction:<tag>。
    草稿态（draft）不展示。
    """
    if direction:
        row = c.execute(
            "SELECT summary, created_at FROM insights WHERE llm_generated = 1"
            " AND status = 'published' AND tags LIKE '%news-digest%'"
            " AND tags LIKE ? AND published_at = ? ORDER BY id DESC LIMIT 1",
            (f"%direction:{direction}%", week)).fetchone()
    else:
        row = c.execute(
            "SELECT summary, created_at FROM insights WHERE llm_generated = 1"
            " AND status = 'published' AND tags LIKE '%news-digest%'"
            " AND tags NOT LIKE '%direction:%' AND published_at = ?"
            " ORDER BY id DESC LIMIT 1", (week,)).fetchone()
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


def news_digests(c, weeks: int = 8,
                 categories: list[str] | None = None) -> list[dict]:
    """按 ISO 周分组（published_at 缺失回退 fetched_at），最近 N 周。

    categories 非空时只统计该方向的新闻范围（周报管线按 news_tag 参数化）。
    """
    from . import news as newslib
    clause, params = _category_filter(categories)
    sql = "SELECT * FROM news_items"
    if params:
        sql += " WHERE category IN (" + clause + ")"
    rows = c.execute(sql, params).fetchall()
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
