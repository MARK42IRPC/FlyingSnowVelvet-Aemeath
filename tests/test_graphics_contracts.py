from lib.core.graphics.commands import DrawRequest, RenderItem, RenderRequest
from lib.core.graphics.types import Color, Point, Rect, Size, coerce_color, coerce_rect


def test_graphics_contracts_are_backend_neutral():
    request = DrawRequest("pet", position=Point(12, 24))
    item = RenderItem("effect", lambda *_: None)
    render_request = RenderRequest("effect", lambda *_: None)

    assert request.position == Point(12, 24)
    assert item.item_id == render_request.item_id
    assert Rect(1, 2, 3, 4).top_left == Point(1, 2)
    assert Rect(1, 2, 3, 4).size == Size(3, 4)


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
