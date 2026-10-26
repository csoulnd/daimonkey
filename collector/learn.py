"""学习内容（/learn）加载、校验与渲染。

内容主存储：``content/learn/<series>/``，与站点同仓版本化。
知识库不参与渲染；单向自动归档尚在规划中。

对外函数：
    load_all()                  -> list[series dict]
    build(env, common, notes)   -> dict 摘要
"""
from __future__ import annotations

import html
import json
import logging
import posixpath
import re
import shutil
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import bleach
import markdown
from markdown.extensions.toc import slugify_unicode
import yaml

from . import knowledge, seo
from .config import DIST_DIR, ROOT, now_iso

log = logging.getLogger("daimonkey.learn")

LEARN_DIR = ROOT / "content" / "learn"
ASSET_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"}
ALLOWED_TYPES = {"guide", "map", "lab"}
ALLOWED_STATUS = {"draft", "active", "archived"}
SKIP_FILES = {"README.md", "ROADMAP.md"}


class LearnError(ValueError):
    """学习内容结构/校验错误；由 sitegen 捕获并保留旧页面。"""


# ---------------------------------------------------------------------------
# 读取与校验
# ---------------------------------------------------------------------------

def _updated_at(path: Path) -> str:
    """优先取 git 提交时间（内容仓库以提交为准），未提交时退回 mtime。"""
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:  # 测试/临时目录不在仓库内
        rel = ""
    if rel:
        try:
            out = subprocess.check_output(
                ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT),
                 "log", "-1", "--format=%cI", "--", rel],
                text=True, encoding="utf-8", timeout=10,
                stderr=subprocess.DEVNULL).strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _read_time(body: str) -> int:
    plain = re.sub(r"[#>*`\-\[\]()!|]", "", body)
    chars = len(re.sub(r"\s+", "", plain))
    return max(1, round(chars / 400))


def _as_list(meta: dict, key: str, where: str) -> list[str]:
    value = meta.get(key)
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise LearnError(f"{where}: frontmatter.{key} 必须是列表")
    return [str(v) for v in value]


def _read_article(path: Path, series_id: str, slug: str) -> dict:
    where = f"{series_id}/{slug}"
    text = path.read_text(encoding="utf-8")
    meta, body = knowledge.split_frontmatter(text)
    if not isinstance(meta, dict) or not meta:
        raise LearnError(f"{where}: 缺少 YAML frontmatter")

    title = str(meta.get("title") or "").strip()
    one_liner = str(meta.get("one_liner") or "").strip()
    kind = str(meta.get("type") or "").strip()
    domain = str(meta.get("domain") or "").strip()
    status = str(meta.get("status") or "").strip()

    heading = re.search(r"^#\s+(.+)$", body, re.M)
    if not title and heading:
        title = heading.group(1).strip()
    missing = [k for k, v in (("title", title), ("type", kind), ("domain", domain),
                              ("status", status), ("one_liner", one_liner)) if not v]
    if missing:
        raise LearnError(f"{where}: frontmatter 缺少 {', '.join(missing)}")
    if kind not in ALLOWED_TYPES:
        raise LearnError(f"{where}: type 必须是 {sorted(ALLOWED_TYPES)}")
    if domain != "inference":
        raise LearnError(f"{where}: domain 必须是 inference")
    if status not in ALLOWED_STATUS:
        raise LearnError(f"{where}: status 必须是 {sorted(ALLOWED_STATUS)}")
    if len(one_liner) > 60:
        raise LearnError(f"{where}: one_liner 超过 60 字（当前 {len(one_liner)}）")
    if not heading:
        raise LearnError(f"{where}: 正文缺少 H1")

    return {
        "slug": slug,
        "title": title,
        "type": kind,
        "domain": domain,
        "status": status,
        "one_liner": one_liner,
        "prereq": _as_list(meta, "prereq", where),
        "related": _as_list(meta, "related", where),
        "sources": _as_list(meta, "sources", where),
        "version": str(meta.get("version") or "").strip(),
        "body": body,
        "path": f"{series_id}/{slug}.md",
        "url": f"/learn/{series_id}/{slug}",
        "read_time": _read_time(body),
        "updated": _updated_at(path),
    }


