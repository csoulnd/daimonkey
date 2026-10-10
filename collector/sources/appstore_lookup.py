"""Apple 元数据 API（lookup）：评分数量、版本、上架时间（不提供下载量）。"""
from __future__ import annotations

from .. import db
from .base import BaseSource, Ctx, MetricOut

API = "https://itunes.apple.com/lookup"


class AppstoreLookupSource(BaseSource):
    key = "appstore_lookup"
    name = "Apple 元数据 API"
    parser_version = "appstore-lookup-v1.0"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids["appstore_lookup"]
        metrics: list[MetricOut] = []
        notes: list[str] = []
        apps = [p for p in ctx.products if p.get("appstore_id")]

        def flush(chunk_apps, country, id_kind):
            joined = ",".join(p["appstore_id"] for p in chunk_apps)
            url = f"{API}?{id_kind}={joined}&country={country}"
            try:
                r = ctx.http.fetch(url)
            except Exception as e:
                notes.append(f"Apple lookup 失败: {e}")
                return
            if r.status_code != 200:
                notes.append(f"Apple lookup HTTP {r.status_code}")
                return
            try:
                results = r.json().get("results", [])
            except Exception as e:
                notes.append(f"Apple lookup 解析失败: {e}")
                return
            raw_id = db.save_raw(ctx.conn, sid, "appstore_lookup", None, url,
                                 r.status_code, r.content,
                                 r.headers.get("content-type", ""), True, None,
                                 self.parser_version)
            by_id = {str(x.get("trackId")) if id_kind == "id" else x.get("bundleId"): x
                     for x in results}
            track_map = ctx.shared.setdefault("appstore_track", {})
            for p in chunk_apps:
                info = by_id.get(p["appstore_id"])
                if not info:
                    notes.append(f"Apple lookup 无结果: {p['slug']}")
                    continue
                if info.get("trackId"):
                    track_map[str(info["trackId"])] = p["appstore_id"]
                rc = info.get("userRatingCount")
                if rc is not None:
                    metrics.append(MetricOut(p["id"], "appstore_rating_count", rc,
                                             "count", raw_id, ctx.now))

        groups = {}
        for p in apps:
            country = p.get("appstore_country", "us").lower()
            id_kind = "id" if p["appstore_id"].isdigit() else "bundleId"
            groups.setdefault((country, id_kind), []).append(p)
        for (country, id_kind), group in groups.items():
            for start in range(0, len(group), 10):
                flush(group[start:start + 10], country, id_kind)
        return metrics, notes
