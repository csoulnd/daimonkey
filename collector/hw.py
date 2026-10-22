"""硬件（NV）规格数据层：加速卡官方规格表的加载、校验与型号归一。

数据文件 `collector/hw_specs.json` 由人工维护（UTF-8、缩进 2），本模块只读不写。
新增加速卡 / 服务器 / 集群方案时只改 JSON，不在代码里硬编码单卡信息。

口径约定（与数据文件顶部的 `vendor_note` 呼应）：
- 算力优先填 dense（稠密）值。NVIDIA 官方产品页带 `*` 的 Tensor Core 数值是
  稀疏口径，收录时按官方值 ÷2 折算为 dense，并在该条 `sources_note` 说明；
- 厂商官方未披露、或无法从官方页面核实的数值一律填 null，并在 `sources_note`
  说明原因，禁止估算或引用第三方规格；
- `official_docs` 只收厂商官方域名（见 `OFFICIAL_DOC_HOSTS`），至少一条。

公开 API（集成按此接线）：
- load() -> list[dict]
- by_model(name: str) -> dict | None
- field_labels() -> dict
- validate(specs) -> list[str]
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

SPEC_PATH = Path(__file__).with_name("hw_specs.json")

# 规格表最少收录量：每个厂商至少 2 个型号、整表至少 8 个型号。
MIN_MODELS = 8
MIN_MODELS_PER_VENDOR = 2

REQUIRED_FIELDS = (
    "vendor",
    "series",
    "model",
    "aliases",
    "release_year",
    "process",
    "fp16_tflops",
    "bf16_tflops",
    "fp8_tflops",
    "int8_tops",
    "hbm_gb",
    "hbm_bw_gbs",
    "interconnect",
    "interconnect_bw_gbs",
    "tdp_w",
    "form_factor",
    "servers",
    "clusters",
    "official_docs",
)

# 允许为 null 的数值字段；其余数值字段必须给出数字。
NULLABLE_NUMERIC_FIELDS = (
    "release_year",
    "fp16_tflops",
    "bf16_tflops",
    "fp8_tflops",
    "int8_tops",
    "hbm_gb",
    "hbm_bw_gbs",
    "interconnect_bw_gbs",
    "tdp_w",
)

# 官方文档域名白名单（子域名同样放行，如 e.huawei.com、resources.nvidia.com）。
OFFICIAL_DOC_HOSTS = (
    "nvidia.com",
    "nvidia.cn",
    "amd.com",
    "intel.com",
    "intel.cn",
    "cambricon.com",
    "huawei.com",
    "hiascend.com",
    "t-head.cn",
    "aliyun.com",
    "alibaba.com",
)

SERVER_STATUSES = ("已收录", "规划中")

# 模板渲染用的中文列名与单位；顺序即建议展示顺序。
FIELD_LABELS = {
    "vendor": {"label": "厂商", "unit": ""},
    "series": {"label": "系列", "unit": ""},
    "model": {"label": "型号", "unit": ""},
    "aliases": {"label": "别名", "unit": ""},
    "release_year": {"label": "发布年份", "unit": "年"},
    "process": {"label": "制程", "unit": ""},
    "fp16_tflops": {"label": "FP16 算力", "unit": "TFLOPS"},
    "bf16_tflops": {"label": "BF16 算力", "unit": "TFLOPS"},
    "fp8_tflops": {"label": "FP8 算力", "unit": "TFLOPS"},
    "int8_tops": {"label": "INT8 算力", "unit": "TOPS"},
    "hbm_gb": {"label": "显存容量", "unit": "GB"},
    "hbm_bw_gbs": {"label": "显存带宽", "unit": "GB/s"},
    "interconnect": {"label": "互联技术", "unit": ""},
    "interconnect_bw_gbs": {"label": "互联带宽", "unit": "GB/s"},
    "tdp_w": {"label": "最大功耗", "unit": "W"},
    "form_factor": {"label": "形态", "unit": ""},
    "servers": {"label": "服务器/整机", "unit": ""},
    "clusters": {"label": "集群方案", "unit": ""},
    "official_docs": {"label": "官方文档", "unit": ""},
    "sources_note": {"label": "数据说明", "unit": ""},
}


class HwSpecError(ValueError):
    """规格表读取或校验失败（整表不可用）。"""


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _official_url(url) -> bool:
    """URL 必须为 https，且主机属于厂商官方域名白名单。"""
    if not _is_text(url) or not url.lower().startswith("https://"):
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == allowed or host.endswith("." + allowed)
               for allowed in OFFICIAL_DOC_HOSTS)


def _norm_name(value) -> str:
    """型号归一：全半角、大小写、空白、商标符号、中英文边界差异全部抹平。"""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).lower()
    for mark in ("®", "™", "©"):
        text = text.replace(mark, "")
    # 中文与字母/数字之间补空格，使「真武810E」与「真武 810E」等价。
    text = re.sub(r"([\u4e00-\u9fff])([0-9a-z])", r"\1 \2", text)
    text = re.sub(r"([0-9a-z])([\u4e00-\u9fff])", r"\1 \2", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _validate_doc_list(spec: dict, errors: list[str], where: str) -> None:
    docs = spec.get("official_docs")
    if not isinstance(docs, list) or not docs:
        errors.append(f"{where}: official_docs 必须是非空数组")
        return
    for j, doc in enumerate(docs):
        if not isinstance(doc, dict):
            errors.append(f"{where}.official_docs[{j}] 必须是对象")
            continue
        if not _is_text(doc.get("title")):
            errors.append(f"{where}.official_docs[{j}] 缺少 title")
        if not _official_url(doc.get("url")):
            errors.append(f"{where}.official_docs[{j}] 链接必须是官方域名 https 地址"
                          f"（{'/'.join(OFFICIAL_DOC_HOSTS)}）")


def _validate_entries(spec: dict, field: str, name_key: str,
                      errors: list[str], where: str) -> None:
    entries = spec.get(field)
    if not isinstance(entries, list):
        errors.append(f"{where}: {field} 必须是数组")
        return
    for j, entry in enumerate(entries):
        item_where = f"{where}.{field}[{j}]"
        if not isinstance(entry, dict):
            errors.append(f"{item_where} 必须是对象")
            continue
        if not _is_text(entry.get(name_key)):
            errors.append(f"{item_where} 缺少 {name_key}")
        status = entry.get("status")
        if status not in SERVER_STATUSES:
            errors.append(f"{item_where} status 必须是 {'/'.join(SERVER_STATUSES)}")
        if entry.get("url") is not None and not _official_url(entry.get("url")):
            errors.append(f"{item_where}.url 必须是官方域名 https 地址")


def validate(specs) -> list[str]:
    """校验规格表，返回错误列表（空列表 = 通过）。

    校验范围：字段完整性、数值合法性、官方链接域名、服务器/集群状态、
    型号与别名唯一性，以及「每厂商 ≥2、全表 ≥8」的收录量下限。
    """
    if not isinstance(specs, list):
        return ["规格表必须是 JSON 数组（或含 specs 数组的对象）"]

    errors: list[str] = []
    model_keys: dict[str, str] = {}
    alias_keys: dict[str, str] = {}
    vendor_counts: dict[str, int] = {}

    for i, spec in enumerate(specs):
        where = f"specs[{i}]"
        if not isinstance(spec, dict):
            errors.append(f"{where} 必须是对象")
            continue
        label = str(spec.get("model") or spec.get("vendor") or where)
        where = f"{where}({label})"

        for field in REQUIRED_FIELDS:
            if field not in spec:
                errors.append(f"{where}: 缺少字段 {field}")

        for field in ("vendor", "series", "model", "interconnect", "form_factor"):
            if not _is_text(spec.get(field)):
                errors.append(f"{where}: {field} 必须是非空字符串")

        aliases = spec.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            errors.append(f"{where}: aliases 必须是非空数组")
            aliases = []
        for j, alias in enumerate(aliases):
            if not _is_text(alias):
                errors.append(f"{where}: aliases[{j}] 必须是非空字符串")

        if spec.get("process") is not None and not _is_text(spec.get("process")):
            errors.append(f"{where}: process 必须是字符串或 null")

        for field in NULLABLE_NUMERIC_FIELDS:
            if field not in spec:
                continue
            value = spec[field]
            if value is None:
                continue
            if not _is_num(value):
                errors.append(f"{where}: {field} 必须是数字或 null")
            elif value < 0:
                errors.append(f"{where}: {field} 不能为负数")
            elif field == "release_year" and not (1950 <= value <= 2100):
                errors.append(f"{where}: release_year 超出合理范围")

        numeric_missing = any(spec.get(field) is None for field in
                              ("fp16_tflops", "fp8_tflops", "hbm_gb",
                               "hbm_bw_gbs", "tdp_w"))
        if numeric_missing and not _is_text(spec.get("sources_note")):
            errors.append(f"{where}: 有数值为 null 时必须用 sources_note 说明")

        _validate_entries(spec, "servers", "model", errors, where)
        _validate_entries(spec, "clusters", "name", errors, where)
        _validate_doc_list(spec, errors, where)

        vendor = str(spec.get("vendor") or "").strip()
        if vendor:
            vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1

        model = spec.get("model")
        if _is_text(model):
            key = _norm_name(model)
            if key in model_keys:
                errors.append(f"{where}: 型号与 {model_keys[key]} 重复")
            else:
                model_keys[key] = label

        for alias in aliases:
            if not _is_text(alias):
                continue
            key = _norm_name(alias)
            if not key:
                continue
            owner = alias_keys.get(key)
            if owner and owner != label:
                errors.append(f"{where}: 别名「{alias}」与 {owner} 冲突")
                continue
            alias_keys[key] = label

    total = sum(1 for spec in specs if isinstance(spec, dict))
    if total < MIN_MODELS:
        errors.append(f"规格表至少 {MIN_MODELS} 个型号（当前 {total}）")
    if len(vendor_counts) < 4:
        errors.append("规格表至少覆盖 4 个厂商")
    for vendor, count in sorted(vendor_counts.items()):
        if count < MIN_MODELS_PER_VENDOR:
            errors.append(f"厂商 {vendor} 至少收录 {MIN_MODELS_PER_VENDOR} 个型号"
                          f"（当前 {count}）")
    return errors


def load() -> list[dict]:
    """读取并校验 `collector/hw_specs.json`，返回规格列表。

    文件缺失、JSON 语法错误或校验不通过时抛 HwSpecError（含具体错误）。
    """
    try:
        raw = SPEC_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise HwSpecError(f"读取规格表失败：{SPEC_PATH}（{exc}）") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HwSpecError(f"规格表 JSON 语法错误：{SPEC_PATH}:{exc.lineno}: "
                          f"{exc.msg}") from exc

    specs = data.get("specs") if isinstance(data, dict) else data
    if not isinstance(specs, list):
        raise HwSpecError("规格表顶层必须是对象（含 specs 数组）或数组")
    errors = validate(specs)
    if errors:
        raise HwSpecError("规格表校验失败：" + "；".join(errors))
    return specs


def updated_at() -> str:
    """规格表顶层 `updated_at`（YYYY-MM-DD）；读不到时返回空串，不打断调用方。"""
    try:
        data = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("updated_at") or "") if isinstance(data, dict) else ""


def _alias_index(specs: list[dict]) -> list[tuple[str, set[str], dict]]:
    """(归一别名, 别名分词, 规格对象)；型号、厂商+型号、系列+型号都参与匹配。"""
    index: list[tuple[str, set[str], dict]] = []
    for spec in specs:
        vendor = str(spec.get("vendor") or "").strip()
        series = str(spec.get("series") or "").strip()
        model = str(spec.get("model") or "").strip()
        candidates = [model, f"{vendor} {model}".strip(),
                      f"{series} {model}".strip()]
        candidates.extend(spec.get("aliases") or [])
        for candidate in candidates:
            key = _norm_name(candidate)
            if key:
                index.append((key, set(key.split()), spec))
    return index


def by_model(name: str) -> dict | None:
    """按型号/别名归一化匹配，返回规格 dict；无匹配或歧义返回 None。

    支持 MLPerf 榜单里的写法，如 "H100 SXM"、"NVIDIA H100 80GB HBM3"、
    "MLU370-X8"、"真武810E"、"昇腾910C"。匹配规则：
    1. 归一化后完全相等优先；
    2. 否则别名分词必须全部出现在查询分词中（如 "h100 sxm" ⊂
       "nvidia h100 sxm5 80gb"），取命中词数最多者；
    3. 不同型号得分相同视为歧义，返回 None。
    """
    query = _norm_name(name)
    if not query:
        return None
    query_tokens = set(query.split())
    best_score: tuple[int, int] | None = None
    best_spec: dict | None = None
    ambiguous = False

    for alias, alias_tokens, spec in _alias_index(load()):
        if query == alias:
            score = (10 ** 6, len(alias))
        elif len(alias_tokens) >= 2 and alias_tokens <= query_tokens:
            score = (len(alias_tokens), len(alias))
        else:
            continue
        if best_score is None or score > best_score:
            best_score, best_spec, ambiguous = score, spec, False
        elif score == best_score and spec is not best_spec:
            ambiguous = True

    return None if ambiguous else best_spec


def field_labels() -> dict:
    """返回字段 → {label, unit} 映射（副本，调用方可安全修改）。"""
    return {key: dict(value) for key, value in FIELD_LABELS.items()}


__all__ = [
    "HwSpecError",
    "SPEC_PATH",
    "FIELD_LABELS",
    "OFFICIAL_DOC_HOSTS",
    "load",
    "by_model",
    "field_labels",
    "updated_at",
    "validate",
]
