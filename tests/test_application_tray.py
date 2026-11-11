from __future__ import annotations

import threading
import unittest
import unittest.mock
from unittest.mock import patch

from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.tray_host import TrayCommand, TrayMenuState
from lib.script.main import ApplicationState


class ApplicationTrayRoutingTests(unittest.TestCase):
    def setUp(self):
        self.center = get_event_center()
        self.state = ApplicationState.__new__(ApplicationState)
        self.state._event_center = self.center
        self.state._tray_host = None
        self.state._tray_menu_state = TrayMenuState()
        self.state._tray_action_lock = threading.Lock()
        self.state._pending_tray_actions = set()
        self.state._exit_in_progress = False
        self.state._exit_completed = False

    def tearDown(self):
        cleanup_event_center()

    def test_common_commands_publish_backend_neutral_events(self):
        received = []
        for event_type in (
            EventType.UI_OPEN_CMD_WINDOW,
            EventType.UI_CLICKTHROUGH_TOGGLE,
            EventType.INPUT_HASH,
            EventType.INFORMATION,
        ):
            self.center.subscribe(event_type, lambda event, event_type=event_type: received.append((event_type, event.data)))

        self.state._on_tray_command(TrayCommand.OPEN_CMD)
        self.state._on_tray_command(TrayCommand.TOGGLE_CLICKTHROUGH, True)
        self.state._on_tray_command(TrayCommand.CLEANUP_DESKTOP)

        self.assertIn((EventType.UI_OPEN_CMD_WINDOW, {"entity": None}), received)
        self.assertIn((EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": True}), received)
        self.assertIn((EventType.INPUT_HASH, {"text": "清理"}), received)
        self.assertTrue(any(item[0] == EventType.INFORMATION for item in received))

    def test_settings_command_delegates_to_application_ui(self):
        calls = []
        self.state._application_ui = type('Ui', (), {
            'open_settings': lambda _self: calls.append('open'),
        })()
        self.state._on_tray_command(TrayCommand.OPEN_SETTINGS)
        self.assertEqual(calls, ['open'])

    def test_blocking_commands_use_interactive_action_submission(self):
        submitted = []
        self.state._submit_tray_action = lambda command, worker: submitted.append((command, worker))

        self.state._on_tray_command(TrayCommand.TOGGLE_AUTOSTART, True)
        self.state._on_tray_command(TrayCommand.CLEANUP_CACHE)
        self.state._on_tray_command(TrayCommand.CLEANUP_HISTORY)
        self.state._on_tray_command(TrayCommand.OPEN_AUTHOR_PAGE)

        self.assertEqual(
            [command for command, _worker in submitted],
            [
                TrayCommand.TOGGLE_AUTOSTART,
                TrayCommand.CLEANUP_CACHE,
                TrayCommand.CLEANUP_HISTORY,
                TrayCommand.OPEN_AUTHOR_PAGE,
            ],
        )

    def test_live_stream_command_uses_interactive_action_submission(self):
        submitted = []
        self.state._submit_tray_action = lambda command, worker: submitted.append((command, worker))

        self.state._on_tray_command(TrayCommand.TOGGLE_LIVE_STREAM, True)

        self.assertEqual([command for command, _worker in submitted], [TrayCommand.TOGGLE_LIVE_STREAM])

    def test_tray_live_stream_toggle_is_persisted(self):
        """托盘关掉直播必须写盘：只改运行时的话，下次启动会被配置重新打开。"""
        service = unittest.mock.Mock()
        service.set_enabled.return_value = True
        saved = []
        with patch("lib.script.main.get_live_song_request_service", return_value=service), patch(
            "config.general_user_settings.save_general_values",
            side_effect=lambda values: saved.append(values),
        ):
            self.state._toggle_live_stream(False)

        self.assertEqual(saved, [{"LIVE_STREAM": {"enabled": False}}])

    def test_tray_live_stream_toggle_without_change_does_not_write(self):
        """状态没变就不写盘，避免每次点一下都改一次用户配置。"""
        service = unittest.mock.Mock()
        service.set_enabled.return_value = False
        with patch("lib.script.main.get_live_song_request_service", return_value=service), patch(
            "config.general_user_settings.save_general_values"
        ) as save:
            self.state._toggle_live_stream(True)

        save.assert_not_called()

    def test_persisting_the_live_stream_flag_keeps_other_settings(self):
        """只写 enabled，不能把用户填的房间号一起覆盖回默认值。"""
        saved = []
        with patch(
            "config.general_user_settings.save_general_values",
            side_effect=lambda values: saved.append(values),
        ):
            self.state._persist_live_stream_enabled(False)

        self.assertEqual(saved, [{"LIVE_STREAM": {"enabled": False}}])


if __name__ == "__main__":
    unittest.main()
