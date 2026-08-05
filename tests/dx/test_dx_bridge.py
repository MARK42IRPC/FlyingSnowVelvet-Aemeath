from __future__ import annotations

import ctypes
import unittest

from lib.core.dx_bridge import DxBridgeError, DxOffscreenTarget, find_dx_library
from lib.core.graphics.commands import DrawBatch, RectCommand, ResourceRevision, SpriteCommand
from lib.core.graphics.resources import RasterFrame
from lib.core.graphics.types import Color, Point, Rect
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

    def test_offscreen_prototype_rejects_non_sprite_commands(self):
        with DxOffscreenTarget(2, 2, warp=True) as target:
            with self.assertRaisesRegex(DxBridgeError, "SpriteCommand values only"):
                target.render_batch(DrawBatch((RectCommand(
                    Rect(0, 0, 1, 1),
                    fill=Color(255, 0, 0),
                ),)))


class DxBridgeImportTests(unittest.TestCase):
    def test_module_does_not_import_qt(self):
        import lib.core.dx_bridge.offscreen as module

        self.assertNotIn("PyQt5", module.__dict__)


if __name__ == "__main__":
    unittest.main()
