"""collector.svgchart 图表渲染测试：空态、单点、全 None、降采样、XSS、SVG 结构。

只测构建期纯函数与模板 macro，不触网、不写 dist/、不依赖数据库。
"""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from collector import svgchart as sc

ROOT = Path(__file__).resolve().parents[1]
SVG_NS = "{http://www.w3.org/2000/svg}"


def svg_of(html: str) -> str:
    """从图表输出（可能带 .mchart 包装）里取出 <svg>…</svg>。"""
    m = re.search(r"<svg\b.*?</svg>", html, re.S)
    if not m:
        raise AssertionError(f"输出中没有 <svg>：{html[:200]}")
    return m.group(0)


def root_of(html: str) -> ET.Element:
    return ET.fromstring(svg_of(html))


def class_count(svg: str, cls: str) -> int:
    return sum(1 for m in re.finditer(r'class="([^"]*)"', svg)
               if cls in m.group(1).split())


class TestEmptyStates(unittest.TestCase):
    def test_multi_no_labels(self):
        html = sc.multi_line_chart([], {})
        self.assertIn("chart-empty", html)
        self.assertNotIn("<svg", html)
        self.assertIn("不足 2 份", html)
        self.assertIn('role="status"', html)

    def test_multi_single_label(self):
        html = sc.multi_line_chart(["2026-09-23"], {"A": [1]})
        self.assertIn("chart-empty", html)
        self.assertIn("当前 1 份", html)
        self.assertNotIn("<svg", html)

    def test_multi_all_none(self):
        html = sc.multi_line_chart(
            ["2026-09-21", "2026-09-22", "2026-09-23"],
            {"A": [None, None, None], "B": [None, None, None]},
        )
        self.assertIn("chart-empty", html)
        self.assertIn("暂无可绘制的趋势数据", html)
        self.assertNotIn("<svg", html)

    def test_line_empty_and_all_none(self):
        for points in ([], [("a", None), ("b", None)]):
            html = sc.line_chart(points)
            self.assertIn("chart-empty", html, points)
            self.assertNotIn("<svg", html, points)

    def test_sparkline_empty(self):
        for points in ([], [None, None], [("a", None)]):
            html = sc.sparkline(points)
            self.assertIn("chart-empty", html, points)
            self.assertIn("inline", html, points)
            self.assertNotIn("<svg", html, points)

    def test_bar_empty(self):
        for items in ({}, [], {"A": None}, [("A", None)], [("A", "oops")]):
            html = sc.bar_chart(items)
            self.assertIn("chart-empty", html, items)
            self.assertNotIn("<svg", html, items)

    def test_empty_has_hint(self):
        html = sc.line_chart([])
        self.assertIn("ce-hint", html)
        self.assertIn("接入数据源", html)


class TestSinglePoint(unittest.TestCase):
    def test_line_single_point(self):
        html = sc.line_chart([("2026-09-23", 5)], unit="score")
        svg = svg_of(html)
        root = root_of(html)
        self.assertEqual(root.get("data-points"), "1")
        self.assertEqual(root.get("data-total-points"), "1")
        self.assertIn('role="img"', svg)
        self.assertIn("score", svg)
        self.assertIn("c-last-dot", svg)
        self.assertIn(">5<", svg)

    def test_sparkline_single_point(self):
        html = sc.sparkline([5])
        root = root_of(html)
        self.assertEqual(root.get("data-points"), "1")
        self.assertIn("chart-spark", root.get("class", ""))
        self.assertIn("c-last-dot", html)

    def test_line_single_zero_does_not_crash(self):
        html = sc.line_chart([("2026-09-23", 0)])
        self.assertIn("<svg", html)
        self.assertNotIn("nan", html.lower())
        root_of(html)


