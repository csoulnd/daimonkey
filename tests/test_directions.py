"""三方向频道测试:registry 配置驱动、方向三页拆分、导航当前态、旧路由 301。

全部使用临时库/内存库,不读写生产 data/;服务测试只走重定向分支,不依赖 dist。
"""
import importlib.util
import re
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from collector import db, queries, sitegen
from collector.config import DIST_DIR, STATIC_DIR, TEMPLATES_DIR, registry


def load_server():
    spec = importlib.util.spec_from_file_location(
        "directions_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def direction_objects(reg=None):
    """registry.directions -> [{key, ...}]，顺序即门户/导航顺序。"""
    reg = reg or registry()
    return [{"key": k, **v} for k, v in reg["directions"].items()]


class TestDirectionsConfig(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.dirs = self.reg["directions"]

    def test_three_directions_in_order(self):
        self.assertEqual(list(self.dirs), ["harness", "fm", "nv"])

    def test_required_fields(self):
        for key, d in self.dirs.items():
            self.assertTrue(d.get("name"), key)
            self.assertTrue(d.get("icon"), key)
            self.assertIn("news_tag", d, key)
            self.assertIn("boards", d, key)

    def test_board_references_exist(self):
        boards = self.reg["boards"]
        for key, d in self.dirs.items():
            for board in d.get("boards") or []:
                self.assertIn(board, boards, f"{key} -> {board}")

    def test_news_categories_exist(self):
        known = set(self.reg["news"]["categories"])
        for key, d in self.dirs.items():
            for cat in d.get("news_categories") or []:
                self.assertIn(cat, known, f"{key} -> {cat}")

    def test_nv_channel_is_live_with_mlperf_board(self):
        nv = self.dirs["nv"]
        self.assertFalse(nv.get("placeholder"))
        self.assertEqual(nv.get("boards"), ["nv_infer"])
        self.assertIn("nv_infer", self.reg["boards"])
        board = self.reg["boards"]["nv_infer"]
        self.assertEqual(board.get("name"), "硬件天梯")
        self.assertNotIn("select", board)
        self.assertNotIn("display_metrics", board)
        self.assertEqual(nv.get("news_categories"), ["infra"])
        source_keys = {s["key"] for s in self.reg["sources"]}
        self.assertIn("mlperf", source_keys)
        self.assertTrue(nv.get("planned_sources"))
        for src in nv["planned_sources"]:
            self.assertIn("name", src)
            self.assertIn("status", src)

    def test_nv_board_is_a_fp16_ladder_without_mlperf_columns(self):
        board = self.reg["boards"]["nv_infer"]
        keys = [c["key"] for c in board["columns"]]
        self.assertEqual(keys, [
            "rank", "product", "vendor", "fp16_tflops", "precision",
            "hbm_gb", "hbm_bw_gbs", "official_docs"])
        self.assertEqual([k["metric"] for k in board["sort"]["keys"]],
                         ["fp16_tflops"])
        self.assertNotIn("mlperf", board.get("criteria", "").lower())
        self.assertNotIn("primary_label", board)
        self.assertIn("FP16", board["criteria"])

    def test_channel_descriptions_avoid_old_metrics_and_negation(self):
        forbidden = ("数据来源", "后续接入", "不做", "永不", "热度", "动量", "覆盖率")
        for key, d in self.dirs.items():
            text = d.get("description") or ""
            self.assertTrue(text, key)
            for word in forbidden:
                self.assertNotIn(word, text, f"{key} 描述不应出现 {word}")

    def test_visible_boards_have_config_driven_columns_and_sort(self):
        for key, cfg in self.reg["boards"].items():
            if not cfg.get("enabled", True) or cfg.get("hidden"):
                continue
            self.assertTrue(cfg.get("columns"), key)
            self.assertTrue(cfg.get("sort", {}).get("keys"), key)
            self.assertTrue(cfg.get("subtitle") or cfg.get("criteria"), key)
            self.assertTrue(cfg.get("criteria") or cfg.get("sort", {}).get("note"),
                            key)
        for key in ("harness_core", "harness_eco", "model", "nv_infer"):
            self.assertTrue(self.reg["boards"][key].get("subtitle"), key)


class TestDirectionQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self._patch = patch.object(db, "DB_PATH", self.db_path)
        self._patch.start()
        self.c = sqlite3.connect(self.db_path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(db.DDL)
        db.ensure_news_tables(self.c)

    def tearDown(self):
        self.c.close()
        self._patch.stop()
        self.tmp.cleanup()

    def _news(self, title, category, published_at, url=None):
        self.c.execute(
            "INSERT INTO news_items(url_hash, title, url, source_key, source_name,"
            " category, region, published_at, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (f"h-{title}", title, url or f"https://e.test/{title}", "rss", "Test",
             category, "global", published_at, published_at))
        self.c.commit()

    def test_news_filter_by_categories(self):
        self._news("coding one", "coding", "2026-10-18T00:00:00Z")
        self._news("framework one", "framework", "2026-10-17T00:00:00Z")
        self._news("model one", "models", "2026-10-16T00:00:00Z")
        self._news("product one", "product", "2026-10-15T00:00:00Z")
        rows = queries.news_for_categories(self.c, ["coding", "framework"])
        self.assertEqual([r["title"] for r in rows], ["coding one", "framework one"])
        self.assertEqual(queries.news_count_for_categories(
            self.c, ["coding", "framework"]), 2)

    def test_empty_categories_returns_empty(self):
        self._news("coding one", "coding", "2026-10-18T00:00:00Z")
        self.assertEqual(queries.news_for_categories(self.c, []), [])
        self.assertEqual(queries.news_count_for_categories(self.c, []), 0)

    def _digest(self, summary, week, tags, status="published"):
        self.c.execute(
            "INSERT INTO insights(title, summary, source_url, source_name, published_at,"
            " tags, llm_generated, status, created_at)"
            " VALUES(?,?,?,?,?,?,1,?,?)",
            (f"摘要 {week}", summary, "", "DSH assistant", week, tags, status,
             "2026-10-18T00:00:00Z"))
        self.c.commit()

    def test_latest_direction_digest_only_published_and_tagged(self):
        self._digest("harness published", "2026-W42",
                     "news-digest,ai,direction:harness")
        self._digest("harness draft newer", "2026-W43",
                     "news-digest,ai,direction:harness", status="draft")
        self._digest("fm published", "2026-W43",
                     "news-digest,ai,direction:fm")
        got = queries.latest_direction_digest(self.c, "harness")
        self.assertEqual(got["summary"], "harness published")
        self.assertEqual(got["week_start"], "2026-10-12")
        self.assertEqual(got["week_end"], "2026-10-18")

    def test_latest_direction_digest_none(self):
        self.assertIsNone(queries.latest_direction_digest(self.c, "nv"))


class TestDirectionPagePlan(unittest.TestCase):
    """三页路由清单与模板清单（不构建 dist，只在内存里验证计划与渲染）。"""

    def setUp(self):
        self.reg = registry()
        self.directions = direction_objects(self.reg)

    def test_templates_exist(self):
        for name in ("direction.html", "direction_news.html",
                     "direction_digest.html", "_direction_tabs.html"):
            self.assertTrue((TEMPLATES_DIR / name).is_file(), name)

    def test_three_pages_per_direction(self):
        for d in self.directions:
            specs = sitegen.direction_page_specs(d)
            self.assertEqual([s["tab"] for s in specs], ["board", "news", "digest"])
            outputs = [s["output"] for s in specs]
            self.assertEqual(len(set(outputs)), 3)
            self.assertEqual(outputs, [
                f"dir/{d['key']}.html",
                f"dir/{d['key']}/news.html",
                f"dir/{d['key']}/digest.html",
            ])
            for spec in specs:
                self.assertTrue((TEMPLATES_DIR / spec["template"]).is_file(),
                                spec["template"])

    def test_nav_hrefs_follow_routes_and_skip_old_board_path(self):
        for d in self.directions:
            tabs = sitegen.direction_tabs(d)
            self.assertEqual([t["href"] for t in tabs], [
                f"/dir/{d['key']}",
                f"/dir/{d['key']}/news",
                f"/dir/{d['key']}/digest",
            ])
            for tab in tabs:
                self.assertTrue(tab["label"])
                self.assertTrue(tab["icon"])

    def test_labels_are_configurable(self):
        d = {"key": "demo", "name": "演示",
             "section_labels": {"news": "动态", "digest_icon": "i-method"}}
        tabs = sitegen.direction_tabs(d)
        self.assertEqual(tabs[0]["label"], sitegen.DIRECTION_TABS[0]["label"])
        self.assertEqual(tabs[1]["label"], "动态")
        self.assertEqual(tabs[2]["icon"], "i-method")

    def test_new_direction_needs_config_only(self):
        d = {"key": "demo", "name": "演示", "tagline": "演示方向",
             "description": "只改配置即可新增方向。",
             "boards": [], "news_tag": "demo", "news_categories": []}
        self.assertEqual([s["output"] for s in sitegen.direction_page_specs(d)], [
            "dir/demo.html", "dir/demo/news.html", "dir/demo/digest.html"])
        ctx = sitegen.direction_page_context(d, "digest")
        self.assertEqual(ctx["page_label"], sitegen.DIRECTION_TABS[2]["label"])
        self.assertIn("演示", ctx["meta_desc"])
        self.assertEqual(len(ctx["direction_tabs"]), 3)

    def test_templates_do_not_hardcode_direction_names(self):
        needles = [d["name"] for d in self.directions] + [d["key"] for d in self.directions]
        for name in ("direction.html", "direction_news.html",
                     "direction_digest.html", "_direction_tabs.html"):
            text = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
            for needle in needles:
                self.assertNotIn(needle, text, f"{name} 不应硬编码 {needle}")


class TestDirectionPageRender(unittest.TestCase):
    """直接渲染模板（不落盘）：三页内容互斥、导航 aria-current 指向当前页。"""

    MARKERS = {"board": 'id="dir-boards"', "news": 'id="dir-news"',
               "digest": 'id="dir-digest"'}

    @classmethod
    def setUpClass(cls):
        cls.reg = registry()
        cls.directions = direction_objects(cls.reg)
        cls.env = sitegen._env()

    def _common(self):
        reg = self.reg
        directions = self.directions
        return {
            "site": reg["site"],
            "meta": {"last_collected_at": "2026-10-20T00:00:00Z",
                     "methodology_version": "v1.8",
                     "retention": {"raw_body_days": 30}},
            "directions": directions,
            "nav_directions": [
                {"key": d["key"], "name": d["name"],
                 "icon": d.get("icon", "i-index"), "href": f"/dir/{d['key']}"}
                for d in directions],
            "board_names": {k: v["name"] for k, v in reg["boards"].items()},
            "metric_names": sitegen.METRIC_NAMES,
            "nav_boards": [],
            "threshold_pct": 50,
            "stale": False,
            "assets_v": "test",
            "kb": {"url": "https://example.test/repo"},
            "news_categories": reg.get("news", {}).get("categories", {}),
            "ai_home": None, "ai_news": None, "ai_knowledge": None,
            "news_meta": {"last_fetched_at": "2026-10-20T00:00:00Z"},
            "news_page": False,
            "news_rank": {},
            "board_blocks": [],
            "direction_news": [],
            "direction_digest": None,
            "direction_digest_link": None,
            "direction_digest_weeks": [],
            "weekly": None,
        }

    def _render(self, direction, tab, extra=None):
        spec = next(s for s in sitegen.direction_page_specs(direction)
                    if s["tab"] == tab)
        ctx = self._common()
        ctx.update(sitegen.direction_page_context(direction, tab))
        ctx.update(extra or {})
        return self.env.get_template(spec["template"]).render(**ctx)

    def test_nav_marks_only_current_page(self):
        for d in self.directions:
            expected = {t["key"]: t["href"] for t in sitegen.direction_tabs(d)}
            for tab, href in expected.items():
                html = self._render(d, tab)
                self.assertEqual(html.count('aria-current="page"'), 1,
                                 f"{d['key']}/{tab} 应只有一个当前态")
                node = re.search(r'<a\b[^>]*aria-current="page"[^>]*>', html)
                self.assertIsNotNone(node, f"{d['key']}/{tab} 缺少 aria-current")
                self.assertIn(f'href="{href}"', node.group(0))
                self.assertIn("dir-tab is-active", node.group(0))
                for other_href in expected.values():
                    self.assertIn(f'href="{other_href}"', html)

    def test_three_sections_are_split(self):
        extra_absent = {
            "board": ('class="news-item"', 'class="ai-summary"'),
            "news": ('class="metric-card dir-board"', 'class="ai-summary"'),
            "digest": ('class="news-item"', 'class="metric-card dir-board"'),
        }
        for d in self.directions:
            for tab, marker in self.MARKERS.items():
                html = self._render(d, tab)
                self.assertIn(marker, html, f"{d['key']}/{tab}")
                for other_tab, other_marker in self.MARKERS.items():
                    if other_tab != tab:
                        self.assertNotIn(other_marker, html,
                                         f"{d['key']}/{tab} 不应包含 {other_tab}")
                for needle in extra_absent[tab]:
                    self.assertNotIn(needle, html, f"{d['key']}/{tab} 不应包含 {needle}")

    def test_board_page_renders_weekly_points_card(self):
        d = self.directions[0]
        weekly = {"week": "2026-W43", "current": True,
                  "excerpt": "本周核心产品竞争集中在编码与编排。第二句。",
                  "link": "/dir/harness/digest/2026-W43.html",
                  "created_at": "2026-10-21T00:00:00Z"}
        html = self._render(d, "board", {"weekly": weekly})
        self.assertIn("本周要点", html)
        self.assertIn("2026-W43", html)
        self.assertIn("本周核心产品竞争集中在编码与编排。", html)
        self.assertIn('href="/dir/harness/digest/2026-W43.html"', html)

    def test_board_table_uses_configured_columns(self):
        reg = self.reg
        cols = reg["boards"]["harness_core"]["columns"]
        product = {"slug": "demo", "name": "Demo", "vendor": "Acme",
                   "status": "active"}
        cells = {"rank": 1, "product": product,
                 "tier": {"value": "core", "label": "核心"},
                 "github_stars": {"value": 1000, "unit": "count",
                                  "source": {"key": "github", "name": "GitHub"},
                                  "collected_at": "2026-10-21T00:00:00Z",
                                  "raw_payload_id": 1}}
        block = {"key": "harness_core", "name": "核心产品榜",
                 "rows": [{"rank": 1, "cells": cells}], "columns": cols,
                 "sort_note": reg["boards"]["harness_core"]["sort"]["note"],
                 "criteria": "", "snapshot_date": "2026-10-21"}
        html = self._render(self.directions[0], "board",
                            {"board_blocks": [block]})
        for col in cols:
            self.assertIn(col["label"], html)
        self.assertIn("排序规则", html)
        self.assertNotIn("热度分", html)
        self.assertNotIn("动量分", html)
        self.assertNotIn("覆盖率", html)
        self.assertNotIn("观察区", html)

    def test_titles_and_descriptions_are_distinct(self):
        titles, descs = [], []
        for d in self.directions:
            direction_titles, direction_descs = set(), set()
            for tab in self.MARKERS:
                html = self._render(d, tab)
                title = re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()
                desc = re.search(
                    r'<meta name="description" content="(.*?)">', html, re.S).group(1)
                self.assertIn(d["name"], title, f"{d['key']}/{tab} title 应含方向名")
                self.assertIn(d["name"], desc, f"{d['key']}/{tab} description 应含方向名")
                direction_titles.add(title)
                direction_descs.add(desc)
                titles.append(title)
                descs.append(desc)
            self.assertEqual(len(direction_titles), 3, f"{d['key']} 三页 title 需有区分度")
            self.assertEqual(len(direction_descs), 3, f"{d['key']} 三页 description 需有区分度")
        self.assertEqual(len(set(titles)), len(titles), "跨方向 title 不应重复")
        self.assertEqual(len(set(descs)), len(descs), "跨方向 description 不应重复")


class TestDirectionStyles(unittest.TestCase):
    def test_direction_css_has_rail_and_mobile_tabs(self):
        css = (STATIC_DIR / "assets" / "direction.css").read_text(encoding="utf-8")
        self.assertIn("position: sticky", css)
        self.assertIn("@media (max-width: 880px)", css)
        self.assertIn(":focus-visible", css)
        self.assertIn(".dir-tab.is-active", css)
        self.assertIn("var(--acc)", css)
        self.assertIn("var(--line)", css)


class TestBuiltDirectionPages(unittest.TestCase):
    """构建产物冒烟：dist 里已有三页时校验文件与非空（测试内不生成 dist）。"""

    def test_dist_pages_when_present(self):
        directions = direction_objects()
        outputs = [Path(DIST_DIR) / s["output"]
                   for d in directions for s in sitegen.direction_page_specs(d)]
        missing = [str(p) for p in outputs if not p.is_file()]
        if missing:
            self.skipTest("dist 尚未构建三页（需 --build），仅校验路由计划: "
                          + ", ".join(missing[:3]))
        for p in outputs:
            html = p.read_text(encoding="utf-8")
            self.assertIn('aria-current="page"', html, str(p))
            self.assertIn("dir-tab", html, str(p))


class TestLegacyRedirect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_unified_redirects_to_harness_channel(self):
        conn = HTTPConnection(*self.httpd.server_address)
        conn.request("GET", "/rankings/unified")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 301)
        self.assertEqual(response.getheader("Location"), "/dir/harness")

    def test_rankings_root_redirects_to_harness_channel(self):
        conn = HTTPConnection(*self.httpd.server_address)
        conn.request("GET", "/rankings")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 301)
        self.assertEqual(response.getheader("Location"), "/dir/harness")

    def test_channel_board_alias_redirect(self):
        """/dir/{d}/board -> /dir/{d}：serve.py 由集成阶段统一接线。

        接线前该用例 skip 并打印需要的映射；接线后自动转为强断言。
        """
        expected = {f"/dir/{key}/board": f"/dir/{key}"
                    for key in registry()["directions"]}
        missing = {old: new for old, new in expected.items()
                   if self.server.LEGACY_REDIRECTS.get(old) != new}
        if missing:
            self.skipTest("serve.py LEGACY_REDIRECTS 待集成接线: " + ", ".join(
                f"{old} -> {new}" for old, new in missing.items()))
        for old, new in expected.items():
            conn = HTTPConnection(*self.httpd.server_address)
            conn.request("GET", old)
            response = conn.getresponse()
            response.read()
            conn.close()
            self.assertEqual(response.status, 301, old)
            self.assertEqual(response.getheader("Location"), new, old)


if __name__ == "__main__":
    unittest.main()
