"""各源解析函数测试（固化样本，不发真实请求）。"""
import unittest

from collector.sources.appstore_rss import parse_entries
from collector.sources.npm import _chunks, _sum_days
from collector.sources.vscode_marketplace import parse_installs


class TestNpm(unittest.TestCase):
    def test_chunks(self):
        self.assertEqual([len(c) for c in _chunks(list(range(10)), 4)], [4, 4, 2])

    def test_sum_days_window(self):
        series = [
            {"day": "2026-07-01", "downloads": 5},
            {"day": "2026-07-31", "downloads": 7},
            {"day": "2026-08-01", "downloads": 10},
            {"day": "2026-08-30", "downloads": 20},
            {"day": "2026-09-01", "downloads": 999},   # 窗口外
        ]
        self.assertEqual(_sum_days(series, "2026-07-01", "2026-07-31"), 12)
        self.assertEqual(_sum_days(series, "2026-08-01", "2026-08-30"), 30)
        self.assertEqual(_sum_days(series, "2026-09-02", "2026-09-30"), 0)


class TestVscode(unittest.TestCase):
    def test_parse_installs(self):
        data = {"results": [{"extensions": [{
            "publisherName": "GitHub",
            "extensionName": "copilot",
            "statistics": [
                {"statisticName": "install", "value": 74_622_276},
                {"statisticName": "updateCount", "value": 579_432_965},
            ],
        }]}]}
        self.assertEqual(parse_installs(data), {"github.copilot": 74_622_276})

    def test_parse_empty(self):
        self.assertEqual(parse_installs({}), {})
        self.assertEqual(parse_installs({"results": []}), {})


class TestAppstoreRss(unittest.TestCase):
    def test_parse_entries_rank_order(self):
        data = {"feed": {"entry": [
            {"id": {"attributes": {"im:id": "111"}},
             "im:name": {"label": "Muse from Meta"},
             "category": {"attributes": {"label": "Productivity"}}},
            {"id": {"attributes": {"im:id": "222"}},
             "im:name": {"label": "ChatGPT"},
             "category": {"attributes": {"label": "Productivity"}}},
        ]}}
        entries = parse_entries(data)
        self.assertEqual(entries[0]["id"], "111")
        self.assertEqual(entries[0]["rank"], 1)
        self.assertEqual(entries[1]["rank"], 2)
        self.assertEqual(entries[1]["name"], "ChatGPT")

    def test_single_entry_dict_form(self):
        data = {"feed": {"entry": {
            "id": {"attributes": {"im:id": "1"}},
            "im:name": {"label": "X"},
            "category": {"attributes": {"label": "G"}},
        }}}
        self.assertEqual(len(parse_entries(data)), 1)

    def test_malformed_skipped(self):
        self.assertEqual(parse_entries({"feed": {"entry": [{"im:name": {"label": "no-id"}}]}}, ), [])


if __name__ == "__main__":
    unittest.main()
