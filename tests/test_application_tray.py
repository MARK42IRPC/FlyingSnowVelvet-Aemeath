from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
