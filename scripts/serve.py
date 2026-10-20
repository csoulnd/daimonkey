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
    POST /api/v1/update/knowledge         -> 同步知识库代码仓并重建（子进程 scripts/sync_knowledge.py）
    智能助手（账号登录，签名 Cookie 携带用户名）：
    GET/POST /api/v1/assistant/session|login|logout、GET sessions|sessions/{id}/messages|
    status|usage|workflows、POST chat|workflows/{name}、DELETE sessions/{id}
    云剪贴板（工作台：两设备间文本同步 + 小云盘，全部需登录）：
    GET /api/v1/clipboard/items|usage、GET /api/v1/clipboard/files/{id}（下载）、
    POST /api/v1/clipboard/items（文本）|files（raw body 上传，文件名在 X-Filename 头）、
    DELETE /api/v1/clipboard/items/{id}

站点浏览（GET）无需鉴权。登录与更新触发统一走账号体系：
    账号由 scripts/userctl.py 在服务器上手动创建，全部账号等权（管理员）；
    Cookie 值 = {username}.{expires}.{HMAC(secret, username.expires)}，7 天有效。
    过渡：尚无任何账号时，仅密码登录沿用旧 data/update_password.sha256（用户名 legacy）；
    建号后旧密码路径自动失效。X-Update-Password 头路径已移除（防爆破绕过锁定）。
