"""只读展示 InferTechNote 快照；安全渲染 Markdown、改写本地链接并生成搜索索引。"""
from __future__ import annotations

import html
import json
import posixpath
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import bleach
import markdown
from markdown.extensions.toc import slugify_unicode
import yaml

from .config import DATA_DIR, DIST_DIR

REPO_DIR = DATA_DIR / "knowledge-repo"
REPO_URL = "https://github.com/csoulnd/InferTechNote"
GROUPS = {
    "agent": ("Agent", "业务设计、调研、实践报告与学习笔记"),
    "infra": ("Infra", "推理服务与基础设施工程"),
    "knowledge": ("沉淀知识", "Agent、Infra 与跨领域基础知识"),
}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}
TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "br",
    "table", "thead", "tbody", "tr", "th", "td", "img", "del", "s",
    "details", "summary", "div", "span", "sup", "sub",
}


def displayable(path: str) -> bool:
    """只展示业务笔记与沉淀知识；模板和运行配置留在仓库中。"""
    return path.endswith(".md") and (path == "README.md" or path.split("/")[0] in GROUPS)


def note_url(path: str) -> str:
    return "/knowledge/notes/" + quote(path[:-3] if path.endswith(".md") else path, safe="/") + ".html"


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        meta = {}
    return meta if isinstance(meta, dict) else {}, text[match.end():]


def git_value(*args: str, repo: Path = REPO_DIR) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   encoding="utf-8", timeout=15).strip()


def local_link(url: str, current: str, known: set[str], commit: str) -> str:
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path:
        return url
    path = unquote(parts.path)
    if path.startswith("/"):
        # Absolute URLs in source are already website routes, not repo-relative.
        return url
    target = posixpath.normpath(posixpath.join(posixpath.dirname(current), path))
    if target == ".." or target.startswith("../"):
        return REPO_URL
    suffix = ("?" + parts.query if parts.query else "") + ("#" + parts.fragment if parts.fragment else "")
    if target in known:
        return note_url(target) + suffix
    if target.rstrip("/") + "/README.md" in known:
        return note_url(target.rstrip("/") + "/README.md") + suffix
    if any(note.startswith(target.rstrip("/") + "/") for note in known):
        return "/knowledge?path=" + quote(target.rstrip("/") + "/", safe="/")
    asset = REPO_DIR / target
    if asset.is_file() and not asset.is_symlink() and asset.suffix.lower() in IMAGE_TYPES:
        return "/knowledge/files/" + quote(target, safe="/") + suffix
    # External references/submodules are not silently imported into the personal KB.
    return REPO_URL + "/blob/" + commit + "/" + quote(target, safe="/") + suffix


class Rewriter(HTMLParser):
    def __init__(self, current: str, known: set[str], commit: str):
        super().__init__(convert_charrefs=False)
        self.current, self.known, self.commit = current, known, commit
        self.output: list[str] = []

    def handle_starttag(self, tag, attrs):
        rewritten = []
        for key, value in attrs:
            if key in ("href", "src") and value:
                value = local_link(value, self.current, self.known, self.commit)
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


def render_markdown(body: str, path: str, known: set[str], commit: str) -> tuple[str, str]:
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "sane_lists", "footnotes"],
                           extension_configs={"toc": {"permalink": False, "slugify": slugify_unicode}})
    fragment = md.convert(body)
    clean = bleach.clean(fragment, tags=TAGS, attributes={
        "*": ["id", "class"], "a": ["href", "title"],
        "img": ["src", "alt", "title", "width", "height"],
        "th": ["align"], "td": ["align"], "ol": ["start"], "details": ["open"],
    }, protocols={"http", "https", "mailto"}, strip=True)
    rewrite = Rewriter(path, known, commit)
    rewrite.feed(clean)
    rewrite.close()
    return "".join(rewrite.output), bleach.clean(md.toc, tags=TAGS,
        attributes={"*": ["class"], "a": ["href"]}, strip=True)


