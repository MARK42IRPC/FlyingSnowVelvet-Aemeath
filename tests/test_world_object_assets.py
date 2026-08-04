import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PIL import Image
from PyQt5.QtGui import QColor, QGuiApplication, QImage

from lib.core.qt_bridge.world_object_assets import (
    load_gif_frame_pair,
    load_height_scaled_pixmap_pair,
    load_stretched_pixmap_pair,
    load_width_scaled_pixmap_pair,
    scale_pixmap,
)


class WorldObjectAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def _write_png(self, path: Path) -> None:
        image = QImage(4, 2, QImage.Format_RGBA8888)
        image.fill(QColor("transparent"))
        image.setPixelColor(0, 0, QColor("red"))
        image.setPixelColor(3, 0, QColor("blue"))
        self.assertTrue(image.save(str(path)))

    def test_pixmap_pair_scaling_and_flip_are_backend_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "asset.png"
            self._write_png(source)

            stretched = load_stretched_pixmap_pair(source, (8, 6))
            by_width = load_width_scaled_pixmap_pair(source, 12)
            by_height = load_height_scaled_pixmap_pair(source, 10)

        self.assertIsNotNone(stretched)
        self.assertEqual(stretched.size, (8, 6))
        self.assertEqual(stretched.pixmap.size().width(), 8)
        self.assertEqual(stretched.flipped_pixmap.size().height(), 6)
        self.assertIsNotNone(by_width)
        self.assertEqual(by_width.size[0], 12)
        self.assertIsNotNone(by_height)
        self.assertEqual(by_height.size[1], 10)

    def test_scale_pixmap_uses_exact_size_for_cached_snowballs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "asset.png"
            self._write_png(source)
            pair = load_stretched_pixmap_pair(source, (4, 2))

        scaled = scale_pixmap(pair.pixmap, (9, 9))

        self.assertEqual((scaled.width(), scaled.height()), (9, 9))

    def test_gif_loader_returns_normal_and_flipped_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "asset.gif"
            first = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
            second = Image.new("RGBA", (2, 1), (0, 0, 255, 255))
            first.save(
                source,
                save_all=True,
                append_images=[second],
                duration=20,
                loop=0,
                disposal=2,
            )

            frames, flipped = load_gif_frame_pair(source)

        self.assertEqual(len(frames), 2)
        self.assertEqual(len(flipped), 2)
        self.assertFalse(frames[0].isNull())
        self.assertEqual(frames[0].size(), flipped[0].size())


if __name__ == "__main__":
    unittest.main()
