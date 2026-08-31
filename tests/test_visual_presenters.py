from __future__ import annotations

import ast
import random
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config.config import COMMAND_DIALOG, UI
from lib.core.dx_bridge.effect_system import DxEffectOverlay, build_effect_batch as build_dx_effect_batch
from lib.core.dx_bridge.particle_system import build_particle_batch as build_dx_particle_batch
from lib.core.event.center import Event, EventType
from lib.core.graphics.commands import RectCommand, TextCommand, TransformPush
from lib.core.graphics.application_visuals import (
    COMMAND_HINT_DEFAULT_ITEMS,
    build_bubble_visual,
    build_command_hint_visual,
    build_notice_panel_visual,
    build_command_action_panel_visual,
    build_mic_stt_indicator_visual,
    build_tooltip_visual,
    resolve_command_action_panel_layout,
    build_qr_panel_visual,
    build_rect_action_button_visual,
    command_hint_default_pick,
    create_portable_command_hint_metrics,
    qr_panel_size,
    resolve_bubble_geometry,
    resolve_qr_panel_layout,
)
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.types import Color, FontSpec, Point, Rect, Size
from lib.core.graphics.visuals import (
    build_command_panel_batch,
    build_command_shell_batch,
    build_effect_batch,
    build_particle_batch,
    load_effect_resource,
    resolve_command_panel_geometry,
    resolve_speaker_scale,
    sample_motor_jitter,
    build_world_object_batch,
    update_speaker_intensity,
)
from lib.core.layer import Layer


class _Particle:
    alive = True
    x = 10.0
    y = 12.0
    life = 1.0
    max_life = 1.0
    size = 4.0
    color = Color(10, 20, 30)
    layer = Layer.PARTICLE
    z = 0
    _draw_order = 1


class _TextParticle(_Particle):
    is_text = True
    text = "shared"
    font = FontSpec("Microsoft YaHei", 14)
    bloom = 2.0


class _TextEffect:
    alive = True
    x = 8.0
    y = 8.0
    opacity = 1.0
    scale = 1.0
    rotation = 0.0
    text = "Ready"
    font_size = 16
    font_type = "ui"
    color = (255, 255, 255)
    glow = 1.0
    glow_color = (100, 200, 255)
    layer = Layer.EFFECT
    z = 0
    _draw_order = 1


class _BubbleMetrics:
    default_font = FontSpec("UI", 12, True)
    digit_font = FontSpec("Digits", 12)
    default_line_height = 14
    digit_line_height = 12
    default_ascent = 10
    default_descent = 3
    digit_ascent = 9
    digit_descent = 2

    def measure(self, text: str, *, digit: bool = False) -> float:
        return len(text) * (5 if digit else 8)


class _CommandHintMetrics:
    default_font = FontSpec("UI", 12, True)
    digit_font = FontSpec("Digits", 12)
    side_font = FontSpec("Digits", 15)
    default_ascent = 10
    default_descent = 3
    digit_ascent = 9
    digit_descent = 2

    def measure(self, text: str, *, digit: bool = False, side: bool = False) -> float:
        return len(text) * (7 if side else (5 if digit else 8))


