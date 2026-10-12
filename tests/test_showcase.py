"""调研展示（showcase）汇报结构：章节/表格收窄/flow 合并/要点卡/折叠来源/讲稿。

全部临时目录 fixture（patch KB_REPO），不依赖真实知识库快照。
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

    | 关键部分 | 职责与关系 |
    |---|---|
    | Runtime | 承载执行环境与会话生命周期 |
    | Harness / 用户应用 | 托管循环或自带框架 |
""")

COMPARISON_FIXTURE = textwrap.dedent("""\
    ---
    title: 对照
    ---
    # 对照

    ## 产品定位怎样影响软件架构

    | 产品 | 核心问题 | 架构重点 |
    |---|---|---|
    | 阿里云 AgentCore | 统一接入与管理 | Workspace 资产 |
    | Amazon AgentCore | 接入运行与治理 | 服务原语组合 |
    | Dify | 做成业务流程 | 应用定义与流程 |
    | Pi Agent | 改造嵌入循环 | 组件拆分 |

    表后说明段。

    ## 从产品中提炼的软件职责

    | 职责 | 需要回答的问题 | 可借鉴的产品方向 |
    |---|---|---|
    | 产品接入 | 谁能调用 | 云平台 Endpoint |
    | 运行基础设施 | 谁负责隔离 | Runtime、沙箱 |
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
        (self.kb / "agent" / "comparison.md").write_text(COMPARISON_FIXTURE, encoding="utf-8")
        self.report_dir = base / "report"
        self.report_dir.mkdir()
        (self.report_dir / "diagram.svg").write_text("<svg></svg>", encoding="utf-8")
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

    def write_conf(self, text: str):
        (self.report_dir / "showcase.yaml").write_text(
            textwrap.dedent(text), encoding="utf-8")

    # ---- 基础抽取与渲染 ----

    def test_extract_and_mark(self):
        seg, warn = self.mod.extract_section(DETAIL_FIXTURE, "产品定位与边界")
        self.assertIsNone(warn)
        html = self.mod.render_md(seg, "agent/detail.md", set(), "abc")
        self.assertIn("mark-judgment-inline", html)   # 段中判断
        self.assertIn('class="mark-judgment"', html)  # 段首整段判断

    def test_table_filter_keeps_only_target_rows(self):
        seg, _ = self.mod.extract_section(COMPARISON_FIXTURE, "产品定位怎样影响软件架构")
        filtered = self.mod.filter_table_rows(seg, ["阿里云 AgentCore", "Amazon AgentCore"])
        self.assertIn("阿里云 AgentCore", filtered)
        self.assertIn("Amazon AgentCore", filtered)
        self.assertNotIn("Dify", filtered)
        self.assertNotIn("Pi Agent", filtered)
        self.assertIn("表后说明段", filtered)          # 非表格内容不受影响

    def test_render_rewrites_links(self):
        known = {"agent/detail.md", "agent/summary.md"}
        html = self.mod.render_md(
            "[摘要](summary.md) [外部](https://example.com) "
            "[库外](../../.agents/skills/x/SKILL.md)",
            "agent/detail.md", known, "abc123")
        self.assertIn('/knowledge/notes/agent/summary.html', html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn('github.com/csoulnd/InferTechNote/blob/abc123/', html)

    def test_extract_steps(self):
        seg, _ = self.mod.extract_section(DETAIL_FIXTURE, "一次请求如何执行")
        steps = self.mod.extract_steps(seg, set(), "agent/detail.md", "abc")
        self.assertEqual(len(steps), 3)
        self.assertIn("续行", steps[1])

    # ---- 汇报结构端到端 ----

    def test_load_chapters_flow_and_points(self):
        self.write_conf("""
            showcase:
              title: 测试汇报
              subtitle: 两种解法
              docs:
                detail: agent/detail.md
                comparison: agent/comparison.md
              overview:
                points:
                  - {text: "命题句。", kind: judgment, anchor: "detail#产品定位与边界"}
              diagrams:
                amz:
                  file: diagram.svg
                  desc_table_from: comparison
                  desc_table_header: 职责
                  components:
                    - {id: access, label: 产品接入, desc_key: 产品接入}
                    - {id: runtime, label: 运行基础设施, desc_key: 运行基础设施}
                  step_highlights: {"1": [runtime]}
              chapters:
                - id: ch-a
                  num: "一"
                  title: 产品 A
                  gist: 测试
                  lead: 导语
                  points:
                    - {text: "要点。", kind: fact, anchor: "detail#执行关系"}
                  sections:
                    - {id: a-pos, title: 定位, from: detail, heading: 产品定位与边界}
                    - {id: a-flow, title: 执行, mode: flow, from: detail,
                       heading: 一次请求如何执行, diagram: amz}
                    - {id: a-cut, title: 对比节, from: comparison,
                       heading: 产品定位怎样影响软件架构,
                       table_filter: ["阿里云 AgentCore", "Amazon AgentCore"]}
              sources_folds:
                - {label: 来源表, from: detail, heading: 参考来源}
        """)
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertEqual(warnings, [])
        self.assertEqual(ctx["sc_title"], "测试汇报")
        # 开篇要点带锚点（URL 编码后指向原文段落）
        from urllib.parse import unquote
        self.assertTrue(unquote(ctx["overview"]["points"][0]["href"]).endswith(
            "/knowledge/notes/agent/detail.html#产品定位与边界"))
        ch = ctx["chapters"][0]
        secs = {s["id"]: s for s in ch["sections"]}
        # flow：图 + 步骤同在一个章节项内
        self.assertEqual(secs["a-flow"]["mode"], "flow")
        self.assertEqual(len(secs["a-flow"]["steps"]), 3)
        self.assertEqual(secs["a-flow"]["diagram"]["components"][0]["desc_source"],
                         "doc-table")
        self.assertEqual(secs["a-flow"]["diagram"]["step_highlights"], {"1": ["runtime"]})
        # table_filter 生效
        self.assertNotIn("Dify", secs["a-cut"]["html"])
        # 每节带来源（知识库锚点 + GitHub 固定链接）
        self.assertIn("/knowledge/notes/agent/detail.html", secs["a-pos"]["src"]["kb_url"])
        self.assertIn("blob/abc123def", secs["a-pos"]["src"]["gh_url"])
        # 折叠来源（表格渲染为 HTML，链接保留）
        self.assertEqual(ctx["sources"]["folds"][0]["label"], "来源表")
        self.assertIn("example.com/doc1", ctx["sources"]["folds"][0]["html"])

    def test_missing_heading_warns_not_crashes(self):
        self.write_conf("""
            showcase:
              docs: {detail: agent/detail.md}
              chapters:
                - id: ch
                  title: T
                  sections:
                    - {id: x, title: 缺, from: detail, heading: 不存在的章节}
        """)
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertEqual(ctx["showcase_error"], None)
        self.assertTrue(any("未找到章节" in w for w in warnings))
        self.assertTrue(ctx["chapters"][0]["sections"][0]["missing"])

    def test_missing_doc_error(self):
        self.write_conf("""
            showcase:
              docs: {detail: agent/not-there.md}
              chapters: []
        """)
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertIn("showcase_error", ctx)
        self.assertTrue(warnings)

    def test_arch_overview_authored_steps_and_table_override(self):
        """合并图：yaml 书写的双栏步骤 + 步骤高亮 + 组件级表格与列表 desc_key。"""
        self.write_conf("""
            showcase:
              title: 总览测试
              docs:
                detail: agent/detail.md
                amazon: agent/summary.md
              arch_overview:
                id: arch-overview
                title: 架构总览与核心流程
                mode: flow
                from: detail
                diagram: unified
                steps_note: 步骤依据两家文档
                steps:
                  - alibaba: 第一步_阿里动作。
                    amazon: 第一步_Amazon动作。
                    highlights: [al-define, am-run]
                  - text: 第二步_纯文本。
                    highlights: [al-run]
              diagrams:
                unified:
                  file: diagram.svg
                  components:
                    - {id: al-define, label: 定义, table: amazon, desc_key: "Runtime"}
                    - {id: am-run, label: 运行, table: amazon, desc_key: "Harness / 用户应用"}
                    - {id: al-run, label: 双行, table: amazon, desc_key: ["Runtime", "Harness / 用户应用"]}
              chapters: []
        """)
        ctx, warnings = self.mod.load_showcase(self.report_dir)
        self.assertEqual(warnings, [])
        ao = ctx["arch_overview"]
        self.assertEqual(len(ao["steps"]), 2)
        # 双栏步骤：标签 + 各自动作；高亮按步索引覆盖
        self.assertIn("阿里云", ao["steps"][0])
        self.assertIn("第一步_阿里动作", ao["steps"][0])
        self.assertIn("Amazon", ao["steps"][0])
        self.assertIn("第一步_Amazon动作", ao["steps"][0])
        self.assertIn("第二步_纯文本", ao["steps"][1])
        self.assertEqual(ao["step_highlights"]["1"], ["al-define", "am-run"])
        self.assertEqual(ao["step_highlights"]["2"], ["al-run"])
        # 组件级 table 覆盖 + 列表 desc_key 合并
        comps = {c["id"]: c for c in ao["diagram"]["components"]}
        self.assertIn("承载执行环境与会话生命周期", comps["al-define"]["desc_html"])
        self.assertIn("托管循环或自带框架", comps["am-run"]["desc_html"])
        both = comps["al-run"]["desc_html"]
        self.assertIn("承载执行环境与会话生命周期", both)
        self.assertIn("托管循环或自带框架", both)
        self.assertIn("<br>", both)

    def test_render_notes(self):
        notes = self.report_dir / "notes.md"
        notes.write_text("# 讲稿\n\n开场白。\n\n<script>alert(1)</script>\n", encoding="utf-8")
        html = self.mod.render_notes(notes, set(), "abc")
        self.assertIn("开场白", html)
        self.assertNotIn("<script>", html)


class ReportsIntegrationTests(unittest.TestCase):
    """reports 框架识别 showcase.yaml 目录为展示型报告。"""

    def test_registry_detects_showcase_kind(self):
        import importlib
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            reports_dir = base / "reports"
            (reports_dir / "demo").mkdir(parents=True)
            (reports_dir / "demo" / "showcase.yaml").write_text(
                "showcase:\n  chapters: []\n", encoding="utf-8")
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
