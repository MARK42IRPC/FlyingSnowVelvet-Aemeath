"""回复主题标记 [主题] 与原生工具调用的契约。"""

from __future__ import annotations

import unittest

from lib.core.event.center import Event, EventType
from lib.script.chat.handler_stream_presenter import _strip_tool_commands_for_display
from lib.script.chat.memory import StreamMemory
from lib.script.chat.native_tools import default_native_tool_reply
from lib.script.chat.topic_markers import split_topic_marker
from lib.script.tool_dispatcher.dispatcher import ToolDispatcher


class TopicMarkerTests(unittest.TestCase):
    def test_square_bracket_topic_is_split(self):
        self.assertEqual(split_topic_marker('[日常]来首音乐'), ('日常', '来首音乐'))

    def test_full_width_bracket_topic_is_split(self):
        self.assertEqual(split_topic_marker('【心情】有点累'), ('心情', '有点累'))

    def test_legacy_slash_topic_is_still_stripped(self):
        self.assertEqual(split_topic_marker('///日常///来首音乐'), ('日常', '来首音乐'))

    def test_legacy_topic_closed_by_hash_is_still_stripped(self):
        self.assertEqual(split_topic_marker('///日常###来首音乐'), ('日常', '来首音乐'))

    def test_plain_text_has_no_topic(self):
        self.assertEqual(split_topic_marker('今天吃什么'), ('', '今天吃什么'))


class DisplayTests(unittest.TestCase):
    def test_bubble_hides_the_topic_marker(self):
        self.assertEqual(_strip_tool_commands_for_display('[日常]来首音乐'), '来首音乐')

    def test_incomplete_topic_marker_is_hidden_while_streaming(self):
        self.assertEqual(_strip_tool_commands_for_display('[日常'), '')
        self.assertEqual(_strip_tool_commands_for_display('【日常'), '')

    def test_reported_reply_keeps_its_body_in_the_bubble(self):
        self.assertEqual(
            _strip_tool_commands_for_display('[日常]好！桌面上的粉色小宠物是我'),
            '好！桌面上的粉色小宠物是我',
        )

    def test_command_like_text_stays_plain_content(self):
        text = '我这就播放 ###音乐 纸飞机###'
        self.assertEqual(_strip_tool_commands_for_display(text), text)


class MemoryTests(unittest.TestCase):
    def test_memory_records_topic_and_body(self):
        topic, lines = StreamMemory._extract_topic_and_lines('[日常]来首音乐\n第二行')

        self.assertEqual(topic, '日常')
        self.assertEqual(lines, ['来首音乐', '第二行'])

    def test_memory_accepts_the_legacy_slash_marker(self):
        topic, lines = StreamMemory._extract_topic_and_lines('///心情///有点累')

        self.assertEqual(topic, '心情')
        self.assertEqual(lines, ['有点累'])

    def test_memory_keeps_default_topic_without_marker(self):
        topic, lines = StreamMemory._extract_topic_and_lines('随便聊聊')

        self.assertEqual(topic, '日常')
        self.assertEqual(lines, ['随便聊聊'])


class NativeToolOnlyTests(unittest.TestCase):
    class _Dispatcher(ToolDispatcher):
        def __init__(self):
            self._mode_service = None
            self.calls = []

        def execute_command(self, command, argument='', *, mode_generation=None):
            self.calls.append((command, argument))
            return True

    def test_text_command_marker_is_not_dispatched(self):
        dispatcher = self._Dispatcher()

        dispatcher._on_stream_final(
            Event(EventType.STREAM_FINAL, {'text': '###音乐 纸飞机###'})
        )

        self.assertEqual(dispatcher.calls, [])

    def test_native_call_is_dispatched(self):
        dispatcher = self._Dispatcher()

        dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {
            'text': '',
            'tool_call': {'name': 'play_music', 'arguments': {'query': '纸飞机'}},
        }))

        self.assertEqual(dispatcher.calls, [('音乐', '纸飞机')])

    def test_allow_tool_commands_false_blocks_the_native_call(self):
        dispatcher = self._Dispatcher()

        dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {
            'text': '',
            'tool_call': {'name': 'play_music', 'arguments': {'query': '纸飞机'}},
            'allow_tool_commands': False,
        }))

        self.assertEqual(dispatcher.calls, [])

    def test_default_reply_uses_the_square_bracket_topic(self):
        reply = default_native_tool_reply(
            {'name': 'play_music', 'arguments': {'query': '纸飞机'}}
        )

        self.assertTrue(reply.startswith('[音乐]'))


if __name__ == '__main__':
    unittest.main()
