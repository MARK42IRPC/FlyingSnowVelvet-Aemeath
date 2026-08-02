import unittest

from lib.core.qt_bridge.screen_capture import QtScreenCapture


class _Pixmap:
    def __init__(self, payload=b"png-data", success=True):
        self.payload = payload
        self.success = success

    def save(self, buffer, image_format):
        if not self.success:
            return False
        self.image_format = image_format
        buffer.write(self.payload)
        return True


class _Screen:
    def __init__(self, pixmap):
        self.pixmap = pixmap
        self.window_id = None

    def grabWindow(self, window_id):
        self.window_id = window_id
        return self.pixmap


class _Application:
    def __init__(self, screen):
        self.screen = screen

    def primaryScreen(self):
        return self.screen


class QtScreenCaptureTests(unittest.TestCase):
    def test_capture_returns_encoded_png_bytes(self):
        pixmap = _Pixmap()
        screen = _Screen(pixmap)
        capture = QtScreenCapture(lambda: _Application(screen))

        image_data = capture.capture_primary_png()

        self.assertEqual(image_data, b"png-data")
        self.assertEqual(screen.window_id, 0)
        self.assertEqual(pixmap.image_format, "PNG")

    def test_capture_returns_none_without_application_or_on_encode_failure(self):
        self.assertIsNone(QtScreenCapture(lambda: None).capture_primary_png())
        failed = QtScreenCapture(
            lambda: _Application(_Screen(_Pixmap(success=False)))
        )
        self.assertIsNone(failed.capture_primary_png())


if __name__ == "__main__":
    unittest.main()