class TestStructureAndAccessibility(unittest.TestCase):
    def test_svg_is_well_formed_and_accessible(self):
        labels = [f"2026-09-{d:02d}" for d in range(1, 21)]
        cases = {
            "line": sc.line_chart(
                [(d, i * 1.5) for i, d in enumerate(labels)],
                unit="GB/s", title="吞吐", x_label="日期"),
            "multi": sc.multi_line_chart(
                labels, {"A": [i for i in range(20)], "B": [i / 2 for i in range(20)]},
                unit="score", title="对比"),
            "spark": sc.sparkline([i for i in range(20)], title="迷你趋势"),
            "bar": sc.bar_chart({"H100": 3.35, "A100": 1.55}, unit="GB/s",
                                title="带宽对比"),
        }
        for name, html in cases.items():
            root = root_of(html)
            self.assertEqual(root.tag, SVG_NS + "svg", name)
            self.assertEqual(root.get("role"), "img", name)
            self.assertTrue(root.get("viewBox"), name)
            title = root.find(SVG_NS + "title")
            desc = root.find(SVG_NS + "desc")
            self.assertIsNotNone(title, name)
            self.assertIsNotNone(desc, name)
            self.assertTrue((title.text or "").strip(), name)
            self.assertTrue((desc.text or "").strip(), name)
            labelled = root.get("aria-labelledby") or ""
            self.assertIn(title.get("id"), labelled, name)
            self.assertIn(desc.get("id"), labelled, name)

    def test_viewbox_dimensions(self):
        root = root_of(sc.line_chart([("a", 1), ("b", 2)], width=500, height=180))
        self.assertEqual(root.get("viewBox"), "0 0 500 180")
        root = root_of(sc.sparkline([1, 2], width=80, height=24))
        self.assertEqual(root.get("viewBox"), "0 0 80 24")

    def test_no_nan_or_inf(self):
        html = sc.line_chart(
            [("a", 1e-9), ("b", 1e9), ("c", None), ("d", -1e6)],
            smooth=True, unit="tokens/s")
        self.assertNotIn("nan", html.lower())
        self.assertNotIn("inf", html.lower())
        root_of(html)

    def test_css_variable_palette(self):
        html = sc.multi_line_chart(
            ["2026-09-21", "2026-09-22"],
            {"A": [1, 2], "B": [3, 4]})
        self.assertIn("var(--chart-1", html)
        self.assertIn("var(--chart-2", html)
        self.assertIn('class="lg"', html)
        # 图例颜色也走变量，便于明暗主题切换
        self.assertIn("background:var(--chart-1", html)


class TestAxesAndLayout(unittest.TestCase):
    def test_y_ticks_and_unit(self):
        labels = [f"2026-09-{d:02d}" for d in range(1, 31)]
        html = sc.line_chart([(d, i * 10) for i, d in enumerate(labels)],
                             unit="stars")
        svg = svg_of(html)
        self.assertGreaterEqual(class_count(svg, "c-y"), 3)
        self.assertIn("stars", svg)
        self.assertIn('class="ax c-unit"', svg)

    def test_x_labels_are_thinned(self):
        labels = [f"2026-09-{d:02d}" for d in range(1, 31)]
        html = sc.line_chart([(d, i) for i, d in enumerate(labels)])
        svg = svg_of(html)
        n_x = class_count(svg, "c-x")
        self.assertGreaterEqual(n_x, 2)
        self.assertLess(n_x, len(labels))
        self.assertLessEqual(n_x, 8)

    def test_date_labels_shortened(self):
        html = sc.line_chart([("2026-09-21", 1), ("2026-09-22", 2)])
        self.assertIn(">09-21<", html)
        self.assertIn(">09-22<", html)

    def test_smooth_vs_polyline(self):
        pts = [("2026-09-01", 1), ("2026-09-02", 3), ("2026-09-03", 2)]
        smooth = svg_of(sc.line_chart(pts, smooth=True))
        raw = svg_of(sc.line_chart(pts, smooth=False))
        self.assertIn('<path class="c-line"', smooth)
        self.assertIn(" C", smooth)
        self.assertIn('<polyline class="c-line"', raw)

    def test_gaps_are_not_connected(self):
        html = sc.line_chart(
            [("a", 1), ("b", 2), ("c", None), ("d", 4), ("e", 5)])
        self.assertEqual(class_count(svg_of(html), "c-line"), 2)

    def test_hover_titles(self):
        html = sc.line_chart([("2026-09-21", 1), ("2026-09-22", 2.5)],
                             unit="GB/s")
        svg = svg_of(html)
        self.assertIn('class="c-hit"', svg)
        self.assertGreaterEqual(svg.count("<title>"), 2)
        self.assertIn("2026-09-22 · 2.5 GB/s", svg)

    def test_endpoint_annotation_and_legend(self):
        html = sc.line_chart([("a", 1), ("b", 7)], unit="score")
        self.assertIn('class="last c-last"', html)
        self.assertIn(">7<", html)
        multi = sc.multi_line_chart(
            ["2026-09-21", "2026-09-22"],
            {"产品 A": [1, 2], "产品 B": [3, 4]})
        self.assertIn('class="legend"', multi)
        self.assertEqual(multi.count('class="lg"'), 2)

    def test_bar_chart_sorted_and_truncated(self):
        html = sc.bar_chart({"A": 1, "B": 9, "C": 5}, unit="GB/s",
                            max_bars=2, sort=True)
        root = root_of(html)
        self.assertEqual(root.get("data-bars"), "2")
        self.assertEqual(root.get("data-total-bars"), "3")
        svg = svg_of(html)
        self.assertEqual(class_count(svg, "c-bar"), 2)
        self.assertIn("GB/s", svg)
        # 最大值 B 应排在 A/C 前面
        self.assertLess(svg.index(">B<"), svg.index(">C<"))

    def test_bar_chart_keeps_order_when_sort_disabled(self):
        html = sc.bar_chart([("A", 1), ("B", 9)], sort=False)
        self.assertLess(svg_of(html).index(">A<"), svg_of(html).index(">B<"))

    def test_sparkline_accepts_pairs_and_numbers(self):
        self.assertIn("<svg", sc.sparkline([1, 2, 3]))
        self.assertIn("<svg", sc.sparkline([("2026-09-21", 1), ("2026-09-22", 2)]))
        self.assertIn("chart-spark", sc.sparkline([1, 2, 3]))