def load_all() -> list[dict]:
    """读取 content/learn/*/series.yaml 与文章；任何结构问题抛 LearnError。"""
    if not LEARN_DIR.is_dir():
        return []
    series_list: list[dict] = []
    for series_dir in sorted(p for p in LEARN_DIR.iterdir() if p.is_dir()):
        cfg_path = series_dir / "series.yaml"
        if not cfg_path.is_file():
            raise LearnError(f"{series_dir.name}: 缺少 series.yaml")
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise LearnError(f"{series_dir.name}/series.yaml: YAML 解析失败: {exc}") from exc
        sid = str(cfg.get("id") or series_dir.name)
        if sid != series_dir.name:
            raise LearnError(f"{series_dir.name}: series.yaml 的 id 必须与目录名一致")
        parts_cfg = cfg.get("parts") or []
        if not isinstance(parts_cfg, list) or not parts_cfg:
            raise LearnError(f"{sid}: parts 不能为空")

        articles: list[dict] = []
        seen: set[str] = set()
        parts: list[dict] = []
        for part_cfg in parts_cfg:
            if not isinstance(part_cfg, dict):
                raise LearnError(f"{sid}: parts 项必须是对象")
            pid = str(part_cfg.get("id") or "").strip()
            ptitle = str(part_cfg.get("title") or pid).strip()
            pdesc = str(part_cfg.get("description") or "").strip()
            chapters = part_cfg.get("chapters") or []
            if not pid or not isinstance(chapters, list) or not chapters:
                raise LearnError(f"{sid}: 分卷 {pid or '?'} 缺少 id 或 chapters")
            part_articles: list[dict] = []
            for raw in chapters:
                if isinstance(raw, str):
                    slug, planned, cfg_title = raw.strip(), False, ""
                elif isinstance(raw, dict):
                    slug = str(raw.get("slug") or "").strip()
                    planned = bool(raw.get("planned"))
                    cfg_title = str(raw.get("title") or "").strip()
                else:
                    raise LearnError(f"{sid}: chapters 项必须是 slug 或对象")
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
                    raise LearnError(f"{sid}: slug 只能是小写字母/数字/连字符: {slug}")
                if slug in seen:
                    raise LearnError(f"{sid}: slug 重复: {slug}")
                path = series_dir / f"{slug}.md"
                if path.is_file():
                    if planned:
                        raise LearnError(f"{sid}/{slug}: 文章已存在，去掉 planned")
                    if cfg_title:
                        raise LearnError(f"{sid}/{slug}: 已发布章节不要在 series.yaml 写 title")
                    art = _read_article(path, sid, slug)
                    art.update({"part_id": pid, "part_title": ptitle,
                                "part_index": len(parts), "index": len(articles),
                                "planned": False})
                    part_articles.append(art)
                    articles.append(art)
                else:
                    if not planned:
                        raise LearnError(
                            f"{sid}: {slug}.md 不存在；规划章节请标 planned: true")
                    if not cfg_title:
                        raise LearnError(f"{sid}/{slug}: planned 章节必须给 title")
                    part_articles.append({
                        "slug": slug, "title": cfg_title, "planned": True,
                        "url": None, "part_id": pid, "part_title": ptitle,
                        "part_index": len(parts),
                    })
                seen.add(slug)
            parts.append({"id": pid, "title": ptitle,
                          "description": pdesc, "chapters": part_articles})

        for path in series_dir.glob("*.md"):
            if path.name in SKIP_FILES:
                continue
            if path.stem not in seen:
                raise LearnError(f"{sid}: {path.name} 未在 series.yaml 中登记")

        by_slug = {a["slug"]: a for a in articles}
        for art in articles:
            for q in art["prereq"]:
                if q not in by_slug:
                    raise LearnError(f"{sid}/{art['slug']}: prereq 不存在: {q}")
                if by_slug[q]["index"] >= art["index"]:
                    raise LearnError(f"{sid}/{art['slug']}: prereq 必须位于本篇之前: {q}")

        count = sum(1 for a in articles if a["status"] != "archived")
        total = sum(len(p["chapters"]) for p in parts)
        updated = max((a["updated"] for a in articles), default="")
        series_list.append({
            "id": sid,
            "title": str(cfg.get("title") or sid).strip(),
            "description": str(cfg.get("description") or "").strip(),
            "parts": parts,
            "articles": articles,
            "count": count,
            "total": total,
            "updated": updated,
        })
    return series_list


# ---------------------------------------------------------------------------
# Markdown 渲染（复用 knowledge 的清洗规则，链接按学习内容重写）
# ---------------------------------------------------------------------------

