import builtins
import subprocess
import sys
import textwrap
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from lib.core.dx_bridge.screen import DxMonitor, DxScreenProvider
from lib.core.dx_bridge.screen_capture import DxScreenCapture
from lib.core.graphics.types import Point, Rect


class DxScreenProviderTests(unittest.TestCase):
    def test_geometry_queries_follow_current_monitor_topology(self):
        topology = [
            DxMonitor(
                Rect(-1280, 0, 1280, 1024),
                Rect(-1280, 0, 1280, 984),
                dpi=120,
            ),
            DxMonitor(
                Rect(0, 0, 1920, 1080),
                Rect(0, 0, 1920, 1040),
                primary=True,
                dpi=144,
            ),
        ]
        provider = DxScreenProvider(lambda: tuple(topology))

        self.assertEqual(
            provider.get_virtual_screen_rect(),
            Rect(-1280, 0, 3200, 1080),
        )
        self.assertEqual(
            provider.get_screen_rect_for_point(Point(-100, 10)),
            Rect(-1280, 0, 1280, 1024),
        )
        self.assertEqual(
            provider.get_screen_rect_for_point(Point(5000, 5000)),
            Rect(0, 0, 1920, 1080),
        )
        self.assertEqual(provider.get_primary_screen_rect(), Rect(0, 0, 1920, 1080))
        self.assertEqual(provider.get_dpi_for_point(Point(-100, 10)), 120)
        self.assertEqual(provider.get_dpi_for_point(Point(100, 10)), 144)
        self.assertEqual(provider.get_scale_for_point(Point(100, 10)), 1.5)

        topology[:] = [
            DxMonitor(
                Rect(100, -900, 1600, 900),
                Rect(100, -900, 1600, 860),
                primary=True,
            )
        ]

        self.assertEqual(
            provider.get_virtual_screen_rect(),
            Rect(100, -900, 1600, 900),
        )
        self.assertEqual(
            provider.get_screen_rect_for_point(Point(200, -800)),
            Rect(100, -900, 1600, 900),
        )

    def test_empty_or_failed_topology_uses_explicit_fallback(self):
        fallback = Rect(10, 20, 800, 600)
        empty = DxScreenProvider(lambda: (), fallback=fallback)

        def fail():
            raise OSError("display enumeration failed")

        failed = DxScreenProvider(fail, fallback=fallback)

        self.assertEqual(empty.get_virtual_screen_rect(), fallback)
        self.assertEqual(empty.get_primary_screen_rect(), fallback)
        self.assertEqual(empty.get_screen_rect_for_point(Point()), fallback)
        self.assertEqual(failed.get_virtual_screen_rect(), fallback)

    @unittest.skipUnless(sys.platform == "win32", "Win32 monitor API required")
    def test_real_win32_provider_returns_positive_primary_geometry(self):
        provider = DxScreenProvider()

        primary = provider.get_primary_screen_rect()
        virtual = provider.get_virtual_screen_rect()

        self.assertGreater(primary.width, 0)
        self.assertGreater(primary.height, 0)
        self.assertGreaterEqual(virtual.width, primary.width)
        self.assertGreaterEqual(virtual.height, primary.height)


class DxScreenCaptureTests(unittest.TestCase):
    def test_capture_encodes_injected_bgra_pixels_as_png(self):
        secondary = DxMonitor(Rect(-1, 0, 1, 1), Rect(-1, 0, 1, 1))
        primary = DxMonitor(Rect(10, 20, 2, 1), Rect(10, 20, 2, 1), primary=True)
        provider = DxScreenProvider(lambda: (secondary, primary))
        captured_rects = []

        def capture_pixels(rect):
            captured_rects.append(rect)
            return bytes(
                (
                    0,
                    0,
                    255,
                    0,
                    0,
                    255,
                    0,
                    0,
                )
            )

        payload = DxScreenCapture(provider, capture_pixels).capture_primary_png()

        self.assertIsNotNone(payload)
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(captured_rects, [primary.geometry])
        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.size, (2, 1))
            self.assertEqual(list(image.convert("RGB").getdata()), [(255, 0, 0), (0, 255, 0)])

    def test_capture_returns_none_for_missing_invalid_or_failed_pixels(self):
        monitor = DxMonitor(Rect(0, 0, 2, 2), Rect(0, 0, 2, 2), primary=True)
        provider = DxScreenProvider(lambda: (monitor,))

        self.assertIsNone(DxScreenCapture(provider, lambda _rect: None).capture_primary_png())
        self.assertIsNone(DxScreenCapture(provider, lambda _rect: b"short").capture_primary_png())

        def fail(_rect):
            raise OSError("capture failed")

        self.assertIsNone(DxScreenCapture(provider, fail).capture_primary_png())

    def test_screen_modules_import_with_pyqt_blocked(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.dx_bridge import DxMonitor, DxScreenCapture, DxScreenProvider
            from lib.core.graphics.types import Rect

            monitor = DxMonitor(Rect(0, 0, 1, 1), Rect(0, 0, 1, 1), primary=True)
            provider = DxScreenProvider(lambda: (monitor,))
            capture = DxScreenCapture(provider, lambda rect: b"\\x00\\x00\\xff\\x00")
            assert provider.get_primary_screen_rect() == Rect(0, 0, 1, 1)
            assert capture.capture_primary_png().startswith(b"\\x89PNG")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
