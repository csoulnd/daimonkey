"""评分算法测试：分位数（含并列/方向/缺失）、缺失值填充、覆盖率门控、整榜缺失降级。"""
import unittest

from collector import queries
from collector.scoring import compute_board, percentile_ranks, weighted_coverage


class TestPercentile(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(percentile_ranks([1, 2, 3]), [0.0, 0.5, 1.0])

    def test_ties_average_rank(self):
        self.assertEqual(percentile_ranks([1, 2, 2, 3]), [0.0, 0.5, 0.5, 1.0])

    def test_lower_better_inverts(self):
        self.assertEqual(percentile_ranks([1, 2, 3], higher_better=False),
                         [1.0, 0.5, 0.0])

    def test_single_value_is_middle(self):
        self.assertEqual(percentile_ranks([7]), [0.5])

    def test_none_passthrough(self):
        r = percentile_ranks([None, 1, 3])
        self.assertIsNone(r[0])
        self.assertEqual(r[1], 0.0)
        self.assertEqual(r[2], 1.0)


class TestComputeBoard(unittest.TestCase):
    cfg = {"heat": {"a": 0.6, "b": 0.4}, "momentum": {"c": 1.0}}
    products = [
        {"id": 1, "slug": "p1", "name": "P1", "boards": ["t"]},
        {"id": 2, "slug": "p2", "name": "P2", "boards": ["t"]},
    ]

    def test_impute_median_and_scores(self):
        # p2 缺 b -> 用组内中位数 5 填充；p2 的 a 更大 -> 排第一
        latest = {
            1: {"a": {"value": 10.0}, "b": {"value": 5.0}},
            2: {"a": {"value": 20.0}},
        }
        res = compute_board("t", self.cfg, self.products, latest, {},
                            "2026-09-23", 0.3)
        self.assertEqual([it["product"]["slug"] for it in res["ranked"]],
                         ["p2", "p1"])
        # p1: a 分位 0.0, b 填充后并列 0.5 -> 100*(0.6*0 + 0.4*0.5) = 20
        # p2: a 分位 1.0, b 0.5            -> 100*(0.6*1 + 0.4*0.5) = 80
        by_slug = {it["product"]["slug"]: it for it in res["items"]}
        self.assertAlmostEqual(by_slug["p1"]["heat_score"], 20.0)
        self.assertAlmostEqual(by_slug["p2"]["heat_score"], 80.0)
        self.assertEqual(by_slug["p1"]["coverage"], 1.0)
        self.assertEqual(by_slug["p2"]["coverage"], 0.6)
        # 动量指标 c 全榜缺失 -> 动量分为 None，指标进入 dropped
        self.assertIsNone(by_slug["p1"]["momentum_score"])
        self.assertIn("c", res["dropped"])

    def test_coverage_threshold_gates_observation(self):
        latest = {
            1: {"a": {"value": 10.0}, "b": {"value": 5.0}},
            2: {"a": {"value": 20.0}},   # 覆盖率 0.6
        }
        res = compute_board("t", self.cfg, self.products, latest, {},
                            "2026-09-23", 0.7)
        self.assertEqual(len(res["ranked"]), 1)
        self.assertEqual(len(res["observation"]), 1)
        self.assertEqual(res["observation"][0]["product"]["slug"], "p2")

    def test_boardwide_missing_heat(self):
        # 指标 a/b 全榜缺失 -> 热度分 None -> 全部进观察区
        latest = {1: {"x": {"value": 1.0}}, 2: {}}
        res = compute_board("t", self.cfg, self.products, latest, {},
                            "2026-09-23", 0.5)
        self.assertEqual(res["ranked"], [])
        self.assertEqual(len(res["observation"]), 2)
        self.assertIn("a", res["dropped"])
        self.assertIn("b", res["dropped"])

    def test_weighted_coverage(self):
        pm = {"a": {"value": 1.0}}
        self.assertEqual(weighted_coverage(self.cfg["heat"], pm), 0.6)
        self.assertEqual(weighted_coverage(self.cfg["heat"], {}), 0.0)


class TestDisplaySort(unittest.TestCase):
    """榜单展示排序：配置驱动、缺失值列后。"""

    def _item(self, name, **metrics):
        return {"name": name, "metric_values": metrics}

    def test_harness_stars_first_then_downloads(self):
        items = [
            self._item("no-repo-small", downloads=10),
            self._item("stars-low", github_stars=10),
            self._item("no-repo-big", downloads=500),
            self._item("stars-high", github_stars=100),
            self._item("no-data"),
        ]
        got = [x["name"] for x in queries.sort_items_for_board(
            items, "harness_core")]
        self.assertEqual(got, ["stars-high", "stars-low", "no-repo-big",
                               "no-repo-small", "no-data"])

    def test_downloads_aggregates_available_components(self):
        metrics = {
            "npm_monthly_downloads": {"value": 1000, "unit": "count"},
            "vscode_installs": {"value": 250, "unit": "count"},
            "appstore_rank": {"value": 4, "unit": "rank"},
        }
        derived = queries.derived_metrics(metrics)
        self.assertEqual(derived["downloads"]["value"], 1250)
        self.assertNotIn("appstore_rank", derived["downloads"]["title"])
        self.assertIn("npm 月下载 1,000", derived["downloads"]["title"])
        self.assertIn("VS Code 安装 250", derived["downloads"]["title"])

    def test_downloads_can_use_store_rating_count_only(self):
        derived = queries.derived_metrics({
            "appstore_rating_count": {"value": 336689, "unit": "count"}})
        self.assertEqual(derived["downloads"]["value"], 336689)
        self.assertIn("App Store 评分人数 336,689", derived["downloads"]["title"])

    def test_downloads_all_missing_is_absent(self):
        self.assertEqual(queries.derived_metrics(
            {"appstore_rank": {"value": 4, "unit": "rank"}}), {})

    def test_model_sorts_by_aa_intelligence(self):
        items = [
            self._item("low", aa_intelligence=10.0),
            self._item("high", aa_intelligence=20.0),
            self._item("missing"),
        ]
        got = [x["name"] for x in queries.sort_items_for_board(items, "model")]
        self.assertEqual(got, ["high", "low", "missing"])

    def test_nv_sorts_by_fp16_tflops(self):
        items = [
            self._item("slow", fp16_tflops=1000),
            self._item("fast", fp16_tflops=2000),
            self._item("missing"),
        ]
        got = [x["name"] for x in queries.sort_items_for_board(items, "nv_infer")]
        self.assertEqual(got, ["fast", "slow", "missing"])


if __name__ == "__main__":
    unittest.main()
