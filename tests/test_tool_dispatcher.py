from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType
from lib.script.tool_dispatcher import dispatcher as dispatcher_module
from lib.script.tool_dispatcher.dispatcher import (
    ToolDispatcher,
    _SUPPORTED_COMMANDS,
    _extract_tool_invocation,
    _normalize_url_arg,
    _split_recall_arg,
)


class _FakeEventCenter:
    def __init__(self):
        self.published = []
        self.subscriptions = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))

    def unsubscribe(self, event_type, callback):
        self.subscriptions = [item for item in self.subscriptions if item != (event_type, callback)]

    def publish(self, event):
        self.published.append(event)


class _ImmediateComputeHub:
    def submit_io(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return Mock()


class _FakeModeService:
    def __init__(self, mode="companion", generation=0):
        self.mode = mode
        self.generation = generation

    def snapshot(self):
        return SimpleNamespace(value=self.mode), self.generation

    def accepts_companion_generation(self, generation):
        return self.mode == "companion" and generation == self.generation

    @property
    def is_office(self):
        return self.mode == "office"


class ToolDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.center = _FakeEventCenter()
        with patch.object(dispatcher_module, 'get_event_center', return_value=self.center):
            self.dispatcher = ToolDispatcher(defer=lambda _delay, callback: callback())

    def _dispatch(self, text: str):
        self.center.published.clear()
        self.dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {'text': text}))
        return list(self.center.published)

    def test_parser_accepts_supported_marker_variants(self):
        self.assertEqual(_extract_tool_invocation('///音乐///好呀###音乐：纸飞机###'), ('音乐', '纸飞机'))
        self.assertEqual(_extract_tool_invocation('＃＃＃ 计时\n1 02 03 ＃＃＃'), ('计时', '1 02 03'))
        self.assertEqual(_extract_tool_invocation('###下一曲###'), ('下一曲', ''))

    def test_parser_accepts_common_tool_name_aliases(self):
        cases = (
            ('###播放音乐 电子朋克低沉###', ('音乐', '电子朋克低沉')),
            ('###播放 黎那汐塔###', ('音乐', '黎那汐塔')),
            ('###play_music L!!!ght###', ('音乐', 'L!!!ght')),
            ('###下一首###', ('下一曲', '')),
        )
        for marker, expected in cases:
            with self.subTest(marker=marker):
                self.assertEqual(_extract_tool_invocation(marker), expected)

    def test_native_call_takes_priority_over_legacy_marker(self):
        self.center.published.clear()
        self.dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {
            'text': '###下一曲###',
            'tool_call': {'name': 'toggle_play_pause', 'arguments': {}},
        }))

        self.assertEqual(len(self.center.published), 1)
        self.assertEqual(self.center.published[0].type, EventType.MUSIC_PLAY_PAUSE)

    def test_stale_generation_cannot_execute_tool_side_effects(self):
        mode = _FakeModeService(mode="office", generation=1)
        with patch.object(dispatcher_module, 'get_event_center', return_value=self.center):
            dispatcher = ToolDispatcher(mode_service=mode)
        self.addCleanup(dispatcher.cleanup)

        dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {
            "text": "###下一曲###",
            "mode_generation": 0,
        }))

        self.assertEqual(self.center.published, [])

    def test_office_pet_tool_without_generation_is_gated_by_the_current_mode(self):
        """办公模式的桌宠工具没有陪伴代次：只有停在办公模式才放行。"""
        mode = _FakeModeService(mode="office", generation=1)
        with patch.object(dispatcher_module, 'get_event_center', return_value=self.center):
            dispatcher = ToolDispatcher(mode_service=mode)
        self.addCleanup(dispatcher.cleanup)

        self.assertTrue(dispatcher._accepts_mode_generation(None))

        mode.mode = "companion"
        mode.generation = 2
        self.assertFalse(dispatcher._accepts_mode_generation(None))
        self.assertTrue(dispatcher._accepts_mode_generation(2))

    def test_office_music_request_survives_the_companion_generation_gate(self):
        mode = _FakeModeService(mode="office", generation=3)
        with patch.object(dispatcher_module, 'get_event_center', return_value=self.center):
            dispatcher = ToolDispatcher(mode_service=mode)
        self.addCleanup(dispatcher.cleanup)
        dispatcher._check_has_speaker = Mock(return_value=True)
        service = Mock()
        service.search.return_value = [
            SimpleNamespace(track_id='netease:1', title='纸飞机', artist='鸣潮', display='03:20 纸飞机 - 鸣潮')
        ]

        with patch.object(dispatcher_module, 'get_music_service', return_value=service):
            handled = dispatcher.execute_command('音乐', '纸飞机')

        self.assertTrue(handled)
        self.assertEqual([event.type for event in self.center.published], [EventType.MUSIC_PLAY_TOP])

    def test_direct_commands_publish_expected_events(self):
        cases = (
            ('###下一曲###', EventType.MUSIC_NEXT_TRACK, {}),
            ('###暂停###', EventType.MUSIC_PLAY_PAUSE, {}),
            ('###雪豹 3###', EventType.MANAGER_SPAWN_REQUEST, {'manager_id': 'snow_leopard', 'count': 3}),
            ('###沙发 2###', EventType.MANAGER_SPAWN_REQUEST, {'manager_id': 'sofa', 'count': 2}),
            ('###摩托 4###', EventType.MANAGER_SPAWN_REQUEST, {'manager_id': 'mortor', 'count': 4}),
            ('###闹钟 1 2 3###', EventType.MANAGER_SPAWN_REQUEST, {'manager_id': 'clock', 'seconds': 3723}),
            ('###计时 45###', EventType.MANAGER_SPAWN_REQUEST, {'manager_id': 'clock', 'seconds': 45}),
            ('###音量 +10###', EventType.MUSIC_VOLUME, {'delta': 0.1}),
            ('###音量 50###', EventType.MUSIC_VOLUME, {'volume': 0.5}),
            ('###窥屏###', EventType.INPUT_CHAT, {'source': 'tool_screen_peek', 'allow_tool_commands': False}),
        )
        for marker, event_type, expected_data in cases:
            with self.subTest(marker=marker):
                events = self._dispatch(marker)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].type, event_type)
                for key, value in expected_data.items():
                    self.assertEqual(events[0].data.get(key), value)

    def test_spawn_count_is_bounded(self):
        event = self._dispatch('###雪豹 999999###')[0]
        self.assertEqual(event.data['count'], dispatcher_module._MAX_SPAWN_COUNT)

    def test_teleport_maps_normalized_coordinates(self):
        with patch.dict(dispatcher_module.DRAW, {'screen_width': 1000, 'screen_height': 800}), patch.dict(
            dispatcher_module.ANIMATION, {'pet_size': (100, 100)}
        ):
            event = self._dispatch('###瞬移 1 0###')[0]

        self.assertEqual(event.type, EventType.PET_TELEPORT)
        self.assertEqual(event.data, {'entity_id': 'pet_window', 'x': 0, 'y': 700})

    def test_music_search_result_publishes_play_event(self):
        service = Mock()
        service.search.return_value = [
            SimpleNamespace(track_id='netease:1', title='纸飞机', artist='鸣潮', display='03:20 纸飞机 - 鸣潮')
        ]
        self.dispatcher._check_has_speaker = Mock(return_value=True)

        with patch.object(dispatcher_module, 'get_music_service', return_value=service):
            self.dispatcher._handle_music_request('纸飞机')

        event = self.center.published[-1]
        self.assertEqual(event.type, EventType.MUSIC_PLAY_TOP)
        self.assertEqual(event.data['track_ref'], 'netease:1')

    def test_recall_reads_canonical_memory_and_disables_recursive_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)
            (memory_dir / 'memory_2026-07-29.txt').write_text(
                '[2026-07-29 12:00:00][游戏][user:]今天玩了拉海洛方块\n',
                encoding='utf-8',
            )
            with patch.object(dispatcher_module, '_MEMORY_DIR', memory_dir):
                events = self._dispatch('###回忆 游戏###')

        input_event = next(event for event in events if event.type == EventType.INPUT_CHAT)
        self.assertEqual(input_event.data['source'], 'tool_recall')
        self.assertFalse(input_event.data['allow_tool_commands'])
        self.assertIn('今天玩了拉海洛方块', input_event.data['text'])

    def test_bare_recall_topic_is_recent_filter(self):
        self.assertEqual(_split_recall_arg('游戏'), ('recent', '游戏'))

    def test_browser_only_accepts_http_and_reports_open_failure(self):
        self.assertEqual(_normalize_url_arg('example.com/path'), 'https://example.com/path')
        self.assertEqual(_normalize_url_arg('localhost:8000/status'), 'https://localhost:8000/status')
        self.assertEqual(_normalize_url_arg('file:///C:/Windows/win.ini'), '')
        self.assertEqual(_normalize_url_arg('javascript:alert(1)'), '')

        with patch.object(dispatcher_module, 'get_compute_hub', return_value=_ImmediateComputeHub()), patch.object(
            dispatcher_module.webbrowser, 'open', return_value=False
        ):
            events = self._dispatch('###浏览器 example.com###')

        info = next(event for event in events if event.type == EventType.INFORMATION)
        self.assertIn('未能打开', info.data['text'])

    def test_persona_no_longer_teaches_the_legacy_command_markers(self):
        persona = (Path(__file__).resolve().parents[1] / 'resc' / 'persona.txt').read_text(encoding='utf-8')
        self.assertNotIn('###', persona)
        self.assertIn('function calling', persona)

    def test_legacy_fallback_prompt_still_lists_every_supported_command(self):
        from lib.script.chat.native_tools import LEGACY_TOOL_SYSTEM_NOTE

        for command in _SUPPORTED_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(f'###{command}', LEGACY_TOOL_SYSTEM_NOTE)


if __name__ == '__main__':
    unittest.main()
