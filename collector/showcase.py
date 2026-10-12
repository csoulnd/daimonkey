"""产品调研展示（showcase）：构建时从知识库快照导入 Markdown 的报告形态。

定位：InferTechNote 是研究内容的唯一来源；本模块只做"呈现"。页面内容在构建
时从 data/knowledge-repo 导入（章节级抽取 + 链接重写 + 事实/判断标记），不在
网站侧另行维护一套文案；文档更新经知识库同步 + 建站后自动反映到页面。

报告目录（reports/{slug}/）：
    showcase.yaml      清单映射：源文档、章节选择、交互图组件、步骤高亮
    diagram.svg        交互架构图（呈现层资产；研究出处为文档 Mermaid 研究视图）

导入规则：
- 章节抽取：按 Markdown 标题文本（## 或 ###）模糊匹配定位，缺章节只警告并
  在页面标注"文档已变更"，不阻断建站；
- 链接重写：仓库内相对链接 → /knowledge/notes 页面；库外链接（如 .agents/）
  → 版本固定的 GitHub 链接；外部链接原样保留；
- Mermaid 代码块不入页面（由交互视图承载），原图入口指向知识库原文；
- "**研究判断：**" 段落加显著样式，与事实陈述区分；待验证事项区独立标注。

内容问题（发现源文档的笔误/引用问题）记录在本报告目录的 content-issues.md，
交回研究侧处理，本模块不改动源文档。
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


def _mark_judgments(html: str) -> str:
    """让「研究判断」与事实陈述视觉区分（只加样式，不改文案）。

    段落以标记开头 → 整段标记；标记在段落中间（前有事实句）→ 仅标记判断
    部分，避免把事实句也罩进判断样式。
    """
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
        # 库内但不在展示范围（如 .agents/），或库外路径：给版本固定的原文链接
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


def render_md(text: str, doc_rel: str, known: set[str], commit: str,
              *, keep_ids: bool = True) -> str:
    """Markdown → 清洗后的 HTML（含链接重写与研究判断标记）。"""
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


# ---- 结构化抽取：组件表 / 步骤列表 ----

def parse_component_table(body: str) -> dict[str, str]:
    """从「## 软件架构」首个含"关键部分"的表提取组件职责：{行首列: 职责描述}。"""
    seg, _ = extract_section(body, "软件架构")
    source = seg if seg is not None else body
    rows: dict[str, str] = {}
    header_seen = False
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header_seen = False if stripped else header_seen
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if not header_seen:
            if "关键部分" in cells[0]:
                header_seen = True
            continue
        if set(cells[0]) <= set("-: "):      # 分隔行
            continue
        rows[cells[0]] = cells[1]
    return rows


def extract_steps(section_md: str) -> list[str]:
    """从章节 md 中提取有序列表步骤（含续行），返回 md 段落列表。"""
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
    return steps


# ---- 主入口 ----

def _known_paths() -> set[str]:
    """知识库展示范围内的全部 .md 路径（文件系统枚举，不逐文件跑 git）。"""
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


def load_showcase(report_dir: Path) -> tuple[dict, list[str]]:
    """读取报告目录的 showcase.yaml，导入内容；返回 (模板上下文, 警告列表)。"""
    warnings: list[str] = []
    conf_file = report_dir / "showcase.yaml"
    try:
        conf = (yaml.safe_load(conf_file.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError) as e:
        return {"showcase_error": f"showcase.yaml 读取失败: {e}"}, [str(e)]
    conf = conf.get("showcase") or {}
    commit = _commit()
    known = _known_paths()

    # 源文档（当前活跃产品；pending 产品只作进度展示）
    products = conf.get("products") or []
    active = next((p for p in products if p.get("status") == "ready"), None)
    if active is None:
        return ({"showcase_error": "showcase.yaml 未配置 status: ready 的产品",
                 "products": products}, [])
    detail_rel = str(active.get("detail_doc", ""))
    summary_rel = str(active.get("summary_doc", ""))
    detail_path = KB_REPO / detail_rel
    if not detail_path.is_file():
        msg = f"源文档不存在: {detail_rel}（先同步知识库）"
        return {"showcase_error": msg, "products": products}, [msg]
    detail_body = detail_path.read_text(encoding="utf-8")
    # 去 frontmatter
    from .knowledge import split_frontmatter
    _, detail_body = split_frontmatter(detail_body)

    fingerprint = hashlib.sha256()
    for rel in (detail_rel, summary_rel):
        p = KB_REPO / rel
        if p.is_file():
            fingerprint.update(p.read_bytes())
    fp = fingerprint.hexdigest()[:10]

    # 章节导入
    sections = []
    for spec in conf.get("sections") or []:
        heading = str(spec.get("heading", ""))
        seg, warn = extract_section(detail_body, heading)
        if warn:
            warnings.append(f"[showcase] {warn}（文档可能已变更）")
            sections.append({**spec, "missing": warn})
            continue
        md_text = MERMAID_RE.sub("", seg).strip()
        item = {**spec, "missing": None}
        if spec.get("mode") == "steps":
            raw_steps = extract_steps(md_text)
            item["steps"] = [render_md(s, detail_rel + "#" + heading, known, commit)
                             for s in raw_steps]
            item["step_count"] = len(raw_steps)
        else:
            item["html"] = render_md(md_text, detail_rel + "#" + heading,
                                     known, commit)
        sections.append(item)

    # 交互图：组件说明来自文档组件表（desc_key）。「关键部分」表在 summary，
    # 七维模型表在 detail——两张表都尝试提取，按行首列建索引。
    diagram = conf.get("diagram") or {}
    table = parse_component_table(detail_body)
    summary_body = ""
    summary_path = KB_REPO / summary_rel
    if summary_path.is_file():
        from .knowledge import split_frontmatter as _sf
        _, summary_body = _sf(summary_path.read_text(encoding="utf-8"))
    for key, desc in parse_component_table(summary_body).items():
        table.setdefault(key, desc)
    components = []
    for comp in diagram.get("components") or []:
        desc_html = ""
        key = comp.get("desc_key")
        if key:
            row = table.get(key)
            if row is None:
                row = next((v for k, v in table.items()
                            if _norm_heading(str(key)) in _norm_heading(k)), None)
            if row is None:
                warnings.append(f"[showcase] 组件表未找到行: {key}")
            else:
                desc_html = render_md(row, detail_rel, known, commit)
        components.append({**comp, "desc_html": desc_html,
                           "desc_source": "doc-table" if desc_html else "view-note"})
    diagram["components"] = components
    svg_file = report_dir / str(diagram.get("file", "diagram.svg"))
    diagram["svg"] = svg_file.read_text(encoding="utf-8") if svg_file.is_file() else ""
    if not diagram["svg"]:
        warnings.append("[showcase] 缺少交互图文件 diagram.svg")

    ctx = {
        "showcase_error": None,
        "conf": conf,
        "products": products,
        "baseline": active.get("baseline", ""),
        "product_name": active.get("name", ""),
        "sections": sections,
        "diagram": diagram,
        "fingerprint": fp,
        "detail_rel": detail_rel,
        "summary_rel": summary_rel,
        "detail_kb_url": note_url(detail_rel),
        "summary_kb_url": note_url(summary_rel),
        "detail_github_url": _github_blob(detail_rel, commit),
        "summary_github_url": _github_blob(summary_rel, commit),
        "short_commit": commit[:8],
    }
    return ctx, warnings
