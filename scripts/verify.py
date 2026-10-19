#!/usr/bin/env python3
"""一键验收：页面可访问性、关键结构、两跳溯源抽查、页面体积预算。

用法: .venv/bin/python scripts/verify.py [--base http://127.0.0.1:8080] [--n 5]
退出码 0 = 全部通过。建议每次「更新数据」后跑一次。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKS: list[tuple[str, bool]] = []

# 部署防回归例外：这两份文档分别记录历史问题示例与当前服务器交接信息，
# 属于说明性文本，不参与运行路径解析。
HARDCODE_ALLOW = {"docs/PLAN-deploy.md", "docs/HANDOVER.md"}
# 拆开拼接，避免本文件自身命中检查
HARDCODE_NEEDLE = "/home/" + "daimonkey"
TEXT_SUFFIXES = {".py", ".sh", ".md", ".html", ".js", ".cjs", ".css",
                 ".yaml", ".yml", ".txt", ".json", ".service", ".conf"}


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, ok))
    print(f"  {'✅' if ok else '❌'} {name}")


def get(url: str, timeout: int = 10):
    return urllib.request.urlopen(url, timeout=timeout)


def page(url: str) -> str:
    return get(url).read().decode("utf-8")


def _tracked_text_files() -> list[Path]:
    """git 跟踪 + 未忽略的新文件（排除 data/dist 等未入库目录）；无 git 时回退遍历。"""
    try:
        rels = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files"], text=True,
            stderr=subprocess.DEVNULL).splitlines()
        rels += subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"],
            text=True, stderr=subprocess.DEVNULL).splitlines()
        return [ROOT / r for r in dict.fromkeys(rels) if (ROOT / r).is_file()]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", ".venv", "data", "dist", "node_modules", "__pycache__"}
        out = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in skip]
            out.extend(Path(dirpath) / f for f in filenames)
        return out


def deploy_checks() -> None:
    """D1/D2-D6 防回归：相对路径、无硬编码旧目录、交付件齐全。"""
    from collector.config import DB_PATH, resolve_data_path

    print("== 5. 可搬迁性与部署交付件 ==")
    if DB_PATH.is_file():
        with sqlite3.connect(DB_PATH) as c:
            total, absn = c.execute(
                "SELECT COUNT(*), SUM(CASE WHEN body_path LIKE '/%' THEN 1 ELSE 0 END)"
                " FROM raw_payloads WHERE body_path IS NOT NULL").fetchone()
            rows = c.execute(
                "SELECT id, body_path FROM raw_payloads WHERE body_path IS NOT NULL"
                " AND body_evicted_at IS NULL").fetchall()
        check(f"库内 body_path 全部相对路径（{total} 条）",
              bool(total) and not absn)
        missing = [(rid, p) for rid, p in rows
                   if not resolve_data_path(p).is_file()]
        check(f"溯源文件按当前 DATA_DIR 可解析（{len(rows)} 个）", not missing)
        for rid, p in missing[:3]:
            print(f"     raw#{rid} 缺失: {p}")
    else:
        check(f"数据库存在（{DB_PATH}）", False)

    hits = []
    for f in _tracked_text_files():
        if f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = f.relative_to(ROOT).as_posix()
        if rel in HARDCODE_ALLOW:
            continue
        try:
            if HARDCODE_NEEDLE in f.read_text(encoding="utf-8"):
                hits.append(rel)
        except (OSError, UnicodeDecodeError):
            continue
    check("仓库代码/文档无旧安装目录硬编码", not hits)
    for h in hits[:5]:
        print(f"     硬编码: {h}")

    required = [
        ("bootstrap.sh", ROOT / "deploy/bootstrap.sh", True),
        ("backup.sh", ROOT / "deploy/backup.sh", True),
        ("smoke.sh", ROOT / "deploy/smoke.sh", True),
        ("systemd 单元", ROOT / "deploy/daimonkey.service", False),
        ("部署文档", ROOT / "docs/deploy.md", False),
    ]
    for name, path, need_exec in required:
        ok = path.is_file() and (not need_exec or os.access(path, os.X_OK))
        check(f"交付件 {name}", ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--n", type=int, default=5, help="两跳溯源抽查数量")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("== 1. 页面与 API 可访问性 ==")
    paths = ["/", "/dir/harness", "/dir/fm", "/dir/nv",
             "/rankings/unified", "/rankings/coding", "/rankings/harness_core",
             "/rankings/harness_eco", "/product/claude-code",
             "/sources", "/api.html", "/news",
             "/api/v1/boards.json", "/api/v1/rankings/unified.json",
             "/api/v1/rankings/harness_core.json",
             "/api/v1/rankings/harness_eco.json",
             "/api/v1/meta.json", "/api/v1/news.json"]
    for p in paths:
        try:
            r = get(base + p)
            check(f"{p} -> {r.status}", r.status == 200)
        except Exception as e:
            check(f"{p} ({e})", False)

    print("== 1b. 新闻功能结构 ==")
    news_html = page(base + "/news")
    for name, pat in [
        ("新闻页标题", r"AI 新闻汇总"),
        ("导航第三入口", r'href="/news" data-path="/news"'),
        ("分类筛选 chips", r'id="news-filter"'),
        ("新闻条目", r'class="news-item"'),
        ("轻量更新按钮", r'id="btn-news-update"'),
    ]:
        check(name, bool(re.search(pat, news_html)))
    try:
        news_api = json.load(get(base + "/api/v1/news.json"))
        check(f"新闻条目数 {news_api['meta']['total']}", news_api["meta"]["total"] > 0)
        with_raw = sum(1 for it in news_api["items"] if it.get("raw_payload_id"))
        check(f"新闻溯源链 {with_raw}/{len(news_api['items'])}",
              news_api["items"] and with_raw == len(news_api["items"]))
        digests = re.findall(r'href="(/news/digest/[0-9W-]+\.html)"', news_html)
        check(f"周报入口（{len(digests)} 个）", len(digests) > 0)
        if digests:
            r = get(base + digests[0])
            check(f"周报页 {digests[0]} -> {r.status}", r.status == 200)
    except Exception as e:
        check(f"news.json 解析 ({e})", False)
    prod_html = page(base + "/product/claude-code")
    check("产品页相关新闻区块", 'class="p-news-item"' in prod_html)

    print("== 2. 页面关键结构 ==")
    home = page(base + "/")
    uni = page(base + "/dir/harness")
    model_page = page(base + "/rankings/model")
    for name, pat, html in [
        ("更新按钮", r'id="btn-update"', home),
        ("数据截至时间", r'id="asof"', home),
        ("来源标签", r'class="src-tag s-', uni),
        ("覆盖率条", r'class="cov"', uni),
        ("榜单徽章", r'class="badge b-', uni),
        ("热度/动量切换", r'data-view="mom"', model_page),
    ]:
        check(name, bool(re.search(pat, html)))
    meta = json.load(get(base + "/api/v1/meta.json"))
    check(f"数据新鲜度存在（截至 {meta.get('last_collected_at')}）",
          bool(meta.get("last_collected_at")))

    print("== 2b. 三方向频道结构 ==")
    for key in ("harness", "fm", "nv"):
        html = page(base + f"/dir/{key}")
        for label, pat in [
            ("方向新闻区", r'id="dir-news"'),
            ("方向榜单区", r'id="dir-boards"'),
            ("每周总结区", r'id="dir-digest"'),
        ]:
            check(f"/dir/{key} {label}", bool(re.search(pat, html)))
    nv = page(base + "/dir/nv")
    check("NV 建设中说明", "建设中" in nv)
    check("NV 规划数据源清单", "规划中的数据源" in nv and "MLPerf" in nv)
    check("导航三方向入口", all(f'href="/dir/{k}"' in home
                                for k in ("harness", "fm", "nv")))
    harness_html = page(base + "/dir/harness")
    check("Harness 收录三问", "收录说明（核心榜三问）" in harness_html
          and harness_html.count("<li>") >= 3)

    print("== 2d. Harness 分层不混排 ==")
    try:
        core = json.load(get(base + "/api/v1/rankings/harness_core.json"))
        eco = json.load(get(base + "/api/v1/rankings/harness_eco.json"))
        legacy = json.load(get(base + "/api/v1/rankings/unified.json"))
        core_slugs = {it["slug"] for it in core["items"]}
        eco_slugs = {it["slug"] for it in eco["items"]}
        check("核心榜全部 tier=core",
              core["items"] and {it.get("tier") for it in core["items"]} == {"core"})
        check("生态榜全部 tier=eco",
              eco["items"] and {it.get("tier") for it in eco["items"]} == {"eco"})
        check("核心/生态无交集", not (core_slugs & eco_slugs))
        check("MCP/n8n 不在核心榜",
              not ({"mcp-servers", "n8n"} & core_slugs))
        check("MCP/n8n 在生态榜", {"mcp-servers", "n8n"} <= eco_slugs)
        check("旧 unified API 指向核心榜",
              legacy["meta"].get("legacy_of") == "harness_core")
    except Exception as e:
        check(f"Harness 分层 API 解析 ({e})", False)

    print("== 2c. 旧路由 301 ==")
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(base + "/rankings/unified", timeout=10)
        code, location = 200, None
    except urllib.error.HTTPError as e:
        code, location = e.code, e.headers.get("Location")
    check("/rankings/unified -> 301 /dir/harness",
          code == 301 and location == "/dir/harness")

    print(f"== 3. 两跳溯源抽查（{args.n} 个随机数字） ==")
    # 只抽榜单指标单元格的溯源链接(mval 宏),新闻条目的 raw 不带提取指标,
    # 不属于“数字两跳溯源”的检查对象。
    ids = sorted(set(int(m) for m in re.findall(
        r'class="sv-v" href="/raw/(\d+)"', uni)))
    sample = random.sample(ids, min(args.n, len(ids))) if ids else []
    check(f"榜单页含溯源链接（{len(ids)} 个）", len(ids) > 0)
    full = 0
    pages_ok = 0
    for rid in sample:
        try:
            d = json.load(get(f"{base}/api/v1/raw/{rid}.json"))
            body_ok = d["raw_body"] is not None
            ext_ok = len(d["extracted"]) > 0
            sha_ok = bool(d["meta"]["body_sha256"])
            if body_ok and ext_ok and sha_ok:
                full += 1
            else:
                print(f"     raw#{rid}: body={body_ok} extracted={ext_ok} sha={sha_ok}")
            try:
                if get(f"{base}/raw/{rid}").status == 200:
                    pages_ok += 1
            except Exception as e:
                print(f"     raw#{rid} 页面: {e}")
        except Exception as e:
            print(f"     raw#{rid}: {e}")
    check(f"完整链路 {full}/{len(sample)}（原始体+提取指标+SHA256）",
          sample and full == len(sample))
    check(f"溯源页可访问 {pages_ok}/{len(sample)}", sample and pages_ok == len(sample))

    print("== 4. 页面体积预算（gzip ≤ 200KB，不含图片） ==")
    for p in ["/", "/rankings/unified", "/product/claude-code"]:
        b = get(base + p).read()
        kb = len(gzip.compress(b, 9)) / 1024
        check(f"{p} gzip {kb:.0f}KB", kb <= 200)

    deploy_checks()

    ok_n = sum(1 for _, ok in CHECKS if ok)
    print(f"\n验收结果: {ok_n}/{len(CHECKS)} 通过")
    return 0 if ok_n == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