def load_notes(repo: Path = REPO_DIR) -> tuple[dict, list[dict]]:
    if not repo.is_dir():
        return {"available": False, "count": 0, "url": REPO_URL}, []
    commit = git_value("rev-parse", "HEAD", repo=repo)
    committed_at = git_value("show", "-s", "--format=%cI", "HEAD", repo=repo)
    # Tracked files only: exclude Git/Obsidian/agent runtime configuration.
    paths = git_value("ls-files", repo=repo).splitlines()
    paths = [p for p in paths if displayable(p) and (repo / p).is_file()
             and not (repo / p).is_symlink()]
    notes = []
    for path in paths:
        front, body = split_frontmatter((repo / path).read_text(encoding="utf-8"))
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        title = str(front.get("title") or (heading.group(1) if heading else Path(path).stem))
        group = path.split("/")[0] if "/" in path else "overview"
        updated = git_value("log", "-1", "--format=%cI", "--", path, repo=repo)
        notes.append({"path": path, "url": note_url(path), "title": title,
                      "group": group, "group_name": GROUPS.get(group, ("仓库概览", ""))[0],
                      "status": str(front.get("status", "")), "type": str(front.get("type", "")),
                      "body": body, "updated": updated,
                      "source_url": REPO_URL + "/blob/" + commit + "/" + quote(path, safe="/")})
    return {"available": True, "count": len(notes), "url": REPO_URL,
            "commit": commit, "short_commit": commit[:8], "updated": committed_at}, notes


def directory_tree(notes: list[dict]) -> dict:
    root = {"dirs": {}, "notes": []}
    for note in notes:
        node = root
        for part in note["path"].split("/")[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "notes": []})
        node["notes"].append(note)
    return root


def build(env, common: dict, notes: list[dict]) -> None:
    """与热度站同一次建站生成，保留旧页面直到新快照生成完成。"""
    root = DIST_DIR / "knowledge"
    root.mkdir(parents=True, exist_ok=True)
    ctx = {**common, "knowledge_page": True, "kb_tree": directory_tree(notes)}

    def page(tpl, output, **extra):
        target = root / output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(tpl).render(**ctx, **extra), encoding="utf-8")

    groups = [{"key": k, "name": name, "description": desc,
               "count": sum(n["group"] == k for n in notes),
               "url": note_url(k + "/README.md") if any(n["path"] == k + "/README.md" for n in notes) else "/knowledge?group=" + k}
              for k, (name, desc) in GROUPS.items()]
    page("knowledge_index.html", "index.html", kb_groups=groups,
         kb_notes=sorted(notes, key=lambda n: n["updated"], reverse=True))
    # /knowledge resolves via the existing extensionless static route.
    shutil.copyfile(root / "index.html", DIST_DIR / "knowledge.html")
    known = {n["path"] for n in notes}
    commit = common["kb"].get("commit", "main")
    outputs = set()
    for note in notes:
        fragment, toc = render_markdown(note["body"], note["path"], known, commit)
        output = "notes/" + note["path"][:-3] + ".html"
        page("knowledge_note.html", output, note=note, article_html=fragment, article_toc=toc)
        outputs.add(root / output)
    # Remove obsolete generated notes only; source repository is never modified here.
    if (root / "notes").is_dir():
        for old in (root / "notes").rglob("*.html"):
            if old not in outputs:
                old.unlink()
    for source in REPO_DIR.rglob("*") if REPO_DIR.is_dir() else []:
        rel = source.relative_to(REPO_DIR)
        if rel.parts[0] in GROUPS and source.is_file() and not source.is_symlink() and source.suffix.lower() in IMAGE_TYPES:
            target = root / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    search = [{k: n[k] for k in ("title", "path", "url", "group", "group_name", "body", "updated")} for n in notes]
    # Full text stays on the server; atomic replacement keeps ongoing queries consistent.
    staging = DATA_DIR / "knowledge_search.json.tmp"
    staging.write_text(json.dumps(search, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    staging.replace(DATA_DIR / "knowledge_search.json")
    manifest = [{k: v for k, v in n.items() if k != "body"} for n in search]
    (root / "search.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
