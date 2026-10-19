"""静态 API 导出（规范第 8 章契约；Phase 2 可换 FastAPI 同名路由，前端零改动）。

所有端点落在 dist/api/v1/ 下，同时榜单快照归档到 data/snapshots/{date}/。
"""
from __future__ import annotations

import json

from . import db, queries, scoring
from .config import (DIST_DIR, SNAPSHOT_DIR, date_days_ago, ensure_dirs,
                     now_iso, registry, today_utc)
from .scoring import load_latest_metrics


def _jwrite(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def export_all() -> None:
    ensure_dirs()
    reg = registry()
    version = reg["methodology"]["version"]
    generated = now_iso()
    api = DIST_DIR / "api" / "v1"
    c = db.conn()
    try:
        latest = load_latest_metrics(c, today_utc())
        products = queries.all_products(c)
        snaps = queries.snapshot_dates(c)
        order = queries.boards_order()
        hist_base = {base: scoring.load_history(c, base, date_days_ago(30))
                     for base in reg.get("derived_growth", {}).values()}

        # ---- boards.json ----
        boards_list = []
        for b in order:
            if b not in snaps:
                continue
            obs = queries.observation_items(c, b, snaps[b], latest, products)
            boards_list.append({
                "key": b, "name": reg["boards"][b]["name"],
                "hidden": bool(reg["boards"][b].get("hidden")),
                "product_count": len(queries.board_items(c, b, snaps[b], latest)),
                "observation_count": len(obs),
                "snapshot_date": snaps[b],
            })
        _jwrite(api / "boards.json", {
            "meta": {"schema_version": "1.0", "methodology_version": version,
                     "generated_at": generated},
            "boards": boards_list,
        })

        # ---- rankings/{board}.json + history ----
        payloads: dict[str, dict] = {}
        for b in order:
            if b not in snaps:
                continue
            items = queries.board_items(c, b, snaps[b], latest)
            obs = queries.observation_items(c, b, snaps[b], latest, products)
            is_derived = bool(reg["boards"][b].get("derived"))
            configured = {} if is_derived else reg["boards"][b]
            effective = {}
            if not is_derived:
                members = scoring.board_members(products, b)
                effective = scoring.compute_board(
                    b, configured, members, latest, hist_base, snaps[b],
                    reg["methodology"]["observation_threshold"])["weights"]
            payload = {
                "meta": {
                    "board": b, "name": reg["boards"][b]["name"],
                    "snapshot_date": snaps[b], "methodology_version": version,
                    "generated_at": generated,
                    "hidden": bool(reg["boards"][b].get("hidden")),
                    "weights": configured,
                    "effective_weights": effective,
                },
                "items": [{k: v for k, v in it.items() if k != "product_id"}
                          for it in items],
                "observation": obs,
            }
            payloads[b] = payload
            _jwrite(api / "rankings" / f"{b}.json", payload)
            _jwrite(api / "rankings" / b / "history.json", {
                "meta": {"board": b, "days": 90},
                "series": queries.history_series(c, b, 90),
            })
            _jwrite(SNAPSHOT_DIR / snaps[b] / f"{b}.json", payload)

        # 旧 API 契约兼容：unified 已并入 harness_core，保留只读别名。
        if "harness_core" in payloads:
            legacy = dict(payloads["harness_core"])
            legacy["meta"] = dict(legacy["meta"], board="unified",
                                  name="联合榜单（已并入核心产品榜）",
                                  legacy_of="harness_core")
            _jwrite(api / "rankings" / "unified.json", legacy)
            _jwrite(api / "rankings" / "unified" / "history.json", {
                "meta": {"board": "unified", "legacy_of": "harness_core", "days": 90},
                "series": queries.history_series(c, "harness_core", 90),
            })

        # ---- products/{slug}.json ----
        news_idx = queries.news_by_product(c, limit_per_product=5)
        for p in products:
            d = queries.product_detail(c, p["slug"])
            _jwrite(api / "products" / f"{p['slug']}.json", {
                "meta": {"slug": p["slug"], "generated_at": generated},
                "product": {k: d[k] for k in (
                    "name", "vendor", "category", "boards", "repo", "repo_aliases_list",
                    "license", "npm_package", "pypi_package", "appstore_id",
                    "vscode_ext_id", "status", "notes")},
                "integration": d["integration"],
                "ranks": d["ranks"],
                "metrics_series": d["metrics_series"],
                "news": [{k: n[k] for k in ("title", "url", "category", "region",
                                            "source_name", "published_at", "points")}
                         for n in news_idx.get(p["id"], [])],
            })

        # ---- raw/{id}.json ----
        for r in c.execute("SELECT id FROM raw_payloads"):
            d = queries.raw_detail(c, r["id"])
            _jwrite(api / "raw" / f"{r['id']}.json", {
                "meta": {"payload_id": d["id"],
                         "source": {"key": d["source_key"], "name": d["source_name"]},
                         "request_url": d["request_url"], "http_status": d["http_status"],
                         "fetched_at": d["fetched_at"], "body_sha256": d["body_sha256"],
                         "parser_version": d["parser_version"],
                         "evicted": d["evicted"],
                         "body_evicted_at": d.get("body_evicted_at")},
                "raw_body": d["body"],
                "extracted": d["extracted"],
            })

        # ---- insights.json / news.json / meta.json ----
        insights = [dict(r) for r in c.execute(
            "SELECT * FROM insights WHERE status='published' ORDER BY published_at DESC")]
        _jwrite(api / "insights.json", {
            "meta": {"generated_at": generated}, "items": insights})
        nmeta = queries.news_meta(c)
        nitems = queries.news_recent(c, 200)
        _jwrite(api / "news.json", {
            "meta": {"schema_version": "1.0", "generated_at": generated,
                     "total": nmeta["total"], "returned": len(nitems),
                     "last_fetched_at": nmeta["last_fetched_at"],
                     "by_category": nmeta["by_category"],
                     "retention_days": reg.get("retention", {}).get("news_days", 0),
                     "categories": reg.get("news", {}).get("categories", {})},
            "items": [{k: it[k] for k in (
                "id", "title", "url", "summary", "source_key", "source_name",
                "category", "region", "author", "published_at", "points",
                "products", "raw_payload_id")} for it in nitems],
        })
        _jwrite(DIST_DIR / "api" / "v1" / "meta.json", queries.meta_info(c))
    finally:
        c.close()
