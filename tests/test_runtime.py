"""长驻 runtime 管理器生命周期：懒启动复用 / 空闲回收 / 故障重建（SDK 全 mock）。"""
import importlib
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import runtime


class RuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        runtime.shutdown()  # 每个用例从干净状态开始
        self.factory = Mock(side_effect=lambda: Mock(name="harness"))

    def tearDown(self):
        runtime.shutdown()

    def test_lazy_start_and_reuse(self):
        with patch.object(runtime, "_start", self.factory):
            h1 = runtime.get_harness()
            h2 = runtime.get_harness()
        self.assertIs(h1, h2, "活跃期内应复用同一 harness 进程")
        self.factory.assert_called_once()

    def test_idle_recycle_restarts(self):
        with patch.object(runtime, "_start", self.factory), \
                patch.object(runtime, "_idle_recycle_s", lambda: 0):
            h1 = runtime.get_harness()
            time.sleep(0.01)  # idle 阈值 0，下一次必然超时
            h2 = runtime.get_harness()
        self.assertIsNot(h1, h2, "空闲超阈值应回收并重建")
        self.assertEqual(self.factory.call_count, 2)
        h1.close.assert_called_once()

    def test_mark_broken_forces_rebuild(self):
        with patch.object(runtime, "_start", self.factory):
            h1 = runtime.get_harness()
            runtime.mark_broken()
            h2 = runtime.get_harness()
        self.assertIsNot(h1, h2, "broken 后应重建")
        h1.close.assert_called_once()

    def test_shutdown_closes(self):
        with patch.object(runtime, "_start", self.factory):
            h1 = runtime.get_harness()
            runtime.shutdown()
        h1.close.assert_called_once()
        self.assertIsNone(runtime._state["harness"])


if __name__ == "__main__":
    unittest.main()
