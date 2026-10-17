"""路径常量与 registry.yaml 加载。所有可变配置集中在 registry.yaml。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "heat_index.sqlite"
DIST_DIR = ROOT / "dist"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yaml"

_reg: dict | None = None


def registry() -> dict:
    global _reg
    if _reg is None:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            _reg = yaml.safe_load(f)
    return _reg


def ensure_dirs() -> None:
    for p in (DATA_DIR, RAW_DIR, CACHE_DIR, SNAPSHOT_DIR, LOG_DIR, DIST_DIR):
        p.mkdir(parents=True, exist_ok=True)


def resolve_data_path(value: str | Path, data_dir: Path | None = None) -> Path:
    """把库内存储的路径解析为实际文件路径。

    库内一律存相对 DATA_DIR 的路径（`raw/<源>/<日期>/<hash>.json.gz`），
    换机器/改目录后依然可读；同时兼容迁移前遗留的绝对路径。
    data_dir 显式传入时以调用方为准（便于测试替换数据目录）。
    """
    p = Path(value)
    return p if p.is_absolute() else (data_dir or DATA_DIR) / p


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")
