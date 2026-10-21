"""base.html 基础层渲染测试：SEO 双模式回退、私有页 noindex、搜索入口、登录态显隐。

全部为纯 Jinja 渲染（FileSystemLoader + 内存模板），不写 dist/、data/，
不 build、不联网。
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from collector import seo
from collector.config import TEMPLATES_DIR

DEFAULT_DESC = ("以推理技术为重心,聚合智能体热度榜单、AI 新闻、报告厅"
                "与个人知识库的可溯源技术观察站。")

# 私有页上下文：reports/knowledge/clipboard/notes 四种页面标志。
# knowledge 页面还需要 kb / kb_tree 两棵侧栏数据（空树即可）。
PRIVATE_CONTEXTS = {
    "reports": {"reports_page": True, "reports_total": 3},
    "knowledge": {"knowledge_page": True,
                  "kb": {"updated": "", "short_commit": "", "url": ""},
                  "kb_tree": {"dirs": {}, "notes": []}},
    "clipboard": {"clipboard_page": True},
    "notes": {"notes_page": True},
}


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["dt"] = lambda s: (str(s).replace("T", " ")[:16] + " UTC") if s else "—"
    return env


def _ctx(**overrides) -> dict:
    ctx = {
        "site": {"name": "InferTech 观察站", "lang": "zh-CN"},
        "meta": {"last_collected_at": "2026-10-21T00:00:00Z",
                 "methodology_version": "v1.5",
                 "retention": {"raw_body_days": 90}},
        "nav_directions": [],
        "assets_v": "test123",
        "stale": False,
    }
    ctx.update(overrides)
    return ctx


def _public_meta(kind: str = "home") -> dict:
    return seo.page_meta(kind, {
        "site": {"name": "InferTech 观察站", "lang": "zh-CN"},
        "base_url": "https://example.com",
        "path": "/",
    })


class BaseLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _env()
        self.base = self.env.get_template("base.html")
        url_patch = patch.dict(os.environ, {"DM_SITE_URL": "https://example.com"}, clear=False)
        url_patch.start()
        self.addCleanup(url_patch.stop)

    def render(self, **overrides) -> str:
        return self.base.render(**_ctx(**overrides))

    def head(self, html: str) -> str:
        return html.split("</head>", 1)[0]

    # ------------------------------------------------------------------
    # 1) 无 seo_meta：完整回退到现有行为
    # ------------------------------------------------------------------
    def test_no_seo_public_falls_back_to_default_description(self) -> None:
        html = self.render()
        head = self.head(html)
        self.assertIn(f'<meta name="description" content="{DEFAULT_DESC}">', head)
        self.assertNotIn("None", head)
        self.assertNotIn('rel="canonical"', html)
        self.assertNotIn('name="robots"', html)
        self.assertNotIn("application/ld+json", html)

    def test_no_seo_uses_meta_desc_before_default(self) -> None:
        html = self.render(meta_desc="方向首页的定制描述")
        self.assertIn('content="方向首页的定制描述"', html)
        self.assertNotIn(DEFAULT_DESC, html)

    def test_no_seo_private_pages_are_noindex_with_description(self) -> None:
        for label, ctx in PRIVATE_CONTEXTS.items():
            with self.subTest(page=label):
                html = self.render(**ctx)
                head = self.head(html)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', head)
                self.assertNotIn('rel="canonical"', html)
                self.assertNotIn("application/ld+json", html)
                self.assertIn(DEFAULT_DESC, head)
                self.assertNotIn("None", head)

    def test_missing_seo_meta_is_safe_when_none(self) -> None:
        # 上下文里显式给 seo_meta=None 也应按“没有 seo_meta”回退，不能报错。
        html = self.render(seo_meta=None, seo_jsonld="")
        self.assertIn(DEFAULT_DESC, html)
        self.assertNotIn('rel="canonical"', html)

    # ------------------------------------------------------------------
    # 2) 有 seo_meta：公开页输出完整 head
    # ------------------------------------------------------------------
    def test_seo_public_outputs_description_canonical_robots_og_twitter_jsonld(self) -> None:
        meta = _public_meta()
        jsonld = seo.dump_jsonld(meta)
        html = self.render(seo_meta=meta, seo_jsonld=jsonld)
        head = self.head(html)

        self.assertIn(f'<meta name="description" content="{meta["description"]}">', head)
        self.assertIn('<link rel="canonical" href="https://example.com/">', head)
        self.assertIn('<meta name="robots" content="index, follow">', head)
        self.assertIn('<meta property="og:title"', head)
        self.assertIn('<meta property="og:description"', head)
        self.assertIn('<meta property="og:type" content="website">', head)
        self.assertIn('<meta property="og:url" content="https://example.com/">', head)
        self.assertIn('<meta property="og:image"', head)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', head)
        self.assertIn('<meta name="twitter:title"', head)
        self.assertIn('<script type="application/ld+json">' + jsonld + "</script>", head)
        self.assertNotIn("None", head)

    def test_seo_public_falls_back_to_meta_jsonld_when_string_missing(self) -> None:
        # sitegen 约定同时传 seo_meta/seo_jsonld；只传前者的旧接线也不应丢 JSON-LD。
        meta = _public_meta()
        html = self.render(seo_meta=meta)
        self.assertIn('<script type="application/ld+json">', html)
        self.assertIn('"@type": "WebSite"', html)

    def test_seo_private_only_noindex_no_canonical(self) -> None:
        meta = _public_meta()  # 即使是公开页的完整 seo_meta（含 canonical）
        jsonld = seo.dump_jsonld(meta)
        for label, ctx in PRIVATE_CONTEXTS.items():
            with self.subTest(page=label):
                html = self.render(seo_meta=meta, seo_jsonld=jsonld, **ctx)
                head = self.head(html)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', head)
                self.assertNotIn('rel="canonical"', html)
                self.assertNotIn('property="og:', html)
                self.assertNotIn('name="twitter:', html)
                self.assertNotIn("application/ld+json", html)
                self.assertNotIn("None", head)

    def test_seo_private_page_meta_itself_is_noindex(self) -> None:
        meta = seo.page_meta("reports", {
            "site": {"name": "InferTech 观察站", "lang": "zh-CN"},
            "base_url": "https://example.com",
        })
        self.assertTrue(meta["noindex"])
        html = self.render(seo_meta=meta, seo_jsonld=seo.dump_jsonld(meta),
                           reports_page=True, reports_total=3)
        head = self.head(html)
        self.assertIn('<meta name="robots" content="noindex, nofollow">', head)
        self.assertNotIn('rel="canonical"', html)
        self.assertIn(DEFAULT_DESC, head)  # 私有页 description 为空时回退
        self.assertNotIn("None", head)

    # ------------------------------------------------------------------
    # 3) JSON-LD 内嵌安全
    # ------------------------------------------------------------------
    def test_jsonld_script_close_is_escaped(self) -> None:
        meta = _public_meta()
        jsonld = seo.dump_jsonld({"jsonld": {"x": "</script><script>bad()</script>"}})
        html = self.render(seo_meta=meta, seo_jsonld=jsonld)

        marker = '<script type="application/ld+json">'
        self.assertEqual(html.count(marker), 1)
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        payload = html[start:end]
        self.assertIn("<\\/script>", payload)
        self.assertNotIn("</script>", payload)

    # ------------------------------------------------------------------
    # 4) 搜索入口与资源
    # ------------------------------------------------------------------
    def test_search_entry_and_assets(self) -> None:
        html = self.render()
        self.assertIn('id="search-open"', html)
        self.assertIn('type="button"', html)
        self.assertIn('title="搜索（Ctrl+K）"', html)
        self.assertIn('aria-label="站内搜索"', html)
        self.assertIn('aria-controls="search-root"', html)
        self.assertIn('<div id="search-root"></div>', html)
        self.assertIn('<script src="/assets/search.js?v=test123" defer></script>', html)
        # 搜索按钮位于 asof-box 内、登录链接之前
        self.assertLess(html.index('class="asof-box"'), html.index('id="search-open"'))
        self.assertLess(html.index('id="search-open"'), html.index('id="nav-login"'))

    def test_new_stylesheets_come_after_style_and_assistant(self) -> None:
        html = self.render()
        style_pos = html.index('/assets/style.css?v=test123')
        assistant_pos = html.index('/assets/assistant.css?v=test123')
        charts_pos = html.index('/assets/charts.css?v=test123')
        search_css_pos = html.index('/assets/search.css?v=test123')
        self.assertLess(style_pos, assistant_pos)
        self.assertLess(assistant_pos, charts_pos)
        self.assertLess(assistant_pos, search_css_pos)

    # ------------------------------------------------------------------
    # 5) 未登录隐藏不可用功能
    # ------------------------------------------------------------------
    def test_update_buttons_carry_auth_only_for_anonymous(self) -> None:
        cases = {
            "btn-kb-update": PRIVATE_CONTEXTS["knowledge"],
            "btn-news-update": {"news_page": True, "news_meta": {}},
            "btn-update": {},
        }
        for btn_id, ctx in cases.items():
            with self.subTest(button=btn_id):
                html = self.render(**ctx)
                self.assertRegex(html, rf'<button id="{btn_id}"[^>]*class="auth-only"')

    def test_auth_only_uses_css_default_hidden_then_authed_reveal(self) -> None:
        html = self.render()
        # 新按钮必须能被 CSS 默认隐藏（style.css 的 .auth-only），并由 html.authed 显示；
        # 显示规则必须来自 CSS（首帧），不能依赖 JS。
        self.assertIn("html.authed .asof-box button.auth-only", html)
        self.assertIn("display: inline-block !important", html)

    # ------------------------------------------------------------------
    # 6) 可访问性与扩展块
    # ------------------------------------------------------------------
    def test_interactive_controls_are_labelled(self) -> None:
        html = self.render()
        self.assertIn('aria-label="站内搜索"', html)
        self.assertIn('id="nav-login" class="login-link" aria-label="登录"', html)
        self.assertIn('id="btn-theme" type="button" title="切换明暗主题" aria-label="切换明暗主题"', html)
        self.assertIn('aria-haspopup="dialog"', html)

    def test_head_extra_block_renders_once(self) -> None:
        child = self.env.from_string(
            '{% extends "base.html" %}'
            '{% block head_extra %}<meta name="x-test" content="1">{% endblock %}'
        )
        html = child.render(**_ctx())
        self.assertIn('<meta name="x-test" content="1">', html)


if __name__ == "__main__":
    unittest.main()
