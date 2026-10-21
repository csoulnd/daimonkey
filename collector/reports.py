"""报告厅：汇报报告的模块化框架（网页展示 / PPT 展示）。

内容全部版本化在仓库 `reports/` 目录（与 skills/ 同级，进 git）：

    reports/
    ├── README.md            # 模块说明
    ├── registry.yaml        # 报告清单（新增报告 = 建目录 + 登记一行）
    └── {slug}/
        ├── report.md        #   或 index.html / deck.pptx（见下）
        └── assets…          #   图片等同目录资源（自动拷贝）

内容文件按扩展名分发（`file:` 可显式指定，缺省按 report.md → index.html →
report.html → deck.pptx 顺序探测第一个存在的文件）：
- .md          站内渲染（与知识库同管线的 Markdown → HTML）
- .html/.htm   原样网页：详情页 iframe 嵌入 + 新窗口打开（报告可自带样式/脚本）
- .pptx        演示文稿：详情页提供原件下载（PPT 网页化为后续规划）

建站（sitegen.build → reports.build）产出：
- /reports               报告厅索引（卡片列表）
- /reports/{slug}        报告详情（按内容形态渲染）
- dist/reports/{slug}/   报告目录内资源（图片/pptx/原样 html 等）原样拷贝

知识库（/knowledge，data/knowledge-repo）是撰写报告时的**原材料**来源之一，
不是报告本身；素材引用可登记在清单的 `materials:` 字段，详情页展示为参考材料。

约定：清单缺字段 / 内容文件缺失 / 扩展名不支持 / slug 重复时跳过该条并打印
警告，不阻断建站——报告是内容侧数据，格式错误应只影响自身。
"""
from __future__ import annotations

import html
import posixpath
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import bleach
import markdown
import yaml
from markdown.extensions.toc import slugify_unicode

from .config import DIST_DIR, ROOT
from .knowledge import TAGS

REPORTS_DIR = ROOT / "reports"
REGISTRY_FILE = REPORTS_DIR / "registry.yaml"
REQUIRED_FIELDS = ("slug", "title", "date")
KIND_BY_SUFFIX = {".md": "md", ".html": "html", ".htm": "html", ".pptx": "ppt"}
KIND_NAMES = {"md": "网页", "html": "网页", "ppt": "PPT", "showcase": "展示"}
DEFAULT_FILES = ("report.md", "index.html", "report.html", "deck.pptx")


