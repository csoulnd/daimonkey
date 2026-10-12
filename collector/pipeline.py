"""采集编排：单源失败隔离、评分、LRU 清理、静态 API 导出、建站。

入口见 __main__.py：python3 -m collector --update
"""
from __future__ import annotations

import json
import resource
import traceback

from . import ai, db, export, retention, scoring, sitegen
from .config import DATA_DIR, LOG_DIR, ensure_dirs, now_iso, registry, today_utc
from .http import Http
from .sources import SOURCE_REGISTRY
from .sources.base import Ctx

STATUS_FILE = DATA_DIR / "update_status.json"
NEWS_SOURCE_KEYS = {"news_rss", "news_hn"}   # 仅新闻更新模式执行的源


def write_status(state: str, **extra) -> None:
    STATUS_FILE.write_text(json.dumps(
        {"state": state, "time": now_iso(), **extra}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def _collect(only: set[str] | None = None) -> dict:
    """采集编排核心。only=None 全量；only=NEWS_SOURCE_KEYS 仅新闻（跳过评分）。"""
    ensure_dirs()
    reg = registry()
    started = now_iso()
    write_status("running", started_at=started,
                 mode="news" if only else "full")
    with db.conn() as c:
        db.ensure_news_tables(c)
        products = db.upsert_products(c, reg["products"])
        source_ids = db.upsert_sources(c, reg["sources"])
        run_id = c.execute("INSERT INTO collect_runs(run_type, started_at)"
                           " VALUES(?, ?)",
                           ("news" if only else "full", started)).lastrowid
        http = Http()
        ctx = Ctx(conn=c, http=http, source_ids=source_ids,
                  products=products, now=started)
        enabled_keys = {s["key"] for s in reg["sources"] if s.get("enabled", True)}
        board_of = {p["id"]: p["boards"][0] for p in products}
        total_metrics = 0
        ok = failed = 0
        notes: list[str] = []
        for cls in SOURCE_REGISTRY:
            if cls.key not in enabled_keys:
                continue
            if only is not None and cls.key not in only:
                continue
            try:
                metrics, src_notes = cls().collect(ctx)
                total_metrics += db.save_metrics(c, source_ids[cls.key],
                                                 board_of, metrics)
                db.mark_source(c, cls.key, True)
                ok += 1
            except Exception as e:
                db.mark_source(c, cls.key, False, reachable=False)
                failed += 1
                notes.append(f"[{cls.key}] 源失败(已隔离): {e}")
                traceback.print_exc()
            notes.extend(f"[{cls.key}] {n}" for n in src_notes)

        summary = (scoring.compute_all(c, products, today_utc())
                   if only is None else {"skipped": "news-only run"})
        # AI 分析评分：仅全量更新；失败只降级记 notes，不影响状态
        ai_notes: list[str] = []
        if only is None:
            db.ensure_ai_tables(c)
            try:
                ai_notes = ai.score_boards(c, today_utc())
            except Exception as e:  # 双保险：任何异常都不允许打挂采集
                ai_notes = [f"[ai] 评分步骤异常（已跳过）: {type(e).__name__}: {e}"]
            notes.extend(ai_notes)
        ret_counts = retention.apply(c)
        peak_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        status = "success" if failed == 0 else "partial"
        c.execute(
            "UPDATE collect_runs SET finished_at=?, sources_total=?, sources_ok=?,"
            " sources_failed=?, rows_written=?, peak_mem_mb=?, status=?, error_summary=?"
            " WHERE id=?",
            (now_iso(), ok + failed, ok, failed, total_metrics, peak_mb, status,
             json.dumps(notes[-50:], ensure_ascii=False), run_id))
    # 日志留档
    (LOG_DIR / f"run-{started.replace(':', '').replace('-', '')}.log").write_text(
        "\n".join(notes), encoding="utf-8")
    return {"run_id": run_id, "metrics": total_metrics, "sources_ok": ok,
            "sources_failed": failed, "status": status, "notes": notes,
            "scoring": summary, "retention": ret_counts, "peak_mem_mb": peak_mb,
            "ai": ai_notes, "http_requests": http.request_count}


def run_collect() -> dict:
    """采集 -> 评分 -> LRU。返回运行摘要（导出与建站在 run_update 中执行）。"""
    return _collect(None)


def run_news_collect() -> dict:
    """仅采集新闻 + LRU（不评分、不触热度数据源）。"""
    return _collect(NEWS_SOURCE_KEYS)


def run_build() -> list[str]:
    """导出 API + 建站。建站前按需刷新三板块 AI 摘要（数据指纹变化才生成）。

    返回 AI 摘要步骤的 notes（失败已降级，不影响建站）。
    """
    ensure_dirs()
    ai_notes: list[str] = []
    try:
        with db.conn() as c:
            db.ensure_ai_tables(c)
            ai_notes = ai.refresh_summaries_if_stale(c)
    except Exception as e:  # 摘要步骤绝不该阻断建站
        ai_notes = [f"[ai] 摘要步骤异常（已跳过）: {e}"]
    export.export_all()
    sitegen.build()
    return ai_notes


def run_update() -> dict:
    """手动更新全流程：采集 -> 评分 -> LRU -> 导出 -> 建站。"""
    return _update_with(run_collect())


def run_news_update() -> dict:
    """仅新闻更新：采集新闻 -> LRU -> 导出 -> 建站（不触热度数据源）。"""
    return _update_with(run_news_collect())


def _update_with(result: dict) -> dict:
    try:
        ai_notes = run_build()
        result["ai"] = list(result.get("ai", [])) + ai_notes
        result["notes"] = result.get("notes", []) + ai_notes
        build_ok = True
    except Exception as e:
        build_ok = False
        result["build_error"] = str(e)
        traceback.print_exc()
    state = "success" if (result["status"] == "success" and build_ok) else \
            ("partial" if build_ok else "failed")
    write_status(state, **{k: v for k, v in result.items()
                           if k in ("run_id", "metrics", "sources_ok",
                                    "sources_failed", "peak_mem_mb",
                                    "http_requests", "build_error", "ai")})
    result["state"] = state
    return result
