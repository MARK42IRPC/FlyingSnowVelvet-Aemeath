import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt5.QtCore import QPoint

from lib.core.graphics.types import Point
from lib.core.qt_bridge.world_object_factory import (
    _resolve_world_object_type,
    create_world_object,
)


class _WorldObject:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class WorldObjectFactoryTests(unittest.TestCase):
    def setUp(self):
        _resolve_world_object_type.cache_clear()

    def tearDown(self):
        _resolve_world_object_type.cache_clear()

    def test_factory_converts_core_position_and_caches_widget_type(self):
        module = SimpleNamespace(WorldObject=_WorldObject)
        with patch(
            "lib.core.qt_bridge.world_object_factory.import_module",
            return_value=module,
        ) as importer:
            first = create_world_object(
                "example.world_object",
                "WorldObject",
                position=Point(12.4, 35.6),
                size=(40, 50),
            )
            second = create_world_object(
                "example.world_object",
                "WorldObject",
                position=Point(1, 2),
            )

        position = first.kwargs["position"]
        self.assertIsInstance(position, QPoint)
        self.assertEqual((position.x(), position.y()), (12, 36))
        self.assertEqual(first.kwargs["size"], (40, 50))
        self.assertEqual(
            (second.kwargs["position"].x(), second.kwargs["position"].y()),
            (1, 2),
        )
        importer.assert_called_once_with("example.world_object")

    def test_factory_rejects_non_callable_world_object_type(self):
        module = SimpleNamespace(WorldObject=object())
        with patch(
            "lib.core.qt_bridge.world_object_factory.import_module",
            return_value=module,
        ):
            with self.assertRaisesRegex(TypeError, "not callable"):
                create_world_object(
                    "example.invalid_object",
                    "WorldObject",
                    position=Point(),
                )


if __name__ == "__main__":
    unittest.main()
