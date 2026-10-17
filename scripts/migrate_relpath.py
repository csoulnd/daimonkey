#!/usr/bin/env python3
"""把 raw_payloads.body_path 的绝对路径一次性迁移为相对 DATA_DIR 的路径。

背景：库内存 `/home/.../data/raw/...` 时，换服务器或改目录会断溯源链。
迁移后统一存 `raw/<源>/<日期>/<hash>.json.gz`，读取时拼当前 DATA_DIR。

幂等：已是相对路径的行跳过，可重复执行。
用法:
    .venv/bin/python scripts/migrate_relpath.py [--db PATH] [--dry-run]
退出码 0 = 迁移成功（或无需迁移）；1 = 仍有无法解析的绝对路径。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.config import DATA_DIR, DB_PATH  # noqa: E402


def to_relative(stored: str, data_dir: Path = DATA_DIR) -> str | None:
    """绝对路径 -> 相对 data_dir 的 POSIX 路径；无需/无法转换返回 None。

    兼容旧目录：路径不在当前 DATA_DIR 下时，取最后一个 `data/` 段之后的部分
    （例如 `/old/root/data/raw/x.gz` -> `raw/x.gz`）。
    """
    p = Path(stored)
    if not p.is_absolute():
        return None
    base = data_dir.resolve()
    try:
        return p.resolve().relative_to(base).as_posix()
    except ValueError:
        parts = p.parts
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "data" and i + 1 < len(parts):
                return Path(*parts[i + 1:]).as_posix()
    return None


def migrate(db_path: Path, data_dir: Path = DATA_DIR,
            dry_run: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, body_path FROM raw_payloads WHERE body_path IS NOT NULL"
    ).fetchall()
    stats = {"total": len(rows), "converted": 0, "already_relative": 0,
             "unresolvable": 0, "missing_file": 0, "dry_run": dry_run}
    updates: list[tuple[str, int]] = []
    for row in rows:
        stored = row["body_path"]
        rel = to_relative(stored, data_dir)
        if rel is None:
            if Path(stored).is_absolute():
                stats["unresolvable"] += 1
                print(f"  [WARN] raw#{row['id']} 无法解析: {stored}",
                      file=sys.stderr)
            else:
                stats["already_relative"] += 1
            continue
        if not (data_dir / rel).exists():
            stats["missing_file"] += 1
        updates.append((rel, row["id"]))
    if updates and not dry_run:
        with conn:
            conn.executemany(
                "UPDATE raw_payloads SET body_path=? WHERE id=?", updates)
    conn.close()
    stats["converted"] = len(updates)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="raw_payloads.body_path 绝对路径迁移")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="SQLite 数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()
    if not args.db.is_file():
        print(f"数据库不存在: {args.db}", file=sys.stderr)
        return 1
    s = migrate(args.db, DATA_DIR, args.dry_run)
    action = "将转换" if args.dry_run else "已转换"
    print(f"body_path: 共 {s['total']} 行 | {action} {s['converted']} 行 | "
          f"原本相对 {s['already_relative']} 行 | 文件缺失 {s['missing_file']} 行 | "
          f"无法解析 {s['unresolvable']} 行")
    return 1 if s["unresolvable"] else 0


if __name__ == "__main__":
    sys.exit(main())
