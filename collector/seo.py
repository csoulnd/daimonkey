"""SEO 元数据与站点发现文件（robots.txt / sitemap.xml / feed.xml）。

设计约束：
- 纯标准库，不引入第三方依赖；可在构建期离线运行；
- 页面元数据按页面类型生成，调用方只需传上下文（sitegen/serve 接线）；
- 私有区（报告厅/知识库/剪贴板/讲稿/原始数据）永不进 sitemap、永不 index；
- 站点根 URL 统一读环境变量 ``DM_SITE_URL``；未设置时使用 ``http://<host>``
  占位并在 robots/sitemap 中写注释提醒部署方补全。

对外函数：
    page_meta(kind, ctx) -> dict
    write_robots(dist_dir) -> Path
    write_sitemap(dist_dir, base_url=None, *, exclude=None,
                  exclude_prefixes=None, **kw) -> Path
    write_feed(dist_dir, items=None, base_url=None, ...) -> Path
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

SITE_NAME = "InferTech 观察站"
SITE_TAGLINE = "每个数字都能溯源到原始响应的第三方智能体榜单"
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_IMAGE = "/assets/daimonkey-color.svg"

# 私有区：不抓取、不索引、不进 sitemap；knowledge-<user> 为按用户隔离的构建产物。
PRIVATE_DIRS = ("reports", "knowledge", "clipboard", "notes", "raw")
PRIVATE_PREFIXES = ("knowledge-",)
# 不应出现在 sitemap 的页面：404 与登录页本身带 noindex。
SITEMAP_SKIP_FILES = ("404.html", "login.html")
# 原型页目录（design/ 仅内部评审用，/design 本身没有可 200 的 canonical 页）。
DEFAULT_SITEMAP_EXCLUDE_PREFIXES = ("/design",)
# 私有区即使以单文件形式落在 dist 根目录（如 reports.html）也不进 sitemap。
PRIVATE_PAGE_FILES = tuple(f"{name}.html" for name in PRIVATE_DIRS)
# robots.txt 明确禁抓的路径（顺序即输出顺序，便于人工检查）
ROBOTS_DISALLOW = (
    "/reports",
    "/knowledge",
    "/clipboard",
    "/notes",
    "/raw",
    "/api/v1/raw",
)

# 产品页描述用到的关键指标（与 sitegen.METRIC_NAMES 保持同名，避免循环依赖）。
METRIC_LABELS = {
    "github_stars": "GitHub Stars",
    "github_forks": "Forks",
    "github_contributors": "贡献者",
    "github_commits_30d": "30 天提交",
    "npm_monthly_downloads": "npm 月下载",
    "pypi_monthly_downloads": "PyPI 月下载",
    "vscode_installs": "VS Code 安装",
    "appstore_rating_count": "App Store 评分数",
    "aa_intelligence": "AA 智能指数",
    "sc_total": "SuperCLUE 总分",
    "mlperf_gptoss_120b": "gpt-oss-120b 单卡 Tokens/s",
    "mlperf_deepseek_r1": "DeepSeek-R1 单卡 Tokens/s",
    "mlperf_llama3_8b": "Llama3.1-8B 单卡 Tokens/s",
}
PREFERRED_METRICS = (
    "github_stars",
    "npm_monthly_downloads",
    "pypi_monthly_downloads",
    "vscode_installs",
    "aa_intelligence",
    "sc_total",
    "mlperf_gptoss_120b",
)

KIND_ALIASES = {
    "index": "home",
    "homepage": "home",
    "portal": "home",
    "board": "ranking",
    "rank": "ranking",
    "rankings": "ranking",
    "dir": "direction",
    "channel": "direction",
    "digest": "news",
    "news_digest": "news",
    "weekly": "news",
    "report": "reports",
    "kb": "knowledge",
    "knowledge_base": "knowledge",
    "raw_data": "raw",
    "notfound": "404",
    "not_found": "404",
}

# 需要 noindex 的页面类型（私有区/账号页/错误页/搜索页）。
NOINDEX_KINDS = {"reports", "knowledge", "clipboard", "notes", "raw", "login", "404", "search"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _site_cfg(ctx: dict | None) -> dict:
    """优先用调用方传入的 site 配置，缺失时回退 registry.yaml。"""
    ctx = ctx or {}
    site = ctx.get("site")
    if isinstance(site, dict) and site.get("name"):
        return site
    try:
        from .config import registry
        reg_site = registry().get("site") or {}
        if isinstance(reg_site, dict) and reg_site.get("name"):
            return reg_site
    except Exception:  # registry 读失败不应让页面构建整体失败
        pass
    return {"name": SITE_NAME, "tagline": SITE_TAGLINE, "lang": DEFAULT_LANGUAGE}


def _registry() -> dict:
    try:
        from .config import registry
        return registry() or {}
    except Exception:
        return {}


def site_base_url(base_url: str | None = None) -> str:
    """站点根 URL（不含末尾斜杠）。

    优先级：显式参数 > 环境变量 DM_SITE_URL > ``http://<host>`` 占位。
    """
    raw = (base_url or os.environ.get("DM_SITE_URL") or "").strip()
    if not raw:
        return "http://<host>"
    if raw.startswith("//"):
        raw = "https:" + raw
    elif "://" not in raw:
        raw = "https://" + raw
    return raw.rstrip("/")


def is_placeholder_base(base_url: str) -> bool:
    """是否为未配置站点基址的占位 URL。"""
    return "<host>" in (base_url or "")


def _abs_url(path_or_url: str | None, base_url: str) -> str:
    if not path_or_url:
        return base_url + "/"
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    if path_or_url.startswith("//"):
        return "https:" + path_or_url
    return base_url.rstrip("/") + "/" + path_or_url.lstrip("/")


def _clean(text, limit: int = 160) -> str:
    """去 HTML/换行、压缩空白、按字符数截断（中文按字，不按字节）。"""
    if text is None:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip("，,。.;；、 ") + "…"
    return text


def _plain(text, limit: int = 0) -> str:
    """只做实体解码与空白压缩，不剥离尖括号（feed 标题可能含字面 <）。"""
    if text is None:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip("，,。.;；、 ") + "…"
    return text


def _fmt_num(value) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return f"{int(f):,}"
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:,.1f}"


def _fmt_date(value: str | None) -> str:
    """ISO 时间 -> YYYY-MM-DD；无法解析时返回原值前 10 位。"""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return str(value)[:10]


def _iso_date(value: str | None) -> str:
    """严格取 YYYY-MM-DD（用于 JSON-LD 的 datePublished/dateModified）。"""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(value))
        return match.group(1) if match else ""


def _normalize_kind(kind: str | None) -> str:
    key = (kind or "page").strip().lower()
    return KIND_ALIASES.get(key, key)


def _ctx_get(ctx: dict, *keys, default=None):
    for key in keys:
        value = ctx.get(key)
        if value is not None:
            return value
    return default


def _row_get(row, key, default=None):
    """兼容 dict 与 sqlite3.Row（调用方可能直接传查询行）。"""
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


# ---------------------------------------------------------------------------
# page_meta
# ---------------------------------------------------------------------------

def page_meta(kind: str, ctx: dict | None = None) -> dict:
    """按页面类型生成 SEO 元数据。

    Args:
        kind: ``home|product|ranking|direction|news|article|sources|api|login|
        404|reports|knowledge|clipboard|notes|raw|page``（含常见别名）。
        ctx: 页面上下文。兼容 sitegen 的模板变量：
            site / base_url / path|url / product|p / board / name / snapshot_date /
            items / direction / week / week_start / week_end / digest_items /
            ai_digest / news_meta / title / summary|excerpt|description /
            published_at|date / author / image / metric_names / board_names。

    Returns:
        dict: ``title``、``description``、``canonical``、``robots``、``og``、
        ``twitter``、``jsonld``、``site_name``、``image``、``noindex``。
        私有/错误页返回 ``robots='noindex, nofollow'``、``jsonld=None``。
    """
    kind = _normalize_kind(kind)
    ctx = dict(ctx or {})
    site = _site_cfg(ctx)
    site_name = _clean(site.get("name") or SITE_NAME, 80)
    lang = site.get("lang") or DEFAULT_LANGUAGE
    base = site_base_url(ctx.get("base_url"))
    image = _abs_url(ctx.get("image") or DEFAULT_IMAGE, base)

    if kind in NOINDEX_KINDS:
        title = _private_title(kind, ctx, site_name)
        return _payload(title=title, description="", canonical=None,
                        robots="noindex, nofollow", og=None, twitter=None,
                        jsonld=None, site_name=site_name, image=image,
                        noindex=True, lang=lang)

    if kind == "home":
        title = _clean(_ctx_get(ctx, "title", default=
                       f"{site_name} · 智能体/模型/硬件三方向热度榜与 AI 新闻"), 90)
        desc = _home_description(ctx, site)
        path = ctx.get("path") or "/"
        canonical = _abs_url(path, base)
        og_type = "website"
        jsonld = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": site_name,
            "url": _abs_url("/", base),
            "description": desc,
            "inLanguage": lang,
            "publisher": {"@type": "Organization", "name": site_name,
                          "url": _abs_url("/", base)},
        }
    elif kind == "product":
        title, desc, canonical, og_type, jsonld = _product_meta(ctx, site_name, base, lang)
    elif kind == "ranking":
        title, desc, canonical, og_type, jsonld = _ranking_meta(ctx, site_name, base, lang)
    elif kind == "direction":
        title, desc, canonical, og_type, jsonld = _direction_meta(ctx, site_name, base, lang)
    elif kind == "news":
        title, desc, canonical, og_type, jsonld = _news_meta(ctx, site_name, base, lang)
    elif kind == "article":
        title, desc, canonical, og_type, jsonld = _article_meta(ctx, site_name, base, lang)
    elif kind == "sources":
        title = _clean(ctx.get("title") or f"数据源与采集方法 | {site_name}", 90)
        desc = _clean(ctx.get("description") or
                      "站点全部数据源、采集频率、授权与原始响应保留策略；每个数字都可回查原始响应。", 160)
        canonical = _abs_url(ctx.get("path") or "/sources", base)
        og_type, jsonld = "website", _webpage_jsonld(
            title, desc, canonical, site_name, base, lang)
    elif kind == "api":
        title = _clean(ctx.get("title") or f"开放 API | {site_name}", 90)
        desc = _clean(ctx.get("description") or
                      "榜单、新闻与数据源元信息的只读 JSON API；字段契约稳定，便于二次分析。", 160)
        canonical = _abs_url(ctx.get("path") or "/api", base)
        og_type, jsonld = "website", _webpage_jsonld(
            title, desc, canonical, site_name, base, lang)
    else:  # page 及其他未知类型：使用调用方提供的标题/描述，保持可用
        title = _clean(ctx.get("title") or site_name, 90)
        desc = _clean(ctx.get("description") or site.get("tagline") or SITE_TAGLINE, 160)
        canonical = _abs_url(ctx.get("path") or ctx.get("url") or "/", base)
        og_type, jsonld = "website", _webpage_jsonld(
            title, desc, canonical, site_name, base, lang)

    og = {
        "title": title,
        "description": desc,
        "type": og_type,
        "url": canonical,
        "image": image,
        "site_name": site_name,
        "locale": "zh_CN" if (lang or "").lower().startswith("zh") else lang.replace("-", "_"),
    }
    twitter = {
        "card": "summary_large_image",
        "title": title,
        "description": desc,
        "image": image,
    }
    return _payload(title=title, description=desc, canonical=canonical,
                    robots="index, follow", og=og, twitter=twitter, jsonld=jsonld,
                    site_name=site_name, image=image, noindex=False, lang=lang)


def _payload(*, title: str, description: str, canonical: str | None, robots: str,
             og: dict | None, twitter: dict | None, jsonld: dict | None,
             site_name: str, image: str, noindex: bool, lang: str) -> dict:
    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "robots": robots,
        "noindex": noindex,
        "og": og or {},
        "twitter": twitter or {},
        "jsonld": jsonld,
        "site_name": site_name,
        "image": image,
        "lang": lang,
    }


def _private_title(kind: str, ctx: dict, site_name: str) -> str:
    defaults = {
        "reports": "报告厅",
        "knowledge": "个人知识库",
        "clipboard": "云剪贴板",
        "notes": "讲稿",
        "raw": "原始响应",
        "login": "登录",
        "404": "页面不存在",
        "search": "站内搜索",
    }
    return _clean(ctx.get("title") or f"{defaults.get(kind, '私有内容')} | {site_name}", 90)


def _home_description(ctx: dict, site: dict) -> str:
    tagline = _clean(site.get("tagline") or SITE_TAGLINE, 80)
    parts = [tagline]
    counts = ctx.get("counts") if isinstance(ctx.get("counts"), dict) else {}
    products = counts.get("products") or ctx.get("product_total")
    news = counts.get("news_items") or ctx.get("news_total")
    if products:
        parts.append(f"已收录 {products} 个产品")
    if news:
        parts.append(f"{news} 条 AI 资讯")
    parts.append("每个数字可溯源到原始响应，附评分方法与数据源说明。")
    return _clean("；".join(parts), 160)


def _product_meta(ctx, site_name, base, lang):
    p = _ctx_get(ctx, "product", "p") or {}
    name = _clean(p.get("name") or ctx.get("name") or "产品", 60)
    vendor = _clean(p.get("vendor") or "", 40)
    category = _clean(p.get("category") or "", 40)
    slug = p.get("slug") or ctx.get("slug") or ""
    path = ctx.get("path") or (f"/product/{slug}" if slug else "/")
    canonical = _abs_url(path, base)

    ranks = p.get("latest_ranks") if isinstance(p.get("latest_ranks"), dict) else {}
    rank_phrase = ""
    if ranks:
        board, row = min(
            ranks.items(),
            key=lambda kv: (_row_get(kv[1], "rank") is None,
                            _row_get(kv[1], "rank") or 10 ** 6),
        )
        board_name = _board_name(board, ctx)
        rank = _row_get(row, "rank")
        if rank:
            rank_phrase = f"{board_name}第 {rank} 名"
    rank_phrase = rank_phrase or (ctx.get("rank_phrase") or "")

    metrics = _product_metric_bits(p, ctx)
    lead = f"{name}"
    if vendor:
        lead += f"（{vendor}）"
    bits = [lead + "热度与排名"]
    if metrics:
        bits.append("、".join(metrics[:2]))
    if rank_phrase:
        bits.append(rank_phrase)
    desc = _clean("：".join([bits[0], "，".join(bits[1:])]) if len(bits) > 1 else bits[0], 160)
    if len(desc) < 40:
        desc = _clean(f"{desc}。汇总公开可溯源指标、指标时序与相关新闻，数据来自第三方公开渠道。", 160)
    title = _clean(f"{name}{' · ' + vendor if vendor else ''} 热度、排名与指标 | {site_name}", 90)

    application_category = "AI Agent"
    if category == "model":
        application_category = "AI Model"
    elif category == "nv" or (p.get("origin") == "mlperf"):
        application_category = "AI Hardware"
    jsonld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": name,
        "description": desc,
        "url": canonical,
        "applicationCategory": application_category,
        "operatingSystem": "Any",
        "inLanguage": lang,
        "isPartOf": {"@type": "WebSite", "name": site_name, "url": _abs_url("/", base)},
    }
    if vendor:
        jsonld["author"] = {"@type": "Organization", "name": vendor}
    keywords = [name, vendor, category, rank_phrase] + metrics
    jsonld["keywords"] = "、".join(k for k in keywords if k)
    return title, desc, canonical, "article", jsonld


def _product_metric_bits(p: dict, ctx: dict) -> list[str]:
    """从 metrics_series 或 board item 的 metrics 里取最关键指标的展示文本。"""
    labels = dict(METRIC_LABELS)
    if isinstance(ctx.get("metric_names"), dict):
        labels.update(ctx["metric_names"])
    values: dict[str, object] = {}
    series = p.get("metrics_series")
    if isinstance(series, dict):
        for key, points in series.items():
            if not isinstance(points, (list, tuple)):
                continue
            for point in reversed(points):
                value = point.get("value") if isinstance(point, dict) else None
                if value is not None:
                    values[key] = value
                    break
    metrics = p.get("metrics")
    if isinstance(metrics, dict):
        for key, meta in metrics.items():
            if isinstance(meta, dict) and meta.get("value") is not None:
                values.setdefault(key, meta["value"])
    out = []
    for key in PREFERRED_METRICS:
        if key in values:
            out.append(f"{labels.get(key, key)} {_fmt_num(values[key])}")
        if len(out) >= 3:
            break
    return out


def _board_name(board: str, ctx: dict) -> str:
    names = ctx.get("board_names") if isinstance(ctx.get("board_names"), dict) else {}
    name = names.get(board)
    if not name:
        name = (_registry().get("boards", {}).get(board) or {}).get("name")
    return f"{name or board}"


def _ranking_meta(ctx, site_name, base, lang):
    board = ctx.get("board") or ""
    name = _clean(ctx.get("name") or _board_name(board, ctx) or "榜单", 60)
    snapshot = _fmt_date(ctx.get("snapshot_date"))
    items = ctx.get("items") if isinstance(ctx.get("items"), list) else []
    path = ctx.get("path") or (f"/rankings/{board}" if board else "/rankings")
    canonical = _abs_url(path, base)
    date_part = f"{snapshot} 快照" if snapshot else "最新榜单"
    title = _clean(f"{name} · {date_part} | {site_name}", 90)

    lead = f"{name}（{date_part}）："
    top = []
    for i, it in enumerate(items[:3], 1):
        if not isinstance(it, dict):
            continue
        item_name = _clean(it.get("name") or "", 40)
        if not item_name:
            continue
        heat = it.get("heat_score")
        if heat is None:
            heat = it.get("final_score")
        top.append(f"第 {i} 名 {item_name}" + (f"（热度 {_fmt_num(heat)}）" if heat is not None else ""))
    total = len(items)
    desc = lead + ("、".join(top) if top else "按公开可溯源指标计算排名") + (
        f"。本期共 {total} 个条目，数据截至 {snapshot}。" if snapshot else "。")
    desc = _clean(desc, 160)

    elements = []
    for i, it in enumerate(items[:10], 1):
        if not isinstance(it, dict) or not it.get("name"):
            continue
        slug = it.get("slug")
        elements.append({
            "@type": "ListItem",
            "position": i,
            "name": _clean(it.get("name"), 60),
            "url": _abs_url(f"/product/{slug}" if slug else path, base),
        })
    jsonld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": name,
        "description": desc,
        "url": canonical,
        "numberOfItems": total or len(elements),
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "itemListElement": elements,
        "inLanguage": lang,
    }
    return title, desc, canonical, "website", jsonld


# 方向频道三子页（sitegen.direction_page_context 提供 active_tab/page_label）。
# 默认标签与定位句和 sitegen.DIRECTION_TABS 同口径；seo.py 不反向依赖 sitegen，
# 避免循环导入。页面标签仍可被 registry 的 section_labels 覆盖（经 page_label 传入）。
DIRECTION_TAB_LABELS = {"board": "榜单", "news": "新闻", "digest": "每周总结"}
DIRECTION_TAB_FOCUS = {
    "board": "榜单与指标",
    "news": "方向新闻",
    "digest": "每周总结",
}
DIRECTION_LEGACY_LABEL = "新闻/榜单/周总结"


def _direction_tab(ctx: dict) -> tuple[str, str]:
    """解析方向频道当前子页：返回 ``(tab, label)``，两者都缺失时返回 ``("", "")``。

    ``active_tab`` 缺失但 ``page_label`` 存在时按默认标签反查（自定义标签反查
    不到则只影响标题，描述退回方向总述）；两者都缺失时调用方沿用旧版组合标题，
    保证不传 tab 的历史调用方行为不变。
    """
    tab = str(ctx.get("active_tab") or "").strip().lower()
    if tab not in DIRECTION_TAB_LABELS:
        tab = ""
    label = _clean(ctx.get("page_label") or "", 20)
    if not label and tab:
        label = DIRECTION_TAB_LABELS[tab]
    if not tab and label:
        tab = next((k for k, v in DIRECTION_TAB_LABELS.items() if v == label), "")
    return tab, label


def _direction_meta(ctx, site_name, base, lang):
    d = ctx.get("direction") if isinstance(ctx.get("direction"), dict) else {}
    name = _clean(d.get("name") or ctx.get("name") or "方向频道", 40)
    key = d.get("key") or ctx.get("key") or ""
    path = ctx.get("path") or (f"/dir/{key}" if key else "/")
    canonical = _abs_url(path, base)
    nmeta = ctx.get("news_meta") if isinstance(ctx.get("news_meta"), dict) else {}
    date = _fmt_date(nmeta.get("last_fetched_at") or ctx.get("date") or ctx.get("snapshot_date"))
    tab, label = _direction_tab(ctx)
    date_part = f"（{date}）" if date else ""
    if label:
        title = _clean(f"{name}频道 · {label}{date_part} | {site_name}", 90)
    else:  # 兼容：调用方未传 active_tab/page_label 时保留旧版组合标题
        title = _clean(f"{name}频道 · {DIRECTION_LEGACY_LABEL}{date_part}"
                       f" | {site_name}", 90)

    base_desc = _clean(d.get("description") or d.get("tagline") or
                       f"{name}方向的最新动态、榜单与每周总结。", 110).rstrip("。")
    if tab:  # 子页专属：带上方向名/子页名 + 定位句，再接公共 boards/date/溯源 tail
        focus = DIRECTION_TAB_FOCUS[tab]
        lead = f"{name}方向{label}：{base_desc}"
        lead = f"{lead}{'，' if lead.endswith('…') else '。'}本页聚焦{focus}"
    else:    # 兼容：不传 tab 时描述与旧版完全一致
        lead = base_desc
    boards = d.get("boards") or []
    board_names = "、".join(_board_name(b, ctx) for b in boards) if boards else ""
    tail = []
    if board_names:
        tail.append(f"含{board_names}")
    if date:
        tail.append(f"数据/新闻截至 {date}")
    tail.append("每个数字可溯源到原始响应")
    desc = _clean(lead + "。" + "；".join(tail) + "。", 160)

    jsonld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        # 三子页共享 CollectionPage 语义（都是集合页），但 name 必须区分；
        # 无 tab 时保持旧值，避免影响历史调用方。
        "name": f"{name}频道 · {label}" if label else f"{name}频道",
        "description": desc,
        "url": canonical,
        "inLanguage": lang,
        "isPartOf": {"@type": "WebSite", "name": site_name, "url": _abs_url("/", base)},
    }
    if _iso_date(date):
        jsonld["dateModified"] = _iso_date(date)
    return title, desc, canonical, "website", jsonld


def _news_meta(ctx, site_name, base, lang):
    d = ctx.get("direction") if isinstance(ctx.get("direction"), dict) else {}
    dir_name = _clean(d.get("name") or ctx.get("direction_name") or "AI", 40)
    key = d.get("key") or ctx.get("key") or ""
    week = _clean(ctx.get("week") or "", 20)
    start = _fmt_date(ctx.get("week_start"))
    end = _fmt_date(ctx.get("week_end"))
    path = ctx.get("path") or (
        f"/dir/{key}/digest/{week}.html" if key and week else "/")
    canonical = _abs_url(path, base)

    digest = ctx.get("ai_digest") if isinstance(ctx.get("ai_digest"), dict) else {}
    items = ctx.get("digest_items") if isinstance(ctx.get("digest_items"), list) else []
    title = _clean(
        f"{dir_name}方向周报 {week}" + (f"（{start}~{end}）" if start and end else "")
        + f" | {site_name}", 100)
    summary = _clean(digest.get("summary") or ctx.get("summary") or "", 150)
    if not summary and items:
        summary = "；".join(_clean(it.get("title"), 50) for it in items[:3]
                           if isinstance(it, dict) and it.get("title"))
    desc = _clean(
        (summary or f"{dir_name}方向本周新闻聚合与要点")
        + (f"。本期收录 {len(items)} 条。" if items else ""), 160)

    published = (start or _iso_date(digest.get("created_at")) or
                 _iso_date(ctx.get("published_at")))
    jsonld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": f"{dir_name}方向周报 {week}",
        "description": desc,
        "url": canonical,
        "inLanguage": lang,
        "publisher": {"@type": "Organization", "name": site_name,
                      "url": _abs_url("/", base)},
        "isPartOf": {"@type": "WebSite", "name": site_name, "url": _abs_url("/", base)},
    }
    if published:
        jsonld["datePublished"] = published
        jsonld["dateModified"] = published
    return title, desc, canonical, "article", jsonld


def _article_meta(ctx, site_name, base, lang):
    raw_url = ctx.get("url") or ctx.get("path") or "/"
    canonical = _abs_url(raw_url, base)
    title = _clean(ctx.get("title") or ctx.get("headline") or site_name, 100)
    if site_name not in title:
        title = _clean(f"{title} | {site_name}", 100)
    summary = _ctx_get(ctx, "summary", "excerpt", "description", default="")
    desc = _clean(summary or f"{title}。", 160)
    published = _iso_date(_ctx_get(ctx, "published_at", "date", default=None))
    author = _clean(ctx.get("author") or "", 60)
    source = _clean(ctx.get("source_name") or "", 60)
    jsonld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle" if source else "Article",
        "headline": _clean(ctx.get("title") or title, 110),
        "description": desc,
        "url": canonical,
        "inLanguage": lang,
        "publisher": {"@type": "Organization", "name": site_name,
                      "url": _abs_url("/", base)},
    }
    if published:
        jsonld["datePublished"] = published
        jsonld["dateModified"] = published
    if author:
        jsonld["author"] = {"@type": "Person", "name": author}
    return title, desc, canonical, "article", jsonld


def _webpage_jsonld(title, desc, url, site_name, base, lang) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": title,
        "description": desc,
        "url": url,
        "inLanguage": lang,
        "isPartOf": {"@type": "WebSite", "name": site_name, "url": _abs_url("/", base)},
    }


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

def write_robots(dist_dir: str | Path) -> Path:
    """写 ``<dist>/robots.txt``：允许公开页，禁抓私有区，声明 sitemap。

    sitemap 的站点根 URL 读 ``DM_SITE_URL``；未设置时用 ``http://<host>``
    占位，并在文件头注释中提醒。
    """
    dist = Path(dist_dir)
    dist.mkdir(parents=True, exist_ok=True)
    base = site_base_url()
    lines = [f"# robots.txt · {SITE_NAME}"]
    if is_placeholder_base(base):
        lines.append("# DM_SITE_URL 未设置，将使用 http://<host> 占位；"
                     "部署时请设置 DM_SITE_URL=http://106.12.30.116:8080")
    lines.extend([
        "User-agent: *",
        "Allow: /",
        "",
        "# 登录后可见 / 原始数据：禁止抓取与索引",
    ])
    lines.extend(f"Disallow: {path}" for path in ROBOTS_DISALLOW)
    lines.extend(["", f"Sitemap: {base}/sitemap.xml", ""])
    target = dist / "robots.txt"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# sitemap.xml
# ---------------------------------------------------------------------------

def _is_private_rel(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return True
    head = parts[0]
    if head in PRIVATE_DIRS:
        return True
    if head.startswith(PRIVATE_PREFIXES):
        return True
    # 任意层级出现私有目录（防御性：私有区可能被嵌套构建）
    if any(part in PRIVATE_DIRS for part in parts[:-1]):
        return True
    if rel.name in SITEMAP_SKIP_FILES or rel.name in PRIVATE_PAGE_FILES:
        return True
    return False


def _url_for_rel(rel: Path) -> str:
    if rel.as_posix() == "index.html":
        return "/"
    if rel.name == "index.html":
        return "/" + rel.parent.as_posix()
    return "/" + rel.with_suffix("").as_posix()


def _sitemap_meta(url: str) -> tuple[str, str]:
    """返回 (changefreq, priority)。"""
    if url == "/":
        return "daily", "1.0"
    if url.startswith("/dir/"):
        return ("weekly", "0.7") if "/digest/" in url else ("daily", "0.9")
    if url.startswith("/rankings/"):
        return "daily", "0.8"
    if url.startswith("/product/"):
        return "weekly", "0.6"
    if url in ("/sources", "/api"):
        return "monthly", "0.5"
    return "monthly", "0.4"


def _normalize_sitemap_path(path: str | Path | None) -> str:
    """把文件相对路径或 URL 路径规范成带前导斜杠、无 ``.html`` 的站点路径。

    ``index.html`` 与服务端目录语义一致：``/x/index.html`` → ``/x``，
    根 ``index.html`` → ``/``。查询串/锚点被丢弃（sitemap 只用路径）。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    text = text.split("#", 1)[0].split("?", 1)[0]
    text = "/" + text.lstrip("/")
    if text.endswith("index.html"):
        text = text[: -len("index.html")]
    elif text.endswith(".html"):
        text = text[: -len(".html")]
    if not text:
        return "/"
    if text != "/":
        text = text.rstrip("/") or "/"
    return text


