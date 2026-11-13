import unittest
from unittest.mock import Mock

from lib.script.browser_auth import launch_playwright_edge


class BrowserAuthTests(unittest.TestCase):
    def test_launch_playwright_edge_uses_system_edge_channel(self):
        browser = object()
        playwright = Mock()
        playwright.chromium.launch.return_value = browser

        result = launch_playwright_edge(playwright, headless=True)

        self.assertIs(result, browser)
        playwright.chromium.launch.assert_called_once_with(channel="msedge", headless=True)

    def test_launch_playwright_edge_reports_launch_failure(self):
        playwright = Mock()
        playwright.chromium.launch.side_effect = OSError("launch failed")

        with self.assertRaisesRegex(RuntimeError, "Microsoft Edge") as raised:
            launch_playwright_edge(playwright, headless=False)

        self.assertIsInstance(raised.exception.__cause__, OSError)


if __name__ == "__main__":
    unittest.main()