class _Rewriter(HTMLParser):
    def __init__(self, series_id: str, slug: str, slugs: set[str], kb_known: set[str]):
        super().__init__(convert_charrefs=False)
        self.series_id, self.slug = series_id, slug
        self.slugs, self.kb_known = slugs, kb_known
        self.output: list[str] = []

    def _map(self, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path:
            return value
        path = unquote(parts.path)
        if path.startswith("/"):
            return value
        # 知识库备份里的原子笔记：../knowledge/... 或 knowledge/...
        if path.startswith("knowledge/") and path in self.kb_known:
            return knowledge.note_url(path) + self._suffix(parts)
        target = posixpath.normpath(
            posixpath.join(posixpath.dirname(f"{self.series_id}/{self.slug}.md"), path))
        if target in self.kb_known:
            return knowledge.note_url(target) + self._suffix(parts)
        if target.endswith(".md"):
            prefix = f"{self.series_id}/"
            if target.startswith(prefix):
                stem = target[len(prefix):-3]
                if stem in self.slugs:
                    return f"/learn/{self.series_id}/{stem}" + self._suffix(parts)
            return value
        if target.startswith(f"{self.series_id}/"):
            return "/learn/" + target + self._suffix(parts)
        return value

    @staticmethod
    def _suffix(parts) -> str:
        return (("?" + parts.query) if parts.query else "") + \
               (("#" + parts.fragment) if parts.fragment else "")

    def handle_starttag(self, tag, attrs):
        rewritten = []
        for key, value in attrs:
            if key in ("href", "src") and value:
                value = self._map(value)
            rewritten.append(" " + key + ("=\"" + html.escape(value, quote=True) + "\""
                                          if value is not None else ""))
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


def _render_markdown(body: str, series_id: str, slug: str,
                     slugs: set[str], kb_known: set[str]) -> tuple[str, str]:
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists", "footnotes", "attr_list"],
        extension_configs={"toc": {"permalink": False, "slugify": slugify_unicode,
                                  "toc_depth": "2-3"}})
    fragment = md.convert(body)
    clean = bleach.clean(fragment, tags=knowledge.TAGS, attributes={
        "*": ["id", "class"], "a": ["href", "title"],
        "img": ["src", "alt", "title", "width", "height"],
        "th": ["align"], "td": ["align"], "ol": ["start"], "details": ["open"],
    }, protocols={"http", "https", "mailto"}, strip=True)
    rewrite = _Rewriter(series_id, slug, slugs, kb_known)
    rewrite.feed(clean)
    rewrite.close()
    toc = bleach.clean(md.toc, tags=knowledge.TAGS,
                       attributes={"*": ["class"], "a": ["href"]}, strip=True)
    return "".join(rewrite.output), toc


def _note_one_liner(note: dict) -> str:
    body = note.get("body") or ""
    match = re.search(r"^##\s+一句话解释\s*$\n+(.+?)(?:\n\s*\n|\Z)", body, re.M | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:120]


def _render_diagrams(fragment: str, env) -> str:
    """仅替换已清洗的固定图表标记；文章不能指定模板路径或注入 HTML。"""
    marker = '<pre><code class="language-learn-diagram">inference-stack\n</code></pre>'
    if marker in fragment:
        fragment = fragment.replace(
            marker, env.get_template("_learn_inference_stack.html").render())
    return fragment


def _related_notes(paths: list[str], notes: list[dict]) -> list[dict]:
    by_path = {n["path"]: n for n in notes}
    out = []
    for path in paths:
        note = by_path.get(path)
        if not note:
            log.warning("[learn] related 笔记不存在，已隐藏: %s", path)
            continue
        out.append({"title": note["title"], "url": note["url"],
                    "one_liner": _note_one_liner(note)})
    return out


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def _page(env, common: dict, tpl: str, *, title: str, description: str,
          path: str, **extra) -> str:
    ctx = {**common, **extra}
    meta = seo.page_meta("page", {**ctx, "title": title,
                                  "description": description, "path": path})
    ctx.update({"seo_meta": meta, "seo_jsonld": seo.dump_jsonld(meta)})
    return env.get_template(tpl).render(**ctx)


def _manifest(series_list: list[dict]) -> dict:
    out = []
    for s in series_list:
        parts = []
        for part in s["parts"]:
            parts.append({
                "id": part["id"], "title": part["title"],
                "description": part["description"],
                "chapters": [{
                    "slug": a["slug"], "title": a["title"],
                    "one_liner": a.get("one_liner", ""),
                    "status": "planned" if a.get("planned") else a.get("status", ""),
                    "updated": a.get("updated", ""), "url": a.get("url"),
                    "read_time": a.get("read_time", 0),
                } for a in part["chapters"]],
            })
        out.append({"id": s["id"], "title": s["title"],
                    "description": s["description"], "parts": parts})
    return {"generated_at": now_iso(), "series": out}


