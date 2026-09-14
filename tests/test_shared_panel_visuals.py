"""Contract tests for the shared panel / media / settings visual presenters.

``lib/core/graphics/panel_visuals.py``, ``media_panel_visuals.py`` and
``settings_panel_visuals.py`` are the backend-neutral source of the pet
panels' shell, geometry and text placement. The Qt hosts only execute the
resulting batches, so the command order, colours and rects asserted here are
the contract every backend consumes.
"""

from __future__ import annotations

import unittest

from config.font_config import get_digit_font_family
from config.scale import scale_px
from lib.core.graphics.commands import (
    ClipPop,
    ClipPush,
    RectCommand,
    TextAlignment,
    TextCommand,
)
from lib.core.graphics.media_panel_visuals import (
    PROGRESS_PANEL_HEIGHT,
    PROGRESS_PANEL_WIDTH,
    SEARCH_RESULT_EMPTY_TEXT,
    SEARCH_RESULT_MAX_WIDTH,
    SEARCH_RESULT_MIN_WIDTH,
    SEARCH_RESULT_PAGE_SIZE,
    SEARCH_RESULT_ROW_HEIGHT,
    SEARCH_RESULT_SEARCHING_TEXT,
    build_playlist_panel_visual,
    build_progress_panel_visual,
    build_search_result_panel_visual,
    elide_mixed_text,
    mixed_text_commands,
    mixed_text_width,
    search_result_panel_size,
)
from lib.core.graphics.palette import COLORS, UI_THEME
from lib.core.graphics.panel_visuals import (
    SLIDER_HANDLE_ASPECT,
    action_button_commands,
    build_action_button_visual,
    build_panel_shell_visual,
    build_tab_bar_visual,
    inset_rect,
    panel_frame_commands,
    panel_inset,
    panel_shell_commands,
    slider_handle_commands,
)
from lib.core.graphics.settings_panel_visuals import (
    LEFT_WATERMARK_SCALE,
    WATERMARK_ALPHA,
    build_ai_settings_panel_visual,
)
from lib.core.graphics.types import FontSpec, Rect, Size
from lib.core.layer import Layer


class _Metrics:
    """Deterministic metrics: 8px per UI glyph, 5px per digit glyph."""

    default_font = FontSpec("UI", 12, True)
    digit_font = FontSpec("Digits", 12)
    default_line_height = 14.0
    digit_line_height = 12.0
    default_ascent = 10.0
    default_descent = 3.0
    digit_ascent = 9.0
    digit_descent = 2.0

    def measure(self, text: str, *, digit: bool = False) -> float:
        return float(len(str(text or "")) * (5 if digit else 8))

    def ascent_for(self, text: str, *, digit: bool = False) -> float:
        return self.digit_ascent if digit else self.default_ascent


METRICS = _Metrics()


def _fills(commands) -> list:
    return [command.fill for command in commands if isinstance(command, RectCommand)]


def _texts(commands) -> list:
    return [command for command in commands if isinstance(command, TextCommand)]


def _mixed_texts(commands) -> list[str]:
    """Rejoin the digit/UI segments produced by ``mixed_text_commands``."""
    runs: list[str] = []
    current: list[str] = []
    inside = False
    for command in commands:
        if isinstance(command, ClipPush):
            inside, current = True, []
        elif isinstance(command, ClipPop):
            inside = False
            runs.append("".join(current))
            current = []
        elif inside and isinstance(command, TextCommand):
            current.append(command.text)
    return runs


