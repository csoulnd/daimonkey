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
from . import reports as reportslib
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
    "sc_total": "SuperCLUE 总分",
    "sc_math": "SuperCLUE 数学",
    "sc_science": "SuperCLUE 科学",
    "sc_code": "SuperCLUE 代码",
    "sc_agent": "SuperCLUE 智能体",
    "sc_instruction": "SuperCLUE 指令",
    "sc_hallucination": "SuperCLUE 幻觉",
    "aa_intelligence": "AA 智能指数",
    "aa_coding": "AA 编码指数",
    "aa_agentic": "AA 智能体指数",
    "aa_finance": "AA 金融指数",
    "aa_strategy": "AA 策略运营指数",
    "aa_legal": "AA 法律指数",
    "aa_healthcare": "AA 医疗指数",
    "aa_engineering": "AA 工程指数",
    "aa_economics": "AA 经济指数",
    # MLPerf：单卡吞吐（Tokens/s per accelerator）
    "mlperf_gptoss_120b": "gpt-oss-120b 单卡 Tokens/s",
    "mlperf_deepseek_r1": "DeepSeek-R1 单卡 Tokens/s",
    "mlperf_llama3_8b": "Llama3.1-8B 单卡 Tokens/s",
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
    configured = reg["boards"].get(board_key, {}).get("display_metrics")
    if configured:
        return list(configured)
    if board_key == "harness_core":
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