def _copy_assets(series_list: list[dict]) -> None:
    for s in series_list:
        src_root = LEARN_DIR / s["id"] / "assets"
        if not src_root.is_dir():
            continue
        for src in src_root.rglob("*"):
            if not src.is_file() or src.suffix.lower() not in ASSET_SUFFIXES:
                continue
            rel = src.relative_to(src_root)
            if rel.parts and rel.parts[0] == "src":
                continue
            dst = DIST_DIR / "learn" / s["id"] / "assets" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)


def _build_introduction_review(env, series_list: list[dict], assets_v: str = "") -> list[tuple[str, str]]:
    """独立评审快照，不加入课程目录或覆盖正式导读。"""
    source = ROOT / "docs" / "reviews" / "learn-introduction-20261024"
    if not source.is_dir():
        return []
    slugs = {a["slug"] for s in series_list for a in s["articles"]}
    versions = []
    for name, title in (("minimal-change", "A · 只改图与路线"),
                        ("previous-rewrite", "B · 上一轮重写版")):
        _, body = knowledge.split_frontmatter((source / f"{name}.md").read_text(encoding="utf-8"))
        fragment, _ = _render_markdown(body, "inference", "introduction", slugs, set())
        versions.append({"id": name, "title": title,
                         "html": _render_diagrams(fragment, env)})
    original = (source / "original.md").read_text(encoding="utf-8")
    return [("design/learn-introduction.html", env.get_template("learn_review.html").render(
        versions=versions, original=original, assets_v=assets_v))]


def build(env, common: dict, notes: list[dict] | None = None) -> dict:
    """渲染 /learn；先把所有页面渲染到内存，再落盘，避免半成品。"""
    series_list = load_all()
    if not series_list:
        return {"series": 0, "articles": 0, "updated": ""}

    notes = notes or []
    kb_known = {n["path"] for n in notes}
    pages: list[tuple[str, str]] = []
    total = 0
    latest = ""

    for series in series_list:
        sid = series["id"]
        slugs = {a["slug"] for a in series["articles"]}
        for i, art in enumerate(series["articles"]):
            fragment, toc = _render_markdown(art["body"], sid, art["slug"], slugs, kb_known)
            fragment = _render_diagrams(fragment, env)
            art["article_html"] = fragment
            art["article_toc"] = toc
            art["related_notes"] = _related_notes(art["related"], notes)
            art["prev"] = series["articles"][i - 1] if i else None
            art["next"] = series["articles"][i + 1] if i + 1 < len(series["articles"]) else None
            total += 1
            latest = max(latest, art["updated"])

        learn_meta = {"count": series["count"], "total": series["total"],
                      "updated": series["updated"],
                      "series_count": len(series_list)}
        for art in series["articles"]:
            pages.append((f"learn/{sid}/{art['slug']}.html", _page(
                env, common, "learn_article.html",
                title=f"{art['title']} · LLM 推理技术基础",
                description=art["one_liner"], path=art["url"],
                learn_page=True, learn_nav=series, learn_meta=learn_meta,
                article=art, prev=art["prev"], next=art["next"],
                article_html=art["article_html"], article_toc=art["article_toc"],
                related_notes=art["related_notes"])))

    pages.append(("learn/index.html", _page(
        env, common, "learn_index.html",
        title="LLM 推理技术基础 · 学习",
        description="从数学与计算基础、模型结构到推理过程、算子与系统优化的学习地图。",
        path="/learn", learn_page=True, learn_series=series_list,
        learn_nav=series_list[0],
        learn_meta={"count": total,
                    "total": sum(s["total"] for s in series_list),
                    "updated": latest,
                    "series_count": len(series_list)})))

    pages.extend(_build_introduction_review(env, series_list, common.get("assets_v", "")))

    for rel, text in pages:
        target = DIST_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    index = DIST_DIR / "learn" / "index.html"
    if index.is_file():
        shutil.copyfile(index, DIST_DIR / "learn.html")

    _copy_assets(series_list)
    (DIST_DIR / "learn" / "manifest.json").write_text(
        json.dumps(_manifest(series_list), ensure_ascii=False,
                   separators=(",", ":")), encoding="utf-8")

    keep = {rel for rel, _ in pages}
    if (DIST_DIR / "learn").is_dir():
        for stale in (DIST_DIR / "learn").rglob("*.html"):
            rel = stale.relative_to(DIST_DIR).as_posix()
            if rel not in keep:
                stale.unlink()

    return {"series": len(series_list), "articles": total, "updated": latest}
