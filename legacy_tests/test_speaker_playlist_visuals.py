from __future__ import annotations

import ast
import unittest
from pathlib import Path

from lib.core.render.visuals.application_visuals import (
    build_page_turn_button_visual,
    build_speaker_action_button_visual,
    create_portable_command_hint_metrics,
    glyph_center,
)
from lib.core.render.visuals.speaker_playlist_visuals import (
    build_speaker_playlist_visual,
    speaker_playlist_hit_test,
)
from lib.core.render.visuals.types import Color, FontSpec, Rect


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


class SpeakerActionButtonVisualTests(unittest.TestCase):
    """音响菜单按钮族的共享绘制事实源：几何中心、图标方向与文字对齐。

    这些几何都来自迁移前的 ``QPainter`` 代码，最容易在"抄一遍"时走样：

    - 图标锚点用的是 ``QRect.center()``（``x + (w - 1) // 2``），不是 ``x + w / 2``；
      差一个像素时整枚图标会偏移。
    - 上一页箭头朝左、下一页箭头朝右；方向取反后视觉上仍像"箭头"，只有逐字对照才看得出。
    """

    def test_glyph_center_matches_qt_qrect_center(self):
        self.assertEqual(glyph_center(Rect(4, 4, 32, 24)), (19.0, 15.0))
        self.assertEqual(glyph_center(Rect(4, 4, 76, 24)), (41.0, 15.0))

    def test_speaker_action_button_visual_layers_and_glyph(self):
        font = FontSpec("Arial", 12, True)
        text_visual = build_speaker_action_button_visual(80, 32, "播放列表", font)
        kinds = [type(command).__name__ for command in text_visual.batch.commands]
        self.assertEqual(kinds, ["RectCommand", "RectCommand", "RectCommand", "TextCommand"])
        self.assertEqual(text_visual.batch.commands[-1].text, "播放列表")

        glyph_visual = build_speaker_action_button_visual(40, 32, None, None, glyph="pause")
        kinds = [type(command).__name__ for command in glyph_visual.batch.commands]
        self.assertEqual(kinds, ["RectCommand"] * 3 + ["RectCommand", "RectCommand"])

    def test_pause_glyph_bars_sit_around_the_glyph_center(self):
        visual = build_speaker_action_button_visual(40, 32, None, None, glyph="pause")
        bars = [command for command in visual.batch.commands if type(command).__name__ == "RectCommand"][3:]
        self.assertEqual(len(bars), 2)
        center_x, center_y = glyph_center(Rect(4, 4, 32, 24))
        self.assertAlmostEqual(bars[0].rect.y, center_y - bars[0].rect.height // 2)
        # 两条竖线关于图标中心左右对称，缺口 = 2 * gap。
        gap = (bars[1].rect.x - (bars[0].rect.x + bars[0].rect.width)) / 2
        self.assertAlmostEqual(bars[0].rect.x, center_x - gap - bars[0].rect.width)
        self.assertAlmostEqual(bars[1].rect.x, center_x + gap)

    def test_page_turn_arrow_direction_is_left_for_previous(self):
        """上一页箭头尖必须落在图标中心左侧；下一页落在右侧。"""
        previous = build_page_turn_button_visual(120, 20, direction=-1)
        following = build_page_turn_button_visual(120, 20, direction=1)

        def apex(visual):
            """三角形三个顶点中 y 落在中线上那个就是箭头尖。"""
            segments = visual.batch.commands[-1].segments
            vertices = [(segment.start.x, segment.start.y) for segment in segments]
            tips = [x for x, y in vertices if abs(y - center_y) < 1e-6]
            self.assertEqual(len(tips), 1, "chevron 应只有一个尖端顶点")
            return tips[0]

        center_x, center_y = glyph_center(Rect(4, 4, 112, 12))
        previous_apex = apex(previous)
        following_apex = apex(following)
        self.assertLess(previous_apex, center_x, "上一页箭头应朝左")
        self.assertGreater(following_apex, center_x, "下一页箭头应朝右")
        self.assertAlmostEqual(
            abs(previous_apex - center_x), abs(following_apex - center_x), places=6
        )


class SpeakerMenuStyleFacadeTests(unittest.TestCase):
    """`speaker_menu_style` 去 Qt 后仍是同一份配方。

    它现在只做三件事：把 paintEvent 的 `QRect` 换成核心几何、向描述层要一份批次、
    交给注入的宿主画。这里逐字节比对"门面画的"与"宿主直接画的"，并钉死面板/按钮
    的内容区与三层面板底壳的配色来源（核心 `Color`，不再是 `QColor`）。
    """

    def test_panel_facade_matches_host_batch(self):
        from PyQt5.QtGui import QImage, QPainter
        from PyQt5.QtCore import QRect

        from lib.core.render.visuals import controls as control_visuals
        from lib.script.ui import render_bridge
        from lib.script.ui import speaker_menu_style as style

        width, height = 160, 36

        def render(fn):
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, False)
            out = fn(painter)
            painter.end()
            return bytes(image.bits().asstring(image.byteCount())), out

        facade_bytes, facade_content = render(
            lambda painter: style.paint_speaker_menu_panel(painter, QRect(0, 0, width, height))
        )
        host = render_bridge.create_painter_host()
        spec = control_visuals.SpeakerPanelSpec(width, height).build_visual()

        def host_paint(painter):
            host.render(spec.batch, painter)
            return host.qrect(spec.content_rect)

        host_bytes, host_content = render(host_paint)
        self.assertEqual(facade_bytes, host_bytes)
        self.assertEqual(
            (facade_content.x(), facade_content.y(), facade_content.width(), facade_content.height()),
            (host_content.x(), host_content.y(), host_content.width(), host_content.height()),
        )
        self.assertEqual((facade_content.x(), facade_content.y()), (4, 4))

    def test_action_button_facade_matches_host_batch_for_every_state(self):
        from PyQt5.QtGui import QImage, QPainter
        from PyQt5.QtCore import QRect

        from lib.core.render.visuals import controls as control_visuals
        from lib.script.ui import render_bridge
        from lib.script.ui import speaker_menu_style as style

        width, height = 80, 32

        def render(fn):
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, False)
            out = fn(painter)
            painter.end()
            return bytes(image.bits().asstring(image.byteCount())), out

        for hovered, pressed in ((False, False), (True, False), (False, True), (True, True)):
            facade_bytes, facade_content = render(
                lambda painter, h=hovered, p=pressed: style.paint_speaker_action_button(
                    painter, QRect(0, 0, width, height), hovered=h, pressed=p
                )
            )
            host = render_bridge.create_painter_host()
            spec = control_visuals.SpeakerActionButtonSpec(
                width, height, hovered=hovered, pressed=pressed
            ).build_visual()

            def host_paint(painter, batch=spec.batch, content=spec.content_rect):
                host.render(batch, painter)
                return host.qrect(content)

            host_bytes, host_content = render(host_paint)
            self.assertEqual(facade_bytes, host_bytes, (hovered, pressed))
            self.assertEqual(
                (facade_content.x(), facade_content.y(), facade_content.width(), facade_content.height()),
                (host_content.x(), host_content.y(), host_content.width(), host_content.height()),
            )

    def test_facade_matches_the_shared_recipe_at_a_non_zero_origin(self):
        """非零原点也要与 ``panel_visuals`` 的共享配方逐字节一致。

        搜索框在同一个 painter 上并排画输入区（``x = 0``）和按钮
        （``x = _INPUT_W``）。只测 ``QRect(0, 0, w, h)`` 看不见"原点被丢掉"的回归，
        所以这里专门用带偏移的矩形打一次对照。
        """
        from PyQt5.QtCore import QRect
        from PyQt5.QtGui import QImage, QPainter

        from lib.core.render.visuals.commands import DrawBatch
        from lib.core.render.visuals.panel_visuals import (
            action_button_commands,
            panel_shell_commands,
        )
        from lib.script.ui import render_bridge
        from lib.script.ui import speaker_menu_style as style

        canvas_w, canvas_h = 240, 36
        rect = QRect(160, 8, 80, 32)

        def render(fn):
            image = QImage(canvas_w, canvas_h, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, False)
            out = fn(painter)
            painter.end()
            return bytes(image.bits().asstring(image.byteCount())), out

        facade_bytes, facade_content = render(
            lambda painter: style.paint_speaker_action_button(
                painter, rect, hovered=True, pressed=True
            )
        )

        host = render_bridge.create_painter_host()
        commands, content = action_button_commands(
            Rect(rect.x(), rect.y(), rect.width(), rect.height()),
            None,
            state="pressed",
            inset=style._LAYER,
        )
        expected_bytes, _ = render(
            lambda painter: host.render(DrawBatch(tuple(commands)), painter)
        )

        self.assertEqual(facade_bytes, expected_bytes)
        self.assertEqual(
            (facade_content.x(), facade_content.y()),
            (content.x, content.y),
        )
        # hover + pressed 比静止态多缩一圈（``inset * 3``：160+6 / 8+6）。
        self.assertEqual((facade_content.x(), facade_content.y()), (166, 14))

        panel_facade_bytes, panel_facade_content = render(
            lambda painter: style.paint_speaker_menu_panel(painter, rect)
        )
        panel_commands, panel_content = panel_shell_commands(
            Rect(rect.x(), rect.y(), rect.width(), rect.height()),
            inset=style._LAYER,
        )
        panel_expected_bytes, _ = render(
            lambda painter: host.render(DrawBatch(tuple(panel_commands)), painter)
        )
        self.assertEqual(panel_facade_bytes, panel_expected_bytes)
        self.assertEqual(
            (panel_facade_content.x(), panel_facade_content.y()),
            (panel_content.x, panel_content.y),
        )

    def test_facade_paints_the_button_at_the_given_origin(self):
        """门面必须画在调用方给的 ``QRect`` 原点，而不是永远画在 (0, 0)。

        丢掉原点会把按钮画到输入区上（``x = 0..80``），被输入框盖住后看起来就像
        按钮消失了——这正是搜索框"搜索歌曲按钮不见"的真实回归。
        """
        from PyQt5.QtCore import QRect
        from PyQt5.QtGui import QImage, QPainter

        from lib.script.ui import speaker_menu_style as style

        canvas_w, canvas_h = 240, 36
        origin_x, button_w = 160, 80
        image = QImage(canvas_w, canvas_h, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, False)
        content = style.paint_speaker_action_button(
            painter, QRect(origin_x, 0, button_w, canvas_h)
        )
        painter.end()

        self.assertEqual((content.x(), content.y()), (origin_x + 4, 4))
        self.assertEqual(image.pixel(origin_x + button_w // 2, canvas_h // 2) & 0xFFFFFF, 0xFFB6C1)
        self.assertEqual(image.pixel(origin_x - button_w // 2, canvas_h // 2), 0)

    def test_painter_host_colour_boundary_accepts_tokens_hex_and_colours(self):
        """共享色板与工作台主题令牌两种颜色事实都要能过边界。

        工作台主题给的是 `#rrggbb` **文本**；如果它被当成通道元组拆开，就会在
        `int('#', 10)` 上炸掉——这正是本轮真实踩到的回归（`workbench_components`
        的 about 按钮）。这里把三种输入形态一起钉死。
        """
        from lib.script.ui import render_bridge

        self.assertEqual(render_bridge.painter_color(Color(255, 182, 193)).name(), "#ffb6c1")
        self.assertEqual(render_bridge.painter_color((255, 0, 0)).name(), "#ff0000")
        self.assertEqual(render_bridge.painter_color("#8cd2ff").name(), "#8cd2ff")
        self.assertEqual(render_bridge.painter_color(" #FF0000 ").name(), "#ff0000")
        with self.assertRaises(ValueError):
            render_bridge.painter_color("not-a-colour")

    def test_speaker_menu_style_is_qt_free_and_colours_are_core_values(self):
        from lib.script.ui import speaker_menu_style as style

        tree = ast.parse(Path(style.__file__).read_text(encoding="utf-8-sig"))
        qt_imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertFalse(
            [name for name in qt_imports if name == "PyQt5" or name.startswith("PyQt5.")]
        )
        self.assertIsInstance(style._C_BG, Color)
        self.assertIsInstance(style._C_ACTION_TEXT, Color)
        self.assertIsInstance(style._C_ENTRY_BG, Color)
        self.assertEqual(style._C_ENTRY_BG, Color(255, 255, 255))
        self.assertEqual(style._C_ACTION_BG, Color(255, 182, 193))
        self.assertEqual(style.qt_color_name("border"), "#000000")
        self.assertEqual(style.qt_color_name("bg"), "#ffb6c1")


if __name__ == "__main__":
    unittest.main()
