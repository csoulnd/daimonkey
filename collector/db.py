"""SQLite 存储层：建表（规范 6.1 DDL）、原始响应归档、指标写入。

与规范 DDL 的差异（为满足 LRU 保留要求）：
- raw_payloads.body_path 改为可空，新增 body_evicted_at：
  响应体文件到期被清理后保留元数据行，metrics.raw_payload_id 溯源链不断。
- metrics 的 UNIQUE 约束不含 board（指标值本身与所属榜无关，board 仅记录主榜）。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3

from .config import RAW_DIR, DB_PATH, now_iso

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    vendor          TEXT,
    category        TEXT NOT NULL,
    subcategory     TEXT,
    board_tags      TEXT,
    homepage        TEXT,
    repo            TEXT,
    repo_aliases    TEXT,
    license         TEXT,
    primary_lang    TEXT,
    npm_package     TEXT,
    pypi_package    TEXT,
    appstore_id     TEXT,
    vscode_ext_id   TEXT,
    launch_date     TEXT,
    status          TEXT DEFAULT 'active',
    maturity        TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_slug ON products(slug);

CREATE TABLE IF NOT EXISTS product_aliases (
    id              INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    alias_type      TEXT NOT NULL,
    alias_value     TEXT NOT NULL,
    discovered_at   TEXT NOT NULL,
    is_current      INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alias ON product_aliases(alias_type, alias_value);

CREATE TABLE IF NOT EXISTS integration_profile (
    product_id          INTEGER PRIMARY KEY REFERENCES products(id),
    has_docker          INTEGER,
    docker_image_ref    TEXT,
    has_cli             INTEGER,
    has_tui             INTEGER,
    has_webui           INTEGER,
    has_api             INTEGER,
    protocol            TEXT,
    auth_model          TEXT,
    min_mem_mb          INTEGER,
    container_friendly  INTEGER,
    install_difficulty  TEXT,
    verified_at         TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    key             TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    base_url        TEXT,
    auth_required   INTEGER DEFAULT 0,
    rate_limit      TEXT,
    parser_version  TEXT,
    enabled         INTEGER DEFAULT 1,
    reachable       INTEGER,
    last_success_at TEXT,
    failure_count   INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS raw_payloads (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    product_id      INTEGER REFERENCES products(id),
    request_url     TEXT NOT NULL,
    http_status     INTEGER,
    fetched_at      TEXT NOT NULL,
    body_sha256     TEXT NOT NULL,
    body_path       TEXT,
    body_evicted_at TEXT,
    body_bytes      INTEGER,
    content_type    TEXT,
    parser_version  TEXT,
    parse_ok        INTEGER,
    parse_error     TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_source_time ON raw_payloads(source_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_product ON raw_payloads(product_id, fetched_at);

CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    board           TEXT NOT NULL,
    metric_key      TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    raw_payload_id  INTEGER NOT NULL REFERENCES raw_payloads(id),
    collected_at    TEXT NOT NULL,
    granularity     TEXT NOT NULL,
    UNIQUE(product_id, metric_key, collected_at)
);
CREATE INDEX IF NOT EXISTS idx_metrics_lookup ON metrics(product_id, metric_key, collected_at);
CREATE INDEX IF NOT EXISTS idx_metrics_board ON metrics(board, metric_key, collected_at);

CREATE TABLE IF NOT EXISTS rankings (
    id                  INTEGER PRIMARY KEY,
    board               TEXT NOT NULL,
    snapshot_date       TEXT NOT NULL,
    rank                INTEGER NOT NULL,
    product_id          INTEGER NOT NULL REFERENCES products(id),
    heat_score          REAL,
    momentum_score      REAL,
    delta_7d            REAL,
    delta_30d           REAL,
    coverage            REAL,
    methodology_version TEXT NOT NULL,
    UNIQUE(board, snapshot_date, product_id)
);
CREATE INDEX IF NOT EXISTS idx_rankings_board_date ON rankings(board, snapshot_date, rank);

CREATE TABLE IF NOT EXISTS insights (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,
    summary         TEXT,
    source_url      TEXT NOT NULL,
    source_name     TEXT,
    published_at    TEXT,
    tags            TEXT,
    product_ids     TEXT,
    author          TEXT,
    llm_generated   INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'published',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collect_runs (
    id              INTEGER PRIMARY KEY,
    run_type        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    sources_total   INTEGER,
    sources_ok      INTEGER,
    sources_failed  INTEGER,
    rows_written    INTEGER,
    peak_mem_mb     REAL,
    status          TEXT,
    error_summary   TEXT
);
"""

