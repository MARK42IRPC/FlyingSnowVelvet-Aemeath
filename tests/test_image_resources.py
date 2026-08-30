import tempfile
import unittest
from pathlib import Path

from PIL import Image

from lib.core.graphics.image_loader import (
    load_image_resource,
    resize_image_resource,
    resize_image_resource_to_height,
    resize_image_resource_to_width,
)


class ImageResourceTests(unittest.TestCase):
    @staticmethod
    def _write_png(path: Path) -> None:
        image = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
        image.putpixel((0, 0), (255, 0, 0, 255))
        image.putpixel((3, 0), (0, 0, 255, 255))
        image.save(path)

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


if __name__ == "__main__":
    unittest.main()
