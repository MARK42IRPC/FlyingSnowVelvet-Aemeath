from __future__ import annotations

import ctypes
import unittest

from lib.core.dx_bridge import DxBridgeError, DxOffscreenTarget, find_dx_library
from lib.core.dx_bridge.offscreen import (
    FSDX_ABI_VERSION,
    FSDX_STATUS_ABI_MISMATCH,
    FSDX_STATUS_UNSUPPORTED,
    _DrawCommand,
)
from lib.core.graphics.commands import (
    DrawBatch,
    EllipseCommand,
    LineCommand,
    RectCommand,
    ResourceRevision,
    SpriteCommand,
    TextCommand,
)
from lib.core.graphics.resources import RasterFrame
from lib.core.graphics.types import Color, FontSpec, Point, Rect
from lib.core.layer import Layer


def _batch(frame: RasterFrame, *, alpha: float = 1.0, flipped: bool = False) -> DrawBatch:
    return DrawBatch((SpriteCommand(
        resource_id="probe",
        resource_revision=1,
        frame_index=0,
        frame=frame,
        position=Point(0, 0),
        alpha=alpha,
        flipped=flipped,
        scale=1.0,
        layer=int(Layer.MAIN_PET),
        z=0,
        order=1,
    ),))


def _revision_batch(frame: RasterFrame, revision: int) -> DrawBatch:
    command = SpriteCommand(
        resource_id="probe",
        resource_revision=revision,
        frame_index=0,
        frame=frame,
        position=Point(0, 0),
        alpha=1.0,
        flipped=False,
        scale=1.0,
        layer=int(Layer.MAIN_PET),
        z=0,
        order=1,
    )
    return DrawBatch((command,), (ResourceRevision("probe", revision),))


