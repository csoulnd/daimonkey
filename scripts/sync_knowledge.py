#!/usr/bin/env python3
"""同步只读知识库并重建网站，不触发热度数据采集。"""
import subprocess
import sys
import os
import signal
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector.knowledge import REPO_DIR, REPO_URL
from collector.sitegen import build


def git_run(args, timeout=90):
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
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    if not REPO_DIR.exists():
        staging = Path(tempfile.mkdtemp(prefix="knowledge-clone-", dir=REPO_DIR.parent)) / "repo"
        git_run(["clone", REPO_URL + ".git", str(staging)])
        staging.rename(REPO_DIR)
        staging.parent.rmdir()
    else:
        if subprocess.check_output(["git", "-C", str(REPO_DIR), "status", "--porcelain"], text=True).strip():
            raise RuntimeError("知识库工作区有改动，停止同步以保护现有内容")
        git_run(["-C", str(REPO_DIR), "pull", "--ff-only", "origin", "main"])
    build()
    print("个人知识库同步与建站完成")


if __name__ == "__main__":
    main()
