"""PyPI 下载量（pypistats）：recent 端点取月下载量。

pypistats 限流严格：Http 层 3s 间隔 + [5,15] 短退避；本源再设时间预算，
全线 429 时放弃剩余包（下轮再补），保证单源不拖垮整轮更新。
"""
from __future__ import annotations

import time

from .. import db
from .base import BaseSource, Ctx, MetricOut

API = "https://pypistats.org/api/packages"
TIME_BUDGET_S = 300


class PypiSource(BaseSource):
    key = "pypi"
    name = "PyPI 下载量 (pypistats)"
    parser_version = "pypi-v1.0"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids["pypi"]
        metrics: list[MetricOut] = []
        notes: list[str] = []
        started = time.monotonic()
        for p in ctx.products:
            pkg = p.get("pypi_package")
            if not pkg:
                continue
            if time.monotonic() - started > TIME_BUDGET_S:
                notes.append(f"pypi 达到 {TIME_BUDGET_S}s 时间预算，剩余包下轮补齐"
                             f"（已完成 {len(metrics)} 条）")
                break
            url = f"{API}/{pkg}/recent"
            try:
                r = ctx.http.fetch(url)
            except Exception as e:
                notes.append(f"pypi 请求失败 {pkg}: {e}")
                continue
            if r.status_code == 404:
                notes.append(f"pypi 包不存在: {pkg}")
                continue
            if r.status_code != 200:
                notes.append(f"pypi HTTP {r.status_code}: {pkg}")
                continue
            try:
                data = r.json()["data"]
                value = data.get("last_month")
            except Exception as e:
                notes.append(f"pypi 解析失败 {pkg}: {e}")
                continue
            if value is None:
                continue
            raw_id = db.save_raw(ctx.conn, sid, "pypi", p["id"], url, r.status_code,
                                 r.content, r.headers.get("content-type", ""), True,
                                 None, self.parser_version)
            metrics.append(MetricOut(p["id"], "pypi_monthly_downloads", value, "count",
                                     raw_id, ctx.now))
        return metrics, notes
