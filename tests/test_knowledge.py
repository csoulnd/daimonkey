import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import knowledge as kb


class KnowledgeTests(unittest.TestCase):
    def test_display_scope_excludes_templates_and_runtime_configuration(self):
        for path in ("templates/study-note.md", "templates/atomic-knowledge.md", ".agents/skills/x/SKILL.md", ".obsidian/app.json"):
            self.assertFalse(kb.displayable(path), path)
        for path in ("README.md", "agent/README.md", "infra/design.md", "knowledge/foundations/tools/ssh.md"):
            self.assertTrue(kb.displayable(path), path)

    def test_frontmatter_and_body(self):
        meta, body = kb.split_frontmatter('---\ntitle: 知识\nstatus: active\n---\n# 正文\n')
        self.assertEqual(meta["title"], "知识")
        self.assertEqual(body, "# 正文\n")
        self.assertEqual(kb.split_frontmatter("# 普通文档"), ({}, "# 普通文档"))

    def test_internal_links_images_fragments_and_submodule(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            image = repo / "agent/assets/image.png"
            image.parent.mkdir(parents=True)
            image.touch()
            with patch.object(kb, "REPO_DIR", repo):
                known = {"agent/README.md", "knowledge/README.md"}
                self.assertEqual(kb.local_link("../../knowledge/README.md#基础", "agent/reports/a.md", known, "abc"),
                                 "/knowledge/notes/knowledge/README.html#基础")
                self.assertEqual(kb.local_link("../assets/image.png", "agent/reports/a.md", known, "abc"),
                                 "/knowledge/files/agent/assets/image.png")
                self.assertEqual(kb.local_link("#正文", "agent/README.md", known, "abc"), "#正文")
                self.assertTrue(kb.local_link("../knowledge/infra/infratech", "agent/README.md", known, "abc").startswith(kb.REPO_URL))
                self.assertEqual(kb.local_link("https://example.com", "a.md", known, "abc"), "https://example.com")

    def test_render_tables_code_unicode_headings_and_sanitize(self):
        body = '# 中文标题\n\n[阅读](../knowledge/README.md)\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```\n\n<img src="x" onerror="alert(1)"><script>alert(1)</script>\n\n[危险](javascript:alert(1))'
        rendered, toc = kb.render_markdown(body, "agent/README.md", {"knowledge/README.md"}, "abc")
        self.assertIn('id="中文标题"', rendered)
        self.assertIn('<table>', rendered)
        self.assertIn('language-python', rendered)
        self.assertIn('/knowledge/notes/knowledge/README.html', rendered)
        self.assertIn('#中文标题', toc)
        self.assertNotIn('<script', rendered)
        self.assertNotIn('onerror', rendered)
        self.assertNotIn('javascript:', rendered)

    def test_directory_tree(self):
        note = {"path": "agent/reports/a.md", "title": "A"}
        tree = kb.directory_tree([note])
        self.assertEqual(tree["dirs"]["agent"]["dirs"]["reports"]["notes"], [note])


if __name__ == "__main__":
    unittest.main()
