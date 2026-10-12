"""产品调研展示（showcase）：汇报型展示页——构建时从知识库快照导入 Markdown。

定位：InferTechNote 是研究内容的唯一来源；本模块把多份调研文档**选材、提炼、
聚合**为一页汇报（四段式：产品 A → 产品 B → 对比 → 架构提炼）。事实内容全部
构建时导入；呈现层（开篇概览、章要点、讲稿）只做提炼与组织，不新造事实。

报告目录（reports/{slug}/）：
    showcase.yaml   汇报结构：chapters（章）/ docs（源文档表）/ diagrams（交互图表）
    diagram*.svg    各交互图（呈现层研究视图）
    notes.md        讲稿（逐章口播稿，渲染到 /notes/{slug}，登录可见）
    content-issues.md  内容问题记录（交回研究侧）

导入规则（与 research→presentation 边界一致）：
- 章节抽取：标题模糊匹配，缺章节只警告并在页面标注，不阻断建站；
- table_filter：对比类表格按行收窄（只保留指定产品行）；
- flow 模式：图 + 执行步骤合并为一个卡片（点步骤高亮同图节点，点节点看说明）；
- 链接重写：库内 → /knowledge/notes；库外 → GitHub 版本固定链接；外链保留；
- Mermaid 不入页面（交互图承载），判断标记样式化，「研究阶段」小节不展示。
"""
from __future__ import annotations

import hashlib
import html as html_mod
import os
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import bleach
import markdown
import yaml
from markdown.extensions.toc import slugify_unicode

from .knowledge import REPO_DIR as KB_REPO, REPO_URL, TAGS, displayable, note_url

MERMAID_RE = re.compile(r"```mermaid\b[\s\S]*?```\n?")
HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.M)
JUDGMENT_MARK = "<strong>研究判断：</strong>"
PARA_RE = re.compile(r"<p>(.*?)</p>", re.S)


def _norm_heading(text: str) -> str:
    """标题归一化：去除强调符号与各类引号，便于跨文档模糊匹配。"""
    return re.sub(r'[「」“”"\'*`#\s]', "", text)


def split_segments(body: str) -> list[dict]:
    """正文按 ## / ### / #### 标题切段：{level, heading, text}。"""
    matches = list(HEADING_RE.finditer(body))
    segments = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        end = len(body)
        for later in matches[i + 1:]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        segments.append({"level": level, "heading": m.group(2).strip(),
                         "text": body[m.end():end].strip()})
    return segments


def extract_section(body: str, heading: str) -> tuple[str | None, str | None]:
    """按标题（含子标题）抽取段落；返回 (markdown, 警告)。"""
    target = _norm_heading(heading)
    for seg in split_segments(body):
        got = _norm_heading(seg["heading"])
        if got == target or target in got or got in target:
            return seg["text"], None
    return None, f"未找到章节「{heading}」"


# ---- 表格行收窄（对比章只保留目标产品行） ----

def filter_table_rows(md_text: str, keywords: list[str]) -> str:
    """按首列关键词过滤 Markdown 表格数据行（表头与分隔行保留）。"""
    lines = md_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i])
            i += 1
        if len(block) < 2:
            out.extend(block)
            continue
        kept = [block[0], block[1]]
        for row in block[2:]:
            first_cell = row.strip().strip("|").split("|")[0].strip()
            if any(kw in first_cell for kw in keywords):
                kept.append(row)
        out.extend(kept)
    return "\n".join(out)


# ---- 链接重写：仓库内 → 知识库页面；库外 → 版本固定 GitHub ----

def _github_blob(rel_path: str, commit: str) -> str:
    return f"{REPO_URL}/blob/{commit}/{quote(rel_path, safe='/')}"


