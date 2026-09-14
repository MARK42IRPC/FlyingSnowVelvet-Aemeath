import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMenu

from lib.core.forum import FORUM_ACCENTS, ForumMessage, ForumPage
from lib.script.ui.forum_style import FORUM_ACCENT_LABELS, forum_accent_color
from lib.script.ui.forum_window import (
    CARD_MIN_WIDTH,
    COLUMN_COUNT,
    ForumCard,
    ForumWindow,
)


def message(index, *, accent="pink", content=None):
    return ForumMessage(
        id=100 - index,
        nickname=f"来访者{index}",
        content=content or f"第 {index} 条留言",
        accent=accent,
        created_at=1_700_000_000_000,
    )


class FakeService:
    def __init__(self, *, reached_end=False, post_error=None):
        self.reached_end = reached_end
        self.loading_older = False
        self.post_error = post_error
        self.older_calls = 0
        self.refresh_calls = 0
        self.posts = []
        self.cleaned = False
        self._remaining = 0.0

    def cooldown_remaining(self):
        return self._remaining

    def refresh(self):
        self.refresh_calls += 1
        return True

    def load_older(self):
        if self.reached_end or self.loading_older:
            return False
        self.older_calls += 1
        return True

    def post(self, content, *, accent=""):
        self.posts.append((content, accent))
        if self.post_error:
            return self.post_error
        self._remaining = 12.0
        return None

    def cleanup(self):
        self.cleaned = True


class ForumWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = ForumWindow()
        self.addCleanup(self._dispose)

    def _dispose(self):
        self.window.cleanup()
        self.window.deleteLater()
        self.app.processEvents()

    def test_latest_page_renders_three_columns_of_accent_cards(self):
        messages = tuple(message(index, accent=FORUM_ACCENTS[index % 4]) for index in range(7))
        self.window._on_page(ForumPage(messages=messages, mode="latest", total=7))

        self.assertEqual(len(self.window._column_layouts), COLUMN_COUNT)
        cards = self.window.findChildren(ForumCard)
        self.assertEqual(len(cards), len(messages))
        self.assertEqual(
            {card.property("accent") for card in cards},
            {entry.accent for entry in messages},
        )
        stylesheet = self.window.styleSheet()
        for accent in FORUM_ACCENTS:
            self.assertIn(f'QFrame#ForumCard[accent="{accent}"]', stylesheet)
            self.assertIn(forum_accent_color(accent), stylesheet)
        self.assertIn("ForumAccentSwatch", stylesheet)
        self.assertIn("WorkbenchWindowButton", stylesheet)
        self.assertEqual(set(self.window._accent_buttons), set(FORUM_ACCENTS))
        self.assertEqual(
            self.window._accent_buttons["pink"].toolTip(),
            f"{FORUM_ACCENT_LABELS['pink']}色描边",
        )

    def test_cards_are_even_width_and_follow_their_content(self):
        short = message(1, content="短")
        long = message(2, content="很长的留言内容 " * 12)
        self.window._on_page(ForumPage(messages=(short, long), mode="latest", total=2))
        self.window.resize(1240, 800)
        self.window.show()
        self.app.processEvents()

        cards = {
            card.message.id: card for card in self.window.findChildren(ForumCard)
        }
        short_card, long_card = cards[short.id], cards[long.id]
        self.assertLess(short_card.sizeHint().height(), long_card.sizeHint().height())
        # 三列等分列宽，整数分配有 1px 取整差；卡片宽度只允许这一档误差。
        widths = [card.width() for card in cards.values()]
        self.assertLessEqual(max(widths) - min(widths), 2, widths)
        self.assertGreaterEqual(min(widths), CARD_MIN_WIDTH)

    def test_older_page_appends_and_bottom_scroll_requests_more(self):
        service = FakeService()
        self.window._service = service
        self.window._on_page(ForumPage(messages=(message(1), message(2)), mode="latest", total=4))
        self.window._on_page(ForumPage(messages=(message(3),), mode="older", total=4))

        self.assertEqual(len(self.window.findChildren(ForumCard)), 3)
        self.assertIn("已加载 3 条", self.window._status.text())

        self.window._on_scrolled(self.window._scroll.verticalScrollBar().maximum())
        self.assertEqual(service.older_calls, 1)

        service.reached_end = True
        self.window._on_scrolled(self.window._scroll.verticalScrollBar().maximum())
        self.assertEqual(service.older_calls, 1)

    def test_composer_posts_then_locks_the_button_for_the_cooldown(self):
        service = FakeService()
        self.window._service = service
        self.window._select_accent("cyan")
        self.window._input.setText("飞行雪绒加油")
        self.window._sync_composer_state()
        self.assertTrue(self.window._send_button.isEnabled())

        self.window._on_send()

        self.assertEqual(service.posts, [("飞行雪绒加油", "cyan")])
        self.assertEqual(self.window._input.text(), "")
        self.assertFalse(self.window._send_button.isEnabled())
        self.assertTrue(self.window._send_button.text().startswith("发送（"))

    def test_composer_reports_refusals_without_clearing_the_input(self):
        service = FakeService(post_error="发帖冷却中，请等待 8 秒")
        self.window._service = service
        self.window._input.setText("稍后再发")
        self.window._sync_composer_state()

        self.window._on_send()

        self.assertEqual(self.window._input.text(), "稍后再发")
        self.assertEqual(self.window._status.text(), "发帖冷却中，请等待 8 秒")

    def test_empty_input_keeps_the_send_button_disabled(self):
        service = FakeService()
        self.window._service = service
        self.window._input.setText("   ")
        self.window._sync_composer_state()

        self.assertFalse(self.window._send_button.isEnabled())
        self.window._on_send()
        self.assertEqual(service.posts, [])

    def test_callbacks_arrive_through_the_dispatched_signal(self):
        received = []
        self.window._dispatch(lambda: received.append("done"))
        self.assertEqual(received, [])
        self.app.processEvents()
        self.assertEqual(received, ["done"])

    def test_window_is_frameless_and_keeps_a_drag_handle(self):
        self.assertTrue(self.window.windowFlags() & Qt.FramelessWindowHint)
        self.assertIsNotNone(self.window._drag_handle)
        self.assertEqual(self.window._drag_handle.cursor().shape(), Qt.OpenHandCursor)

    def test_tray_menu_exposes_the_forum_entry(self):
        from lib.script.ui.tray_icon import TrayIcon

        tray = TrayIcon()
        opened = []
        try:
            with patch("lib.script.ui.tray_icon.TrayContextMenu", QMenu), patch.object(
                tray, "_is_autostart_enabled", return_value=False
            ), patch(
                "lib.script.ui.tray_icon.get_game_mode_service"
            ) as game_mode_service, patch(
                "lib.script.ui.forum_window.open_forum_window",
                lambda: opened.append(True),
            ):
                game_mode_service.return_value.is_enabled.return_value = False
                tray._create_menu()

                action = next(
                    action
                    for action in tray._menu.actions()
                    if action.text() == "雪绒论坛"
                )
                action.trigger()

            self.assertEqual(opened, [True])
        finally:
            tray.cleanup()
            self.app.processEvents()

    def test_open_forum_window_reuses_one_singleton(self):
        from lib.script.ui import forum_window as forum_module

        forum_module.cleanup_forum_window()
        fake = ForumWindow()
        self.addCleanup(fake.cleanup)
        self.addCleanup(forum_module.cleanup_forum_window)

        with patch.object(forum_module, "ForumWindow", lambda *a, **k: fake), patch.object(
            fake, "show_centered"
        ), patch.object(fake, "fade_in"), patch.object(fake, "refresh") as refresh:
            first = forum_module.open_forum_window()
            second = forum_module.open_forum_window()

        self.assertIs(first, fake)
        self.assertIs(second, fake)
        self.assertIs(forum_module.get_forum_window(), fake)
        self.assertEqual(refresh.call_count, 2)


if __name__ == "__main__":
    unittest.main()
