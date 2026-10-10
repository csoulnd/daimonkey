"""AI Agent 新闻：URL 规范化去重、RSS/Atom 解析、确定性分类与产品关联。

不引入 LLM：分类用 registry.yaml -> news.keyword_rules 的关键词规则（先匹配先得），
产品关联用产品名/仓库名/别名做词边界匹配，全程可复现。
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from .config import now_iso, registry

TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "spm", "ref", "ref_src",
    "si", "share_source",
}

_ASCII_TERM = re.compile(r"^[a-z0-9][a-z0-9 .+#/_-]*$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# ---- URL 规范化与去重键 ----

def normalize_url(url: str) -> str:
    """剥推广/跟踪参数与 fragment，统一大小写与末尾斜杠。"""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS
             and not k.lower().startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(),
                       path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


# ---- 文本清洗 ----

def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub(" ", text))).strip()


def truncate(text: str, limit: int = 280) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---- RSS / Atom 解析（标准 XML，无需第三方库） ----

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(el, name: str) -> str:
    for ch in el:
        if _local(ch.tag) == name:
            return "".join(ch.itertext()).strip()
    return ""


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_rss_item(el) -> dict | None:
    link = _child_text(el, "link")
    title = _child_text(el, "title")
    if not link or not title:
        return None
    summary = _child_text(el, "description") or _child_text(el, "summary")
    date = None
    for name in ("pubDate", "date", "published"):
        date = _parse_date(_child_text(el, name))
        if date:
            break
    author = _child_text(el, "creator") or _child_text(el, "author")
    return {"title": title, "url": link, "summary": strip_html(summary),
            "author": author or None, "published_at": date, "points": None}


def _parse_atom_entry(el) -> dict | None:
    link = ""
    for ch in el:
        if _local(ch.tag) != "link":
            continue
        rel = ch.get("rel", "alternate")
        href = ch.get("href", "").strip()
        if href and (rel == "alternate" or not link):
            if rel == "alternate":
                link = href
                break
            link = href
    title = _child_text(el, "title")
    if not link or not title:
        return None
    summary = _child_text(el, "summary") or _child_text(el, "content")
    date = _parse_date(_child_text(el, "published")) or \
        _parse_date(_child_text(el, "updated"))
    author = ""
    for ch in el:
        if _local(ch.tag) == "author":
            author = _child_text(ch, "name") or "".join(ch.itertext()).strip()
            break
    return {"title": title, "url": link, "summary": strip_html(summary),
            "author": author or None, "published_at": date, "points": None}


def parse_feed(body: bytes) -> list[dict]:
    """RSS 2.0 / Atom / RSS 1.0（RDF）通吃：按本地标签名取 item/entry。"""
    root = ET.fromstring(body)
    items: list[dict] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "item":
            parsed = _parse_rss_item(el)
        elif tag == "entry":
            parsed = _parse_atom_entry(el)
        else:
            continue
        if parsed:
            items.append(parsed)
    return items


# ---- 确定性分类（registry 关键词规则，先匹配先得） ----

def classify(title: str, summary: str, default: str, rules: dict) -> str:
    text = f"{title} {summary}".lower()
    for category, keywords in rules.items():
        for kw in keywords or []:
            if kw and kw.lower() in text:
                return category
    return default


# ---- 产品关联（V2 联动：新闻条目自动打产品标） ----

def build_matcher(products: list[dict]) -> list[tuple[dict, list[re.Pattern]]]:
    """产品名 / 仓库名 / news_aliases 编译为匹配器；news_match: false 可禁用。

    ASCII 短词（如 vercel/ai 的 basename "ai"、"go"）词边界也会满篇命中，
    一律跳过：ASCII 词要求 >=3 字符，CJK/混排词要求 >=2 字符。
    """
    entries: list[tuple[dict, list[re.Pattern]]] = []
    for p in products:
        if p.get("news_match") is False:
            continue
        terms = {p["name"].strip()}
        if p.get("repo"):
            terms.add(str(p["repo"]).split("/")[-1])
        terms.update(a.strip() for a in p.get("news_aliases") or [] if a.strip())
        patterns = []
        for term in terms:
            if not term:
                continue
            low = term.lower()
            if _ASCII_TERM.match(low):
                if len(low) < 3:
                    continue
                # ASCII 词做边界匹配，避免 "cursor" 命中 "cursory"、"go" 满天飞
                patterns.append(re.compile(
                    r"(?<![a-z0-9])" + re.escape(low) + r"(?![a-z0-9])"))
            elif len(low) >= 2:
                patterns.append(re.compile(re.escape(low)))
        if patterns:
            entries.append((p, patterns))
    return entries


def match_products(text_lower: str, matcher: list, limit: int = 6) -> list[dict]:
    hits = []
    for p, patterns in matcher:
        if any(pat.search(text_lower) for pat in patterns):
            hits.append(p)
            if len(hits) >= limit:
                break
    return hits


# ---- ISO 周分组（每周 digest） ----

def week_key(iso: str) -> str:
    dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def week_range(key: str) -> tuple[str, str]:
    """'2026-W41' -> ('2026-10-05', '2026-10-11')，ISO 周一为一周之始。"""
    year, week = key.split("-W")
    monday = datetime.fromisocalendar(int(year), int(week), 1)
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


# ---- 入库（去重：规范化 URL 的 sha256 唯一） ----

def save_items(c, entries: list[dict], *, source_key: str, source_name: str,
               matcher: list, rules: dict, region: str = "global") -> tuple[int, int]:
    """条目已含 category_default 与 raw_payload_id；返回 (入库, 采集到)。"""
    cfg = registry().get("news", {})
    lookback_days = int(cfg.get("lookback_days", 7))
    max_items = int(cfg.get("max_items_per_feed", 40))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = [e for e in entries
             if not e.get("published_at") or e["published_at"] >= cutoff]
    fresh.sort(key=lambda e: e.get("published_at") or "", reverse=True)
    inserted = 0
    ts = now_iso()
    for e in fresh[:max_items]:
        if not e.get("title") or not e.get("url"):
            continue
        text = f"{e['title']} {e.get('summary') or ''}".lower()
        category = classify(e["title"], e.get("summary") or "",
                            e.get("category_default", "product"), rules)
        products = match_products(text, matcher)
        cur = c.execute(
            "INSERT OR IGNORE INTO news_items(url_hash, title, url, summary,"
            " source_key, source_name, category, region, author, published_at,"
            " points, product_ids, raw_payload_id, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (url_hash(e["url"]), e["title"].strip(), e["url"],
             truncate(e.get("summary") or ""), source_key, source_name,
             category, e.get("region") or region, e.get("author"),
             e.get("published_at"), e.get("points"),
             json.dumps([p["id"] for p in products], separators=(",", ":")),
             e.get("raw_payload_id"), ts))
        inserted += cur.rowcount
    return inserted, len(entries)
