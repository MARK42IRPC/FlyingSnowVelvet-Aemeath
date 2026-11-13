import unittest
from io import BytesIO
from unittest.mock import patch

import config.ollama_config as oc
from PIL import Image

from lib.script.chat.vision_codec import _prepare_image_payload


def _png_bytes(width: int, height: int, color=(255, 0, 0, 255)) -> bytes:
    image = Image.new("RGBA", (width, height), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class VisionCodecModelVisionTests(unittest.TestCase):
    def test_model_vision_zero_compresses_large_image_to_720p(self):
        image_data = _png_bytes(2000, 1000)

        with patch.dict(oc.OLLAMA, {"model_vision": 0}, clear=False):
            payload, mime = _prepare_image_payload(image_data)

        self.assertEqual(mime, "image/jpeg")
        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.size, (1280, 640))

    def test_model_vision_hundred_keeps_original_bytes_and_format(self):
        image_data = _png_bytes(2000, 1000)

        with patch.dict(oc.OLLAMA, {"model_vision": 100}, clear=False):
            payload, mime = _prepare_image_payload(image_data)

        self.assertEqual(payload, image_data)
        self.assertEqual(mime, "image/png")

    def test_model_vision_midpoint_uses_linear_resolution_scale(self):
        image_data = _png_bytes(2000, 1000)

        with patch.dict(oc.OLLAMA, {"model_vision": 50}, clear=False):
            payload, mime = _prepare_image_payload(image_data)

        self.assertEqual(mime, "image/jpeg")
        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.size, (1640, 820))

    def test_small_image_keeps_original_even_at_low_vision(self):
        image_data = _png_bytes(800, 600)

        with patch.dict(oc.OLLAMA, {"model_vision": 0}, clear=False):
            payload, mime = _prepare_image_payload(image_data)

        self.assertEqual(payload, image_data)
        self.assertEqual(mime, "image/png")


if __name__ == "__main__":
    unittest.main()
