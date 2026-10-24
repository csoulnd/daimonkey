"""学习内容（/learn）加载、校验与 Markdown 链接改写测试。

不写 dist/、不联网；结构校验用临时目录，真实内容只做形状断言。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import learn


class LearnContentTest(unittest.TestCase):
    def test_repository_content_loads(self) -> None:
        series = learn.load_all()
        self.assertTrue(series, "content/learn 下应至少有一个系列")
        ids = {s["id"] for s in series}
        self.assertIn("inference", ids)
        inference = next(s for s in series if s["id"] == "inference")
        self.assertEqual(inference["title"], "LLM 推理技术基础")
        self.assertTrue(inference["parts"])
        self.assertEqual(inference["total"], 44)
        self.assertGreater(inference["total"], inference["count"])
        planned = [c for p in inference["parts"] for c in p["chapters"] if c.get("planned")]
        self.assertTrue(planned)
        self.assertTrue(all(c.get("title") and c.get("url") is None for c in planned))

    def test_introduction_article(self) -> None:
        series = learn.load_all()
        articles = {a["slug"]: a for s in series for a in s["articles"]}
        self.assertIn("introduction", articles)
        art = articles["introduction"]
        self.assertEqual(art["title"], "导读")
        self.assertEqual(art["url"], "/learn/inference/introduction")
        self.assertTrue(art["one_liner"])
        self.assertGreaterEqual(art["read_time"], 1)

    def test_knowledge_base_article(self) -> None:
        series = learn.load_all()
        articles = {a["slug"]: a for s in series for a in s["articles"]}
        self.assertIn("knowledge-base-method", articles)
        art = articles["knowledge-base-method"]
        self.assertEqual(art["title"], "知识库核心思路")
        self.assertEqual(art["type"], "map")
        self.assertEqual(art["prereq"], ["introduction"])
        self.assertEqual(art["url"], "/learn/inference/knowledge-base-method")

    def test_relative_links_assets_and_kb_links_are_rewritten(self) -> None:
        fragment, toc = learn._render_markdown(
            "# T\n\n[Next](request-lifecycle.md)\n\n![x](assets/01.svg)\n\n"
            "[Note](../knowledge/infra/x.md)\n\n## 小节\n",
            "inference", "introduction", {"request-lifecycle"},
            {"knowledge/infra/x.md"})
        self.assertIn("/learn/inference/request-lifecycle", fragment)
        self.assertIn("/learn/inference/assets/01.svg", fragment)
        self.assertIn("/knowledge/notes/knowledge/infra/x.html", fragment)
        self.assertIn('loading="lazy"', fragment)
        self.assertIn('href="#小节"', toc)

    def test_one_liner_over_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            series_dir = root / "x"
            series_dir.mkdir()
            (series_dir / "series.yaml").write_text(
                "id: x\nparts:\n  - id: p\n    title: P\n    chapters: [a]\n",
                encoding="utf-8")
            (series_dir / "a.md").write_text(
                "---\ntitle: A\ntype: guide\ndomain: inference\n"
                "status: draft\none_liner: \"" + "x" * 61 + "\"\n---\n\n# A\n",
                encoding="utf-8")
            with patch.object(learn, "LEARN_DIR", root):
                with self.assertRaisesRegex(learn.LearnError, "one_liner"):
                    learn.load_all()

    def test_orphan_article_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            series_dir = root / "x"
            series_dir.mkdir()
            (series_dir / "series.yaml").write_text(
                "id: x\nparts:\n  - id: p\n    title: P\n    chapters: [a]\n",
                encoding="utf-8")
            for slug in ("a", "b"):
                (series_dir / f"{slug}.md").write_text(
                    f"---\ntitle: {slug}\ntype: guide\ndomain: inference\n"
                    "status: draft\none_liner: ok\n---\n\n# " + slug + "\n",
                    encoding="utf-8")
            with patch.object(learn, "LEARN_DIR", root):
                with self.assertRaisesRegex(learn.LearnError, "未在 series.yaml"):
                    learn.load_all()

    def test_prereq_must_be_earlier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            series_dir = root / "x"
            series_dir.mkdir()
            (series_dir / "series.yaml").write_text(
                "id: x\nparts:\n  - id: p\n    title: P\n    chapters: [a, b]\n",
                encoding="utf-8")
            (series_dir / "a.md").write_text(
                "---\ntitle: a\ntype: guide\ndomain: inference\nstatus: draft\n"
                "one_liner: ok\nprereq: [b]\n---\n\n# a\n", encoding="utf-8")
            (series_dir / "b.md").write_text(
                "---\ntitle: b\ntype: guide\ndomain: inference\nstatus: draft\n"
                "one_liner: ok\n---\n\n# b\n", encoding="utf-8")
            with patch.object(learn, "LEARN_DIR", root):
                with self.assertRaisesRegex(learn.LearnError, "prereq"):
                    learn.load_all()


if __name__ == "__main__":
    unittest.main()
