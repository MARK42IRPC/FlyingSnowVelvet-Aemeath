import unittest

from config.config import UI
from lib.script.ui.tooltip_panel import _tooltip_target_opacity


class TooltipPanelOpacityTests(unittest.TestCase):
    def test_tooltip_target_opacity_uses_ui_setting_and_clamps(self):
        original = UI.get('tooltip_opacity', 0.5)
        try:
            UI['tooltip_opacity'] = 0.5
            self.assertEqual(_tooltip_target_opacity(), 0.5)

            UI['tooltip_opacity'] = 1.7
            self.assertEqual(_tooltip_target_opacity(), 1.0)

            UI['tooltip_opacity'] = -0.2
            self.assertEqual(_tooltip_target_opacity(), 0.0)
        finally:
            UI['tooltip_opacity'] = original


if __name__ == '__main__':
    unittest.main()
