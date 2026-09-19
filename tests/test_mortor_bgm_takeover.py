"""摩托管理器的 BGM 接管路径。

`_play_mortor_bgm` / `_pause_mortor_bgm` 里曾把日志写到未定义的 `logger`（本文件定义的
是 `_logger`），于是"用户正在听歌时放摩托"这条常见路径必然抛 `NameError`——
`can_takeover_for_bgm()` 为假就会走到那一行。ruff 的 F821 也一直指着这两处。
"""

import contextlib
import importlib
import unittest
from unittest.mock import patch

from lib.core.event.center import EventType

mortor_module = importlib.import_module("lib.script.obj-摩托.manager")
MortorManager = mortor_module.MortorManager


class _EventSink:
    """最小事件中心替身：只记录 publish，订阅面留空。"""

    def __init__(self):
        self.events = []

    def subscribe(self, *args, **kwargs):
        return None

    def unsubscribe(self, *args, **kwargs):
        return None

    def publish(self, event):
        self.events.append(event)


def _payloads(sink, event_type):
    return [event.data for event in sink.events if event.type is event_type]


class _StubMusicService:
    """只实现 BGM 接管判定所需的最小接口。"""

    def __init__(self, can_takeover: bool):
        self.can_takeover = can_takeover
        self.initialized = False

    def initialize(self):
        self.initialized = True

    def can_takeover_for_bgm(self) -> bool:
        return self.can_takeover


@contextlib.contextmanager
def _mortor_manager(service):
    """构造管理器并保持补丁在整个用例期间生效。

    `_play_mortor_bgm` / `_pause_mortor_bgm` 在调用时才会去取音乐服务，所以补丁不能只在
    构造阶段生效——否则会打到真实的 `get_music_service()` 上。
    """
    sink = _EventSink()
    with patch.object(mortor_module, "get_music_service", return_value=service), patch.object(
        MortorManager, "_load_png", lambda self: None
    ), patch.object(mortor_module, "get_event_center", return_value=sink), patch.object(
        mortor_module, "get_hash_cmd_registry", lambda: _FakeRegistry()
    ):
        yield MortorManager(entity=None), sink


class _FakeRegistry:
    def register(self, *args, **kwargs):
        return None


class MortorBgmSkipTests(unittest.TestCase):
    def test_existing_music_skips_bgm_without_raising(self):
        # 这是 V1 的回归点：跳过分支曾因 logger 未定义而抛 NameError。
        service = _StubMusicService(can_takeover=False)
        with _mortor_manager(service) as (manager, sink):
            manager._play_mortor_bgm()

        self.assertFalse(manager._bgm_started_by_mortor)
        self.assertEqual(_payloads(sink, EventType.MUSIC_PLAY_TOP), [])
        self.assertTrue(service.initialized)

    def test_free_player_takes_over_the_bgm(self):
        service = _StubMusicService(can_takeover=True)
        with _mortor_manager(service) as (manager, sink):
            manager._play_mortor_bgm()

        self.assertTrue(manager._bgm_started_by_mortor)
        payloads = _payloads(sink, EventType.MUSIC_PLAY_TOP)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["song_id"], mortor_module._MORTOR_BGM_SONG_ID)

    def test_pause_failure_is_logged_not_raised(self):
        # 异常分支同样写过 logger；这里让 publish 抛错，确认它被吞掉且状态被复位。
        service = _StubMusicService(can_takeover=True)
        with _mortor_manager(service) as (manager, sink):
            manager._bgm_started_by_mortor = True

            def _boom(event):
                raise RuntimeError("publish failed")

            sink.publish = _boom
            manager._pause_mortor_bgm()  # 不应抛异常

        self.assertFalse(manager._bgm_started_by_mortor)

    def test_pause_publishes_pause_event(self):
        service = _StubMusicService(can_takeover=True)
        with _mortor_manager(service) as (manager, sink):
            manager._bgm_started_by_mortor = True

            manager._pause_mortor_bgm()

        self.assertFalse(manager._bgm_started_by_mortor)
        payloads = _payloads(sink, EventType.MUSIC_PLAY_PAUSE)
        self.assertEqual(payloads, [{"playing": False}])


if __name__ == "__main__":
    unittest.main()
