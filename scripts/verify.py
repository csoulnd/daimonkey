#!/usr/bin/env python3
"""一键验收：页面可访问性、关键结构、两跳溯源抽查、页面体积预算。

用法: .venv/bin/python scripts/verify.py [--base http://127.0.0.1:8080] [--n 5]
退出码 0 = 全部通过。建议每次「更新数据」后跑一次。
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
import urllib.request

CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, ok))
    print(f"  {'✅' if ok else '❌'} {name}")


def get(url: str, timeout: int = 10):
    return urllib.request.urlopen(url, timeout=timeout)


def page(url: str) -> str:
    return get(url).read().decode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--n", type=int, default=5, help="两跳溯源抽查数量")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("== 1. 页面与 API 可访问性 ==")
    paths = ["/", "/rankings/unified", "/rankings/coding", "/product/claude-code",
             "/sources", "/api.html", "/news",
             "/api/v1/boards.json", "/api/v1/rankings/unified.json",
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
    uni = page(base + "/rankings/unified")
    for name, pat in [
        ("更新按钮", r'id="btn-update"'), ("数据截至时间", r'id="asof"'),
        ("来源标签", r'class="src-tag s-'), ("覆盖率条", r'class="cov"'),
        ("榜单徽章", r'class="badge b-'), ("热度/动量切换", r'data-view="mom"'),
    ]:
        check(name, bool(re.search(pat, uni if name != "更新按钮" and name != "数据截至时间" else home)))
    meta = json.load(get(base + "/api/v1/meta.json"))
    check(f"数据新鲜度存在（截至 {meta.get('last_collected_at')}）",
          bool(meta.get("last_collected_at")))

    print(f"== 3. 两跳溯源抽查（{args.n} 个随机数字） ==")
    ids = sorted(set(int(m) for m in re.findall(r'href="/raw/(\d+)"', uni)))
    sample = random.sample(ids, min(args.n, len(ids))) if ids else []
    check(f"榜单页含溯源链接（{len(ids)} 个）", len(ids) > 0)
    full = 0
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
        except Exception as e:
            print(f"     raw#{rid}: {e}")
    check(f"完整链路 {full}/{len(sample)}（原始体+提取指标+SHA256）",
          sample and full == len(sample))

    print("== 4. 页面体积预算（gzip ≤ 200KB，不含图片） ==")
    for p in ["/", "/rankings/unified", "/product/claude-code"]:
        b = get(base + p).read()
        kb = len(gzip.compress(b, 9)) / 1024
        check(f"{p} gzip {kb:.0f}KB", kb <= 200)

    ok_n = sum(1 for _, ok in CHECKS if ok)
    print(f"\n验收结果: {ok_n}/{len(CHECKS)} 通过")
    return 0 if ok_n == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
