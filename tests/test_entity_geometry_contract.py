from lib.core.graphics.types import Point, Rect
from lib.core.pet_window import PetWindow
from lib.core.qt_bridge.window import to_qpoint


class _Point:
    def __init__(self, x, y):
        self._x = x
        self._y = y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Rect:
    def x(self):
        return 10

    def y(self):
        return 20

    def width(self):
        return 300

    def height(self):
        return 180

    def topLeft(self):
        return _Point(self.x(), self.y())


class _WindowGeometryProbe:
    def frameGeometry(self):
        return _Rect()


def test_pet_window_core_geometry_methods_return_backend_neutral_values():
    probe = _WindowGeometryProbe()
    assert PetWindow.get_core_position(probe) == Point(10, 20)
    assert PetWindow.get_core_geometry(probe) == Rect(10, 20, 300, 180)


def test_qt_boundary_converter_accepts_core_point():
    point = to_qpoint(Point(12.4, 33.6))
    assert (point.x(), point.y()) == (12, 34)