def _sitemap_excluded(url_path: str, exclude: set[str], prefixes: tuple[str, ...]) -> bool:
    """规范路径是否命中 exclude 集合或某个前缀（按路径段匹配）。"""
    norm = _normalize_sitemap_path(url_path)
    if norm in exclude:
        return True
    for prefix in prefixes:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    return False


def write_sitemap(dist_dir: str | Path, base_url: str | None = None, *,
                  exclude: set[str] | tuple[str, ...] | list[str] | str | None = None,
                  exclude_prefixes: tuple[str, ...] | list[str] | str | None = None,
                  **kw) -> Path:
    """遍历 ``dist/`` 生成 sitemap.xml。

    排除私有目录（reports/knowledge*/clipboard/notes/raw）、404 页与 ``*.gz``；
    只收录 ``.html``。``base_url`` 缺省读 ``DM_SITE_URL``，再缺省用
    ``http://<host>`` 占位并在 XML 注释中说明。

    Args:
        exclude: 需排除的规范路径集合（如 ``/rankings/unified``）。比较前双方
            都会规范化：``a/b.html`` → ``/a/b``、``index.html`` → ``/``，
            因此 ``/a/b.html``、``a/b.html``、``/a/b`` 等价。
        exclude_prefixes: 需按前缀排除的路径，默认 ``("/design",)``
            （原型页不入 sitemap）。传入空元组可关闭默认前缀排除。
        **kw: 兼容旧调用方的扩展参数，当前忽略。
    """
    del kw  # 保留签名兼容；当前没有额外选项
    dist = Path(dist_dir)
    dist.mkdir(parents=True, exist_ok=True)
    base = site_base_url(base_url)
    placeholder = is_placeholder_base(base)
    from urllib.parse import quote  # 仅对路径中的非 ASCII 做百分号编码

    if isinstance(exclude, str):
        exclude = (exclude,)
    excluded = {_normalize_sitemap_path(p) for p in (exclude or ())}
    excluded.discard("")
    if exclude_prefixes is None:
        exclude_prefixes = DEFAULT_SITEMAP_EXCLUDE_PREFIXES
    elif isinstance(exclude_prefixes, str):
        exclude_prefixes = (exclude_prefixes,)
    prefixes = tuple(_normalize_sitemap_path(p) for p in exclude_prefixes)
    prefixes = tuple(p for p in prefixes if p)

    entries = []
    for path in sorted(dist.rglob("*.html")):
        rel = path.relative_to(dist)
        if _is_private_rel(rel):
            continue
        url_path = _url_for_rel(rel)
        if _sitemap_excluded(url_path, excluded, prefixes):
            continue
        # 保留斜杠，其余按 URL 规则编码（slug 一般已是 ASCII）
        loc = base + quote(url_path, safe="/:@")
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            lastmod = mtime.strftime("%Y-%m-%d")
        except OSError:
            lastmod = ""
        entries.append((loc, lastmod, url_path))

    # 首页优先，其余按 URL 字典序，输出稳定可 diff。
    entries.sort(key=lambda e: (e[2] != "/", e[2]))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
    ]
    if placeholder:
        lines.append("<!-- DM_SITE_URL 未设置，将使用 http://<host> 占位；"
                     "部署时请设置 DM_SITE_URL=http://106.12.30.116:8080 -->")
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for loc, lastmod, url_path in entries:
        changefreq, priority = _sitemap_meta(url_path)
        lines.append("  <url>")
        lines.append(f"    <loc>{_xml_escape(loc)}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append(f"    <changefreq>{changefreq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    lines.append("")
    target = dist / "sitemap.xml"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# feed.xml（可选；按方向聚合的 Atom）
# ---------------------------------------------------------------------------

def _rfc3339(value: str | None) -> str:
    if not value:
        return _utc_now_iso()
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _utc_now_iso()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_feed(dist_dir: str | Path, items: list[dict] | None = None,
               base_url: str | None = None, *, title: str | None = None,
               feed_path: str = "feed.xml", updated: str | None = None) -> Path:
    """写站点 Atom feed（默认 ``<dist>/feed.xml``）。

    ``items`` 为新闻条目（title/url/summary/published_at/source_name/category/
    direction），可选；不传时输出空 feed。条目按其 ``direction`` 打 category 标签。
    """
    dist = Path(dist_dir)
    base = site_base_url(base_url)
    site = _site_cfg({})
    feed_title = _plain(title or f"{site.get('name') or SITE_NAME} · AI 新闻", 100)
    feed_href = _abs_url("/feed.xml", base)
    rows = [it for it in (items or []) if isinstance(it, dict) and it.get("title")]
    stamps = [_rfc3339(it.get("published_at") or it.get("date")) for it in rows]
    feed_updated = _rfc3339(updated) if updated else (max(stamps) if stamps else _utc_now_iso())

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{_xml_escape(feed_title)}</title>",
        f"  <id>{_xml_escape(_abs_url('/', base))}</id>",
        f"  <link href=\"{_xml_escape(feed_href)}\" rel=\"self\" type=\"application/atom+xml\"/>",
        f"  <link href=\"{_xml_escape(_abs_url('/', base))}\" rel=\"alternate\" type=\"text/html\"/>",
        f"  <updated>{feed_updated}</updated>",
        f"  <author><name>{_xml_escape(site.get('name') or SITE_NAME)}</name></author>",
    ]
    for it in rows[:200]:
        link = _abs_url(it.get("url") or "/", base)
        stamp = _rfc3339(it.get("published_at") or it.get("date"))
        entry_title = _plain(it.get("title"), 200)
        summary = _plain(it.get("summary") or it.get("subtitle") or "", 500)
        lines.append("  <entry>")
        lines.append(f"    <title>{_xml_escape(entry_title)}</title>")
        lines.append(f"    <id>{_xml_escape(link)}</id>")
        lines.append(f"    <link href=\"{_xml_escape(link)}\"/>")
        lines.append(f"    <updated>{stamp}</updated>")
        lines.append(f"    <published>{stamp}</published>")
        if summary:
            lines.append(f"    <summary type=\"text\">{_xml_escape(summary)}</summary>")
        source = _plain(it.get("source_name") or "", 80)
        if source:
            lines.append(f"    <source><title>{_xml_escape(source)}</title></source>")
        direction = _clean(it.get("direction") or it.get("tag") or it.get("category") or "", 40)
        if direction:
            lines.append(f"    <category term=\"{_xml_escape(direction)}\"/>")
        lines.append("  </entry>")
    lines.append("</feed>")
    lines.append("")
    target = dist / feed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _static_assets_version() -> str:
    """与 sitegen._assets_version 同口径的静态资源指纹（避免循环依赖）。"""
    try:
        import hashlib
        from .config import STATIC_DIR
        h = hashlib.sha256()
        for f in sorted((STATIC_DIR / "assets").iterdir()):
            if f.is_file():
                h.update(f.read_bytes())
        return h.hexdigest()[:8]
    except Exception:
        return ""


def render_404(env, dist_dir: str | Path) -> Path:
    """用构建期的 Jinja Environment 渲染 ``templates/404.html`` 到 ``dist/404.html``。

    Args:
        env: Jinja2 ``Environment``（需能按名加载 ``404.html``）。
        dist_dir: 输出目录（通常 ``config.DIST_DIR``）。

    Returns:
        Path: 生成的 ``<dist>/404.html``。
    """
    site = _site_cfg({})
    reg = _registry()
    meta = {
        "last_collected_at": None,
        "methodology_version": (reg.get("methodology") or {}).get("version", ""),
        "retention": reg.get("retention") or {},
        "counts": {},
    }
    assets_v = str(os.environ.get("DM_ASSETS_VERSION") or _static_assets_version())
    html = env.get_template("404.html").render(
        site=site, assets_v=assets_v, meta=meta)
    target = Path(dist_dir) / "404.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def dump_jsonld(meta: dict) -> str:
    """把 page_meta()['jsonld'] 序列化为可直接内嵌 ``<script>`` 的 JSON。"""
    value = (meta or {}).get("jsonld")
    if not value:
        return ""
    # 防止 </script> 提前闭合（JSON-LD 内嵌场景）
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/")
