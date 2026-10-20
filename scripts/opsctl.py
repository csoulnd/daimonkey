#!/usr/bin/env python3
"""daimonkey 运维 CLI：只读体检 + 受控延时重启。

为什么需要它：
    站内运维 agent（pi 工具）以 daimonkey 普通用户运行。如果它直接
    `systemctl restart daimonkey`，会先杀掉自己所在的服务、中断当前轮，
    重启结果也无法回传。因此重启统一走 root 拥有的固定 wrapper，
    由 systemd-run 延时（默认 3 秒）触发，给 agent 留出收尾时间。

设计约束：
    - 纯标准库，Python 3.12；所有外部命令都用 subprocess 列表参数，不经 shell。
    - 退出码固定 0（成功）/ 1（失败）；失败给出可操作提示，不吞异常伪装成功。
    - restart 前必须确认 daimonkey 服务存在，避免重启一个不存在的单元。
    - deploy-check 只做“命令存在性/权限/服务用户/目录属主”的轻量检查，
      不跑全量单测、不构建站点、不发起任何会改变系统状态的命令。

用法（仓库根目录）：
    python scripts/opsctl.py status
    python scripts/opsctl.py logs -n 80
    python scripts/opsctl.py restart --in 3
    python scripts/opsctl.py deploy-check
"""
from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = "daimonkey"
WRAPPER = Path("/usr/local/bin/daimonkey-restart")
UNIT_FILE = Path(f"/etc/systemd/system/{SERVICE}.service")
SUDOERS_FILE = Path(f"/etc/sudoers.d/{SERVICE}")
JOURNAL_GROUP = "systemd-journal"

_LOOPBACK_BASE = os.environ.get("DAIMONKEY_URL", "").strip()
if _LOOPBACK_BASE:
    SITE_URL = _LOOPBACK_BASE.rstrip("/") + "/"
else:
    try:
        _PORT = int(os.environ.get("DAIMONKEY_PORT", "8080"))
    except ValueError:
        _PORT = 8080
    SITE_URL = f"http://127.0.0.1:{_PORT}/"


