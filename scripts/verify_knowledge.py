"""检查已生成知识库的站内文档、资源链接和版本信息。"""
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector.knowledge import load_notes


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.links.append(value)


dist = ROOT / "dist"
notes = list((dist / "knowledge/notes").rglob("*.html"))
broken = []
checked = 0
for page in [dist / "index.html", dist / "knowledge.html", *notes]:
    parser = Links()
    text = page.read_text(encoding="utf-8")
    parser.feed(text)
    assert "个人知识库" in text and ("InferTech 观察站" in t or "智能体热度指数" in t)ext, page
    for link in parser.links:
        parsed = urlsplit(link)
        path = parsed.path
        if parsed.scheme or parsed.netloc or not path.startswith("/"):
            continue
        target = dist / unquote(path).lstrip("/")
        if path.endswith("/"):
            target /= "index.html"
        if not target.is_file() and not target.suffix:
            target = target.with_suffix(".html")
        checked += 1
        if not target.is_file():
            broken.append((str(page.relative_to(dist)), link))
meta, source = load_notes()
search = json.loads((dist / "knowledge/search.json").read_text(encoding="utf-8"))
assert len(notes) == len(search) == len(source) == meta["count"]
assert not any(n["path"].startswith("templates/") for n in search)
assert all("body" not in n for n in search)
private_index = json.loads((ROOT / "data/knowledge_search.json").read_text(encoding="utf-8"))
assert len(private_index) == len(search)
assert not any("/notes/templates/" in str(n) for n in notes)
assert all(n["source_url"].startswith(meta["url"] + "/blob/" + meta["commit"]) for n in source)
assert len((dist / "assets/mermaid-11.12.0.min.js").read_bytes()) == 2748992
if broken:
    print(json.dumps(broken[:30], ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(f"知识库验证通过：{len(notes)} 篇文章，{checked} 个站内链接，commit {meta['short_commit']}")
