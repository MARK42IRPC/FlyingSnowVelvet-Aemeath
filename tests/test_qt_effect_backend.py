from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.core.qt_bridge.effect_system import _render_text_effect_pixmap
from lib.core.qt_bridge.font import init_font_config
from lib.script.effects.flash_text_effect import FlashTextEffectScript


class QtEffectBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        init_font_config()

    def test_text_effect_state_is_rasterized_at_qt_boundary(self):
        effect = FlashTextEffectScript().create_effects(
            anchor_type="point",
            anchor_data=(320, 180),
            effect_options={
                "text": "backend text",
                "fade_in_duration": 0.2,
                "hold_duration": 0.5,
                "fade_out_duration": 0.2,
                "font_size": 28,
                "font_bold": True,
                "glow": 8.0,
            },
        )[0]

        pixmap = _render_text_effect_pixmap(effect)

        self.assertIsNotNone(pixmap)
        self.assertGreater(pixmap.width(), 0)
        self.assertGreater(pixmap.height(), 0)


if __name__ == "__main__":
    unittest.main()
