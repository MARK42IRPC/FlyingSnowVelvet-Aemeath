from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageChops

from lib.core.graphics.announcement_visuals import ANNOUNCEMENT_LIGHT_COLORS
from scripts import build_offline_installer as installer


@unittest.skipUnless(os.name == "nt", "native installer visual checks are Windows-only")
class OfflineInstallerVisualTests(unittest.TestCase):
    """Pixel-level acceptance for the native installer light announcement surface."""

    _BASE_WIDTH = 880
    _BASE_HEIGHT = 568
    _DPI_CASES = ((96, 1), (120, 2), (144, 3))
    _STATE_CASES = (4, 5, 6, 7, 8, 9)

    @classmethod
    def setUpClass(cls) -> None:
        try:
            vsdevcmd = installer.find_vsdevcmd(None)
        except SystemExit as exc:
            raise unittest.SkipTest(str(exc)) from exc

        cls._temporary = tempfile.TemporaryDirectory(prefix="fsv-installer-visual-")
        root = Path(cls._temporary.name)
        source_root = installer.DEFAULT_INSTALLER_SOURCE / "src"
        native_test_root = Path(__file__).parent / "native"
        for source in (
            source_root / "main.c",
            source_root / "resource.h",
            source_root / "zip_extract.h",
            native_test_root / "installer_visual_harness.c",
            native_test_root / "installer_visual_harness.rc",
            installer.PRODUCT_ROOT / "resc" / "icon.ico",
        ):
            destination = root / source.name
            shutil.copy2(source, destination)
        shutil.copy2(
            installer.PRODUCT_ROOT / "resc" / "FRONTS" / "HarmonyOS_Sans_SC_Bold.ttf",
            root / "HarmonyOS_Sans_SC_Bold.ttf",
        )
        installer._write_installer_theme_header(root / "installer_theme.h")
        (root / "payload_info.h").write_text(
            "#pragma once\n"
            "#define FSV_PAYLOAD_ARCHIVE_BYTES 1ULL\n"
            "#define FSV_PAYLOAD_FILE_COUNT 1ULL\n"
            "#define FSV_PAYLOAD_UNCOMPRESSED_BYTES 1ULL\n",
            encoding="ascii",
        )

        installer.run_vs_command(
            vsdevcmd,
            'rc.exe /nologo /fo"visual_harness.res" "installer_visual_harness.rc"',
            root,
        )
        installer.run_vs_command(
            vsdevcmd,
            " ".join(
                (
                    "cl.exe",
                    "/nologo",
                    "/MT",
                    "/O2",
                    "/W4",
                    "/WX",
                    "/utf-8",
                    '/Fe:"installer_visual_harness.exe"',
                    '"installer_visual_harness.c"',
                    '"visual_harness.res"',
                    "/link",
                    "/SUBSYSTEM:CONSOLE",
                    "/MANIFEST:NO",
                )
            ),
            root,
        )
        cls._harness = root / "installer_visual_harness.exe"
        if not cls._harness.is_file():
            raise unittest.SkipTest("Visual Studio harness compilation produced no executable")

    @classmethod
    def tearDownClass(cls) -> None:
        temporary = getattr(cls, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()

    @staticmethod
    def _rgb(name: str) -> tuple[int, int, int]:
        color = ANNOUNCEMENT_LIGHT_COLORS[name]
        return color.red, color.green, color.blue

    @staticmethod
    def _pixels(image: Image.Image):
        # Pillow 11+ renamed Image.getdata(); keep the visual test quiet on
        # both the current and older supported Pillow releases.
        flattened = getattr(image, "get_flattened_data", None)
        return flattened() if flattened is not None else image.getdata()

    @classmethod
    def _run_case(cls, dpi: int, page: int) -> Image.Image:
        output = Path(cls._temporary.name) / f"page-{dpi}-{page}.bmp"
        result = subprocess.run(
            [str(cls._harness), str(output), str(dpi), str(page)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"native visual harness failed for dpi={dpi}, page={page}: "
                f"{result.stdout}{result.stderr}"
            )
        return Image.open(output).convert("RGB")

    def test_light_palette_and_dimensions_are_present_at_each_dpi(self) -> None:
        expected_colors = {
            self._rgb("canvas"),
            self._rgb("surface"),
            self._rgb("surface_raised"),
            self._rgb("border"),
            self._rgb("pink"),
            self._rgb("text"),
        }
        for dpi, page in self._DPI_CASES:
            with self.subTest(dpi=dpi, page=page):
                image = self._run_case(dpi, page)
                self.assertEqual(
                    image.size,
                    (
                        self._BASE_WIDTH * dpi // 96,
                        self._BASE_HEIGHT * dpi // 96,
                    ),
                )
                pixels = Counter(self._pixels(image))
                self.assertGreater(
                    sum(pixels[color] for color in expected_colors),
                    image.width * image.height // 2,
                )
                self.assertGreater(pixels[self._rgb("pink")], 100)
                self.assertGreater(pixels[self._rgb("text")], 100)

    def test_states_have_real_pixel_differences(self) -> None:
        images = {
            1: self._run_case(96, 1),
            2: self._run_case(96, 2),
            3: self._run_case(96, 3),
        }
        images.update({page: self._run_case(96, page) for page in self._STATE_CASES})
        for left, right in ((1, 3), (1, 4), (4, 5), (1, 6), (2, 7)):
            with self.subTest(left=left, right=right):
                difference = ImageChops.difference(images[left], images[right])
                self.assertIsNotNone(difference.getbbox())
                changed = sum(1 for pixel in self._pixels(difference) if pixel != (0, 0, 0))
                self.assertGreater(changed, 1000)

    def test_button_states_and_disabled_space_action_use_light_tokens(self) -> None:
        normal = self._run_case(96, 1)
        hover = self._run_case(96, 8)
        pressed = self._run_case(96, 9)
        insufficient = self._run_case(96, 7)

        # The logical primary button is x=680..840, y=510..550 at 96 DPI.
        button_pixel = (760, 520)
        self.assertEqual(normal.getpixel(button_pixel), self._rgb("pink"))
        self.assertEqual(hover.getpixel(button_pixel), self._rgb("pink_hover"))
        self.assertEqual(pressed.getpixel(button_pixel), self._rgb("cyan"))
        self.assertEqual(insufficient.getpixel(button_pixel), self._rgb("surface_raised"))

        # The long-path and insufficient-space states must keep their content
        # inside the same light surface instead of introducing a dark fallback.
        for image in (insufficient,):
            self.assertEqual(image.getpixel((1, 1)), self._rgb("canvas"))
            self.assertEqual(image.getpixel((50, 300)), self._rgb("surface_raised"))


if __name__ == "__main__":
    unittest.main()
