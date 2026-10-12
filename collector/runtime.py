"""长驻 DSH agent runtime 管理器（聊天通道的执行底座）。

设计：
- 懒启动：首次使用才创建 SDK 上下文（实测冷启动 ~0.8s、空闲内存 ~160MB）；
- 复用：所有聊天消息共用一个长驻 harness 进程，会话内多轮上下文由 DSH 原生保持；
- 空闲回收：超过 idle_s 未使用自动退出进程、内存归还系统，下次使用重新冷启动；
- 故障重建：run 失败（协议/网络错误、RSS 击杀）后标记 broken，下次使用自动重建；
- 生命周期全部惰性判定（获取时检查），无需后台线程。

与一次性工作流（assistant.run_internal）的关系：同一 SDK、两种形态——
工作流按需起进程用完即退（内存即时归还），聊天走本模块的长驻 runtime。
"""
from __future__ import annotations

import threading
import time

from .config import DATA_DIR

DSH_HOME = DATA_DIR / "dsh-home"

DEFAULT_IDLE_RECYCLE_S = 1800  # 空闲 30 分钟回收

_lock = threading.Lock()
_state: dict = {"harness": None, "last_used": 0.0, "broken": False}


def _idle_recycle_s() -> int:
    """registry.yaml → assistant.execution.idle_recycle_s（默认 1800）。"""
    try:
        from .config import registry
        return int((registry().get("assistant") or {})
                   .get("execution", {}).get("idle_recycle_s", DEFAULT_IDLE_RECYCLE_S))
    except Exception:
        return DEFAULT_IDLE_RECYCLE_S


def _close_quietly(harness) -> None:
    try:
        harness.close()
    except Exception:
        pass


def _start():
    """创建一个新的长驻 SDK 上下文（配置读取失败会抛 AssistantError 语义异常）。"""
    import os

    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig  # 延迟导入

    from . import assistant as _assistant

    conf = _assistant.cfg()
    _assistant.load_env_file()
    api_key = os.environ.get(conf["api_key_env"]) or None
    if not api_key:
        raise _assistant.AssistantError(
            f"缺少 {conf['api_key_env']}（检查 data/assistant.env）")
    DSH_HOME.mkdir(parents=True, exist_ok=True)
    config = DeepSeekHarnessConfig(
        provider=conf["provider"], model=conf["model"], api_key=api_key,
        profile=conf["profile"], dsh_home=str(DSH_HOME),
        max_tokens=int(conf["interactive"]["max_output_tokens"]),
        request_timeout_seconds=int(conf["execution"]["timeout_s"]),
        initialize_timeout_seconds=30,
        env={"DSH_TELEMETRY_MODE": "DISABLED"},
    )
    return DeepSeekHarness(config)


def get_harness():
    """返回可用的长驻 harness（懒启动 / 空闲回收 / 故障重建都在这里惰性判定）。

    线程安全；配合 assistant 的全局执行锁（并发=1），同一时刻只有一个调用方使用。
    """
    with _lock:
        now = time.monotonic()
        harness = _state["harness"]
        if harness is not None:
            expired = now - _state["last_used"] > _idle_recycle_s()
            if not _state["broken"] and not expired:
                _state["last_used"] = now
                return harness
            _close_quietly(harness)  # broken 或空闲超时：关闭并重建
        harness = _start()
        _state.update(harness=harness, last_used=now, broken=False)
        return harness


def mark_broken() -> None:
    """run 失败后调用：当前 runtime 视为不健康，下次 get_harness 重建。"""
    with _lock:
        _state["broken"] = True


def shutdown() -> None:
    """进程退出/测试清理时调用。"""
    with _lock:
        if _state["harness"] is not None:
            _close_quietly(_state["harness"])
        _state.update(harness=None, broken=False)
