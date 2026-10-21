"""MLPerf Inference 结果源（NV 硬件方向首个数据源）。

链路（2026-10-21 定案）：
1. 取 MLCommons 官方仓库根目录的 `summary.json`（v6.1，约 650KB，492 条提交结果）；
   这份文件是官方汇总的机器可读版本，无需解析各 submitter 的目录树；
2. 只取 **closed / datacenter / Offline** 的 LLM 结果，按 `Result / total_accelerators`
   折算成“单卡吞吐（Tokens/s per 卡）”，同一加速卡取多提交中的最大值；
3. 加速卡型号做规范名归一（NVIDIA/AMD/Intel 厂商大小写、多余空白），
   归一后写入 products（origin=mlperf、category=nv、board_tags=[nv_infer]），
   掉出榜单的旧卡归档但保留页面；
4. 指标每格带 raw_payload_id，指向本次 summary.json 的原始响应（可 /raw/{id} 回查）；
5. 缓存 `data/cache/mlperf_summary.json` 七天有效（MLPerf 约季度更新，无需频繁请求）。

注意：v6.1 的 summary.json 里 `has_power` 全为 false，因此首版不产出能效指标；
待 MLPerf Power 数据可用时再扩展 `mlperf_energy`。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import CACHE_DIR, now_iso, registry
from .base import BaseSource, Ctx, MetricOut

PARSER_VERSION = "mlperf-summary-v1"
DEFAULT_CACHE_DAYS = 7
REQUEST_TIMEOUT = 90
MAX_BYTES = 8 * 1024 * 1024

# MLPerf 模型名 -> 站点指标键（只取 LLM 推理，避免把 resnet/bert 等旧基准混进同一口径）
MODEL_METRICS = {
    "gpt-oss-120b": "mlperf_gptoss_120b",
    "deepseek-r1": "mlperf_deepseek_r1",
    "llama3.1-8b": "mlperf_llama3_8b",
}
PRIMARY_METRIC = "mlperf_gptoss_120b"

_VENDOR_PREFIX = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "intel": "Intel",
    "google": "Google",
    "huawei": "Huawei",
    "cambricon": "寒武纪",
    "biren": "壁仞",
    "metax": "沐曦",
}
_SPACES = re.compile(r"\s+")


class MlperfError(Exception):
    """MLPerf 取数/解析失败（整源失败，由 pipeline 隔离）。"""


@dataclass
class AccelRow:
    name: str          # 归一后的展示名
    slug: str
    vendor: str
    values: dict       # metric_key -> 单卡吞吐（Tokens/s）


def _source_conf() -> dict:
    for src in registry()["sources"]:
        if src.get("key") == "mlperf":
            return src
    raise MlperfError("registry.yaml 缺少 sources.mlperf 配置")


def normalize_accel(raw: str | None, aliases: dict | None = None) -> str:
    """加速卡展示名归一：去多余空白、统一厂商大小写、应用显式别名。"""
    name = _SPACES.sub(" ", (raw or "").strip())
    if not name:
        return ""
    aliases = aliases or {}
    if name in aliases:
        name = aliases[name]
    low = name.lower()
    for key, vendor in _VENDOR_PREFIX.items():
        if low.startswith(key):
            rest = name[len(key):].strip()
            # Intel(R) Arc(TM) 这类商标写法保留原文，只规范厂商前缀
            if key in ("intel", "google", "huawei"):
                rest = rest.lstrip("(R)").strip()
            return f"{vendor} {rest}".strip()
    return name


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"mlperf-{slug}" if slug else "mlperf-unknown"


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_summary(payload, raw_id: int, *, aliases: dict | None = None) -> list[AccelRow]:
    """把 summary.json 聚合成“每张加速卡一行”。

    规则：closed + datacenter + Offline + LLM 模型；单卡值 = Result / total_accelerators，
    同一张卡多次提交取最大值。组合卡（"A + B"）与缺失型号直接跳过。
    """
    recs = payload.values() if isinstance(payload, dict) else payload
    if not isinstance(recs, (list, dict)) and not hasattr(recs, "__iter__"):
        raise MlperfError("MLPerf summary 结构无法识别")
    best: dict[str, dict] = {}
    seen = 0
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("Division", "")).lower() != "closed":
            continue
        if str(rec.get("SystemType", "")).lower() != "datacenter":
            continue
        if str(rec.get("Scenario", "")).lower() != "offline":
            continue
        metric = MODEL_METRICS.get(str(rec.get("Model", "")))
        if not metric:
            continue
        accel_raw = rec.get("accelerator_model_name")
        if not isinstance(accel_raw, str) or not accel_raw.strip():
            continue
        # 混合提交（"A + B" / "A and B"）无法折算单卡值，直接跳过
        if "+" in accel_raw or re.search(r"\band\b", accel_raw, re.I):
            continue
        result = rec.get("Result")
        if not _is_num(result) or result <= 0:
            continue
        per = float(result) / max(int(rec.get("total_accelerators") or 1), 1)
        name = normalize_accel(accel_raw, aliases)
        if not name:
            continue
        entry = best.setdefault(name.lower(), {"name": name, "values": {}})
        # 同名不同大小写的提交合并：保留首次出现的规范名
        if per > entry["values"].get(metric, 0.0):
            entry["values"][metric] = round(per, 1)
        seen += 1
    if not seen:
        raise MlperfError("summary 中没有可用的 closed/datacenter/Offline LLM 结果")
    rows: list[AccelRow] = []
    for entry in best.values():
        name = entry["name"]
        vendor = name.split(" ", 1)[0] if " " in name else "—"
        rows.append(AccelRow(name=name, slug=slugify(name), vendor=vendor,
                             values=entry["values"]))
    rows.sort(key=lambda r: (-r.values.get(PRIMARY_METRIC, 0.0), r.name.lower()))
    return rows


def _archive_stale(c, keep_ids: list[int]) -> None:
    if keep_ids:
        marks = ",".join("?" * len(keep_ids))
        c.execute("UPDATE products SET status='archived'"
                  " WHERE origin='mlperf' AND id NOT IN (" + marks + ")", keep_ids)
    else:
        c.execute("UPDATE products SET status='archived' WHERE origin='mlperf'")


def sync_products(c, rows: list[AccelRow]) -> tuple[dict[str, int], list[dict]]:
    """把加速卡同步为 products 行；返回 {slug: product_id} 与产品摘要。"""
    existing = [dict(r) for r in c.execute(
        "SELECT id, slug, name, vendor, origin, status FROM products"
        " WHERE category='nv' OR origin='mlperf'")]
    by_slug = {r["slug"]: r for r in existing}
    ts = now_iso()
    ids: dict[str, int] = {}
    synced: list[dict] = []
    for row in rows:
        candidate = by_slug.get(row.slug) or by_slug.get(row.slug.replace("mlperf-", ""))
        if candidate:
            pid = candidate["id"]
            c.execute(
                "UPDATE products SET name=?, vendor=?, category='nv',"
                " board_tags='[\"nv_infer\"]', status='active', origin='mlperf',"
                " notes=?, updated_at=? WHERE id=?",
                (row.name, row.vendor, "MLPerf Inference 自动收录（单卡吞吐）", ts, pid))
        else:
            cur = c.execute(
                "INSERT INTO products(slug, name, vendor, category, tier, origin,"
                " board_tags, status, notes, created_at, updated_at)"
                " VALUES(?,?,?,'nv',NULL,'mlperf','[\"nv_infer\"]','active',?,?,?)",
                (row.slug, row.name, row.vendor,
                 "MLPerf Inference 自动收录（单卡吞吐）", ts, ts))
            pid = cur.lastrowid
            by_slug[row.slug] = {"id": pid, "slug": row.slug}
        ids[row.slug] = pid
        synced.append({"id": pid, "slug": row.slug, "name": row.name,
                       "boards": ["nv_infer"], "origin": "mlperf", "status": "active"})
    _archive_stale(c, list(ids.values()))
    return ids, synced


def metrics_for_rows(rows: list[AccelRow], product_ids: dict[str, int],
                     raw_id: int, collected_at: str) -> list[MetricOut]:
    out: list[MetricOut] = []
    for row in rows:
        pid = product_ids.get(row.slug)
        if pid is None:
            continue
        for metric, value in row.values.items():
            out.append(MetricOut(
                product_id=pid, metric_key=metric, value=float(value),
                unit="tokens_s", raw_payload_id=raw_id, collected_at=collected_at))
    return out


def cache_fresh(cache: dict | None, days: float, now: datetime | None = None) -> bool:
    if not cache or not cache.get("fetched_at"):
        return False
    try:
        fetched = datetime.fromisoformat(str(cache["fetched_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - fetched < timedelta(days=days)


def _load_cache() -> dict | None:
    conf = _source_conf()
    path = CACHE_DIR / str(conf.get("cache_file", "mlperf_summary.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_cache(rows: list[AccelRow], raw_id: int) -> None:
    conf = _source_conf()
    path = CACHE_DIR / str(conf.get("cache_file", "mlperf_summary.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": now_iso(),
        "raw_id": raw_id,
        "rows": [{"name": r.name, "slug": r.slug, "vendor": r.vendor,
                  "values": r.values} for r in rows],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _rows_from_cache(cache: dict) -> list[AccelRow]:
    out: list[AccelRow] = []
    for r in cache.get("rows") or []:
        if not isinstance(r, dict) or not r.get("slug"):
            continue
        out.append(AccelRow(
            name=str(r.get("name") or r["slug"]),
            slug=str(r["slug"]),
            vendor=str(r.get("vendor") or "—"),
            values={k: float(v) for k, v in (r.get("values") or {}).items()
                    if _is_num(v)},
        ))
    return out


class MlperfSource(BaseSource):
    key = "mlperf"
    parser_version = PARSER_VERSION

    def collect(self, ctx: Ctx) -> tuple[list[MetricOut], list[str]]:
        conf = _source_conf()
        aliases = conf.get("aliases") or {}
        days = float(conf.get("cache_days", DEFAULT_CACHE_DAYS))
        cache = _load_cache()
        if cache_fresh(cache, days):
            rows = _rows_from_cache(cache)
            raw_id = int(cache.get("raw_id") or 0)
            collected_at = str(cache.get("fetched_at") or now_iso())
            notes = [f"MLPerf 缓存有效（{collected_at}，{days:g} 天内不重复请求）"]
        else:
            url = str(conf["base_url"])
            resp = ctx.http.fetch(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                raise MlperfError(f"MLPerf summary HTTP {resp.status_code}")
            body = resp.content
            if len(body) > MAX_BYTES:
                raise MlperfError(f"MLPerf summary 体积异常（{len(body)} 字节）")
            try:
                payload = resp.json()
            except ValueError as e:
                db.save_raw(ctx.conn, ctx.source_ids["mlperf"], "mlperf", None,
                            url, resp.status_code, body, "application/json", False,
                            parse_error=f"JSON 解析失败: {e}",
                            parser_version=PARSER_VERSION)
                raise MlperfError(f"MLPerf summary JSON 解析失败: {e}") from e
            raw_id = db.save_raw(ctx.conn, ctx.source_ids["mlperf"], "mlperf", None,
                                 url, resp.status_code, body, "application/json",
                                 True, parser_version=PARSER_VERSION)
            rows = parse_summary(payload, raw_id, aliases=aliases)
            _save_cache(rows, raw_id)
            collected_at = ctx.now
            notes = [f"MLPerf summary 已归档 {len(rows)} 张加速卡（raw #{raw_id}）"]
        if not rows:
            raise MlperfError("MLPerf 数据中没有可入榜的加速卡")
        ids, synced = sync_products(ctx.conn, rows)
        known = {p.get("id") for p in ctx.products}
        ctx.products.extend(p for p in synced if p["id"] not in known)
        metrics = metrics_for_rows(rows, ids, raw_id, collected_at)
        primary = sum(1 for r in rows if PRIMARY_METRIC in r.values)
        notes.append(f"NV 推理榜：{len(rows)} 张加速卡入池（{primary} 张有主指标"
                     f" gpt-oss-120b Offline 单卡吞吐）")
        return metrics, notes