# ---------------------------------------------------------------- 基础工具
def run_cmd(args: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess | None:
    """执行外部命令；命令不存在或超时返回 None（调用方必须显式处理）。"""
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        print(f"错误: 找不到命令 {args[0]}（请安装，或确认 PATH 包含 /usr/bin:/bin）")
    except subprocess.TimeoutExpired:
        print(f"错误: 命令超时（{timeout}s）: {' '.join(args)}")
    return None


def service_prop(prop: str) -> str:
    """读取 systemd 单元属性；读不到返回空串。"""
    proc = run_cmd(["systemctl", "show", f"-p{prop}", "--value", SERVICE])
    if proc is None or proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _fmt_proc_error(proc: subprocess.CompletedProcess) -> str:
    out = (proc.stderr or proc.stdout or "").strip()
    return out or f"退出码 {proc.returncode}"


# ---------------------------------------------------------------- status
def cmd_status() -> int:
    """服务状态 + 最近一次更新任务 + 站点本地 HTTP 探活。"""
    print(f"== {SERVICE} 服务状态 ==")
    load = service_prop("LoadState")
    active = service_prop("ActiveState")
    sub = service_prop("SubState")
    user = service_prop("User") or "(未配置，默认 root)"
    group = service_prop("Group") or "(未配置)"

    if not load:
        print("错误: 读不到 systemd 单元状态（systemd 不可用，或权限不足）")
        print("提示: 容器/无 systemd 环境请直接探活站点；服务安装见 docs/deploy.md")
        return 1
    if load != "loaded":
        print(f"错误: 单元 {SERVICE} 未加载（LoadState={load}）")
        print("提示: 先安装服务: sudo bash deploy/bootstrap.sh")
        return 1

    print(f"  LoadState   : {load}")
    print(f"  ActiveState : {active or 'unknown'} ({sub or 'unknown'})")
    print(f"  运行用户    : User={user} Group={group}")
    if user == "root":
        print("  注意: 服务仍以 root 运行；降权步骤见 docs/deploy.md「服务降权与运维 Agent」")

    # 最近一次更新任务（采集/评分/构建）状态
    status_file = ROOT / "data" / "update_status.json"
    print("== 最近一次更新任务 ==")
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"  (无 {status_file}，站点可能从未跑过更新)")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  错误: 无法解析 {status_file}: {exc}")
    else:
        state = payload.get("state", "unknown")
        when = payload.get("time") or payload.get("started_at") or "-"
        mode = payload.get("mode") or "-"
        print(f"  state={state} mode={mode} time={when}")
        err = payload.get("error")
        if err:
            print(f"  错误信息: {err}")

    # 站点本地 HTTP 探活（只认 200）
    print("== 站点探活 ==")
    print(f"  URL: {SITE_URL}")
    http_ok = False
    try:
        with urllib.request.urlopen(SITE_URL, timeout=5) as resp:
            code = resp.status
            if code == 200:
                http_ok = True
                print("  HTTP 200（OK）")
            else:
                print(f"  HTTP {code}（期望 200）")
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code}（期望 200）: {exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  连接失败: {exc}")
        print("  提示: 确认端口是否正确（DAIMONKEY_PORT/DAIMONKEY_URL），"
              "并查看 python scripts/opsctl.py logs -n 50")

    healthy = active == "active" and http_ok
    print("== 结论 ==")
    print("  服务健康" if healthy else "  服务不健康（见上方各项）")
    return 0 if healthy else 1


# ---------------------------------------------------------------- logs
def cmd_logs(lines: int) -> int:
    """读 journal；非 root 且不在 systemd-journal 组时给出明确提示。"""
    if not 1 <= lines <= 5000:
        print("错误: -n 需在 1~5000 之间")
        return 1
    proc = run_cmd(["journalctl", "-u", SERVICE, "-n", str(lines), "--no-pager"],
                   timeout=30)
    if proc is None:
        print("提示: 没有 journalctl（非 systemd 系统？）；可看站点自己的日志文件")
        return 1
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        print(f"\n错误: 读取 journal 失败: {_fmt_proc_error(proc)}", file=sys.stderr)
        if os.geteuid() != 0:
            who = pwd.getpwuid(os.geteuid()).pw_name
            print(
                "提示: 非 root 读 journal 需要加入 systemd-journal 组：\n"
                f"      sudo usermod -aG {JOURNAL_GROUP} {who}\n"
                "      退出重登（或 newgrp systemd-journal）后生效；"
                "临时可用 sudo journalctl ...",
                file=sys.stderr,
            )
        else:
            print("提示: 确认服务已启动过：systemctl status daimonkey", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- restart
def _unit_loaded() -> bool:
    return service_prop("LoadState") == "loaded"


def _try_wrapper(delay: int) -> bool:
    """优先用 root 固定的 wrapper（非 root 时经 sudo -n）。"""
    if not WRAPPER.is_file():
        return False
    if os.geteuid() == 0:
        cmd = [str(WRAPPER), str(delay)]
    else:
        if not shutil.which("sudo"):
            print("  跳过 wrapper: 找不到 sudo")
            return False
        cmd = ["sudo", "-n", str(WRAPPER), str(delay)]

    print(f"  尝试: {' '.join(cmd)}")
    proc = run_cmd(cmd, timeout=30)
    if proc is not None and proc.returncode == 0:
        if proc.stdout.strip():
            print(f"  {proc.stdout.strip()}")
        return True
    if proc is not None:
        print(f"  wrapper 失败: {_fmt_proc_error(proc)}")
        if os.geteuid() != 0:
            print("  提示: 若提示需要密码，检查 /etc/sudoers.d/daimonkey 是否已安装"
                  "（sudo -n -l /usr/local/bin/daimonkey-restart）")
    return False


def _try_systemd_run(delay: int) -> bool:
    """无 wrapper 时自建一次性 transient unit（需要 root）。"""
    if not shutil.which("systemd-run"):
        return False
    unit = f"{SERVICE}-restart-{int(time.time())}"
    cmd = ["systemd-run", f"--on-active={delay}", f"--unit={unit}",
           "systemctl", "restart", SERVICE]
    print(f"  尝试: {' '.join(cmd)}")
    proc = run_cmd(cmd, timeout=30)
    if proc is not None and proc.returncode == 0:
        if proc.stdout.strip():
            print(f"  {proc.stdout.strip()}")
        return True
    if proc is not None:
        print(f"  systemd-run 失败: {_fmt_proc_error(proc)}")
        if os.geteuid() != 0:
            print("  提示: systemd-run 需要 root；普通用户请改用 wrapper + sudo")
    return False


def cmd_restart(delay: int) -> int:
    """确认服务存在后，按 wrapper > systemd-run > 直接 systemctl（仅 root）回退。"""
    if delay < 0 or delay > 3600:
        print("错误: --in 需在 0~3600 秒之间")
        return 1
    if not _unit_loaded():
        print(f"错误: 服务单元 {SERVICE} 不存在或未加载，拒绝重启")
        print("提示: 先安装服务: sudo bash deploy/bootstrap.sh；"
              "查看单元: systemctl list-unit-files | grep daimonkey")
        return 1

    print(f"== 延时重启 {SERVICE}（{delay}s 后） ==")
    if _try_wrapper(delay):
        print(f"  [OK] 已通过 {WRAPPER} 安排 {delay}s 后重启")
        print("  提示: 重启由 systemd 异步执行，当前轮可继续收尾；"
              "稍后运行 status 验证")
        return 0
    if _try_systemd_run(delay):
        print(f"  [OK] 已通过 systemd-run 安排 {delay}s 后重启")
        return 0

    if os.geteuid() != 0:
        print("错误: 三种重启方式都不可用（无 wrapper / systemd-run 非 root 不可用）")
        print("提示: 让管理员执行 sudo bash deploy/bootstrap.sh 安装 wrapper 与 sudoers；"
              "或临时由 root 重启")
        return 1

    # 最后兜底：root 直接重启。注意会中断本进程所在的服务。
    print("  [WARN] 未找到 wrapper/systemd-run，回退为 root 直接 systemctl restart")
    if delay > 0:
        print(f"  等待 {delay}s 后执行（本进程可能随服务重启被中断）")
        time.sleep(delay)
    proc = run_cmd(["systemctl", "restart", SERVICE], timeout=60)
    if proc is None:
        return 1
    if proc.returncode != 0:
        print(f"错误: 重启失败: {_fmt_proc_error(proc)}")
        print("提示: journalctl -u daimonkey -n 50 --no-pager 查看原因")
        return 1
    print(f"  [OK] {SERVICE} 已重启")
    return 0


# ---------------------------------------------------------------- deploy-check
def _check(ok: bool, name: str, hint: str = "") -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if not ok and hint:
        print(f"         提示: {hint}")
    return ok


def cmd_deploy_check() -> int:
    """轻量部署体检：不跑全量测试，不改变系统状态。"""
    print("== daimonkey 部署检查（只读） ==")
    results: list[bool] = []

    py = ROOT / ".venv" / "bin" / "python"
    results.append(_check(py.is_file() and os.access(py, os.X_OK),
                          f"venv 解释器可执行: {py}",
                          "重建: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"))
    serve = ROOT / "scripts" / "serve.py"
    results.append(_check(serve.is_file(), f"站点入口存在: {serve}",
                          "仓库不完整，重新 git clone"))

    # --- systemd 单元与服务用户
    load = service_prop("LoadState")
    results.append(_check(load == "loaded", f"systemd 单元已加载（LoadState={load or 'unknown'}）",
                          "sudo bash deploy/bootstrap.sh 安装服务"))
    if load == "loaded":
        user = service_prop("User") or "root"
        group = service_prop("Group") or user
        results.append(_check(user != "root", f"服务用户已降权（User={user}）",
                              "仍为 root：按 docs/deploy.md 降权，或 deploy/ops-rollback.sh 回滚后重试"))
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            pw = None
            results.append(_check(False, f"服务用户存在: {user}",
                                  "sudo bash deploy/bootstrap.sh --run-as daimonkey --create-user"))
        else:
            results.append(_check(True, f"服务用户存在: {user} (uid={pw.pw_uid})"))
        try:
            grp.getgrnam(group)
        except KeyError:
            results.append(_check(False, f"服务用户组存在: {group}",
                                  "Group= 必须与运行用户同名；检查 deploy/daimonkey.service"))
        else:
            results.append(_check(True, f"服务用户组存在: {group}"))

        # --- 目录属主
        if pw is not None:
            for rel in ["", "data", "dist", ".venv"]:
                path = ROOT / rel if rel else ROOT
                if not path.exists():
                    results.append(_check(False, f"目录存在: {path}",
                                          "运行 bootstrap 生成 data/dist/.venv"))
                    continue
                st = path.stat()
                results.append(_check(
                    st.st_uid == pw.pw_uid,
                    f"属主为 {user}: {path} (uid={st.st_uid})",
                    f"sudo chown -R {user}:{group} '{path}'",
                ))
            # journal 组（读日志）
            try:
                jg = grp.getgrnam(JOURNAL_GROUP)
            except KeyError:
                results.append(_check(False, f"系统组 {JOURNAL_GROUP} 存在",
                                      "安装 systemd 后才有该组；无 journal 可跳过"))
            else:
                in_group = user in jg.gr_mem or pw.pw_gid == jg.gr_gid
                results.append(_check(
                    in_group, f"{user} 在 {JOURNAL_GROUP} 组",
                    f"sudo usermod -aG {JOURNAL_GROUP} {user}（需重新登录生效）"))

    # --- 运维权限
    if WRAPPER.is_file():
        st = WRAPPER.stat()
        text = ""
        try:
            text = WRAPPER.read_text(encoding="utf-8")
        except OSError:
            pass
        results.append(_check(
            st.st_uid == 0 and (st.st_mode & 0o777) == 0o755,
            f"重启 wrapper 属主/权限正确: {WRAPPER}",
            "sudo bash deploy/bootstrap.sh 重新安装（root:root 0755）"))
        results.append(_check("systemd-run" in text, "wrapper 使用 systemd-run 延时重启",
                              "wrapper 内容异常，重新安装"))
    else:
        results.append(_check(False, f"重启 wrapper 存在: {WRAPPER}",
                              "sudo bash deploy/bootstrap.sh 安装"))

    if SUDOERS_FILE.is_file():
        st = SUDOERS_FILE.stat()
        results.append(_check(
            st.st_uid == 0 and (st.st_mode & 0o777) == 0o440,
            f"sudoers 文件属主/权限正确: {SUDOERS_FILE}",
            "sudo chown root:root ... && sudo chmod 0440 ..."))
        if os.geteuid() != 0:
            # 0440 root:root 的文件普通用户读不到；visudo 校验只在 root 下做。
            # 安装时 bootstrap 已 visudo -cf 校验过，这里不把"无权读"误报成失败。
            print("  [SKIP] sudoers 语法校验（需要 root；用 sudo 复跑 deploy-check 可校验）")
        elif shutil.which("visudo"):
            proc = run_cmd(["visudo", "-cf", str(SUDOERS_FILE)], timeout=20)
            ok = proc is not None and proc.returncode == 0
            detail = "" if ok else (f"：{_fmt_proc_error(proc)}" if proc else "")
            results.append(_check(ok, f"sudoers 语法校验通过{detail}",
                                  "修正语法后重装，避免 sudo 全局失效"))
        else:
            results.append(_check(False, "visudo 可用（校验 sudoers 必需）",
                                  "安装 sudo 包"))
    else:
        results.append(_check(False, f"sudoers 文件存在: {SUDOERS_FILE}",
                              "sudo bash deploy/bootstrap.sh 安装"))

    failed = results.count(False)
    print("== 汇总 ==")
    print(f"  通过 {results.count(True)} 项，失败 {failed} 项")
    if failed:
        print("  部署未达标，按上方提示修复；回滚见 deploy/ops-rollback.sh")
        return 1
    print("  部署检查全部通过")
    return 0


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="opsctl.py",
        description="daimonkey 运维 CLI：状态/日志/受控延时重启/部署体检（只读为主）",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="服务状态 + 最近更新任务 + 站点 HTTP 探活")

    p_logs = sub.add_parser("logs", help="查看 journal（默认最近 80 行）")
    p_logs.add_argument("-n", "--lines", type=int, default=80,
                        help="行数（1~5000，默认 80）")

    p_restart = sub.add_parser("restart", help="受控延时重启（默认 3 秒）")
    p_restart.add_argument("--in", dest="delay", type=int, default=3,
                           metavar="N", help="延时秒数（默认 3）")

    sub.add_parser("deploy-check", help="轻量部署体检（不跑全量测试）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "logs":
        return cmd_logs(args.lines)
    if args.cmd == "restart":
        return cmd_restart(args.delay)
    if args.cmd == "deploy-check":
        return cmd_deploy_check()
    print(f"错误: 未知子命令 {args.cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