class TestDownsampling(unittest.TestCase):
    def _series(self, n=400):
        return [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", i * 1.5)
                for i in range(n)]

    def test_line_downsampling_bounds_and_size(self):
        pts = self._series(400)
        sampled = sc.line_chart(pts, hover=False)
        full = sc.line_chart(pts, max_points=10000, hover=False)
        root = root_of(sampled)
        self.assertEqual(root.get("data-total-points"), "400")
        self.assertLessEqual(int(root.get("data-points")), sc.DEFAULT_MAX_POINTS)
        self.assertEqual(root.get("data-downsampled"), "true")
        self.assertLess(len(sampled), len(full))
        self.assertLess(len(sampled), 20_000)

    def test_multi_downsampling_bounds(self):
        n = 365
        labels = [f"2026-{1 + i // 31:02d}-{1 + i % 31:02d}" for i in range(n)]
        series = {f"S{k}": [i + k for i in range(n)] for k in range(5)}
        sampled = sc.multi_line_chart(labels, series, hover=False)
        full = sc.multi_line_chart(labels, series, max_points=10000, hover=False)
        root = root_of(sampled)
        self.assertEqual(root.get("data-total-points"), str(n))
        self.assertLessEqual(int(root.get("data-points")), sc.DEFAULT_MAX_POINTS)
        self.assertEqual(root.get("data-downsampled"), "true")
        self.assertLess(len(sampled), len(full))

    def test_sparkline_downsampling(self):
        html = sc.sparkline([i for i in range(400)])
        root = root_of(html)
        self.assertLessEqual(int(root.get("data-points")), sc.DEFAULT_MAX_POINTS)
        self.assertEqual(root.get("data-downsampled"), "true")

    def test_small_series_not_downsampled(self):
        html = sc.line_chart([("a", 1), ("b", 2), ("c", 3)])
        root = root_of(html)
        self.assertEqual(root.get("data-points"), "3")
        self.assertEqual(root.get("data-downsampled"), "false")


