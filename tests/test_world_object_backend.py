import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.types import Point, Rect
from lib.core.qt_bridge.world_object_backend import QtWorldObjectBackend
from lib.script.app.qt_backend_bootstrap import _QT_WORLD_OBJECT_TYPES
from lib.core.world_objects import (
    WorldObjectInstance,
    WorldObjectMotion,
    WorldObjectRequest,
    WorldObjectState,
    configure_world_object_backend,
    create_world_object,
    get_world_object_backend,
    get_world_object_center,
    get_world_object_geometry,
    reset_world_object_backend,
)


def _resource() -> ImageResource:
    return ImageResource("test-resource", (RasterFrame(1, 1, bytes((255, 0, 0, 255))),))


class _Backend:
    backend_id = "fake"

    def __init__(self):
        self.calls = []

    def create(self, request):
        self.calls.append(("create", request))
        return 7

    def get_state(self, instance_id):
        self.calls.append(("state", instance_id))
        return WorldObjectState(alive=True)

    def get_motion(self, instance_id):
        self.calls.append(("motion", instance_id))
        return WorldObjectMotion(Point(1, 2), Point(3, 4), 5)

    def apply_motion_delta(self, instance_id, *, position, velocity, wake):
        self.calls.append(("motion_delta", instance_id, position, velocity, wake))

    def set_gravity_enabled(self, instance_id, enabled):
        self.calls.append(("gravity", instance_id, enabled))

    def start_fadeout(self, instance_id):
        self.calls.append(("fade", instance_id))

    def spawn_jump(self, instance_id, power_min, power_max):
        self.calls.append(("jump", instance_id, power_min, power_max))

    def close(self, instance_id):
        self.calls.append(("close", instance_id))

    def get_center(self, instance_id):
        self.calls.append(("center", instance_id))
        return Point(20, 30)

    def get_geometry(self, instance_id):
        self.calls.append(("geometry", instance_id))
        return Rect(10, 15, 20, 30)


class WorldObjectBackendTests(unittest.TestCase):
    def setUp(self):
        self.previous = get_world_object_backend()
        self.backend = _Backend()
        configure_world_object_backend(self.backend)

    def tearDown(self):
        if self.previous is not None:
            configure_world_object_backend(self.previous)
        else:
            reset_world_object_backend()

    def test_business_facade_submits_immutable_request_and_opaque_handle(self):
        instance = create_world_object(
            "example",
            resource=_resource(),
            position=Point(12, 34),
            size=(80, 40),
            answer=42,
        )

        self.assertEqual(
            instance,
            WorldObjectInstance("fake", 7, "example"),
        )
        request = self.backend.calls[0][1]
        self.assertIsInstance(request, WorldObjectRequest)
        self.assertEqual(request.position, Point(12, 34))
        self.assertEqual(request.size, (80, 40))
        self.assertEqual(request.option_dict(), {"answer": 42})
        with self.assertRaises(TypeError):
            request.options[0] = ("changed", None)

    def test_instance_operations_are_translated_to_backend_ids(self):
        instance = create_world_object(
            "example",
            resource=_resource(),
            position=Point(),
            size=(1, 1),
        )

        self.assertTrue(instance.is_alive())
        self.assertEqual(instance.get_motion().position, Point(1, 2))
        instance.set_gravity_enabled(False)
        instance.start_fadeout()
        instance.spawn_jump(0.8, 1.8)
        instance.apply_motion_delta(
            position=Point(1, -2),
            velocity=Point(3, 4),
            wake=True,
        )
        instance.close()
        self.assertEqual(
            [call[0] for call in self.backend.calls],
            ["create", "state", "motion", "gravity", "fade", "jump", "motion_delta", "close"],
        )

    def test_business_geometry_queries_return_core_values(self):
        instance = create_world_object(
            "example",
            resource=_resource(),
            position=Point(),
            size=(1, 1),
        )

        self.assertEqual(get_world_object_center(instance), Point(20, 30))
        self.assertEqual(get_world_object_geometry(instance), Rect(10, 15, 20, 30))
        self.assertEqual(self.backend.calls[-2:], [("center", 7), ("geometry", 7)])

    def test_request_rejects_callbacks_and_mutable_backend_values(self):
        with self.assertRaises(TypeError):
            WorldObjectRequest(
                "example",
                _resource(),
                Point(),
                (1, 1),
                (("callback", lambda: None),),
            )
        with self.assertRaises(TypeError):
            WorldObjectRequest(
                "example",
                _resource(),
                Point(),
                (1, 1),
                (("config", {"interval": 1}),),
            )

    def test_qt_widget_types_stay_inside_the_adapter_package(self):
        self.assertEqual(
            set(_QT_WORLD_OBJECT_TYPES),
            {
                "motor",
                "clock",
                "sofa",
                "snow_pile",
                "snowball",
                "snow_leopard",
                "speaker",
            },
        )
        for module_name, _class_name in _QT_WORLD_OBJECT_TYPES.values():
            self.assertTrue(
                module_name.startswith("lib.script.ui.world_objects."),
                module_name,
            )

    def test_qt_backend_translates_resource_at_construction_and_returns_id(self):
        backend = QtWorldObjectBackend(_QT_WORLD_OBJECT_TYPES)
        resource = _resource()
        with patch(
            "lib.core.qt_bridge.world_object_backend.resize_image_resource",
            return_value=resource,
        ), patch(
            "lib.core.qt_bridge.world_object_backend.create_world_object",
            return_value="created",
        ) as factory:
            instance_id = backend.create(WorldObjectRequest(
                "speaker",
                resource,
                Point(12, 34),
                (80, 40),
            ))

        self.assertEqual(instance_id, 1)
        factory.assert_called_once_with(
            "lib.script.ui.world_objects.speaker",
            "Speaker",
            position=Point(12, 34),
            size=(80, 40),
            visual_resource=resource,
        )

    def test_qt_backend_converts_widget_state_and_geometry_at_adapter_boundary(self):
        backend = QtWorldObjectBackend(_QT_WORLD_OBJECT_TYPES)
        point = SimpleNamespace(x=lambda: 20, y=lambda: 30)
        geometry = SimpleNamespace(
            x=lambda: 10,
            y=lambda: 15,
            width=lambda: 80,
            height=lambda: 40,
        )
        body = SimpleNamespace(
            x=1.0,
            y=2.0,
            vx=3.0,
            vy=4.0,
            on_position_change=None,
        )
        native = SimpleNamespace(
            is_alive=lambda: True,
            get_center=lambda: point,
            geometry=lambda: geometry,
            physics_body=body,
            radius=5,
            _fading=True,
            _flipped=True,
            _drag_offset=None,
            _frozen=False,
        )
        backend._instances[9] = native

        self.assertEqual(backend.get_state(9), WorldObjectState(True, True, True))
        self.assertEqual(backend.get_motion(9), WorldObjectMotion(Point(1, 2), Point(3, 4), 5))
        self.assertEqual(backend.get_center(9), Point(20, 30))
        self.assertEqual(backend.get_geometry(9), Rect(10, 15, 80, 40))

    def test_registered_qt_widget_types_are_importable(self):
        for module_name, class_name in _QT_WORLD_OBJECT_TYPES.values():
            object_type = getattr(__import__(module_name, fromlist=[class_name]), class_name)
            self.assertTrue(callable(object_type), f"{module_name}.{class_name}")


if __name__ == "__main__":
    unittest.main()
