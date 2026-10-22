"""站内搜索索引（轻量 JSON，供 /assets/search.js 懒加载）。

索引只含公开内容：产品、榜单、方向页/静态页、新闻。私有区（报告厅/知识库/
剪贴板/讲稿/原始数据）永不进入索引；不写生产库，只执行 SELECT。

对外函数：
    build_index(conn, out_path, *, max_news=450) -> dict
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path

INDEX_VERSION = 1
# 450 条新闻足以覆盖最近数周；实测生产库 gzip 约 46KB，留足 <60KB 余量。
MAX_NEWS = 450
NEWS_SUMMARY_CHARS = 56
TAGLINE_CHARS = 90

# 静态公开页面（私有区一律不列）
STATIC_PAGES = (
    ("首页", "/", "智能体、模型、硬件三方向榜单与 AI 新闻；每个数字可溯源。"),
    ("数据源与采集方法", "/sources", "数据源清单、采集频率、授权与原始响应保留策略。"),
    ("开放 API", "/api", "榜单、新闻与数据源元信息的只读 JSON API。"),
    ("登录 / 工作台", "/login", "登录后使用报告厅、个人知识库与云剪贴板。"),
)


def _clean(text, limit: int = 0) -> str:
    if text is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip("，,。.;；、 ") + "…"
    return text


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _registry() -> dict:
    try:
        from .config import registry
        return registry() or {}
    except Exception:
        return {}


def _rows(conn, sql: str, params=()) -> list[dict]:
    """执行 SELECT 并统一成 dict 行（兼容 tuple / sqlite3.Row 两种 row_factory）。"""
    cur = conn.execute(sql, params)
    columns = [d[0] for d in (cur.description or [])]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _table_columns(conn, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _public_boards(reg: dict) -> dict[str, dict]:
    out = {}
    for key, cfg in (reg.get("boards") or {}).items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled", True) and not cfg.get("hidden"):
            out[key] = cfg
    return out


def _direction_of_category(reg: dict) -> dict[str, tuple[str, str]]:
    """news category -> (direction key, direction display name)。"""
    mapping: dict[str, tuple[str, str]] = {}
    for key, d in (reg.get("directions") or {}).items():
        if not isinstance(d, dict):
            continue
        for cat in d.get("news_categories") or []:
            mapping.setdefault(str(cat), (key, _clean(d.get("name") or key, 20)))
    return mapping


def _category_label(reg: dict, category: str) -> str:
    labels = ((reg.get("news") or {}).get("categories") or {})
    return _clean(labels.get(category) or category or "资讯", 30)


def _latest_rankings(conn, public_boards: dict) -> dict[int, dict]:
    """每个产品取最新公开榜名次；同一天优先 registry 顺序靠前的主榜。

    无公开名次时回退任意榜单（保证观察区/归档产品也有可点击入口）。
    """
    priority = {board: i for i, board in enumerate(public_boards)}
    rws = _rows(conn, "SELECT product_id, board, snapshot_date, rank, heat_score"
                      " FROM rankings")
    # 最新快照优先；同一天按主榜顺序（harness_core → 生态/模型/硬件 → 专题榜）；
    # 同一榜单内按名次靠前。
    rws.sort(key=lambda r: ((r.get("snapshot_date") or ""),
                            -priority.get(r.get("board"), 999),
                            -(r.get("rank") or 10 ** 6)), reverse=True)
    public_best: dict[int, dict] = {}
    fallback: dict[int, dict] = {}
    for row in rws:
        pid = row["product_id"]
        item = {k: row[k] for k in ("board", "snapshot_date", "rank", "heat_score")}
        fallback.setdefault(pid, item)
        if item["board"] in public_boards:
            public_best.setdefault(pid, item)
    return {pid: public_best.get(pid, fallback[pid]) for pid in fallback}


def _product_items(conn, reg: dict, public_boards: dict) -> list[dict]:
    columns = _table_columns(conn, "products")
    if not columns:
        return []
    wanted = ["id", "slug", "name", "vendor", "category", "tier", "origin",
              "status", "notes", "board_tags"]
    select = [name for name in wanted if name in columns]
    if "id" not in select or "slug" not in select or "name" not in select:
        return []
    rows = _rows(conn, "SELECT " + ", ".join(select) + " FROM products ORDER BY name")

    ranks = _latest_rankings(conn, public_boards)
    board_names = {k: _clean((v or {}).get("name") or k, 40)
                   for k, v in (reg.get("boards") or {}).items()}
    category_labels = ((reg.get("news") or {}).get("categories") or {})

    items = []
    for row in rows:
        slug = row.get("slug")
        name = _clean(row.get("name"), 80)
        if not slug or not name:
            continue
        rank = ranks.get(row["id"]) or {}
        board = rank.get("board") or ""
        if not board:
            try:
                tags = json.loads(row.get("board_tags") or "[]")
            except (TypeError, ValueError):
                tags = []
            aliases = reg.get("board_aliases") or {}
            tags = [aliases.get(t, t) for t in tags]
            board = next((t for t in tags if t in public_boards), tags[0] if tags else "")
        board_name = board_names.get(board, _clean(board, 30))
        category = _clean(row.get("category"), 30)
        category_label = _clean(category_labels.get(category) or category or "", 30)
        vendor = _clean(row.get("vendor"), 40)
        notes = _clean(row.get("notes"), TAGLINE_CHARS)
        tagline = notes or " · ".join(x for x in (vendor, category_label) if x) or "产品"
        heat = rank.get("heat_score")
        if heat is not None:
            heat = round(float(heat), 1)
        item = {
            "type": "product",
            "title": name,
            "url": f"/product/{slug}",
            "subtitle": tagline,
            "slug": slug,
            "name": name,
            "tagline": tagline,
            "board": board,
            "board_name": board_name,
            "heat": heat,
            "keywords": " ".join(x for x in (name, vendor, category_label, board_name) if x),
        }
        status = row.get("status")
        if status and status != "active":
            item["status"] = status
        tier = row.get("tier")
        if tier:
            item["tier"] = tier
        items.append(item)
    return items


def _ranking_items(conn, reg: dict, public_boards: dict) -> list[dict]:
    if not public_boards:
        return []
    dates = {r["board"]: r["d"] for r in _rows(
        conn, "SELECT board, MAX(snapshot_date) AS d FROM rankings GROUP BY board")}
    items = []
    for board, cfg in public_boards.items():
        date = dates.get(board)
        if not date:
            continue
        name = _clean((cfg or {}).get("name") or board, 50)
        top = [r["name"] for r in _rows(
            conn,
            "SELECT p.name FROM rankings r JOIN products p ON p.id = r.product_id"
            " WHERE r.board=? AND r.snapshot_date=? ORDER BY r.rank LIMIT 3",
            (board, date))]
        subtitle = f"{date} 快照" + (f" · 前三：{'、'.join(_clean(n, 30) for n in top)}" if top else "")
        items.append({
            "type": "ranking",
            "title": name,
            "url": f"/rankings/{board}",
            "subtitle": subtitle,
            "board": board,
            "board_name": name,
            "snapshot_date": date,
            "keywords": f"{name} {board} 榜单 排名",
        })
    return items


def _news_items(conn, reg: dict, max_news: int) -> list[dict]:
    columns = _table_columns(conn, "news_items")
    if not columns:
        return []
    wanted = ["title", "url", "summary", "source_name", "category",
              "published_at", "fetched_at"]
    select = [name for name in wanted if name in columns]
    if "title" not in select:
        return []
    order_cols = [c for c in ("published_at", "fetched_at") if c in columns]
    order = f" ORDER BY COALESCE({', '.join(order_cols)}) DESC" if order_cols else ""
    rows = _rows(conn, "SELECT " + ", ".join(select) + " FROM news_items" + order
                 + " LIMIT ?", (int(max_news),))

    direction_map = _direction_of_category(reg)
    items = []
    for row in rows:
        title = _clean(row.get("title"), 140)
        if not title:
            continue
        category = row.get("category") or ""
        tag, direction = direction_map.get(str(category), ("", ""))
        if not direction:
            direction = _category_label(reg, str(category))
        if not tag:
            tag = _clean(category or "news", 30)
        summary = _clean(row.get("summary"), NEWS_SUMMARY_CHARS)
        date = _clean((row.get("published_at") or row.get("fetched_at") or ""), 10)
        url = _clean(row.get("url"), 500)
        source = _clean(row.get("source_name"), 50)
        items.append({
            "type": "news",
            "title": title,
            "url": url or "/",
            "subtitle": summary or source,
            "date": date,
            "category": _category_label(reg, str(category)),
            "tag": tag,
            "direction": direction,
            "source": source,
            "external": bool(re.match(r"^https?://", url or "")),
        })
    return items


def _page_items(reg: dict) -> list[dict]:
    items = []
    for title, url, subtitle in STATIC_PAGES:
        items.append({
            "type": "page", "title": title, "url": url,
            "subtitle": subtitle, "keywords": f"{title} {subtitle}",
        })
    for key, d in (reg.get("directions") or {}).items():
        if not isinstance(d, dict):
            continue
        name = _clean(d.get("name") or key, 30)
        desc = _clean(d.get("description") or d.get("tagline") or "", 100)
        boards = d.get("boards") or []
        board_names = "、".join(
            _clean(((reg.get("boards") or {}).get(b) or {}).get("name") or b, 30)
            for b in boards)
        subtitle = desc or f"{name}方向频道：新闻、榜单与每周总结。"
        if board_names:
            subtitle = f"{subtitle.rstrip('。')}。含 {board_names}"
        items.append({
            "type": "page",
            "title": f"{name}频道",
            "url": f"/dir/{key}",
            "subtitle": _clean(subtitle, 130),
            "keywords": f"{name} {name}频道 {d.get('short') or ''} {board_names} 方向 周报",
        })
    return items


def build_index(conn, out_path, *, max_news: int = MAX_NEWS) -> dict:
    """从数据库生成站内搜索索引 JSON。

    Args:
        conn: sqlite3 连接（可只读打开；本函数只执行 SELECT，不写库）。
        out_path: 输出路径（通常 ``dist/assets/search-index.json``）。
        max_news: 最多收录的新闻条数（按发布时间倒序），默认 450。

    Returns:
        dict: ``path``、``generated_at``、``counts``、``json_bytes``、``gzip_bytes``。
    """
    reg = _registry()
    public_boards = _public_boards(reg)
    products = _product_items(conn, reg, public_boards)
    rankings = _ranking_items(conn, reg, public_boards)
    pages = _page_items(reg)
    news = _news_items(conn, reg, max_news=max(0, int(max_news)))

    generated_at = _iso_now()
    counts = {
        "product": len(products),
        "ranking": len(rankings),
        "page": len(pages),
        "news": len(news),
    }
    payload = {
        "generated_at": generated_at,
        "version": INDEX_VERSION,
        "counts": counts,
        "items": products + rankings + pages + news,
    }
    raw = json.dumps(payload, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换，避免请求恰好读到半截索引。
    staging = target.with_name(target.name + ".tmp")
    staging.write_bytes(raw)
    staging.replace(target)
    return {
        "path": str(target),
        "generated_at": generated_at,
        "counts": counts,
        "json_bytes": len(raw),
        "gzip_bytes": len(gzip.compress(raw, compresslevel=6, mtime=0)),
    }
