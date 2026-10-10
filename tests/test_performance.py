import gzip
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from collector import kbsearch
from collector.compression import compress_site

spec = importlib.util.spec_from_file_location("performance_server", Path(__file__).resolve().parents[1] / "scripts/serve.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class SearchTests(unittest.TestCase):
    def test_fulltext_filter_pagination_and_cache_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "index.json"
            notes = [dict(title="KV Cache", path="knowledge/kv.md", body="缓存 memory", group="knowledge", group_name="知识", url="/kb/kv"),
                     dict(title="Agent", path="agent/a.md", body="KV Cache production", group="agent", group_name="Agent", url="/kb/a")]
            source.write_text(json.dumps(notes), encoding="utf-8")
            with patch.object(kbsearch, "INDEX_FILE", source):
                self.assertEqual(kbsearch.search("kv")["total"], 2)
                self.assertEqual(kbsearch.search("缓存")["results"][0]["title"], "KV Cache")
                self.assertEqual(kbsearch.search("KV memory")["total"], 1)
                self.assertEqual(kbsearch.search("kv", group="agent")["results"][0]["title"], "Agent")
                self.assertEqual(kbsearch.search(path="knowledge/")["total"], 1)
                self.assertEqual(kbsearch.search("kv", offset=1, limit=1)["results"][0]["title"], "Agent")
                self.assertNotIn("body", kbsearch.search("kv")["results"][0])
                replacement = source.with_suffix(".tmp")
                replacement.write_text("[]")
                replacement.replace(source)
                self.assertEqual(kbsearch.search("kv")["total"], 0)


class HttpPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = Path(self.temp.name) / "dist"
        self.dist.mkdir()
        self.body = b"<h1>Page</h1>" * 1000
        (self.dist / "index.html").write_bytes(self.body)
        (self.dist / "assets").mkdir()
        (self.dist / "assets/test.js").write_bytes(b"console.log('test');" * 1000)
        compress_site(self.dist)
        self.patches = [patch.object(server, "DIST", self.dist), patch.object(server.Handler, "log_message")]
        for item in self.patches:
            item.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, path="/", method="GET", headers=None):
        conn = HTTPConnection(*self.httpd.server_address)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_gzip_and_identity_negotiation(self):
        status, headers, body = self.request(headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), self.body)
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        for accepted in ("identity", "gzip;q=0", "gzip;q=0, *;q=1"):
            _, headers, body = self.request(headers={"Accept-Encoding": accepted})
            self.assertNotIn("Content-Encoding", headers)
            self.assertEqual(body, self.body)

    def test_etag_head_versioned_cache_and_stale_gzip(self):
        _, headers, _ = self.request()
        status, _, body = self.request(headers={"If-None-Match": headers["ETag"]})
        self.assertEqual((status, body), (304, b""))
        status, headers, body = self.request(method="HEAD", headers={"Accept-Encoding": "gzip"})
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(self.request("/assets/test.js?v=abc")[1]["Cache-Control"], "public, max-age=31536000, immutable")
        (self.dist / "index.html").write_bytes(b"new page")
        os.utime(self.dist / "index.html.gz", (1, 1))
        _, headers, body = self.request(headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, b"new page")
        self.assertEqual(self.request("/%2e%2e/private")[0], 403)

    def test_search_route_and_bad_pagination(self):
        with patch.object(kbsearch, "search", return_value={"total": 1, "results": [{"title": "cache" * 500}]}):
            status, headers, body = self.request("/api/v1/knowledge/search?q=KV", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(gzip.decompress(body))["total"], 1)
            self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/v1/knowledge/search?offset=bad")[0], 400)


if __name__ == "__main__":
    unittest.main()
