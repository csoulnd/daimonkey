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
import time
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


def news_redirect_ok(base: str) -> bool:
    """/news 应 301 到门户(全站新闻页已下线)。"""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(base + "/news", timeout=10)
        return False
    except urllib.error.HTTPError as e:
        return e.code == 301 and e.headers.get("Location") == "/"


def get(url: str, timeout: int = 10):
    # 站内限流是 120 次/分；验收脚本短时间连打时对 429 做短暂退避重试。
    for attempt in range(4):
        try:
            return urllib.request.urlopen(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


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
    paths = ["/", "/dir/harness", "/dir/harness/news", "/dir/harness/digest",
             "/dir/fm", "/dir/fm/news", "/dir/fm/digest",
             "/dir/nv", "/dir/nv/news", "/dir/nv/digest",
             "/rankings/harness_core", "/rankings/harness_eco",
             "/rankings/model", "/rankings/nv_infer",
             "/rankings/domestic", "/rankings/domestic_app",
             "/product/claude-code", "/product/codex-cli",
             "/sources", "/api.html",
             "/api/v1/boards.json", "/api/v1/rankings/unified.json",
             "/api/v1/rankings/harness_core.json",
             "/api/v1/rankings/harness_eco.json",
             "/api/v1/rankings/model.json", "/api/v1/rankings/nv_infer.json",
             "/api/v1/hardware/ladder.json",
             "/api/v1/meta.json", "/api/v1/news.json"]
    for p in paths:
        try:
            r = get(base + p)
            check(f"{p} -> {r.status}", r.status == 200)
        except Exception as e:
            check(f"{p} ({e})", False)

    print("== 1b. 新闻功能结构(按方向聚合) ==")
    harness_news_html = page(base + "/dir/harness/news")
    for name, pat in [
        ("方向新闻区", r'id="dir-news"'),
        ("新闻条目", r'class="news-item"'),
        ("轻量更新按钮", r'id="btn-news-update"'),
    ]:
        check(name, bool(re.search(pat, harness_news_html)))
    check("导航无全站新闻入口", 'href="/news"' not in harness_news_html)
    check("全站新闻页已下线(301)", news_redirect_ok(base))
    try:
        news_api = json.load(get(base + "/api/v1/news.json"))
        check(f"新闻条目数 {news_api['meta']['total']}", news_api["meta"]["total"] > 0)
        with_raw = sum(1 for it in news_api["items"] if it.get("raw_payload_id"))
        check(f"新闻溯源链 {with_raw}/{len(news_api['items'])}",
              news_api["items"] and with_raw == len(news_api["items"]))
    except Exception as e:
        check(f"news.json 解析 ({e})", False)
    prod_html = page(base + "/product/claude-code")
    check("产品页相关新闻区块", 'class="p-news-item"' in prod_html)

    print("== 2. 页面关键结构 ==")
    home = page(base + "/")
    uni = page(base + "/dir/harness")
    harness_news = page(base + "/dir/harness/news")
    model_page = page(base + "/rankings/model")
    nv_page = page(base + "/rankings/nv_infer")
    harness_page = page(base + "/rankings/harness_core")
    for name, pat, html in [
        ("更新按钮", r'id="btn-update"', home),
        ("数据截至时间", r'id="asof"', home),
        ("来源标签", r'class="src-tag s-', uni),
        ("原始指标溯源", r'class="sv-v"', uni),
        ("榜单徽章", r'class="badge b-', harness_news),
        ("本周要点卡", r'class="ai-summary weekly-points"', harness_page),
    ]:
        check(name, bool(re.search(pat, html)))
    check("旧热度/动量视图已移除",
          'data-view="mom"' not in harness_page
          and "热度视图" not in harness_page
          and "动量视图" not in harness_page)
    meta = json.load(get(base + "/api/v1/meta.json"))
    check(f"数据新鲜度存在（截至 {meta.get('last_collected_at')}）",
          bool(meta.get("last_collected_at")))

    print("== 2b. 三方向频道结构 ==")
    for key in ("harness", "fm", "nv"):
        # 三区已拆到三个子页:榜单页 / 新闻页 / 周总结页各校验自己的区块 id
        pages = [
            ("方向榜单区", page(base + f"/dir/{key}"), r'id="dir-boards"'),
            ("方向新闻区", page(base + f"/dir/{key}/news"), r'id="dir-news"'),
            ("每周总结区", page(base + f"/dir/{key}/digest"), r'id="dir-digest"'),
        ]
        for label, html, pat in pages:
            check(f"/dir/{key} {label}", bool(re.search(pat, html)))
        board_html = page(base + f"/dir/{key}")
        check(f"/dir/{key} 本周要点卡与周总结入口",
              "本周要点" in board_html and f"/dir/{key}/digest" in board_html)
    nv = page(base + "/dir/nv")
    check("NV 硬件天梯已上线", "硬件天梯" in nv and "建设中" not in nv)
    check("NV 榜含 MLPerf 头部卡",
          "NVIDIA GB300" in nv and "NVIDIA Vera Rubin" in nv)
    check("NV 榜官方文档可点",
          bool(re.search(r'href="https://[^"]+" target="_blank" rel="noopener">官方文档</a>',
                         nv)))
    check("导航三方向入口", all(f'href="/dir/{k}"' in home
                                for k in ("harness", "fm", "nv")))
    harness_html = page(base + "/dir/harness")
    check("Harness 收录三问", "收录说明（核心榜三问）" in harness_html
          and harness_html.count("<li>") >= 3)

    print("== 2b2. 榜单列与数据层排序 ==")
    fm_channel = page(base + "/dir/fm")
    nv_channel = page(base + "/dir/nv")
    harness_labels = ("类型", "GitHub Stars", "30 天提交", "下载量")
    fm_labels = ("厂商", "AA 智能指数", "编码指数", "Agent 指数")
    nv_labels = ("加速卡", "FP16 算力 (TFLOPS)", "低精度支持", "显存 (GB)",
                 "显存带宽 (GB/s)", "官方文档")
    check("Harness 配置化新列", all(x in harness_html for x in harness_labels))
    check("Harness 已移除 npm/VS Code/商店列",
          not any(f">{x}</th>" in harness_html
                  for x in ("npm 月下载", "VS Code 安装", "商店排名")))
    check("Harness 排序规则", harness_html.count(
        "GitHub Stars 优先；无仓库产品按下载量列后") >= 2)
    check("Harness 下载量带聚合构成 title",
          "下载量 = npm 月下载 " in harness_html
          and bool(re.search(r'title="下载量 = npm 月下载 [0-9,]+', harness_html)))
    check("FM 配置化新列", all(x in fm_channel for x in fm_labels))
    check("FM 评判标准", "评判标准：AA Intelligence Index" in fm_channel)
    check("NV 配置化新列", all(x in nv_channel for x in nv_labels))
    nv_head = re.search(
        r'<table class="tbl board-table[^"]*">\s*<thead>\s*<tr>(.*?)</tr>',
        nv_channel, re.S)
    nv_headers = re.findall(r"<th[^>]*>(.*?)</th>", nv_head.group(1),
                            re.S) if nv_head else []
    nv_headers = [re.sub(r"<[^>]+>", "", h).strip() for h in nv_headers]
    check("NV 表头为 8 列", nv_headers == [
        "#", "加速卡", "厂商", "FP16 算力 (TFLOPS)", "低精度支持",
        "显存 (GB)", "显存带宽 (GB/s)", "官方文档"])
    check("NV 不再展示 MLPerf 吞吐列",
          not any(x in nv_channel for x in ("gpt-oss-120b Offline", "DeepSeek R1",
                                            "Llama 3.1 8B")))
    check("NV 评判标准", "按 FP16 稠密算力排序；低精度与规格为厂商公开数据"
          in nv_channel)
    domestic_ok = all(
        re.search(r'<table class="tbl board-table[^"]*">\s*<thead>\s*<tr>\s*<th>',
                  page(base + f"/rankings/{b}"))
        for b in ("domestic", "domestic_app"))
    check("地域专题榜列定义非空", domestic_ok)

    def first(html: str) -> str:
        m = re.search(
            r'<table class="tbl board-table[^"]*">.*?<a href="/product/[^"]+">([^<]+)</a>',
            html, re.S)
        return m.group(1) if m else ""
    check(f"Harness 按 Stars 排序（首行 {first(harness_page)}）",
          first(harness_page) == "OpenClaw")
    check(f"FM 按 AA 智能指数排序（首行 {first(model_page)}）",
          first(model_page) == "Claude Opus 5.5 (Max)")
    check(f"NV 按 FP16 算力排序（首行 {first(nv_page)}）",
          first(nv_page) == "AMD Instinct MI355X 288GB HBM3e")

    print("== 2b3. 旧口径已从页面移除 ==")
    old_patterns = {
        "热度分": r"热度分", "7d": r"(?<![0-9A-Za-z])7d(?![0-9A-Za-z])",
        "动量分": r"动量分", "覆盖率": r"覆盖率", "观察区": r"观察区",
        "AI 分析分": r"AI 分析分", "热度视图": r"热度视图",
    }
    pages_to_check = {
        "/": home, "/dir/harness": harness_html, "/dir/fm": fm_channel,
        "/dir/nv": nv_channel, "/rankings/model": model_page,
        "/rankings/nv_infer": nv_page, "/product/codex-cli":
            page(base + "/product/codex-cli"),
    }
    bad_pages = {}
    for path, html in pages_to_check.items():
        hits = [label for label, pat in old_patterns.items()
                if re.search(pat, html)]
        if hits:
            bad_pages[path] = hits
    check("旧口径字段已从页面移除" + (f"（命中 {bad_pages}）" if bad_pages else ""),
          not bad_pages)
    prod = page(base + "/product/codex-cli")
    check("产品页只保留名次与原始指标",
          "最新排名" in prod and 'class="rcard"' in prod
          and "指标时序" in prod and 'class="sv-v"' in prod)


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

    print("== 2e. FM 总评分(AA) ==")
    try:
        model = json.load(get(base + "/api/v1/rankings/model.json"))
        aa_items = [it for it in model["items"]
                    if it["metrics"].get("aa_intelligence")]
        check(f"模型榜按 AA 总评分入池（{len(aa_items)} 个）", len(aa_items) >= 20)
        check("模型榜成员均为 AA 自动入池",
              aa_items and all(it.get("origin") == "aa" and it.get("status") == "active"
                               for it in model["items"]))
        check("模型榜排序口径为 AA 智能指数",
              "以 AA 智能指数从高到低排序" in model_page)
        check("AA 数值带来源与溯源",
              aa_items and all(it["metrics"]["aa_intelligence"].get("raw_payload_id")
                               and it["metrics"]["aa_intelligence"]["source"]["key"] == "aa"
                               for it in aa_items))
        check("模型榜页 AA 列与来源标注",
              "AA 智能指数" in model_page and "src-tag s-aa" in model_page
              and "Artificial Analysis" in model_page)
        check("模型榜不再展示 SuperCLUE 列",
              "SuperCLUE 总分" not in model_page
              and "src-tag s-superclue" not in model_page)
        if aa_items:
            rid = aa_items[0]["metrics"]["aa_intelligence"]["raw_payload_id"]
            raw = json.load(get(f"{base}/api/v1/raw/{rid}.json"))
            extracted = {x["metric_key"] for x in raw["extracted"]}
            check("AA raw 归档可回放", raw["raw_body"] is not None
                  and any(k.startswith("aa_") for k in extracted))
        meta_src = {s["key"]: s for s in json.load(
            get(base + "/api/v1/meta.json"))["sources"]}
        check("AA 源健康可达", meta_src.get("aa", {}).get("reachable") == 1)
        check("SuperCLUE 源已停用", meta_src.get("superclue", {}).get("enabled") == 0)
    except Exception as e:
        check(f"AA 总评分检查 ({e})", False)

    print("== 2e2. NV 硬件天梯(MLPerf 自动入池 + 厂商手录) ==")
    try:
        nv_api = json.load(get(base + "/api/v1/rankings/nv_infer.json"))
        items = nv_api["items"]
        check(f"NV 榜快照入池加速卡（{len(items)} 张）", len(items) >= 8)
        check("NV 榜快照仍保留 MLPerf 自动入池卡",
              items and all(it.get("origin") == "mlperf"
                            and it.get("status") == "active" for it in items))
        check("NV 榜排序口径为 FP16 稠密算力",
              "按 FP16 稠密算力从高到低排序" in nv_page)
        primary = [it for it in items
                   if it["metrics"].get("mlperf_gptoss_120b")]
        check("MLPerf 吞吐仍在库/API 留档", len(primary) >= 8)
        if primary:
            top = primary[0]
            check("NV MLPerf 头部为 GB300 且吞吐 ≥10000 Tokens/s",
                  top["name"] == "NVIDIA GB300"
                  and top["metrics"]["mlperf_gptoss_120b"]["value"] >= 10000)
            check("MLPerf 数值带来源与溯源",
                  all(it["metrics"]["mlperf_gptoss_120b"].get("raw_payload_id")
                      and it["metrics"]["mlperf_gptoss_120b"]["source"]["key"] == "mlperf"
                      for it in primary))
            rid = top["metrics"]["mlperf_gptoss_120b"]["raw_payload_id"]
            raw = json.load(get(f"{base}/api/v1/raw/{rid}.json"))
            extracted = {x["metric_key"] for x in raw["extracted"]}
            check("MLPerf raw 归档可回放", raw["raw_body"] is not None
                  and any(k.startswith("mlperf_") for k in extracted))
        check("NV 页面含昇腾官网最新型号",
              "Atlas 300I A2" in nv_page and "Atlas 300I Pro" in nv_page)
        row = re.search(
            r'<a href="/product/atlas-300i-a2">.*?</tr>', nv_page, re.S)
        check("昇腾 A2 的 FP16/显存/带宽/低精度有值",
              bool(row) and all(x in row.group(0)
                                for x in ("280", "64", "1600", "560 TOPS")))
        check("昇腾 A2 官方文档可点",
              bool(row) and 'href="https://e.huawei.com' in row.group(0)
              and "官方文档" in row.group(0))
        meta_src = {s["key"]: s for s in json.load(
            get(base + "/api/v1/meta.json"))["sources"]}
        check("MLPerf 源健康可达", meta_src.get("mlperf", {}).get("reachable") == 1)
    except Exception as e:
        check(f"NV 硬件天梯检查 ({e})", False)

    print("== 2e3. 硬件天梯规格 API（独立于 rankings 契约） ==")
    try:
        ladder = json.load(get(base + "/api/v1/hardware/ladder.json"))
        lmeta, litems = ladder["meta"], ladder["items"]
        page_rows = len(re.findall(r'<td class="rank(?: top)?">', nv_page))
        check(f"硬件天梯 {len(litems)} 行与页面一致（页面 {page_rows} 行）",
              bool(litems) and len(litems) == page_rows)
        check(f"硬件天梯 meta.total 与行数一致（{lmeta.get('total')}）",
              lmeta.get("total") == len(litems))
        check(f"硬件天梯 16 行（当前 {len(litems)} 行）", len(litems) == 16)
        fp16 = [it.get("fp16_tflops") for it in litems]
        valid = [v for v in fp16 if v is not None]
        check("硬件天梯首行 FP16 为最大值",
              bool(valid) and fp16[0] == max(valid))
        check("硬件天梯按 FP16 降序、缺值排最后",
              fp16[:len(valid)] == sorted(valid, reverse=True)
              and all(v is None for v in fp16[len(valid):]))
        check("硬件天梯名次连续且从 1 开始",
              [it.get("rank") for it in litems] == list(range(1, len(litems) + 1)))
        check("硬件天梯含 Atlas 300I A2",
              any(it.get("name") == "Atlas 300I A2" for it in litems))
        check("硬件天梯 official_docs URL 以 http 开头",
              any(it.get("official_docs") for it in litems)
              and all(d.get("url", "").startswith("http")
                      for it in litems for d in (it.get("official_docs") or [])))
        numeric = ("fp16_tflops", "fp8_tflops", "int8_tops", "bf16_tflops",
                   "hbm_gb", "hbm_bw_gbs", "interconnect_bw_gbs", "tdp_w")
        check("硬件天梯无值字段为 null（无 0/— 占位）",
              all(it.get(k) is None or isinstance(it.get(k), (int, float))
                  for it in litems for k in numeric)
              and all(it.get("lowp_label") is None or isinstance(
                  it.get("lowp_label"), str) for it in litems))
        gz = ROOT / "dist" / "api" / "v1" / "hardware" / "ladder.json.gz"
        check("硬件天梯 .json.gz 预压缩存在", gz.is_file())
        req = urllib.request.Request(
            base + "/api/v1/hardware/ladder.json",
            headers={"Accept-Encoding": "gzip"})
        gzr = urllib.request.urlopen(req, timeout=10)
        check("硬件天梯响应 Content-Encoding: gzip",
              gzr.headers.get("Content-Encoding") == "gzip")
    except Exception as e:
        check(f"硬件天梯规格 API 检查 ({e})", False)

    print("== 2f. 方向周总结与方向周报 ==")
    for key in ("harness", "fm"):
        html = page(base + f"/dir/{key}/digest")
        check(f"{key} 每周总结区", 'id="dir-digest"' in html)
        check(f"{key} 周总结卡片或空态",
              "ai-summary" in html or "empty-state" in html)
        links = re.findall(
            rf'href="(/dir/{key}/digest/[0-9]{{4}}-W[0-9]{{2}}\.html)"', html)
        check(f"{key} 方向周报入口（{len(links)} 个）", bool(links))
        if links:
            r = get(base + links[0])
            check(f"方向周报页 {links[0]} -> {r.status}", r.status == 200)
    ranking_weekly_bad = []
    for board in ("harness_core", "model", "nv_infer"):
        html = page(base + f"/rankings/{board}")
        if not ("本周要点" in html
                and ("查看完整周总结" in html or "查看每周总结" in html)):
            ranking_weekly_bad.append(board)
    check("榜单页均有本周要点卡", not ranking_weekly_bad)

    print("== 2c. 旧路由 301 ==")
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    code, location = 200, None
    for attempt in range(4):
        try:
            opener.open(base + "/rankings/unified", timeout=10)
            code, location = 200, None
            break
        except urllib.error.HTTPError as e:
            code, location = e.code, e.headers.get("Location")
            if code != 429 or attempt == 3:
                break
            time.sleep(3 * (attempt + 1))
    check("/rankings/unified -> 301 /dir/harness",
          code == 301 and location == "/dir/harness")

    print(f"== 3. 两跳溯源抽查（{args.n} 个随机数字） ==")
    # 只抽榜单指标单元格的溯源链接(mval 宏),新闻条目的 raw 不带提取指标,
    # 不属于“数字两跳溯源”的检查对象。
    ids = sorted(set(int(m) for m in re.findall(
        r'class="sv-v" href="/raw/(\d+)"', uni)))
    # raw API 只导出近 14 天；更早条目的溯源页仍可访问（trace_url），
    # 因此两跳 JSON 抽查只在已导出的 id 内抽样，避免把保留策略当成坏链。
    try:
        raw_index = json.load(get(base + "/api/v1/raw/index.json"))
        exported = {int(it["id"]) for it in raw_index.get("items", [])}
    except Exception:
        exported = set()
    linked_n = len(ids)
    ids = [rid for rid in ids if rid in exported] if exported else ids
    sample = random.sample(ids, min(args.n, len(ids))) if ids else []
    check(f"榜单页含可导出溯源链接（{len(ids)}/{linked_n} 个）", len(ids) > 0)
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