class _LinkRewriter(HTMLParser):
    def __init__(self, doc_rel: str, known: set[str], commit: str):
        super().__init__(convert_charrefs=False)
        self.doc_rel = doc_rel
        self.known = known
        self.commit = commit
        self.output: list[str] = []

    def _rewrite(self, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path:
            return value
        path = unquote(parts.path)
        if path.startswith("/") or path.startswith("#"):
            return value
        target = posixpath.normpath(posixpath.join(
            posixpath.dirname(self.doc_rel), path))
        suffix = (("?" + parts.query) if parts.query else "") + \
                 (("#" + parts.fragment) if parts.fragment else "")
        if target == ".." or target.startswith("../"):
            return _github_blob(self.doc_rel, self.commit) + suffix
        if target in self.known:
            return note_url(target) + suffix
        if target.rstrip("/") + "/README.md" in self.known:
            return note_url(target.rstrip("/") + "/README.md") + suffix
        return _github_blob(target, self.commit) + suffix

    def handle_starttag(self, tag, attrs):
        rewritten = []
        for key, value in attrs:
            if key in ("href", "src") and value:
                value = self._rewrite(value)
            rewritten.append(" " + key + ("=\"" + html_mod.escape(value, quote=True) + "\"" if value is not None else ""))
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


def _mark_judgments(html: str) -> str:
    """「研究判断」与事实陈述视觉区分（只加样式，不改文案）。"""
    def one(match: re.Match) -> str:
        inner = match.group(1)
        if JUDGMENT_MARK not in inner:
            return match.group(0)
        if inner.startswith(JUDGMENT_MARK):
            return f'<p class="mark-judgment">{inner}</p>'
        before, _, after = inner.partition(JUDGMENT_MARK)
        return (f'<p>{before}<span class="mark-judgment-inline">{JUDGMENT_MARK}'
                f'{after}</span></p>')
    return PARA_RE.sub(one, html)


def render_md(text: str, doc_rel: str, known: set[str], commit: str) -> str:
    """Markdown → 清洗后的 HTML（链接重写 + 研究判断标记）。"""
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "sane_lists", "footnotes"],
        extension_configs={})
    fragment = md.convert(text)
    clean = bleach.clean(fragment, tags=TAGS, attributes={
        "*": ["id", "class"], "a": ["href", "title"],
        "img": ["src", "alt", "title", "width", "height"],
        "th": ["align"], "td": ["align"], "ol": ["start"], "details": ["open"],
    }, protocols={"http", "https", "mailto"}, strip=True)
    rewrite = _LinkRewriter(doc_rel, known, commit)
    rewrite.feed(clean)
    rewrite.close()
    return _mark_judgments("".join(rewrite.output))


def render_inline(text: str, doc_rel: str, known: set[str], commit: str) -> str:
    """行内片段（要点卡文字）：渲染后去掉外层 <p>。"""
    out = render_md(text, doc_rel, known, commit).strip()
    if out.startswith("<p>") and out.endswith("</p>") and out.count("<p>") == 1:
        out = out[3:-4]
    return out


# ---- 结构化抽取：组件表 / 步骤列表 ----

def parse_component_table(body: str, header_keyword: str = "关键部分") -> dict[str, str]:
    """提取组件/职责表：{行首列: 其余列合并}（说明引自文档、单一来源）。"""
    rows: dict[str, str] = {}
    header_seen = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header_seen = False if stripped else header_seen
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if not header_seen:
            if header_keyword in cells[0]:
                header_seen = True
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = " ｜ ".join(cells[1:])
    return rows


def extract_steps(section_md: str, known: set[str], doc_rel: str,
                  commit: str) -> list[str]:
    """从章节 md 提取有序列表步骤（含续行），返回渲染后的 HTML 列表。"""
    steps: list[str] = []
    current: list[str] = []
    for line in section_md.splitlines():
        if re.match(r"^\d+\.\s+", line):
            if current:
                steps.append("\n".join(current).strip())
            current = [re.sub(r"^\d+\.\s+", "", line)]
        elif current:
            if line.strip():
                current.append(line)
            else:
                steps.append("\n".join(current).strip())
                current = []
    if current:
        steps.append("\n".join(current).strip())
    return [render_md(s, doc_rel, known, commit) for s in steps]


# ---- 知识库环境 ----

