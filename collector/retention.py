"""历史数据 LRU 保留：按时间窗口清理，每次更新后自动执行。

窗口在 registry.yaml -> retention 配置（单位：天/小时，0 = 永久保留）：
- raw_body_days  原始响应体文件：删文件、保留元数据行与 sha256（溯源链不断，页面显示"已清理"）
- metrics_days   指标时序行
- rankings_days  榜单快照行
- cache_hours    GitHub 采集缓存文件
"""
from __future__ import annotations

import time

from .config import (CACHE_DIR, DATA_DIR, date_days_ago, days_ago, now_iso,
                     registry, resolve_data_path)


def apply(c) -> dict:
    cfg = registry().get("retention", {})
    counts = {"raw_bodies_evicted": 0, "metrics_deleted": 0,
              "rankings_deleted": 0, "ai_scores_deleted": 0,
              "cache_files_deleted": 0, "news_deleted": 0}

    if cfg.get("raw_body_days"):
        cutoff = days_ago(int(cfg["raw_body_days"]))
        rows = c.execute(
            "SELECT id, body_path FROM raw_payloads"
            " WHERE body_path IS NOT NULL AND body_evicted_at IS NULL"
            " AND fetched_at < ?", (cutoff,)).fetchall()
        ts = now_iso()
        for r in rows:
            resolve_data_path(r["body_path"], DATA_DIR).unlink(missing_ok=True)
            c.execute("UPDATE raw_payloads SET body_path=NULL, body_evicted_at=?"
                      " WHERE id=?", (ts, r["id"]))
            counts["raw_bodies_evicted"] += 1

    if cfg.get("metrics_days"):
        cutoff = days_ago(int(cfg["metrics_days"]))
        counts["metrics_deleted"] = c.execute(
            "DELETE FROM metrics WHERE collected_at < ?", (cutoff,)).rowcount

    if cfg.get("rankings_days"):
        cutoff = date_days_ago(int(cfg["rankings_days"]))
        counts["rankings_deleted"] = c.execute(
            "DELETE FROM rankings WHERE snapshot_date < ?", (cutoff,)).rowcount
        # AI 分析分与榜单快照同窗口清理（表可能尚未建：忽略错误）
        try:
            counts["ai_scores_deleted"] = c.execute(
                "DELETE FROM ai_scores WHERE snapshot_date < ?", (cutoff,)).rowcount
        except Exception:
            counts["ai_scores_deleted"] = 0

    if cfg.get("news_days"):
        cutoff = days_ago(int(cfg["news_days"]))
        counts["news_deleted"] = c.execute(
            "DELETE FROM news_items WHERE fetched_at < ?", (cutoff,)).rowcount

    if cfg.get("cache_hours"):
        max_age = int(cfg["cache_hours"]) * 3600
        for f in CACHE_DIR.glob("*.json"):
            try:
                if time.time() - f.stat().st_mtime > max_age:
                    f.unlink()
                    counts["cache_files_deleted"] += 1
            except OSError:
                continue
    return counts