@unittest.skipUnless(find_dx_library() is not None, "build flying_snow_dx.dll to run DX integration tests")
class DxBridgeTests(unittest.TestCase):
    def test_warp_renders_and_reads_back_rgba(self):
        frame = RasterFrame(2, 1, bytes((255, 0, 0, 255, 0, 0, 255, 255)))
        with DxOffscreenTarget(2, 1, warp=True) as target:
            target.render_batch(_batch(frame))
            self.assertEqual(
                target.readback_rgba(),
                bytes((255, 0, 0, 255, 0, 0, 255, 255)),
            )

    def test_warp_preserves_transparent_alpha_and_flip(self):
        frame = RasterFrame(2, 1, bytes((255, 0, 0, 128, 0, 0, 255, 255)))
        with DxOffscreenTarget(2, 1, warp=True) as target:
            target.render_batch(_batch(frame, flipped=True))
            pixels = target.readback_rgba()
            self.assertEqual(pixels[0:4], bytes((0, 0, 255, 255)))
            self.assertEqual(pixels[4:8], bytes((128, 0, 0, 128)))

    def test_resource_revision_replaces_native_bitmap(self):
        red = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
        blue = RasterFrame(1, 1, bytes((0, 0, 255, 255)))
        with DxOffscreenTarget(1, 1, warp=True) as target:
            target.render_batch(_revision_batch(red, 1))
            self.assertEqual(target.readback_rgba(), bytes((255, 0, 0, 255)))
            target.render_batch(_revision_batch(blue, 2))
            self.assertEqual(target.readback_rgba(), bytes((0, 0, 255, 255)))

    def test_rectangle_fill_combines_color_and_command_alpha(self):
        batch = DrawBatch((RectCommand(
            Rect(0, 0, 2, 2),
            fill=Color(200, 100, 50, 128),
            alpha=0.5,
        ),))
        with DxOffscreenTarget(3, 3, warp=True) as target:
            target.render_batch(batch)
            pixels = target.readback_rgba()
            self.assertEqual(pixels[0:4], bytes((50, 25, 13, 64)))
            self.assertEqual(pixels[-4:], bytes((0, 0, 0, 0)))

    def test_rectangle_draws_fill_and_stroke(self):
        batch = DrawBatch((RectCommand(
            Rect(1, 1, 5, 5),
            fill=Color(255, 0, 0),
            stroke=Color(0, 0, 255),
            stroke_width=2,
        ),))
        with DxOffscreenTarget(7, 7, warp=True) as target:
            target.render_batch(batch)
            pixels = target.readback_rgba()
            center_offset = (3 * 7 + 3) * 4
            edge_offset = (3 * 7 + 1) * 4
            self.assertEqual(pixels[center_offset:center_offset + 4], bytes((255, 0, 0, 255)))
            self.assertEqual(pixels[edge_offset:edge_offset + 4], bytes((0, 0, 255, 255)))

    def test_ellipse_fill_preserves_transparent_exterior(self):
        batch = DrawBatch((EllipseCommand(
            Rect(1, 1, 5, 5),
            fill=Color(0, 255, 0),
        ),))
        with DxOffscreenTarget(7, 7, warp=True) as target:
            target.render_batch(batch)
            pixels = target.readback_rgba()
            center_offset = (3 * 7 + 3) * 4
            self.assertEqual(pixels[center_offset:center_offset + 4], bytes((0, 255, 0, 255)))
            self.assertEqual(pixels[0:4], bytes((0, 0, 0, 0)))

    def test_line_draws_stroke_color_and_leaves_background_transparent(self):
        batch = DrawBatch((LineCommand(
            Point(0.5, 0.5),
            Point(4.5, 0.5),
            Color(0, 0, 255),
            width=1,
        ),))
        with DxOffscreenTarget(5, 2, warp=True) as target:
            target.render_batch(batch)
            pixels = target.readback_rgba()
            self.assertEqual(pixels[4:8], bytes((0, 0, 255, 255)))
            self.assertEqual(pixels[5 * 4:6 * 4], bytes((0, 0, 0, 0)))

    def test_mixed_sprite_and_shape_commands_follow_sort_keys(self):
        red = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
        sprite = _batch(red).commands[0]
        top_shape = RectCommand(
            Rect(0, 0, 1, 1),
            fill=Color(0, 255, 0),
            layer=sprite.layer,
            z=sprite.z + 1,
            order=sprite.order,
        )
        with DxOffscreenTarget(1, 1, warp=True) as target:
            target.render_batch(DrawBatch((top_shape, sprite)))
            self.assertEqual(target.readback_rgba(), bytes((0, 255, 0, 255)))

    def test_cleanup_is_idempotent(self):
        target = DxOffscreenTarget(1, 1, warp=True)
        target.cleanup()
        target.cleanup()

    def test_invalid_runtime_handle_has_diagnostic_error(self):
        target = DxOffscreenTarget(1, 1, warp=True)
        try:
            status = int(target._library.fsdx_destroy_runtime(ctypes.c_uint64(0xFFFFFFFF)))
            self.assertNotEqual(status, 0)
            self.assertIn(b"runtime handle", target._library.fsdx_get_last_error())
        finally:
            target.cleanup()

    def test_native_destroy_and_release_are_idempotent(self):
        target = DxOffscreenTarget(1, 1, warp=True)
        frame = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
        command = _batch(frame).commands[0]
        resource_handle = target._register_frame(command)
        first_release = int(target._library.fsdx_release_resource(
            target._runtime,
            ctypes.c_uint64(resource_handle),
        ))
        second_release = int(target._library.fsdx_release_resource(
            target._runtime,
            ctypes.c_uint64(resource_handle),
        ))
        self.assertEqual((first_release, second_release), (0, 0))
        target._resource_handles.clear()
        target._resource_revisions.clear()
        first_destroy = int(target._library.fsdx_destroy_runtime(target._runtime))
        second_destroy = int(target._library.fsdx_destroy_runtime(target._runtime))
        self.assertEqual((first_destroy, second_destroy), (0, 0))
        target._closed = True

    def test_offscreen_prototype_rejects_text_commands(self):
        with DxOffscreenTarget(2, 2, warp=True) as target:
            with self.assertRaisesRegex(DxBridgeError, "text, clip, and transform"):
                target.render_batch(DrawBatch((TextCommand(
                    "probe",
                    FontSpec("Segoe UI", 12),
                    Color(255, 255, 255),
                    Rect(0, 0, 2, 2),
                ),)))

    def test_native_unknown_command_type_is_reported_as_unsupported(self):
        with DxOffscreenTarget(1, 1, warp=True) as target:
            command = _DrawCommand()
            command.abi_version = FSDX_ABI_VERSION
            command.struct_size = ctypes.sizeof(_DrawCommand)
            command.type = 0xFFFFFFFF
            command.alpha = 1.0
            status = int(target._library.fsdx_submit_frame(
                target._runtime,
                ctypes.byref(command),
                ctypes.c_uint32(1),
            ))
            self.assertEqual(status, FSDX_STATUS_UNSUPPORTED)
            self.assertIn(b"unsupported draw command type", target._library.fsdx_get_last_error())

    def test_native_rejects_draw_command_from_previous_abi(self):
        with DxOffscreenTarget(1, 1, warp=True) as target:
            command = _DrawCommand()
            command.abi_version = FSDX_ABI_VERSION - 1
            command.struct_size = ctypes.sizeof(_DrawCommand)
            command.alpha = 1.0
            status = int(target._library.fsdx_submit_frame(
                target._runtime,
                ctypes.byref(command),
                ctypes.c_uint32(1),
            ))
            self.assertEqual(status, FSDX_STATUS_ABI_MISMATCH)
            self.assertIn(b"ABI version or size mismatch", target._library.fsdx_get_last_error())


class DxBridgeImportTests(unittest.TestCase):
    def test_draw_command_v2_has_stable_abi_size(self):
        self.assertEqual(ctypes.sizeof(_DrawCommand), 72)

    def test_module_does_not_import_qt(self):
        import lib.core.dx_bridge.offscreen as module

        self.assertNotIn("PyQt5", module.__dict__)


if __name__ == "__main__":
    unittest.main()
