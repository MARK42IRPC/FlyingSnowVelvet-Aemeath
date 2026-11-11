"""自研 CUDA 推理器的常驻 KV 缓存释放。

回归自真实故障：每合成一句话显存涨一点。原因是常驻缓存（`resident_`）只在
「下一次同名覆盖」时才释放，而上一句的缓存通常不会再被同一名字写一遍。
"""

from __future__ import annotations

import unittest

from lib.script.gsvmove.onnx_runtime import _release_resident_caches


class _Resettable:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_resident(self) -> None:
        self.reset_calls += 1


class _Raising:
    def reset_resident(self) -> None:
        raise RuntimeError("device lost")


class ReleaseResidentCachesTests(unittest.TestCase):
    def test_resets_the_session_held_on_the_engine(self):
        session = _Resettable()

        class _Engine:
            def __init__(self) -> None:
                self.session = session

        _release_resident_caches(_Engine())

        self.assertEqual(session.reset_calls, 1)

    def test_resets_every_session_in_a_list(self):
        sessions = [_Resettable(), _Resettable()]

        class _Engine:
            def __init__(self) -> None:
                self.sessions = sessions

        _release_resident_caches(_Engine())

        self.assertEqual([session.reset_calls for session in sessions], [1, 1])

    def test_engines_without_a_resettable_session_are_ignored(self):
        # 纯 ONNX Runtime 引擎没有这个方法，不能因此报错。
        _release_resident_caches(object())

        class _Plain:
            def __init__(self) -> None:
                self.session = object()

        _release_resident_caches(_Plain())

    def test_a_failing_reset_does_not_break_synthesis(self):
        class _Engine:
            def __init__(self) -> None:
                self.session = _Raising()

        _release_resident_caches(_Engine())


if __name__ == "__main__":
    unittest.main()
