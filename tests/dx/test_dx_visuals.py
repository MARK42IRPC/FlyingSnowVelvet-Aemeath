from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest

from lib.core.dx_bridge.effect_system import DxEffectOverlay, build_effect_batch
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.overlay_window import DxOverlayWindow
from lib.core.dx_bridge.particle_system import DxParticleOverlay, build_particle_batch
from lib.core.dx_bridge.screen import DxScreenProvider
from lib.core.graphics.commands import DrawBatch, EllipseCommand, LineCommand, RectCommand, SpriteCommand, TextCommand
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.types import Color, FontSpec, Point, Rect, Size
from lib.core.event.center import Event, EventType
from lib.core.layer import Layer
from lib.core.dx_bridge.offscreen import DxOffscreenTarget, find_dx_library


class _Particle:
    alive = True
    x = 10.0
    y = 12.0
    _render_x = 10.0
    _render_y = 12.0
    life = 1.0
    max_life = 1.0
    size = 4.0
    color = Color(10, 20, 30)
    layer = Layer.PARTICLE
    z = 0
    _draw_order = 1

    def update(self):
        self.x += 1.0


class _Line(_Particle):
    is_line = True
    length = 8.0
    line_dx = 1.0
    line_dy = 0.0
    pen_width = 2.0


class _Text(_Particle):
    is_text = True
    text = "DX"
    font = FontSpec("Microsoft YaHei", 14)
    bloom = 2.0


class _Effect:
    alive = True
    x = 8.0
    y = 8.0
    opacity = 1.0
    scale = 1.0
    rotation = 0.0
    layer = Layer.EFFECT
    z = 0
    _draw_order = 1

    def update(self):
        return None


class _TextEffect(_Effect):
    text = "Ready"
    font_size = 16
    font_type = "ui"
    font_bold = False
    color = (255, 255, 255)
    glow = 1.0
    glow_color = (100, 200, 255)


class _FakeOverlayWindow:
    def __init__(self):
        self.geometry = Rect(100, 200, 300, 200)
        self.window_host = None
        self.batches = []
        self.flush_count = 0
        self.cleaned = False

    def submit(self, batch):
        self.batches.append(batch)

    def flush_immediately(self):
        self.flush_count += 1

    def cleanup(self):
        self.cleaned = True