# 新闻表独立建表：旧库在线升级只需 executescript（IF NOT EXISTS），不动既有结构。
NEWS_DDL = """
CREATE TABLE IF NOT EXISTS news_items (
    id              INTEGER PRIMARY KEY,
    url_hash        TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    summary         TEXT,
    source_key      TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    category        TEXT NOT NULL,
    region          TEXT NOT NULL DEFAULT 'global',
    author          TEXT,
    published_at    TEXT,
    points          INTEGER,
    product_ids     TEXT,
    raw_payload_id  INTEGER REFERENCES raw_payloads(id),
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at);
CREATE INDEX IF NOT EXISTS idx_news_category ON news_items(category);
"""


def ensure_news_tables(c) -> None:
    """既有数据库升级入口：全量更新 / 仅新闻更新前调用。"""
    c.executescript(NEWS_DDL)


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db() -> None:
    with conn() as c:
        c.executescript(DDL)
        ensure_news_tables(c)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_raw(c, source_id: int, source_key: str, product_id: int | None,
             request_url: str, http_status: int, body: bytes, content_type: str,
             parse_ok: bool, parse_error: str | None = None,
             parser_version: str = "") -> int:
    """响应体落盘为 gzip 文件，元数据行入库，返回 raw_payload id。"""
    import re
    from datetime import datetime, timezone
    h = sha256_hex(body)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_key = re.sub(r"[^a-z0-9_-]", "", source_key)
    folder = RAW_DIR / safe_key / day
    folder.mkdir(parents=True, exist_ok=True)
    body_path = folder / f"{h}.json.gz"
    if not body_path.exists():
        body_path.write_bytes(gzip.compress(body, mtime=0))
    cur = c.execute(
        "INSERT INTO raw_payloads(source_id, product_id, request_url, http_status,"
        " fetched_at, body_sha256, body_path, body_bytes, content_type,"
        " parser_version, parse_ok, parse_error)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, product_id, request_url, http_status, now_iso(), h,
         str(body_path), len(body), content_type, parser_version,
         1 if parse_ok else 0, parse_error))
    return cur.lastrowid


def load_body(c, raw_id: int) -> bytes | None:
    row = c.execute("SELECT body_path, body_evicted_at FROM raw_payloads WHERE id=?",
                    (raw_id,)).fetchone()
    if not row or not row["body_path"] or row["body_evicted_at"]:
        return None
    return gzip.decompress(open(row["body_path"], "rb").read())


