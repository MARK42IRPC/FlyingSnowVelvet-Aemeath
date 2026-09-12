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


@unittest.skipUnless(os.name == "nt", "native uninstaller visual checks are Windows-only")
class OfflineUninstallerVisualTests(unittest.TestCase):
    """Pixel-level acceptance for the native uninstaller light workbench surface."""

    _BASE_WIDTH = 880
    _BASE_HEIGHT = 568
    _DPI_CASES = ((96, 1), (120, 1), (144, 1))

    @classmethod
    def setUpClass(cls) -> None:
        try:
            vsdevcmd = installer.find_vsdevcmd(None)
        except SystemExit as exc:
            raise unittest.SkipTest(str(exc)) from exc

        cls._temporary = tempfile.TemporaryDirectory(prefix="fsv-uninstaller-visual-")
        root = Path(cls._temporary.name)
        source_root = installer.DEFAULT_INSTALLER_SOURCE / "src"
        native_test_root = Path(__file__).parent / "native"
        for source in (
            source_root / "uninstaller.c",
            source_root / "resource.h",
            native_test_root / "uninstaller_visual_harness.c",
            native_test_root / "uninstaller_visual_harness.rc",
            installer.PRODUCT_ROOT / "resc" / "icon.ico",
        ):
            shutil.copy2(source, root / source.name)
        installer.create_installer_font_subset(root / "HarmonyOS_Sans_SC_Bold.ttf")
        installer._write_installer_theme_header(root / "installer_theme.h")

        installer.run_vs_command(
            vsdevcmd,
            'rc.exe /nologo /fo"visual_harness.res" "uninstaller_visual_harness.rc"',
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
                    '/Fe:"uninstaller_visual_harness.exe"',
                    '"uninstaller_visual_harness.c"',
                    '"visual_harness.res"',
                    "/link",
                    "/SUBSYSTEM:CONSOLE",
                    "/MANIFEST:NO",
                )
            ),
            root,
        )
        cls._harness = root / "uninstaller_visual_harness.exe"
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
                f"native uninstaller harness failed for dpi={dpi}, page={page}: "
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

    def test_checkbox_states_render_check_fill_and_hover(self) -> None:
        unchecked = self._run_case(96, 1)
        checked = self._run_case(96, 2)
        hovered = self._run_case(96, 3)

        voice_box = (45, 356)
        data_box = (45, 420)
        self.assertEqual(unchecked.getpixel(voice_box), self._rgb("surface"))
        self.assertEqual(unchecked.getpixel(data_box), self._rgb("surface"))
        self.assertEqual(checked.getpixel(voice_box), self._rgb("pink"))
        self.assertEqual(checked.getpixel(data_box), self._rgb("pink"))

        # The two optional cleanups are independent controls: page 2 keeps the
        # data box untouched until its own state flips, page 3 only hovers.
        self.assertEqual(hovered.getpixel(voice_box), self._rgb("surface"))
        self.assertEqual(hovered.getpixel(data_box), self._rgb("surface"))

    def test_cleanup_page_reuses_the_installer_bar_pair(self) -> None:
        cleanup = self._run_case(96, 4)

        # The scan bar (cyan) sits at y=320..344 and the delete bar (pink) at
        # y=364..388, the same hairline-bordered rounded track the installer
        # shows on its resource page.
        self.assertEqual(cleanup.getpixel((100, 332)), self._rgb("cyan"))
        self.assertEqual(cleanup.getpixel((820, 332)), self._rgb("cyan"))
        self.assertEqual(cleanup.getpixel((100, 376)), self._rgb("pink"))
        self.assertEqual(cleanup.getpixel((820, 376)), self._rgb("surface_raised"))
        self.assertEqual(cleanup.getpixel((820, 365)), self._rgb("border"))

        # The completed scan says so; the delete bar shows its percentage.
        scan_text = sum(
            1
            for x in range(360, 520)
            for y in range(324, 341)
            if cleanup.getpixel((x, y)) == self._rgb("text")
        )
        delete_text = sum(
            1
            for x in range(410, 480)
            for y in range(368, 385)
            if cleanup.getpixel((x, y)) == self._rgb("text")
        )
        self.assertGreater(scan_text, 20)
        self.assertGreater(delete_text, 10)

        # The counters sit under the bars and the gap between the pair stays
        # empty, so the page does not read as cramped.
        stats_text = sum(
            1
            for x in range(40, 600)
            for y in range(400, 418)
            if cleanup.getpixel((x, y)) == self._rgb("text")
        )
        self.assertGreater(stats_text, 50)
        for x in (200, 440, 700):
            self.assertEqual(cleanup.getpixel((x, 352)), self._rgb("surface"))
            self.assertEqual(cleanup.getpixel((x, 440)), self._rgb("surface"))

    def test_states_have_real_pixel_differences(self) -> None:
        images = {
            page: self._run_case(96, page)
            for page in (1, 2, 3, 4)
        }
        # Page 2 only flips the two 20px checkbox boxes, so it carries fewer
        # changed pixels than the full-surface hover and cleanup transitions.
        cases = ((1, 2, 500), (1, 3, 1000), (1, 4, 1000), (2, 3, 1000))
        for left, right, minimum in cases:
            with self.subTest(left=left, right=right):
                difference = ImageChops.difference(images[left], images[right])
                self.assertIsNotNone(difference.getbbox())
                changed = sum(1 for pixel in self._pixels(difference) if pixel != (0, 0, 0))
                self.assertGreater(changed, minimum)

    def test_primary_button_and_cleanup_surface_use_light_tokens(self) -> None:
        normal = self._run_case(96, 1)
        hover = self._run_case(96, 3)
        cleanup = self._run_case(96, 4)

        # The logical primary button is x=680..840, y=510..550 at 96 DPI.
        button_pixel = (760, 520)
        self.assertEqual(normal.getpixel(button_pixel), self._rgb("pink"))
        self.assertEqual(hover.getpixel(button_pixel), self._rgb("pink_hover"))

        for image in (normal, cleanup):
            self.assertEqual(image.getpixel((1, 1)), self._rgb("canvas"))
            # x=860 sits right of the 800px-wide label column, so it samples
            # the raw light surface card instead of a control background.
            self.assertEqual(image.getpixel((860, 200)), self._rgb("surface"))


if __name__ == "__main__":
    unittest.main()