class PanelVisualTests(unittest.TestCase):
    def test_panel_shell_builds_three_layer_shell_and_content_rect(self):
        rect = Rect(0, 0, 100, 40)
        commands, content = panel_shell_commands(rect)
        inset = panel_inset()
        border = inset * 2

        self.assertEqual(_fills(commands), [COLORS["black"], COLORS["cyan"], COLORS["pink"]])
        self.assertEqual([command.rect for command in commands], [
            rect,
            inset_rect(rect, inset),
            inset_rect(rect, border),
        ])
        self.assertEqual([command.z for command in commands], [0, 1, 2])
        self.assertEqual([command.layer for command in commands], [int(Layer.PANEL)] * 3)
        self.assertEqual(content, inset_rect(rect, border))

    def test_panel_shell_honours_asymmetric_content_inset_and_alpha(self):
        rect = Rect(0, 0, 60, 20)
        commands, content = panel_shell_commands(
            rect,
            inset=2,
            z=5,
            alpha=0.5,
            content_inset=2,
            border=COLORS["black"],
            mid=COLORS["cyan"],
            bg=COLORS["pink"],
        )
        self.assertEqual(content, Rect(2, 2, 56, 16))
        self.assertEqual([command.z for command in commands], [5, 6, 7])
        self.assertEqual([command.alpha for command in commands], [0.5] * 3)

    def test_panel_frame_is_four_strips(self):
        rect = Rect(10, 20, 40, 30)
        commands = panel_frame_commands(rect, inset=2, z=4)
        self.assertEqual([command.rect for command in commands], [
            Rect(10, 20, 40, 2),
            Rect(10, 48, 40, 2),
            Rect(10, 20, 2, 30),
            Rect(48, 20, 2, 30),
        ])
        self.assertEqual(_fills(commands), [COLORS["black"]] * 4)
        self.assertEqual([command.z for command in commands], [4] * 4)

    def test_panel_frame_accepts_custom_colour(self):
        commands = panel_frame_commands(Rect(0, 0, 10, 10), inset=1, color=COLORS["pink"])
        self.assertEqual(_fills(commands), [COLORS["pink"]] * 4)

    def test_action_button_states(self):
        rect = Rect(0, 0, 40, 32)
        inset = panel_inset()

        normal, normal_content = action_button_commands(rect, None)
        self.assertEqual(_fills(normal), [COLORS["black"], COLORS["cyan"], COLORS["pink"]])
        self.assertEqual(normal_content, inset_rect(rect, inset * 2))

        hover, hover_content = action_button_commands(rect, None, state="hover")
        self.assertEqual(_fills(hover), [
            COLORS["black"], COLORS["cyan"], UI_THEME["deep_pink"], COLORS["pink"],
        ])
        self.assertEqual(hover_content, inset_rect(rect, inset * 3))

        pressed, pressed_content = action_button_commands(rect, None, state="pressed")
        self.assertEqual(_fills(pressed[2:]), [UI_THEME["deep_pink"], UI_THEME["highlight"]])
        self.assertEqual(pressed_content, inset_rect(rect, inset * 3))

        flat, flat_content = action_button_commands(rect, None, state="pressed_flat")
        self.assertEqual(_fills(flat), [COLORS["black"], COLORS["cyan"], UI_THEME["highlight"]])
        self.assertEqual(flat_content, inset_rect(rect, inset * 2))

    def test_action_button_label_uses_content_rect_and_black_text(self):
        rect = Rect(0, 0, 40, 32)
        font = FontSpec("UI", 12, True)
        commands, content = action_button_commands(rect, "搜索", font)
        label = _texts(commands)
        self.assertEqual(len(label), 1)
        self.assertEqual(label[0].text, "搜索")
        self.assertEqual(label[0].font, font)
        self.assertEqual(label[0].color, COLORS["black"])
        self.assertEqual(label[0].rect, content)
        self.assertEqual(
            label[0].alignment,
            int(TextAlignment.HCENTER | TextAlignment.VCENTER),
        )

    def test_build_helpers_expose_size_and_content(self):
        rect = Rect(0, 0, 30, 18)
        panel = build_panel_shell_visual(rect)
        self.assertEqual(panel.size, Size(30, 18))
        self.assertEqual(panel.rect, rect)
        self.assertEqual(panel.content_rect, inset_rect(rect, panel_inset() * 2))
        self.assertEqual(len(panel.batch.commands), 3)

        button = build_action_button_visual(rect, "OK", FontSpec("UI", 12, True))
        self.assertEqual(button.size, Size(30, 18))
        self.assertEqual(button.content_rect, inset_rect(rect, panel_inset() * 2))
        self.assertEqual(len(button.batch.commands), 4)

    def test_slider_handle_is_a_portrait_rect_inside_the_track(self):
        track = Rect(4, 4, 100, 12)
        commands, rect = slider_handle_commands(54.0, track, COLORS["pink"])
        self.assertEqual(len(commands), 1)
        self.assertIsInstance(commands[0], RectCommand)
        self.assertEqual(commands[0].rect, rect)
        self.assertEqual(commands[0].fill, COLORS["pink"])
        self.assertGreater(rect.height, rect.width)
        self.assertAlmostEqual(rect.height / rect.width, SLIDER_HANDLE_ASPECT, places=2)
        self.assertAlmostEqual(rect.x + rect.width / 2.0, 54.0, places=6)
        self.assertEqual(rect.y, track.y)
        self.assertEqual(rect.height, track.height)

        # The handle never leaves the track, even at both ends.
        empty_commands, empty = slider_handle_commands(-10.0, track, COLORS["pink"])
        self.assertEqual(empty.x, track.x)
        _full_commands, full = slider_handle_commands(200.0, track, COLORS["pink"])
        self.assertEqual(full.x + full.width, track.x + track.width)

    def test_tab_bar_keeps_pink_open_to_the_right_edge(self):
        size = Size(200, 30)
        visual = build_tab_bar_visual(size)
        inset = panel_inset()
        border = inset * 2
        self.assertEqual(visual.content_rect, Rect(border, border, 200 - border, 30 - border * 2))
        self.assertEqual(visual.size, size)
        self.assertEqual(len(visual.batch.commands), 8)
        self.assertEqual(visual.batch.commands[0].fill, UI_THEME["bg"])
        self.assertEqual(visual.batch.commands[0].rect, visual.content_rect)

        # The outer black frame draws only the left/top/bottom strips, so the
        # pink background stays open against the settings panel's right edge.
        black = [
            command for command in visual.batch.commands
            if isinstance(command, RectCommand) and command.fill == UI_THEME["border"]
        ]
        self.assertEqual(len(black), 3)
        self.assertIn(Rect(0, 0, inset, 30), [command.rect for command in black])
        self.assertFalse(any(
            command.rect.x + command.rect.width == 200 and command.rect.height == 30
            for command in black
        ))
        mid = [
            command for command in visual.batch.commands
            if isinstance(command, RectCommand) and command.fill == UI_THEME["mid"]
        ]
        self.assertIn(
            Rect(200 - inset, inset, inset, 30 - inset * 2),
            [command.rect for command in mid],
        )


