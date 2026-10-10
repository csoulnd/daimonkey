"""一次性跨网络出口采集/导入国产专题榜仓库快照。

fetch 在可访问 GitHub 的机器执行；import 在服务端执行。导入仍通过
raw_payloads -> metrics 链接，后续日常更新继续使用常规 GitHub 采集器。
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path


from collector import db
from collector.config import now_iso, registry, today_utc


def fetch(path: Path, slug: str | None = None) -> None:
    import httpx
    entries = []
    for p in registry()["products"]:
        if "domestic" not in p.get("board_tags", []) or not p.get("repo"):
            continue
        if slug and p["slug"] != slug:
            continue
        url = f"https://api.github.com/repos/{p['repo']}"
        response = httpx.get(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "agent-heat-index/1.2",
        }, timeout=25)
        response.raise_for_status()
        body = response.content
        data = response.json()
        if data.get("full_name", "").lower() != p["repo"].lower():
            raise ValueError(f"repository redirect/mismatch: {p['repo']}")
        entries.append({"slug": p["slug"], "url": str(response.url),
                        "body_b64": base64.b64encode(body).decode("ascii")})
        print(p["slug"], data["stargazers_count"], data["forks_count"])
    path.write_text(json.dumps({"fetched_at": now_iso(), "entries": entries}),
                    encoding="utf-8")


def import_bundle(path: Path) -> None:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    with db.conn() as c:
        products = db.upsert_products(c, registry()["products"])
        source_ids = db.upsert_sources(c, registry()["sources"])
        by_slug = {p["slug"]: p for p in products}
        sid = source_ids["github"]
        for entry in bundle["entries"]:
            p = by_slug[entry["slug"]]
            body = base64.b64decode(entry["body_b64"])
            data = json.loads(body)
            raw_id = db.save_raw(c, sid, "github", p["id"], entry["url"],
                                 200, body, "application/json", True,
                                 parser_version="github-v1.0-seed")
            for metric_key, value in (
                ("github_stars", data["stargazers_count"]),
                ("github_forks", data["forks_count"]),
            ):
                c.execute(
                    "INSERT INTO metrics(product_id,board,metric_key,value,unit,source_id,"
                    "raw_payload_id,collected_at,granularity) VALUES(?,?,?,?,?,?,?,?,?)",
                    (p["id"], p["category"], metric_key, value, "count", sid,
                     raw_id, bundle["fetched_at"], "snapshot"))
        db.mark_source(c, "github", True)
        from collector import scoring
        result = scoring.compute_all(c, products, today_utc())
        print(json.dumps(result, ensure_ascii=False))
    from collector.pipeline import run_build
    run_build()


def rescore() -> None:
    from collector import scoring
    from collector.pipeline import run_build
    with db.conn() as c:
        products = db.upsert_products(c, registry()["products"])
        print(json.dumps(scoring.compute_all(c, products, today_utc()),
                         ensure_ascii=False))
    run_build()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["fetch", "import", "rescore"])
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--slug")
    args = parser.parse_args()
    if args.mode == "fetch":
        if args.path is None:
            parser.error("fetch requires a path")
        fetch(args.path, args.slug)
    elif args.mode == "import":
        if args.path is None:
            parser.error("import requires a path")
        import_bundle(args.path)
    else:
        rescore()


if __name__ == "__main__":
    main()
