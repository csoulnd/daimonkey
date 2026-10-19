#!/usr/bin/env python3
"""方向周总结人工维护工具（AI 生成、人工可改）。

用法：
  .venv/bin/python scripts/digestctl.py list [--direction harness] [--week 2026-W42]
  .venv/bin/python scripts/digestctl.py edit <id> --file summary.txt [--publish]
  .venv/bin/python scripts/digestctl.py publish <id>
  .venv/bin/python scripts/digestctl.py generate [--direction harness] [--week 2026-W42] [--force]

只操作 insights 表中 llm_generated=1 且 tags 含 news-digest 的 AI 产物；
不触碰确定性新闻/榜单数据。发布后频道页在下一次 --build 时展示。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector import db  # noqa: E402
from collector.config import registry  # noqa: E402

DIGEST_LIKE = "%news-digest%"


def _direction_keys() -> set[str]:
    return set((registry().get("directions") or {}).keys())


def _validate_direction(direction: str | None) -> None:
    if direction and direction not in _direction_keys():
        known = ", ".join(sorted(_direction_keys()))
        raise ValueError(f"未知方向 {direction}（可选：{known}）")


def list_digests(c, direction: str | None = None, week: str | None = None,
                 limit: int = 20) -> list[dict]:
    _validate_direction(direction)
    sql = ("SELECT id, title, status, published_at, tags, summary, created_at"
           " FROM insights WHERE llm_generated = 1 AND tags LIKE ?")
    params: list = [DIGEST_LIKE]
    if direction:
        sql += " AND tags LIKE ?"
        params.append(f"%direction:{direction}%")
    if week:
        sql += " AND published_at = ?"
        params.append(week)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return [dict(r) for r in c.execute(sql, params)]


def _digest_row(c, insight_id: int):
    row = c.execute(
        "SELECT id, title, status, published_at, tags FROM insights"
        " WHERE id = ? AND llm_generated = 1 AND tags LIKE ?",
        (insight_id, DIGEST_LIKE)).fetchone()
    if not row:
        raise ValueError(f"#{insight_id} 不是 AI 周总结（或不存在）")
    return row


def edit_digest(c, insight_id: int, text: str, publish: bool = False) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("summary 不能为空")
    _digest_row(c, insight_id)
    c.execute("UPDATE insights SET summary = ? WHERE id = ?", (text, insight_id))
    if publish:
        c.execute("UPDATE insights SET status = 'published' WHERE id = ?",
                  (insight_id,))
    return dict(c.execute(
        "SELECT id, title, status, published_at, tags, summary FROM insights"
        " WHERE id = ?", (insight_id,)).fetchone())


def publish_digest(c, insight_id: int) -> dict:
    _digest_row(c, insight_id)
    c.execute("UPDATE insights SET status = 'published' WHERE id = ?",
              (insight_id,))
    return dict(c.execute(
        "SELECT id, title, status, published_at, tags, summary FROM insights"
        " WHERE id = ?", (insight_id,)).fetchone())


def generate(direction: str | None = None, week: str | None = None,
             force: bool = False) -> list[str]:
    _validate_direction(direction)
    from collector import workflows
    return workflows.generate_direction_digests(week=week, direction=direction,
                                                force=force)


def _print_row(row: dict) -> None:
    summary = (row.get("summary") or "").replace("\n", " ")[:70]
    print(f"#{row['id']} [{row['status']}] {row.get('published_at') or '—'} "
          f"{row.get('tags') or ''}\n  {row.get('title')}\n  {summary}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="方向周总结人工维护")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出 AI 周总结")
    p_list.add_argument("--direction")
    p_list.add_argument("--week")
    p_list.add_argument("--limit", type=int, default=20)

    p_edit = sub.add_parser("edit", help="用文本文件替换总结正文")
    p_edit.add_argument("id", type=int)
    p_edit.add_argument("--file", required=True, help="UTF-8 文本文件路径")
    p_edit.add_argument("--publish", action="store_true", help="同时发布")

    p_pub = sub.add_parser("publish", help="发布某条总结")
    p_pub.add_argument("id", type=int)

    p_gen = sub.add_parser("generate", help="按方向生成 AI 总结（已有则跳过）")
    p_gen.add_argument("--direction")
    p_gen.add_argument("--week")
    p_gen.add_argument("--force", action="store_true", help="已有总结也再生成一条")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "generate":
            for line in generate(args.direction, args.week, args.force):
                print(line)
            return 0
        with db.conn() as c:
            if args.cmd == "list":
                rows = list_digests(c, args.direction, args.week, args.limit)
                if not rows:
                    print("没有匹配的 AI 周总结")
                for row in rows:
                    _print_row(row)
                return 0
            if args.cmd == "edit":
                text = Path(args.file).read_text(encoding="utf-8")
                _print_row(edit_digest(c, args.id, text, args.publish))
                return 0
            if args.cmd == "publish":
                _print_row(publish_digest(c, args.id))
                return 0
    except (ValueError, OSError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