def upsert_products(c, products: list[dict]) -> list[dict]:
    """registry 产品写入 products 表；返回带 id 的产品列表。"""
    ts = now_iso()
    out = []
    for p in products:
        row = c.execute("SELECT id FROM products WHERE slug=?", (p["slug"],)).fetchone()
        if row:
            pid = row["id"]
            c.execute(
                "UPDATE products SET name=?, vendor=?, category=?, board_tags=?, repo=?,"
                " repo_aliases=?, license=?, npm_package=?, pypi_package=?,"
                " appstore_id=?, vscode_ext_id=?, notes=?, updated_at=? WHERE id=?",
                (p["name"], p.get("vendor"), p["category"],
                 json.dumps(p.get("board_tags") or [p["category"]], ensure_ascii=False),
                 p.get("repo"), json.dumps(p.get("repo_aliases") or []),
                 p.get("license"), p.get("npm_package"), p.get("pypi_package"),
                 p.get("appstore_id"), p.get("vscode_ext_id"), p.get("notes"), ts, pid))
        else:
            cur = c.execute(
                "INSERT INTO products(slug, name, vendor, category, board_tags, repo,"
                " repo_aliases, license, npm_package, pypi_package, appstore_id,"
                " vscode_ext_id, notes, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?, ?)",
                (p["slug"], p["name"], p.get("vendor"), p["category"],
                 json.dumps(p.get("board_tags") or [p["category"]], ensure_ascii=False),
                 p.get("repo"), json.dumps(p.get("repo_aliases") or []),
                 p.get("license"), p.get("npm_package"), p.get("pypi_package"),
                 p.get("appstore_id"), p.get("vscode_ext_id"), p.get("notes"), ts, ts))
            pid = cur.lastrowid
        p = dict(p)
        p["id"] = pid
        p["boards"] = p.get("board_tags") or [p["category"]]
        out.append(p)
        # 接入属性（可选，人工初始录入）
        integ = p.get("integration")
        if integ:
            c.execute(
                "INSERT INTO integration_profile(product_id, has_docker, docker_image_ref,"
                " has_cli, has_tui, has_webui, has_api, protocol, container_friendly,"
                " install_difficulty, verified_at, notes)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?, 'registry 初始录入')"
                " ON CONFLICT(product_id) DO UPDATE SET"
                " has_docker=excluded.has_docker, docker_image_ref=excluded.docker_image_ref,"
                " has_cli=excluded.has_cli, has_tui=excluded.has_tui,"
                " has_webui=excluded.has_webui, has_api=excluded.has_api,"
                " protocol=excluded.protocol, container_friendly=excluded.container_friendly,"
                " install_difficulty=excluded.install_difficulty, verified_at=excluded.verified_at",
                (pid, integ.get("has_docker"), integ.get("docker_image_ref"),
                 integ.get("has_cli"), integ.get("has_tui"), integ.get("has_webui"),
                 integ.get("has_api"),
                 json.dumps(integ.get("protocol") or [], ensure_ascii=False),
                 integ.get("container_friendly"), integ.get("install_difficulty"), ts))
    return out


def upsert_sources(c, sources_cfg: list[dict]) -> dict[str, int]:
    ids = {}
    for s in sources_cfg:
        row = c.execute("SELECT id FROM sources WHERE key=?", (s["key"],)).fetchone()
        if row:
            sid = row["id"]
            c.execute("UPDATE sources SET name=?, source_type=?, base_url=?, rate_limit=?,"
                      " enabled=?, notes=?, auth_required=? WHERE id=?",
                      (s["name"], s["type"], s.get("base_url"), s.get("rate_limit", ""),
                       1 if s.get("enabled", True) else 0, s.get("notes", ""),
                       1 if s.get("auth") == "required" else 0, sid))
        else:
            cur = c.execute(
                "INSERT INTO sources(key, name, source_type, base_url, auth_required,"
                " rate_limit, enabled, notes) VALUES(?,?,?,?,?,?,?,?)",
                (s["key"], s["name"], s["type"], s.get("base_url"),
                 1 if s.get("auth") == "required" else 0, s.get("rate_limit", ""),
                 1 if s.get("enabled", True) else 0, s.get("notes", "")))
            sid = cur.lastrowid
        ids[s["key"]] = sid
    return ids


def mark_source(c, key: str, ok: bool, reachable: bool | None = None) -> None:
    if ok:
        c.execute("UPDATE sources SET last_success_at=?, reachable=1, failure_count=0"
                  " WHERE key=?", (now_iso(), key))
    else:
        c.execute("UPDATE sources SET failure_count=failure_count+1"
                  + (", reachable=0" if reachable is False else "")
                  + " WHERE key=?", (key,))


def save_metrics(c, source_id: int, board_of: dict[int, str], metrics: list) -> int:
    """写入指标；metric.raw_payload_id 强制非空（要求三）。"""
    n = 0
    for m in metrics:
        if m.value is None or m.raw_payload_id is None:
            continue
        c.execute(
            "INSERT INTO metrics(product_id, board, metric_key, value, unit, source_id,"
            " raw_payload_id, collected_at, granularity)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(product_id, metric_key, collected_at) DO UPDATE SET"
            " value=excluded.value, unit=excluded.unit, raw_payload_id=excluded.raw_payload_id",
            (m.product_id, board_of.get(m.product_id, "general"), m.metric_key,
             float(m.value), m.unit, source_id, m.raw_payload_id,
             m.collected_at or now_iso(), m.granularity))
        n += 1
    return n
