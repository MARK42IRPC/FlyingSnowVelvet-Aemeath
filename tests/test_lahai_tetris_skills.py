import unittest

from PyQt5.QtWidgets import QApplication

from config.font_config import init_font_config
from lib.script.gemes.MAIN.lahai_tetris.skills import LahaiSkillSlot
from lib.script.effects.flash_text_effect import FlashTextEffectScript


class _DummySkill(LahaiSkillSlot):
    def __init__(self, cooldown_secs: float = 0.0) -> None:
        self.slot_index = 9
        self.name = ""
        self.avatar_filename = None
        self.base_cooldown_secs = float(cooldown_secs)
        self.cooldown_secs = float(cooldown_secs)
        self.cooldown_until = 0.0
        self._paused_remaining = 0.0
        self.avatar = None

    def apply(self, owner):
        return False


class LahaiTetrisSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        init_font_config()

    def test_skill_cooldown_curve_advances_by_one_point_one_and_rounds_up(self):
        skill = _DummySkill(30.0)

        skill.advance_cooldown_curve(30.0)
        self.assertEqual(skill.cooldown_secs, 33.0)

        skill.advance_cooldown_curve(33.0)
        self.assertEqual(skill.cooldown_secs, 37.0)

    def test_skill_zero_cooldown_does_not_grow(self):
        skill = _DummySkill(12.0)

        skill.advance_cooldown_curve(0.0)
        self.assertEqual(skill.cooldown_secs, 0.0)

    def test_flash_text_effect_builds_effect_instance(self):
        script = FlashTextEffectScript()

        effects = script.create_effects(
            anchor_type="point",
            anchor_data=(320, 180),
            effect_options={
                "text": "随机消除三行",
                "fade_in_duration": 0.3,
                "fade_in_frequency": 10.0,
                "hold_duration": 1.0,
                "fade_out_duration": 0.3,
                "fade_out_frequency": 5.0,
                "font_type": "ui",
                "font_size": 28,
                "color": (255, 255, 255),
                "font_bold": True,
                "glow": 10.0,
                "z": 23,
            },
            request_context={
                "offset_x": 0.0,
                "offset_y": 0.0,
            },
        )

        self.assertEqual(len(effects), 1)
        effect = effects[0]
        self.assertEqual(effect.center_pos, (320.0, 180.0))
        self.assertAlmostEqual(effect.total_duration, 1.6)
        self.assertEqual(effect.z, 23)
        self.assertGreater(effect.pixmap.width(), 0)
        self.assertGreater(effect.pixmap.height(), 0)


if __name__ == "__main__":
    unittest.main()
