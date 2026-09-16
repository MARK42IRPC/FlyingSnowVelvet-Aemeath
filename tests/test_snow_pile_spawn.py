"""雪堆右键与批次生成共用同一条雪豹生成事件路径。

回归：`SnowPile.mousePressEvent` 的右键分支曾经调用一个从未存在过的
`self._spawn_cb`，每次右键都抛 `AttributeError: 'SnowPile' object has no
attribute '_spawn_cb'`，在 Qt 事件循环里表现为「右键雪堆闪退」。
两处现在都走 `_request_leopard_spawn()`，只发布 `MANAGER_INTERACTION`。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.script.ui.world_objects.snow_pile import SnowPile


def _resource() -> ImageResource:
    return ImageResource("snow-pile-test", (RasterFrame(2, 2, bytes([255] * 16)),))


def _pile() -> SnowPile:
    return SnowPile(
        position=QPoint(120, 240),
        size=(64, 40),
        batch_interval=(10_000, 20_000),
        batch_size=(1, 2),
        batch_item_interval=(3_000, 5_000),
        visual_resource=_resource(),
    )


def _right_click(pile: SnowPile) -> None:
    position = QPoint(pile.width() // 2, pile.height() // 2)
    event = QMouseEvent(
        QMouseEvent.MouseButtonPress,
        position,
        pile.mapToGlobal(position),
        Qt.RightButton,
        Qt.RightButton,
        Qt.NoModifier,
    )
    pile.mousePressEvent(event)


class SnowPileSpawnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        cleanup_event_center()
        self.requests: list[dict] = []
        get_event_center().subscribe(
            EventType.MANAGER_INTERACTION,
            lambda event: self.requests.append(dict(event.data)),
        )

    def tearDown(self) -> None:
        cleanup_event_center()

    def test_right_click_requests_a_leopard_without_raising(self):
        pile = _pile()
        try:
            _right_click(pile)
        finally:
            pile.close()

        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request["manager_id"], "snow_pile")
        self.assertEqual(request["action"], "spawn_leopard")
        # 事件载荷是后端无关的 core Point，管理器按它算生成位置。
        self.assertEqual(
            (request["position"].x, request["position"].y),
            (float(pile.get_center().x()), float(pile.get_center().y())),
        )

    def test_batch_spawn_uses_the_same_event_path(self):
        pile = _pile()
        try:
            pile._batch_remaining = 1
            pile._spawn_next_in_batch()
        finally:
            pile.close()

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["action"], "spawn_leopard")

    def test_snow_pile_has_no_stale_spawn_callback_attribute(self):
        pile = _pile()
        try:
            self.assertFalse(hasattr(pile, "_spawn_cb"))
        finally:
            pile.close()


if __name__ == "__main__":
    unittest.main()
