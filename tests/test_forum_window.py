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

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter
from PyQt5.QtWidgets import QApplication, QLabel, QMenu

from config.scale import scale_px
from lib.core.forum import FORUM_DEFAULT_ACCENT, FORUM_ACCENTS, ForumMessage, ForumPage
from lib.core.layer_manager import get_layer_manager
from lib.script.ui.forum_style import (
    FORUM_ACCENT_LABELS,
    FORUM_TEXTURE_TINT_RATIO,
    forum_accent_color,
    forum_texture_color,
)
from lib.script.ui.forum_texture import (
    CARD_TEXTURE_ALPHA_RANGE,
    CARD_TEXTURE_COARSE_PATTERNS,
    CARD_TEXTURE_PATTERNS,
    CARD_TEXTURE_SIDES,
    CARD_TEXTURE_STROKE_BASE,
    CARD_TEXTURE_STROKE_DIVISORS,
    CARD_TEXTURE_TILES,
    CardTexture,
    card_texture,
    texture_seed,
)
from lib.script.ui.forum_window import (
    CARD_MIN_WIDTH,
    COLUMN_COUNT,
    DEFAULT_WINDOW_WIDTH,
    FORUM_NICKNAME_PLACEHOLDER,
    MIN_WINDOW_WIDTH,
    NICKNAME_MAX_LENGTH,
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


#: 底纹在纯灰卡片上的单通道均值偏移上限：更深的花纹允许到这个量级，再多就会压过卡片底色。
_TEXTURE_DELTA_BUDGET = 14
#: 逐像素最大通道偏差上限：底纹带 accent 色调后会明显大于中性色，但不能变成彩色噪点。
_TEXTURE_TINT_BUDGET = 24


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

    def post(self, content, *, nickname=None, accent=""):
        self.posts.append((content, nickname, accent))
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
        self.window.resize(620, 800)
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

        # 昵称留空就原样交给核心，由核心按服务端约定落成「匿名」。
        self.assertEqual(service.posts, [("飞行雪绒加油", "", "cyan")])
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

    def test_window_matches_the_workbench_priority_and_keeps_three_columns(self):
        flags = self.window.windowFlags()
        self.assertTrue(flags & Qt.Window)
        self.assertTrue(flags & Qt.FramelessWindowHint)
        # windowType 是掩码取出的枚举，不能用按位与判断；普通窗口 = Qt.Window。
        self.assertEqual(self.window.windowType(), Qt.Window)
        self.assertFalse(flags & Qt.WindowStaysOnTopHint)
        registered = {
            name for _layer, _z, _seq, name, _visible in get_layer_manager().snapshot()
        }
        self.assertNotIn("ForumWindow", registered)

        # 宽度只有工作台的一半左右，三列仍要能并排放下：最小宽度由网格推出。
        wall_layout = self.window._scroll.parentWidget().layout()
        margins = wall_layout.contentsMargins()
        needed = (
            COLUMN_COUNT * CARD_MIN_WIDTH
            + (COLUMN_COUNT - 1) * self.window._host_layout.spacing()
            + margins.left()
            + margins.right()
        )
        self.assertEqual(needed, MIN_WINDOW_WIDTH)
        self.assertEqual(self.window.minimumWidth(), MIN_WINDOW_WIDTH)
        self.assertEqual(self.window.width(), DEFAULT_WINDOW_WIDTH)
        self.assertLessEqual(self.window.width(), scale_px(660, min_abs=620))

    def test_nickname_box_is_small_and_explains_the_anonymous_default(self):
        nickname = self.window._nickname

        self.assertEqual(nickname.placeholderText(), FORUM_NICKNAME_PLACEHOLDER)
        self.assertIn("匿名", nickname.placeholderText())
        self.assertEqual(nickname.maxLength(), NICKNAME_MAX_LENGTH)
        # 小输入框：宽度刚好放下提示文案，不挤压正文输入框。
        self.assertGreaterEqual(
            nickname.width(),
            nickname.fontMetrics().horizontalAdvance(FORUM_NICKNAME_PLACEHOLDER),
        )

        self.window.resize(620, 800)
        self.window.show()
        self.app.processEvents()
        self.assertLess(nickname.width(), self.window._input.width())

    def test_composer_sends_the_typed_nickname(self):
        service = FakeService()
        self.window._service = service
        self.window._nickname.setText("  小明  ")
        self.window._input.setText("带昵称的留言")
        self.window._sync_composer_state()

        self.window._on_send()

        self.assertEqual(service.posts, [("带昵称的留言", "  小明  ", FORUM_DEFAULT_ACCENT)])
        self.assertEqual(self.window._nickname.text(), "  小明  ")

    def test_card_puts_nickname_top_left_content_centered_date_bottom_right(self):
        card = ForumCard(message(1, content="居中大字文案"))
        card.resize(CARD_MIN_WIDTH, card.sizeHint().height())
        card.show()
        try:
            self.app.processEvents()
            name = card.findChild(QLabel, "ForumCardName")
            content = card.findChild(QLabel, "ForumCardText")
            stamp = card.findChild(QLabel, "ForumCardMeta")

            name_top = name.mapTo(card, QPoint(0, 0))
            content_top = content.mapTo(card, QPoint(0, 0))
            stamp_top = stamp.mapTo(card, QPoint(0, 0))

            self.assertLessEqual(name_top.x(), content_top.x())
            self.assertLess(name_top.y(), stamp_top.y())
            self.assertAlmostEqual(
                content_top.x() + content.width() / 2, card.width() / 2, delta=2
            )
            self.assertGreater(stamp_top.y(), content_top.y())
            self.assertAlmostEqual(
                stamp_top.x() + stamp.width(),
                card.width() - card.layout().contentsMargins().right(),
                delta=2,
            )
        finally:
            card.deleteLater()
            self.app.processEvents()

    def test_card_fonts_follow_the_workbench_ramp(self):
        # 用一条够长的正文（≥ 24 字）把正文锁在基准字号上，只看三档字号的相对关系。
        card = ForumCard(message(1, content="这是一条足够长的留言内容用来验证卡片的基准字号不会变化"))
        try:
            name = card.findChild(QLabel, "ForumCardName")
            content = card.findChild(QLabel, "ForumCardText")
            stamp = card.findChild(QLabel, "ForumCardMeta")

            self.assertEqual(content.font().pixelSize(), scale_px(17, min_abs=12))
            self.assertEqual(name.font().pixelSize(), scale_px(14, min_abs=12))
            self.assertEqual(stamp.font().pixelSize(), scale_px(11, min_abs=9))
            self.assertGreater(content.font().pixelSize(), name.font().pixelSize())
            self.assertGreater(name.font().pixelSize(), stamp.font().pixelSize())
        finally:
            card.deleteLater()

    def test_card_text_grows_as_the_message_gets_shorter(self):
        contents = (
            "喵",
            "今天天气不错，出门散散步吧",
            "这是一条足够长的留言内容用来验证卡片的基准字号不会变化",
        )
        sizes = []
        for content in contents:
            card = ForumCard(message(1, content=content))
            try:
                sizes.append(card.findChild(QLabel, "ForumCardText").font().pixelSize())
            finally:
                card.deleteLater()

        base = scale_px(17, min_abs=12)
        short, middle, long = sizes
        # 字越少字越大：一个字直接顶到两倍，长文回到基准字号，中间线性过渡。
        self.assertEqual(short, base * 2)
        self.assertEqual(long, base)
        self.assertLess(base, middle)
        self.assertLess(middle, short)
        # 放大倍数收在 1x~2x 之间，不会更小也不会更大。
        for size in sizes:
            self.assertGreaterEqual(size, base)
            self.assertLessEqual(size, base * 2)

    def test_card_texture_is_stable_per_message(self):
        self.assertEqual(card_texture(42), card_texture(42))
        self.assertIsInstance(card_texture("坏 id"), CardTexture)

        patterns = set()
        for message_id in range(1, 200):
            texture = card_texture(message_id)
            patterns.add(texture.pattern)
            self.assertIn(texture.pattern, CARD_TEXTURE_PATTERNS)
            self.assertGreaterEqual(texture.alpha, CARD_TEXTURE_ALPHA_RANGE[0])
            self.assertLessEqual(texture.alpha, CARD_TEXTURE_ALPHA_RANGE[1])
            self.assertGreater(texture.tile, 0)
            self.assertGreaterEqual(texture.gap, 1)
            self.assertLess(texture.gap, texture.tile)
            self.assertLessEqual(abs(texture.origin_x), texture.tile)
            self.assertLessEqual(abs(texture.origin_y), texture.tile)
            self.assertTrue(0.0 <= texture.focus_x <= 1.0)
            self.assertTrue(0.0 <= texture.focus_y <= 1.0)
        # 不同留言要拿到不同花纹，否则整面墙看起来是同一个底。
        self.assertEqual(patterns, set(CARD_TEXTURE_PATTERNS))

    def test_card_texture_seed_hashes_the_card_content(self):
        base = message(1, content="同一段内容")
        copy = ForumMessage(
            id=base.id,
            nickname=base.nickname,
            content=base.content,
            accent=base.accent,
            created_at=base.created_at,
        )
        edited = ForumMessage(
            id=base.id,
            nickname=base.nickname,
            content="改过的内容",
            accent=base.accent,
            created_at=base.created_at,
        )

        # 种子是内容哈希而不是进程内 hash()：同一张卡片跨进程也拿同一套底纹。
        self.assertEqual(texture_seed(base), texture_seed(copy))
        self.assertEqual(texture_seed(42), 9514420467213374319)
        self.assertEqual(card_texture(base), card_texture(copy))
        # id 不变、只有正文变了也要换底纹。
        self.assertNotEqual(texture_seed(base), texture_seed(edited))

    def test_coarse_textures_seed_their_shape_and_stroke(self):
        self.assertTrue(set(CARD_TEXTURE_COARSE_PATTERNS) <= set(CARD_TEXTURE_PATTERNS))
        sides = set()
        strokes = set()
        coarse = set()
        for message_id in range(1, 200):
            texture = card_texture(message_id)
            sides.add(texture.sides)
            strokes.add(round(texture.stroke, 2))
            if texture.pattern in CARD_TEXTURE_COARSE_PATTERNS:
                coarse.add(texture.pattern)
            # 粗线花纹要拿到种子给出的边数、线宽、间隔与噪波盐值。
            self.assertGreaterEqual(texture.sides, 3)
            self.assertLessEqual(texture.sides, 8)
            self.assertGreater(texture.stroke, 0.0)
            self.assertGreater(texture.spacing, 0)
            self.assertGreater(texture.noise_salt, 0)
        # 多边形要从三角形铺到八边形，线宽也不能只有一档。
        self.assertEqual(sides, set(CARD_TEXTURE_SIDES))
        self.assertGreater(len(strokes), 1)
        self.assertEqual(coarse, set(CARD_TEXTURE_COARSE_PATTERNS))

    def test_seeded_textures_stay_within_the_contrast_budget(self):
        plain, _ = self._card_means(None, accent=FORUM_DEFAULT_ACCENT)
        for message_id in range(1, 60):
            texture = card_texture(message_id)
            means, spread = self._card_means(texture, accent=FORUM_DEFAULT_ACCENT)
            delta = abs(sum(means) / 3 - sum(plain) / 3)
            # 花纹可以更深、带卡片色调，但不能压过卡片本身和正文。
            self.assertLessEqual(delta, _TEXTURE_DELTA_BUDGET, texture.pattern)
            self.assertLessEqual(spread, _TEXTURE_TINT_BUDGET, texture.pattern)

    def test_texture_density_and_stroke_are_turned_up(self):
        """底纹契约：平铺更密、线宽更粗、颜色更深；改这几个常数要同步这里。"""
        self.assertLessEqual(max(CARD_TEXTURE_TILES), 22)
        self.assertLessEqual(max(CARD_TEXTURE_STROKE_DIVISORS), 6)
        self.assertGreaterEqual(CARD_TEXTURE_STROKE_BASE, 2)
        self.assertGreaterEqual(CARD_TEXTURE_ALPHA_RANGE[0], 20)
        self.assertLessEqual(CARD_TEXTURE_ALPHA_RANGE[1], 40)

    @staticmethod
    def _card_means(texture, accent=FORUM_DEFAULT_ACCENT):
        """在一张纯灰卡片上只画底纹（标签藏起来），返回逐通道均值与最大通道偏差。"""
        card = ForumCard(message(1, accent=accent, content="纹理"))
        card.texture = texture
        card.setStyleSheet("QFrame#ForumCard { background: #808080; border: none; }")
        card.resize(200, 140)
        for label in card.findChildren(QLabel):
            label.hide()
        image = QImage(200, 140, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0))
        painter = QPainter(image)
        card.render(painter)
        painter.end()
        card.deleteLater()

        totals = [0, 0, 0]
        spread = 0
        sampled = 0
        for x in range(0, 200, 2):
            for y in range(0, 140, 2):
                red, green, blue = image.pixelColor(x, y).getRgb()[:3]
                totals[0] += red
                totals[1] += green
                totals[2] += blue
                spread = max(spread, max(red, green, blue) - min(red, green, blue))
                sampled += 1
        return [value / sampled for value in totals], spread

    def test_texture_color_blends_the_neutral_with_the_card_accent(self):
        """底纹颜色 = 中性色 + accent 描边色按比例混合；不带 accent 时是纯中性色。"""
        self.assertGreater(FORUM_TEXTURE_TINT_RATIO, 0.0)
        expected = {
            "dark": {"pink": "#ffd5e4", "cyan": "#d1edff", "blue": "#d8e1ff", "snow": "#f2f5fa"},
            "light": {"pink": "#5a2a3e", "cyan": "#1e4056", "blue": "#242e50", "snow": "#393e46"},
        }
        for mode, table in expected.items():
            with self.subTest(mode=mode), patch(
                "lib.core.graphics.workbench_tokens.resolve_workbench_mode",
                return_value=mode,
            ):
                self.assertEqual(
                    forum_texture_color(),
                    "#ffffff" if mode == "dark" else "#000000",
                )
                for accent, color in table.items():
                    with self.subTest(accent=accent):
                        self.assertEqual(forum_texture_color(accent=accent), color)
                        # 底纹色和描边色同源：色调方向必须一致（粉偏红、青偏蓝）。
                        stroke = QColor(forum_accent_color(accent, mode))
                        texture = QColor(color)
                        self.assertEqual(
                            (texture.red() - texture.green() > 0),
                            (stroke.red() - stroke.green() > 0),
                        )

    def test_texture_tints_the_card_and_follows_the_theme(self):
        """渲染核验：底纹把 accent 色调带上卡片，深色主题提亮、浅色主题压暗。"""
        for mode, direction in (("dark", 1), ("light", -1)):
            for accent in ("pink", "cyan", "blue"):
                with self.subTest(mode=mode, accent=accent), patch(
                    "lib.core.graphics.workbench_tokens.resolve_workbench_mode",
                    return_value=mode,
                ):
                    plain, plain_spread = self._card_means(None, accent=accent)
                    self.assertLessEqual(plain_spread, 3)
                    means, spread = self._card_means(
                        CardTexture(pattern="hexes", alpha=CARD_TEXTURE_ALPHA_RANGE[1], tile=16),
                        accent=accent,
                    )
                    delta = sum(means) / 3 - sum(plain) / 3
                    # 深色主题偏亮纹理、浅色主题偏暗纹理，幅度受预算约束。
                    self.assertGreater(direction * delta, 0, (mode, accent))
                    self.assertLessEqual(abs(delta), _TEXTURE_DELTA_BUDGET, (mode, accent))
                    self.assertLessEqual(spread, _TEXTURE_TINT_BUDGET, (mode, accent))
                    # 色调方向与 accent 一致：红绿偏差、蓝绿偏差的符号都要对上。
                    stroke = QColor(forum_accent_color(accent, mode))
                    tinted = [value - base for value, base in zip(means, plain)]
                    self.assertGreater(
                        (tinted[0] - tinted[1]) * (stroke.red() - stroke.green()),
                        0,
                        (mode, accent),
                    )
                    self.assertGreater(
                        (tinted[2] - tinted[1]) * (stroke.blue() - stroke.green()),
                        0,
                        (mode, accent),
                    )
        self.app.processEvents()

    def test_accent_labels_cover_every_core_accent(self):
        # 档位来自核心、中文标签在样式模块，两边漂移就会在铺色板时 KeyError。
        self.assertEqual(set(FORUM_ACCENT_LABELS), set(FORUM_ACCENTS))

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
