"""Hacker News（Algolia API）：按 agent 关键词检索故事，自带社区热度分。

查询词与分类在 registry.yaml -> news.hn；points_min 过滤低分噪声。
"""
from __future__ import annotations

from urllib.parse import quote_plus

from .. import db, news as newslib
from ..config import registry
from .base import BaseSource, Ctx

API = "https://hn.algolia.com/api/v1/search_by_date"


class NewsHnSource(BaseSource):
    key = "news_hn"
    name = "Hacker News（Algolia）"
    parser_version = "news-hn-v1.0"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids[self.key]
        hn = registry().get("news", {}).get("hn", {})
        rules = registry().get("news", {}).get("keyword_rules", {})
        matcher = ctx.shared.setdefault("news_matcher",
                                        newslib.build_matcher(ctx.products))
        notes: list[str] = []
        merged: dict[str, dict] = {}
        raw_ids: dict[str, int] = {}
        for q in hn.get("queries", []):
            url = (f"{API}?query={quote_plus(q['q'])}&tags=story"
                   f"&numericFilters=points%3E{int(hn.get('points_min', 3))}"
                   f"&hitsPerPage={int(hn.get('hits_per_query', 30))}")
            try:
                r = ctx.http.fetch(url)
                if r.status_code != 200:
                    notes.append(f"HN[{q['q']}] HTTP {r.status_code}，跳过")
                    continue
                hits = r.json().get("hits", [])
                raw_ids[q["q"]] = db.save_raw(
                    ctx.conn, sid, self.key, None, url, r.status_code,
                    r.content, r.headers.get("content-type", ""), True, None,
                    self.parser_version)
                for hit in hits:
                    oid = str(hit.get("objectID") or "")
                    if not oid or oid in merged:
                        continue
                    merged[oid] = {
                        "title": str(hit.get("title") or "").strip(),
                        "url": hit.get("url")
                            or f"https://news.ycombinator.com/item?id={oid}",
                        "summary": newslib.strip_html(hit.get("story_text") or ""),
                        "author": hit.get("author"),
                        "published_at": newslib._parse_date(hit.get("created_at")),
                        "points": hit.get("points"),
                        "category_default": q.get("category", "product"),
                        "region": "global",
                        "raw_payload_id": raw_ids[q["q"]],
                    }
            except Exception as e:
                notes.append(f"HN[{q['q']}] 失败: {e}")
        entries = [e for e in merged.values() if e["title"]]
        inserted, got = newslib.save_items(
            ctx.conn, entries, source_key=self.key, source_name="Hacker News",
            matcher=matcher, rules=rules)
        notes.append(f"{self.key} +{inserted}/{got}（{len(hn.get('queries', []))} 组查询）")
        return [], notes
