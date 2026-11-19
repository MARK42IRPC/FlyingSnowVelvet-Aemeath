"""自动陪伴观察必须带标记，并且不能回灌进模型上下文。"""

from __future__ import annotations

import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType
from lib.script.chat.context_markers import (
    AUTO_COMPANION_SOURCE,
    AUTO_COMPANION_TOPIC,
    tag_auto_companion_text,
)
from lib.script.chat.handler_persona import ChatHandlerPersonaMixin
from lib.script.chat.handler_stream_presenter import ChatHandlerStreamPresenterMixin
from lib.script.chat.memory import StreamMemory


class _EventCenter:
    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)


class _PersonaHandler(ChatHandlerPersonaMixin):
    def __init__(self):
        self._recent_context = deque(maxlen=12)

    @staticmethod
    def _memory_context_limit() -> int:
        return 12


class _PresenterHandler(ChatHandlerStreamPresenterMixin):
    def __init__(self):
        self._event_center = _EventCenter()
        self.context_calls = []
        self._cleaned = False

    def _append_recent_context(self, role, text, *, source=None):
        self.context_calls.append((role, text, source))

    @staticmethod
    def _calc_stream_final_min_ticks(text: str) -> int:
        return 2


class AutoCompanionMarkingTests(unittest.TestCase):
    def test_auto_companion_does_not_enter_recent_context(self):
        handler = _PersonaHandler()

        handler._append_recent_context('user', '你好')
        handler._append_recent_context('assistant', '你好呀')
        handler._append_recent_context(
            'user', '（观察屏幕）', source=AUTO_COMPANION_SOURCE
        )
        handler._append_recent_context(
            'assistant', '漂泊者正在看视频。', source=AUTO_COMPANION_SOURCE
        )

        self.assertEqual(
            handler._get_recent_context_snapshot(),
            [
                {'role': 'user', 'content': '你好'},
                {'role': 'assistant', 'content': '你好呀'},
            ],
        )

    def test_snapshot_filters_defensive_auto_source_entries(self):
        handler = _PersonaHandler()
        handler._recent_context.append(
            {'role': 'assistant', 'content': '旧观察', 'source': AUTO_COMPANION_SOURCE}
        )
        handler._recent_context.append(
            {'role': 'user', 'content': '正常输入'}
        )

        self.assertEqual(
            handler._get_recent_context_snapshot(),
            [{'role': 'user', 'content': '正常输入'}],
        )

    def test_persona_memory_block_skips_auto_topic(self):
        handler = _PersonaHandler()
        memory = Mock()
        memory.get_recent_entries.return_value = [
            {'topic': AUTO_COMPANION_TOPIC, 'role': 'you', 'content': '漂泊者正在看视频。'},
            {'topic': '日常', 'role': 'user', 'content': '今天吃什么'},
        ]

        with patch('lib.script.chat.memory.get_stream_memory', return_value=memory):
            block = handler._build_recent_memory_block()

        self.assertNotIn(AUTO_COMPANION_TOPIC, block)
        self.assertNotIn('漂泊者正在看视频。', block)
        self.assertIn('今天吃什么', block)

    def test_auto_response_marks_context_memory_and_stream_event(self):
        handler = _PresenterHandler()
        memory = Mock()

        with patch('lib.script.chat.memory.get_stream_memory', return_value=memory):
            handler._publish_auto_response(
                '收到', include_history=True, user_text='观察屏幕'
            )

        self.assertEqual(
            handler.context_calls,
            [
                ('user', '观察屏幕', AUTO_COMPANION_SOURCE),
                ('assistant', '收到', AUTO_COMPANION_SOURCE),
            ],
        )
        memory.record_user_input.assert_called_once_with(
            tag_auto_companion_text('观察屏幕')
        )
        finals = [
            event for event in handler._event_center.published
            if event.type == EventType.STREAM_FINAL
        ]
        self.assertTrue(finals)
        self.assertEqual(finals[-1].data.get('source'), AUTO_COMPANION_SOURCE)

    def test_stream_memory_writes_auto_reply_under_auto_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = object.__new__(StreamMemory)
            memory._memory_dir = Path(tmp)
            memory._memory_dir.mkdir(parents=True, exist_ok=True)
            memory._write_lock = threading.Lock()
            memory._suppress_next_response = False
            memory._mode_service = None

            memory._on_stream_final(
                Event(
                    EventType.STREAM_FINAL,
                    {'text': '漂泊者正在看视频。', 'source': AUTO_COMPANION_SOURCE},
                )
            )

            files = list(memory._memory_dir.glob('memory_*.txt'))
            self.assertEqual(len(files), 1)
            content = files[0].read_text(encoding='utf-8')
            self.assertIn(f'[{AUTO_COMPANION_TOPIC}][you:]漂泊者正在看视频。', content)


if __name__ == '__main__':
    unittest.main()
