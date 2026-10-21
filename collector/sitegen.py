"""静态站点生成（Jinja2 模板 -> dist/）。

原则（规范第 9 章）：
- 图表构建期渲染为 SVG，无客户端图表库；
- 所有数字经来源标注组件渲染（数值 + 来源 + 采集时间 + /raw 链接）；
- 系统字体栈，无中文 webfont；单页体积远小于 200KB 预算。
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import ai as ai_mod
from . import db, hw, queries, searchindex, seo, svgchart, knowledge
from . import news as newslib
from . import reports as reportslib
from .config import (DB_PATH, DIST_DIR, STATIC_DIR, TEMPLATES_DIR, ensure_dirs,
                     now_iso, registry, today_utc)
from .scoring import load_latest_metrics
from .compression import compress_site

log = logging.getLogger("daimonkey.sitegen")

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


# registry.metric_definitions.unit（英文代码）→ 图表展示用的中文单位。
# 未登记的 unit 回退为空串，图表仍然可用。
METRIC_UNITS = {
    "count": "个",
    "pct": "%",
    "score": "分",
    "rank": "位",
    "tokens_s": "Tokens/s",
}

# base.html 在缺少 seo_meta 时使用的兜底描述（用于测试“不是默认描述”）。
DEFAULT_META_DESCRIPTION = ("以推理技术为重心,聚合智能体热度榜单、AI 新闻、报告厅"
                            "与个人知识库的可溯源技术观察站。")

# 硬件规格对比图：字段由 hw.field_labels() 提供中文名与单位。
HW_BAR_FIELDS = ("hbm_bw_gbs", "fp16_tflops", "int8_tops")


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


def fmt_hw(v):
    """硬件规格单元格：null → —；数字不带千分位（发布年份/带宽都更易读）。"""
    if v is None or isinstance(v, bool):
        return "—"
    if isinstance(v, (int, float)):
        if float(v).is_integer():
            return str(int(v))
        return f"{v:.4f}".rstrip("0").rstrip(".")
    text = str(v).strip()
    return text or "—"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters.update({"num": fmt_num, "pct": fmt_pct, "dt": fmt_dt,
                        "d10": lambda s: (s or "")[:10], "hw_num": fmt_hw})
    return env


def _seo_meta_for(kind: str, ctx: dict) -> tuple[dict, str]:
    """计算页面 SEO 元数据并序列化 JSON-LD。

    SEO 是增强项：page_meta 异常时回退到最小可用元数据，绝不让整站构建失败。
    """
    try:
        meta = seo.page_meta(kind, ctx)
    except Exception:
        log.exception("[sitegen] SEO 元数据生成失败（kind=%s），使用安全回退", kind)
        site = ctx.get("site") if isinstance(ctx.get("site"), dict) else {}
        meta = {
            "title": ctx.get("title") or site.get("name") or seo.SITE_NAME,
            "description": (ctx.get("description") or site.get("tagline")
                            or seo.SITE_TAGLINE or DEFAULT_META_DESCRIPTION),
            "canonical": None,
            "robots": "index, follow",
            "noindex": False,
            "og": {},
            "twitter": {},
            "jsonld": None,
            "site_name": site.get("name") or seo.SITE_NAME,
            "image": seo.DEFAULT_IMAGE,
            "lang": site.get("lang") or seo.DEFAULT_LANGUAGE,
        }
    try:
        return meta, seo.dump_jsonld(meta)
    except Exception:  # dump_jsonld 理论上不会抛，但同样不应阻断建站
        log.exception("[sitegen] JSON-LD 序列化失败（kind=%s），已跳过", kind)
        return meta, ""


def _seo_vars(kind: str, ctx: dict) -> dict:
    """供 render 与 reports.build 共用的 seo_meta / seo_jsonld 变量。"""
    meta, jsonld = _seo_meta_for(kind, ctx)
    return {"seo_meta": meta, "seo_jsonld": jsonld}


def _render_page(env, common: dict, out_dir, tpl: str, out: str,
                 seo_kind: str | None = None,
                 seo_ctx: dict | None = None, **ctx) -> str:
    """渲染单页：注入 seo_meta / seo_jsonld，写入 out_dir/out，返回 HTML。

    seo_ctx 用于补充模板上下文里没有的 SEO 输入（如 path/url/counts）。
    计算失败时由 _seo_meta_for 回退，渲染照常进行。
    """
    page_ctx = dict(ctx)
    render_ctx = {**common, **page_ctx}
    if seo_kind:
        seo_input = {**render_ctx, **(seo_ctx or {})}
        page_ctx.update(_seo_vars(seo_kind, seo_input))
        render_ctx.update(page_ctx)
    html = env.get_template(tpl).render(**render_ctx)
    p = Path(out_dir) / out
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return html


def _metric_unit(key: str, reg: dict) -> str:
    """指标单位：registry.metric_definitions.unit → 中文展示单位。"""
    definition = (reg.get("metric_definitions") or {}).get(key) or {}
    return METRIC_UNITS.get(definition.get("unit") or "", "")


def _product_charts(detail: dict, reg: dict) -> tuple[dict, dict, dict]:
    """产品页指标图：构建期 SVG。

    证据：新版 svgchart 默认 tooltip 会让产品页 SVG 体积 +299%；
    因此产品页固定 hover=False、max_points=60、smooth=True（页面 < 120KB 预算）。
    """
    charts: dict[str, str] = {}
    units: dict[str, str] = {}
    sources: dict[str, dict] = {}
    for key, pts in (detail.get("metrics_series") or {}).items():
        unit = _metric_unit(key, reg)
        charts[key] = svgchart.line_chart(
            [(pt["date"], pt["value"]) for pt in pts],
            unit=unit,
            title=f"{METRIC_NAMES.get(key, key)}趋势",
            hover=False, max_points=60, smooth=True)
        units[key] = unit
        last = pts[-1] if pts else None
        source = (last or {}).get("source") or {}
        if source or (last or {}).get("raw_payload_id"):
            sources[key] = {
                "name": source.get("name") or source.get("key") or "",
                "url": (f"/raw/{last['raw_payload_id']}"
                        if last.get("raw_payload_id") else ""),
            }
    return charts, units, sources


def _hw_updated() -> str:
    """规格表数据更新时间（hw.load() 只返回 specs，updated_at 从 JSON 读）。"""
    try:
        raw = json.loads(Path(hw.SPEC_PATH).read_text(encoding="utf-8"))
    except Exception:
        log.exception("[sitegen] 读取硬件规格表 updated_at 失败")
        return ""
    return str(raw.get("updated_at") or "") if isinstance(raw, dict) else ""


def _hw_bars(specs: list[dict], labels: dict,
             field_keys=None) -> list[dict]:
    """显存带宽 / FP16 / INT8 三张对比图。

    只画 registry.spec_fields 里配置的字段；全 null 的图跳过。
    """
    bars = []
    allowed = set(field_keys) if field_keys is not None else set(HW_BAR_FIELDS)
    for key in HW_BAR_FIELDS:
        if key not in allowed:
            continue
        info = labels.get(key) or {}
        items = [(s.get("model"), s.get(key)) for s in specs if s.get("model")]
        if not any(value is not None for _, value in items):
            continue
        title = info.get("label") or key
        unit = info.get("unit") or ""
        bars.append({
            "key": key,
            "title": title,
            "unit": unit,
            "chart": svgchart.bar_chart(items, unit=unit, title=f"{title}对比",
                                        max_bars=12),
        })
    return bars


def _hw_context(direction: dict) -> dict:
    """按 directions.*.spec_fields / spec_vendors 配置生成硬件规格上下文。

    配置缺失或规格表不可用时返回空 dict，方向页照常渲染（不硬编码厂商/列）。
    """
    try:
        specs = hw.load()
    except Exception:
        log.exception("[sitegen] 硬件规格加载失败（direction=%s，已跳过）",
                      direction.get("key"))
        return {}
    labels = hw.field_labels()
    fields = []
    for key in direction.get("spec_fields") or []:
        info = labels.get(key) or {}
        fields.append({"key": key, "label": info.get("label") or key,
                       "unit": info.get("unit") or ""})
    vendors: list[str] = []
    for vendor in direction.get("spec_vendors") or []:
        if vendor not in vendors:
            vendors.append(vendor)
    if not vendors:
        # 未配置 spec_vendors 时回退为规格表出现顺序，仍然不硬编码厂商。
        for spec in specs:
            vendor = spec.get("vendor")
            if vendor and vendor not in vendors:
                vendors.append(vendor)
    vendors = [v for v in vendors if any(s.get("vendor") == v for s in specs)]
    visible = ([s for s in specs if s.get("vendor") in vendors]
               if vendors else list(specs))
    return {
        "hw_specs": specs,
        "hw_fields": fields,
        "hw_vendors": vendors,
        "hw_updated": _hw_updated(),
        "hw_bars": _hw_bars(visible, labels,
                            [f["key"] for f in fields]),
    }


# sitemap 中永久排除的路径：全站新闻页与旧榜单入口（与 serve.LEGACY_REDIRECTS
# 的 301 对齐）。hidden 的源榜由 _seo_exclude 从 registry 推导，不在此硬编码；
# 这里只列 registry 中已不存在、但历史构建可能残留的旧 board_key。
SITEMAP_EXCLUDE_EXTRA = ("/rankings", "/rankings/unified", "/rankings/framework", "/news")


def _seo_exclude(reg: dict) -> set[str]:
    """本次构建不应进 sitemap 的榜单/旧入口路径集合。

    - registry 中 ``hidden: true`` 的源榜：``/rankings/{board}``（不单独展示）；
    - 显式旧入口：``/rankings``、``/rankings/unified``、``/rankings/framework``、
      ``/news``（均被 serve 301，canonical 不是 200）。
    """
    hidden = {f"/rankings/{board}"
              for board, cfg in (reg.get("boards") or {}).items()
              if isinstance(cfg, dict) and cfg.get("hidden")}
    return hidden | set(SITEMAP_EXCLUDE_EXTRA)


def _prune_stale_rankings(dist_dir: str | Path, rendered_boards) -> list[str]:
    """删除 ``dist/rankings/*.html`` 中本次未渲染的榜单页（连带 ``.html.gz``）。

    旧构建残留的源榜（coding/general/claw）与旧榜单页（unified/framework）虽然
    serve 的 301 会先命中，但文件不应留在产物里，避免以后误服务。只处理
    ``rankings`` 目录顶层的 ``*.html``，不递归、不碰其它子目录。
    """
    root = Path(dist_dir) / "rankings"
    keep = {str(board) for board in (rendered_boards or ())}
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for page in sorted(root.glob("*.html")):
        if page.stem in keep:
            continue
        for target in (page, page.with_name(page.name + ".gz")):
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        removed.append(page.stem)
    return removed


def _finalize_site(env, seo_exclude: set[str] | None = None) -> dict:
    """构建收尾（顺序固定）：静态资源 → 搜索索引 → robots → sitemap → 404 → 压缩。

    - ``copytree`` 会重建 ``dist/assets``，搜索索引必须在其后生成，否则被覆盖；
    - 预压缩必须最后执行，才能把索引 / robots / sitemap / 404 一并压成 .gz；
    - 每一步独立捕获异常并记日志：SEO 发现文件失败不应让整站构建失败。
    """
    result = {"assets": None, "search_index": None, "robots": None,
              "sitemap": None, "404": None, "compressed": False}

    # 1) 静态资源（dist/assets 从零重建）
    try:
        dst = DIST_DIR / "assets"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(STATIC_DIR / "assets", dst)
        result["assets"] = str(dst)
    except Exception:
        log.exception("[sitegen] 静态资源拷贝失败（已跳过）")

    # 2) 站内搜索索引：只读连接，绝不写库
    try:
        ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            ro.row_factory = sqlite3.Row
            result["search_index"] = searchindex.build_index(
                ro, DIST_DIR / "assets" / "search-index.json")
        finally:
            ro.close()
    except Exception:
        log.exception("[sitegen] 站内搜索索引生成失败（已跳过）")

    # 3) robots.txt
    try:
        result["robots"] = str(seo.write_robots(DIST_DIR))
    except Exception:
        log.exception("[sitegen] robots.txt 生成失败（已跳过）")

    # 4) sitemap.xml（排除私有区/404/login、hidden 榜与旧入口）
    if seo_exclude is None:
        seo_exclude = _seo_exclude(registry())
    try:
        result["sitemap"] = str(seo.write_sitemap(
            DIST_DIR, seo.site_base_url(), exclude=seo_exclude))
    except Exception:
        log.exception("[sitegen] sitemap.xml 生成失败（已跳过）")

    # 5) 404 页（独立模板；noindex）
    try:
        result["404"] = str(seo.render_404(env, DIST_DIR))
    except Exception:
        log.exception("[sitegen] 404 页生成失败（已跳过）")

    # 6) 预压缩（必须最后，覆盖前面所有文本资源）
    try:
        compress_site(DIST_DIR)
        result["compressed"] = True
    except Exception:
        log.exception("[sitegen] 预压缩失败（已跳过）")
    return result


def _top_metrics(board_key: str, reg: dict) -> list[str]:
    configured = reg["boards"].get(board_key, {}).get("display_metrics")
    if configured:
        return list(configured)
    if board_key == "harness_core":
        return ["github_stars", "npm_monthly_downloads", "vscode_installs"]
    heat = reg["boards"].get(board_key, {}).get("heat", {})
    return [k for k, _ in sorted(heat.items(), key=lambda kv: -kv[1])[:3]]


# ---- 方向频道子页面（榜单 / 新闻 / 每周总结）--------------------
# 三页共用一套导航与 SEO 文案；标签可被 directions 配置的 section_labels 覆盖，
# 模板只遍历变量，不写死方向名或板块名。
DIRECTION_TABS = (
    {"key": "board", "label": "榜单", "icon": "i-index", "segment": "",
     "copy": "榜单与指标：数据随全量更新刷新，每个数字可溯源。"},
    {"key": "news", "label": "新闻", "icon": "i-news", "segment": "news",
     "copy": "方向新闻：按方向聚合行业资讯，标注来源与站内榜单名次。"},
    {"key": "digest", "label": "每周总结", "icon": "i-report", "segment": "digest",
     "copy": "每周总结：AI 生成、人工可复核，按周回顾方向要闻。"},
)


def direction_tabs(direction: dict) -> list[dict]:
    """导航 partial 的三个入口（href 指向 /dir/{key}[/news|/digest]）。"""
    labels = direction.get("section_labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    base = f"/dir/{direction['key']}"
    return [
        {"key": t["key"],
         "label": labels.get(t["key"]) or t["label"],
         "icon": labels.get(f"{t['key']}_icon") or t["icon"],
         "href": base + (f"/{t['segment']}" if t["segment"] else "")}
        for t in DIRECTION_TABS
    ]


def direction_page_specs(direction: dict) -> list[dict]:
    """每个方向三页：(激活 tab, 模板, dist 相对输出路径)。"""
    key = direction["key"]
    return [
        {"tab": "board", "template": "direction.html", "output": f"dir/{key}.html"},
        {"tab": "news", "template": "direction_news.html",
         "output": f"dir/{key}/news.html"},
        {"tab": "digest", "template": "direction_digest.html",
         "output": f"dir/{key}/digest.html"},
    ]


def direction_page_context(direction: dict, tab_key: str) -> dict:
    """单页上下文：导航、当前态、页面标签与 SEO 描述都由配置生成。"""
    tabs = direction_tabs(direction)
    tab = next(t for t in tabs if t["key"] == tab_key)
    spec = next(t for t in DIRECTION_TABS if t["key"] == tab_key)
    tag = direction.get("tagline") or direction.get("short") or ""
    meta_desc = (f"{direction['name']}{tab['label']}："
                 f"{tag + '。' if tag else ''}{spec['copy']}")
    return {
        "direction": direction,
        "direction_page": True,
        "active_tab": tab_key,
        "direction_tabs": tabs,
        "page_label": tab["label"],
        "meta_desc": meta_desc,
    }


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
        # 本次实际渲染的榜单页集合（导航只含可见且有快照的 board）。
        rendered_boards = [b for b in order if b in snaps]
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
            # 本次真正生成榜单页的 board（hidden 源榜与无快照 board 不在内）。
            # 产品页据此决定 /rankings/{board} 是否可点，避免 404 死链。
            "board_pages": rendered_boards,
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

        def render(tpl: str, out: str, seo_kind: str | None = None,
                   seo_ctx: dict | None = None, **ctx) -> None:
            _render_page(env, common, DIST_DIR, tpl, out,
                         seo_kind=seo_kind, seo_ctx=seo_ctx, **ctx)

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

        # ---- 三方向频道页（每方向三子页：榜单 / 新闻 / 每周总结，共用导航切换）----
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
        direction_outputs: set[str] = set()
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
            # 三页共用：导航/当前态/SEO 由 direction_page_context 生成；
            # 各页只携带自己的板块数据（榜单 / 新闻 / 周总结）。
            direction_news = queries.news_for_categories(c, cats, 12)
            pages = {p["tab"]: p for p in direction_page_specs(d)}
            # 硬件规格：由 registry 的 spec_fields 配置驱动（当前仅 nv），
            # 不硬编码方向 key；配置缺失时返回空上下文，页面照常渲染。
            hw_ctx = _hw_context(d) if d.get("spec_fields") else {}
            render(pages["board"]["template"], pages["board"]["output"],
                   seo_kind="direction",
                   seo_ctx={"path": f"/dir/{d['key']}"},
                   **direction_page_context(d, "board"),
                   **hw_ctx,
                   board_blocks=blocks,
                   news_meta=nmeta, news_page=False,
                   news_rank=rank_of)
            render(pages["news"]["template"], pages["news"]["output"],
                   seo_kind="direction",
                   seo_ctx={"path": f"/dir/{d['key']}/news"},
                   **direction_page_context(d, "news"),
                   direction_news=direction_news,
                   news_meta=nmeta, news_page=False,
                   news_rank=rank_of)
            render(pages["digest"]["template"], pages["digest"]["output"],
                   seo_kind="direction",
                   seo_ctx={"path": f"/dir/{d['key']}/digest"},
                   **direction_page_context(d, "digest"),
                   direction_digest=digest,
                   direction_digest_link=digest_link,
                   direction_digest_weeks=digest_weeks,
                   news_meta=nmeta, news_page=False)
            direction_outputs.update(p["output"] for p in pages.values())
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

        # 配置中删除的方向不残留旧页面（历史周报目录与全站页不动）
        for pattern in ("dir/*.html", "dir/*/news.html", "dir/*/digest.html"):
            for stale in DIST_DIR.glob(pattern):
                if stale.relative_to(DIST_DIR).as_posix() not in direction_outputs:
                    stale.unlink()

        render("index.html", "index.html",
               seo_kind="home",
               seo_ctx={"path": "/", "counts": meta.get("counts") or {}},
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
        for b in rendered_boards:
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
            history_chart = svgchart.multi_line_chart(
                dates, series, unit="score", hover=True, max_points=120,
                title=f"{reg['boards'][b]['name']}近 90 天热度走势")
            render("rankings.html", f"rankings/{b}.html",
                   seo_kind="ranking",
                   seo_ctx={"path": f"/rankings/{b}"},
                   board=b, name=reg["boards"][b]["name"],
                   snapshot_date=snaps[b], items=items, momentum_items=mom,
                   observation=bobs[b], top_metrics=_top_metrics(b, reg),
                   history_chart=history_chart)

        # 清理旧构建残留的榜单页（hidden 源榜 / 旧 board_key），连带 .html.gz
        try:
            pruned = _prune_stale_rankings(DIST_DIR, rendered_boards)
            if pruned:
                log.info("[sitegen] 清理残留榜单页: %s", ", ".join(pruned))
        except Exception:
            log.exception("[sitegen] 残留榜单页清理失败（已跳过）")

        # ---- 产品页 ----
        news_idx = queries.news_by_product(c, limit_per_product=8)
        for p in products:
            d = queries.product_detail(c, p["slug"])
            d["latest_ranks"] = {}
            for r in d["ranks"]:
                d["latest_ranks"].setdefault(r["board"], r)
            d["news"] = news_idx.get(p["id"], [])
            charts, chart_units, chart_sources = _product_charts(d, reg)
            render("product.html", f"product/{p['slug']}.html",
                   seo_kind="product",
                   seo_ctx={"path": f"/product/{p['slug']}"},
                   p=d, charts=charts, chart_units=chart_units,
                   chart_sources=chart_sources)

        # ---- 原始数据页 ----
        for r in c.execute("SELECT id FROM raw_payloads"):
            d = queries.raw_detail(c, r["id"])
            if isinstance(d["body"], (dict, list)):
                body_txt = json.dumps(d["body"], ensure_ascii=False, indent=2)
            else:
                body_txt = d["body"] or ""
            render("raw.html", f"raw/{r['id']}.html",
                   seo_kind="raw",
                   seo_ctx={"path": f"/raw/{r['id']}",
                            "title": f"原始响应 #{r['id']}"},
                   raw=d, body_txt=body_txt[:200_000])

        # ---- 静态说明页 ----
        # 方法论 v1.5 起不再独立成页,内容并入首页底部折叠块(#methodology)
        render("sources.html", "sources.html", sources=queries.sources_health(c),
               seo_kind="sources", seo_ctx={"path": "/sources"})
        render("login.html", "login.html",
               seo_kind="login", seo_ctx={"path": "/login"})
        render("api.html", "api.html",
               seo_kind="api", seo_ctx={"path": "/api"})
        # 云剪贴板(工作台):页面壳静态构建,数据走 serve.py 动态 API(/api/v1/clipboard/*)
        from . import clipboard as clipboard_mod
        render("clipboard.html", "clipboard.html", clipboard_page=True,
               seo_kind="clipboard", seo_ctx={"path": "/clipboard"},
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
                   seo_kind="news",
                   seo_ctx={"path": f"/dir/{direction['key']}/digest/{week}"},
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
        # 报告厅属于私有区：reports.build 通过 page_seo 回调拿到 noindex 的
        # seo_meta / seo_jsonld，索引页与详情页都不会被索引。
        reportslib.build(env, common, page_seo=_seo_vars)
    finally:
        c.close()

    # ---- 静态资源与站点发现文件（顺序固定，勿调换）----
    # 1) dist/assets 从零重建；search-index.json 必须在其后生成，否则被覆盖；
    # 2) robots/sitemap/404 生成后，最后统一预压缩。
    # 每一步异常只记日志，不阻断其余步骤（详见 _finalize_site）。
    _finalize_site(env, seo_exclude=_seo_exclude(reg))