class MixedTextVisualTests(unittest.TestCase):
    def test_mixed_text_splits_digit_and_ui_runs_on_shared_baseline(self):
        rect = Rect(0, 0, 100, 20)
        commands = mixed_text_commands(rect, "ab12", METRICS)

        self.assertIsInstance(commands[0], ClipPush)
        self.assertEqual(commands[0].rect, rect)
        self.assertIsInstance(commands[-1], ClipPop)

        segments = _texts(commands)
        self.assertEqual([segment.text for segment in segments], ["ab", "12"])
        self.assertEqual(segments[0].font, METRICS.default_font)
        self.assertEqual(segments[1].font, METRICS.digit_font)
        self.assertEqual(segments[0].color, COLORS["text"])
        self.assertEqual([segment.z for segment in segments], [4, 4])

        # baseline = round(y + (h + max_ascent - max_descent) / 2) = 14
        self.assertEqual(segments[0].rect, Rect(0, 4, 100, 16))
        self.assertEqual(segments[1].rect, Rect(16, 5, 84, 15))
        self.assertEqual(
            {segment.alignment for segment in segments},
            {int(TextAlignment.LEFT | TextAlignment.TOP)},
        )

    def test_mixed_text_aligns_center_and_right(self):
        rect = Rect(0, 0, 100, 20)
        center = _texts(mixed_text_commands(
            rect, "12", METRICS,
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
        ))
        self.assertEqual(center[0].rect.x, 45)

        right = _texts(mixed_text_commands(
            rect, "12", METRICS, alignment=int(TextAlignment.RIGHT | TextAlignment.VCENTER),
        ))
        self.assertEqual(right[0].rect.x, 90)

    def test_mixed_text_measure_and_elide(self):
        self.assertEqual(mixed_text_width("ab12", METRICS), 26.0)
        self.assertEqual(mixed_text_width("", METRICS), 0.0)

        self.assertEqual(elide_mixed_text("abcdef", 100, METRICS), "abcdef")
        self.assertEqual(elide_mixed_text("abcdef", 40, METRICS), "ab...")
        self.assertEqual(elide_mixed_text("abcdef", 30, METRICS), "...")
        self.assertEqual(elide_mixed_text("abcdef", 20, METRICS), "")
        self.assertEqual(elide_mixed_text("abcdef", 0, METRICS), "")