class VisualPresenterTests(unittest.TestCase):
    def test_mic_indicator_presenter_has_stable_active_state(self):
        idle = build_mic_stt_indicator_visual(speech_active=False)
        active = build_mic_stt_indicator_visual(speech_active=True)

        self.assertEqual(idle.size, Size(24, 24))
        self.assertEqual(len(idle.batch.commands), 8)
        self.assertEqual(idle.batch.commands[2].fill, Color(255, 182, 193))
        self.assertEqual(active.batch.commands[2].fill, Color(255, 230, 240))

    def test_tooltip_presenter_owns_wrap_theme_and_opacity(self):
        visual = build_tooltip_visual(
            "打开功能123",
            _BubbleMetrics(),
            max_text_width=80,
            opacity=0.8,
        )

        self.assertGreater(visual.size.width, visual.content_rect.width)
        self.assertEqual(visual.batch.commands[0].fill, Color(0, 0, 0))
        self.assertEqual(visual.batch.commands[0].alpha, 0.8)
        self.assertEqual(visual.batch.commands[2].fill, Color(255, 182, 193))
        self.assertTrue(any(
            isinstance(command, TextCommand) and command.text == "123"
            for command in visual.batch.commands
        ))

    def test_bubble_presenter_resolves_layout_and_mixed_font_commands(self):
        visual = build_bubble_visual(
            "AB12",
            _BubbleMetrics(),
            max_width=100,
            padding=12,
            border_width=2,
        )

        self.assertEqual((visual.size.width, visual.size.height), (50, 38))
        self.assertEqual(visual.lines, ("AB12",))
        self.assertEqual(visual.batch.commands[0].fill, Color(0, 0, 0))
        self.assertEqual(visual.batch.commands[1].fill, Color(173, 216, 230))
        self.assertEqual(visual.batch.commands[2].fill, Color(255, 182, 193))
        text_commands = [item for item in visual.batch.commands if isinstance(item, TextCommand)]
        self.assertEqual([item.text for item in text_commands], ["AB", "12"])
        self.assertEqual([item.font.family for item in text_commands], ["UI", "Digits"])

    def test_bubble_geometry_anchors_and_clamps_to_screen(self):
        size = Size(100, 40)
        screen = Rect(0, 0, 800, 600)
        self.assertEqual(
            resolve_bubble_geometry(Point(400, 300), size, screen),
            Rect(350, 260, 100, 40),
        )
        self.assertEqual(
            resolve_bubble_geometry(Point(20, 10), size, screen),
            Rect(0, 0, 100, 40),
        )

    def test_command_hint_presenter_owns_rows_selection_and_paging(self):
        default = build_command_hint_visual(
            "default",
            ("/-在CMD窗口中执行命令", "#-执行玩法命令", "聊天-与爱弥斯聊天"),
            1,
            0,
            _CommandHintMetrics(),
        )
        self.assertEqual(default.size, Size(240, 78))
        self.assertEqual(len(default.row_rects), 3)
        self.assertIsNone(default.page_indicator_rect)
        self.assertEqual(default.batch.commands[0].fill, Color(0, 0, 0))
        self.assertTrue(any(
            isinstance(command, TextCommand) and command.text == "RUNcmd"
            for command in default.batch.commands
        ))

        paged = build_command_hint_visual(
            "hash",
            tuple((f"命令{i}", "[参数]", "说明") for i in range(7)),
            0,
            1,
            _CommandHintMetrics(),
        )
        self.assertEqual(paged.size, Size(240, 68))
        self.assertEqual(len(paged.row_rects), 2)
        self.assertIsNotNone(paged.page_indicator_rect)
        page_segments = [
            command.text
            for command in paged.batch.commands
            if isinstance(command, TextCommand)
        ]
        self.assertIn("/", page_segments)

        portable = create_portable_command_hint_metrics()
        native_default = build_command_hint_visual(
            "default", COMMAND_HINT_DEFAULT_ITEMS, 0, 0, portable
        )
        self.assertEqual(len(native_default.row_rects), 3)
        self.assertGreater(portable.measure("命令123"), 0)
        self.assertEqual(command_hint_default_pick(1), "#")

    def test_rect_action_button_presenter_owns_hover_layers(self):
        font = FontSpec("UI", 12, True)
        normal = build_rect_action_button_visual(80, 32, "关闭桌宠", font)
        hovered = build_rect_action_button_visual(
            80,
            32,
            "关闭桌宠",
            font,
            hovered=True,
        )

        self.assertEqual(len(normal.batch.commands), 4)
        self.assertEqual(normal.batch.commands[2].rect, Rect(4, 4, 72, 24))
        self.assertEqual(len(hovered.batch.commands), 5)
        self.assertEqual(hovered.batch.commands[2].fill, Color(255, 149, 164))
        self.assertEqual(hovered.batch.commands[3].rect, Rect(6, 6, 68, 20))

    def test_qr_panel_uses_qt_reference_layout_and_theme(self):
        width, height = qr_panel_size()
        resource = ImageResource(
            "qr:test",
            (RasterFrame(2, 1, bytes((20, 30, 40, 255)) * 2),),
        )

        visual = build_qr_panel_visual("扫码登录", "等待扫码", "加载中", resource)
        layout = resolve_qr_panel_layout()

        self.assertEqual(visual.size, layout.size)
        self.assertEqual((width, height), (320, 430))
        self.assertEqual(visual.batch.commands[0].fill, Color(0, 0, 0))
        self.assertEqual(visual.batch.commands[1].fill, Color(173, 216, 230))
        self.assertEqual(visual.batch.commands[2].fill, Color(255, 182, 193))
        sprite = next(item for item in visual.batch.commands if hasattr(item, "frame"))
        self.assertEqual(sprite.target_size.width, layout.qr_rect.width)
        self.assertEqual(sprite.target_size.height, layout.qr_rect.width / 2)

    def test_notice_and_world_object_visuals_are_shared_batches(self):
        notice = build_notice_panel_visual("服务已就绪")
        self.assertTrue(notice.batch.commands)
        resource = ImageResource(
            "world:test",
            (RasterFrame(3, 2, bytes((1, 2, 3, 255)) * 6),),
        )
        world = build_world_object_batch(resource, 0, alpha=0.5, flipped=True, order=7)
        command = world.commands[0]
        self.assertEqual(command.alpha, 0.5)
        self.assertTrue(command.flipped)
        self.assertEqual((command.target_size.width, command.target_size.height), (3, 2))

        clock = build_world_object_batch(
            resource,
            0,
            object_type="clock",
            countdown_centis=6500,
        )
        clock_text = next(item for item in clock.commands if isinstance(item, TextCommand))
        self.assertEqual(clock_text.text, "01:05")
        self.assertTrue(clock_text.font.bold)
        self.assertEqual(clock_text.color, Color(35, 76, 128))

        speaker = build_world_object_batch(
            resource,
            0,
            object_type="speaker",
            scale_x=1.05,
            scale_y=0.95,
        )
        self.assertIsInstance(speaker.commands[0], TransformPush)

    def test_command_action_panel_has_complete_qt_baseline_geometry(self):
        command_rect = Rect(100, 200, 240, 36)
        layout = resolve_command_action_panel_layout(command_rect)
        self.assertEqual(tuple(name for name, _rect in layout.rects), (
            "clickthrough", "scale_up", "scale_down", "close",
            "launch_wuwa", "chat_mode", "interaction_mode", "more_functions",
        ))
        self.assertEqual(layout.rects[0][1], Rect(100, 166, 80, 32))
        rects = dict(layout.rects)
        self.assertEqual(rects["launch_wuwa"], Rect(100, 134, 80, 32))
        self.assertEqual(rects["more_functions"], Rect(100, 102, 80, 32))
        self.assertEqual(layout.size, Size(240, 96))
        normal = build_command_action_panel_visual(command_rect)
        hovered = build_command_action_panel_visual(command_rect, hovered="close")
        self.assertGreater(len(hovered.batch.commands), len(normal.batch.commands))
        self.assertEqual(normal.batch.commands[0].fill, hovered.batch.commands[0].fill)

    def test_world_object_motion_visual_algorithms_are_shared_and_bounded(self):
        idle_rng = random.Random(42)
        moving_rng = random.Random(42)

        idle_samples = [sample_motor_jitter(False, rng=idle_rng) for _ in range(32)]
        moving_samples = [sample_motor_jitter(True, rng=moving_rng) for _ in range(32)]
        replay_rng = random.Random(42)

        self.assertTrue(all(abs(item.x) <= 1 and abs(item.y) <= 1 for item in idle_samples))
        self.assertTrue(all(abs(item.x) <= 2 and abs(item.y) <= 2 for item in moving_samples))
        self.assertEqual(
            idle_samples,
            [sample_motor_jitter(False, rng=replay_rng) for _ in range(32)],
        )

        with patch.dict(
            "lib.core.graphics.visuals.SPEAKER_AUDIO",
            {
                "ema_attack": 0.35,
                "ema_decay": 0.08,
                "scale_exp": 2.0,
                "scale_range": 0.1,
                "response_gain": 4.0,
            },
            clear=True,
        ):
            self.assertAlmostEqual(update_speaker_intensity(0.2, 0.8), 0.41)
            self.assertAlmostEqual(update_speaker_intensity(0.8, 0.2), 0.752)
            self.assertEqual(update_speaker_intensity(-1.0, None), 0.0)
            self.assertEqual(resolve_speaker_scale(-1.0), (1.0, 1.0))
            self.assertEqual(resolve_speaker_scale(0.25), (1.025, 0.975))
            self.assertEqual(resolve_speaker_scale(2.0), (1.1, 0.9))

    def test_dx_bridge_does_not_construct_product_draw_commands_or_import_qt(self):
        repo = Path(__file__).resolve().parents[1]
        forbidden = {
            "Color",
            "FontSpec",
            "EllipseCommand",
            "LineCommand",
            "RectCommand",
            "SpriteCommand",
            "TextCommand",
        }
        violations = []
        for path in (repo / "lib/core/dx_bridge").glob("*.py"):
            relative = path.relative_to(repo).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    if isinstance(node, ast.ImportFrom) and node.module:
                        names.append(node.module)
                    if any(name == "PyQt5" or name.startswith("PyQt5.") for name in names):
                        violations.append(f"{relative}:{node.lineno}:PyQt5")
                    continue
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in forbidden:
                    violations.append(f"{relative}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_qt_overlay_hosts_only_use_painter_for_technical_clear(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = {"drawPixmap", "drawText", "drawLine", "drawRect", "drawEllipse", "setTransform"}
        violations = []
        for relative in (
            "lib/core/qt_bridge/effect_system.py",
            "lib/core/qt_bridge/particle_system.py",
        ):
            path = root / relative
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in forbidden:
                    violations.append(f"{relative}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_qt_bubble_does_not_construct_or_paint_product_visuals(self):
        relative = "lib/script/ui/bubble.py"
        path = Path(__file__).resolve().parents[1] / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = {
            "Color",
            "EllipseCommand",
            "LineCommand",
            "RectCommand",
            "SpriteCommand",
            "TextCommand",
            "drawText",
            "draw_mixed_text",
            "fillRect",
        }
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in forbidden:
                violations.append(f"{relative}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_qt_command_hint_only_measures_and_executes_shared_visual(self):
        relative = "lib/script/ui/command_hint_box.py"
        path = Path(__file__).resolve().parents[1] / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = {
            "Color",
            "RectCommand",
            "TextCommand",
            "drawText",
            "draw_mixed_text",
            "fillRect",
        }
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in forbidden:
                violations.append(f"{relative}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_qt_rect_action_style_only_executes_shared_visual(self):
        relative = "lib/script/ui/rect_action_button_style.py"
        path = Path(__file__).resolve().parents[1] / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = {
            "Color",
            "RectCommand",
            "TextCommand",
            "drawText",
            "fillRect",
        }
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in forbidden:
                violations.append(f"{relative}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_qt_world_objects_only_execute_shared_batches(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = {"drawPixmap", "drawText", "setTransform", "Color", "FontSpec", "TextCommand", "SpriteCommand"}
        violations = []
        for path in (root / "lib/script/ui/world_objects").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in forbidden:
                    violations.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual(violations, [])

    def test_dx_exports_delegate_to_shared_presenters(self):
        particles = [_Particle(), _TextParticle()]
        effects = [_TextEffect()]

        self.assertEqual(build_dx_particle_batch(particles), build_particle_batch(particles))
        self.assertEqual(build_dx_effect_batch(effects), build_effect_batch(effects))

    def test_no_fade_shape_matches_qt_opaque_semantics(self):
        particle = _Particle()
        particle.no_fade = True
        particle.alpha_override = 12

        command = build_particle_batch([particle]).commands[0]

        self.assertIsInstance(command, RectCommand)
        self.assertEqual(command.alpha, 1.0)

    def test_text_override_and_bloom_match_qt_budget(self):
        particle = _TextParticle()
        particle.alpha_override = 128

        commands = build_particle_batch([particle]).commands
        text_commands = [item for item in commands if isinstance(item, TextCommand)]

        self.assertEqual(len(text_commands), 25)
        self.assertAlmostEqual(text_commands[-1].alpha, 128 / 255.0)
        self.assertAlmostEqual(text_commands[0].alpha, (128 / 255.0) * 0.30 * 0.10)
        self.assertEqual(text_commands[0].color, Color(161, 165, 169))

    def test_effect_glow_uses_qt_eight_direction_pattern(self):
        commands = build_effect_batch([_TextEffect()]).commands
        text_commands = [item for item in commands if isinstance(item, TextCommand)]

        self.assertEqual(len(text_commands), 25)
        self.assertAlmostEqual(text_commands[0].alpha, 0.30 * 0.10)

    def test_effect_feather_multiplies_qt_edge_masks(self):
        frame = RasterFrame(4, 4, bytes((10, 20, 30, 255)) * 16)
        resource = ImageResource("effect:test", (frame,))
        with patch("lib.core.graphics.visuals.load_image_resource", return_value=resource):
            result = load_effect_resource(
                "unused.png",
                {"edge_feather": True, "feather_ratio": 0.25},
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.frames[0].pixels[3], 64)

    def test_masked_size_only_applies_to_qt_feather_path(self):
        frame = RasterFrame(2, 2, bytes((10, 20, 30, 255)) * 4)
        resource = ImageResource("effect:test", (frame,))
        with (
            patch("lib.core.graphics.visuals.load_image_resource", return_value=resource),
            patch("lib.core.graphics.visuals.resize_image_resource") as resize,
        ):
            result = load_effect_resource(
                "unused.png",
                {"edge_feather": False, "masked_output_size": (20, 20)},
            )

        self.assertIs(result, resource)
        resize.assert_not_called()

    def test_dx_effect_fallback_interpolation_matches_qt(self):
        effect = _TextEffect()
        effect._tick_prev_x = 0.0
        effect._tick_prev_y = 4.0
        effect._tick_prev_opacity = 0.2
        effect._tick_prev_scale = 1.0
        effect._tick_prev_rotation = 10.0
        effect.x = 8.0
        effect.y = 12.0
        effect.opacity = 1.0
        effect.scale = 2.0
        effect.rotation = 30.0

        submitted = []
        overlay = DxEffectOverlay.__new__(DxEffectOverlay)
        overlay._paused = False
        overlay._cleanup_done = False
        overlay._effects = [effect]
        overlay._window = SimpleNamespace(submit=submitted.append)

        overlay._on_frame(Event(EventType.FRAME, {"tick_alpha": 0.25}))

        self.assertEqual((effect._render_x, effect._render_y), (2.0, 6.0))
        self.assertAlmostEqual(effect._render_opacity, 0.4)
        self.assertAlmostEqual(effect._render_scale, 1.25)
        self.assertAlmostEqual(effect._render_rotation, 15.0)
        self.assertEqual(len(submitted), 1)

    def test_command_panel_uses_qt_shell_tokens_and_dimensions(self):
        width = int(UI["cmd_window_width"])
        height = int(UI["cmd_window_height"])
        shell = build_command_shell_batch(width, height)
        panel = build_command_panel_batch(width, height, "hello")

        self.assertEqual(shell.commands[0].fill, Color(0, 0, 0))
        self.assertEqual(shell.commands[1].fill, Color(173, 216, 230))
        self.assertEqual(shell.commands[2].fill, Color(255, 182, 193))
        self.assertEqual(panel.commands[:3], shell.commands)
        field = panel.commands[3]
        self.assertEqual(field.rect, Rect(4, 4, width - 8, height - 8))

    def test_command_panel_geometry_anchors_and_flips_at_screen_edge(self):
        screen = Rect(0, 0, 800, 600)
        size = (int(UI["cmd_window_width"]), int(UI["cmd_window_height"]))
        offset_x = float(COMMAND_DIALOG["offset_x"])

        right = resolve_command_panel_geometry(Rect(100, 200, 150, 150), size, screen)
        self.assertEqual(right, Rect(250 + offset_x, 257, size[0], size[1]))

        left = resolve_command_panel_geometry(Rect(700, 200, 100, 150), size, screen)
        self.assertEqual(left, Rect(700 - size[0] - offset_x, 257, size[0], size[1]))


if __name__ == "__main__":
    unittest.main()
