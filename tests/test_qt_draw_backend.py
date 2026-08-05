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

from PyQt5.QtGui import QColor, QGuiApplication, QImage, QPainter

from lib.core.graphics.commands import DrawBatch, SpriteCommand
from lib.core.graphics.resources import RasterFrame
from lib.core.graphics.types import Rect
from lib.core.layer import Layer
from lib.core.qt_bridge.draw_backend import QtDrawBackend


def _batch(frame: RasterFrame, *, revision: int, flipped: bool = False) -> DrawBatch:
    return DrawBatch((SpriteCommand(
        resource_id="pet",
        resource_revision=revision,
        frame_index=0,
        frame=frame,
        position=None,
        alpha=1.0,
        flipped=flipped,
        scale=1.0,
        layer=int(Layer.MAIN_PET),
        z=0,
        order=1,
    ),))


class QtDrawBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def _render(self, backend: QtDrawBackend, batch: DrawBatch) -> QImage:
        target = QImage(4, 2, QImage.Format_ARGB32_Premultiplied)
        target.fill(QColor("transparent"))
        painter = QPainter(target)
        try:
            backend.render(batch, painter, Rect(0, 0, 4, 2))
        finally:
            painter.end()
        return target

    def test_sprite_batch_scales_and_flips_at_qt_boundary(self):
        frame = RasterFrame(
            2,
            1,
            bytes((255, 0, 0, 255, 0, 0, 255, 255)),
        )
        backend = QtDrawBackend()

        target = self._render(backend, _batch(frame, revision=1, flipped=True))

        self.assertEqual(target.pixelColor(0, 0), QColor("blue"))
        self.assertEqual(target.pixelColor(3, 1), QColor("red"))

    def test_resource_revision_replaces_cached_qt_images(self):
        red = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
        blue = RasterFrame(1, 1, bytes((0, 0, 255, 255)))
        backend = QtDrawBackend()

        self._render(backend, _batch(red, revision=1))
        target = self._render(backend, _batch(blue, revision=2))

        self.assertEqual(target.pixelColor(0, 0), QColor("blue"))
        self.assertEqual({key[1] for key in backend._frame_pixmap_cache}, {2})

        backend.render(DrawBatch(), object())

        self.assertEqual(backend._frame_pixmap_cache, {})
        self.assertEqual(backend._render_pixmap_cache, {})


if __name__ == "__main__":
    unittest.main()
