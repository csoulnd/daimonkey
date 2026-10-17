"""缓存只读全文索引，浏览器只下载当前查询的结果。"""
from __future__ import annotations

import json
import threading
from .config import DATA_DIR

DEFAULT_INDEX = DATA_DIR / "knowledge_search.json"
_lock = threading.Lock()
_cache: dict[str, tuple] = {}   # 签名 -> notes


def index(user: str = "") -> list[dict]:
    index_file = (DATA_DIR / f"knowledge_search-{user}.json" if user
                  else DEFAULT_INDEX)
    try:
        stat = index_file.stat()
    except FileNotFoundError:
        return []
    signature = (str(index_file), stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _cache.get(signature)
        if cached is None:
            notes = json.loads(index_file.read_text(encoding="utf-8"))
            for note in notes:
                note["_text"] = (note["title"] + " " + note["path"] + " " + note["body"]).lower()
                note["_heading"] = (note["title"] + " " + note["path"]).lower()
            _cache[signature] = notes
            if len(_cache) > 8:   # 简单防膨胀
                _cache.clear()
                _cache[signature] = notes
        return _cache[signature]


def search(query: str = "", group: str = "", path: str = "", offset: int = 0,
           limit: int = 40, user: str = "") -> dict:
    terms = query[:256].strip().lower().split()
    offset, limit = max(0, offset), max(1, min(100, limit))
    found = [n for n in index(user) if (not group or n["group"] == group)
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
