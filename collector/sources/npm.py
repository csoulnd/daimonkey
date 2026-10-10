"""npm Registry Downloads API 采集器：月下载量 + 30 天增长率。

批量端点一次取 8 个包，整组失败时回退逐包请求（防单个失效包拖垮整组）。
增长率来自 range 端点的 60 天日序列：sum(近30天) vs sum(前30天)。
"""
from __future__ import annotations

import json

from .. import db
from ..config import date_days_ago
from .base import BaseSource, Ctx, MetricOut

API = "https://api.npmjs.org/downloads"


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _sum_days(series: list[dict], start: str, end: str) -> int:
    return sum(d.get("downloads", 0) for d in series if start <= d.get("day", "") <= end)


class NpmSource(BaseSource):
    key = "npm"
    name = "npm Registry Downloads API"
    parser_version = "npm-v1.0"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids["npm"]
        pkgs = [(p, p["npm_package"]) for p in ctx.products if p.get("npm_package")]
        metrics: list[MetricOut] = []
        notes: list[str] = []

        def save(url: str, body: bytes, product_id=None) -> int:
            return db.save_raw(ctx.conn, sid, "npm", product_id, url, 200, body,
                               "application/json", True, None, self.parser_version)

        # ---- 月下载量（point 批量）----
        downloads: dict[str, float] = {}
        raw_of: dict[str, int] = {}
        missing: set[str] = set()
        for chunk in _chunks(pkgs, 8):
            joined = ",".join(pkg for _, pkg in chunk)
            url = f"{API}/point/last-month/{joined}"
            ok = False
            try:
                r = ctx.http.fetch(url)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        raw_id = save(url, r.content)
                        for it in data:
                            if it.get("package") and it.get("downloads") is not None:
                                downloads[it["package"]] = it["downloads"]
                                raw_of[it["package"]] = raw_id
                        ok = True
            except Exception:
                ok = False
            if not ok:
                # 回退逐包（坏包只影响自己）
                for p, pkg in chunk:
                    try:
                        r = ctx.http.fetch(f"{API}/point/last-month/{pkg}")
                        if r.status_code == 200:
                            d = r.json()
                            raw_id = save(f"{API}/point/last-month/{pkg}", r.content, p["id"])
                            downloads[pkg] = d["downloads"]
                            raw_of[pkg] = raw_id
                        else:
                            missing.add(pkg)
                            notes.append(f"npm 包不可得({r.status_code}): {pkg}")
                    except Exception as e:
                        missing.add(pkg)
                        notes.append(f"npm 请求失败: {pkg}: {e}")
        for p, pkg in pkgs:
            if pkg in downloads:
                metrics.append(MetricOut(p["id"], "npm_monthly_downloads",
                                         downloads[pkg], "count", raw_of[pkg], ctx.now))

        # ---- 30 天增长率（range，60 天窗口）----
        end = date_days_ago(1)
        s30, e30 = date_days_ago(30), date_days_ago(1)
        s60 = date_days_ago(60)
        for chunk in _chunks(pkgs, 8):
            joined = ",".join(pkg for _, pkg in chunk if pkg not in missing)
            if not joined:
                continue
            url = f"{API}/range/{s60}:{end}/{joined}"
            got: list[tuple[str, list[dict]]] = []
            range_raw: int | None = None
            try:
                r = ctx.http.fetch(url)
                if r.status_code == 200:
                    data = r.json()
                    data = data if isinstance(data, list) else [data]
                    got = [(d["package"], d.get("downloads", [])) for d in data if "package" in d]
                    range_raw = save(url, r.content)
            except Exception:
                got = []
            if not got:
                for p, pkg in chunk:
                    if pkg in missing:
                        continue
                    try:
                        r = ctx.http.fetch(f"{API}/range/{s60}:{end}/{pkg}")
                        if r.status_code == 200:
                            got.append((pkg, r.json().get("downloads", [])))
                            raw_of[pkg] = save(f"{API}/range/{s60}:{end}/{pkg}",
                                               r.content, p["id"])
                        else:
                            notes.append(f"npm range 不可得({r.status_code}): {pkg}")
                    except Exception as e:
                        notes.append(f"npm range 失败: {pkg}: {e}")
            by_pkg = {name: series for name, series in got}
            for p, pkg in chunk:
                series = by_pkg.get(pkg)
                if not series:
                    continue
                prev = _sum_days(series, s60, s30)
                last = _sum_days(series, s30, e30)
                if prev and last is not None:
                    metrics.append(MetricOut(p["id"], "npm_downloads_growth_30d",
                                             round((last - prev) / prev * 100, 2), "pct",
                                             range_raw or raw_of.get(pkg), ctx.now))
        return metrics, notes
