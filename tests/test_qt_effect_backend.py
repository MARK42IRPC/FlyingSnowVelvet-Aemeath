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

from lib.core.event.center import Event, EventType
from lib.core.graphics.commands import TextCommand
from lib.core.graphics.visuals import build_effect_batch
from lib.core.qt_bridge.effect_system import EffectOverlay
from lib.core.qt_bridge.font import init_font_config
from lib.script.effects.flash_text_effect import FlashTextEffectScript


class QtEffectBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        init_font_config()

    def test_text_effect_state_is_translated_by_shared_presenter(self):
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
        effect.opacity = 1.0
        effect._render_opacity = 1.0

        commands = build_effect_batch([effect]).commands

        self.assertTrue(commands)
        self.assertTrue(all(isinstance(command, TextCommand) for command in commands))

    def test_overlay_keeps_text_effect_as_shared_declaration(self):
        effect = FlashTextEffectScript().create_effects(
            anchor_type="point",
            anchor_data=(32, 24),
            effect_options={
                "text": "shared",
                "font_size": 18,
                "glow": 2.0,
                "fade_in_duration": 0.2,
                "hold_duration": 0.5,
                "fade_out_duration": 0.2,
            },
        )[0]

        class Script:
            def create_effects(self, **_kwargs):
                return [effect]

        class Manager:
            def get_script(self, _effect_id):
                return Script()

        overlay = EffectOverlay()
        overlay._effect_manager = Manager()
        try:
            overlay._on_effect_request(Event(EventType.EFFECT_REQUEST, {
                "effect_id": "shared",
                "anchor_type": "point",
                "anchor_data": (32, 24),
            }))
            overlay._on_tick(Event(EventType.TICK))

            self.assertEqual(overlay._effects, [effect])
            self.assertFalse(hasattr(effect, "pixmap"))
            effect._render_opacity = 1.0
            self.assertTrue(any(
                isinstance(command, TextCommand)
                for command in build_effect_batch(overlay._effects).commands
            ))
        finally:
            overlay.cleanup()


if __name__ == "__main__":
    unittest.main()
