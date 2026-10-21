"""SEO 元数据与 robots/sitemap/feed 生成测试。

全部在 TemporaryDirectory 中完成，不读写生产 dist/、data/，不访问网络。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from jinja2 import Environment, FileSystemLoader

from collector import seo
from collector.config import TEMPLATES_DIR, registry


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestPageMeta(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DM_SITE_URL": "https://example.com"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_home_has_full_meta(self):
        meta = seo.page_meta("home", {})
        self.assertIn("InferTech", meta["title"])
        self.assertTrue(meta["description"])
        self.assertEqual(meta["canonical"], "https://example.com/")
        self.assertEqual(meta["robots"], "index, follow")
        self.assertFalse(meta["noindex"])
        self.assertEqual(meta["og"]["type"], "website")
        self.assertEqual(meta["og"]["url"], meta["canonical"])
        self.assertEqual(meta["og"]["locale"], "zh_CN")
        self.assertEqual(meta["twitter"]["card"], "summary_large_image")
        self.assertEqual(meta["jsonld"]["@type"], "WebSite")
        self.assertEqual(meta["jsonld"]["@context"], "https://schema.org")
        self.assertTrue(meta["image"].startswith("https://example.com/"))

    def test_product_uses_name_metrics_and_rank(self):
        product = {
            "slug": "claude-code",
            "name": "Claude Code",
            "vendor": "Anthropic",
            "category": "coding",
            "latest_ranks": {"harness_core": {"rank": 3, "heat_score": 98.2}},
            "metrics_series": {
                "github_stars": [{"date": "2026-10-20", "value": 12345}],
                "npm_monthly_downloads": [{"date": "2026-10-20", "value": 456789}],
            },
        }
        meta = seo.page_meta("product", {"p": product, "board_names": {
            "harness_core": "核心产品榜"}})
        self.assertIn("Claude Code", meta["title"])
        self.assertIn("Anthropic", meta["title"])
        self.assertIn("Claude Code", meta["description"])
        self.assertIn("12,345", meta["description"])
        self.assertIn("核心产品榜第 3 名", meta["description"])
        self.assertEqual(meta["canonical"], "https://example.com/product/claude-code")
        self.assertEqual(meta["og"]["type"], "article")
        self.assertEqual(meta["jsonld"]["@type"], "SoftwareApplication")
        self.assertEqual(meta["jsonld"]["url"], meta["canonical"])
        self.assertEqual(meta["jsonld"]["author"]["name"], "Anthropic")

    def test_ranking_uses_board_name_and_snapshot_date(self):
        meta = seo.page_meta("ranking", {
            "board": "harness_core",
            "name": "核心产品榜",
            "snapshot_date": "2026-10-21",
            "items": [
                {"name": "Claude Code", "heat_score": 99.2, "slug": "claude-code"},
                {"name": "OpenClaw", "heat_score": 88.0, "slug": "openclaw"},
            ],
        })
        self.assertIn("核心产品榜", meta["title"])
        self.assertIn("2026-10-21", meta["title"])
        self.assertIn("第 1 名 Claude Code", meta["description"])
        self.assertIn("2026-10-21", meta["description"])
        self.assertEqual(meta["canonical"], "https://example.com/rankings/harness_core")
        self.assertEqual(meta["jsonld"]["@type"], "ItemList")
        self.assertEqual(len(meta["jsonld"]["itemListElement"]), 2)
        self.assertEqual(meta["jsonld"]["itemListElement"][0]["position"], 1)

    def test_direction_carries_date_and_boards(self):
        meta = seo.page_meta("direction", {
            "direction": {
                "key": "harness", "name": "智能体",
                "description": "关注能自主执行、多步编排的智能体产品。",
                "boards": ["harness_core", "harness_eco"],
            },
            "news_meta": {"last_fetched_at": "2026-10-20T18:36:03Z"},
            "board_names": {"harness_core": "核心产品榜",
                            "harness_eco": "生态项目榜"},
        })
        self.assertIn("智能体频道", meta["title"])
        self.assertIn("2026-10-20", meta["title"])
        self.assertIn("核心产品榜", meta["description"])
        self.assertIn("生态项目榜", meta["description"])
        self.assertEqual(meta["canonical"], "https://example.com/dir/harness")
        self.assertEqual(meta["jsonld"]["@type"], "CollectionPage")
        self.assertEqual(meta["jsonld"]["dateModified"], "2026-10-20")

    # ---- 方向频道三子页：标题/描述必须区分（真实 registry directions）----
    DIRECTION_TABS = ("board", "news", "digest")
    TAB_LABELS = {"board": "榜单", "news": "新闻", "digest": "每周总结"}
    TAB_SEGMENTS = {"board": "", "news": "/news", "digest": "/digest"}

    def _direction_ctx(self, key: str, tab: str | None = None) -> dict:
        """模拟 sitegen.render 的入参：direction 上下文 + seo_ctx.path。"""
        reg = registry()
        ctx = {
            "direction": {"key": key, **reg["directions"][key]},
            "news_meta": {"last_fetched_at": "2026-10-20T18:36:03Z"},
            "board_names": {k: v["name"] for k, v in reg["boards"].items()},
            "path": f"/dir/{key}" + (self.TAB_SEGMENTS[tab] if tab else ""),
        }
        if tab:
            ctx["active_tab"] = tab
            ctx["page_label"] = self.TAB_LABELS[tab]
        return ctx

    def test_direction_subpages_have_distinct_meta(self):
        for key, direction in registry()["directions"].items():
            metas = {tab: seo.page_meta("direction", self._direction_ctx(key, tab))
                     for tab in self.DIRECTION_TABS}
            titles = [metas[tab]["title"] for tab in self.DIRECTION_TABS]
            descs = [metas[tab]["description"] for tab in self.DIRECTION_TABS]
            names = [metas[tab]["jsonld"]["name"] for tab in self.DIRECTION_TABS]
            self.assertEqual(len(set(titles)), 3, f"{key} 三页 title 需两两不同: {titles}")
            self.assertEqual(len(set(descs)), 3, f"{key} 三页 description 需两两不同: {descs}")
            self.assertEqual(len(set(names)), 3, f"{key} jsonld.name 需随子页变化")
            focus = {"board": "榜单与指标", "news": "方向新闻", "digest": "每周总结"}
            for tab in self.DIRECTION_TABS:
                meta = metas[tab]
                self.assertIn(direction["name"], meta["title"], f"{key}/{tab}")
                self.assertIn(direction["name"], meta["description"], f"{key}/{tab}")
                self.assertIn(self.TAB_LABELS[tab], meta["title"], f"{key}/{tab}")
                self.assertIn(focus[tab], meta["description"], f"{key}/{tab}")
                self.assertLessEqual(len(meta["title"]), 90, f"{key}/{tab}")
                self.assertLessEqual(len(meta["description"]), 160, f"{key}/{tab}")
                self.assertEqual(meta["jsonld"]["url"], meta["canonical"], f"{key}/{tab}")
                self.assertEqual(meta["og"]["title"], meta["title"], f"{key}/{tab}")
            self.assertEqual(metas["board"]["canonical"], f"https://example.com/dir/{key}")
            self.assertEqual(metas["news"]["canonical"],
                             f"https://example.com/dir/{key}/news")
            self.assertEqual(metas["digest"]["canonical"],
                             f"https://example.com/dir/{key}/digest")

    def test_direction_without_tab_keeps_legacy_combined_title(self):
        ctx = self._direction_ctx("nv")   # 模拟旧调用方：不传 active_tab/page_label
        meta = seo.page_meta("direction", ctx)
        site_name = registry()["site"]["name"]
        self.assertEqual(
            meta["title"],
            f"{ctx['direction']['name']}频道 · 新闻/榜单/周总结"
            f"（2026-10-20） | {site_name}")
        self.assertEqual(meta["canonical"], "https://example.com/dir/nv")
        self.assertEqual(meta["jsonld"]["name"], "硬件频道")
        self.assertLessEqual(len(meta["title"]), 90)
        self.assertLessEqual(len(meta["description"]), 160)

    def test_direction_page_label_override_is_used(self):
        # registry.section_labels -> sitegen.direction_page_context -> page_label
        ctx = self._direction_ctx("harness", "news")
        ctx["direction"] = {**ctx["direction"], "section_labels": {"news": "动态"}}
        ctx["page_label"] = "动态"
        meta = seo.page_meta("direction", ctx)
        self.assertIn("智能体频道 · 动态", meta["title"])
        self.assertEqual(meta["canonical"], "https://example.com/dir/harness/news")

    def test_news_digest_uses_week_and_summary(self):
        meta = seo.page_meta("news", {
            "direction": {"key": "harness", "name": "智能体"},
            "week": "2026-W42",
            "week_start": "2026-10-12",
            "week_end": "2026-10-18",
            "digest_items": [{"title": "一条新闻"}],
            "ai_digest": {"summary": "本周智能体方向的关键进展。",
                          "created_at": "2026-10-19T00:00:00Z"},
        })
        self.assertIn("2026-W42", meta["title"])
        self.assertIn("2026-10-12", meta["title"])
        self.assertIn("本周智能体方向的关键进展", meta["description"])
        self.assertEqual(meta["canonical"],
                         "https://example.com/dir/harness/digest/2026-W42.html")
        self.assertEqual(meta["jsonld"]["@type"], "NewsArticle")
        self.assertEqual(meta["jsonld"]["datePublished"], "2026-10-12")

    def test_article_uses_summary(self):
        meta = seo.page_meta("article", {
            "title": "一个新闻标题",
            "summary": "这是新闻摘要，用来生成 meta description。",
            "url": "https://news.example.org/post/1",
            "published_at": "2026-10-20T12:00:00Z",
            "source_name": "Example News",
        })
        self.assertIn("一个新闻标题", meta["title"])
        self.assertIn("这是新闻摘要", meta["description"])
        self.assertEqual(meta["canonical"], "https://news.example.org/post/1")
        self.assertEqual(meta["jsonld"]["@type"], "NewsArticle")
        self.assertEqual(meta["jsonld"]["datePublished"], "2026-10-20")

    def test_private_pages_are_noindex_and_have_no_jsonld(self):
        for kind in ("reports", "knowledge", "clipboard", "notes", "raw", "login", "404"):
            meta = seo.page_meta(kind, {})
            self.assertEqual(meta["robots"], "noindex, nofollow", kind)
            self.assertTrue(meta["noindex"], kind)
            self.assertIsNone(meta["canonical"], kind)
            self.assertIsNone(meta["jsonld"], kind)
            self.assertEqual(meta["og"], {}, kind)
            self.assertEqual(meta["twitter"], {}, kind)

    def test_kind_aliases(self):
        self.assertEqual(seo.page_meta("index", {})["canonical"], "https://example.com/")
        self.assertEqual(seo.page_meta("dir", {"direction": {"key": "nv", "name": "硬件"}})
                         ["canonical"], "https://example.com/dir/nv")

    def test_descriptions_are_page_specific(self):
        home = seo.page_meta("home", {})["description"]
        product = seo.page_meta("product", {
            "p": {"slug": "x", "name": "X", "latest_ranks": {}}})["description"]
        ranking = seo.page_meta("ranking", {
            "board": "model", "name": "模型评测",
            "snapshot_date": "2026-10-21", "items": []})["description"]
        self.assertNotEqual(home, product)
        self.assertNotEqual(product, ranking)
        self.assertNotEqual(home, ranking)

    def test_description_is_truncated(self):
        meta = seo.page_meta("article", {"title": "长文", "summary": "很长的摘要。" * 100})
        self.assertLessEqual(len(meta["description"]), 160)
        self.assertTrue(meta["description"].endswith("…"))

    def test_dump_jsonld_escapes_script_close(self):
        text = seo.dump_jsonld(seo.page_meta("article", {"title": "x", "summary": "y"}))
        self.assertNotIn("</", text)
        text = seo.dump_jsonld({"jsonld": {"x": "</script>"}})
        self.assertNotIn("</script>", text)
        self.assertIn("<\\/script>", text)

    def test_base_url_normalization_and_placeholder(self):
        self.assertEqual(seo.site_base_url("example.com/"), "https://example.com")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(seo.site_base_url(), "http://<host>")
            self.assertTrue(seo.is_placeholder_base(seo.site_base_url()))


class TestRender404(unittest.TestCase):
    def test_render_404_uses_environment_and_is_noindex(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        with tempfile.TemporaryDirectory() as tmp:
            target = seo.render_404(env, tmp)
            self.assertEqual(target, Path(tmp) / "404.html")
            text = target.read_text(encoding="utf-8")
            self.assertIn('name="robots" content="noindex', text)
            self.assertIn("页面不存在", text)
            self.assertIn("InferTech 观察站", text)
            self.assertIn("/assets/search.js", text)
            self.assertIn("/assets/search.css", text)
            self.assertNotIn("{%", text)
            self.assertIn('id="search-open"', text)
            self.assertIn('id="search-root"', text)

    def test_render_404_creates_missing_dist(self):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        with tempfile.TemporaryDirectory() as tmp:
            target = seo.render_404(env, Path(tmp) / "dist")
            self.assertTrue(target.is_file())


class TestRobots(unittest.TestCase):
    def test_robots_rules_and_sitemap(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"DM_SITE_URL": "https://example.com"}, clear=False):
            target = seo.write_robots(tmp)
            self.assertEqual(target, Path(tmp) / "robots.txt")
            text = target.read_text(encoding="utf-8")
            self.assertIn("User-agent: *", text)
            self.assertIn("Allow: /", text)
            for path in ("/reports", "/knowledge", "/clipboard", "/notes",
                         "/raw", "/api/v1/raw"):
                self.assertIn(f"Disallow: {path}", text)
            self.assertIn("Sitemap: https://example.com/sitemap.xml", text)
            self.assertNotIn("DM_SITE_URL 未设置", text)

    def test_robots_placeholder_comment(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {}, clear=True):
            text = seo.write_robots(tmp).read_text(encoding="utf-8")
            self.assertIn("DM_SITE_URL 未设置", text)
            self.assertIn("Sitemap: http://<host>/sitemap.xml", text)


class TestSitemap(unittest.TestCase):
    def _make_dist(self, root: Path) -> None:
        for rel in (
            "index.html",
            "product/claude-code.html",
            "rankings/harness_core.html",
            "dir/harness.html",
            "dir/harness/digest/2026-W42.html",
            "sources.html",
            "api.html",
            "design/prototype.html",
            "login.html",
            "404.html",
            "clipboard.html",
            "knowledge.html",
            "reports.html",
            "raw/42.html",
            "reports/index.html",
            "knowledge/index.html",
            "knowledge-alice/index.html",
            "notes/talk.html",
            "assets/search-index.json",
            "assets/search-index.json.gz",
        ):
            _write(root / rel)

    def test_sitemap_traverses_public_html_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_dist(root)
            target = seo.write_sitemap(root, "https://example.com")
            self.assertEqual(target, root / "sitemap.xml")
            tree = ElementTree.parse(target)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locs = [e.text for e in tree.findall(".//sm:loc", ns)]
            self.assertEqual(locs[0], "https://example.com/")
            for loc in (
                "https://example.com/product/claude-code",
                "https://example.com/rankings/harness_core",
                "https://example.com/dir/harness",
                "https://example.com/dir/harness/digest/2026-W42",
                "https://example.com/sources",
                "https://example.com/api",
            ):
                self.assertIn(loc, locs)
            joined = "\n".join(locs)
            for private in ("/raw/", "/reports", "/knowledge", "/notes/",
                            "/clipboard", "/reports.html", "/knowledge.html",
                            "/clipboard.html", "/login", "/404", ".gz"):
                self.assertNotIn(private, joined)
            # 每个 <url> 有 lastmod，且首页 priority 最高
            first = tree.find(".//sm:url", ns)
            self.assertIsNotNone(first.find("sm:lastmod", ns))
            self.assertEqual(first.find("sm:priority", ns).text, "1.0")
            self.assertIn("daily", first.find("sm:changefreq", ns).text)

    def test_sitemap_placeholder_comment_and_valid_xml(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            _write(root / "index.html")
            text = seo.write_sitemap(root).read_text(encoding="utf-8")
            self.assertIn("DM_SITE_URL 未设置", text)
            self.assertIn("&lt;host&gt;", text)
            ElementTree.fromstring(text)  # 必须是合法 XML

    def test_sitemap_ignores_missing_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = seo.write_sitemap(Path(tmp) / "dist", "https://example.com")
            self.assertTrue(target.is_file())
            self.assertIn("</urlset>", target.read_text(encoding="utf-8"))

    def test_sitemap_exclude_and_default_design_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in (
                "index.html",
                "rankings/harness_core.html",
                "rankings/unified.html",
                "rankings/coding.html",
                "design/index.html",
                "design/a-midnight-terminal.html",
                "product/x.html",
            ):
                _write(root / rel)
            target = seo.write_sitemap(
                root, "https://example.com",
                exclude={"/rankings/unified", "/rankings/coding"})
            # sitemap 不预压缩，排除项也就不会出现 .gz 变体
            self.assertFalse((root / "sitemap.xml.gz").exists())
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locs = [e.text for e in ElementTree.parse(target).findall(".//sm:loc", ns)]
            for excluded in (
                "https://example.com/rankings/unified",
                "https://example.com/rankings/coding",
                "https://example.com/design",
                "https://example.com/design/a-midnight-terminal",
            ):
                self.assertNotIn(excluded, locs)
            self.assertIn("https://example.com/rankings/harness_core", locs)
            self.assertIn("https://example.com/product/x", locs)

    def test_sitemap_exclude_normalizes_html_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("a/b.html", "a/index.html", "c/index.html", "c/d.html",
                        "rankings/old.html", "rankings/keep.html"):
                _write(root / rel)
            target = seo.write_sitemap(
                root, "https://example.com",
                exclude={"a/b.html", "/c/index.html", "rankings/old.html"},
                exclude_prefixes=())
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locs = [e.text for e in ElementTree.parse(target).findall(".//sm:loc", ns)]
            # a/b.html 相对写法与 /c/index.html 目录写法都能命中规范路径
            self.assertNotIn("https://example.com/a/b", locs)
            self.assertNotIn("https://example.com/c", locs)
            self.assertNotIn("https://example.com/rankings/old", locs)
            self.assertIn("https://example.com/a", locs)
            self.assertIn("https://example.com/c/d", locs)
            self.assertIn("https://example.com/rankings/keep", locs)


class TestFeed(unittest.TestCase):
    def test_feed_is_valid_atom_with_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                {"title": "A <B> & C", "url": "https://news.example.org/a?x=1&y=2",
                 "summary": "摘要 & 说明", "published_at": "2026-10-20T12:00:00Z",
                 "source_name": "Example", "direction": "harness"},
                {"title": "第二条", "url": "/dir/fm", "date": "2026-10-19",
                 "direction": "fm"},
            ]
            target = seo.write_feed(tmp, items, "https://example.com")
            tree = ElementTree.parse(target)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            titles = [e.text for e in tree.findall(".//a:entry/a:title", ns)]
            self.assertEqual(titles, ["A <B> & C", "第二条"])
            links = [e.get("href") for e in tree.findall(".//a:entry/a:link", ns)]
            self.assertIn("https://news.example.org/a?x=1&y=2", links)
            self.assertIn("https://example.com/dir/fm", links)
            cats = [e.get("term") for e in tree.findall(".//a:entry/a:category", ns)]
            self.assertEqual(cats, ["harness", "fm"])
            updated = tree.find(".//a:entry/a:updated", ns).text
            self.assertRegex(updated, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_empty_feed_still_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = seo.write_feed(tmp, [], "https://example.com")
            tree = ElementTree.parse(target)
            self.assertEqual(tree.getroot().tag, "{http://www.w3.org/2005/Atom}feed")
            self.assertEqual(tree.findall("{http://www.w3.org/2005/Atom}entry"), [])


if __name__ == "__main__":
    unittest.main()
