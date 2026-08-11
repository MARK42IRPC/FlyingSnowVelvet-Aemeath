from __future__ import annotations

import ctypes
import os
import unittest

from lib.core.dx_bridge.offscreen import FSDX_ABI_VERSION, find_dx_library
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.tray_host import (
    FSDX_EVENT_TRAY_COMMAND,
    DxTrayHost,
    _TrayState,
)
from lib.core.tray_host import TrayCommand, TrayMenuState


@unittest.skipUnless(
    os.name == "nt" and find_dx_library() is not None,
    "DirectX tray integration requires Windows and a built DX DLL",
)
class DxTrayHostTests(unittest.TestCase):
    def setUp(self):
        self.context = DxLoopContext()
        self.host = DxTrayHost(self.context, warp=True)
        if not self.host.initialize():
            self.host.cleanup()
            self.skipTest(f"Windows notification area unavailable: {self.host.last_error}")

    def tearDown(self):
        self.host.cleanup()
        self.host.cleanup()

    def test_state_and_visibility_are_idempotent(self):
        self.assertEqual(ctypes.sizeof(_TrayState), 24)
        self.assertEqual(self.host.native_handle, int(self.host.native_handle))
        self.assertTrue(self.host.is_visible())

        self.host.hide()
        self.assertFalse(self.host.is_visible())
        self.host.show()
        self.assertTrue(self.host.is_visible())

        self.host.begin_shutdown()
        self.assertFalse(self.host.is_visible())
        self.host.begin_shutdown()

    def test_native_menu_commands_are_delivered_on_owner_thread(self):
        received = []
        self.host.connect_announcement_requested(lambda: received.append("announcement"))
        self.host.connect_quit_requested(lambda: received.append("quit"))
        commands = []
        self.host.connect_command_requested(lambda command, checked: commands.append((command, checked)))

        user32 = ctypes.windll.user32
        post = user32.PostMessageW
        post.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        post.restype = ctypes.c_int
        hwnd = ctypes.c_void_p(self.host.native_handle)
        self.assertTrue(post(hwnd, 0x0111, int(TrayCommand.ANNOUNCEMENT), 0))
        self.assertTrue(post(hwnd, 0x0111, int(TrayCommand.QUIT), 0))
        for command in (
            TrayCommand.TOGGLE_GAME_MODE,
            TrayCommand.TOGGLE_CLICKTHROUGH,
            TrayCommand.TOGGLE_AUTOSTART,
            TrayCommand.OPEN_CMD,
            TrayCommand.CLEANUP_DESKTOP,
            TrayCommand.CLEANUP_CACHE,
            TrayCommand.CLEANUP_HISTORY,
            TrayCommand.OPEN_AUTHOR_PAGE,
            TrayCommand.OPEN_SETTINGS,
        ):
            self.assertTrue(post(hwnd, 0x0111, int(command), 0))

        events = self.host.poll_events()
        self.assertEqual(events, (FSDX_EVENT_TRAY_COMMAND,) * 11)
        self.assertEqual(received, ["announcement", "quit"])
        self.assertEqual(
            commands,
            [
                (TrayCommand.TOGGLE_GAME_MODE, True),
                (TrayCommand.TOGGLE_CLICKTHROUGH, True),
                (TrayCommand.TOGGLE_AUTOSTART, True),
                (TrayCommand.OPEN_CMD, None),
                (TrayCommand.CLEANUP_DESKTOP, None),
                (TrayCommand.CLEANUP_CACHE, None),
                (TrayCommand.CLEANUP_HISTORY, None),
                (TrayCommand.OPEN_AUTHOR_PAGE, None),
                (TrayCommand.OPEN_SETTINGS, None),
            ],
        )

    def test_menu_state_is_forwarded_to_native_tray(self):
        self.host.set_menu_state(TrayMenuState(game_mode_enabled=True, autostart_enabled=True))
        self.assertEqual(self.host._menu_state.game_mode_enabled, True)
        self.assertEqual(self.host._menu_state.autostart_enabled, True)


if __name__ == "__main__":
    unittest.main()