class MediaPanelVisualTests(unittest.TestCase):
    def test_progress_panel_geometry_and_clamping(self):
        visual = build_progress_panel_visual(progress=0.5, time_text="1:23", metrics=METRICS)
        inset = panel_inset()
        border = inset * 2
        separator_width = scale_px(5, min_abs=1)
        time_width = scale_px(57, min_abs=1)
        slider_width = PROGRESS_PANEL_WIDTH - border * 2 - separator_width - time_width

        self.assertEqual(visual.size, Size(PROGRESS_PANEL_WIDTH, PROGRESS_PANEL_HEIGHT))
        self.assertEqual(
            visual.slider_rect,
            Rect(border, border, slider_width, PROGRESS_PANEL_HEIGHT - border * 2),
        )
        self.assertEqual(
            visual.separator_rect,
            Rect(border + slider_width, inset, separator_width, PROGRESS_PANEL_HEIGHT - inset * 2),
        )
        self.assertEqual(
            visual.time_rect,
            Rect(border + slider_width + separator_width, border, time_width, PROGRESS_PANEL_HEIGHT - border * 2),
        )

        handle_width = round(visual.slider_rect.height / SLIDER_HANDLE_ASPECT)
        self.assertEqual(visual.handle_rect.width, handle_width)
        self.assertEqual(visual.handle_rect.height, visual.slider_rect.height)
        self.assertEqual(
            visual.handle_rect.x,
            border + int(0.5 * slider_width) - handle_width / 2.0,
        )
        self.assertEqual(visual.handle_rect.y, border)

        empty = build_progress_panel_visual(progress=-1.0, time_text="0:00", metrics=METRICS)
        self.assertEqual(empty.handle_rect.x, border)
        full = build_progress_panel_visual(progress=2.0, time_text="9:99", metrics=METRICS)
        self.assertEqual(
            full.handle_rect.x + full.handle_rect.width,
            border + slider_width,
        )

    def test_progress_panel_draws_frame_after_content(self):
        visual = build_progress_panel_visual(progress=0.5, time_text="1:23", metrics=METRICS)
        commands = visual.batch.commands
        frame = [
            command for command in commands
            if isinstance(command, RectCommand) and command.z == 5
        ]
        self.assertEqual(len(frame), 4)
        self.assertEqual(list(commands[-4:]), frame)
        self.assertEqual(_fills(frame), [COLORS["black"]] * 4)

        fills = _fills(commands)
        self.assertIn(COLORS["pink"], fills)
        self.assertIn(UI_THEME["deep_cyan"], fills)
        self.assertIn(UI_THEME["deep_pink"], fills)
        time = _texts(commands)
        self.assertEqual(len(time), 1)
        self.assertEqual(time[0].text, "1:23")
        self.assertEqual(time[0].font, METRICS.digit_font)

    def test_playlist_rows_current_track_and_selection(self):
        queue = tuple((index, f"歌曲{index}") for index in range(9))
        visual = build_playlist_panel_visual(
            queue, METRICS, page=0, selected=2, current_index=1,
        )
        self.assertEqual(len(visual.row_rects), 7)
        self.assertIsNotNone(visual.page_rect)

        commands = visual.batch.commands
        current_row = visual.row_rects[1]
        selected_row = visual.row_rects[2]
        row_fills = [
            (command.rect, command.fill)
            for command in commands
            if isinstance(command, RectCommand) and command.fill in (COLORS["cyan"], UI_THEME["highlight"])
        ]
        self.assertIn((current_row, COLORS["cyan"]), row_fills)
        self.assertIn((selected_row, UI_THEME["highlight"]), row_fills)
        self.assertNotIn((current_row, UI_THEME["highlight"]), row_fills)

        labels = _mixed_texts(commands)
        self.assertIn("> 歌曲2", labels)
        self.assertIn("♪ 歌曲1", labels)

    def test_playlist_clamps_page_and_handles_empty_queue(self):
        queue = tuple((index, f"歌曲{index}") for index in range(9))
        visual = build_playlist_panel_visual(queue, METRICS, page=99)
        self.assertEqual(visual.size, Size(240, (2 + 1) * 20 + 8))
        self.assertEqual(len(visual.row_rects), 2)
        self.assertIsNotNone(visual.page_rect)
        self.assertIn("2/2", _mixed_texts(visual.batch.commands))

        empty = build_playlist_panel_visual((), METRICS)
        self.assertEqual(empty.row_rects, ())
        self.assertIsNone(empty.page_rect)
        self.assertIn("（队列为空）", [command.text for command in _texts(empty.batch.commands)])

    def test_search_result_size_tracks_status_and_width_clamp(self):
        items = tuple((index, f"歌曲 {index}") for index in range(7))
        size = search_result_panel_size(items, METRICS)
        # Five rows per page plus the page indicator row.
        self.assertEqual(size, Size(SEARCH_RESULT_MIN_WIDTH, SEARCH_RESULT_ROW_HEIGHT * 6 + 8))

        wide = tuple((index, "很长的歌曲标题" * 12) for index in range(2))
        self.assertEqual(
            search_result_panel_size(wide, METRICS).width,
            SEARCH_RESULT_MAX_WIDTH,
        )
        self.assertEqual(
            search_result_panel_size((), METRICS, searching=True),
            Size(SEARCH_RESULT_MIN_WIDTH, SEARCH_RESULT_ROW_HEIGHT + 8),
        )
        self.assertEqual(
            search_result_panel_size((), METRICS),
            Size(SEARCH_RESULT_MIN_WIDTH, SEARCH_RESULT_ROW_HEIGHT + 8),
        )

    def test_search_result_visual_rows_highlight_and_page_indicator(self):
        items = tuple((index, f"歌曲 {index}") for index in range(7))
        size = search_result_panel_size(items, METRICS)
        visual = build_search_result_panel_visual(size, items, METRICS, page=0, selected=1)
        self.assertEqual(len(visual.row_rects), SEARCH_RESULT_PAGE_SIZE)
        self.assertIsNotNone(visual.page_rect)

        row_fills = [
            command for command in visual.batch.commands
            if isinstance(command, RectCommand) and command.fill == UI_THEME["highlight"]
        ]
        self.assertEqual([command.rect for command in row_fills], [visual.row_rects[1]])

        self.assertIn("1/2", _mixed_texts(visual.batch.commands))
        page_segments: list[TextCommand] = []
        inside_page = False
        for command in visual.batch.commands:
            if isinstance(command, ClipPush):
                inside_page = command.rect == visual.page_rect
            elif isinstance(command, ClipPop):
                inside_page = False
            elif inside_page and isinstance(command, TextCommand):
                page_segments.append(command)
        self.assertEqual("".join(segment.text for segment in page_segments), "1/2")
        # mixed_text_commands encodes centre alignment in the run's x origin.
        total = mixed_text_width("1/2", METRICS)
        self.assertEqual(
            page_segments[0].rect.x,
            int(round(visual.page_rect.x + (visual.page_rect.width - total) / 2.0)),
        )

    def test_search_result_status_lines_stay_rect_aligned(self):
        size = search_result_panel_size((), METRICS, searching=True)
        searching = build_search_result_panel_visual(size, (), METRICS, searching=True)
        texts = _texts(searching.batch.commands)
        self.assertEqual([command.text for command in texts], [SEARCH_RESULT_SEARCHING_TEXT])
        self.assertEqual(
            texts[0].alignment,
            int(TextAlignment.LEFT | TextAlignment.VCENTER),
        )
        self.assertEqual(texts[0].font, METRICS.default_font)
        self.assertEqual(texts[0].color, UI_THEME["text"])
        self.assertEqual(searching.row_rects, ())
        self.assertIsNone(searching.page_rect)

        empty = build_search_result_panel_visual(size, (), METRICS)
        self.assertEqual(
            [command.text for command in _texts(empty.batch.commands)],
            [SEARCH_RESULT_EMPTY_TEXT],
        )


