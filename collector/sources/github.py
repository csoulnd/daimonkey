"""GitHub REST API 采集器。

设计要点（应对未认证 60 次/小时限流）：
1. 响应缓存 6h（含 raw_payload_id，命中缓存时溯源指向原始那次采集，语义不变）；
2. 四轮采集按重要性排序：仓库信息 -> 30天提交 -> 贡献者 -> 30天发布，
   配额不足时整轮跳过并记录备注（降级不失败）；
3. 跟随重定向并把最终 full_name 写回 products，历史路径记入 product_aliases（防数据源腐烂）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import CACHE_DIR, days_ago, registry
from .base import BaseSource, Ctx, MetricOut

API = "https://api.github.com"


class GithubSource(BaseSource):
    key = "github"
    name = "GitHub REST API"
    parser_version = "github-v1.0"

    def collect(self, ctx: Ctx):
        cfg = registry().get("github", {})
        ttl = cfg.get("cache_ttl_hours", 6) * 3600
        token = os.environ.get(cfg.get("token_env", "GITHUB_TOKEN"))
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        sid = ctx.source_ids["github"]
        repos = [p for p in ctx.products if p.get("repo")]
        metrics: list[MetricOut] = []
        notes: list[str] = []
        state = {"remaining": None}

        def cache_path(url: str):
            return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

        def budget_left() -> int:
            if token or state["remaining"] is None:
                return 999
            return state["remaining"]

        def get(url: str, product_id: int | None):
            """返回 (data, raw_id, link_header) 或 None。"""
            cp = cache_path(url)
            if cp.exists():
                try:
                    c = json.loads(cp.read_text())
                    if time.time() - c["ts"] < ttl:
                        return c["content"], c["raw_id"], c.get("link", "")
                except Exception:
                    cp.unlink(missing_ok=True)
            if budget_left() <= 2:
                notes.append(f"配额不足（剩余 {state['remaining']}），跳过 {url}")
                return None
            try:
                r = ctx.http.fetch(url, headers=headers)
            except Exception as e:
                notes.append(f"请求失败 {url}: {e}")
                return None
            rem = r.headers.get("x-ratelimit-remaining", "")
            if rem.isdigit():
                state["remaining"] = int(rem)
            if r.status_code == 403 and state["remaining"] == 0:
                notes.append("GitHub 未认证配额已耗尽（60次/时），本轮后续 GitHub 请求跳过；"
                             "配置 GITHUB_TOKEN 可解")
                return None
            if r.status_code == 404:
                notes.append(f"仓库不存在: {url.rsplit('/', 2)[-2] if '/' in url else url}")
                return None
            if r.status_code != 200:
                notes.append(f"GitHub HTTP {r.status_code}: {url}")
                return None
            body = r.content
            try:
                content = json.loads(body)
                perr = None
            except Exception as e:
                content, perr = None, str(e)
            raw_id = db.save_raw(ctx.conn, sid, "github", product_id, str(r.url),
                                 r.status_code, body, r.headers.get("content-type", ""),
                                 perr is None, perr, self.parser_version)
            cp.write_text(json.dumps({
                "ts": time.time(), "content": content, "raw_id": raw_id,
                "link": r.headers.get("link", ""), "url": url,
            }))
            return content, raw_id, r.headers.get("link", "")

        def round_ok(label: str, items: list) -> bool:
            need = len(items) + 2
            if budget_left() < need:
                notes.append(f"[{label}] GitHub 配额剩余 {state['remaining']}，"
                             f"需约 {need} 次，本轮跳过（缓存/下轮补齐）")
                return False
            return True

        # ---- 轮次 1：仓库基本信息（stars/forks/issues + 别名追踪 + 停滞检测）----
        for p in repos:
            res = get(f"{API}/repos/{p['repo']}", p["id"])
            if not res:
                continue
            d, raw_id, _ = res
            if not isinstance(d, dict):
                continue
            full = d.get("full_name")
            if full and full != p["repo"]:
                ctx.conn.execute("UPDATE products SET repo=?, updated_at=? WHERE id=?",
                                 (full, ctx.now, p["id"]))
                try:
                    ctx.conn.execute(
                        "INSERT INTO product_aliases(product_id, alias_type, alias_value,"
                        " discovered_at, is_current) VALUES(?,?,?,?,0)",
                        (p["id"], "repo", p["repo"], ctx.now))
                except sqlite3.IntegrityError:
                    pass
                notes.append(f"{p['slug']}: 仓库迁移 {p['repo']} -> {full}")
                p["repo"] = full
            for mkey, fkey in (("github_stars", "stargazers_count"),
                               ("github_forks", "forks_count"),
                               ("github_open_issues", "open_issues_count")):
                if d.get(fkey) is not None:
                    metrics.append(MetricOut(p["id"], mkey, d[fkey], "count",
                                             raw_id, ctx.now))
            lic = (d.get("license") or {}).get("spdx_id")
            if lic and lic != "NOASSERTION":
                ctx.conn.execute(
                    "UPDATE products SET license=? WHERE id=? AND (license IS NULL OR license=?)",
                    (lic, p["id"], lic))
            pushed = d.get("pushed_at")
            if pushed:
                try:
                    t = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    stale = t < datetime.now(timezone.utc) - timedelta(days=90)
                    ctx.conn.execute("UPDATE products SET status=? WHERE id=?",
                                     ("stale" if stale else "active", p["id"]))
                except ValueError:
                    pass

        # ---- 轮次 2：30 天提交数（动量）----
        since = days_ago(31)
        if round_ok("commits", repos):
            for p in repos:
                url = f"{API}/repos/{p['repo']}/commits?since={since}&per_page=100"
                res = get(url, p["id"])
                if not res:
                    continue
                data, raw_id, link = res
                if not isinstance(data, list):
                    continue
                count = len(data)
                m = re.search(r'<([^>]+)>;\s*rel="last"', link)
                if count == 100 and m:
                    last_url = m.group(1)
                    mp = re.search(r"[?&]page=(\d+)", last_url)
                    last_page = int(mp.group(1)) if mp else 1
                    res2 = get(last_url, p["id"])
                    if res2:
                        count = (last_page - 1) * 100 + len(res2[0])
                    else:
                        count = (last_page - 1) * 100
                        notes.append(f"{p['slug']}: 提交数取保守下界 {count}+")
                metrics.append(MetricOut(p["id"], "github_commits_30d", count, "count",
                                         raw_id, ctx.now))

        # ---- 轮次 3：贡献者数（Link rel=last 页号）----
        if round_ok("contributors", repos):
            for p in repos:
                url = f"{API}/repos/{p['repo']}/contributors?per_page=1&anon=true"
                res = get(url, p["id"])
                if not res:
                    continue
                data, raw_id, link = res
                if not isinstance(data, list):
                    continue
                m = re.search(r'<([^>]+)>;\s*rel="last"', link)
                count = int(re.search(r"[?&]page=(\d+)", m.group(1)).group(1)) if m else len(data)
                metrics.append(MetricOut(p["id"], "github_contributors", count, "count",
                                         raw_id, ctx.now))

        # ---- 轮次 4：30 天发布数 ----
        cutoff = days_ago(31)
        if round_ok("releases", repos):
            for p in repos:
                url = f"{API}/repos/{p['repo']}/releases?per_page=100"
                res = get(url, p["id"])
                if not res:
                    continue
                data, raw_id, _ = res
                if not isinstance(data, list):
                    continue
                count = sum(1 for r in data if (r.get("published_at") or "") >= cutoff)
                metrics.append(MetricOut(p["id"], "github_releases_30d", count, "count",
                                         raw_id, ctx.now))

        if state["remaining"] is not None:
            notes.append(f"GitHub 剩余配额: {state['remaining']}"
                         + ("（未认证）" if not token else ""))
        return metrics, notes