class DxVisualDeclarationTests(unittest.TestCase):
    def test_sprite_target_size_is_independent_from_repaint_viewport(self):
        frame = RasterFrame(200, 200, bytes((255, 255, 255, 255)) * 200 * 200)
        command = SpriteCommand(
            "pet",
            1,
            0,
            frame,
            Point(),
            1.0,
            False,
            1.0,
            int(Layer.MAIN_PET),
            0,
            0,
            target_size=Size(150, 150),
        )

        self.assertEqual(
            DxOffscreenTarget._target_rect(command, Rect(0, 0, 300, 300)),
            (0, 0, 150, 150),
        )

    def test_visual_adapters_import_with_pyqt_blocked(self):
        script = textwrap.dedent(
            """
            import builtins
            original_import = builtins.__import__
            def blocked(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked")
                return original_import(name, *args, **kwargs)
            builtins.__import__ = blocked
            from lib.core.dx_bridge.effect_system import build_effect_batch
            from lib.core.dx_bridge.overlay_window import DxOverlayWindow
            from lib.core.dx_bridge.particle_system import build_particle_batch
            assert callable(build_effect_batch)
            assert callable(build_particle_batch)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_particle_shapes_convert_without_qt(self):
        batch = build_particle_batch([_Particle(), _Line(), _Text()])
        self.assertTrue(any(isinstance(item, RectCommand) for item in batch.commands))
        self.assertTrue(any(isinstance(item, LineCommand) for item in batch.commands))
        self.assertTrue(any(isinstance(item, TextCommand) for item in batch.commands))

    def test_effect_text_and_sprite_convert_to_one_batch(self):
        frame = RasterFrame(2, 2, bytes((255, 0, 0, 255)) * 4)
        resource = ImageResource("effect:test", (frame,))
        image_effect = _Effect()
        image_effect._dx_resource = resource
        image_effect._render_x = 3.0
        image_effect._render_y = 4.0
        image_effect.rotation = 20.0
        batch = build_effect_batch([_TextEffect(), image_effect])
        self.assertTrue(any(isinstance(item, SpriteCommand) for item in batch.commands))
        self.assertTrue(any(isinstance(item, TextCommand) for item in batch.commands))
        self.assertEqual(batch.resource_revisions[0].resource_id, resource.resource_id)

    def test_particle_controller_drains_updates_and_cleans(self):
        class Script:
            def create_particles(self, area_type, area_data):
                self.area_data = area_data
                return [_Particle()]

        script = Script()
        manager = type("Manager", (), {"get_script": lambda _self, _id: script})()
        window = _FakeOverlayWindow()
        overlay = DxParticleOverlay(
            DxLoopContext(),
            window=window,
            particle_manager=manager,
        )
        try:
            request = Event(EventType.PARTICLE_REQUEST, {
                "particle_id": "test",
                "area_type": "point",
                "area_data": (110, 220),
            })
            overlay._on_particle_request(request)
            overlay._on_tick(Event(EventType.TICK))
            overlay._on_frame(Event(EventType.FRAME, {"tick_alpha": 1.0}))
            self.assertEqual(script.area_data, (10.0, 20.0))
            self.assertTrue(window.batches[-1].commands)
        finally:
            overlay.cleanup()
            overlay.cleanup()
        self.assertTrue(window.cleaned)

    def test_effect_controller_drains_text_effect_and_cleans(self):
        class Script:
            def create_effects(self, **kwargs):
                self.anchor_data = kwargs["anchor_data"]
                return [_TextEffect()]

        script = Script()
        manager = type("Manager", (), {"get_script": lambda _self, _id: script})()
        window = _FakeOverlayWindow()
        overlay = DxEffectOverlay(
            DxLoopContext(),
            window=window,
            effect_manager=manager,
        )
        try:
            overlay._on_effect_request(Event(EventType.EFFECT_REQUEST, {
                "effect_id": "test",
                "anchor_type": "point",
                "anchor_data": (110, 220),
            }))
            overlay._on_tick(Event(EventType.TICK))
            overlay._on_frame(Event(EventType.FRAME, {"tick_alpha": 1.0}))
            self.assertEqual(script.anchor_data, (10.0, 20.0))
            self.assertTrue(window.batches[-1].commands)
        finally:
            overlay.cleanup()
            overlay.cleanup()
        self.assertTrue(window.cleaned)


@unittest.skipUnless(
    os.name == "nt" and find_dx_library() is not None,
    "DX overlay integration requires Windows and a built DX DLL",
)
class DxOverlayWindowTests(unittest.TestCase):
    def test_overlay_submits_and_cleans_a_transparent_batch(self):
        context = DxLoopContext()
        provider = DxScreenProvider(
            monitor_loader=lambda: (),
            fallback=Rect(0, 0, 12, 10),
        )
        overlay = DxOverlayWindow(
            context,
            Layer.PARTICLE,
            name="DxOverlayTest",
            screen_provider=provider,
            warp=True,
        )
        try:
            overlay.submit(DrawBatch((RectCommand(Rect(0, 0, 12, 10), fill=Color(20, 30, 40)),)))
            context.run_once()
            self.assertTrue(overlay.window_host.is_visible())
            self.assertEqual(overlay.window_host.readback_rgba()[:4], bytes((20, 30, 40, 255)))
            overlay.flush_immediately()
            self.assertFalse(overlay.window_host.is_visible())
        finally:
            overlay.cleanup()
            overlay.cleanup()
        self.assertFalse(overlay.window_host is not None)


if __name__ == "__main__":
    unittest.main()