def _known_paths() -> set[str]:
    known: set[str] = set()
    if not KB_REPO.is_dir():
        return known
    for root, _dirs, files in os.walk(KB_REPO):
        if "/.git" in root:
            continue
        for name in files:
            if not name.endswith(".md"):
                continue
            rel = Path(root, name).relative_to(KB_REPO).as_posix()
            if displayable(rel):
                known.add(rel)
    return known


def _commit() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "-C", str(KB_REPO), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "main"


# ---- 主入口 ----

def _load_docs(conf: dict, known: set[str], commit: str,
               report_dir: Path) -> tuple[dict, list[str]]:
    """加载 docs 注册表中的源文档：{key: {rel, path, body, meta, kb_url, gh_url, label}}。

    支持两类来源：
    - 知识库路径（默认）：相对 data/knowledge-repo，链接指向 /knowledge 页面 + GitHub 固定链接；
    - 本地素材（`local:` 前缀）：相对报告目录（如来源素材文档），链接指向 doc_urls
      登记的外部官方原文。
    """
    from .knowledge import split_frontmatter
    docs: dict[str, dict] = {}
    warnings: list[str] = []
    urls = conf.get("doc_urls") or {}
    for key, val in (conf.get("docs") or {}).items():
        rel = str(val)
        local = rel.startswith("local:")
        path = report_dir / rel[len("local:"):] if local else KB_REPO / rel
        if not path.is_file():
            warnings.append(f"[showcase] 源文档不存在: {rel}（知识库未同步或素材缺失）")
            continue
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        if local:
            kb_url, gh_url, label = str(urls.get(str(key), "")), "", "官方原文"
        else:
            kb_url = note_url(rel)
            gh_url = _github_blob(rel, commit)
            label = "知识库原文"
        docs[str(key)] = {"rel": rel, "path": path, "body": body, "meta": meta,
                          "kb_url": kb_url, "gh_url": gh_url, "label": label,
                          "local": local}
    return docs, warnings


def _load_diagrams(conf: dict, report_dir: Path, docs: dict,
                   known: set[str], commit: str) -> tuple[dict, list[str]]:
    """加载 diagrams 注册表：svg 内容 + 组件说明（desc_key → 文档表行）。"""
    diagrams: dict[str, dict] = {}
    warnings: list[str] = []
    for key, spec in (conf.get("diagrams") or {}).items():
        key = str(key)
        d = dict(spec)
        svg_file = report_dir / str(d.get("file", "diagram.svg"))
        d["svg"] = svg_file.read_text(encoding="utf-8") if svg_file.is_file() else ""
        if not d["svg"]:
            warnings.append(f"[showcase] 缺少交互图文件: {d.get('file')}")
        table_cache: dict[tuple[str, str], dict] = {}

        def table_of(doc_key: str | None, keyword: str) -> tuple[dict, dict | None]:
            doc = docs.get(str(doc_key or ""))
            if doc is None:
                return {}, None
            ck = (str(doc_key), keyword)
            if ck not in table_cache:
                table_cache[ck] = parse_component_table(doc["body"], keyword)
            return table_cache[ck], doc

        components = []
        for comp in d.get("components") or []:
            ckey = comp.get("desc_key")
            keyword = str(comp.get("desc_table_header")
                          or d.get("desc_table_header", "关键部分"))
            table, table_doc = table_of(comp.get("table") or d.get("desc_table_from"),
                                        keyword)
            desc_html = ""
            if ckey and table_doc:
                keys = ckey if isinstance(ckey, list) else [ckey]
                parts = []
                for k in keys:
                    row = table.get(str(k)) or next(
                        (v for kk, v in table.items()
                         if _norm_heading(str(k)) in _norm_heading(kk)), None)
                    if row is None:
                        warnings.append(f"[showcase] 组件表未找到行: {k}")
                    else:
                        parts.append(render_inline(row, table_doc["rel"], known, commit))
                desc_html = "<br>".join(parts)
            components.append({**comp, "desc_html": desc_html,
                               "desc_source": "doc-table" if desc_html else "view-note"})
        d["components"] = components
        default_doc = docs.get(str(d.get("desc_table_from", "")))
        d["doc_url"] = default_doc["kb_url"] if default_doc else ""
        diagrams[key] = d
    return diagrams, warnings


