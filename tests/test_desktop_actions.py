from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.desktop_actions import (
    adjust_desktop_scale,
    dispatch_desktop_action,
)


class DesktopActionTests(unittest.TestCase):
    def setUp(self) -> None:
        # Other integration tests may leave the process-wide interaction-mode
        # service subscribed; desktop action dispatch must be verified in an
        # isolated event center.
        cleanup_event_center()

    def tearDown(self) -> None:
        cleanup_event_center()

    def test_scale_action_uses_shared_persistence_and_feedback(self) -> None:
        scale = Mock()
        scale.adjust_scale.return_value = 1.3
        messages = []
        get_event_center().subscribe(
            EventType.INFORMATION,
            lambda event: messages.append(event.data),
        )

        with patch(
            "lib.core.desktop_actions.get_user_scale_config",
            return_value=scale,
        ):
            self.assertEqual(adjust_desktop_scale(0.1), 1.3)

        scale.adjust_scale.assert_called_once_with(0.1)
        self.assertEqual(messages[-1]["text"], "缩放: 1.3（重启生效）")

    def test_all_command_panel_actions_have_backend_neutral_dispatch(self) -> None:
        received = []
        center = get_event_center()
        for event_type in (
            EventType.APP_QUIT,
            EventType.UI_CLICKTHROUGH_TOGGLE,
            EventType.MIC_STT_START,
            EventType.MIC_STT_STOP,
            EventType.INTERACTION_MODE_SET,
            EventType.UI_TRAY_MENU_REQUEST,
        ):
            center.subscribe(
                event_type,
                lambda event, kind=event_type: received.append((kind, event.data)),
            )

        launcher = Mock()
        with patch(
            "lib.core.desktop_actions.adjust_desktop_scale"
        ) as adjust:
            dispatch_desktop_action("scale_up")
            dispatch_desktop_action("scale_down")
        dispatch_desktop_action("close")
        dispatch_desktop_action("clickthrough", clickthrough_enabled=False)
        dispatch_desktop_action("chat_mode", chat_listening=False)
        dispatch_desktop_action("chat_mode", chat_listening=True)
        dispatch_desktop_action("interaction_mode")
        dispatch_desktop_action("more_functions")
        dispatch_desktop_action("launch_wuwa", launch_wuwa=launcher)

        self.assertEqual(adjust.call_args_list[0].args, (0.1,))
        self.assertEqual(adjust.call_args_list[1].args, (-0.1,))
        self.assertEqual(
            [kind for kind, _data in received],
            [
                EventType.APP_QUIT,
                EventType.UI_CLICKTHROUGH_TOGGLE,
                EventType.MIC_STT_START,
                EventType.MIC_STT_STOP,
                EventType.INTERACTION_MODE_SET,
                EventType.UI_TRAY_MENU_REQUEST,
            ],
        )
        launcher.assert_called_once_with()

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dispatch_desktop_action("missing")


if __name__ == "__main__":
    unittest.main()
