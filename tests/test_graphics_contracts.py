import unittest

from lib.core.graphics.commands import (
    ClipPop,
    ClipPush,
    DrawBatch,
    DrawRequest,
    EllipseCommand,
    LineCommand,
    RectCommand,
    SpriteCommand,
    TextAlignment,
    TextCommand,
    TransformPop,
    TransformPush,
)
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.types import Color, FontSpec, Point, Rect, Size, coerce_color, coerce_rect


def test_graphics_contracts_are_backend_neutral():
    frame = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
    resource = ImageResource("pet", (frame,))
    request = DrawRequest("pet", position=(12, 24))
    command = SpriteCommand(
        resource_id="pet",
        resource_revision=1,
        frame_index=0,
        frame=frame,
        position=request.position,
        alpha=1.0,
        flipped=False,
        scale=1.0,
        layer=request.layer,
        z=request.z,
        order=1,
    )
    batch = DrawBatch((command,))

    assert request.position == Point(12, 24)
    assert resource.frames == (frame,)
    assert batch.commands == (command,)
    assert Rect(1, 2, 3, 4).top_left == Point(1, 2)
    assert Rect(1, 2, 3, 4).size == Size(3, 4)


def test_raster_resources_validate_dimensions_and_rgba_byte_count():
    with unittest.TestCase().assertRaisesRegex(ValueError, "dimensions"):
        RasterFrame(0, 1, b"")
    with unittest.TestCase().assertRaisesRegex(ValueError, "requires 8 bytes"):
        RasterFrame(2, 1, b"short")
    with unittest.TestCase().assertRaisesRegex(ValueError, "at least one frame"):
        ImageResource("empty", ())


def test_color_is_clamped_and_coerces_color_like_values():
    assert Color(-1, 128, 999, 300) == Color(0, 128, 255, 255)
    assert coerce_color((12, 34, 56, 78)) == Color(12, 34, 56, 78)
    assert Color(100, 80, 40).with_alpha(90) == Color(100, 80, 40, 90)


def test_coerce_rect_accepts_core_tuple_and_qt_like_values():
    class QtLikeRect:
        def x(self): return 1
        def y(self): return 2
        def width(self): return 30
        def height(self): return 40

    assert coerce_rect(Rect(1, 2, 30, 40)) == Rect(1, 2, 30, 40)
    assert coerce_rect((1, 2, 30, 40)) == Rect(1, 2, 30, 40)
    assert coerce_rect(QtLikeRect()) == Rect(1, 2, 30, 40)


class GraphicsCommandTests(unittest.TestCase):
    def test_draw_batch_accepts_all_declarative_command_kinds(self):
        commands = (
            TextCommand(
                "hello",
                FontSpec("Arial", 12),
                Color(255, 255, 255),
                Rect(0, 0, 30, 12),
                alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            ),
            LineCommand(Point(0, 0), Point(2, 2), Color(255, 0, 0)),
            RectCommand(Rect(0, 0, 2, 2), fill=Color(0, 255, 0)),
            EllipseCommand(Rect(0, 0, 2, 2), stroke=Color(0, 0, 255)),
            ClipPush(Rect(0, 0, 10, 10)),
            ClipPop(),
            TransformPush(),
            TransformPop(),
        )
        batch = DrawBatch(commands)
        self.assertEqual(batch.commands, commands)

    def test_draw_batch_rejects_non_command_values(self):
        with self.assertRaisesRegex(TypeError, "unsupported command"):
            DrawBatch((object(),))
