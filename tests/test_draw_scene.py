from lib.core.graphics.commands import DrawBatch, DrawRequest
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.scene import DrawScene
from lib.core.graphics.types import Point, Rect
from lib.core.layer import Layer


def _frame(red: int) -> RasterFrame:
    return RasterFrame(1, 1, bytes((red, 0, 0, 255)))


def test_draw_scene_cycles_frames_without_backend_objects():
    scene = DrawScene()
    frames = (_frame(10), _frame(20))
    scene.register_resource(ImageResource("idle", frames))

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


def test_draw_scene_builds_an_immutable_resolved_batch():
    scene = DrawScene()
    frame = _frame(30)
    scene.register_resource(ImageResource("pet", (frame,)))
    scene.add_draw_request(DrawRequest(
        "pet",
        position=(12, 24),
        alpha=2.0,
        flipped=True,
        layer=Layer.MAIN_PET,
    ))

    batch = scene.build_batch()

    assert isinstance(batch, DrawBatch)
    assert len(batch.commands) == 1
    command = batch.commands[0]
    assert command.frame is frame
    assert command.position == Point(12, 24)
    assert command.alpha == 1.0
    assert command.flipped is True
    assert batch.resource_revisions[0].resource_id == "pet"
    assert batch.resource_revisions[0].revision == command.resource_revision


def test_resource_replacement_changes_revision_and_frame_payload():
    scene = DrawScene()
    first = _frame(40)
    second = _frame(50)
    scene.register_resource(ImageResource("pet", (first,)))
    scene.add_draw_request(DrawRequest("pet"))
    first_command = scene.build_batch().commands[0]

    scene.register_resource(ImageResource("pet", (second,)))
    second_command = scene.build_batch().commands[0]

    assert second_command.resource_revision > first_command.resource_revision
    assert second_command.frame is second


def test_resource_unregistration_disappears_from_the_next_batch_snapshot():
    scene = DrawScene()
    scene.register_resource(ImageResource("pet", (_frame(55),)))
    assert scene.build_batch().resource_revisions

    scene.unregister_resource("pet")

    assert scene.build_batch().resource_revisions == ()


class _FakeBackend:
    def __init__(self):
        self.calls = []
        self.cleaned = False

    def render(self, batch, target, viewport=None):
        self.calls.append((batch, target, viewport))

    def cleanup(self):
        self.cleaned = True


def test_draw_core_can_run_with_a_non_qt_backend():
    from lib.core.draw_core import DrawCore

    backend = _FakeBackend()
    core = DrawCore(backend)
    core.register_resource(ImageResource("pet", (_frame(60),)))
    core.add_draw_request(DrawRequest("pet"))
    viewport = Rect(0, 0, 20, 30)
    core.render("target", viewport)
    core.cleanup()

    assert len(backend.calls[0][0].commands) == 1
    assert backend.calls[0][1:] == ("target", viewport)
    assert backend.cleaned
