"""各源解析函数测试（固化样本，不发真实请求）。"""
import time
import unittest
from types import SimpleNamespace

import httpx

from collector.sources.appstore_rss import parse_entries
from collector.sources.npm import _chunks, _sum_days
from collector.sources.vscode_marketplace import parse_installs


class TestHttpTimeout(unittest.TestCase):
    """回归(2026-10-20):未显式传 timeout 时不能透传 None。

    httpx 的 timeout=None 是"禁用超时",会把 Client 的 20s 读超时覆盖掉;
    曾导致一次数据更新卡在"连接存活但不返回数据"的链路上 9 分钟以上。
    """

    def _http(self):
        from collector.http import Http
        h = Http()
        h._throttle = lambda url: None
        self.seen = {}

        def fake_request(method, url, **kw):
            self.seen.update(kw)
            return httpx.Response(200, request=httpx.Request(method, url))

        h._client.request = fake_request
        return h

    def test_default_uses_client_timeout(self):
        h = self._http()
        self.assertEqual(h.fetch("https://example.com/x").status_code, 200)
        self.assertNotIn("timeout", self.seen)
        self.assertGreater(h._client.timeout.read, 0)

    def test_explicit_timeout_passed_through(self):
        h = self._http()
        h.fetch("https://example.com/x", timeout=45)
        self.assertEqual(self.seen["timeout"], 45)

    def _fake_stream(self, h, chunks):
        def fake_stream(method, url, **kw):
            req = httpx.Request(method, url)

            class _Ctx:
                def __enter__(self):
                    return SimpleNamespace(
                        status_code=200,
                        headers=httpx.Headers({"content-type": "application/json"}),
                        iter_bytes=lambda: iter(chunks),
                        request=req)

                def __exit__(self, *exc):
                    return False

            return _Ctx()

        h._client.stream = fake_stream
        return h

    def test_deadline_in_past_skips_request(self):
        from collector.http import DeadlineExceeded
        h = self._http()
        with self.assertRaises(DeadlineExceeded):
            h.fetch("https://example.com/x", deadline=time.monotonic() - 1)

    def test_deadline_stream_returns_full_body(self):
        h = self._fake_stream(self._http(), [b'{"a": ', b"1}"])
        r = h.fetch("https://example.com/x", deadline=time.monotonic() + 5)
        self.assertEqual(r.json(), {"a": 1})

    def test_deadline_mid_stream_aborts(self):
        """慢速滴流不会触发单次读超时，必须靠整体预算中断。"""
        from collector.http import DeadlineExceeded

        def slow():
            yield b"{"
            time.sleep(0.05)
            yield b"}"

        h = self._fake_stream(self._http(), slow())
        with self.assertRaises(DeadlineExceeded):
            h.fetch("https://example.com/x",
                    deadline=time.monotonic() + 0.01)


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
