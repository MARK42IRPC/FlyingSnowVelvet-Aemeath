import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "yuanbao-free-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

bm = importlib.import_module("src.services.browser.browser_manager")


class YuanbaoBrowserLoginProviderTests(unittest.TestCase):
    def test_normalize_login_provider_defaults_to_wechat(self):
        self.assertEqual(bm._normalize_login_provider(None), "wechat")
        self.assertEqual(bm._normalize_login_provider("unknown"), "wechat")
        self.assertEqual(bm._normalize_login_provider("qq"), "qq")

    def test_status_exposes_active_login_provider(self):
        manager = bm.BrowserManager()
        manager._active_login_provider = "qq"

        status = manager.status()

        self.assertEqual(status["login_provider"], "qq")


if __name__ == "__main__":
    unittest.main()
