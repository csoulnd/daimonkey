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


class DeadlineExceeded(FetchError):
    """整体时间预算耗尽（慢速滴流时单次读超时不会触发）。"""


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
              headers: dict | None = None, timeout: float | None = None,
              deadline: float | None = None) -> httpx.Response:
        """带重试的请求。4xx（除 429）不重试直接返回，由调用方决定处置。

        timeout 为单请求覆盖（秒）；AA 免费接口部分分页较慢，源级可传更长超时。
        deadline 为整体时间预算（`time.monotonic()` 基准的绝对时间点）：到点即中断，
        包含"服务端持续慢速滴流"的情况——这种传输每次都有字节到达，单次读超时
        永远不会触发（2026-10-26 实测 AA 四页约 200KB/页在跨境链路上要 3 分钟）。
        命中预算抛 DeadlineExceeded，不重试，由源自己决定降级策略。
        """
        host = urlparse(url).netloc
        backoff = self.host_backoffs.get(host, self.backoff)
        last_err: Exception | None = None
        for attempt in range(len(backoff) + 1):
            self._throttle(url)
            if deadline is not None and time.monotonic() >= deadline:
                raise DeadlineExceeded(f"超过时间预算，放弃拉取: {url}")
            try:
                # httpx 里 timeout=None 是"禁用超时",不是"沿用 Client 默认值";
                # 未显式指定时不能传这个 key,否则会把 20s 读超时整个覆盖掉,
                # 遇到"连接存活但不再返回数据"的链路会永久阻塞(2026-10-20 实测)。
                extra = {} if timeout is None else {"timeout": timeout}
                if deadline is None:
                    r = self._client.request(method, url, json=json_body,
                                             headers=headers, **extra)
                else:
                    r = self._streamed(method, url, json_body, headers, timeout,
                                       deadline)
            except DeadlineExceeded:
                raise
            except httpx.HTTPError as e:
                last_err = e
                if deadline is not None and time.monotonic() >= deadline:
                    raise DeadlineExceeded(f"超过时间预算，放弃拉取: {url}") from e
                if attempt < len(backoff):
                    time.sleep(self._sleep_for(backoff[attempt], deadline))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After", "")
                delay = int(ra) if ra.isdigit() else backoff[min(attempt, len(backoff) - 1)]
                last_err = FetchError(f"HTTP {r.status_code} {url}")
                if attempt < len(backoff):
                    time.sleep(self._sleep_for(delay, deadline))
                continue
            return r
        raise FetchError(f"{url} 不可达: {last_err}")

    def _sleep_for(self, seconds: float, deadline: float | None) -> float:
        """退避等待，但不越过整体预算。"""
        if deadline is None:
            return seconds
        return max(0.0, min(seconds, deadline - time.monotonic()))

    def _streamed(self, method: str, url: str, json_body: dict | None,
                  headers: dict | None, timeout: float | None,
                  deadline: float) -> httpx.Response:
        """流式下载并逐块检查预算：慢速滴流也能按时中断。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded(f"超过时间预算，放弃拉取: {url}")
        eff_timeout = min(timeout or self.timeout, remaining)
        chunks: list[bytes] = []
        with self._client.stream(method, url, json=json_body, headers=headers,
                                 timeout=eff_timeout) as r:
            for chunk in r.iter_bytes():
                if time.monotonic() > deadline:
                    raise DeadlineExceeded(f"超过时间预算，中断下载: {url}")
                chunks.append(chunk)
            # httpx 已解压：去掉与解压后长度不符的头部，避免下游按旧长度误读。
            headers_out = [(k, v) for k, v in r.headers.multi_items()
                           if k.lower() not in ("content-encoding", "content-length",
                                                "transfer-encoding")]
            return httpx.Response(r.status_code, headers=headers_out,
                                  content=b"".join(chunks), request=r.request)