def _render_points(points: list | None, docs: dict, known: set[str], commit: str,
                   warnings: list[str]) -> list[dict]:
    """要点卡：呈现层提炼；文本为行内 Markdown，可附 ★ 原文锚点。"""
    out = []
    for p in points or []:
        if isinstance(p, str):
            p = {"text": p}
        anchor = p.get("anchor")  # "doc_key#标题"
        href = ""
        if anchor:
            key, _, heading = str(anchor).partition("#")
            doc = docs.get(key)
            if doc and heading and not doc.get("local"):
                href = doc["kb_url"] + "#" + quote(slugify_unicode(heading, "-"), safe="")
            elif doc:
                href = doc["kb_url"]
            else:
                warnings.append(f"[showcase] 要点锚点引用了未知文档: {key}")
        rel = docs.get(str(anchor).partition("#")[0], {}).get("rel", "")
        out.append({
            "html": render_inline(str(p.get("text", "")), rel, known, commit),
            "kind": str(p.get("kind", "plain")),
            "href": href,
        })
    return out


def _build_section(spec: dict, docs: dict, diagrams: dict, known: set[str],
                   commit: str, warnings: list[str],
                   section_source) -> dict | None:
    """构建一个小节：full 导入 / diagram / flow（文档抽取或 yaml 书写步骤）/ fold。"""
    doc_key = str(spec.get("from", ""))
    doc = docs.get(doc_key)
    if doc is None:
        warnings.append(f"[showcase] 章节引用了未知文档: {doc_key}")
        return None
    heading = str(spec.get("heading", ""))
    item = {"id": str(spec.get("id", "")), "title": str(spec.get("title", "")),
            "mode": str(spec.get("mode", "full")),
            "auxiliary": bool(spec.get("auxiliary")),
            "missing": None, "step_highlights": None,
            "steps_note": str(spec.get("steps_note", "")),
            "src": section_source(doc_key, heading)}
    rel, body = doc["rel"], doc["body"]
    if item["mode"] == "fold":
        items = []
        for fold in spec.get("items") or []:
            fdoc = docs.get(str(fold.get("from", "")))
            if fdoc is None:
                warnings.append(f"[showcase] 折叠项引用未知文档: {fold.get('from')}")
                continue
            seg, warn = extract_section(fdoc["body"], str(fold.get("heading", "")))
            if warn:
                warnings.append(f"[showcase] {warn}（文档可能已变更）")
                continue
            items.append({"label": str(fold.get("label", "")),
                          "html": render_md(MERMAID_RE.sub("", seg).strip(),
                                            fdoc["rel"], known, commit)})
        item["folds"] = items
    elif item["mode"] in ("diagram", "flow"):
        dkey = str(spec.get("diagram", ""))
        diagram = diagrams.get(dkey)
        if diagram is None:
            warnings.append(f"[showcase] 章节引用了未知图: {dkey}")
            item["missing"] = f"未知图 {dkey}"
            return item
        item["diagram"] = diagram
        if item["mode"] == "flow":
            authored = spec.get("steps")
            if authored:
                # 书写的步骤（跨产品合并图）：每步可含 alibaba/amazon 双栏文案与高亮
                steps, highlights = [], {}
                for i, st in enumerate(authored, start=1):
                    parts = []
                    if st.get("alibaba"):
                        parts.append('<p><span class="sc-step-tag">阿里云</span>'
                                     + render_inline(str(st["alibaba"]), rel, known, commit)
                                     + "</p>")
                    if st.get("amazon"):
                        parts.append('<p><span class="sc-step-tag">Amazon</span>'
                                     + render_inline(str(st["amazon"]), rel, known, commit)
                                     + "</p>")
                    if st.get("text"):
                        parts.append("<p>" + render_inline(str(st["text"]), rel, known,
                                                           commit) + "</p>")
                    steps.append("".join(parts))
                    if st.get("highlights"):
                        highlights[str(i)] = [str(h) for h in st["highlights"]]
                item["steps"] = steps
                item["step_highlights"] = highlights
            else:
                seg, warn = extract_section(body, heading)
                if warn:
                    warnings.append(f"[showcase] {warn}（文档可能已变更）")
                    item["steps"] = []
                else:
                    item["steps"] = extract_steps(
                        MERMAID_RE.sub("", seg).strip(), known, rel, commit)
    else:
        seg, warn = extract_section(body, heading)
        if warn:
            warnings.append(f"[showcase] {warn}（文档可能已变更）")
            item["missing"] = warn
        else:
            md_text = MERMAID_RE.sub("", seg).strip()
            flt = spec.get("table_filter")
            if flt:
                md_text = filter_table_rows(md_text, [str(k) for k in flt])
            if spec.get("compact"):
                # 精简节：去掉首行导语（文档的"阅读路线"类句子）
                lines = md_text.splitlines()
                if lines and not lines[0].strip().startswith(("|", "#", "-", "*")):
                    md_text = "\n".join(lines[1:]).strip()
            item["html"] = render_md(md_text, rel, known, commit)
    return item


