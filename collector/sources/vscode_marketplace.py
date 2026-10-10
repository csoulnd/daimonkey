"""VS Code Marketplace：扩展安装量（POST extensionquery, flags=914, filterType=7）。"""
from __future__ import annotations

from .. import db
from .base import BaseSource, Ctx, MetricOut

API = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"


def parse_installs(data: dict) -> dict[str, float]:
    """{extFullName(lower): install}。发布商名在 publisher.publisherName（实测），兼容旧平铺字段。"""
    out = {}
    try:
        extensions = data["results"][0]["extensions"]
    except (KeyError, IndexError, TypeError):
        return out
    for ext in extensions:
        pub = (ext.get("publisher") or {}).get("publisherName") or ext.get("publisherName")
        name = ext.get("extensionName")
        if not pub or not name:
            continue
        for st in ext.get("statistics", []):
            if st.get("statisticName") == "install":
                out[f"{pub}.{name}".lower()] = st.get("value")
    return out


class VscodeMarketplaceSource(BaseSource):
    key = "vscode"
    name = "VS Code Marketplace"
    parser_version = "vscode-v1.1"

    def collect(self, ctx: Ctx):
        sid = ctx.source_ids["vscode"]
        metrics: list[MetricOut] = []
        notes: list[str] = []
        exts = [p for p in ctx.products if p.get("vscode_ext_id")]
        if not exts:
            return metrics, notes

        def query(items, save_raw=True):
            body = {"filters": [{"criteria": [{"filterType": 7, "value": p["vscode_ext_id"]}
                                              for p in items]}], "flags": 914}
            r = ctx.http.fetch(API, "POST", json_body=body, headers={
                "Accept": "application/json;api-version=7.2-preview.1",
                "Content-Type": "application/json",
            })
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            raw_id = (db.save_raw(ctx.conn, sid, "vscode", None, API, r.status_code,
                                  r.content, r.headers.get("content-type", ""), True,
                                  None, self.parser_version)
                      if save_raw else None)
            return parse_installs(r.json()), raw_id

        try:
            installs, raw_id = query(exts)
        except Exception as e:
            notes.append(f"VS Code Marketplace 批量查询失败: {e}")
            return metrics, notes
        raw_of = {ext: raw_id for ext in installs}
        # 批量未命中的扩展逐个补查（个别扩展在批量响应中不返回）
        missing = [p for p in exts if p["vscode_ext_id"].lower() not in installs]
        for p in missing:
            try:
                more, rid = query([p])
                installs.update(more)
                raw_of.update({k: rid for k in more})
            except Exception as e:
                notes.append(f"VS Code 单查失败 {p['vscode_ext_id']}: {e}")
        for p in exts:
            key = p["vscode_ext_id"].lower()
            v = installs.get(key)
            if v is None:
                notes.append(f"Marketplace 未返回统计: {p['vscode_ext_id']}")
                continue
            metrics.append(MetricOut(p["id"], "vscode_installs", v, "count",
                                     raw_of.get(key), ctx.now))
        return metrics, notes
