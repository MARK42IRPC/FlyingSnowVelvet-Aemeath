from __future__ import annotations

import unittest

from lib.script.chat.native_tools import get_native_tool_definitions
from lib.script.office import pet_tools


class _RecordingDispatcher:
    """记录办公模式实际下发的桌宠指令，不碰真实窗口。"""

    def __init__(self, *, handled: bool = True, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._handled = handled
        self._error = error

    def execute_command(self, command: str, argument: str = "") -> bool:
        self.calls.append((command, argument))
        if self._error is not None:
            raise self._error
        return self._handled


class PetToolRegistryTests(unittest.TestCase):
    def test_every_native_pet_tool_is_open_or_explicitly_excluded(self):
        native = {
            item["function"]["name"] for item in get_native_tool_definitions()
        }
        self.assertEqual(
            native,
            set(pet_tools.PET_TOOL_NAMES) | set(pet_tools.EXCLUDED_PET_TOOL_NAMES),
        )
        self.assertEqual(
            set(pet_tools.PET_TOOL_NAMES) & set(pet_tools.EXCLUDED_PET_TOOL_NAMES),
            set(),
        )

    def test_pet_tool_definitions_keep_the_companion_schema_and_order(self):
        native = get_native_tool_definitions()
        opened = set(pet_tools.PET_TOOL_NAMES)
        definitions = pet_tools.pet_tool_definitions()

        self.assertEqual(
            definitions,
            [item for item in native if item["function"]["name"] in opened],
        )
        self.assertEqual(
            [item["function"]["name"] for item in definitions],
            list(pet_tools.PET_TOOL_NAMES),
        )


class PetToolDispatchTests(unittest.TestCase):
    def test_supported_calls_map_to_desktop_pet_commands(self):
        cases = (
            ("play_music", {"query": "纸飞机"}, ("音乐", "纸飞机")),
            ("next_track", {}, ("下一曲", "")),
            ("toggle_play_pause", {}, ("暂停", "")),
            ("spawn_snow_leopard", {"count": 3}, ("雪豹", "3")),
            ("spawn_sofa", {}, ("沙发", "1")),
            ("spawn_motorcycle", {"count": 2}, ("摩托", "2")),
            ("start_timer", {"seconds": 45}, ("计时", "45")),
            ("set_volume", {"percent": 30}, ("音量", "30")),
            ("change_volume", {"delta_percent": -10}, ("音量", "-10")),
            ("teleport_pet", {"x": 0.5, "y": 0.25}, ("瞬移", "0.5 0.25")),
        )
        for name, arguments, expected in cases:
            with self.subTest(tool=name):
                self.assertEqual(pet_tools.build_pet_dispatch(name, arguments), expected)

    def test_closed_tools_and_incomplete_arguments_have_no_command(self):
        cases = (
            ("open_browser", {"url": "https://example.test"}),
            ("inspect_screen", {}),
            ("recall_memory", {"topic": "昨天"}),
            ("teleport_pet", {"x": 0.5}),
            ("set_volume", {}),
            ("change_volume", {}),
        )
        for name, arguments in cases:
            with self.subTest(tool=name):
                self.assertIsNone(pet_tools.build_pet_dispatch(name, arguments))


class PetToolExecutionTests(unittest.TestCase):
    def test_execute_dispatches_to_the_desktop_pet_dispatcher(self):
        dispatcher = _RecordingDispatcher()

        result = pet_tools.execute_pet_tool(
            "spawn_snow_leopard", {"count": 2}, dispatcher=dispatcher
        )

        self.assertEqual(dispatcher.calls, [("雪豹", "2")])
        self.assertTrue(result["ok"])
        self.assertIn("雪豹", result["message"])

    def test_execute_reports_missing_arguments_without_dispatching(self):
        dispatcher = _RecordingDispatcher()

        result = pet_tools.execute_pet_tool("teleport_pet", {}, dispatcher=dispatcher)

        self.assertFalse(result["ok"])
        self.assertIn("x", result["message"])
        self.assertEqual(dispatcher.calls, [])

    def test_execute_rejects_tools_outside_the_office_allowlist(self):
        dispatcher = _RecordingDispatcher()

        result = pet_tools.execute_pet_tool("inspect_screen", {}, dispatcher=dispatcher)

        self.assertFalse(result["ok"])
        self.assertIn("inspect_screen", result["message"])
        self.assertEqual(dispatcher.calls, [])

    def test_execute_reports_unhandled_commands_and_dispatcher_failures(self):
        unhandled = pet_tools.execute_pet_tool(
            "next_track", {}, dispatcher=_RecordingDispatcher(handled=False)
        )
        self.assertFalse(unhandled["ok"])

        failure = pet_tools.execute_pet_tool(
            "next_track", {}, dispatcher=_RecordingDispatcher(error=RuntimeError("boom"))
        )
        self.assertFalse(failure["ok"])
        self.assertIn("boom", failure["message"])

    def test_execute_tolerates_arguments_that_are_not_an_object(self):
        dispatcher = _RecordingDispatcher()

        result = pet_tools.execute_pet_tool("next_track", "not-an-object", dispatcher=dispatcher)

        self.assertTrue(result["ok"])
        self.assertEqual(dispatcher.calls, [("下一曲", "")])


if __name__ == "__main__":
    unittest.main()
