"""报告厅框架：清单校验、三形态渲染分发、资源拷贝、清理。全临时目录，不触碰真实内容。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_module():
    import importlib
    import collector.reports as reports
    return importlib.reload(reports)


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.reports_dir = base / "reports"
        self.dist_dir = base / "dist"
        self.reports_dir.mkdir()
        self.dist_dir.mkdir()
        self.mod = load_module()
        self._patches = [
            patch.object(self.mod, "REPORTS_DIR", self.reports_dir),
            patch.object(self.mod, "REGISTRY_FILE", self.reports_dir / "registry.yaml"),
            patch.object(self.mod, "DIST_DIR", self.dist_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def write_registry(self, text: str):
        (self.reports_dir / "registry.yaml").write_text(text, encoding="utf-8")

    def make_report(self, slug: str, filename: str, content: str = "# 标题\n正文"):
        d = self.reports_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")
        return d

    # ---- 清单加载与容错 ----

    def test_load_valid_and_sort(self):
        self.make_report("a-report", "report.md")
        self.make_report("b-report", "report.md")
        self.make_report("c-report", "deck.pptx", "PK-fake")
        self.write_registry(
            "reports:\n"
            "  - {slug: a-report, title: 甲, date: 2026-10-01}\n"
            "  - {slug: b-report, title: 乙, date: 2026-10-12}\n"
            "  - {slug: c-report, title: 丙, date: 2026-10-05}\n")
        reports, warnings = self.mod.load_registry()
        self.assertEqual(warnings, [])
        self.assertEqual([r["slug"] for r in reports],
                         ["b-report", "c-report", "a-report"])  # 日期倒序
        self.assertEqual(reports[1]["kind"], "ppt")
        self.assertEqual(reports[0]["kind"], "md")

    def test_skip_bad_entries_with_warnings(self):
        self.make_report("good", "report.md")
        self.make_report("bad-ext", "notes.txt", "txt 内容")
        self.make_report("no-file", "other.bin", "x")   # 目录存在但无默认内容文件
        self.write_registry(
            "reports:\n"
            "  - {slug: good, title: 好, date: 2026-10-01}\n"
            "  - {title: 缺 slug, date: 2026-10-01}\n"
            "  - {slug: bad-ext, title: 坏, date: 2026-10-01, file: notes.txt}\n"
            "  - {slug: no-file, title: 无文件, date: 2026-10-01}\n"
            "  - {slug: ghost, title: 无目录, date: 2026-10-01}\n"
            "  - {slug: good, title: 重复, date: 2026-10-02}\n")
        reports, warnings = self.mod.load_registry()
        self.assertEqual([r["slug"] for r in reports], ["good"])
        joined = " | ".join(warnings)
        self.assertIn("缺字段", joined)
        self.assertIn("不支持的内容格式", joined)
        self.assertIn("未找到内容文件", joined)
        self.assertIn("other.bin", joined)             # 提示目录现有文件
        self.assertIn("不存在，跳过", joined)          # 目录缺失
        self.assertIn("slug 重复", joined)

    def test_default_file_probe_order(self):
        d = self.make_report("probe", "index.html", "<html>报告</html>")
        content = self.mod.resolve_content("probe", None)
        self.assertEqual(content.name, "index.html")
        self.assertEqual(self.mod.KIND_BY_SUFFIX[content.suffix], "html")
        # 显式指定不存在的 file -> None
        self.assertIsNone(self.mod.resolve_content("probe", "missing.md"))

    # ---- 渲染分发 ----

    def test_prepare_detail_three_kinds(self):
        self.make_report("m", "report.md", "# 标题\n\n![](images/a.png)\n\n正文")
        self.make_report("h", "index.html", "<html><body>原样</body></html>")
        self.make_report("p", "deck.pptx", "PK" + "0" * 2048)
        self.write_registry(
            "reports:\n"
            "  - {slug: m, title: M, date: 2026-10-01}\n"
            "  - {slug: h, title: H, date: 2026-10-01}\n"
            "  - {slug: p, title: P, date: 2026-10-01}\n")
        reports, warnings = self.mod.load_registry()
        self.assertEqual(warnings, [])
        by_slug = {}
        for r in reports:
            ctx, body, toc = self.mod._prepare_detail(r)
            by_slug[r["slug"]] = (ctx, body)
        # md：图片相对路径被重写
        self.assertIn('/reports/m/images/a.png', by_slug["m"][1])
        # html：iframe 地址
        self.assertEqual(by_slug["h"][0]["frame_url"], "/reports/h/index.html")
        # ppt：原件大小
        self.assertEqual(by_slug["p"][0]["file_name"], "deck.pptx")
        self.assertGreaterEqual(by_slug["p"][0]["file_kb"], 2)

    def test_markdown_sanitized(self):
        html, _ = self.mod.render_report_markdown(
            "# 标题\n<script>alert(1)</script>\n**加粗**", "x")
        self.assertNotIn("<script>", html)
        self.assertIn("<strong>", html)

    # ---- build 接线：渲染、资源拷贝、清理 ----

    def test_build_renders_copies_and_cleans(self):
        d = self.make_report("r1", "report.md", "# 报告一")
        (d / "images").mkdir()
        (d / "images" / "a.png").write_bytes(b"\x89PNG")
        self.write_registry("reports:\n  - {slug: r1, title: 一, date: 2026-10-01}\n")
        # 预置一个已下线的旧报告页与资源目录，应被清理
        old = self.dist_dir / "reports"
        old.mkdir()
        (old / "gone.html").write_text("old")
        (old / "gone").mkdir()
        (old / "gone" / "x.bin").write_bytes(b"x")
        (old / "index.html").write_text("旧索引")

        env = Mock()
        env.get_template.return_value.render.return_value = "<html>page</html>"
        count = self.mod.build(env, {"site": {"name": "t"}})
        self.assertEqual(count, 1)
        self.assertTrue((old / "r1.html").is_file())
        self.assertTrue((old / "index.html").is_file())
        self.assertEqual((old / "index.html").read_text(), "<html>page</html>")
        self.assertTrue((old / "r1" / "images" / "a.png").is_file())  # 资源拷贝
        self.assertFalse((old / "gone.html").exists())                # 清理页面
        self.assertFalse((old / "gone").exists())                     # 清理资源目录
        # render 上下文带 reports_page 与总数
        ctx = env.get_template.return_value.render.call_args.kwargs
        self.assertTrue(ctx["reports_page"])
        self.assertEqual(ctx["reports_total"], 1)

    def test_build_empty_registry(self):
        env = Mock()
        env.get_template.return_value.render.return_value = "<html>empty</html>"
        count = self.mod.build(env, {})
        self.assertEqual(count, 0)
        self.assertTrue((self.dist_dir / "reports" / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