class _AssetRewriter(HTMLParser):
    """将 Markdown 正文中的相对链接/图片重写到 /reports/{slug}/ 下（构建时已拷贝资源）。"""

    def __init__(self, slug: str):
        super().__init__(convert_charrefs=False)
        self.prefix = f"/reports/{slug}/"
        self.output: list[str] = []

    def _rewrite(self, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path:
            return value
        if parts.path.startswith("/") or parts.path.startswith("#"):
            return value
        suffix = (("?" + parts.query) if parts.query else "") + \
                 (("#" + parts.fragment) if parts.fragment else "")
        target = posixpath.normpath(unquote(parts.path))
        if target.startswith(".."):
            return value
        return self.prefix + quote(target, safe="/") + suffix

    def handle_starttag(self, tag, attrs):
        rewritten = []
        for key, value in attrs:
            if key in ("href", "src") and value:
                value = self._rewrite(value)
            rewritten.append(" " + key + ("=\"" + html.escape(value, quote=True) + "\"" if value is not None else ""))
        if tag == "img":
            rewritten.append(' loading="lazy"')
        self.output.append("<" + tag + "".join(rewritten) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.output.append("</" + tag + ">")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append("&" + name + ";")

    def handle_charref(self, name):
        self.output.append("&#" + name + ";")


def render_report_markdown(body: str, slug: str) -> tuple[str, str]:
    """报告 Markdown → (正文 HTML, 目录 HTML)，清洗与知识库同规则。"""
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists", "footnotes"],
        extension_configs={"toc": {"permalink": False, "slugify": slugify_unicode}})
    fragment = md.convert(body)
    clean = bleach.clean(fragment, tags=TAGS, attributes={
        "*": ["id", "class"], "a": ["href", "title"],
        "img": ["src", "alt", "title", "width", "height"],
        "th": ["align"], "td": ["align"], "ol": ["start"], "details": ["open"],
    }, protocols={"http", "https", "mailto"}, strip=True)
    rewrite = _AssetRewriter(slug)
    rewrite.feed(clean)
    rewrite.close()
    return "".join(rewrite.output), md.toc


def resolve_content(slug: str, file_field: str | None) -> Path | None:
    """定位报告内容文件：显式 file 优先（须存在），否则按默认名单探测。"""
    directory = REPORTS_DIR / slug
    if file_field:
        candidate = directory / file_field
        return candidate if candidate.is_file() else None
    for name in DEFAULT_FILES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def load_registry() -> tuple[list[dict], list[str]]:
    """读取报告清单；返回 (有效报告列表按日期倒序, 警告列表)。"""
    warnings: list[str] = []
    if not REGISTRY_FILE.is_file():
        return [], []
    try:
        data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return [], [f"reports/registry.yaml 解析失败: {e}"]
    entries = data.get("reports") or []
    seen: set[str] = set()
    reports: list[dict] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            warnings.append(f"第 {i + 1} 条不是映射，跳过")
            continue
        missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            warnings.append(f"第 {i + 1} 条缺字段 {missing}，跳过")
            continue
        slug = str(entry["slug"]).strip()
        if slug in seen:
            warnings.append(f"slug 重复: {slug}，跳过")
            continue
        if not (REPORTS_DIR / slug).is_dir():
            warnings.append(f"{slug}: 目录 reports/{slug}/ 不存在，跳过")
            continue
        content = resolve_content(slug, entry.get("file"))
        showcase_file = (REPORTS_DIR / slug / "showcase.yaml")
        if content is None and not showcase_file.is_file():
            if entry.get("file"):
                hint = f"file: {entry['file']} 不存在"
            else:
                existing = sorted(f.name for f in (REPORTS_DIR / slug).iterdir()
                                  if f.is_file() and not f.name.startswith("."))
                hint = (f"未找到内容文件（{'/'.join(DEFAULT_FILES)} 或 showcase.yaml）"
                        + (f"；目录现有: {', '.join(existing)}" if existing else ""))
            warnings.append(f"{slug}: {hint}，跳过")
            continue
        if content is None:                      # 展示型报告：内容由 showcase 模块导入
            kind, content = "showcase", showcase_file
        else:
            kind = KIND_BY_SUFFIX.get(content.suffix.lower())
        if kind is None:
            warnings.append(f"{slug}: 不支持的内容格式 {content.suffix}"
                            f"（支持 .md / .html / .pptx / showcase.yaml），跳过")
            continue
        seen.add(slug)
        reports.append({
            "slug": slug,
            "title": str(entry["title"]),
            "date": str(entry["date"]),
            "kind": kind,
            "kind_name": KIND_NAMES[kind],
            "content": content,
            "file_name": content.name,
            "summary": str(entry.get("summary", "")),
            "tags": [str(t) for t in (entry.get("tags") or [])],
            "materials": [str(m) for m in (entry.get("materials") or [])],
        })
    reports.sort(key=lambda r: (r["date"], r["slug"]), reverse=True)
    return reports, warnings


def _prepare_detail(report: dict) -> tuple[dict, str, str]:
    """读取报告内容：返回 (页面上下文, 正文 HTML, 目录 HTML)。

    md  → 站内渲染；html → iframe 嵌入原样网页；ppt → 原件下载。
    """
    r = dict(report)
    r.pop("content", None)      # Path 不可进模板上下文
    body_html = toc = ""
    if r["kind"] == "md":
        body_html, toc = render_report_markdown(
            report["content"].read_text(encoding="utf-8"), r["slug"])
    elif r["kind"] == "html":
        r["frame_url"] = f"/reports/{r['slug']}/{quote(r['file_name'])}"
    else:  # ppt
        size = report["content"].stat().st_size
        r["file_kb"] = max(1, round(size / 1024))
        r["file_mb"] = round(size / 1024 / 1024, 2) if size >= 1024 * 1024 else None
    return r, body_html, toc


def _copy_assets(slug: str) -> None:
    """报告目录内资源原样拷贝到 dist/reports/{slug}/（图片、pptx、原样 html 等）。

    仅构建配置与内部文件不拷贝：.md（构建期渲染）、showcase.yaml（构建映射）、
    content-issues.md（.md 已排除，交回研究侧的内部记录）。
    """
    src = REPORTS_DIR / slug
    dst = DIST_DIR / "reports" / slug
    if dst.exists():
        shutil.rmtree(dst)   # 从零重建，避免已删除/改名资源在 dist 残留
    for f in src.rglob("*"):
        if not f.is_file() or f.suffix.lower() == ".md" or f.is_symlink():
            continue
        if f.name == "showcase.yaml":
            continue
        target = dst / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


def build(env, common: dict, page_seo=None) -> int:
    """渲染报告厅索引与全部报告页，拷贝资源；返回已发布报告数。

    page_seo: 可选回调 ``(kind, ctx) -> dict``，用于注入 seo_meta / seo_jsonld。
    报告厅属于私有区，站点构建传入 sitegen._seo_vars 生成 noindex 元数据。
    """
    reports, warnings = load_registry()
    for w in warnings:
        print(f"[reports] 警告: {w}")

    def render(tpl: str, out: str, **ctx) -> None:
        page_ctx = {**common, "reports_page": True,
                    "reports_total": len(reports), **ctx}
        if page_seo is not None:
            try:
                page_ctx.update(page_seo("reports", page_ctx) or {})
            except Exception:  # SEO 是增强项，失败不应阻断报告页渲染
                pass
        page = env.get_template(tpl).render(**page_ctx)
        p = DIST_DIR / out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(page, encoding="utf-8")

    # 索引页的清单条目不含 Path/正文（模板可见字段已足够）
    index_reports = [{k: v for k, v in r.items() if k != "content"}
                     for r in reports]
    render("reports_index.html", "reports/index.html", reports=index_reports)
    # 无扩展名路径回退：/reports 解析到 dist/reports.html（与 /knowledge 同模式）
    shutil.copyfile(DIST_DIR / "reports" / "index.html",
                    DIST_DIR / "reports.html")
    published: set[str] = set()
    for report in reports:
        if report["kind"] == "showcase":
            # 产品调研展示：内容在构建时从知识库快照导入（collector/showcase.py）
            from . import showcase as showcase_lib
            ctx, warnings = showcase_lib.load_showcase(REPORTS_DIR / report["slug"])
            for w in warnings:
                print(f"[reports] {w}")
            render("report_showcase.html", f"reports/{report['slug']}.html",
                   r={k: v for k, v in report.items() if k != "content"}, **ctx)
            # 讲稿页（登录可见，serve.py 对 /notes/ 前缀做鉴权；公开页不放入口）
            notes_file = REPORTS_DIR / report["slug"] / "notes.md"
            if notes_file.is_file():
                notes_html = showcase_lib.render_notes(notes_file, set(), "build")
                render("report_notes.html", f"notes/{report['slug']}.html",
                       notes_title=ctx.get("sc_title", report["title"]),
                       notes_html=notes_html)
            _copy_assets(report["slug"])
            published.add(report["slug"])
            continue
        r, body_html, toc = _prepare_detail(report)
        render("report_detail.html", f"reports/{report['slug']}.html",
               r=r, body_html=body_html, article_toc=toc)
        _copy_assets(report["slug"])
        published.add(report["slug"])
    _remove_stale_pages(published)
    return len(reports)


def _remove_stale_pages(published: set[str]) -> None:
    """清理 dist 中已从 registry 移除的报告页（不触碰其它内容，index 除外）。"""
    root = DIST_DIR / "reports"
    if not root.is_dir():
        return
    for f in root.glob("*.html"):
        if f.stem == "index" or f.stem in published:
            continue
        f.unlink()
        gz = f.with_name(f.name + ".gz")
        if gz.exists():
            gz.unlink()
    for d in root.iterdir():
        if d.is_dir() and d.name not in published:
            shutil.rmtree(d, ignore_errors=True)
    # 讲稿页（dist/notes/）：与已发布集合同步清理
    notes_root = DIST_DIR / "notes"
    if notes_root.is_dir():
        for f in notes_root.glob("*.html"):
            if f.stem not in published:
                f.unlink()
                gz = f.with_name(f.name + ".gz")
                if gz.exists():
                    gz.unlink()
