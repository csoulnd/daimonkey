"""sitegen 的 SEO / 硬件规格 / 图表 / 构建顺序接线测试。

只跑“最小可渲染子集”，不在测试里构建 1260 个 raw 页：
- SEO 元数据：直接调用 sitegen._seo_meta_for；
- 页面注入：用最小 base.html 桩渲染 sources / product / direction 模板；
- 构建顺序：patch DIST_DIR 到临时目录，用记录型 mock 验证
  assets copytree → search-index → robots → sitemap → 404 → 压缩；
- 全程不写生产 dist/ 与 data/。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from jinja2 import ChoiceLoader, DictLoader

from collector import hw, sitegen
from collector.config import TEMPLATES_DIR, registry

# 只保留 SEO 相关输出的 base.html 桩：真实 base 由另一位 agent 接线，
# 这里断言 sitegen 确实把 seo_meta / seo_jsonld 交给了模板。
STUB_BASE = """<!doctype html>
<html><head>
<title>{% block title %}{% endblock %}</title>
{% set sm = seo_meta|default({}, true) %}
<meta name="description" content="{{ sm.description if sm else 'DEFAULT-FALLBACK-DESCRIPTION' }}">
{% if sm and sm.canonical %}<link rel="canonical" href="{{ sm.canonical }}">{% endif %}
{% if seo_jsonld|default('', true) %}<script type="application/ld+json">{{ seo_jsonld|safe }}</script>{% endif %}
</head><body>
{% block crumb %}{% endblock %}
{% block content %}{% endblock %}
</body></html>"""


def make_env():
    env = sitegen._env()   # 复用 num/pct/dt/hw_num 等过滤器
    env.loader = ChoiceLoader([DictLoader({"base.html": STUB_BASE}), env.loader])
    return env


def base_common() -> dict:
    reg = registry()
    return {
        "site": reg["site"],
        "meta": {"last_collected_at": "2026-10-21T00:00:00Z",
                 "counts": {"products": 3, "news_items": 5}},
        "directions": [],
        "nav_directions": [],
        "board_names": {k: v["name"] for k, v in reg["boards"].items()},
        "metric_names": sitegen.METRIC_NAMES,
        "nav_boards": [],
        "threshold_pct": 50,
        "stale": False,
        "assets_v": "test1234",
        "kb": {},
        "news_categories": reg.get("news", {}).get("categories", {}),
        "news_meta": {"last_fetched_at": "2026-10-20T18:00:00Z"},
    }


def fake_product(days: int = 90) -> dict:
    start = date(2026, 7, 1)
    points = [{
        "date": (start + timedelta(days=i)).isoformat(),
        "value": 1000 + i * 37,
        "unit": "count",
        "source": {"key": "github", "name": "GitHub REST API"},
        "raw_payload_id": 7,
        "collected_at": (start + timedelta(days=i)).isoformat() + "T00:00:00Z",
    } for i in range(days)]
    return {
        "slug": "demo-agent", "name": "Demo Agent", "vendor": "Acme",
        "category": "coding", "status": "active", "boards": ["harness_core"],
        "repo": "acme/demo", "repo_aliases_list": [], "license": "MIT",
        "notes": "演示产品", "latest_ai": {}, "news": [], "integration": None,
        "latest_ranks": {"harness_core": {
            "rank": 1, "heat_score": 99.0, "snapshot_date": "2026-10-21"}},
        "metrics_series": {"github_stars": points},
    }


class TestSeoMeta(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"DM_SITE_URL": "https://example.com"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_home_description_is_page_specific(self):
        meta, jsonld = sitegen._seo_meta_for(
            "home", {**base_common(), "path": "/",
                     "counts": {"products": 3, "news_items": 5}})
        self.assertIn("InferTech", meta["title"])
        self.assertTrue(meta["description"])
        self.assertNotEqual(meta["description"], sitegen.DEFAULT_META_DESCRIPTION)
        self.assertNotEqual(meta["description"], "以推理技术为重心,聚合智能体热度榜单、"
                                                "AI 新闻、报告厅与个人知识库的可溯源技术观察站。")
        self.assertIn("已收录 3 个产品", meta["description"])
        self.assertEqual(meta["canonical"], "https://example.com/")
        self.assertIn("WebSite", jsonld)

    def test_product_meta_uses_name_metrics_and_canonical(self):
        p = fake_product()
        meta, jsonld = sitegen._seo_meta_for(
            "product", {**base_common(), "p": p, "path": "/product/demo-agent"})
        self.assertIn("Demo Agent", meta["title"])
        self.assertIn("Demo Agent", meta["description"])
        self.assertEqual(meta["canonical"], "https://example.com/product/demo-agent")
        self.assertIn("SoftwareApplication", jsonld)

    def test_ranking_meta_has_snapshot(self):
        meta, jsonld = sitegen._seo_meta_for(
            "ranking", {**base_common(), "board": "harness_core",
                        "name": "核心产品榜", "snapshot_date": "2026-10-21",
                        "items": [{"name": "Demo Agent", "heat_score": 99.0,
                                   "slug": "demo-agent"}]})
        self.assertIn("核心产品榜", meta["title"])
        self.assertIn("2026-10-21", meta["title"])
        self.assertEqual(meta["canonical"],
                         "https://example.com/rankings/harness_core")
        self.assertIn("ItemList", jsonld)

    def test_direction_and_weekly_digest(self):
        reg = registry()
        nv = {"key": "nv", **reg["directions"]["nv"]}
        meta, _ = sitegen._seo_meta_for(
            "direction", {**base_common(), "direction": nv,
                          "path": "/dir/nv"})
        self.assertIn("硬件频道", meta["title"])
        self.assertEqual(meta["canonical"], "https://example.com/dir/nv")
        self.assertIn("推理性能榜", meta["description"])

        meta, jsonld = sitegen._seo_meta_for(
            "news", {**base_common(), "direction": nv, "week": "2026-W42",
                     "week_start": "2026-10-12", "week_end": "2026-10-18",
                     "digest_items": [{"title": "一条新闻"}],
                     "path": "/dir/nv/digest/2026-W42"})
        self.assertIn("2026-W42", meta["title"])
        self.assertEqual(meta["canonical"],
                         "https://example.com/dir/nv/digest/2026-W42")
        self.assertIn("NewsArticle", jsonld)

    def test_direction_subpages_are_distinct_through_page_context(self):
        """sitegen 的合并路径（direction_page_context + seo_ctx.path）三页互不相同。"""
        nv = {"key": "nv", **registry()["directions"]["nv"]}
        paths = {"board": "/dir/nv", "news": "/dir/nv/news",
                 "digest": "/dir/nv/digest"}
        metas = {
            tab: sitegen._seo_meta_for(
                "direction", {**base_common(),
                              **sitegen.direction_page_context(nv, tab),
                              "path": path})[0]
            for tab, path in paths.items()
        }
        self.assertEqual(len({m["title"] for m in metas.values()}), 3)
        self.assertEqual(len({m["description"] for m in metas.values()}), 3)
        for tab, path in paths.items():
            self.assertEqual(metas[tab]["canonical"], "https://example.com" + path)
            self.assertIn("硬件", metas[tab]["title"])
            self.assertIn("硬件", metas[tab]["description"])

    def test_private_pages_are_noindex(self):
        for kind in ("login", "404", "raw", "reports", "clipboard"):
            meta, jsonld = sitegen._seo_meta_for(kind, base_common())
            self.assertEqual(meta["robots"], "noindex, nofollow", kind)
            self.assertIsNone(meta["canonical"], kind)
            self.assertEqual(jsonld, "", kind)

    def test_page_meta_failure_falls_back_without_raising(self):
        with patch.object(sitegen.seo, "page_meta", side_effect=RuntimeError("boom")), \
                patch.object(sitegen.log, "exception"):
            meta, jsonld = sitegen._seo_meta_for("product", base_common())
        self.assertTrue(meta["title"])
        self.assertTrue(meta["description"])
        self.assertEqual(meta["robots"], "index, follow")
        self.assertEqual(jsonld, "")


class TestRenderInjection(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"DM_SITE_URL": "https://example.com"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = make_env()

    def test_sources_page_gets_seo_meta_and_writes_file(self):
        html = sitegen._render_page(
            self.env, base_common(), self.root, "sources.html", "sources.html",
            seo_kind="sources", seo_ctx={"path": "/sources"}, sources=[])
        target = self.root / "sources.html"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), html)
        self.assertIn("<title>数据源</title>", html)
        self.assertIn('"name":"数据源与采集方法 | InferTech 观察站"', html)
        self.assertIn('content="站点全部数据源、采集频率、授权与原始响应保留策略', html)
        self.assertNotIn("DEFAULT-FALLBACK-DESCRIPTION", html)
        self.assertIn('<link rel="canonical" href="https://example.com/sources">', html)
        self.assertIn("application/ld+json", html)

    def test_product_chart_is_wrapped_and_hover_free(self):
        p = fake_product()
        charts, units, sources = sitegen._product_charts(p, registry())
        html = sitegen._render_page(
            self.env, base_common(), self.root, "product.html",
            "product/demo-agent.html", seo_kind="product",
            seo_ctx={"path": "/product/demo-agent"},
            p=p, charts=charts, chart_units=units, chart_sources=sources)

        self.assertIn('class="chart-card', html)
        self.assertIn("chart-card-title", html)
        self.assertIn("chart-card-unit", html)
        self.assertIn(">个<", html)                     # registry count → 中文单位
        self.assertIn('href="/raw/7"', html)            # 溯源链接保留
        self.assertNotIn('class="c-hit"', html)         # 产品页关闭 tooltip
        self.assertIn('<path class="c-line"', html)     # smooth=True
        self.assertIn('<link rel="canonical" href="https://example.com/product/demo-agent">', html)
        self.assertNotIn("DEFAULT-FALLBACK-DESCRIPTION", html)
        self.assertLess(len(html), 120_000, "产品页 HTML 应保持 < 120KB")

    def test_nv_direction_page_has_spec_table_and_official_links(self):
        reg = registry()
        nv = {"key": "nv", **reg["directions"]["nv"]}
        hw_ctx = sitegen._hw_context(nv)
        html = sitegen._render_page(
            self.env, base_common(), self.root, "direction.html", "dir/nv.html",
            seo_kind="direction", seo_ctx={"path": "/dir/nv"},
            direction=nv, direction_page=True, active_tab="board",
            direction_tabs=sitegen.direction_tabs(nv), page_label="榜单",
            board_blocks=[], news_meta={"last_fetched_at": "2026-10-20T18:00:00Z"},
            news_page=False, news_rank={}, **hw_ctx)

        self.assertIn('id="hw-specs"', html)
        self.assertIn("规格总表", html)
        self.assertIn("官方文档", html)
        self.assertIn("https://www.nvidia.com", html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener"', html)
        self.assertIn("数据说明", html)
        self.assertIn("已收录", html)
        self.assertIn("—", html)       # null 数值占位
        self.assertIn("数据更新于 2026-10-21", html)
        self.assertIn("chart-card", html)   # hw_bars 预渲染卡片
        self.assertIn("显存带宽", html)
        self.assertIn("FP16 算力", html)
        self.assertIn("INT8 算力", html)
        self.assertIn("硬件频道", html)
        self.assertIn('<link rel="canonical" href="https://example.com/dir/nv">', html)


class TestHardwareContext(unittest.TestCase):
    def test_context_is_driven_by_registry_config(self):
        nv = {"key": "nv", **registry()["directions"]["nv"]}
        ctx = sitegen._hw_context(nv)
        for key in ("hw_specs", "hw_fields", "hw_vendors", "hw_updated", "hw_bars"):
            self.assertIn(key, ctx)
        self.assertGreaterEqual(len(ctx["hw_specs"]), 8)
        labels = hw.field_labels()
        self.assertEqual([f["key"] for f in ctx["hw_fields"]], nv["spec_fields"])
        for field in ctx["hw_fields"]:
            self.assertEqual(field["label"], labels[field["key"]]["label"])
            self.assertEqual(field["unit"], labels[field["key"]]["unit"])
        self.assertEqual(ctx["hw_vendors"], nv["spec_vendors"])
        updated = json.loads(hw.SPEC_PATH.read_text(encoding="utf-8"))["updated_at"]
        self.assertEqual(ctx["hw_updated"], updated)
        self.assertEqual([b["unit"] for b in ctx["hw_bars"]],
                         ["GB/s", "TFLOPS", "TOPS"])

    def test_other_direction_config_needs_no_code_change(self):
        fake = {"key": "fake", "spec_fields": ["vendor", "model", "tdp_w"],
                "spec_vendors": ["华为"]}
        ctx = sitegen._hw_context(fake)
        self.assertEqual([f["key"] for f in ctx["hw_fields"]],
                         ["vendor", "model", "tdp_w"])
        self.assertEqual(ctx["hw_vendors"], ["华为"])
        self.assertEqual([b["key"] for b in ctx["hw_bars"]], [])

    def test_all_null_bar_is_skipped(self):
        labels = hw.field_labels()
        specs = [{"model": "X", "hbm_bw_gbs": None, "fp16_tflops": None,
                  "int8_tops": None}]
        self.assertEqual(sitegen._hw_bars(specs, labels), [])

    def test_missing_spec_file_returns_empty_context(self):
        with patch.object(sitegen.hw, "load", side_effect=hw.HwSpecError("bad")), \
                patch.object(sitegen.log, "exception"):
            self.assertEqual(sitegen._hw_context({"key": "nv", "spec_fields": ["model"]}), {})


class TestProductCharts(unittest.TestCase):
    def test_downsampled_and_no_hover(self):
        p = fake_product(days=90)
        charts, units, sources = sitegen._product_charts(p, registry())
        chart = charts["github_stars"]
        root = ElementTree.fromstring(chart)
        self.assertEqual(root.get("data-total-points"), "90")
        self.assertLessEqual(int(root.get("data-points")), 60)
        self.assertEqual(root.get("data-downsampled"), "true")
        self.assertNotIn('class="c-hit"', chart)
        self.assertIn('<path class="c-line"', chart)
        self.assertEqual(units["github_stars"], "个")
        self.assertEqual(sources["github_stars"]["url"], "/raw/7")

    def test_units_come_from_registry_metric_definitions(self):
        reg = registry()
        self.assertEqual(sitegen._metric_unit("github_stars", reg), "个")
        self.assertEqual(sitegen._metric_unit("aa_intelligence", reg), "分")
        self.assertEqual(sitegen._metric_unit("mlperf_gptoss_120b", reg), "Tokens/s")
        self.assertEqual(sitegen._metric_unit("unknown_metric", reg), "")


class TestWeeklyPoints(unittest.TestCase):
    def test_digest_excerpt_keeps_first_two_sentences(self):
        text = "第一句。第二句！第三句。"
        self.assertEqual(sitegen._digest_excerpt(text), "第一句。第二句！")

    def test_digest_excerpt_truncates_long_sentence(self):
        text = "很长" * 200 + "。第二句。"
        got = sitegen._digest_excerpt(text)
        self.assertLessEqual(len(got), 221)
        self.assertTrue(got.endswith("…"))


class TestRankingsCleanup(unittest.TestCase):
    def test_stale_rankings_removed_with_gz(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            for rel in ("rankings/harness_core.html",
                        "rankings/harness_core.html.gz",
                        "rankings/model.html",
                        "rankings/old.html",
                        "rankings/old.html.gz"):
                page = dist / rel
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text("x", encoding="utf-8")
            removed = sitegen._prune_stale_rankings(
                dist, ["harness_core", "model"])
            self.assertEqual(removed, ["old"])
            self.assertTrue((dist / "rankings" / "harness_core.html").is_file())
            self.assertTrue((dist / "rankings" / "harness_core.html.gz").is_file())
            self.assertFalse((dist / "rankings" / "old.html").exists())
            self.assertFalse((dist / "rankings" / "old.html.gz").exists())

    def test_missing_rankings_dir_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sitegen._prune_stale_rankings(tmp, ["harness_core"]), [])


class TestSeoExclude(unittest.TestCase):
    def test_hidden_boards_and_legacy_entries(self):
        reg = {"boards": {"harness_core": {}, "coding": {"hidden": True},
                          "general": {"hidden": True}, "claw": {"hidden": True},
                          "model": {}}}
        excluded = sitegen._seo_exclude(reg)
        self.assertEqual(excluded, {
            "/rankings/coding", "/rankings/general", "/rankings/claw",
            "/rankings", "/rankings/unified", "/rankings/framework", "/news",
        })
        self.assertNotIn("/rankings/harness_core", excluded)


class TestFinalizeOrder(unittest.TestCase):
    def _prepare(self, root: Path):
        static = root / "static"
        assets = static / "assets"
        assets.mkdir(parents=True)
        (assets / "app.css").write_text("body{}", encoding="utf-8")
        dist = root / "dist"
        dist.mkdir()
        db_path = root / "test.sqlite3"
        sqlite3.connect(db_path).close()
        return static, dist, db_path

    def test_assets_then_search_index_then_discovery_files_then_compress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static, dist, db_path = self._prepare(root)
            events = []
            real_connect = sqlite3.connect

            def spy_connect(dsn, *args, **kwargs):
                events.append(("connect", dsn, kwargs.get("uri")))
                return real_connect(dsn, *args, **kwargs)

            def fake_index(conn, out, **kwargs):
                out = Path(out)
                # 索引生成时 assets 必须已经 copytree 完成
                events.append(("index", out.parent.is_dir(),
                               (out.parent / "app.css").is_file()))
                out.write_text("{}", encoding="utf-8")
                return {"path": str(out), "counts": {}}

            def record(name):
                def _inner(*args, **kwargs):
                    events.append((name,))
                    return dist / f"{name}.txt"
                return _inner

            with patch.object(sitegen, "DIST_DIR", dist), \
                    patch.object(sitegen, "STATIC_DIR", static), \
                    patch.object(sitegen, "DB_PATH", db_path), \
                    patch.object(sitegen.sqlite3, "connect", side_effect=spy_connect), \
                    patch.object(sitegen.searchindex, "build_index", side_effect=fake_index), \
                    patch.object(sitegen.seo, "write_robots", side_effect=record("robots")), \
                    patch.object(sitegen.seo, "write_sitemap", side_effect=record("sitemap")), \
                    patch.object(sitegen.seo, "render_404", side_effect=record("404")), \
                    patch.object(sitegen, "compress_site",
                                 side_effect=lambda d: events.append(("compress",))):
                result = sitegen._finalize_site(object())

            order = [e[0] for e in events if e[0] != "connect"]
            self.assertEqual(order, ["index", "robots", "sitemap", "404", "compress"])
            index_event = next(e for e in events if e[0] == "index")
            self.assertTrue(index_event[1] and index_event[2],
                            "search-index 必须在 assets copytree 之后生成")
            self.assertTrue((dist / "assets" / "search-index.json").is_file())
            self.assertTrue((dist / "assets" / "app.css").is_file())
            connect = next(e for e in events if e[0] == "connect")
            self.assertTrue(connect[1].startswith("file:"))
            self.assertIn("mode=ro", connect[1])
            self.assertTrue(connect[2], "sqlite3.connect 必须以 uri=True 打开只读连接")
            self.assertTrue(result["compressed"])

    def test_one_failure_does_not_block_remaining_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            events = []

            with patch.object(sitegen.log, "exception"), \
                    patch.object(sitegen, "DIST_DIR", root / "dist"), \
                    patch.object(sitegen, "STATIC_DIR", root / "static"), \
                    patch.object(sitegen, "DB_PATH", root / "test.sqlite3"), \
                    patch.object(sitegen.searchindex, "build_index",
                                 side_effect=RuntimeError("index boom")), \
                    patch.object(sitegen.seo, "write_robots",
                                 side_effect=lambda d: events.append("robots")), \
                    patch.object(sitegen.seo, "write_sitemap",
                                 side_effect=RuntimeError("sitemap boom")), \
                    patch.object(sitegen.seo, "render_404",
                                 side_effect=RuntimeError("404 boom")), \
                    patch.object(sitegen, "compress_site",
                                 side_effect=lambda d: events.append("compress")):
                result = sitegen._finalize_site(object())

            self.assertEqual(events, ["robots", "compress"])
            self.assertIsNone(result["search_index"])
            self.assertIsNone(result["sitemap"])
            self.assertIsNone(result["404"])
            self.assertTrue(result["compressed"])


if __name__ == "__main__":
    unittest.main()
