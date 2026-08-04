import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from lib.core.graphics.types import Point, Rect
from lib.core.qt_bridge.world_object_backend import QtWorldObjectBackend, _WORLD_OBJECT_TYPES
from lib.core.world_objects import (
    WorldObjectImagePair,
    configure_world_object_backend,
    create_world_object,
    get_image_size,
    get_world_object_backend,
    get_world_object_center,
    get_world_object_geometry,
    load_stretched_image_pair,
    reset_world_object_backend,
)


class _Backend:
    def __init__(self):
        self.calls = []

    def load_stretched_image_pair(self, path, size):
        self.calls.append(("load", path, size))
        return WorldObjectImagePair("normal", "flipped", size)

    def image_size(self, image):
        self.calls.append(("image_size", image))
        return (80, 40)

    def create(self, object_type, *, position, **kwargs):
        self.calls.append(("create", object_type, position, kwargs))
        return {"position": position, **kwargs}

    def get_center(self, instance):
        self.calls.append(("center", instance))
        return Point(20, 30)

    def get_geometry(self, instance):
        self.calls.append(("geometry", instance))
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

    def test_business_facade_keeps_backend_handles_opaque(self):
        pair = load_stretched_image_pair("asset.png", (80, 40))
        created = create_world_object(
            "example",
            position=Point(12, 34),
            image=pair.image,
        )

        self.assertEqual(pair.flipped_image, "flipped")
        self.assertEqual(created["position"], Point(12, 34))
        self.assertEqual(
            self.backend.calls,
            [
                ("load", "asset.png", (80, 40)),
                (
                    "create",
                    "example",
                    Point(12, 34),
                    {"image": "normal"},
                ),
            ],
        )

    def test_business_geometry_queries_return_core_values(self):
        instance = SimpleNamespace(name="speaker")

        self.assertEqual(get_image_size("normal"), (80, 40))
        self.assertEqual(get_world_object_center(instance), Point(20, 30))
        self.assertEqual(get_world_object_geometry(instance), Rect(10, 15, 20, 30))
        self.assertEqual(
            self.backend.calls,
            [
                ("image_size", "normal"),
                ("center", instance),
                ("geometry", instance),
            ],
        )

    def test_qt_widget_types_stay_inside_the_adapter_package(self):
        self.assertEqual(
            set(_WORLD_OBJECT_TYPES),
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
        for module_name, _class_name in _WORLD_OBJECT_TYPES.values():
            self.assertTrue(
                module_name.startswith("lib.core.qt_bridge.world_objects."),
                module_name,
            )

    def test_qt_backend_translates_generic_image_handles_at_construction(self):
        backend = QtWorldObjectBackend()
        with patch(
            "lib.core.qt_bridge.world_object_backend.create_world_object",
            return_value="created",
        ) as factory:
            created = backend.create(
                "speaker",
                position=Point(12, 34),
                image="normal",
                flipped_image="flipped",
                size=(80, 40),
            )

        self.assertEqual(created, "created")
        factory.assert_called_once_with(
            "lib.core.qt_bridge.world_objects.speaker",
            "Speaker",
            position=Point(12, 34),
            pixmap="normal",
            flipped_pixmap="flipped",
            size=(80, 40),
        )

    def test_qt_backend_converts_widget_geometry_at_adapter_boundary(self):
        backend = QtWorldObjectBackend()
        point = SimpleNamespace(x=lambda: 20, y=lambda: 30)
        geometry = SimpleNamespace(
            x=lambda: 10,
            y=lambda: 15,
            width=lambda: 80,
            height=lambda: 40,
        )
        instance = SimpleNamespace(
            get_center=lambda: point,
            geometry=lambda: geometry,
        )

        self.assertEqual(backend.get_center(instance), Point(20, 30))
        self.assertEqual(backend.get_geometry(instance), Rect(10, 15, 80, 40))

    def test_registered_qt_widget_types_are_importable(self):
        for module_name, class_name in _WORLD_OBJECT_TYPES.values():
            object_type = getattr(import_module(module_name), class_name)
            self.assertTrue(callable(object_type), f"{module_name}.{class_name}")


if __name__ == "__main__":
    unittest.main()
