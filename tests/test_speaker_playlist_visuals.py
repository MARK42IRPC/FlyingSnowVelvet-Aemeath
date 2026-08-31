from __future__ import annotations

import unittest

from lib.core.graphics.application_visuals import create_portable_command_hint_metrics
from lib.core.graphics.speaker_playlist_visuals import (
    build_speaker_playlist_visual,
    speaker_playlist_hit_test,
)


class SpeakerPlaylistVisualTests(unittest.TestCase):
    def test_layout_contains_qt_control_progress_and_queue_families(self):
        queue = tuple((index, f"歌曲 {index}") for index in range(9))
        visual = build_speaker_playlist_visual(
            queue,
            create_portable_command_hint_metrics(),
            current_index=1,
            selected=2,
            playing=True,
            progress=0.5,
            remaining=125,
        )
        self.assertEqual([name for name, _rect in visual.control_rects], [
            "liked", "clear", "local", "play_mode", "play_pause",
            "next_track", "history", "volume_up", "volume_down",
        ])
        self.assertEqual(len(visual.row_rects), 7)
        self.assertIsNotNone(visual.remove_rect)
        self.assertIsNotNone(visual.play_rect)
        self.assertIsNotNone(visual.page_rect)

        rect = visual.remove_rect
        self.assertEqual(
            speaker_playlist_hit_test(visual, rect.x + 1, rect.y + 1),
            ("remove", -1),
        )
        rect = visual.row_rects[3]
        self.assertEqual(
            speaker_playlist_hit_test(visual, rect.x + 1, rect.y + 1),
            ("row", 3),
        )


if __name__ == "__main__":
    unittest.main()