"""
from __future__ import annotations

import argparse
import hashlib
import gzip
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, quote
from email.utils import formatdate

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector import assistant, clipboard, kbsearch, users
DIST = ROOT / "dist"
STATUS_FILE = ROOT / "data" / "update_status.json"
PASSWORD_FILE = ROOT / "data" / "update_password.sha256"   # 仅过渡期使用
ASSISTANT_SECRET_FILE = ROOT / "data" / "assistant_secret"
ASSISTANT_COOKIE = "assistant_session"
ASSISTANT_COOKIE_MAX_AGE = 7 * 86400


def password_valid(supplied: str) -> bool:
    try:
        expected = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    actual = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    return bool(supplied) and secrets.compare_digest(actual, expected)

# 旧信息架构 -> 三方向频道（301 永久跳转，保留外链与书签）
LEGACY_REDIRECTS = {
    # 全站新闻页已下线(2026-10-19):新闻按方向聚合到 /dir/*
    "/news": "/",
    "/rankings": "/dir/harness",
    "/rankings/unified": "/dir/harness",   # 联合榜并入核心产品榜
    "/rankings/coding": "/dir/harness",    # 分类源榜不再单独展示
    "/rankings/general": "/dir/harness",
    "/rankings/claw": "/dir/harness",
    "/rankings/framework": "/rankings/harness_eco",
}

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
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_lock = threading.Lock()
_proc: dict = {"running": None, "kind": None}

# ---- 规格化防护(规格:最多 10 并发访问 / 20 人在线,单机 3.3GB) ----
# 上限按规格取 3 倍余量:静态 120 req/min/IP、API 60 req/min/IP、全局在途 30。
# 规格变更时同步调整,并更新 skills/load-optimization。

STATIC_RATE_PER_MIN = 120
API_RATE_PER_MIN = 60
MAX_INFLIGHT = 30
_RATE_MAX_IPS = 1024          # 未知来源过多时整体清空,防内存膨胀


class _TokenBucket:
    __slots__ = ("capacity", "rate", "tokens", "updated")

    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self.tokens = float(per_minute)
        self.updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity,
                           self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


_rate_buckets: dict[str, _TokenBucket] = {}
_rate_lock = threading.Lock()
_inflight = {"count": 0}
_inflight_lock = threading.Lock()


UPDATE_HARD_TIMEOUT_S = 25 * 60


def _update_watchdog(proc, kind: str) -> None:
    """更新子进程看门狗:超时杀进程组;退出后若状态仍卡 running 则落 failed。"""
    import subprocess as _sp
    import json as _json
    try:
        proc.wait(timeout=UPDATE_HARD_TIMEOUT_S)
    except _sp.TimeoutExpired:
        try:
            subprocess.run(["kill", "-9", f"-{proc.pid}"], check=False,
                           capture_output=True)
            proc.kill()
        except OSError:
            pass
        STATUS_FILE.write_text(_json.dumps(
            {"state": "failed", "time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "mode": kind, "error": f"更新超时(>{UPDATE_HARD_TIMEOUT_S // 60}分钟)被看门狗终止"},
            ensure_ascii=False), encoding="utf-8")
        sys.stderr.write(f"WATCHDOG killed stalled update kind={kind}\n")
        return
    # 正常退出但状态文件仍停在 running(子进程崩溃前没写终态)→ 落 failed
    try:
        st = _json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if st.get("state") == "running":
            st.update(state="failed", error="更新进程异常退出(未写终态)")
            STATUS_FILE.write_text(_json.dumps(st, ensure_ascii=False),
                                   encoding="utf-8")
            sys.stderr.write(f"WATCHDOG flagged dead update kind={kind}\n")
    except Exception:
        pass


def status_payload() -> dict:
    p = _proc.get("running")
    if p is not None and p.poll() is None:
        kind = _proc.get("kind")
        note = {"knowledge": "知识库同步中，通常需要 10~60 秒",
                "news": "新闻采集中，通常需要约 1 分钟"}.get(
                    kind, "采集进行中，通常需要 1-5 分钟")
        return {"state": "running", "kind": kind, "note": note}
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"state": "never", "note": "尚未执行过更新"}


# ---- 智能助手：账号登录与任务状态 ----

_login_guard: dict[str, dict] = {}     # username -> {failures, locked_until}
_login_lock = threading.Lock()
_GUARD_MAX_KEYS = 256                  # 防未知用户名撑爆内存
_chat_lock = threading.Lock()
_chat_state: dict = {"state": "idle", "error": None, "session_id": None,
                     "workflow": None, "started_at": None, "reply": None}


def _guard_entry(username: str) -> dict:
    entry = _login_guard.get(username)
    if entry is None:
        if len(_login_guard) >= _GUARD_MAX_KEYS:
            _login_guard.clear()
        entry = {"failures": 0, "locked_until": 0.0}
        _login_guard[username] = entry
    return entry


def _assistant_secret() -> bytes:
    """签名密钥：首用自动生成，600 权限，落盘 data/assistant_secret。"""
    try:
        data = ASSISTANT_SECRET_FILE.read_text(encoding="utf-8").strip()
        if data:
            return bytes.fromhex(data)
    except (OSError, ValueError):
        pass
    secret = secrets.token_bytes(32)
    ASSISTANT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    ASSISTANT_SECRET_FILE.write_text(secret.hex(), encoding="utf-8")
    try:
        os.chmod(ASSISTANT_SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


def _sign_session(username: str, expires: int) -> str:
    payload = f"{username}.{expires}"
    return hmac.new(_assistant_secret(), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def make_cookie_value(username: str) -> str:
    expires = int(time.time()) + ASSISTANT_COOKIE_MAX_AGE
    return f"{username}.{expires}.{_sign_session(username, expires)}"


def session_user(value: str | None) -> str | None:
    """校验 Cookie 值,合法返回用户名,否则 None。"""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    username, expires_str, sig = parts
    if not username or not expires_str.isdigit() or not sig:
        return None
    expires = int(expires_str)
    if expires < time.time():
        return None
    if not secrets.compare_digest(_sign_session(username, expires), sig):
        return None
    return username


def _chat_task_worker(session_id, message: str, workflow: str | None,
                      payload: dict) -> None:
    with _chat_lock:
        _chat_state.update(state="running", error=None, session_id=session_id,
                           workflow=workflow, started_at=time.time(), reply=None)
    try:
        if workflow:
            result = assistant.run_internal(workflow, payload, channel="interactive")
            _chat_state["reply"] = result["reply"]  # 工作流结果直接回显到对话面板
        else:
            assistant.run_chat(session_id, message)
        _chat_state["state"] = "success"
    except Exception as e:  # 状态机兜底：任何异常都落到 failed，界面可读
        _chat_state.update(state="failed",
                           error=str(e) if isinstance(e, assistant.AssistantError)
                           else f"{type(e).__name__}: {e}")


def _start_chat_task(session_id, message: str, workflow: str | None,
                     payload: dict) -> bool:
    """占用执行位并起后台线程；已被占用返回 False（调用方回 409）。"""
    with _chat_lock:
        if _chat_state["state"] == "running":
            return False
        _chat_state["state"] = "running"  # 先占位，worker 再补全字段
    threading.Thread(target=_chat_task_worker,
                     args=(session_id, message, workflow, payload),
                     daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "heat-index/0.1"
    timeout = 30  # 慢速连接读超时,防线程被长期占用

    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- 准入控制:null 字节拒绝 + 按 IP 分类限流 + 全局在途上限 ----
    def _admit(self) -> bool:
        """请求准入;拒绝时已写好响应并返回 False。"""
        self._admitted = False
        self._mark_t0()
        path = urlparse(self.path).path
        # 空字节(urlencode 后的 %00 在 unquote 才变 \x00,此处两者都拦,
        # 否则 Path.resolve 会抛 ValueError 裸崩连接)
        if "\x00" in path or "%00" in path:
            self._json({"error": "bad path"}, 400)
            return False
        kind = "api" if path.startswith("/api/") else "static"
        key = f"{kind}:{self.client_address[0]}"
        with _rate_lock:
            bucket = _rate_buckets.get(key)
            if bucket is None:
                if len(_rate_buckets) >= _RATE_MAX_IPS:
                    _rate_buckets.clear()
                bucket = _TokenBucket(API_RATE_PER_MIN if kind == "api"
                                      else STATIC_RATE_PER_MIN)
                _rate_buckets[key] = bucket
            ok = bucket.allow()
        if not ok:
            self._json({"error": "请求过于频繁,请稍后再试"},
                       429, extra_headers=[("Retry-After", "5")])
            return False
        with _inflight_lock:
            _inflight["count"] += 1
            crowded = _inflight["count"] > MAX_INFLIGHT
        if crowded:
            self._release()
            self._json({"error": "服务器繁忙,请稍后再试"},
                       503, extra_headers=[("Retry-After", "2")])
            return False
        self._admitted = True
        return True

    def _release(self) -> None:
        with _inflight_lock:
            _inflight["count"] = max(0, _inflight["count"] - 1)
        elapsed = time.monotonic() - getattr(self, "_t0", time.monotonic())
        if elapsed > 5.0:   # 慢请求留痕,供稳定性诊断
            sys.stderr.write(f"SLOW {elapsed:.1f}s {self.command} {self.path}\n")

    def _mark_t0(self) -> None:
        self._t0 = time.monotonic()

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

    def _json(self, obj, code: int = 200,
              extra_headers: list[tuple[str, str]] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        compressed = self._gzip_accepted() and len(body) >= 512
        if compressed:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _read_json(self) -> dict | None:
        """读取 JSON 请求体（≤64KB）；解析失败返回 None。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length <= 0 or length > 65536:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _assistant_user(self) -> str | None:
        """从 Cookie 解出登录用户名;未登录返回 None。"""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == ASSISTANT_COOKIE:
                user = session_user(value)
                # 签名有效还不够：账号可能已删除/禁用，必须回库确认
                if user and users.is_active(user):
                    return user
        return None

    def _redirect(self, location: str) -> None:
        """旧路由 301 跳转；响应体仅作无 Location 支持时的兜底。"""
        body = (f'<!doctype html><meta charset="utf-8">'
                f'<meta http-equiv="refresh" content="0;url={location}">'
                f'<a href="{location}">{location}</a>').encode("utf-8")
        self.send_response(301)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=3600")
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
        self.send_header("X-Content-Type-Options", "nosniff")
        if ext == ".html":
            # 基础 CSP:脚本/样式仅限同源(内联为模板预载与报告自带样式暂留);
            # 图片放行 https(知识库笔记引用外部图床),frame 嵌套仅限同源
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: https:;"
                             " style-src 'self' 'unsafe-inline';"
                             " script-src 'self' 'unsafe-inline';"
                             " frame-ancestors 'self'; object-src 'none'")
        if getattr(self, "_noindex", False):
            self.send_header("X-Robots-Tag", "noindex")
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
        try:
            if not self._admit():
                return
            path = urlparse(self.path).path
            target = LEGACY_REDIRECTS.get(path)
            if target is None and path.startswith("/news/"):
                target = "/"
            if target:
                self._redirect(target)
                return
            if path == "/api/v1/knowledge/search":
                if not self._assistant_user():
                    self._json({"error": "知识库内容登录后可见"}, 401)
                    return
                params = parse_qs(urlparse(self.path).query)
                try:
                    offset = int(params.get("offset", ["0"])[0])
                    limit = int(params.get("limit", ["40"])[0])
                except ValueError:
                    self._json({"error": "invalid pagination"}, 400)
                    return
                user = self._assistant_user() or ""
                kb_file_user = user if users.get_knowledge_repo(user) else ""
                self._json(kbsearch.search(params.get("q", [""])[0],
                    params.get("group", [""])[0], params.get("path", [""])[0],
                    offset, limit, user=kb_file_user))
                return
            if path == "/api/v1/update/status":
                self._json(status_payload())
                return
            if path in ("/api/v1/update", "/api/v1/update/news",
                        "/api/v1/update/knowledge"):
                self._json({"error": "use POST to trigger update"}, 405)
                return
            if any(path == p or path.startswith(p + "/")
                   for p in ("/notes", "/reports", "/knowledge", "/clipboard")):
                # 讲稿/报告厅/知识库/剪贴板:仅登录账号可见(响应带 noindex)
                user = self._assistant_user()
                if not user:
                    self._login_denied(path)
                    return
                if path == "/knowledge" or path.startswith("/knowledge/"):
                    # 用户隔离:自配仓库且有独立构建时,改读该用户的页面
                    if (users.get_knowledge_repo(user)
                            and (DIST / f"knowledge-{user}" / "index.html").is_file()):
                        path = path.replace("/knowledge", f"/knowledge-{user}", 1)
                self._noindex = True
                self._file(path)
                return
            if self._assistant_get(path):
                return
            if self._clipboard_get(path):
                return
            self._file(path)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端已断开
        except Exception as e:  # 外层兜底:任何异常都返回 500,不让连接裸崩
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass
        finally:
            if getattr(self, "_admitted", False):
                self._release()

    def _login_denied(self, path: str) -> None:
        from urllib.parse import quote
        body = ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
                "<meta name=\"robots\" content=\"noindex\">"
                "<title>需要登录</title>"
                "<body style=\"font-family:system-ui,sans-serif;max-width:520px;"
                "margin:14vh auto;padding:0 24px;line-height:1.8;color:#556\">"
                "<h2>需要登录</h2>"
                "<p>此内容(报告厅 / 知识库 / 讲稿 / 剪贴板)仅登录账号可见,站点其余页面匿名可浏览。</p>"
                f"<p><a href=\"/login?next={quote(path)}\" "
                "style=\"padding:8px 18px;background:#7c8cff;color:#fff;"
                "border-radius:8px;text-decoration:none\">前往登录</a></p>"
                "<p><a href=\"/\">← 返回首页</a></p></body></html>").encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _assistant_get(self, path: str) -> bool:
        """助手 GET 路由；已处理返回 True。/session 公开，其余需登录。"""
        if not path.startswith("/api/v1/assistant/"):
            return False
        rest = path[len("/api/v1/assistant/"):]
        if rest == "session":
            user = self._assistant_user()
            role = users.get_role(user) if user else None
            self._json({"authed": user is not None, "user": user,
                        "role": role,
                        "ops_enabled": assistant.ops_enabled()})
            return True
        user = self._assistant_user()
        if not user:
            self._json({"error": "未登录"}, 401)
            return True
        try:
            if rest == "knowledge-repo":
                self._json({"repo": users.get_knowledge_repo(user),
                            "default": "https://github.com/csoulnd/InferTechNote"})
            elif rest == "sessions":
                self._json({"sessions": assistant.list_sessions(owner=user)})
            elif rest == "usage":
                self._json({"usage": assistant.usage_summary(),
                            "user": user,
                            "limits": {k: v for k, v in assistant.cfg().items()
                                       if k in ("interactive", "internal", "ops", "execution")}})
            elif rest == "status":
                with _chat_lock:
                    state = dict(_chat_state)
                state.pop("started_at", None)
                self._json(state | {"usage": assistant.usage_summary()})
            elif rest == "workflows":
                from collector import workflows
                self._json({"workflows": workflows.describe()})
            elif rest.startswith("ops/runs/"):
                # 运维 run 轮询:?after=N 只回增量事件
                rid = rest[len("ops/runs/"):]
                if "/" in rid or not rid:
                    self._json({"error": "bad run id"}, 400)
                    return True
                params = parse_qs(urlparse(self.path).query)
                try:
                    after = int(params.get("after", ["0"])[0])
                except ValueError:
                    after = 0
                state = assistant.run_status(rid, owner=user)
                if state is None:
                    self._json({"error": "run 不存在或已过期"}, 404)
                else:
                    state["events"] = [e for e in state["events"]
                                       if int(e.get("seq", 0)) > after]
                    self._json(state)
            elif rest.endswith("/messages") and rest.startswith("sessions/"):
                sid = rest[len("sessions/"):-len("/messages")]
                if sid.isdigit():
                    self._json({"messages": assistant.session_messages(
                        int(sid), owner=user)})
                else:
                    self._json({"error": "bad session id"}, 400)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return True

    # ---- 云剪贴板（工作台：两设备间文本同步 + 小云盘，见 skills/workspace） ----

    def _clipboard_get(self, path: str) -> bool:
        """剪贴板 GET 路由；已处理返回 True。全部需登录。"""
        if not path.startswith("/api/v1/clipboard/"):
            return False
        rest = path[len("/api/v1/clipboard/"):]
        user = self._assistant_user()
        if not user:
            self._json({"error": "未登录"}, 401)
            return True
        try:
            if rest == "items":
                params = parse_qs(urlparse(self.path).query)
                try:
                    limit = int(params.get("limit", ["100"])[0])
                except ValueError:
                    self._json({"error": "invalid limit"}, 400)
                    return True
                self._json({"items": clipboard.list_items(
                    user, max(1, min(limit, 200)))})
            elif rest == "usage":
                self._json({"usage": clipboard.usage(user), "user": user})
            elif rest.startswith("files/"):
                fid = rest[len("files/"):]
                if not fid.isdigit():
                    self._json({"error": "bad file id"}, 400)
                    return True
                item = clipboard.get_file(user, int(fid))
                if item is None:
                    self._json({"error": "文件不存在"}, 404)
                    return True
                self._send_clipboard_file(item)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return True

    def _send_clipboard_file(self, item: dict) -> None:
        """受登录保护的文件下载；分块流出，路径穿越已在 get_file 校验。"""
        f = item["path"]
        size = f.stat().st_size
        name = item.get("file_name") or "file"
        self.send_response(200)
        ctype = item.get("content_type") or "application/octet-stream"
        self.send_header("Content-Type", ctype[:100])
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Robots-Tag", "noindex")
        # RFC 5987：中文文件名走 filename*，ASCII 部分另给兜底
        ascii_name = name.encode("ascii", "ignore").decode("ascii") or "file"
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; "
                         f"filename*=UTF-8''{quote(name)}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端取消了下载

    def _clipboard_post(self, path: str) -> bool:
        """剪贴板 POST 路由；已处理返回 True。全部需登录。"""
        if not path.startswith("/api/v1/clipboard/"):
            return False
        rest = path[len("/api/v1/clipboard/"):]
        user = self._assistant_user()
        if not user:
            self._json({"error": "未登录"}, 401)
            return True
        try:
            if rest == "items":
                body = self._read_json() or {}
                item = clipboard.add_text(user, str(body.get("content", "")))
                self._json({"item": item}, 201)
            elif rest == "files":
                self._clipboard_upload(user)
            else:
                self._json({"error": "not found"}, 404)
        except clipboard.ClipboardTooLarge as e:
            self._json({"error": str(e)}, 413)
        except clipboard.ClipboardQuota as e:
            self._json({"error": str(e)}, 507)
        except clipboard.ClipboardError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return True

    def _clipboard_upload(self, user: str) -> None:
        """raw body 上传：Content-Length 必填；文件名在 X-Filename 头（URL 编码）。"""
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            size = -1
        if size <= 0:
            self._json({"error": "文件为空或缺 Content-Length"}, 400)
            return
        name = unquote(self.headers.get("X-Filename", "") or "file")
        ctype = (self.headers.get("Content-Type") or "")[:100]
        item = clipboard.add_file(user, name, size, self.rfile.read, ctype)
        self._json({"item": item}, 201)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        try:
            if not self._admit():
                return
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/v1/assistant/"):
                self._assistant_post(parsed.path)
                return
            if self._clipboard_post(parsed.path):
                return
            if parsed.path not in ("/api/v1/update", "/api/v1/update/news",
                                   "/api/v1/update/knowledge"):
                self._json({"error": "not found"}, 404)
                return
            # 登录账号完全权限(全部账号等权);不再接受 X-Update-Password 头,
            # 登录端点的失败锁定因此对更新入口同样生效(防爆破绕过)。
            user = self._assistant_user()
            if not user:
                self._json({"error": "未登录,请先通过助手面板登录账号"}, 401)
                return
            if parsed.path.endswith("/knowledge"):
                kind = "knowledge"
            elif parsed.path.endswith("/news"):
                kind = "news"
            else:
                kind = "full"
            with _lock:
                p = _proc.get("running")
                if p is not None and p.poll() is None:
                    self._json(status_payload() | {"note": "已有更新进行中"}, 409)
                    return
                log_dir = ROOT / "data" / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log = log_dir / f"update-{time.strftime('%Y%m%d-%H%M%S')}.log"
                if kind == "knowledge":
                    cmd = [str(ROOT / "scripts" / "sync_knowledge.py")]
                    kb_user = self._assistant_user()
                    if kb_user and users.get_knowledge_repo(kb_user):
                        cmd += ["--user", kb_user]
                else:
                    cmd = ["-m", "collector",
                           "--collect-news" if kind == "news" else "--update"]
                with open(log, "w") as log_fh:
                    proc = subprocess.Popen(
                        [sys.executable, *cmd],
                        cwd=str(ROOT), stdout=log_fh, stderr=subprocess.STDOUT)
                _proc["running"] = proc
                _proc["kind"] = kind
                threading.Thread(target=_update_watchdog, args=(proc, kind),
                                 daemon=True).start()
            self._json({"state": "running", "kind": kind, "log": str(log)}, 202)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass
        finally:
            if getattr(self, "_admitted", False):
                self._release()

    def _assistant_post(self, path: str) -> None:
        rest = path[len("/api/v1/assistant/"):]
        conf = assistant.cfg()["login"]

        if rest == "login":
            body = self._read_json() or {}
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            if not username:
                # 过渡:尚无任何账号时允许仅密码登录(用户名 legacy);
                # 建号后旧单一密码自动失效。
                username = "legacy"
                ok = (not users.has_any_user()) and password_valid(password)
            else:
                ok = users.authenticate(username, password)
            with _login_lock:  # 锁内只做计数判定与更新,不做 IO
                entry = _guard_entry(username)
                if time.monotonic() < entry["locked_until"]:
                    self._json({"error": "尝试次数过多，已临时锁定，请稍后再试"}, 429)
                    return
                if not ok:
                    entry["failures"] += 1
                    if entry["failures"] >= conf["max_failures"]:
                        entry["locked_until"] = (
                            time.monotonic() + conf["lockout_minutes"] * 60)
                        entry["failures"] = 0
                    self._json({"error": "用户名或密码错误"}, 401)
                    return
                entry.update(failures=0, locked_until=0.0)
            cookie = (f"{ASSISTANT_COOKIE}={make_cookie_value(username)}; Path=/;"
                      f" Max-Age={ASSISTANT_COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict")
            self._json({"ok": True, "user": username},
                       extra_headers=[("Set-Cookie", cookie)])
            return

        if rest == "knowledge-repo":
            user_repo = self._assistant_user()
            if not user_repo:
                self._json({"error": "未登录"}, 401)
                return
            if self.command == "GET":
                self._json({"repo": users.get_knowledge_repo(user_repo),
                            "default": "https://github.com/csoulnd/InferTechNote"})
                return
            body = self._read_json() or {}
            repo = str(body.get("repo", "")).strip()
            if repo and not repo.startswith("https://github.com/"):
                self._json({"error": "仅支持 https://github.com/ 开头的仓库地址"}, 400)
                return
            if not users.set_knowledge_repo(user_repo, repo or None):
                self._json({"error": "保存失败"}, 500)
                return
            self._json({"ok": True,
                        "repo": users.get_knowledge_repo(user_repo),
                        "note": "已保存;点击「同步我的知识库」生成页面"})

        if rest == "logout":
            cookie = f"{ASSISTANT_COOKIE}=; Path=/; Max-Age=0;"
            cookie += " HttpOnly; SameSite=Strict"
            self._json({"ok": True}, extra_headers=[("Set-Cookie", cookie)])
            return

        user = self._assistant_user()
        if not user:
            self._json({"error": "未登录"}, 401)
            return
        try:
            if rest == "sessions":
                body = self._read_json() or {}
                mode = str(body.get("mode", "chat"))
                if mode == "ops":
                    if not users.is_admin(user):
                        self._json({"error": "仅管理员可创建运维会话"}, 403)
                        return
                    if not assistant.ops_enabled():
                        self._json({"error": "运维模式未启用"}, 403)
                        return
                sid = assistant.create_session(str(body.get("title", "")),
                                               owner=user, mode=mode)
                self._json({"session_id": sid, "mode": "ops" if mode == "ops" else "chat"}, 201)
            elif rest.startswith("sessions/") and rest.endswith("/rename"):
                sid = rest[len("sessions/"):-len("/rename")]
                if not sid.isdigit():
                    self._json({"error": "bad session id"}, 400)
                    return
                body = self._read_json() or {}
                ok = assistant.rename_session(int(sid), str(body.get("title", "")),
                                              owner=user)
                self._json({"ok": bool(ok)} if ok else {"error": "会话不存在或标题为空"},
                           200 if ok else 404)
            elif rest.startswith("sessions/") and rest.endswith("/mode"):
                sid = rest[len("sessions/"):-len("/mode")]
                if not sid.isdigit():
                    self._json({"error": "bad session id"}, 400)
                    return
                body = self._read_json() or {}
                mode = str(body.get("mode", "chat"))
                if mode not in ("chat", "ops"):
                    self._json({"error": "mode 只能是 chat 或 ops"}, 400)
                    return
                if mode == "ops":
                    if not users.is_admin(user):
                        self._json({"error": "仅管理员可切换运维模式"}, 403)
                        return
                    if not assistant.ops_enabled():
                        self._json({"error": "运维模式未启用"}, 403)
                        return
                ok = assistant.set_session_mode(int(sid), mode, owner=user)
                self._json({"ok": True, "mode": mode} if ok
                           else {"error": "会话不存在"}, 200 if ok else 404)
            elif rest == "chat":
                body = self._read_json() or {}
                sid, message = body.get("session_id"), str(body.get("message", ""))
                if not isinstance(sid, int):
                    self._json({"error": "session_id 必填"}, 400)
                    return
                if not message.strip():
                    self._json({"error": "message 不能为空"}, 400)
                    return
                if not assistant.session_owned(sid, user):
                    self._json({"error": "会话不存在"}, 404)
                    return
                assistant.check_quota("interactive")  # 提前拒绝，避免空跑
                if _start_chat_task(sid, message, None, {}):
                    self._json({"state": "running"}, 202)
                else:
                    self._json({"state": "running", "note": "已有一个任务进行中"}, 409)
            elif rest.startswith("workflows/"):
                name = rest[len("workflows/"):]
                payload = self._read_json() or {}
                assistant.check_quota("interactive")
                if _start_chat_task(None, "", name, payload):
                    self._json({"state": "running", "workflow": name}, 202)
                else:
                    self._json({"state": "running", "note": "已有一个任务进行中"}, 409)
            elif rest == "ops/chat":
                if not assistant.ops_enabled():
                    self._json({"error": "运维模式未启用"}, 403)
                    return
                if not users.is_admin(user):
                    self._json({"error": "仅管理员可使用运维模式"}, 403)
                    return
                body = self._read_json() or {}
                sid = body.get("session_id")
                message = str(body.get("message", ""))
                if not isinstance(sid, int):
                    self._json({"error": "session_id 必填"}, 400)
                    return
                if not message.strip():
                    self._json({"error": "message 不能为空"}, 400)
                    return
                if not assistant.session_owned(sid, user):
                    self._json({"error": "会话不存在"}, 404)
                    return
                run_id = assistant.start_ops_turn(sid, message, user)
                self._json({"run_id": run_id, "state": "running"}, 202)
            elif rest.startswith("ops/runs/"):
                tail = rest[len("ops/runs/"):]
                parts = tail.split("/")
                rid = parts[0]
                if len(parts) == 2 and parts[1] == "abort":
                    ok = assistant.abort_run(rid, owner=user)
                    self._json({"ok": True} if ok else {"error": "run 不存在"}, 200 if ok else 404)
                elif len(parts) == 3 and parts[1] == "approvals":
                    body = self._read_json() or {}
                    ok = assistant.resolve_approval(rid, parts[2],
                                                    bool(body.get("allow")), owner=user)
                    self._json({"ok": True} if ok else {"error": "审批不存在或已失效"},
                               200 if ok else 404)
                else:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)
        except assistant.AssistantPermission as e:
            self._json({"error": str(e)}, 403)
        except assistant.AssistantBusy as e:
            self._json({"error": str(e)}, 409)
        except assistant.AssistantQuota as e:
            self._json({"error": str(e)}, 429)
        except assistant.AssistantError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_DELETE(self) -> None:
        try:
            if not self._admit():
                return
            path = urlparse(self.path).path
            cb_prefix = "/api/v1/clipboard/items/"
            if path.startswith(cb_prefix):
                user = self._assistant_user()
                if not user:
                    self._json({"error": "未登录"}, 401)
                    return
                cid = path[len(cb_prefix):]
                if not cid.isdigit():
                    self._json({"error": "bad id"}, 400)
                    return
                ok = clipboard.delete_item(user, int(cid))
                self._json({"ok": bool(ok)}, 200 if ok else 404)
                return
            prefix = "/api/v1/assistant/sessions/"
            if not path.startswith(prefix) or not self._assistant_user():
                if path.startswith("/api/v1/assistant/"):
                    if not self._assistant_user():
                        self._json({"error": "未登录"}, 401)
                    else:
                        self._json({"error": "not found"}, 404)
                else:
                    self._json({"error": "not found"}, 404)
                return
            user = self._assistant_user()
            sid = path[len(prefix):]
            if not sid.isdigit():
                self._json({"error": "bad session id"}, 400)
                return
            try:
                ok = assistant.delete_session(int(sid), owner=user)
                self._json({"ok": bool(ok)}, 200 if ok else 404)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass
        finally:
            if getattr(self, "_admitted", False):
                self._release()


def main() -> None:
    ap = argparse.ArgumentParser(description="InferTech 观察站本地服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    if not DIST.is_dir():
        print("dist/ 不存在，先执行一次构建：.venv/bin/python -m collector --update")
        sys.exit(1)
    try:
        from collector import db
        with db.conn() as c:
            db.ensure_assistant_tables(c)
            db.ensure_clipboard_tables(c)
    except Exception as e:  # 助手表初始化失败不阻断静态站点服务
        print(f"警告: 助手表初始化失败（{e}），助手接口可能不可用")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"服务已启动: http://{args.host}:{args.port}  （Ctrl+C 停止）")
    print("触发更新: POST /api/v1/update（需登录账号 Cookie）|  状态: GET /api/v1/update/status")
    print("智能助手: 全站 🐱 面板 |  账号管理: scripts/userctl.py")
    try:
        if not users.has_any_user():
            print("提示: 尚未创建任何账号,登录暂用旧单一密码过渡(用户名留空)。建议尽快执行:")
            print("      userctl add <用户名>   (任意目录;等价 .venv/bin/python scripts/userctl.py add)")
    except Exception as e:
        print(f"警告: 账号检查失败（{e}）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
