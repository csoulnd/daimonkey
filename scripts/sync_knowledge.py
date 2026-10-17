#!/usr/bin/env python3
"""同步只读知识库代码仓并重建网站，不触发热度数据采集。

支持网页按钮（POST /api/v1/update/knowledge）与 CLI 两种触发方式：
- 状态写入 data/update_status.json（mode=knowledge），前端轮询通用状态接口；
- 建站走 pipeline.run_build()，随仓库 commit 变化自动刷新知识库 AI 摘要；
- 失败写 failed 状态并抛出（HTTP 触发时退出码非零，日志在 data/logs/update-*.log）。
"""
import os
import signal
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --user 须在导入 collector 之前生效(knowledge 模块在导入时读环境变量)
_user = None
if "--user" in sys.argv:
    i = sys.argv.index("--user")
    if i + 1 < len(sys.argv):
        _user = sys.argv[i + 1]
        sys.argv = [sys.argv[0]] + sys.argv[:i] + sys.argv[i + 2:]
if _user:
    from collector import users as _users
    _repo = _users.get_knowledge_repo(_user)
    if not _repo:
        raise SystemExit(f"用户 {_user} 未配置知识库仓库")
    _repo = _repo.removesuffix(".git").removesuffix("/")
    os.environ["KNOWLEDGE_USER"] = _user
    os.environ["KNOWLEDGE_URL"] = _repo
    _https = _repo.replace("https://github.com/", "").rstrip("/")
    os.environ["KNOWLEDGE_GIT"] = f"git@github.com:{_https}.git"

from collector import pipeline
from collector.knowledge import GIT_URL, REPO_DIR


def git_run(args, timeout=180):
    # Kill the whole Git process group on timeout, including git-remote-https.
    proc = subprocess.Popen(["git", *args], start_new_session=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    try:
        result = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait()
        raise
    if result:
        raise subprocess.CalledProcessError(result, proc.args)


def main():
    pipeline.write_status("running", mode="knowledge")
    try:
        REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
        if not REPO_DIR.exists():
            staging = Path(tempfile.mkdtemp(prefix="knowledge-clone-",
                                            dir=REPO_DIR.parent)) / "repo"
            git_run(["clone", GIT_URL, str(staging)])
            staging.rename(REPO_DIR)
            staging.parent.rmdir()
        else:
            if subprocess.check_output(["git", "-C", str(REPO_DIR), "status",
                                        "--porcelain"], text=True).strip():
                raise RuntimeError("知识库工作区有改动，停止同步以保护现有内容")
            # 旧工作区 remote 可能是 HTTPS（本机被阻断）：自动迁移到 SSH
            current = subprocess.check_output(
                ["git", "-C", str(REPO_DIR), "remote", "get-url", "origin"],
                text=True).strip()
            if current != GIT_URL:
                git_run(["-C", str(REPO_DIR), "remote", "set-url", "origin",
                         GIT_URL])
                print(f"origin 已迁移: {current} -> {GIT_URL}")
            git_run(["-C", str(REPO_DIR), "pull", "--ff-only", "origin", "main"])
        if _user:
            from collector import knowledge as _kb
            _kb.build_user_standalone(_user)
            ai_notes = []
        else:
            ai_notes = pipeline.run_build()   # 含 AI 摘要刷新(指纹=commit,变更才生成)
        pipeline.write_status("success", mode="knowledge", ai=ai_notes)
        for line in ai_notes:
            print(line)
        print("个人知识库同步与建站完成")
    except Exception as e:
        pipeline.write_status("failed", mode="knowledge",
                              error=f"{type(e).__name__}: {e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
