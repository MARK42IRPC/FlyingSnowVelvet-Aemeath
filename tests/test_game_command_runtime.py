from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.core.event.center import Event, EventType, cleanup_event_center, get_event_center
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.script.gemes.MAIN.command_runtime import GameCommandRuntime
from lib.script.app.qt_backend_bootstrap import _open_game_helper


class _PackageService:
    def __init__(self) -> None:
        manifest = SimpleNamespace(
            name="拉海洛方块",
            command_aliases=("拉海洛",),
        )
        self.games = [SimpleNamespace(game_id="lahai_tetris", manifest=manifest)]
        self.refresh_count = 0

    def refresh(self) -> None:
        self.refresh_count += 1

    def list_installed_games(self):
        return list(self.games)


class GameCommandRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        cleanup_event_center()
        self.registry = get_hash_cmd_registry()
        for name, _usage, _description in tuple(self.registry.get_all()):
            self.registry.unregister(name)
        self.service = _PackageService()
        self.launches: list[tuple[str, str]] = []
        self.cleanup_count = 0
        self.runtime = GameCommandRuntime(
            self.service,
            lambda action, game_id: self.launches.append((action, game_id)) or True,
            package_cleanup=self._cleanup_package,
        )

    def tearDown(self) -> None:
        self.runtime.cleanup()
        cleanup_event_center()
        for name, _usage, _description in tuple(self.registry.get_all()):
            self.registry.unregister(name)

    def _cleanup_package(self) -> None:
        self.cleanup_count += 1

    def _publish(self, text: str) -> None:
        get_event_center().publish(Event(EventType.INPUT_HASH, {"text": text}))

    def test_registers_manager_name_and_alias_commands(self):
        names = {name for name, _usage, _description in self.registry.get_all()}
        self.assertEqual(names, {"游戏", "拉海洛方块", "拉海洛"})
        self.assertEqual(self.service.refresh_count, 1)

    def test_routes_manager_and_game_actions(self):
        self._publish("游戏 打开")
        self._publish("游戏 关闭")
        self._publish("拉海洛")
        self._publish("拉海洛方块 关闭")

        self.assertEqual(self.launches, [
            ("open", ""),
            ("close", ""),
            ("open", "lahai_tetris"),
            ("close", "lahai_tetris"),
        ])

    def test_lists_games_and_reports_launcher_failure(self):
        messages: list[str] = []
        get_event_center().subscribe(
            EventType.INFORMATION,
            lambda event: messages.append(event.data["text"]),
        )
        self._publish("游戏 列表")
        self.assertEqual(messages[-1], "已安装游戏: 拉海洛方块")

        self.runtime._request_launcher = lambda _action, _game_id: False
        self._publish("拉海洛")
        self.assertIn("启动失败", messages[-1])

    def test_cleanup_is_idempotent_and_unregisters_everything(self):
        self.runtime.cleanup()
        self.runtime.cleanup()
        self._publish("拉海洛")

        self.assertEqual(self.cleanup_count, 1)
        self.assertEqual(self.launches, [])
        names = {name for name, _usage, _description in self.registry.get_all()}
        self.assertFalse(names & {"游戏", "拉海洛方块", "拉海洛"})

    def test_bootstrap_maps_manager_and_game_requests(self):
        with patch(
            "lib.script.app.workbench_helper.launch_workbench_helper",
            return_value=True,
        ) as launch:
            self.assertTrue(_open_game_helper("open", ""))
            launch.assert_called_with(
                initial_page="game_manager",
                game_id="",
                game_action="open_manager",
            )

            self.assertTrue(_open_game_helper("close", ""))
            launch.assert_called_with(
                initial_page="game_manager",
                game_id="",
                game_action="close_manager",
            )

            self.assertTrue(_open_game_helper("open", "lahai_tetris"))
            launch.assert_called_with(
                initial_page="game_manager",
                game_id="lahai_tetris",
                game_action="open",
            )

            call_count = launch.call_count
            self.assertFalse(_open_game_helper("invalid", "lahai_tetris"))
            self.assertEqual(launch.call_count, call_count)


if __name__ == "__main__":
    unittest.main()
