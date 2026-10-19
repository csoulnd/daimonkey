"""Artificial Analysis 免费模型指数源（FM 方向唯一排名口径）。

链路（2026-10-19 定案）：
1. `GET /api/v2/language/models/free`，头 `x-api-key`；分页 200/页，共 4 页 704 模型；
   **必须 httpx**（curl 在此线路会截断），单页可能较慢，源级超时 75s；
2. 每页原始响应 gzip 归档为 raw_payload（逐模型指标指向所属页 raw id，溯源完整）；
3. 模型池：按“同系列（去掉 Max/High/Low、Reasoning、Fallback 等变体）取总评分最高版本”
   去重，再按 AA Intelligence Index 取前 N（默认 40）自动入池；
4. 产品同步：AA 模型写入 products（origin=aa、board_tags=model、status=active），
   同次新增/更新后把掉出 Top N 的 AA 模型归档；旧 SuperCLUE 模型产品归档但保留页面；
5. 指数 v4.3：intelligence/coding/agentic + 六类域指数，产出 MetricOut(aa_*)；
   FM 榜 heat 使用 aa_intelligence，其他指数只作展示列；
6. 免费层限额 1000 次：`data/cache/aa_models.json` 七天有效，有效期内直接复用缓存，
   不发起任何请求（实际约每 7 天 4 次调用）；缓存行带 raw_id，重放指标不重复插行。

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
PARSER_VERSION = "aa-v2-free-2"
DEFAULT_CACHE_DAYS = 7
DEFAULT_TOP_N = 40
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

_VARIANT_RE = re.compile(
    r"\b(default fallback|fallback|non-reasoning|reasoning|max|high|medium|low|xhigh|minimal)\b",
    re.I)


class AAError(Exception):
    """AA 取数/匹配失败（整源失败，由 pipeline 隔离并记 notes）。"""


@dataclass
class AARow:
    slug: str
    name: str
    evaluations: dict
    raw_id: int
    creator: str = ""
    release_date: str = ""


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


def family_key(name: str | None) -> str:
    """同系列归并键：去掉括号变体与 effort/reasoning/fallback 词。"""
    text = re.sub(r"\([^)]*\)", " ", name or "")
    text = _VARIANT_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9.]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def clean_name(name: str | None) -> str:
    """展示名去掉 Default Fallback 等噪声，保留 (Max)/(High) 变体。"""
    text = name or ""
    text = re.sub(r",\s*[^)]*fallback[^)]*", "", text, flags=re.I)
    text = re.sub(r"\([^)]*fallback[^)]*\)", "", text, flags=re.I)
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
            creator = rec.get("model_creator") or {}
            rows.append(AARow(
                slug=str(rec["slug"]),
                name=str(rec.get("name") or rec["slug"]),
                evaluations=evals if isinstance(evals, dict) else {},
                raw_id=raw_id,
                creator=str(creator.get("name") or "") if isinstance(creator, dict) else "",
                release_date=str(rec.get("release_date") or ""),
            ))
    return rows, version


def select_universe(rows: list[AARow], top_n: int = DEFAULT_TOP_N) -> list[AARow]:
    """同系列取 Intelligence Index 最高版本，再按总评分取前 N。"""
    best: dict[str, tuple[float, AARow]] = {}
    for row in rows:
        value = row.evaluations.get("artificial_analysis_intelligence_index")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        key = family_key(row.name) or row.slug
        current = best.get(key)
        # 同分时取更短的名字（通常是去掉 Default Fallback 的规范名）
        if current is None or (float(value), -len(row.name)) > (current[0], -len(current[1].name)):
            best[key] = (float(value), row)
    ordered = sorted(best.values(), key=lambda x: (-x[0], x[1].name.lower()))
    return [
        AARow(slug=r.slug, name=clean_name(r.name), evaluations=r.evaluations,
              raw_id=r.raw_id, creator=r.creator, release_date=r.release_date)
        for _, r in ordered[:max(0, int(top_n))]
    ]


def metrics_for_rows(rows: list[AARow], product_ids: dict[str, int],
                     collected_at: str) -> list[MetricOut]:
    """逐指标产出 MetricOut；空值跳过，每个指标带所属页 raw id。"""
    metrics: list[MetricOut] = []
    for row in rows:
        pid = product_ids.get(row.slug)
        if pid is None:
            continue
        for aa_key, metric_key in AA_METRIC_MAP.items():
            value = row.evaluations.get(aa_key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            metrics.append(MetricOut(
                product_id=pid, metric_key=metric_key,
                value=float(value), unit="score",
                raw_payload_id=row.raw_id, collected_at=collected_at))
    return metrics


def _product_boards_archive(c, selected_ids: list[int]) -> None:
    """掉出 Top N 的 AA 模型归档；旧 SuperCLUE 模型产品也归档（页面保留）。"""
    if selected_ids:
        marks = ",".join("?" * len(selected_ids))
        c.execute("UPDATE products SET status='archived'"
                  " WHERE origin='aa' AND id NOT IN (" + marks + ")", selected_ids)
    else:
        c.execute("UPDATE products SET status='archived' WHERE origin='aa'")
    c.execute("UPDATE products SET status='archived'"
              " WHERE category='model' AND COALESCE(origin,'') <> 'aa'")


def sync_products(c, rows: list[AARow], registry_products: list[dict]) -> tuple[dict[str, int], list[dict]]:
    """把选中的 AA 模型同步为 products 行；返回 {aa_slug: product_id} 与产品摘要。"""
    existing = [dict(r) for r in c.execute(
        "SELECT id, slug, name, vendor, origin, aa_slug, status"
        " FROM products WHERE category='model'")]
    by_aa = {r["aa_slug"]: r for r in existing if r.get("aa_slug")}
    by_slug = {r["slug"]: r for r in existing}
    reg_by_aa = {p.get("aa_slug"): p for p in registry_products
                 if p.get("category") == "model" and p.get("aa_slug")}
    ts = now_iso()
    ids: dict[str, int] = {}
    synced: list[dict] = []
    for row in rows:
        candidate = (by_aa.get(row.slug) or reg_by_aa.get(row.slug)
                     or by_slug.get(row.slug) or by_slug.get(f"aa-{row.slug}"))
        if candidate:
            pid = candidate["id"]
            slug = candidate["slug"]
            c.execute(
                "UPDATE products SET name=?, vendor=?, category='model',"
                " board_tags='[\"model\"]', status='active', origin='aa',"
                " aa_slug=?, notes=?, updated_at=? WHERE id=?",
                (row.name, row.creator or candidate.get("vendor") or "—",
                 row.slug, "Artificial Analysis 自动收录（总评分口径）", ts, pid))
        else:
            base = f"aa-{row.slug}"
            slug = base
            n = 2
            while slug in by_slug:
                slug = f"{base}-{n}"
                n += 1
            cur = c.execute(
                "INSERT INTO products(slug, name, vendor, category, tier, origin,"
                " aa_slug, board_tags, status, notes, created_at, updated_at)"
                " VALUES(?,?,?,'model',NULL,'aa',?,'[\"model\"]','active',?,?,?)",
                (slug, row.name, row.creator or "—", row.slug,
                 "Artificial Analysis 自动收录（总评分口径）", ts, ts))
            pid = cur.lastrowid
            by_slug[slug] = {"id": pid, "slug": slug, "origin": "aa"}
        ids[row.slug] = pid
        synced.append({"id": pid, "slug": slug, "name": row.name,
                       "boards": ["model"], "origin": "aa", "status": "active"})
    _product_boards_archive(c, list(ids.values()))
    return ids, synced


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
        "rows": [{"slug": r.slug, "name": r.name, "evaluations": r.evaluations,
                  "raw_id": r.raw_id, "creator": r.creator,
                  "release_date": r.release_date} for r in rows],
    }
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def _rows_from_cache(cache: dict) -> list[AARow]:
    out: list[AARow] = []
    for r in cache.get("rows") or []:
        if not isinstance(r, dict) or not r.get("slug"):
            continue
        out.append(AARow(
            slug=str(r["slug"]), name=str(r.get("name") or r["slug"]),
            evaluations=r.get("evaluations") if isinstance(r.get("evaluations"), dict) else {},
            raw_id=int(r.get("raw_id") or 0), creator=str(r.get("creator") or ""),
            release_date=str(r.get("release_date") or "")))
    return out


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
        top_n = int(conf.get("top_n", DEFAULT_TOP_N))
        cache = _load_cache()
        if cache_fresh(cache, days):
            rows = _rows_from_cache(cache)
            version = cache.get("index_version")
            collected_at = str(cache.get("fetched_at") or now_iso())
            notes = [f"AA 缓存有效（{collected_at}，{days:g} 天内不重复请求）"]
        else:
            key = _env_key()
            if not key:
                raise AAError("未配置 AA_API_KEY（data/model_eval.env，600；禁止入库）")
            rows, version, notes = _fetch_all(ctx, key, conf["base_url"])
            _save_cache(rows, version)
            collected_at = ctx.now
        selected = select_universe(rows, top_n)
        if not selected:
            raise AAError("AA 数据中没有可用的 Intelligence Index，无法建立模型池")
        product_ids, synced = sync_products(ctx.conn, selected, ctx.products)
        known = {p.get("id") for p in ctx.products}
        ctx.products.extend(p for p in synced if p["id"] not in known)
        metrics = metrics_for_rows(selected, product_ids, collected_at)
        notes.append(f"AA 总评分 v{version or '?'} · 入榜 {len(selected)} 个模型"
                     f"（同系列去重后按 Intelligence Index 前 {top_n}）")
        return metrics, notes
