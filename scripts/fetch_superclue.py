#!/usr/bin/env python3
"""模型评测榜首灌:SuperCLUE 月报 → 指标 → 评分(model 榜) → AI 复评 → 建站。

用法: .venv/bin/python scripts/fetch_superclue.py
(后续月份更新:改 registry superclue.base_url 指向新月报后重跑;
 榜单快照随日期变化,历史自然累积。)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector import ai, db, pipeline, scoring  # noqa: E402
from collector.config import now_iso, registry, today_utc  # noqa: E402
from collector.http import Http  # noqa: E402
from collector.sources.base import Ctx  # noqa: E402
from collector.sources.superclue import SuperClueSource  # noqa: E402


def main() -> int:
    reg = registry()
    with db.conn() as c:
        db.ensure_news_tables(c)
        db.ensure_ai_tables(c)
        products = db.upsert_products(c, reg["products"])
        source_ids = db.upsert_sources(c, reg["sources"])
        ctx = Ctx(conn=c, http=Http(), source_ids=source_ids,
                  products=products, now=now_iso())
        metrics, notes = SuperClueSource().collect(ctx)
        print("\n".join("· " + n for n in notes))
        board_of = {p["id"]: p["boards"][0] for p in products}
        written = db.save_metrics(c, source_ids["superclue"], board_of, metrics)
        db.mark_source(c, "superclue", True)
        print(f"入库指标 {written} 条")
        scoring.compute_all(c, products, today_utc())
        print("评分完成(model 榜已生成 rankings)")
    print("AI 复评 model 榜…")
    with db.conn() as c:
        for n in ai.score_boards(c, today_utc()):
            print("·", n)
    print("重建站点…")
    for n in pipeline.run_build():
        print("·", n)
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
