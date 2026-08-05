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

from lib.core.graphics.image_loader import (
    load_image_resource,
    resize_image_resource,
    resize_image_resource_to_height,
    resize_image_resource_to_width,
)
from lib.core.qt_bridge.world_object_assets import (
    image_frame_pair_from_resource,
    pixmap_pair_from_resource,
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

    def test_core_loader_and_scaling_return_backend_neutral_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "asset.png"
            self._write_png(source)

            resource = load_image_resource(source)
            stretched = resize_image_resource(resource, (8, 6))
            by_width = resize_image_resource_to_width(resource, 12)
            by_height = resize_image_resource_to_height(resource, 10)

        self.assertIsNotNone(resource)
        self.assertEqual(resource.size, (4, 2))
        self.assertEqual(stretched.size, (8, 6))
        self.assertEqual(by_width.size, (12, 6))
        self.assertEqual(by_height.size, (20, 10))
        self.assertNotIn("PyQt5", type(stretched.frames[0].pixels).__module__)

    def test_keep_aspect_scaling_derives_fit_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "asset.png"
            self._write_png(source)
            resource = load_image_resource(source)

        fitted = resize_image_resource(resource, (8, 6), keep_aspect=True)
        self.assertEqual(fitted.size, (8, 4))

    def test_qt_adapter_converts_static_resource_and_animation_at_boundary(self):
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
            resource = load_image_resource(source)

        pair = pixmap_pair_from_resource(resource)
        frames, flipped = image_frame_pair_from_resource(resource)

        self.assertEqual(pair.size, (2, 1))
        self.assertEqual(pair.pixmap.size(), pair.flipped_pixmap.size())
        self.assertEqual(len(frames), 2)
        self.assertEqual(len(flipped), 2)
        self.assertFalse(frames[0].isNull())
        self.assertEqual(frames[0].size(), flipped[0].size())


if __name__ == "__main__":
    unittest.main()