class SettingsPanelVisualTests(unittest.TestCase):
    def test_ai_settings_panel_watermarks(self):
        visual = build_ai_settings_panel_visual(
            Size(300, 200),
            top_watermark_text="GPU 12GB",
            side_watermark_text="RAM",
        )
        inset = panel_inset()
        border = inset * 2
        self.assertEqual(visual.size, Size(300, 200))
        self.assertEqual(visual.content_rect, Rect(border, border, 300 - border * 2, 200 - border * 2))
        self.assertEqual(len(visual.batch.commands), 5)

        watermarks = _texts(visual.batch.commands)
        self.assertEqual([command.text for command in watermarks], ["GPU 12GB", "RAM"])
        top, side = watermarks
        self.assertEqual(top.alignment, int(TextAlignment.HCENTER | TextAlignment.TOP))
        self.assertEqual(side.alignment, int(TextAlignment.LEFT | TextAlignment.BOTTOM))
        self.assertEqual(top.rect, visual.top_watermark_rect)
        self.assertEqual(side.rect, visual.side_watermark_rect)
        self.assertEqual(top.color, side.color)
        self.assertEqual(top.color.alpha, WATERMARK_ALPHA)
        self.assertEqual(top.font.family, get_digit_font_family())
        self.assertEqual(side.font.family, get_digit_font_family())
        self.assertTrue(top.font.bold and side.font.bold)
        self.assertEqual(
            side.font.pixel_size,
            max(scale_px(12, min_abs=10), int(round(scale_px(46, min_abs=24) * LEFT_WATERMARK_SCALE))),
        )
        self.assertEqual(
            top.font.pixel_size,
            max(scale_px(8, min_abs=6), scale_px(46, min_abs=24) // 3),
        )
        self.assertLess(side.rect.y, visual.content_rect.y + visual.content_rect.height)


if __name__ == "__main__":
    unittest.main()
