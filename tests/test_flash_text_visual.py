from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from lib.core.graphics.commands import TextCommand
from lib.core.graphics.visuals import build_effect_batch, estimate_text_advance
from lib.core.layer import Layer

# The real skill announcements rendered by the Lahai Tetris flash effect.
_FLASH_TEXTS = (
    "随机消除三行",
    "重力压实所有方块",
    "短时间大幅提升红条概率",
    "填充填充率最低的三列",
    "消除颜色大于4的行",
    "引爆并生成日灵方块",
)
_FONT_SIZE = 40
# The pre-fix presenter assumed 0.72 em for every character, including the
# full-width CJK glyphs that actually advance a whole em.
_LEGACY_ADVANCE_RATIO = 0.72
_HARNESS = Path(__file__).parent / "native" / "flash_text_render_harness.py"


def _effect(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        opacity=1.0,
        layer=int(Layer.EFFECT),
        z=0,
        _draw_order=1,
        x=480.0,
        y=80.0,
        text=text,
        font_size=_FONT_SIZE,
        font_type="ui",
        font_bold=False,
        font_weight=None,
        color=(255, 255, 255),
        glow=0.0,
        glow_color=(255, 255, 255),
    )


def _cjk_lower_bound(text: str, size: float) -> float:
    """Minimum advance for a run where every CJK glyph is a full em wide."""
    import unicodedata

    return sum(
        size if unicodedata.east_asian_width(character) in {"W", "F"} else 0.0
        for character in text
    )


class FlashTextPresenterTests(unittest.TestCase):
    def test_presenter_box_covers_every_full_width_glyph(self) -> None:
        for text in _FLASH_TEXTS:
            with self.subTest(text=text):
                command = build_effect_batch([_effect(text)]).commands[-1]
                self.assertIsInstance(command, TextCommand)
                self.assertGreaterEqual(command.rect.width, _cjk_lower_bound(text, _FONT_SIZE))
                self.assertGreaterEqual(command.rect.width, estimate_text_advance(text, _FONT_SIZE))

    def test_regression_is_wider_than_the_legacy_latin_only_estimate(self) -> None:
        for text in _FLASH_TEXTS:
            with self.subTest(text=text):
                command = build_effect_batch([_effect(text)]).commands[-1]
                legacy = len(text) * _FONT_SIZE * _LEGACY_ADVANCE_RATIO
                self.assertGreater(command.rect.width, legacy)

    def test_backend_measurements_take_precedence_over_the_estimate(self) -> None:
        effect = _effect("短时间大幅提升红条概率")
        effect._text_w = 1234.5
        effect._text_h = 55.0

        command = build_effect_batch([effect]).commands[-1]

        self.assertEqual(command.rect.width, 1234.5)
        self.assertEqual(command.rect.height, 55.0)

    def test_estimate_keeps_latin_widths_stable(self) -> None:
        self.assertEqual(estimate_text_advance("Ready", 16), len("Ready") * 16 * _LEGACY_ADVANCE_RATIO)


class FlashTextBackendMetricTests(unittest.TestCase):
    def test_qt_edge_stores_measured_text_metrics(self) -> None:
        import PyQt5

        qt_root = os.path.dirname(PyQt5.__file__)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.path.join(qt_root, "Qt5", "plugins", "platforms"),
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(qt_root, "Qt5", "plugins"))

        from PyQt5.QtGui import QFont, QFontMetrics
        from PyQt5.QtWidgets import QApplication

        from lib.core.graphics.visuals import resolve_effect_font
        from lib.core.qt_bridge.effect_system import _prepare_effect_backend_state

        application = QApplication.instance() or QApplication([])
        self.assertIsNotNone(application)

        effect = _effect("短时间大幅提升红条概率")
        spec = resolve_effect_font(effect)
        font = QFont(spec.family)
        font.setPixelSize(int(spec.pixel_size))
        font.setBold(bool(spec.bold))
        metrics = QFontMetrics(font)

        _prepare_effect_backend_state(effect)

        self.assertEqual(effect._text_w, float(metrics.horizontalAdvance(effect.text)))
        self.assertEqual(effect._text_h, float(metrics.height()))

    def test_backend_state_ignores_effects_without_text(self) -> None:
        from lib.core.qt_bridge.effect_system import _prepare_effect_backend_state

        effect = _effect("")
        _prepare_effect_backend_state(effect)

        self.assertFalse(hasattr(effect, "_text_w"))


@unittest.skipUnless(os.name == "nt", "flash text rasterisation needs the Windows Qt platform")
class FlashTextClippingVisualTests(unittest.TestCase):
    """Rasterise the real presenter output and prove the glyphs are not cut."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="fsv-flash-text-")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(_HARNESS), cls._temporary.name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            cls._temporary.cleanup()
            raise unittest.SkipTest(
                f"Qt render harness unavailable (exit {result.returncode}): "
                f"{result.stdout}{result.stderr}"
            )
        cls._report = json.loads(
            (Path(cls._temporary.name) / "report.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        temporary = getattr(cls, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()

    def _ink_bounds(self, index: int, mode: str) -> tuple[int, int, int, int]:
        image = Image.open(Path(self._temporary.name) / f"{index}-{mode}.png")
        try:
            alpha = image.convert("RGBA").split()[3]
            bounds = alpha.getbbox()
        finally:
            image.close()
        self.assertIsNotNone(bounds, f"{mode} rendered no ink for case {index}")
        return bounds

    def test_presenter_output_matches_an_unclipped_reference(self) -> None:
        for index, case in enumerate(self._report["cases"]):
            with self.subTest(text=case["text"]):
                reference = self._ink_bounds(index, "reference")
                self.assertEqual(self._ink_bounds(index, "measured"), reference)
                self.assertEqual(self._ink_bounds(index, "estimate"), reference)

    def test_legacy_estimate_visibly_cut_the_glyphs(self) -> None:
        for index, case in enumerate(self._report["cases"]):
            with self.subTest(text=case["text"]):
                reference = self._ink_bounds(index, "reference")
                legacy = self._ink_bounds(index, "legacy")
                self.assertNotEqual(legacy, reference)
                self.assertLess(
                    legacy[2] - legacy[0],
                    reference[2] - reference[0],
                )

    def test_measured_metrics_match_the_rasterised_ink_width(self) -> None:
        for index, case in enumerate(self._report["cases"]):
            with self.subTest(text=case["text"]):
                reference = self._ink_bounds(index, "reference")
                ink_width = reference[2] - reference[0]
                self.assertLessEqual(ink_width, case["measured_text_w"] + 2.0)
                # Glyph side bearings shave a couple of pixels off the advance.
                self.assertGreater(ink_width, case["measured_text_w"] * 0.9)


if __name__ == "__main__":
    unittest.main()