def load_showcase(report_dir: Path) -> tuple[dict, list[str]]:
    """读取报告目录的 showcase.yaml，导入并组织汇报内容。"""
    warnings: list[str] = []
    conf_file = report_dir / "showcase.yaml"
    try:
        conf = (yaml.safe_load(conf_file.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError) as e:
        return {"showcase_error": f"showcase.yaml 读取失败: {e}"}, [str(e)]
    conf = conf.get("showcase") or {}
    commit = _commit()
    known = _known_paths()
    docs, w = _load_docs(conf, known, commit, report_dir)
    warnings.extend(w)
    if not docs:
        return {"showcase_error": "未配置可用的源文档（先同步知识库）"}, warnings
    diagrams, w = _load_diagrams(conf, report_dir, docs, known, commit)
    warnings.extend(w)

    def section_source(doc_key: str, heading: str) -> dict:
        doc = docs.get(doc_key, {})
        anchor = ""
        if heading and not doc.get("local"):
            anchor = quote(slugify_unicode(heading, "-"), safe="")
        return {
            "kb_url": doc.get("kb_url", ""),
            "gh_url": doc.get("gh_url", ""),
            "label": doc.get("label", "知识库原文"),
            "anchor": anchor,
        }

    chapters = []
    for ch_spec in conf.get("chapters") or []:
        chapter = {"id": str(ch_spec["id"]), "num": str(ch_spec.get("num", "")),
                   "title": str(ch_spec.get("title", "")),
                   "gist": str(ch_spec.get("gist", "")),
                   "lead": str(ch_spec.get("lead", "")),
                   "points": _render_points(ch_spec.get("points"), docs, known,
                                            commit, warnings),
                   "sections": []}
        used_docs: list[str] = []
        for spec in ch_spec.get("sections") or []:
            item = _build_section(spec, docs, diagrams, known, commit,
                                  warnings, section_source)
            if item is None:
                continue
            doc_key = str(spec.get("from", ""))
            if doc_key not in used_docs:
                used_docs.append(doc_key)
            chapter["sections"].append(item)
        chapter["src_links"] = [{
            "label": (docs[k]["meta"].get("title") or docs[k]["rel"])
                     if docs[k]["local"] else docs[k]["rel"].split("/")[-2],
            "kb_url": docs[k]["kb_url"],
            "gh_url": docs[k]["gh_url"],
            "link_label": docs[k]["label"]} for k in used_docs]
        chapters.append(chapter)

    # 架构总览（报告摘要之后的独立单元：多产品合并图 + 书写的核心流程步骤）
    arch_overview = None
    if conf.get("arch_overview"):
        arch_overview = _build_section(conf["arch_overview"], docs, diagrams,
                                       known, commit, warnings, section_source)

    # 参考来源：链接行 + 折叠表
    sources_links = []
    for d in docs.values():
        if d["local"]:
            label = str(d["meta"].get("title") or d["rel"])
        else:
            label = f"{Path(d['rel']).parent.name}/{Path(d['rel']).name}"
        sources_links.append({"label": label, "kb_url": d["kb_url"],
                              "gh_url": d["gh_url"], "link_label": d["label"]})
    sources_folds = []
    for fold in conf.get("sources_folds") or []:
        doc = docs.get(str(fold.get("from", "")))
        if doc is None:
            warnings.append(f"[showcase] 来源折叠引用未知文档: {fold.get('from')}")
            continue
        seg, warn = extract_section(doc["body"], str(fold.get("heading", "参考来源")))
        if warn:
            warnings.append(f"[showcase] {warn}（文档可能已变更）")
            continue
        sources_folds.append({"label": str(fold.get("label", "来源表")),
                              "html": render_md(MERMAID_RE.sub("", seg).strip(),
                                                doc["rel"], known, commit)})

    # 附件（如原始演示文稿）：注册为报告页的下载条，注明版本口径
    attachments = []
    for att in conf.get("attachments") or []:
        fname = str(att.get("file", ""))
        fpath = report_dir / fname
        if not fname or not fpath.is_file():
            warnings.append(f"[showcase] 附件不存在: {fname}")
            continue
        attachments.append({
            "label": str(att.get("label", fname)),
            "note": str(att.get("note", "")),
            "file": fname,
            "url": f"/reports/{report_dir.name}/{quote(fname)}",
            "size_kb": max(1, round(fpath.stat().st_size / 1024)),
        })

    # 演示文稿网页版：构建时解析 pptx（文字/表格重建为可翻页幻灯片）
    slides_ctx = None
    sl_conf = conf.get("slides")
    if sl_conf:
        fname = str(sl_conf.get("file", ""))
        fpath = report_dir / fname
        if not fpath.is_file():
            warnings.append(f"[showcase] 演示文稿不存在: {fname}")
        else:
            try:
                from . import pptx as pptx_lib
                items = pptx_lib.extract_slides(fpath)
                slides_ctx = {
                    "title": str(sl_conf.get("title", "演示文稿")),
                    "note": str(sl_conf.get("note", "")),
                    "items": items, "count": len(items),
                    "download_url": f"/reports/{report_dir.name}/{quote(fname)}",
                }
            except Exception as e:   # 解析失败降级为仅下载，不阻断建站
                warnings.append(f"[showcase] 演示文稿解析失败（降级为下载）: {e}")

    fingerprint = hashlib.sha256()
    for d in docs.values():
        fingerprint.update(d["path"].read_bytes())

    ctx = {
        "showcase_error": None,
        "sc_title": str(conf.get("title", "调研展示")),
        "sc_subtitle": str(conf.get("subtitle", "")),
        "overview": {
            "title": str((conf.get("overview") or {}).get("title", "报告摘要")),
            "points": _render_points((conf.get("overview") or {}).get("points"),
                                     docs, known, commit, warnings),
        },
        "arch_overview": arch_overview,
        "chapters": chapters,
        "attachments": attachments,
        "slides": slides_ctx,
        "sources": {"links": sources_links, "folds": sources_folds},
        "notes_url": f"/notes/{report_dir.name}",
        "short_commit": commit[:8],
    }
    return ctx, warnings


def render_notes(notes_file: Path, known: set[str], commit: str) -> str:
    """讲稿 Markdown → HTML（不重写链接，讲稿允许直接出现裸链接）。"""
    md = markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists"],
                           extension_configs={})
    fragment = md.convert(notes_file.read_text(encoding="utf-8"))
    return bleach.clean(fragment, tags=TAGS, attributes={
        "*": ["id", "class"], "a": ["href", "title"],
        "th": ["align"], "td": ["align"], "ol": ["start"],
    }, protocols={"http", "https", "mailto"}, strip=True)
