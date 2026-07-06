import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = PROJECT_ROOT / "lib" / "script" / "ui" / "ai_settings_panel.py"


class AISettingsPanelYuanbaoLoginButtonLabelsTests(unittest.TestCase):
    def test_source_hides_qq_yuanbao_login_button_but_keeps_handlers(self):
        source = PANEL_PATH.read_text(encoding="utf-8")

        self.assertIn('QPushButton("微信登录元宝")', source)
        self.assertNotIn('QPushButton("QQ登录元宝")', source)
        self.assertIn('def _on_start_yuanbao_wechat_login(self) -> None:', source)
        self.assertIn('def _on_start_yuanbao_qq_login(self) -> None:', source)


if __name__ == "__main__":
    unittest.main()
