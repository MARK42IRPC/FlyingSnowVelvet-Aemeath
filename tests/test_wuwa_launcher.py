from __future__ import annotations

import unittest
from unittest.mock import patch

from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.script.app.wuwa_launcher import WutheringWavesLauncher


class WutheringWavesLauncherTests(unittest.TestCase):
    def tearDown(self) -> None:
        cleanup_event_center()

    def test_configured_path_is_shared_by_all_backends(self) -> None:
        launcher = WutheringWavesLauncher()
        messages = []
        get_event_center().subscribe(
            EventType.INFORMATION,
            lambda event: messages.append(event.data["text"]),
        )
        with (
            patch.dict("lib.script.app.wuwa_launcher.CLOUD_MUSIC", {"launch_wuwa_path": "game.exe"}),
            patch.object(launcher, "normalize_path", return_value=r"C:\Games\game.exe"),
            patch.object(launcher, "_is_supported_launch_file", return_value=True),
            patch("lib.script.app.wuwa_launcher.os.startfile", create=True) as startfile,
        ):
            self.assertTrue(launcher.launch())

        startfile.assert_called_once_with(r"C:\Games\game.exe")
        self.assertEqual(messages[-1], "已通过配置路径启动鸣潮...")

    def test_discovery_fallback_order_is_stable(self) -> None:
        launcher = WutheringWavesLauncher()
        with (
            patch.dict("lib.script.app.wuwa_launcher.CLOUD_MUSIC", {"launch_wuwa_path": ""}),
            patch.object(launcher, "_find_named_desktop_shortcut", return_value=""),
            patch.object(launcher, "_find_executable", return_value=r"C:\Games\Wuthering Waves.exe"),
            patch("lib.script.app.wuwa_launcher.subprocess.Popen") as popen,
        ):
            self.assertTrue(launcher.launch())

        popen.assert_called_once_with(
            [r"C:\Games\Wuthering Waves.exe"],
            cwd=r"C:\Games",
        )


if __name__ == "__main__":
    unittest.main()
