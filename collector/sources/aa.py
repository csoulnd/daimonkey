"""Artificial Analysis 免费模型指数源（FM 方向主数据通道）。

链路（2026-10-19 本机实测）：
1. `GET /api/v2/language/models/free`，头 `x-api-key`；分页 200/页，共 4 页 704 模型；
   **必须 httpx**（curl 在此线路会截断），单页可能较慢，源级超时 75s；
2. 每页原始响应 gzip 归档为 raw_payload（逐模型指标指向所属页 raw id，溯源完整）；
3. 只取 registry 中 category=model 的产品：优先 `aa_slug` 精确匹配，其次规范化名称唯一匹配；
4. 指数 v4.3：intelligence/coding/agentic + 六类域指数，产出 MetricOut(aa_*)；
5. 免费层限额 1000 次：`data/cache/aa_models.json` 七天有效，有效期内直接复用、
   不发起任何请求（实际约每 7 天 4 次调用）。

密钥只从 data/model_eval.env（AA_API_KEY，600）或环境变量读取，禁止入库/日志。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import CACHE_DIR, DATA_DIR, now_iso, registry
from .base import BaseSource, Ctx, MetricOut

ENV_FILE = DATA_DIR / "model_eval.env"
CACHE_FILE = CACHE_DIR / "aa_models.json"
KEY_NAME = "AA_API_KEY"
PARSER_VERSION = "aa-v2-free-1"
DEFAULT_CACHE_DAYS = 7
REQUEST_TIMEOUT = 75
MAX_PAGES = 20

AA_METRIC_MAP = {
    "artificial_analysis_intelligence_index": "aa_intelligence",
    "artificial_analysis_coding_index": "aa_coding",
    "artificial_analysis_agentic_index": "aa_agentic",
    "artificial_analysis_finance_and_accounting_index": "aa_finance",
    "artificial_analysis_strategy_and_ops_index": "aa_strategy",
    "artificial_analysis_legal_index": "aa_legal",
    "artificial_analysis_healthcare_and_medical_index": "aa_healthcare",
    "artificial_analysis_engineering_index": "aa_engineering",
    "artificial_analysis_economics_index": "aa_economics",
}


class AAError(Exception):
    """AA 取数/匹配失败（整源失败，由 pipeline 隔离并记 notes）。"""


@dataclass
class AARow:
    slug: str
    name: str
    evaluations: dict
    raw_id: int


def _env_key() -> str | None:
    """读 AA_API_KEY：环境变量优先，其次 data/model_eval.env（600）。"""
    value = os.environ.get(KEY_NAME, "").strip()
    if value:
        return value
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == KEY_NAME:
                return v.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def _norm_name(name: str | None) -> str:
    text = (name or "").lower().replace("-", " ").replace("_", " ")
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_pages(pages: list[dict], raw_ids: list[int]) -> tuple[list[AARow], float | None]:
    """把逐页响应扁平化为模型行；版本号取响应里的 intelligence_index_version。"""
    if len(pages) != len(raw_ids):
        raise AAError("分页响应与 raw id 数量不一致")
    rows: list[AARow] = []
    version = None
    for payload, raw_id in zip(pages, raw_ids):
        version = payload.get("intelligence_index_version") or version
        data = payload.get("data")
        if not isinstance(data, list):
            raise AAError("AA 响应缺少 data 数组")
        for rec in data:
            if not isinstance(rec, dict) or not rec.get("slug"):
                continue
            evals = rec.get("evaluations")
            rows.append(AARow(slug=str(rec["slug"]),
                              name=str(rec.get("name") or rec["slug"]),
                              evaluations=evals if isinstance(evals, dict) else {},
                              raw_id=raw_id))
    return rows, version


def match_models(rows: list[AARow], products: list[dict]) -> list[tuple[dict, AARow]]:
    """registry 模型 → AA 行：aa_slug 精确优先；否则规范化名称唯一匹配。"""
    by_slug = {r.slug: r for r in rows}
    by_name: dict[str, list[AARow]] = {}
    for row in rows:
        by_name.setdefault(_norm_name(row.name), []).append(row)
    out: list[tuple[dict, AARow]] = []
    for p in products:
        if p.get("category") != "model":
            continue
        row = by_slug.get(p.get("aa_slug")) if p.get("aa_slug") else None
        if row is None:
            candidates = by_name.get(_norm_name(p.get("name")), [])
            row = candidates[0] if len(candidates) == 1 else None
        if row is not None:
            out.append((p, row))
    return out


def metrics_from_matches(matches: list[tuple[dict, AARow]],
                         collected_at: str) -> list[MetricOut]:
    """逐指标产出 MetricOut；空值跳过，每个指标带所属页 raw id。"""
    metrics: list[MetricOut] = []
    for product, row in matches:
        for aa_key, metric_key in AA_METRIC_MAP.items():
            value = row.evaluations.get(aa_key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            metrics.append(MetricOut(
                product_id=product["id"], metric_key=metric_key,
                value=float(value), unit="score",
                raw_payload_id=row.raw_id, collected_at=collected_at))
    return metrics


def cache_fresh(cache: dict | None, days: float = DEFAULT_CACHE_DAYS,
                now: datetime | None = None) -> bool:
    if not cache or not cache.get("fetched_at"):
        return False
    try:
        fetched = datetime.fromisoformat(
            str(cache["fetched_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - fetched < timedelta(days=days)


def _load_cache() -> dict | None:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_cache(rows: list[AARow], version: float | None) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": now_iso(),
        "index_version": version,
        "rows": [{"slug": r.slug, "name": r.name,
                  "evaluations": r.evaluations, "raw_id": r.raw_id}
                 for r in rows],
    }
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def _source_conf() -> dict:
    for src in registry()["sources"]:
        if src.get("key") == "aa":
            return src
    raise AAError("registry.yaml 缺少 sources.aa 配置")


def _fetch_all(ctx: Ctx, key: str, url: str) -> tuple[list[AARow], float | None, list[str]]:
    """逐页拉取并归档 raw；任何一页失败整源失败（不产出半套指标）。"""
    rows: list[AARow] = []
    raw_ids: list[int] = []
    pages: list[dict] = []
    version: float | None = None
    total_pages = 1
    page = 1
    while page <= total_pages and page <= MAX_PAGES:
        page_url = f"{url}{'&' if '?' in url else '?'}page={page}"
        resp = ctx.http.fetch(page_url, headers={"x-api-key": key},
                              timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise AAError(f"AA API HTTP {resp.status_code}（page {page}）")
        error = None
        try:
            payload = resp.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                error = "响应缺少 data 数组"
                payload = {}
        except ValueError as e:
            payload = {}
            error = f"JSON 解析失败: {e}"
        raw_id = db.save_raw(ctx.conn, ctx.source_ids["aa"], "aa", None,
                             page_url, resp.status_code, resp.content,
                             "application/json", error is None,
                             parse_error=error, parser_version=PARSER_VERSION)
        if error:
            raise AAError(error)
        pages.append(payload)
        raw_ids.append(raw_id)
        if page == 1:
            pagination = payload.get("pagination") or {}
            try:
                total_pages = max(1, int(pagination.get("total_pages") or 1))
            except (TypeError, ValueError):
                total_pages = 1
        page += 1
    rows, version = parse_pages(pages, raw_ids)
    notes = [f"AA API {len(pages)} 页 {len(rows)} 模型已归档（raw 溯源）"]
    if total_pages > MAX_PAGES:
        notes.append(f"AA 分页 {total_pages} 页超过上限 {MAX_PAGES}，仅取前 {MAX_PAGES} 页")
    return rows, version, notes


class AASource(BaseSource):
    key = "aa"
    parser_version = PARSER_VERSION

    def collect(self, ctx: Ctx) -> tuple[list[MetricOut], list[str]]:
        conf = _source_conf()
        days = float(conf.get("cache_days", DEFAULT_CACHE_DAYS))
        cache = _load_cache()
        if cache_fresh(cache, days):
            return [], [f"AA 缓存有效（{cache.get('fetched_at')}，{days:g} 天内不重复请求）"]
        key = _env_key()
        if not key:
            raise AAError("未配置 AA_API_KEY（data/model_eval.env，600；禁止入库）")
        rows, version, notes = _fetch_all(ctx, key, conf["base_url"])
        _save_cache(rows, version)
        products = [p for p in ctx.products if p.get("category") == "model"]
        matches = match_models(rows, products)
        if not matches:
            raise AAError("AA 数据与 registry 模型未能匹配，检查 aa_slug/模型名")
        metrics = metrics_from_matches(matches, ctx.now)
        notes.append(f"AA 指数 v{version or '?'} · registry 匹配 {len(matches)}/{len(products)}")
        return metrics, notes
