#!/usr/bin/env python3
"""校验 content/learn：结构、frontmatter、前置顺序与内部链接。

用法：
    .venv/bin/python scripts/verify_learn.py

退出码 0 = 通过；1 = 有结构/链接错误（构建仍会保留旧页面）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import learn  # noqa: E402

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    try:
        series_list = learn.load_all()
    except learn.LearnError as exc:
        print(f"❌ 结构校验失败: {exc}")
        return 1

    errors: list[str] = []
    total = 0
    for series in series_list:
        base = learn.LEARN_DIR / series["id"]
        slugs = {a["slug"] for a in series["articles"]}
        for art in series["articles"]:
            total += 1
            for raw in LINK_RE.findall(art["body"]):
                target = raw.split("#", 1)[0].split("?", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "/",
                                                    "mailto:", "../knowledge/")):
                    continue
                path = (base / target).resolve()
                if target.endswith(".md"):
                    if path.name[:-3] not in slugs and not path.is_file():
                        errors.append(f"{art['path']}: 链接目标不存在 {target}")
                elif not path.is_file():
                    errors.append(f"{art['path']}: 资源不存在 {target}")

    for err in errors:
        print(f"❌ {err}")
    if not series_list:
        print("ℹ️ content/learn 下暂无系列")
        return 0
    print(f"✅ {len(series_list)} 个系列 / {total} 篇文章；链接问题 {len(errors)} 个")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
