"""调研展示（showcase）：章节抽取、链接重写、判断标记、步骤/组件表解析、端到端导入。

全部使用临时目录 fixture（patch KB_REPO），不依赖真实知识库快照。
"""
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DETAIL_FIXTURE = textwrap.dedent("""\
    ---
    title: 测试详细调研
    ---
    # 测试详细调研

    [先读摘要](summary.md) · [产品索引](../README.md)

    ## 产品定位与边界

    官方定义为平台。[B1](https://example.com/doc1) **研究判断：**这是判断句。

    **研究判断：**整段判断。

    ## 软件架构

    ### “七层”实际上对应两种不同表述

    两种表述说明段。

    ### 执行关系

    ```mermaid
    flowchart TD
      A --> B
    ```

    图后解释段。

    ## 一次请求如何执行

    1. 第一步动作，含 [B2](https://example.com/doc2) 引用。
    2. 第二步动作
       带续行。
    3. 第三步动作。

    ## 参考来源

    | 编号 | 来源 |
    |---|---|
    | B1 | [Doc](https://example.com/doc1) |
""")

SUMMARY_FIXTURE = textwrap.dedent("""\
    ---
    title: 测试摘要
    ---
    # 测试摘要

    ## 软件架构：按需组合

    | 关键部分 | 职责与关系 |
    |---|---|
    | Runtime | 承载执行环境与会话生命周期 |
    | Harness / 用户应用 | 托管循环或自带框架 |
""")


