#!/usr/bin/env python3
"""本地静态服务 + 手动更新接口（更新按钮的后端）。

用法（用项目 venv 的解释器启动，更新子进程会继承同一解释器）：
    .venv/bin/python scripts/serve.py            # 默认 127.0.0.1:8080
    .venv/bin/python scripts/serve.py --host 0.0.0.0 --port 8080

端点：
    GET  / 与 /xxx.html、/api/v1/*.json   -> dist/ 静态文件
    GET  /api/v1/update/status            -> 更新状态（never/running/success/partial/failed）
    POST /api/v1/update                   -> 触发一次完整更新（子进程 python3 -m collector --update）
    POST /api/v1/update/news              -> 仅更新新闻（子进程 python3 -m collector --collect-news）

站点浏览（GET）无需鉴权；更新接口（POST）通过 X-Update-Password 验证密码。
密码的 SHA-256 校验值保存在 data/update_password.sha256，不接受 URL 令牌。
"""
from __future__ import annotations

import argparse
import hashlib
import gzip
import json
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs
from email.utils import formatdate

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector import kbsearch
DIST = ROOT / "dist"
STATUS_FILE = ROOT / "data" / "update_status.json"
PASSWORD_FILE = ROOT / "data" / "update_password.sha256"


def password_valid(supplied: str) -> bool:
    try:
        expected = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    actual = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    return bool(supplied) and secrets.compare_digest(actual, expected)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}

_lock = threading.Lock()
_proc: dict = {"running": None}


def status_payload() -> dict:
    p = _proc.get("running")
    if p is not None and p.poll() is None:
        return {"state": "running", "note": "采集进行中，通常需要 1-5 分钟"}
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"state": "never", "note": "尚未执行过更新"}


class Handler(BaseHTTPRequestHandler):
    server_version = "heat-index/0.1"

    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def _gzip_accepted(self) -> bool:
        qualities = {}
        for value in self.headers.get("Accept-Encoding", "").lower().split(","):
            parts = [p.strip() for p in value.split(";")]
            quality = 1.0
            for parameter in parts[1:]:
                if parameter.startswith("q="):
                    try:
                        quality = float(parameter[2:])
                    except ValueError:
                        quality = 0.0
            qualities[parts[0]] = quality
        return qualities.get("gzip", qualities.get("*", 0)) > 0

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        compressed = self._gzip_accepted() and len(body) >= 512
        if compressed:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _file(self, path: str) -> None:
        rel = unquote(path).lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        f = (DIST / rel).resolve()
        if not f.is_relative_to(DIST.resolve()):
            self._json({"error": "forbidden"}, 403)
            return
        if not f.is_file() and "." not in f.name:
            f = DIST / (rel + ".html")   # 无扩展名路径回退 .html（如 /rankings/unified）
        if not f.is_file():
            self._json({"error": "not found", "path": path}, 404)
            return
        ext = f.suffix.lower()
        compressed_file = f.with_name(f.name + ".gz")
        compressed = (self._gzip_accepted() and compressed_file.is_file()
                      and compressed_file.stat().st_mtime_ns >= f.stat().st_mtime_ns)
        representation = compressed_file if compressed else f
        stat = representation.stat()
        etag = f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}-{"gz" if compressed else "raw"}"'
        query = parse_qs(urlparse(self.path).query)
        versioned = path.startswith("/assets/") and ("v" in query or f.name == "mermaid-11.12.0.min.js")
        cache = "public, max-age=31536000, immutable" if versioned else (
            "no-cache" if ext in (".html", ".json") else "public, max-age=3600")
        candidates = [v.strip() for v in self.headers.get("If-None-Match", "").split(",")]
        not_modified = etag in candidates or "*" in candidates
        self.send_response(304 if not_modified else 200)
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(f.stat().st_mtime, usegmt=True))
        if not not_modified:
            self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(stat.st_size))
            if compressed:
                self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if not not_modified and self.command != "HEAD":
            try:
                self.wfile.write(representation.read_bytes())
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser cancelled a superseded request.

    # ---- HTTP methods ----
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/v1/knowledge/search":
            params = parse_qs(urlparse(self.path).query)
            try:
                offset = int(params.get("offset", ["0"])[0])
                limit = int(params.get("limit", ["40"])[0])
            except ValueError:
                self._json({"error": "invalid pagination"}, 400)
                return
            self._json(kbsearch.search(params.get("q", [""])[0],
                params.get("group", [""])[0], params.get("path", [""])[0], offset, limit))
            return
        if path == "/api/v1/update/status":
            self._json(status_payload())
            return
        if path.startswith("/api/v1/update"):
            self._json({"error": "use POST to trigger update"}, 405)
            return
        self._file(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/v1/update", "/api/v1/update/news"):
            self._json({"error": "not found"}, 404)
            return
        supplied = self.headers.get("X-Update-Password", "")
        if not password_valid(supplied):
            self._json({"error": "更新密码错误或未填写"}, 401)
            return
        with _lock:
            p = _proc.get("running")
            if p is not None and p.poll() is None:
                self._json(status_payload() | {"note": "已有更新进行中"}, 409)
                return
            log_dir = ROOT / "data" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log = log_dir / f"update-{time.strftime('%Y%m%d-%H%M%S')}.log"
            cmd = ["-m", "collector",
                   "--collect-news" if parsed.path.endswith("/news") else "--update"]
            proc = subprocess.Popen(
                [sys.executable, *cmd],
                cwd=str(ROOT), stdout=open(log, "w"), stderr=subprocess.STDOUT)
            _proc["running"] = proc
        self._json({"state": "running", "log": str(log)}, 202)


def main() -> None:
    ap = argparse.ArgumentParser(description="智能体热度指数本地服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    if not DIST.is_dir():
        print("dist/ 不存在，先执行一次构建：.venv/bin/python -m collector --update")
        sys.exit(1)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"服务已启动: http://{args.host}:{args.port}  （Ctrl+C 停止）")
    print("触发更新: POST /api/v1/update（需 X-Update-Password）|  状态: GET /api/v1/update/status")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
