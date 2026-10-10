"""采集器基类：一源一模块（collector/sources/），注册式接入。

collect() 返回 (metrics, notes)：
- 单个产品失败不抛异常（失败隔离），记入 notes，由 pipeline 汇总；
- 整源失败才抛异常。
每个指标必须携带 raw_payload_id（要求三：数字可溯源）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MetricOut:
    product_id: int
    metric_key: str
    value: float | None
    unit: str
    raw_payload_id: int | None = None
    collected_at: str = ""
    granularity: str = "daily"


@dataclass
class Ctx:
    """采集上下文：由 pipeline 构造并传递。"""
    conn: object            # sqlite3.Connection
    http: object            # collector.http.Http
    source_ids: dict        # key -> sources.id
    products: list          # 带 id 的产品列表
    now: str                # 本轮统一时间戳
    log: list = field(default_factory=list)   # 本源运行备注
    shared: dict = field(default_factory=dict)  # 跨源共享数据（如 bundleId -> trackId）


class BaseSource:
    key: str = ""
    name: str = ""
    parser_version: str = "v1.0"

    def collect(self, ctx: Ctx) -> tuple[list[MetricOut], list[str]]:
        raise NotImplementedError
