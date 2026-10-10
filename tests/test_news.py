"""新闻模块测试：URL 规范化、RSS/Atom 解析、分类、产品匹配、周分组（不发真实请求）。"""
import unittest

from collector.news import (build_matcher, classify, match_products,
                            normalize_url, parse_feed, strip_html, truncate,
                            url_hash, week_key, week_range)

RULES = {
    "coding": ["claude code", "cursor", "copilot"],
    "framework": ["mcp", "langchain"],
    "models": ["gpt", "claude", "deepseek"],
    "product": ["融资"],
}

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>t</title>
<item>
  <title>Claude Code adds new agent hooks</title>
  <link>https://example.com/a?utm_source=rss&amp;id=1</link>
  <description>&lt;p&gt;Anthropic ships &lt;b&gt;agent&lt;/b&gt; updates&lt;/p&gt;</description>
  <pubDate>Wed, 08 Oct 2026 13:00:00 +0000</pubDate>
  <dc:creator>anthropic</dc:creator>
</item>
<item><title>no link item</title><description>x</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>feed</title>
<entry>
  <title type="html">LangGraph 2.0 released</title>
  <link rel="alternate" href="https://example.com/b"/>
  <link rel="self" href="https://example.com/feed"/>
  <summary>multi-agent orchestration framework</summary>
  <published>2026-10-07T09:00:00Z</published>
  <author><name>simonw</name></author>
</entry>
</feed>"""


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_tracking_and_fragment(self):
        self.assertEqual(
            normalize_url("https://Example.com/a/?utm_source=x&fbclid=abc&id=2#ref"),
            "https://example.com/a?id=2")

    def test_hash_stable_across_noise(self):
        self.assertEqual(url_hash("https://x.com/p?utm_campaign=n"),
                         url_hash("https://x.com/p#/other"))

    def test_hash_differs_for_real_change(self):
        self.assertNotEqual(url_hash("https://x.com/a"), url_hash("https://x.com/b"))


class TestParseFeed(unittest.TestCase):
    def test_rss2(self):
        items = parse_feed(RSS)
        self.assertEqual(len(items), 1)  # 无 link 的条目被丢弃
        it = items[0]
        self.assertEqual(it["title"], "Claude Code adds new agent hooks")
        self.assertEqual(it["url"], "https://example.com/a?utm_source=rss&id=1")
        self.assertEqual(it["summary"], "Anthropic ships agent updates")
        self.assertEqual(it["published_at"], "2026-10-08T13:00:00Z")
        self.assertEqual(it["author"], "anthropic")

    def test_atom_prefers_alternate_link(self):
        items = parse_feed(ATOM)
        self.assertEqual(items[0]["url"], "https://example.com/b")
        self.assertEqual(items[0]["published_at"], "2026-10-07T09:00:00Z")
        self.assertEqual(items[0]["author"], "simonw")

    def test_malformed_xml_raises(self):
        with self.assertRaises(Exception):
            parse_feed(b"<not-xml")


class TestClassify(unittest.TestCase):
    def test_first_match_wins(self):
        # "claude code" 在 coding 规则里先于 models 的 "claude"
        self.assertEqual(classify("Claude Code 3.0", "", "product", RULES), "coding")

    def test_fallback_default(self):
        self.assertEqual(classify("某公司季度财报", "", "framework", RULES), "framework")

    def test_case_insensitive(self):
        self.assertEqual(classify("CURSOR raises round", "", "product", RULES), "coding")

    def test_summary_also_searched(self):
        self.assertEqual(classify("Big update", "uses MCP internally", "product", RULES),
                         "framework")


class TestMatchProducts(unittest.TestCase):
    PRODUCTS = [
        {"id": 1, "name": "Claude Code", "repo": "anthropics/claude-code"},
        {"id": 2, "name": "Cursor", "repo": None},
        {"id": 3, "name": "千问", "repo": "QwenLM/Qwen-Agent",
         "news_aliases": ["qwen"]},
        {"id": 4, "name": "Go", "repo": None, "news_match": False},
        {"id": 5, "name": "Vercel AI SDK", "repo": "vercel/ai"},
        {"id": 6, "name": "Ai", "repo": None},   # 2 字符 ASCII 名：过泛，必须跳过
    ]

    def setUp(self):
        self.matcher = build_matcher(self.PRODUCTS)

    def test_word_boundary(self):
        hits = match_products("cursory review of cursor ide", self.matcher)
        self.assertEqual([p["id"] for p in hits], [2])

    def test_short_ascii_repo_basename_skipped(self):
        # vercel/ai 的 basename "ai" 不能把所有 AI 新闻都关联到 Vercel AI SDK
        self.assertEqual(match_products("AI is transforming everything", self.matcher), [])

    def test_full_name_still_matches(self):
        # match_products 约定接收已小写的文本（采集侧 .lower() 后传入）
        hits = match_products("building agents with the vercel ai sdk", self.matcher)
        self.assertIn(5, [p["id"] for p in hits])

    def test_repo_basename_and_alias(self):
        hits = match_products("qwen-agent gets faster inference", self.matcher)
        self.assertIn(3, [p["id"] for p in hits])

    def test_cjk_substring(self):
        hits = match_products("千问App发布新版本", self.matcher)
        self.assertIn(3, [p["id"] for p in hits])

    def test_disabled_product_never_matches(self):
        self.assertEqual(match_products("go go go", self.matcher), [])

    def test_limit(self):
        hits = match_products("cursor and claude code and qwen", self.matcher, limit=2)
        self.assertEqual(len(hits), 2)


class TestText(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual(strip_html("<p>a &amp; <b>b</b></p>"), "a & b")

    def test_truncate(self):
        self.assertEqual(len(truncate("x" * 500)), 280)
        self.assertTrue(truncate("x" * 500).endswith("…"))


class TestWeek(unittest.TestCase):
    def test_week_key(self):
        self.assertEqual(week_key("2026-10-08T13:00:00Z"), "2026-W41")

    def test_week_range_monday_to_sunday(self):
        self.assertEqual(week_range("2026-W41"), ("2026-10-05", "2026-10-11"))

    def test_year_boundary_belongs_to_next_iso_year(self):
        # 2027-01-01 属于 2026 年第 53 周
        self.assertEqual(week_key("2027-01-01T00:00:00Z"), "2026-W53")


if __name__ == "__main__":
    unittest.main()
