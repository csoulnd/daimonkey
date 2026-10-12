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
    智能助手（单用户登录，签名 Cookie）：
    GET/POST /api/v1/assistant/session|login|logout、GET sessions|sessions/{id}/messages|
    status|usage|workflows、POST chat|workflows/{name}、DELETE sessions/{id}

站点浏览（GET）无需鉴权；更新接口（POST）通过 X-Update-Password 验证密码。
密码的 SHA-256 校验值保存在 data/update_password.sha256，不接受 URL 令牌。
助手登录复用同一密码文件，签发 HMAC 签名 Cookie（密钥 data/assistant_secret）。
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
from urllib.parse import unquote, urlparse, parse_qs
from email.utils import formatdate

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector import assistant, kbsearch
DIST = ROOT / "dist"
STATUS_FILE = ROOT / "data" / "update_status.json"
PASSWORD_FILE = ROOT / "data" / "update_password.sha256"
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


# ---- 智能助手：单用户登录与任务状态 ----

_login_guard: dict = {"failures": 0, "locked_until": 0.0}
_login_lock = threading.Lock()
_chat_lock = threading.Lock()
_chat_state: dict = {"state": "idle", "error": None, "session_id": None,
                     "workflow": None, "started_at": None, "reply": None}


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


def _sign_session(expires: int) -> str:
    return hmac.new(_assistant_secret(), str(expires).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def make_cookie_value() -> str:
    expires = int(time.time()) + ASSISTANT_COOKIE_MAX_AGE
    return f"{expires}.{_sign_session(expires)}"


def session_cookie_valid(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    expires_str, _, sig = value.partition(".")
    if not expires_str.isdigit() or not sig:
        return False
    expires = int(expires_str)
    if expires < time.time():
        return False
    return secrets.compare_digest(_sign_session(expires), sig)


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

    def _assistant_authed(self) -> bool:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == ASSISTANT_COOKIE and session_cookie_valid(value):
                return True
        return False

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
        if self._assistant_get(path):
            return
        self._file(path)

    def _assistant_get(self, path: str) -> bool:
        """助手 GET 路由；已处理返回 True。/session 公开，其余需登录。"""
        if not path.startswith("/api/v1/assistant/"):
            return False
        rest = path[len("/api/v1/assistant/"):]
        if rest == "session":
            self._json({"authed": self._assistant_authed()})
            return True
        if not self._assistant_authed():
            self._json({"error": "未登录"}, 401)
            return True
        try:
            if rest == "sessions":
                self._json({"sessions": assistant.list_sessions()})
            elif rest == "usage":
                self._json({"usage": assistant.usage_summary(),
                            "limits": {k: v for k, v in assistant.cfg().items()
                                       if k in ("interactive", "internal", "execution")}})
            elif rest == "status":
                with _chat_lock:
                    state = dict(_chat_state)
                state.pop("started_at", None)
                self._json(state | {"usage": assistant.usage_summary()})
            elif rest == "workflows":
                from collector import workflows
                self._json({"workflows": workflows.describe()})
            elif rest.endswith("/messages") and rest.startswith("sessions/"):
                sid = rest[len("sessions/"):-len("/messages")]
                if sid.isdigit():
                    self._json({"messages": assistant.session_messages(int(sid))})
                else:
                    self._json({"error": "bad session id"}, 400)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return True

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/assistant/"):
            self._assistant_post(parsed.path)
            return
        if parsed.path not in ("/api/v1/update", "/api/v1/update/news"):
            self._json({"error": "not found"}, 404)
            return
        # 一次登录完全权限：已持有助手会话 Cookie 或提供正确密码均可触发更新
        supplied = self.headers.get("X-Update-Password", "")
        if not (self._assistant_authed() or password_valid(supplied)):
            self._json({"error": "未登录或更新密码错误"}, 401)
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

    def _assistant_post(self, path: str) -> None:
        rest = path[len("/api/v1/assistant/"):]
        conf = assistant.cfg()["login"]

        if rest == "login":
            with _login_lock:
                if time.monotonic() < _login_guard["locked_until"]:
                    self._json({"error": "尝试次数过多，已临时锁定，请稍后再试"}, 429)
                    return
                body = self._read_json() or {}
                if not password_valid(str(body.get("password", ""))):
                    _login_guard["failures"] += 1
                    if _login_guard["failures"] >= conf["max_failures"]:
                        _login_guard["locked_until"] = (
                            time.monotonic() + conf["lockout_minutes"] * 60)
                        _login_guard["failures"] = 0
                    self._json({"error": "密码错误"}, 401)
                    return
                _login_guard.update(failures=0, locked_until=0.0)
            cookie = (f"{ASSISTANT_COOKIE}={make_cookie_value()}; Path=/;"
                      f" Max-Age={ASSISTANT_COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict")
            self._json({"ok": True}, extra_headers=[("Set-Cookie", cookie)])
            return

        if rest == "logout":
            cookie = (f"{ASSISTANT_COOKIE}=; Path=/; Max-Age=0;"
                      f" HttpOnly; SameSite=Strict")
            self._json({"ok": True}, extra_headers=[("Set-Cookie", cookie)])
            return

        if not self._assistant_authed():
            self._json({"error": "未登录"}, 401)
            return
        try:
            if rest == "sessions":
                body = self._read_json() or {}
                sid = assistant.create_session(str(body.get("title", "")))
                self._json({"session_id": sid}, 201)
            elif rest == "chat":
                body = self._read_json() or {}
                sid, message = body.get("session_id"), str(body.get("message", ""))
                if not isinstance(sid, int):
                    self._json({"error": "session_id 必填"}, 400)
                    return
                if not message.strip():
                    self._json({"error": "message 不能为空"}, 400)
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
            else:
                self._json({"error": "not found"}, 404)
        except assistant.AssistantQuota as e:
            self._json({"error": str(e)}, 429)
        except assistant.AssistantError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/v1/assistant/sessions/"
        if not path.startswith(prefix) or not self._assistant_authed():
            if path.startswith("/api/v1/assistant/"):
                if not self._assistant_authed():
                    self._json({"error": "未登录"}, 401)
                else:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)
            return
        sid = path[len(prefix):]
        if not sid.isdigit():
            self._json({"error": "bad session id"}, 400)
            return
        try:
            ok = assistant.delete_session(int(sid))
            self._json({"ok": bool(ok)}, 200 if ok else 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main() -> None:
    ap = argparse.ArgumentParser(description="智能体热度指数本地服务")
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
    except Exception as e:  # 助手表初始化失败不阻断静态站点服务
        print(f"警告: 助手表初始化失败（{e}），助手接口可能不可用")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"服务已启动: http://{args.host}:{args.port}  （Ctrl+C 停止）")
    print("触发更新: POST /api/v1/update（需 X-Update-Password）|  状态: GET /api/v1/update/status")
    print("智能助手: /assistant 页面（登录复用更新密码）|  状态: GET /api/v1/assistant/status")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