class TestXSSSafety(unittest.TestCase):
    def test_line_labels_and_unit_escaped(self):
        html = sc.line_chart(
            [("<script>alert(1)</script>", 1),
             ('"><img src=x onerror=alert(1)>', 2)],
            unit='"><svg onload=alert(1)>',
            title="<script>title</script>",
            stroke='red" onload="alert(1)')
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<svg onload", html)
        self.assertNotIn('onload="alert', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x", html)
        # 非法颜色必须回退到安全值，不能带出引号
        self.assertNotIn('stroke:red"', html)
        root_of(html)

    def test_multi_names_escaped(self):
        html = sc.multi_line_chart(
            ["2026-09-21", "2026-09-22"],
            {'"><img src=x onerror=alert(1)>': [1, 2]})
        self.assertNotIn("<img", html)
        self.assertNotIn('onerror="', html)
        self.assertIn("&lt;img src=x", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        root_of(html)

    def test_bar_labels_escaped(self):
        html = sc.bar_chart({"<b onmouseover=alert(1)>X</b>": 3})
        self.assertNotIn("<b ", html)
        self.assertIn("&lt;b onmouseover=alert(1)&gt;", html)
        root_of(html)

    def test_sparkline_pairs_escaped(self):
        html = sc.sparkline([("<script>x</script>", 1), ("b", 2)])
        self.assertNotIn("<script", html)
        root_of(html)

    def test_duplicate_charts_get_unique_ids(self):
        p = [("a", 1), ("b", 2)]
        id1 = re.search(r'<title id="([^"]+)"', sc.line_chart(p)).group(1)
        id2 = re.search(r'<title id="([^"]+)"', sc.line_chart(p)).group(1)
        # 同内容重复图表会得到唯一 id（序号递增），不会在页面内撞 id
        self.assertNotEqual(id1, id2)


class TestBackwardCompatibility(unittest.TestCase):
    def test_old_positional_calls(self):
        labels = ["2026-09-21", "2026-09-22", "2026-09-23"]
        html = sc.multi_line_chart(
            labels, {"A": [1, 2, 3], "B": [3, 2, 1]}, 640, 220)
        self.assertIn('class="mchart"', html)
        self.assertGreaterEqual(class_count(svg_of(html), "chart"), 1)
        self.assertIn('class="legend"', html)
        self.assertIn('class="gl', html)
        self.assertIn('class="ax', html)

        line = sc.line_chart(
            [("2026-09-21", 1), ("2026-09-22", 2)],
            640, 200, "var(--acc, #58a6ff)")
        self.assertIn("stroke:var(--acc, #58a6ff)", line)
        self.assertIn('class="chart"', line)

    def test_old_empty_output_keeps_class(self):
        self.assertIn("chart-empty", sc.multi_line_chart([], {}))
        self.assertIn("chart-empty", sc.line_chart([]))

    def test_functions_exported(self):
        for name in ("multi_line_chart", "line_chart", "sparkline", "bar_chart"):
            self.assertTrue(callable(getattr(sc, name)), name)
            self.assertIn(name, sc.__all__)


class TestChartTemplateMacro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(
            loader=FileSystemLoader(str(ROOT / "templates")),
            autoescape=True,
        )
        cls.mod = cls.env.get_template("_chart.html").make_module()

    def test_chart_card_renders_all_parts(self):
        chart = sc.line_chart([("a", 1), ("b", 2)], unit="GB/s")
        html = str(self.mod.chart_card(
            chart, "吞吐趋势",
            subtitle="近 90 天", unit="GB/s",
            note="构建期渲染", source_name="Artificial Analysis",
            source_url="/sources#aa", chart_id="chart-1"))
        for needle in ("chart-card", "chart-card-title", "吞吐趋势",
                       "近 90 天", "chart-card-unit", "GB/s",
                       "chart-card-body", "chart-card-foot",
                       "Artificial Analysis", 'href="/sources#aa"',
                       "构建期渲染", 'id="chart-1"'):
            self.assertIn(needle, html, needle)
        self.assertIn("<svg", html)

    def test_chart_card_escapes_text(self):
        html = str(self.mod.chart_card(
            "<svg></svg>", "<script>title</script>",
            subtitle='"><img src=x>',
            source_url='"><script>alert(1)</script>'))
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_spark_cell(self):
        spark = sc.sparkline([1, 2, 3])
        html = str(self.mod.spark_cell(spark, "↑12%", "/product/x"))
        self.assertIn("spark-cell", html)
        self.assertIn("spark-label", html)
        self.assertIn('href="/product/x"', html)
        self.assertIn("<svg", html)

    def test_chart_foot(self):
        html = str(self.mod.chart_foot(note="n", source_name="s",
                                       source_url="/x"))
        self.assertIn("chart-card-foot", html)
        self.assertIn("数据来源：s", html)
        self.assertIn('href="/x"', html)


if __name__ == "__main__":
    unittest.main()
