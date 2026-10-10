"""AI Agent 新闻 RSS/Atom 通用采集：源清单在 registry.yaml -> news.feeds。

一个模块吃掉所有标准 RSS/Atom 源；每个 feed 独立失败隔离、独立 raw 归档可溯源。
不产出 metrics，只写 news_items（pipeline 对空指标源零改动）。
"""
from __future__ import annotations

from .. import db, news as newslib
from ..config import registry
from .base import BaseSource, Ctx


class NewsRssSource(BaseSource):
    key = "news_rss"
    name = "新闻 RSS/Atom 聚合"
    parser_version = "news-rss-v1.0"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids[self.key]
        rules = registry().get("news", {}).get("keyword_rules", {})
        matcher = ctx.shared.setdefault("news_matcher",
                                        newslib.build_matcher(ctx.products))
        notes: list[str] = []
        total_new = 0
        feeds = [f for f in registry().get("news", {}).get("feeds", [])
                 if f.get("enabled", True)]
        for feed in feeds:
            url = feed["url"]
            try:
                r = ctx.http.fetch(url)
                if r.status_code != 200:
                    notes.append(f"{feed['key']} HTTP {r.status_code}，跳过")
                    continue
                entries = newslib.parse_feed(r.content)
                if not entries:
                    notes.append(f"{feed['key']} 解析到 0 条，请检查源格式")
                    continue
                raw_id = db.save_raw(ctx.conn, sid, self.key, None, url,
                                     r.status_code, r.content,
                                     r.headers.get("content-type", ""), True,
                                     None, self.parser_version)
                for e in entries:
                    e["category_default"] = feed.get("category", "product")
                    e["region"] = feed.get("region", "global")
                    e["raw_payload_id"] = raw_id
                inserted, got = newslib.save_items(
                    ctx.conn, entries, source_key=feed["key"],
                    source_name=feed["name"], matcher=matcher, rules=rules,
                    region=feed.get("region", "global"))
                total_new += inserted
                notes.append(f"{feed['key']} +{inserted}/{got}")
            except Exception as e:  # 单 feed 失败不影响其他源
                notes.append(f"{feed['key']} 失败: {e}")
        return [], notes
