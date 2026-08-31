from __future__ import annotations

import unittest

from lib.core.graphics.application_visuals import create_portable_command_hint_metrics
from lib.core.graphics.speaker_visuals import (
    SPEAKER_SEARCH_PAGE_SIZE,
    build_speaker_search_visual,
    speaker_visual_hit_test,
)


class SpeakerVisualTests(unittest.TestCase):
    def setUp(self):
        self.metrics = create_portable_command_hint_metrics()

    def test_search_family_uses_qt_baseline_layout_and_hit_rects(self):
        visual = build_speaker_search_visual(
            "雪绒",
            "",
            tuple(f"03:2{i} 测试歌曲 {i}" for i in range(8)),
            self.metrics,
            page=0,
            selected=1,
        )

        self.assertGreaterEqual(visual.size.width, 240)
        self.assertEqual(len(visual.control_rects), 6)
        self.assertEqual(len(visual.result_rects), SPEAKER_SEARCH_PAGE_SIZE)
        self.assertIsNotNone(visual.page_rect)
        self.assertGreater(len(visual.batch.commands), 20)

        search = visual.search_rect
        self.assertEqual(
            speaker_visual_hit_test(visual, search.x + 2, search.y + 2),
            ("search", -1),
        )
        row = visual.result_rects[2]
        self.assertEqual(
            speaker_visual_hit_test(visual, row.x + 2, row.y + 2),
            ("result", 2),
        )

    def test_empty_and_searching_states_keep_stable_search_geometry(self):
        empty = build_speaker_search_visual("", "", (), self.metrics)
        searching = build_speaker_search_visual(
            "关键词", "", (), self.metrics, searching=True,
        )

        self.assertEqual(empty.input_rect, searching.input_rect)
        self.assertEqual(empty.search_rect, searching.search_rect)
        self.assertEqual(empty.size, searching.size)
        self.assertEqual(empty.result_rects, ())
        self.assertEqual(searching.result_rects, ())


if __name__ == "__main__":
    unittest.main()
