"""轻量用户系统:服务器上 CLI 手动建号(scripts/userctl.py),匿名可浏览。

模型(2026-10-16 与需求方确认):全部账号等权(管理员)——登录后可触发
数据更新、知识库同步、助手聊天与讲稿页;无普通用户/角色分层。

密码存 pbkdf2_hmac(sha256, 20 万轮)盐渍哈希,格式
`pbkdf2$<iterations>$<salt_hex>$<dk_hex>`,校验恒定时间比较。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from . import db

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_hex, dk_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def valid_username(username: str) -> bool:
    """用户名限 [a-zA-Z0-9_.-],1~32 字符。"""
    return (1 <= len(username) <= 32
            and all(ch.isalnum() or ch in "_.-" for ch in username))


def create_user(username: str, password: str) -> bool:
    """建号;用户名已存在或非法、密码过短返回 False。"""
    username = (username or "").strip()
    if not valid_username(username) or len(password or "") < 8:
        return False
    with db.conn() as c:
        db.ensure_users_tables(c)
        cur = c.execute(
            "INSERT INTO users (username, pw_hash, created_at, enabled)"
            " VALUES (?, ?, ?, 1)"
            " ON CONFLICT(username) DO NOTHING",
            (username, hash_password(password), _now()))
        return cur.rowcount > 0


def set_password(username: str, password: str) -> bool:
    if len(password or "") < 8:
        return False
    with db.conn() as c:
        db.ensure_users_tables(c)
        cur = c.execute("UPDATE users SET pw_hash = ? WHERE username = ?",
                        (hash_password(password), username))
        return cur.rowcount > 0


def set_enabled(username: str, enabled: bool) -> bool:
    with db.conn() as c:
        db.ensure_users_tables(c)
        cur = c.execute("UPDATE users SET enabled = ? WHERE username = ?",
                        (1 if enabled else 0, username))
        return cur.rowcount > 0


ROLES = ("admin", "member")


def get_role(username: str) -> str | None:
    """账号角色:admin(可开运维会话) / member(仅对话与站点浏览);不存在返回 None。

    历史账号迁移默认 admin,保持"全部账号等权"的旧语义;需要收紧时用
    `userctl role <用户名> member`。
    """
    with db.conn() as c:
        db.ensure_users_tables(c)
        row = c.execute("SELECT role FROM users WHERE username = ?",
                        (username,)).fetchone()
    if row is None:
        return None
    return row["role"] or "admin"


def set_role(username: str, role: str) -> bool:
    if role not in ROLES:
        return False
    with db.conn() as c:
        db.ensure_users_tables(c)
        cur = c.execute("UPDATE users SET role = ? WHERE username = ?",
                        (role, username))
        return cur.rowcount > 0


def is_admin(username: str) -> bool:
    return get_role(username) == "admin"


def is_active(username: str) -> bool:
    """账号存在且启用。Cookie 每次请求都要过这一关：删号/禁用立即失效，
    不能等 7 天 Cookie 自然过期。"""
    with db.conn() as c:
        db.ensure_users_tables(c)
        row = c.execute("SELECT enabled FROM users WHERE username = ?",
                        (username,)).fetchone()
    return bool(row and row["enabled"])


def list_users() -> list[dict]:
    with db.conn() as c:
        db.ensure_users_tables(c)
        rows = c.execute(
            "SELECT username, enabled, role, created_at FROM users"
            " ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def authenticate(username: str, password: str) -> bool:
    """校验用户名+密码;账号不存在/停用/密码错误统一返回 False(不泄露区分)。"""
    if not username or not password:
        return False
    with db.conn() as c:
        db.ensure_users_tables(c)
        row = c.execute(
            "SELECT pw_hash, enabled FROM users WHERE username = ?",
            (username,)).fetchone()
    if row is None or not row["enabled"]:
        # 恒定时间路径:不存在的用户也做一次哈希,避免时序区分用户名
        verify_password(password, hash_password("timing-equalizer"))
        return False
    return verify_password(password, row["pw_hash"])


def get_knowledge_repo(username: str) -> str | None:
    """用户自配的知识库 GitHub 仓库;None = 默认 InferTechNote。"""
    with db.conn() as c:
        db.ensure_users_tables(c)
        row = c.execute("SELECT knowledge_repo FROM users WHERE username = ?",
                        (username,)).fetchone()
    return row["knowledge_repo"] if row else None


def set_knowledge_repo(username: str, repo: str | None) -> bool:
    repo = (repo or "").strip() or None
    if repo is not None and not repo.startswith(("https://github.com/", "git@github.com:")):
        return False
    with db.conn() as c:
        db.ensure_users_tables(c)
        cur = c.execute("UPDATE users SET knowledge_repo = ? WHERE username = ?",
                        (repo, username))
        return cur.rowcount > 0


def has_any_user() -> bool:
    """是否存在至少一个启用账号——serve.py 用于旧单密码的过渡判断。"""
    with db.conn() as c:
        db.ensure_users_tables(c)
        row = c.execute("SELECT 1 FROM users WHERE enabled = 1 LIMIT 1").fetchone()
    return row is not None
