"""云剪贴板（个人工作台）：两台设备间的文本剪贴板同步 + 小云盘文件。

架构与约束（skills/workspace 专家登记 2026-10-19）：
- 数据：clipboard_items 表按 owner（用户名）隔离，与 assistant_sessions.owner 同模式；
  文件落 data/clipboard/{owner}/{sha256}{后缀}，库内存相对 DATA_DIR 的 posix 路径
  （仿 db.save_raw，换机/改目录后依然可读）。
- 配额：registry.yaml → clipboard 块，三级限制（单条文本 / 单文件 / 每用户文件总量）。
- 保留策略：文本滚动保留最近 keep_text_items 条（插入时清理）；文件长期保留、
  手动删除，上传时强制配额。
- 上传走 raw body（serve.py 分块流式读取，经 read 回调进入本模块），避开 multipart。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, now_iso, registry
from . import db

CLIPBOARD_DIR = DATA_DIR / "clipboard"

DEFAULTS: dict = {
    "max_text_bytes": 65536,        # 单条文本上限（64KB，与 serve._read_json 天然一致）
    "max_file_bytes": 104857600,    # 单文件上限（100MB）
    "quota_mb": 1024,               # 每用户文件总配额（1GB）
    "keep_text_items": 100,         # 文本条目滚动保留条数
}


class ClipboardError(Exception):
    """剪贴板操作失败（参数/超限）。"""


class ClipboardTooLarge(ClipboardError):
    """单条文本或单个文件超出大小上限。"""


class ClipboardQuota(ClipboardError):
    """用户文件总配额已用尽。"""


def cfg() -> dict:
    """registry.yaml → clipboard 块，与默认值合并。"""
    merged = dict(DEFAULTS)
    merged.update(registry().get("clipboard") or {})
    return merged


def quota_bytes() -> int:
    return int(cfg()["quota_mb"]) * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _connect():
    c = db.conn()
    db.ensure_clipboard_tables(c)
    return c


def _row_to_item(row) -> dict:
    item = {
        "id": row["id"],
        "kind": row["kind"],
        "created_at": row["created_at"],
    }
    if row["kind"] == "text":
        item["content"] = row["content"]
        item["text_bytes"] = len((row["content"] or "").encode("utf-8"))
    else:
        item["file_name"] = row["file_name"]
        item["file_bytes"] = row["file_bytes"]
        item["content_type"] = row["content_type"]
    return item


def list_items(owner: str, limit: int = 100) -> list[dict]:
    """该用户全部条目，新在上。文本与文件混排，前端按 kind 分区渲染。"""
    with _connect() as c:
        rows = c.execute(
            "SELECT * FROM clipboard_items WHERE owner = ?"
            " ORDER BY id DESC LIMIT ?", (owner, limit)).fetchall()
    return [_row_to_item(r) for r in rows]


def add_text(owner: str, content: str) -> dict:
    """存入一条文本；与最近一条相同则复用（跨设备重复粘贴常见）。
    超上限抛 ClipboardTooLarge；插入后滚动清理旧条目。"""
    if not isinstance(content, str) or not content.strip():
        raise ClipboardError("剪贴板内容不能为空")
    if len(content.encode("utf-8")) > int(cfg()["max_text_bytes"]):
        raise ClipboardTooLarge("单条文本超过上限")
    with _connect() as c:
        last = c.execute(
            "SELECT * FROM clipboard_items WHERE owner = ? AND kind = 'text'"
            " ORDER BY id DESC LIMIT 1", (owner,)).fetchone()
        if last is not None and last["content"] == content:
            return _row_to_item(last)
        cur = c.execute(
            "INSERT INTO clipboard_items(owner, kind, content, created_at)"
            " VALUES(?, 'text', ?, ?)", (owner, content, _now()))
        new_id = cur.lastrowid
        keep = int(cfg()["keep_text_items"])
        stale = c.execute(
            "SELECT id FROM clipboard_items WHERE owner = ? AND kind = 'text'"
            " ORDER BY id DESC LIMIT -1 OFFSET ?", (owner, keep)).fetchall()
        if stale:
            c.executemany("DELETE FROM clipboard_items WHERE id = ?",
                          [(r["id"],) for r in stale])
        row = c.execute("SELECT * FROM clipboard_items WHERE id = ?",
                        (new_id,)).fetchone()
        return _row_to_item(row)


def _safe_name(name: str) -> str:
    """原始文件名仅作展示：去路径成分、去控制字符、限长，空则回退。"""
    base = re.sub(r'[/\\]', "/", name or "").split("/")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:120] or "file"


def add_file(owner: str, name: str, size: int, read, content_type: str = "") -> dict:
    """流式接收一个文件并落盘。

    read(n) 回调返回最多 n 字节（serve.py 传 self.rfile.read）；
    实际读到的字节数以流为准，超过声明或上限即中止并清理。
    超单文件上限抛 ClipboardTooLarge；超总配额抛 ClipboardQuota。
    """
    max_file = int(cfg()["max_file_bytes"])
    if size <= 0:
        raise ClipboardError("文件为空")
    if size > max_file:
        raise ClipboardTooLarge("单个文件超过上限")
    used = file_bytes(owner)
    if used + size > quota_bytes():
        raise ClipboardQuota("云盘配额不足，请先删除旧文件")

    display = _safe_name(name)
    folder = CLIPBOARD_DIR / re.sub(r"[^a-z0-9_-]", "", owner.lower())
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(display).suffix[:16]
    tmp = folder / f".tmp-{os_getpid()}-{next(_counter)}"
    sha = hashlib.sha256()
    written = 0
    try:
        with open(tmp, "wb") as out:
            while written < size:
                chunk = read(min(1 << 20, size - written))
                if not chunk:
                    break
                if written + len(chunk) > max_file:
                    raise ClipboardTooLarge("单个文件超过上限")
                out.write(chunk)
                sha.update(chunk)
                written += len(chunk)
        if written != size:
            raise ClipboardError("上传中断：请重试")
        final = folder / f"{sha.hexdigest()}{suffix}"
        if final.exists():
            tmp.unlink()          # 同内容文件已落盘，直接复用
        else:
            tmp.rename(final)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    with _connect() as c:
        cur = c.execute(
            "INSERT INTO clipboard_items(owner, kind, file_name, file_path,"
            " file_bytes, content_type, created_at)"
            " VALUES(?, 'file', ?, ?, ?, ?, ?)",
            (owner, display, final.relative_to(DATA_DIR).as_posix(),
             written, content_type or "", _now()))
        row = c.execute("SELECT * FROM clipboard_items WHERE id = ?",
                        (cur.lastrowid,)).fetchone()
        return _row_to_item(row)


def get_file(owner: str, item_id: int) -> dict | None:
    """取文件条目（含绝对路径）；不存在或非本人返回 None。"""
    with _connect() as c:
        row = c.execute(
            "SELECT * FROM clipboard_items WHERE id = ? AND owner = ?"
            " AND kind = 'file'", (item_id, owner)).fetchone()
    if row is None:
        return None
    item = _row_to_item(row)
    item["path"] = (DATA_DIR / row["file_path"]).resolve()
    if not item["path"].is_relative_to(CLIPBOARD_DIR.resolve()):
        return None            # 库内路径异常（不应发生），拒绝出文件
    return item


def delete_item(owner: str, item_id: int) -> bool:
    """删除条目；文件仅在无其他条目引用同一落盘路径时才 unlink（同内容复用）。"""
    with _connect() as c:
        row = c.execute(
            "SELECT * FROM clipboard_items WHERE id = ? AND owner = ?",
            (item_id, owner)).fetchone()
        if row is None:
            return False
        c.execute("DELETE FROM clipboard_items WHERE id = ?", (item_id,))
        if row["kind"] == "file" and row["file_path"]:
            sharing = c.execute(
                "SELECT COUNT(*) FROM clipboard_items WHERE file_path = ?",
                (row["file_path"],)).fetchone()[0]
            if not sharing:
                try:
                    (DATA_DIR / row["file_path"]).unlink(missing_ok=True)
                except OSError:
                    pass          # 文件缺失不阻断删行
    return True


def file_bytes(owner: str) -> int:
    """该用户文件占用总字节数（配额统计口径）。"""
    with _connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(file_bytes), 0) FROM clipboard_items"
            " WHERE owner = ? AND kind = 'file'", (owner,)).fetchone()
    return int(row[0])


def usage(owner: str) -> dict:
    conf = cfg()
    with _connect() as c:
        counts = c.execute(
            "SELECT kind, COUNT(*) FROM clipboard_items WHERE owner = ?"
            " GROUP BY kind", (owner,)).fetchall()
    text_n = sum(n for kind, n in counts if kind == "text")
    file_n = sum(n for kind, n in counts if kind == "file")
    return {
        "text_count": text_n,
        "file_count": file_n,
        "file_bytes": file_bytes(owner),
        "quota_bytes": int(conf["quota_mb"]) * 1024 * 1024,
        "max_file_bytes": int(conf["max_file_bytes"]),
        "max_text_bytes": int(conf["max_text_bytes"]),
        "keep_text_items": int(conf["keep_text_items"]),
    }


# 临时文件名序号（避免同进程并发上传互踩）
import itertools
import os as _os


def os_getpid() -> int:
    return _os.getpid()


_counter = itertools.count(1)
