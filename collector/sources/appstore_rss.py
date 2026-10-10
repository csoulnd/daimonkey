"""Apple App Store 免费总榜 Top 200：按产品所属国家/地区采集。

未进入 Top 200 的产品记为 201（截断下界），避免把未上榜误作中位数。
一次响应被多个产品共享引用（raw_payloads.product_id 为空）。
"""
from __future__ import annotations

from .. import db
from .base import BaseSource, Ctx, MetricOut

def parse_entries(data: dict) -> list[dict]:
    """[{id, rank, name, category}]，rank 从 1 开始。"""
    entries = ((data or {}).get("feed") or {}).get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for i, e in enumerate(entries, 1):
        try:
            app_id = (e.get("id") or {}).get("attributes", {}).get("im:id")
            if not app_id:
                continue
            out.append({
                "id": app_id,
                "rank": i,
                "name": (e.get("im:name") or {}).get("label", ""),
                "category": ((e.get("category") or {}).get("attributes") or {}).get("label", ""),
            })
        except AttributeError:
            continue
    return out


class AppstoreRssSource(BaseSource):
    key = "appstore_rss"
    name = "Apple App Store 榜单 RSS"
    parser_version = "appstore-rss-v1.1"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids["appstore_rss"]
        metrics: list[MetricOut] = []
        notes: list[str] = []
        apps = [p for p in ctx.products if p.get("appstore_id")]
        countries = {p.get("appstore_country", "us").lower() for p in apps}
        ranks: dict[str, dict[str, int]] = {}
        raw_ids: dict[str, int] = {}
        for country in countries:
            url = f"https://itunes.apple.com/{country}/rss/topfreeapplications/limit=200/json"
            try:
                r = ctx.http.fetch(url)
                if r.status_code != 200:
                    notes.append(f"App Store RSS({country}) HTTP {r.status_code}")
                    continue
                entries = parse_entries(r.json())
                raw_id = db.save_raw(ctx.conn, sid, "appstore_rss", None, url,
                                     r.status_code, r.content,
                                     r.headers.get("content-type", ""), True, None,
                                     self.parser_version)
            except Exception as e:
                notes.append(f"App Store RSS({country}) 失败: {e}")
                continue
            ranks[country] = {e["id"]: e["rank"] for e in entries}
            raw_ids[country] = raw_id
        for p in apps:
            aid = p.get("appstore_id")
            country = p.get("appstore_country", "us").lower()
            if country not in ranks:
                continue
            # RSS 的 im:id 是数字 trackId；registry 登记的是 bundleId。
            # appstore_lookup（先于本源执行）会把 bundleId -> trackId 放进 ctx.shared
            track_map = ctx.shared.get("appstore_track", {})
            track_id = None
            for tid, bundle in track_map.items():
                if bundle == aid:
                    track_id = tid
                    break
            if track_id is None and aid.isdigit():
                track_id = aid
            if track_id:
                rank = ranks[country].get(track_id, 201)
                metrics.append(MetricOut(p["id"], "appstore_rank", rank, "rank",
                                         raw_ids[country], ctx.now))
                if rank == 201:
                    notes.append(f"App Store {country} 免费总榜 Top200 未见 {p['slug']}（记为 201+）")
            else:
                notes.append(f"App Store 无 trackId: {p['slug']}（{aid}）")
        return metrics, notes
