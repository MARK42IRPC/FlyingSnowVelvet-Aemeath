import unittest

from PyQt5.QtGui import QColor, QImage

from lib.core.graphics.resources import RasterFrame
from lib.core.qt_bridge.gif_loader import flip_frame, qimage_from_raster_frame, scale_frame


class QtGifLoaderTests(unittest.TestCase):
    def test_core_raster_frame_is_copied_into_qt_memory(self):
        frame = RasterFrame(1, 1, bytes((255, 0, 0, 255)))

        image = qimage_from_raster_frame(frame)

        self.assertEqual(image.pixelColor(0, 0), QColor("red"))

    def test_scale_and_horizontal_flip_preserve_expected_pixels(self):
        image = QImage(2, 1, QImage.Format_RGBA8888)
        image.setPixelColor(0, 0, QColor("red"))
        image.setPixelColor(1, 0, QColor("blue"))

        flipped = flip_frame(image)
        scaled = scale_frame(flipped, (4, 2))

        self.assertEqual(flipped.pixelColor(0, 0), QColor("blue"))
        self.assertEqual(flipped.pixelColor(1, 0), QColor("red"))
        self.assertEqual((scaled.width(), scaled.height()), (4, 2))
        self.assertEqual(scaled.pixelColor(0, 0), QColor("blue"))
        self.assertEqual(scaled.pixelColor(3, 1), QColor("red"))


if __name__ == "__main__":
    unittest.main()
