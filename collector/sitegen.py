"""静态站点生成（Jinja2 模板 -> dist/）。

原则（规范第 9 章）：
- 图表构建期渲染为 SVG，无客户端图表库；
- 所有数字经来源标注组件渲染（数值 + 来源 + 采集时间 + /raw 链接）；
- 系统字体栈，无中文 webfont；单页体积远小于 200KB 预算。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader

from . import ai as ai_mod
from . import db, queries, svgchart, knowledge, news as newslib
from .config import (DIST_DIR, STATIC_DIR, TEMPLATES_DIR, ensure_dirs, now_iso,
                     registry, today_utc)
from .scoring import load_latest_metrics
from .compression import compress_site

METRIC_NAMES = {
    "github_stars": "GitHub Stars",
    "github_forks": "Forks",
    "github_open_issues": "Open Issues",
    "github_contributors": "贡献者",
    "github_commits_30d": "30天提交",
    "github_releases_30d": "30天发布",
    "github_stars_growth_30d": "Stars 增长",
    "npm_monthly_downloads": "npm 月下载",
    "npm_downloads_growth_30d": "npm 增长",
    "pypi_monthly_downloads": "PyPI 月下载",
    "pypi_downloads_growth_30d": "PyPI 增长",
    "appstore_rank": "App Store 榜",
    "appstore_rating_count": "App Store 评分数",
    "appstore_rating_growth_30d": "App Store 评分增长",
    "vscode_installs": "VS Code 安装",
}


def fmt_num(v):
    if v is None:
        return "—"
    if isinstance(v, float) and v != int(v):
        return f"{v:,.1f}"
    return f"{int(v):,}"


def fmt_pct(v):
    if v is None:
        return "—"
    return f"{v:+.1f}%"


def fmt_dt(s):
    if not s:
        return "—"
    return s.replace("T", " ")[:16] + " UTC"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters.update({"num": fmt_num, "pct": fmt_pct, "dt": fmt_dt,
                        "d10": lambda s: (s or "")[:10]})
    return env


def _top_metrics(board_key: str, reg: dict) -> list[str]:
    if board_key == "unified":
        return ["github_stars", "npm_monthly_downloads", "vscode_installs"]
    heat = reg["boards"].get(board_key, {}).get("heat", {})
    return [k for k, _ in sorted(heat.items(), key=lambda kv: -kv[1])[:3]]


def _is_stale(last_collected: str | None) -> bool:
    if not last_collected:
        return False
    try:
        t = datetime.fromisoformat(last_collected.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t > timedelta(hours=48)


def _assets_version() -> str:
    """静态资源内容指纹：内容变 -> URL 变，浏览器缓存永不发旧版。"""
    import hashlib
    h = hashlib.sha256()
    for f in sorted((STATIC_DIR / "assets").iterdir()):
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:8]


def build() -> None:
    ensure_dirs()
    reg = registry()
    env = _env()
    kb_meta, kb_notes = knowledge.load_notes()
    c = db.conn()
    try:
        latest = load_latest_metrics(c, today_utc())
        products = queries.all_products(c)
        snaps = queries.snapshot_dates(c)
        order = queries.boards_order()
        meta = queries.meta_info(c)
        bitems = {b: queries.board_items(c, b, snaps[b], latest)
                  for b in order if b in snaps}
        bobs = {b: queries.observation_items(c, b, snaps[b], latest, products)
                for b in order if b in snaps}

        common = {
            "site": reg["site"],
            "meta": meta,
            "board_names": {k: v["name"] for k, v in reg["boards"].items()},
            "metric_names": METRIC_NAMES,
            "nav_boards": [{"key": b, "name": reg["boards"][b]["name"]}
                           for b in order if b in snaps],
            "threshold_pct": int(reg["methodology"]["observation_threshold"] * 100),
            "stale": _is_stale(meta.get("last_collected_at")),
            "assets_v": _assets_version(),
            "kb": kb_meta,
            "news_categories": reg.get("news", {}).get("categories", {}),
            # AI 摘要（v1.4）：三板块顶部卡片；缺失时宏静默不渲染
            "ai_home": ai_mod.summary_of(c, "home"),
            "ai_news": ai_mod.summary_of(c, "news"),
            "ai_knowledge": ai_mod.summary_of(c, "knowledge"),
        }

        def render(tpl: str, out: str, **ctx) -> None:
            html = env.get_template(tpl).render(**common, **ctx)
            p = DIST_DIR / out
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html, encoding="utf-8")

        # ---- 首页（单页：联合榜单 + 编码/通用并排块）----
        def board_block(bkey: str) -> dict:
            return {"key": bkey, "name": reg["boards"][bkey]["name"],
                    "rows": bitems.get(bkey, []), "obs": len(bobs.get(bkey, []))}
        render("index.html", "index.html",
               unified=bitems.get("unified", []),
               unified_total=len(bitems.get("unified", [])),
               blocks=[board_block("coding"), board_block("general"),
                       board_block("domestic"), board_block("domestic_app")],
               obs_total=sum(len(v) for k, v in bobs.items() if k != "unified"))

        # ---- 榜单页 ----
        for b in order:
            if b not in snaps:
                continue
            items = bitems[b]
            mom = sorted((i for i in items if i["momentum_score"] is not None),
                         key=lambda x: -x["momentum_score"])
            hist = queries.history_series(c, b, 90)
            dates = [h["date"] for h in hist]
            by_slug = {h["date"]: {x["slug"]: x["heat_score"] for x in h["items"]}
                       for h in hist}
            series = {it["name"]: [by_slug.get(d, {}).get(it["slug"]) for d in dates]
                      for it in items[:5]}
            render("rankings.html", f"rankings/{b}.html",
                   board=b, name=reg["boards"][b]["name"],
                   snapshot_date=snaps[b], items=items, momentum_items=mom,
                   observation=bobs[b], top_metrics=_top_metrics(b, reg),
                   history_chart=svgchart.multi_line_chart(dates, series))

        # ---- 产品页 ----
        news_idx = queries.news_by_product(c, limit_per_product=8)
        for p in products:
            d = queries.product_detail(c, p["slug"])
            d["latest_ranks"] = {}
            for r in d["ranks"]:
                d["latest_ranks"].setdefault(r["board"], r)
            d["news"] = news_idx.get(p["id"], [])
            charts = {k: svgchart.line_chart([(pt["date"], pt["value"])
                                              for pt in pts])
                      for k, pts in d["metrics_series"].items()}
            render("product.html", f"product/{p['slug']}.html", p=d, charts=charts)

        # ---- 原始数据页 ----
        for r in c.execute("SELECT id FROM raw_payloads"):
            d = queries.raw_detail(c, r["id"])
            if isinstance(d["body"], (dict, list)):
                body_txt = json.dumps(d["body"], ensure_ascii=False, indent=2)
            else:
                body_txt = d["body"] or ""
            render("raw.html", f"raw/{r['id']}.html",
                   raw=d, body_txt=body_txt[:200_000])

        # ---- 静态说明页 ----
        render("methodology.html", "methodology.html",
               boards_cfg={k: v for k, v in reg["boards"].items()
                           if v.get("enabled", True) and not v.get("derived")},
               retention=reg.get("retention", {}))
        render("sources.html", "sources.html", sources=queries.sources_health(c))
        render("api.html", "api.html")
        knowledge.build(env, common, kb_notes)

        # ---- AI Agent 新闻（第三入口 /news + 每周 digest）----
        nmeta = queries.news_meta(c)
        ncfg = reg.get("news", {})
        # 产品当前名次（主榜口径），用于新闻条目上的产品徽章
        rank_of: dict[str, dict] = {}
        for b in order:
            for it in bitems.get(b, []):
                rank_of.setdefault(it["slug"], {
                    "board": it["board"], "rank": it["rank"], "name": it["name"]})
        render("news.html", "news.html", news_page=True, news_meta=nmeta,
               news_items=queries.news_recent(c, 60), news_rank=rank_of,
               news_stale=_is_stale(nmeta.get("last_fetched_at")),
               news_retention_days=reg.get("retention", {}).get("news_days", 0),
               news_lookback_days=ncfg.get("lookback_days", 7),
               digests=[{"week": d["week"], "count": len(d["items"]),
                         "start": newslib.week_range(d["week"])[0],
                         "end": newslib.week_range(d["week"])[1]}
                        for d in queries.news_digests(c, ncfg.get("digest_weeks", 8))])
        digest_root = DIST_DIR / "news" / "digest"
        digest_outputs = set()
        for d in queries.news_digests(c, ncfg.get("digest_weeks", 8)):
            start, end = newslib.week_range(d["week"])
            cat_counts: dict[str, int] = {}
            mentions: dict[int, int] = {}
            for item in d["items"]:
                cat_counts[item["category"]] = cat_counts.get(item["category"], 0) + 1
                for pid in json.loads(item["product_ids"] or "[]"):
                    mentions[pid] = mentions.get(pid, 0) + 1
            name_rows = c.execute(
                "SELECT id, slug, name FROM products WHERE id IN (%s)"
                % ",".join("?" * len(mentions)), list(mentions)).fetchall() if mentions else []
            top_list = sorted(
                ({"slug": r["slug"], "name": r["name"], "mentions": mentions[r["id"]]}
                 for r in name_rows),
                key=lambda x: -x["mentions"])[:10]
            render("news_digest.html", f"news/digest/{d['week']}.html",
                   news_page=True, news_meta=nmeta, week=d["week"],
                   week_start=start, week_end=end,
                   digest_items=queries._attach_products(c, d["items"]),
                   cat_counts=cat_counts, top_products=top_list,
                   ai_digest=queries.published_digest_summary(c, d["week"]),
                   news_rank=rank_of)
            digest_outputs.add(d["week"])
        # 周报随保留窗口滚动，过期文件清理（不触碰 dist 其他内容）
        if digest_root.is_dir():
            for old in digest_root.glob("*.html"):
                if old.stem not in digest_outputs:
                    old.unlink()
    finally:
        c.close()

    # ---- 静态资源 ----
    dst = DIST_DIR / "assets"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(STATIC_DIR / "assets", dst)
    compress_site(DIST_DIR)