def _core_display(items: list[dict], ai_weight: float) -> list[dict]:
    """核心榜展示层变换(原联合榜 v1.5):剔除停滞 → 算法/AI 加权 → 重排名 → 前 10。

    只影响页面呈现;export 的 /api/v1/rankings/*.json 契约冻结,仍为算法分全量。
    """
    rows = [dict(it) for it in items if it.get("status") != "stale"]
    for it in rows:
        base, ai_s = it.get("heat_score"), it.get("ai_score")
        it["final_score"] = (round(base * (1 - ai_weight) + ai_s * ai_weight, 1)
                             if ai_weight > 0 and base is not None and ai_s is not None
                             else base)
    rows.sort(key=lambda x: (x["final_score"] is None, -(x["final_score"] or 0)))
    for i, it in enumerate(rows, 1):
        it["rank"] = i
    return rows[:10]


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
        # 产品当前名次（主榜口径），新闻条目与频道页共用
        rank_of: dict[str, dict] = {}
        for b in order:
            for it in bitems.get(b, []):
                rank_of.setdefault(it["slug"], {
                    "board": it["board"], "rank": it["rank"], "name": it["name"]})
        directions = [{"key": k, **v} for k, v in reg.get("directions", {}).items()]

        common = {
            "site": reg["site"],
            "meta": meta,
            "directions": directions,
            "nav_directions": [
                {"key": d["key"], "name": d["name"],
                 "icon": d.get("icon", "i-index"), "href": f"/dir/{d['key']}"}
                for d in directions],
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

        # ---- 首页（门户：三方向入口 + 资讯 + 工作台）----
        # KPI 数据:数据源健康 + 最新入库日新闻量
        src_rows = queries.sources_health(c)
        src_ok = sum(1 for r in src_rows if r["enabled"] and r["reachable"])
        src_enabled = sum(1 for r in src_rows if r["enabled"])
        nday = c.execute(
            "SELECT substr(published_at, 1, 10) d, COUNT(*) n FROM news_items"
            " WHERE published_at IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 1"
        ).fetchone()
        ai_weight = float(ai_mod.cfg()["scoring"].get("ai_weight", 0.0))
        core_display = _core_display(bitems.get("harness_core", []), ai_weight)
        model_items = bitems.get("model", [])

        # ---- 三方向频道页（三区固定：新闻流 / 榜单 / 每周总结）----
        def direction_block(bkey: str) -> dict:
            rows = bitems.get(bkey, [])
            if bkey == "harness_core":
                rows = _core_display(rows, ai_weight)
            return {
                "key": bkey,
                "name": reg["boards"].get(bkey, {}).get("name", bkey),
                "rows": rows[:10],
                "obs": len(bobs.get(bkey, [])),
                "snapshot_date": snaps.get(bkey),
                "folded": bool(reg["boards"].get(bkey, {}).get("folded")),
                "top_metrics": _top_metrics(bkey, reg),
            }

        direction_cards = []
        nmeta = queries.news_meta(c)   # 方向页顶栏需展示新闻截至时间
        for d in directions:
            blocks = [direction_block(b) for b in (d.get("boards") or [])]
            cats = d.get("news_categories") or []
            digest_weeks = [
                {"week": w["week"], "count": len(w["items"])}
                for w in queries.news_digests(
                    c, reg.get("news", {}).get("digest_weeks", 8), cats)]
            digest = queries.latest_direction_digest(c, d["news_tag"])
            digest_link = (f"/dir/{d['key']}/digest/{digest['week']}.html"
                           if digest and any(w["week"] == digest["week"]
                                             for w in digest_weeks) else None)
            render("direction.html", f"dir/{d['key']}.html",
                   direction=d, direction_page=True,
                   board_blocks=blocks,
                   news_meta=nmeta, news_page=False,
                   direction_news=queries.news_for_categories(c, cats, 12),
                   direction_digest=digest,
                   direction_digest_link=digest_link,
                   direction_digest_weeks=digest_weeks,
                   news_rank=rank_of)
            direction_cards.append({
                "key": d["key"], "name": d["name"], "short": d.get("short", ""),
                "icon": d.get("icon", "i-index"), "tagline": d.get("tagline", ""),
                "href": f"/dir/{d['key']}",
                "top": blocks[0]["rows"][:5] if blocks else [],
                "news_count": queries.news_count_for_categories(c, cats),
                "placeholder": bool(d.get("placeholder")),
                "placeholder_note": d.get("placeholder_note", ""),
                "planned_sources": d.get("planned_sources") or [],
            })

        render("index.html", "index.html",
               unified=core_display,
               model_top=model_items[:5],
               direction_cards=direction_cards,
               ai_board=ai_mod.summary_of(c, "board"),
               ai_weight_pct=round(ai_weight * 100),
               unified_total=len(bitems.get("harness_core", [])),
               eco_total=len(bitems.get("harness_eco", [])),
               model_total=len(bitems.get("model", [])),
               kpi_sources=src_ok, kpi_sources_enabled=src_enabled,
               kpi_news_count=(nday["n"] if nday else 0),
               kpi_news_date=(nday["d"] if nday else "—"),
               obs_total=sum(len(v) for k, v in bobs.items()))

        # ---- 榜单页 ----
        for b in order:
            if b not in snaps:
                continue
            if b == "harness_core":
                items = _core_display(bitems[b], ai_weight)
            elif b == "model":
                items = bitems[b]   # AA Top 40 全量展示
            else:
                items = bitems[b][:15]   # 各赛道页统一前 15,页面长短一致
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
        # 方法论 v1.5 起不再独立成页,内容并入首页底部折叠块(#methodology)
        render("sources.html", "sources.html", sources=queries.sources_health(c))
        render("login.html", "login.html")
        render("api.html", "api.html")
        # 云剪贴板(工作台):页面壳静态构建,数据走 serve.py 动态 API(/api/v1/clipboard/*)
        from . import clipboard as clipboard_mod
        render("clipboard.html", "clipboard.html", clipboard_page=True,
               clip_limits={"max_file_mb": clipboard_mod.cfg()["max_file_bytes"] // 1048576,
                            "quota_mb": clipboard_mod.cfg()["quota_mb"]})
        knowledge.build(env, common, kb_notes)

        # ---- AI 新闻:不再有全站页面,仅按方向聚合(频道页 + 方向周报) ----
        ncfg = reg.get("news", {})
        # 清理已下线的全站新闻页与周报(2026-10-19 产品决策;dist 可重建)
        (DIST_DIR / "news.html").unlink(missing_ok=True)
        shutil.rmtree(DIST_DIR / "news", ignore_errors=True)
        def digest_counts(items: list[dict]) -> tuple[dict, list]:
            cat_counts: dict[str, int] = {}
            mentions: dict[int, int] = {}
            for item in items:
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
            return cat_counts, top_list

        def render_digest_page(direction: dict, week_items: dict) -> None:
            week = week_items["week"]
            start, end = newslib.week_range(week)
            cat_counts, top_list = digest_counts(week_items["items"])
            out = f"dir/{direction['key']}/digest/{week}.html"
            extra = {
                "direction": direction,
                "digest_home": f"/dir/{direction['key']}",
                "digest_home_label": f"{direction['name']}频道",
                "ai_digest_label": "AI 生成 · 人工可复核修改",
            }
            render("news_digest.html", out,
                   news_page=True, news_meta=nmeta, week=week,
                   week_start=start, week_end=end,
                   digest_items=queries._attach_products(c, week_items["items"]),
                   cat_counts=cat_counts, top_products=top_list,
                   ai_digest=queries.published_digest_summary(
                       c, week, direction=direction["news_tag"]),
                   news_rank=rank_of, **extra)

        # 方向周报:同一管线按 directions.news_categories 过滤
        for direction in directions:
            cats = direction.get("news_categories") or []
            if not cats:
                continue
            out_root = DIST_DIR / "dir" / direction["key"] / "digest"
            outputs = set()
            for d in queries.news_digests(c, ncfg.get("digest_weeks", 8), cats):
                render_digest_page(direction, d)
                outputs.add(d["week"])
            if out_root.is_dir():
                for old in out_root.glob("*.html"):
                    if old.stem not in outputs:
                        old.unlink()
        # ---- 报告厅（第四入口 /reports，内容在仓库 reports/ 目录）----
        reportslib.build(env, common)
    finally:
        c.close()

    # ---- 静态资源 ----
    dst = DIST_DIR / "assets"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(STATIC_DIR / "assets", dst)
    compress_site(DIST_DIR)
