#!/usr/bin/env python3
"""轻量用户系统 CLI:服务器上手动注册与管理账号(全部账号等权=管理员)。

用法(服务器上已装全局命令 userctl,任意目录可执行;等价 .venv/bin/python scripts/userctl.py):
    userctl add <用户名>            # 交互输入密码(输两遍)
    userctl passwd <用户名>
    userctl list
    userctl disable <用户名>
    userctl enable <用户名>

用户名规则:[a-zA-Z0-9_.-],1~32 字符;密码至少 8 位。
哈希存 SQLite users 表(pbkdf2_hmac, 20 万轮),不落任何明文。
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector import users  # noqa: E402


def _ask_password() -> str | None:
    first = getpass.getpass("密码(至少 8 位): ")
    if len(first) < 8:
        print("错误: 密码至少 8 位")
        return None
    second = getpass.getpass("再输入一遍: ")
    if first != second:
        print("错误: 两次输入不一致")
        return None
    return first


def cmd_add(username: str) -> int:
    if not users.valid_username(username):
        print("错误: 用户名仅限字母/数字/_ . -,1~32 字符")
        return 1
    password = _ask_password()
    if password is None:
        return 1
    if not users.create_user(username, password):
        print(f"错误: 建号失败(用户名 {username} 可能已存在)")
        return 1
    print(f"已创建账号: {username}")
    return 0


def cmd_passwd(username: str) -> int:
    password = _ask_password()
    if password is None:
        return 1
    if not users.set_password(username, password):
        print(f"错误: 账号 {username} 不存在")
        return 1
    print(f"已更新密码: {username}")
    return 0


def cmd_list() -> int:
    rows = users.list_users()
    if not rows:
        print("(尚无账号——用 add 子命令创建;无账号期间登录走旧单一密码过渡)")
        return 0
    print(f"{'用户名':<24} 状态      创建时间")
    for r in rows:
        state = "启用" if r["enabled"] else "停用"
        print(f"{r['username']:<24} {state:<6}    {r['created_at']}")
    return 0


def cmd_toggle(username: str, enabled: bool) -> int:
    if not users.set_enabled(username, enabled):
        print(f"错误: 账号 {username} 不存在")
        return 1
    print(f"{'已启用' if enabled else '已停用'}: {username}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="账号管理(全部账号等权)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="创建账号")
    p_add.add_argument("username")
    p_pw = sub.add_parser("passwd", help="改密码")
    p_pw.add_argument("username")
    sub.add_parser("list", help="列出账号")
    p_off = sub.add_parser("disable", help="停用账号")
    p_off.add_argument("username")
    p_repo = sub.add_parser("repo", help="配置用户知识库 GitHub 仓库('- '清除恢复默认)")
    p_repo.add_argument("username")
    p_repo.add_argument("repo", nargs="?", default="-")
    p_on = sub.add_parser("enable", help="启用账号")
    p_on.add_argument("username")
    args = ap.parse_args()

    if args.cmd == "add":
        return cmd_add(args.username)
    if args.cmd == "passwd":
        return cmd_passwd(args.username)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "disable":
        return cmd_toggle(args.username, False)
    if args.cmd == "repo":
        repo = None if args.repo == "-" else args.repo
        if not users.set_knowledge_repo(args.username, repo):
            print("错误: 账号不存在或仓库地址需为 https://github.com/... / git@github.com:...")
            return 1
        current = users.get_knowledge_repo(args.username)
        print(f"{args.username} 知识库仓库 = {current or '默认(InferTechNote)'}")
        return 0
    return cmd_toggle(args.username, True)


if __name__ == "__main__":
    sys.exit(main())
