from __future__ import annotations

from lib.core.graphics.types import Point, Rect
from lib.core.window_host import PassiveWindowHost


def test_passive_window_host_tracks_lifecycle_and_geometry():
    owner = object()
    host = PassiveWindowHost(owner)

    assert host.identity == id(owner)
    assert host.native_handle is None
    assert host.is_alive()
    assert not host.is_visible()

    host.set_geometry(Rect(10, 20, 30, 40))
    host.show()
    host.activate()
    host.capture_mouse()
    host.set_clickthrough(True)
    host.request_repaint(Rect(0, 0, 5, 6))

    assert host.get_geometry() == Rect(10, 20, 30, 40)
    assert host.is_visible()
    assert not host.is_active()
    assert host.has_mouse_capture()
    assert host.is_clickthrough_enabled()

    host.release_mouse()
    host.hide()
    host.cleanup()
    host.cleanup()

    assert not host.is_alive()
    assert not host.is_visible()
    assert not host.has_mouse_capture()


def test_passive_window_host_rejects_non_core_rectangles():
    host = PassiveWindowHost(object())

    try:
        host.set_geometry((1, 2, 3, 4))
    except TypeError:
        pass
    else:
        raise AssertionError("set_geometry must require Rect")

    try:
        host.request_repaint(Point(1, 2))
    except TypeError:
        pass
    else:
        raise AssertionError("request_repaint must require Rect or None")
