"""缓存只读全文索引，浏览器只下载当前查询的结果。"""
from __future__ import annotations

import json
import threading
from .config import DATA_DIR

INDEX_FILE = DATA_DIR / "knowledge_search.json"
_lock = threading.Lock()
_signature = None
_notes: list[dict] = []


def index() -> list[dict]:
    global _signature, _notes
    try:
        stat = INDEX_FILE.stat()
    except FileNotFoundError:
        return []
    signature = (str(INDEX_FILE), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if signature != _signature:
            notes = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            for note in notes:
                note["_text"] = (note["title"] + " " + note["path"] + " " + note["body"]).lower()
                note["_heading"] = (note["title"] + " " + note["path"]).lower()
            _notes, _signature = notes, signature
        return _notes


def search(query: str = "", group: str = "", path: str = "", offset: int = 0, limit: int = 40) -> dict:
    terms = query[:256].strip().lower().split()
    offset, limit = max(0, offset), max(1, min(100, limit))
    found = [n for n in index() if (not group or n["group"] == group)
             and (not path or n["path"].startswith(path))
             and all(t in n["_text"] for t in terms)]
    if terms:
        found.sort(key=lambda n: -sum(t in n["_heading"] for t in terms))
    else:
        found.sort(key=lambda n: n.get("updated", ""), reverse=True)
    results = []
    for note in found[offset:offset + limit]:
        item = {k: note[k] for k in ("title", "path", "url", "group", "group_name")}
        if terms:
            position = note["body"].lower().find(terms[0])
            if position >= 0:
                item["preview"] = ("…" if position > 40 else "") + " ".join(
                    note["body"][max(0, position - 40):position + 120].split()) + "…"
        results.append(item)
    return {"total": len(found), "offset": offset, "limit": limit, "results": results}
