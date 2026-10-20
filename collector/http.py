"""HTTP 客户端封装：同主机最小间隔（礼貌频率）、429/5xx 与网络错误退避重试。

规范要求：每源请求间隔 >= 1 秒，遵守 Retry-After，退避 5/15/45s。
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from .config import registry


class FetchError(Exception):
    pass


class Http:
    def __init__(self):
        cfg = registry().get("http", {})
        self.timeout = cfg.get("timeout", 20)
        self.backoff = list(cfg.get("backoff", [5, 15, 45]))
        self.min_interval = cfg.get("min_interval", 1.05)
        self.host_intervals = dict(cfg.get("host_intervals", {}))
        self.host_backoffs = dict(cfg.get("host_backoffs", {}))
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=self.timeout,
            headers={"User-Agent": "agent-heat-index/0.1 (data collection; contact: local)"},
        )
        self._last_hit: dict[str, float] = {}
        self.request_count = 0

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        interval = self.host_intervals.get(host, self.min_interval)
        wait = self._last_hit.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()
        self.request_count += 1

    def fetch(self, url: str, method: str = "GET", *, json_body: dict | None = None,
              headers: dict | None = None, timeout: float | None = None) -> httpx.Response:
        """带重试的请求。4xx（除 429）不重试直接返回，由调用方决定处置。

        timeout 为单请求覆盖（秒）；AA 免费接口部分分页较慢，源级可传更长超时。
        """
        host = urlparse(url).netloc
        backoff = self.host_backoffs.get(host, self.backoff)
        last_err: Exception | None = None
        for attempt in range(len(backoff) + 1):
            self._throttle(url)
            try:
                # httpx 里 timeout=None 是"禁用超时",不是"沿用 Client 默认值";
                # 未显式指定时不能传这个 key,否则会把 20s 读超时整个覆盖掉,
                # 遇到"连接存活但不再返回数据"的链路会永久阻塞(2026-10-20 实测)。
                extra = {} if timeout is None else {"timeout": timeout}
                r = self._client.request(method, url, json=json_body,
                                         headers=headers, **extra)
            except httpx.HTTPError as e:
                last_err = e
                if attempt < len(backoff):
                    time.sleep(backoff[attempt])
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After", "")
                delay = int(ra) if ra.isdigit() else backoff[min(attempt, len(backoff) - 1)]
                last_err = FetchError(f"HTTP {r.status_code} {url}")
                if attempt < len(backoff):
                    time.sleep(delay)
                continue
            return r
        raise FetchError(f"{url} 不可达: {last_err}")
