"""测试包护栏:单测默认「离线、不调真实模型、秒级」。

2026-10-20 事故:test_build_survives_ai_failure 漏 mock 了方向周总结,
run_build 真的起了 pi 子进程联网,整轮测试挂起 5 分钟以上;执行通道超时清理
进程时,连累同机的线上服务被 SIGKILL。这里的护栏让"漏 mock"快速失败,
而不是挂起、触网或打到线上:
  - 只放行 loopback 连接(本机 HTTP 测试服务不受影响),外网必须 mock;
  - 未经 patch 的 assistant.run_internal 直接报错(不再起 pi 子进程);
  - pbkdf2 轮数降到 1/1000(哈希格式与校验逻辑不变,单测无需跑满安全参数)。
"""
from __future__ import annotations

import socket

_LOOPBACK = ("127.0.0.1", "::1", "localhost", "")


def _block_internet() -> None:
    if getattr(socket.socket, "_daimon_guarded", False):
        return
    real_connect = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise OSError(f"单测禁止外网连接: {address!r}(请 mock 数据源)")
        return real_connect(self, address)

    socket.socket.connect = connect
    socket.socket._daimon_guarded = True


def _block_real_model() -> None:
    from collector import assistant

    if getattr(assistant.run_internal, "_daimon_guarded", False):
        return

    def blocked(*_args, **_kwargs):
        raise RuntimeError("单测禁止真实调用 assistant.run_internal;请 patch 它")

    blocked._daimon_guarded = True
    assistant.run_internal = blocked


def _fast_password_hashing() -> None:
    from collector import users

    users._ITERATIONS = 1_000


_block_internet()
_block_real_model()
_fast_password_hashing()