class ShowcaseFixture(unittest.TestCase):
    def setUp(self):
        import importlib
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.kb = base / "kb"
        (self.kb / "agent").mkdir(parents=True)
        (self.kb / "agent" / "detail.md").write_text(DETAIL_FIXTURE, encoding="utf-8")
        (self.kb / "agent" / "summary.md").write_text(SUMMARY_FIXTURE, encoding="utf-8")
        (self.kb / "README.md").write_text("# 索引\n", encoding="utf-8")
        self.report_dir = base / "report"
        self.report_dir.mkdir()
        import collector.showcase as showcase
        self.mod = importlib.reload(showcase)
        self._patches = [
            patch.object(self.mod, "KB_REPO", self.kb),
            patch.object(self.mod, "_commit", lambda: "abc123def"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    # ---- 章节抽取 ----

    def test_extract_section_with_quotes_and_nesting(self):
        seg, warn = self.mod.extract_section(DETAIL_FIXTURE, "“七层”实际上对应两种不同表述")
        self.assertIsNone(warn)
        self.assertIn("两种表述说明段", seg)
        self.assertNotIn("图后解释段", seg)      # 在下一同级标题处截断
        # 二级标题抽取到全部子节
        seg2, _ = self.mod.extract_section(DETAIL_FIXTURE, "软件架构")
        self.assertIn("执行关系", seg2)

    def test_extract_section_missing_returns_warning(self):
        seg, warn = self.mod.extract_section(DETAIL_FIXTURE, "不存在的章节")
        self.assertIsNone(seg)
        self.assertIn("未找到章节", warn)

    # ---- 渲染：判断标记 / 链接重写 / mermaid 剥离 ----

    def test_render_marks_judgments_both_positions(self):
        html = self.mod.render_md(
            "事实句。[B1](https://x.com) **研究判断：**句尾判断。\n\n"
            "**研究判断：**整段判断。", "agent/detail.md", set(), "abc")
        self.assertIn('<span class="mark-judgment-inline">', html)   # 段中
        self.assertIn('<p class="mark-judgment">', html)             # 段首整段
        self.assertIn("事实句", html)                                  # 事实句未被罩入样式

    def test_render_rewrites_links(self):
        known = {"agent/detail.md", "agent/summary.md", "README.md"}
        html = self.mod.render_md(
            "[摘要](summary.md) [索引](../README.md) [外部](https://example.com) "
            "[库外](../../.agents/skills/x/SKILL.md)",
            "agent/detail.md", known, "abc123")
        self.assertIn('/knowledge/notes/agent/summary.html', html)
        self.assertIn('/knowledge/notes/README.html', html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn('github.com/csoulnd/InferTechNote/blob/abc123/', html)  # 库外 → 固定链接

    def test_mermaid_stripped_in_import(self):
        report_conf = self.report_dir / "showcase.yaml"
        report_conf.write_text(textwrap.dedent("""\
            showcase:
              title: t
              products:
                - {key: k, name: N, status: ready,
                   detail_doc: agent/detail.md, summary_doc: agent/summary.md}
              sections:
                - {id: rel, title: 执行关系, heading: 执行关系, mode: diagram}
              diagram:
                components: []
        """), encoding="utf-8")
        (self.report_dir / "diagram.svg").write_text("<svg></svg>", encoding="utf-8")
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertEqual(warnings, [])
        # 抽取的来源 markdown 中 mermaid 已剥离（html 不入页面，由交互图承载）
        self.assertNotIn("flowchart", str(ctx["sections"]))

    # ---- 步骤与组件表 ----

    def test_extract_steps_with_continuation(self):
        seg, _ = self.mod.extract_section(DETAIL_FIXTURE, "一次请求如何执行")
        steps = self.mod.extract_steps(seg)
        self.assertEqual(len(steps), 3)
        self.assertIn("续行", steps[1])

    def test_parse_component_table(self):
        rows = self.mod.parse_component_table(SUMMARY_FIXTURE)
        self.assertEqual(rows["Runtime"], "承载执行环境与会话生命周期")
        self.assertIn("Harness / 用户应用", rows)

    # ---- 端到端导入 ----

    def test_load_showcase_end_to_end(self):
        (self.report_dir / "diagram.svg").write_text("<svg data-x='1'></svg>", encoding="utf-8")
        (self.report_dir / "showcase.yaml").write_text(textwrap.dedent("""\
            showcase:
              title: 测试展示
              stage_note: 测试
              products:
                - {key: k, name: 测试产品, status: ready, baseline: 基线 2026-10-10,
                   detail_doc: agent/detail.md, summary_doc: agent/summary.md}
              sections:
                - {id: pos, title: 定位, heading: 产品定位与边界, group: 定位}
                - {id: flow, title: 执行, heading: 一次请求如何执行, mode: steps, group: 执行路径}
                - {id: gone, title: 缺失, heading: 不存在章节}
              diagram:
                file: diagram.svg
                components:
                  - {id: runtime, label: Runtime, desc_key: Runtime}
                  - {id: caller, label: 调用方, desc_note: 视图定位}
                step_highlights: {"1": [runtime]}
        """), encoding="utf-8")
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertEqual(ctx["showcase_error"], None)
        self.assertEqual(len(warnings), 1)                      # 缺失章节警告
        self.assertIn("未找到章节", warnings[0])
        by_id = {s["id"]: s for s in ctx["sections"]}
        self.assertIn("mark-judgment", by_id["pos"]["html"])
        self.assertEqual(by_id["flow"]["step_count"], 3)
        self.assertTrue(by_id["gone"]["missing"])
        comps = {c["id"]: c for c in ctx["diagram"]["components"]}
        self.assertEqual(comps["runtime"]["desc_source"], "doc-table")
        self.assertIn("承载执行环境", comps["runtime"]["desc_html"])
        self.assertEqual(comps["caller"]["desc_source"], "view-note")
        self.assertEqual(ctx["fingerprint"], ctx["fingerprint"][:10])
        self.assertIn("blob/abc123def/", ctx["detail_github_url"])

    def test_load_showcase_missing_doc(self):
        (self.report_dir / "showcase.yaml").write_text(textwrap.dedent("""\
            showcase:
              products:
                - {key: k, name: N, status: ready,
                   detail_doc: agent/not-there.md, summary_doc: agent/summary.md}
              sections: []
        """), encoding="utf-8")
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertIn("源文档不存在", ctx["showcase_error"])
        self.assertTrue(warnings)


class ReportsIntegrationTests(unittest.TestCase):
    """reports 框架识别 showcase.yaml 目录为展示型报告。"""

    def test_registry_detects_showcase_kind(self):
        import importlib
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            reports_dir = base / "reports"
            (reports_dir / "demo").mkdir(parents=True)
            (reports_dir / "demo" / "showcase.yaml").write_text(
                "showcase:\n  products: []\n", encoding="utf-8")
            (reports_dir / "registry.yaml").write_text(
                "reports:\n  - {slug: demo, title: 展示, date: 2026-10-12}\n",
                encoding="utf-8")
            import collector.reports as reports
            mod = importlib.reload(reports)
            with patch.object(mod, "REPORTS_DIR", reports_dir), \
                    patch.object(mod, "REGISTRY_FILE", reports_dir / "registry.yaml"):
                loaded, warnings = mod.load_registry()
            self.assertEqual(warnings, [])
            self.assertEqual(loaded[0]["kind"], "showcase")
            self.assertEqual(loaded[0]["kind_name"], "展示")


if __name__ == "__main__":
    unittest.main()
