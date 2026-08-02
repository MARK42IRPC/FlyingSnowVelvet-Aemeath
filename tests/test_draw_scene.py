from lib.core.graphics.commands import DrawRequest
from lib.core.graphics.scene import DrawScene
from lib.core.layer import Layer


def test_draw_scene_cycles_frames_without_backend_objects():
    scene = DrawScene()
    frames = [object(), object()]
    scene.register_resource("idle", frames)

    first = scene.next_frame("idle")
    second = scene.next_frame("idle")

    assert first == (frames[1], False)
    assert second == (frames[0], True)


def test_draw_scene_preserves_request_generation_order_on_update():
    scene = DrawScene()
    scene.add_draw_request(DrawRequest("earlier", layer=Layer.MAIN_PET))
    scene.add_draw_request(DrawRequest("later", layer=Layer.MAIN_PET))
    scene.add_draw_request(DrawRequest("earlier", layer=Layer.MAIN_PET))

    ordered = scene.ordered_requests()
    assert [request.resource_id for request in ordered] == ["earlier", "later"]
    assert ordered[0].order < ordered[1].order


class _FakeBackend:
    def __init__(self):
        self.calls = []
        self.cleaned = False

    def render(self, scene, painter, target_rect=None):
        self.calls.append((scene, painter, target_rect))

    def cleanup(self):
        self.cleaned = True


def test_draw_core_can_run_with_a_non_qt_backend():
    from lib.core.draw_core import DrawCore

    backend = _FakeBackend()
    core = DrawCore(backend)
    core.render("target", "rect")
    core.cleanup()

    assert backend.calls[0][1:] == ("target", "rect")
    assert backend.cleaned
