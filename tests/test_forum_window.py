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

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPainter
from PyQt5.QtWidgets import QApplication, QLabel, QMenu, QTextEdit, QWidget

from PIL import Image

from config.scale import scale_px
from lib.core.forum import (
    FORUM_DEFAULT_ACCENT,
    FORUM_ACCENTS,
    FORUM_MAX_CONTENT,
    ForumMessage,
    ForumPage,
)
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.image_loader import decode_image_frames
from lib.core.layer_manager import get_layer_manager
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.gif_loader import qimage_from_raster_frame
from lib.script.ui.forum_markup import (
    FORUM_EFFECT_TOKENS,
    FORUM_MARKUP_FORMATS,
    effect_tokens,
    marker_positions,
    span_at_cursor,
    to_html,
    toggle,
    visible_text,
)
from lib.script.ui.forum_style import (
    FORUM_ACCENT_LABELS,
    FORUM_TEXTURE_TINT_RATIO,
    forum_accent_color,
    forum_card_text_color,
    forum_card_text_size,
    forum_texture_color,
)
from lib.script.ui.forum_sticker import (
    FORUM_STICKER_ASSETS,
    STICKER_HEIGHT,
    ForumSticker,
    ink_bounds,
    sticker_frames,
)
from lib.script.ui.forum_text import (
    BOLD_OUTLINE_MAX_PX,
    BOLD_OUTLINE_MIN_PX,
    MarkupText,
    bold_outline_width,
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
    SCROLL_GAP,
    SCROLL_GUTTER,
    ForumCard,
    ForumWindow,
)
from lib.script.workbench.theme import get_workbench_colors


def message(index, *, accent="pink", content=None):
    return ForumMessage(
        id=100 - index,
        nickname=f"来访者{index}",
        content=content or f"第 {index} 条留言",
        accent=accent,
        created_at=1_700_000_000_000,
    )


def document_fragments(widget):
    """正文文档里的 `(文字, 字符格式)` 片段：用来核对哪一段真的被排版成粗体/斜体。"""
    pieces = []
    block = widget.document().begin()
    while block.isValid():
        piece = block.begin()
        while not piece.atEnd():
            fragment = piece.fragment()
            if fragment.isValid() and fragment.text():
                pieces.append((fragment.text(), fragment.charFormat()))
            piece += 1
        block = block.next()
    return pieces


def text_colors(widget):
    """正文文档里用到的前景色集合：换主题有没有刷到正文，看这个。"""
    return {fmt.foreground().color().name() for _text, fmt in document_fragments(widget)}


def markup_ink(text, size):
    """一段正文的墨迹像素数：量「加粗比普通粗多少」用，与主题无关。

    背景色取左上角那一颗（正文居中，角落一定是底色），只数明显偏离底色的像素；正文用深色，
    这样不依赖控件自己的底色是深是浅（控件单独 grab 出来时底色是浅的，没有窗口样式表）。
    """
    widget = MarkupText(
        to_html(text),
        font=get_ui_font(size=size),
        color="#101820",
        width_hint=430,
    )
    try:
        widget.setFixedWidth(430)
        widget.show()
        for _ in range(4):
            QApplication.processEvents()
        image = widget.grab().toImage()
        background = image.pixelColor(0, 0)
        total = 0
        for y in range(image.height()):
            for x in range(image.width()):
                colour = image.pixelColor(x, y)
                if (
                    abs(colour.red() - background.red())
                    + abs(colour.green() - background.green())
                    + abs(colour.blue() - background.blue())
                    > 60
                ):
                    total += 1
        return total
    finally:
        widget.deleteLater()
        QApplication.processEvents()


#: 底纹单通道均值偏移上限：花纹可以更深、可以带卡片色调，但不能压过卡片底色。
#: 深色主题的底纹从白出发，离中灰天然更远：实测最深的种子在深色主题下约 15、浅色主题下约 8.7，
#: 上限取两者之上并留出余量。种子底纹的两套主题都要跑，改 `CARD_TEXTURE_ALPHA_RANGE`、
#: `FORUM_TEXTURE_TINT_RATIO` 或平铺/线宽常数时要同步这里。
_TEXTURE_DELTA_BUDGET = 16
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

    def test_cards_keep_a_comfortable_gap_from_the_scroll_bar(self):
        messages = tuple(message(index) for index in range(24))
        self.window.resize(DEFAULT_WINDOW_WIDTH, 620)
        self.window.show()
        self.app.processEvents()
        self.window._on_page(ForumPage(messages=messages, mode="latest", total=24))
        for _ in range(4):
            self.window.layout().invalidate()
            self.app.processEvents()

        bar = self.window._scroll.verticalScrollBar()
        self.assertTrue(bar.isVisible())
        bar_left = bar.mapTo(self.window, QPoint(0, 0)).x()
        gaps = [
            bar_left - (card.mapTo(self.window, QPoint(0, 0)).x() + card.width())
            for card in self.window.findChildren(ForumCard)
        ]
        self.assertEqual(len(gaps), 24)
        # 卡片贴着滚动条会被读成「卡片右边框」，最后一列像被压住：右侧要留出 SCROLL_GAP。
        self.assertAlmostEqual(min(gaps), SCROLL_GAP, delta=2)
        # 空隙长在卡片墙上（host 的右边距），不是把滚动条自己推离窗口边。
        self.assertEqual(self.window._host_layout.contentsMargins().right(), SCROLL_GAP)

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

        # 宽度只有工作台的一半左右，三列仍要能并排放下：最小宽度由网格推出，并预留滚动条
        # 与它前面的空隙（滚动条一出现，视口就少这么多）。
        wall_layout = self.window._scroll.parentWidget().layout()
        margins = wall_layout.contentsMargins()
        needed = (
            COLUMN_COUNT * CARD_MIN_WIDTH
            + (COLUMN_COUNT - 1) * self.window._host_layout.spacing()
            + margins.left()
            + margins.right()
            + SCROLL_GUTTER
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
            content = card.findChild(QTextEdit, "ForumCardText")
            stamp = card.findChild(QLabel, "ForumCardMeta")

            name_top = name.mapTo(card, QPoint(0, 0))
            content_top = content.mapTo(card, QPoint(0, 0))
            stamp_top = stamp.mapTo(card, QPoint(0, 0))

            self.assertLessEqual(name_top.x(), content_top.x())
            self.assertLess(name_top.y(), stamp_top.y())
            self.assertGreater(stamp_top.y(), content_top.y())
            # 正文控件铺满列宽，居中由文档的块格式落实。
            self.assertTrue(
                int(content.document().begin().blockFormat().alignment())
                & int(Qt.AlignHCenter)
            )
            self.assertAlmostEqual(
                content_top.x() + content.width() / 2, card.width() / 2, delta=2
            )
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
            content = card.findChild(QTextEdit, "ForumCardText")
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
                sizes.append(card.findChild(QTextEdit, "ForumCardText").font().pixelSize())
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

    def test_card_text_renders_markup_as_rich_text_with_a_bold_outline(self):
        card = ForumCard(message(1, content="**雪绒**最*棒*"))
        try:
            content = card.findChild(QTextEdit, "ForumCardText")
            # 正文是只读富文本控件（原先是 QLabel）：QLabel 拿不到 QTextDocument，
            # 也没法给粗体片段单独加描边。
            self.assertIsInstance(content, MarkupText)
            pieces = dict(document_fragments(content))
            # 标记被吃掉，正文分成粗 / 平 / 斜三种片段。
            self.assertNotIn("*", "".join(pieces))
            self.assertEqual(set(pieces), {"雪绒", "最", "棒"})
            bold = pieces["雪绒"]
            # 粗体 = 字重拉到 Bold + 同色描边把笔画撑粗；UI 字体只有 Bold 一个字面，光靠
            # `<b>` 看不出区别，所以描边宽度跟着字号走。
            self.assertGreaterEqual(bold.fontWeight(), QFont.Bold)
            self.assertNotEqual(bold.textOutline().style(), Qt.NoPen)
            self.assertAlmostEqual(
                bold.textOutline().widthF(),
                bold_outline_width(content.font().pixelSize()),
                delta=0.01,
            )
            # 普通片段不加粗也不描边；斜体由 Qt 给没有斜体面的字体合成。
            self.assertLess(pieces["最"].fontWeight(), QFont.Bold)
            self.assertEqual(pieces["最"].textOutline().style(), Qt.NoPen)
            self.assertTrue(pieces["棒"].fontItalic())
            self.assertFalse(pieces["最"].fontItalic())
            self.assertFalse(bold.fontItalic())
        finally:
            card.deleteLater()

    def test_bold_outline_is_bounded_by_its_limits(self):
        # UI 字体只有 Bold 一个字面，加粗完全靠这层同色描边，所以笔宽是唯一的旋钮：
        # 必须大于 0（`QPen` 宽度 0 会被 Qt 当成 1px 的 cosmetic 笔，比 0.5px 还粗），
        # 也不能随字号无限变粗（2 倍字号的短句上会把字怀填死）。
        self.assertGreater(BOLD_OUTLINE_MIN_PX, 0)
        self.assertEqual(bold_outline_width(8), BOLD_OUTLINE_MIN_PX)
        self.assertAlmostEqual(bold_outline_width(17), 0.51, delta=0.01)
        self.assertEqual(bold_outline_width(34), BOLD_OUTLINE_MAX_PX)
        self.assertEqual(bold_outline_width(96), BOLD_OUTLINE_MAX_PX)
        widths = [bold_outline_width(size) for size in (10, 12, 17, 24, 34, 48)]
        self.assertEqual(widths, sorted(widths))
        for width in widths:
            self.assertGreaterEqual(width, BOLD_OUTLINE_MIN_PX)
            self.assertLessEqual(width, BOLD_OUTLINE_MAX_PX)

        # 实际排版出来的片段用的是同一个笔宽，而且短句那张卡真的落在上限上。
        card = ForumCard(message(1, content="**短**"))
        try:
            content = card.findChild(QTextEdit, "ForumCardText")
            self.assertEqual(content.font().pixelSize(), scale_px(17, min_abs=12) * 2)
            pieces = dict(document_fragments(content))
            self.assertEqual(pieces["短"].textOutline().widthF(), BOLD_OUTLINE_MAX_PX)
        finally:
            card.deleteLater()
            self.app.processEvents()

    def test_bold_ink_is_heavier_than_plain_without_filling_the_strokes(self):
        # 需求原话：「论坛卡片的粗体似乎会有额外描边，导致部分文字会融合细节特征」。加粗要看得出来
        # （墨迹明显多），又不能把笔画糊在一起（墨迹不能失控）。上下限按最容易糊的 2 倍字号量：
        # 现行口径（33px 给 0.99px 描边）实测 +27%；旧口径给 1.48px，同一把尺子上是 +37%，那时
        # 「加粗」两个字的笔画已经连成一片。上限取 0.32 就是要挡住旧口径。
        size = scale_px(17, min_abs=12) * 2
        plain = markup_ink("雪绒细节", size)
        bold = markup_ink("**雪绒细节**", size)
        ratio = (bold - plain) / plain
        self.assertGreater(plain, 0)
        self.assertGreater(ratio, 0.12)
        self.assertLess(ratio, 0.32)

    def test_card_text_wraps_at_the_real_column_width_without_clipping(self):
        card = ForumCard(message(1, content="**粗体**与*斜体*和__下划线__以及~~删除线~~"))
        try:
            for width in (CARD_MIN_WIDTH, CARD_MIN_WIDTH + 90):
                card.resize(width, card.sizeHint().height())
                card.show()
                self.app.processEvents()
                content = card.findChild(QTextEdit, "ForumCardText")
                document = content.document()
                # 量高不能顺带改排版宽度：正文要按真实列宽换行（曾经被 sizeHint 改成卡片最小
                # 宽度，卡片就裁掉最后一行），控件高度也要真的装得下排好的文档。
                self.assertAlmostEqual(
                    document.textWidth(), content.viewport().width(), delta=1
                )
                self.assertGreaterEqual(content.height() + 0.5, document.size().height())
        finally:
            card.deleteLater()
            self.app.processEvents()

    def test_card_text_keeps_written_html_literal_and_centers_every_line(self):
        card = ForumCard(message(1, content="<b>不是标签</b>\n第二行"))
        try:
            content = card.findChild(QTextEdit, "ForumCardText")
            self.assertIn(
                "<b>不是标签</b>",
                "".join(text for text, _fmt in document_fragments(content)),
            )
            self.assertIn("第二行", content.toPlainText())
            block = content.document().begin()
            while block.isValid():
                self.assertTrue(
                    int(block.blockFormat().alignment()) & int(Qt.AlignHCenter)
                )
                block = block.next()
        finally:
            card.deleteLater()

    def test_card_text_size_counts_visible_words_and_color_follows_the_theme(self):
        marked = ForumCard(message(1, content="**喵**"))
        plain = ForumCard(message(2, content="喵"))
        try:
            marked_text = marked.findChild(QTextEdit, "ForumCardText")
            plain_text = plain.findChild(QTextEdit, "ForumCardText")
            # 标记不算字数：`**喵**` 和 `喵` 一样是一个字，字号也该一样大。
            self.assertEqual(forum_card_text_size("**喵**"), forum_card_text_size("喵"))
            self.assertEqual(marked_text.font().pixelSize(), plain_text.font().pixelSize())
            self.assertEqual(plain_text.font().pixelSize(), forum_card_text_size("喵"))
            self.assertNotEqual(
                forum_card_text_color("dark"), forum_card_text_color("light")
            )
            self.assertEqual(
                text_colors(plain_text), {QColor(forum_card_text_color()).name()}
            )
            # 颜色写在字符格式里（粗体描边要用同一个颜色），换主题要显式重刷。
            light = get_workbench_colors("light")
            with patch(
                "lib.script.ui.forum_style.get_workbench_colors", return_value=light
            ):
                plain.refresh_theme()
            self.assertEqual(
                text_colors(plain_text), {QColor(light.text).name()}
            )
        finally:
            marked.deleteLater()
            plain.deleteLater()

    def test_format_buttons_wrap_the_selection_and_untoggle_it(self):
        buttons = self.window._format_buttons
        self.assertEqual(set(buttons), {fmt.key for fmt in FORUM_MARKUP_FORMATS})
        self.assertIn("QToolButton#ForumFormatButton", self.window.styleSheet())
        for fmt in FORUM_MARKUP_FORMATS:
            button = buttons[fmt.key]
            self.assertTrue(button.isCheckable())
            self.assertEqual(button.text(), fmt.button)
            self.assertEqual(button.cursor().shape(), Qt.PointingHandCursor)
            # 提示文案要说清标记本身，以及「选中 / 点亮 / 取消」三种用法。
            self.assertIn(fmt.marker, button.toolTip())
            self.assertIn(fmt.label, button.toolTip())
            self.assertIn(fmt.marker, self.window._input.toolTip())

        self.window._input.setText("飞行雪绒")
        self.window._input.setSelection(0, 4)
        buttons["bold"].click()
        self.assertEqual(self.window._input.text(), "**飞行雪绒**")
        self.assertTrue(buttons["bold"].isChecked())
        buttons["bold"].click()
        self.assertEqual(self.window._input.text(), "飞行雪绒")
        self.assertEqual(self.window._input.selectedText(), "飞行雪绒")
        self.assertFalse(buttons["bold"].isChecked())

    def test_format_button_arms_the_next_keystrokes_and_tracks_the_caret(self):
        self.window._input.setText("")
        self.window._input.setCursorPosition(0)
        bold = self.window._format_buttons["bold"]
        italic = self.window._format_buttons["italic"]
        bold.click()
        # 空输入点亮按钮：先落下标记对，光标停在中间，接着打的字自动是粗体。
        self.assertEqual(self.window._input.text(), "****")
        self.assertEqual(self.window._caret_position(), 2)
        self.assertTrue(bold.isChecked())
        self.window._input.insert("喵")
        self.assertEqual(self.window._input.text(), "**喵**")
        self.assertTrue(bold.isChecked())
        # 光标还在粗体里：斜体按钮不该跟着亮（`**` 是偶数星号串，不算一对斜体）。
        self.assertFalse(italic.isChecked())
        # 光标移出标记对，粗体按钮跟着熄灭。
        self.window._input.setCursorPosition(self.window._input.text().index("**", 1) + 2)
        self.assertFalse(bold.isChecked())

    def test_format_button_refuses_to_push_the_text_over_the_limit(self):
        full = "字" * FORUM_MAX_CONTENT
        self.window._input.setText(full)
        self.window._input.setSelection(0, FORUM_MAX_CONTENT)
        self.window._format_buttons["bold"].click()
        # 加上标记会超上限：正文一个字都不动，只在状态栏说明。
        self.assertEqual(self.window._input.text(), full)
        self.assertIn(str(FORUM_MAX_CONTENT), self.window._status.text())
        self.assertFalse(self.window._format_buttons["bold"].isChecked())

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
        # 底纹色跟着工作台主题走（深色提亮、浅色压暗），两套主题必须都跑：只跑当前主题的话，
        # 开发机上的亮色主题会把深色主题里的超预算种子放过去。
        for mode in ("dark", "light"):
            with self.subTest(mode=mode), patch(
                "lib.core.graphics.workbench_tokens.resolve_workbench_mode",
                return_value=mode,
            ):
                plain, _ = self._card_means(None, accent=FORUM_DEFAULT_ACCENT)
                for message_id in range(1, 60):
                    texture = card_texture(message_id)
                    means, spread = self._card_means(texture, accent=FORUM_DEFAULT_ACCENT)
                    delta = abs(sum(means) / 3 - sum(plain) / 3)
                    # 花纹可以更深、带卡片色调，但不能压过卡片本身和正文。
                    self.assertLessEqual(
                        delta, _TEXTURE_DELTA_BUDGET, (mode, texture.pattern)
                    )
                    self.assertLessEqual(
                        spread, _TEXTURE_TINT_BUDGET, (mode, texture.pattern)
                    )

    def test_texture_density_and_stroke_are_turned_up(self):
        """底纹契约：平铺更密、线宽更粗、颜色更深；改这几个常数要同步这里。"""
        self.assertLessEqual(max(CARD_TEXTURE_TILES), 22)
        self.assertLessEqual(max(CARD_TEXTURE_STROKE_DIVISORS), 6)
        self.assertGreaterEqual(CARD_TEXTURE_STROKE_BASE, 2)
        self.assertGreaterEqual(CARD_TEXTURE_ALPHA_RANGE[0], 20)
        self.assertLessEqual(CARD_TEXTURE_ALPHA_RANGE[1], 40)

    @staticmethod
    def _card_means(texture, accent=FORUM_DEFAULT_ACCENT):
        """在一张纯灰卡片上只画底纹（子控件全藏起来），返回逐通道均值与最大通道偏差。"""
        card = ForumCard(message(1, accent=accent, content="纹理"))
        card.texture = texture
        card.setStyleSheet("QFrame#ForumCard { background: #808080; border: none; }")
        card.resize(200, 140)
        # 正文是 MarkupText（QTextEdit），不再只是 QLabel：漏掉它就等于把正文像素也算进底纹。
        for child in card.findChildren(QWidget):
            child.hide()
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


class ForumStickerTests(unittest.TestCase):
    """效果令牌的贴图：正文里写了 `[雪豹]`，令牌洗掉、卡片底部贴一张会自己走的 gif。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_token_cards_get_a_sticker_at_the_bottom(self):
        plain = ForumCard(message(1, content="今天没有令牌"))
        token = ForumCard(message(2, content="看到[雪豹]了"))
        named = ForumCard(message(3, content="看到雪豹了"))
        try:
            self.assertEqual(plain.findChildren(ForumSticker), [])
            self.assertEqual(named.findChildren(ForumSticker), [])
            stickers = token.findChildren(ForumSticker)
            self.assertEqual(len(stickers), 1)
            sticker = stickers[0]
            self.assertEqual(sticker.path, FORUM_STICKER_ASSETS["[雪豹]"])
            # 令牌从正文里洗掉了，贴图是卡片最底部的一项、居中摆放。
            layout = token.layout()
            last = layout.itemAt(layout.count() - 1)
            self.assertIs(last.widget(), sticker)
            self.assertTrue(last.alignment() & Qt.AlignHCenter)
            self.assertIsInstance(layout.itemAt(layout.count() - 2).widget(), QLabel)
            # 贴图真的占高：同样的正文，带令牌的卡片比不带的高出一张贴图。
            self.assertGreater(token.sizeHint().height(), named.sizeHint().height())
        finally:
            for card in (plain, token, named):
                card.deleteLater()
            self.app.processEvents()

    def test_sticker_frames_are_trimmed_to_the_artwork_and_cached(self):
        path = FORUM_STICKER_ASSETS["[雪豹]"]
        decoded = decode_image_frames(path)
        raw = [qimage_from_raster_frame(frame) for frame in decoded]
        frames = sticker_frames(path, STICKER_HEIGHT)
        self.assertGreater(len(frames), 1)
        self.assertGreater(len(raw), 1)
        self.assertEqual(
            {(frame.width(), frame.height()) for frame in frames},
            {(frames[0].width(), frames[0].height())},
        )
        self.assertEqual(frames[0].height(), STICKER_HEIGHT)
        # 同一张 gif 的所有卡片共用一份解码结果。
        self.assertIs(sticker_frames(path, STICKER_HEIGHT), frames)
        # 贴图方块就是图案本身：按全部帧可见像素的并集裁掉 gif 自带的透明留白，再等比缩放到
        # STICKER_HEIGHT。并集这里用 Pillow 的 alpha 包围盒另算一遍（另一套实现），
        # 逐帧裁会让各帧对齐基准漂移，所以必须取并集。
        union = None
        for frame in decoded:
            alpha = Image.frombytes(
                "RGBA", (frame.width, frame.height), frame.pixels
            ).split()[3]
            left, top, right, bottom = alpha.getbbox()
            union = (
                (left, top, right, bottom)
                if union is None
                else (
                    min(union[0], left),
                    min(union[1], top),
                    max(union[2], right),
                    max(union[3], bottom),
                )
            )
        self.assertEqual(
            (ink_bounds(raw).width(), ink_bounds(raw).height()),
            (union[2] - union[0], union[3] - union[1]),
        )
        self.assertLess(union[3] - union[1], raw[0].height())
        self.assertEqual(
            (frames[0].width(), frames[0].height()),
            (
                round(STICKER_HEIGHT * (union[2] - union[0]) / (union[3] - union[1])),
                STICKER_HEIGHT,
            ),
        )

    def test_sticker_advances_on_the_global_gif_frame_event(self):
        sticker = ForumSticker(FORUM_STICKER_ASSETS["[雪豹]"])
        try:
            count = sticker.frame_count()
            self.assertGreater(count, 1)
            first = sticker.current_frame()
            self.assertIsNotNone(first)
            sticker.advance()
            self.assertEqual(sticker.frame_index(), 1 % count)
            # 帧由全局 GIF_FRAME 推进，和雪豹世界物体同一个时钟，不另起 QTimer。
            get_event_center().publish(Event(EventType.GIF_FRAME, {"frame_count": 1}))
            self.assertEqual(sticker.frame_index(), 2 % count)
            for _ in range(count):
                sticker.advance()
            self.assertEqual(sticker.frame_index(), 2 % count)
        finally:
            sticker.deleteLater()
            self.app.processEvents()

    def test_destroyed_sticker_releases_its_subscription(self):
        center = get_event_center()
        listeners = center._listeners  # 只读：核对订阅有没有跟着控件销毁一起退掉。
        before = len(listeners.get(EventType.GIF_FRAME, []))
        card = ForumCard(message(1, content="[雪豹]"))
        self.app.processEvents()
        self.assertEqual(len(listeners.get(EventType.GIF_FRAME, [])), before + 1)
        # 卡片重排是 setParent(None) + deleteLater()：贴图跟着卡片销毁，订阅必须一起退，
        # 否则事件中心会一直握着一个已经销毁的控件。
        card.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(len(listeners.get(EventType.GIF_FRAME, [])), before)


class ForumMarkupTests(unittest.TestCase):
    """行内标记的解析与发帖框补标记：纯文本进出，不建窗口。"""

    def test_paired_markers_become_tags(self):
        self.assertEqual(to_html("**粗体**"), "<b>粗体</b>")
        self.assertEqual(to_html("*斜体*"), "<i>斜体</i>")
        self.assertEqual(to_html("__下划线__"), "<u>下划线</u>")
        self.assertEqual(to_html("~~删除线~~"), "<s>删除线</s>")
        # `***` 是粗体叠斜体两层，没有单独的按钮。
        self.assertEqual(to_html("***粗斜体***"), "<b><i>粗斜体</i></b>")

    def test_markers_nest_and_the_longest_marker_wins(self):
        self.assertEqual(to_html("**粗 *斜* 体**"), "<b>粗 <i>斜</i> 体</b>")
        self.assertEqual(to_html("**粗**和**再粗**"), "<b>粗</b>和<b>再粗</b>")

    def test_unpaired_markers_and_written_html_stay_literal(self):
        # 没配对的标记按普通字符显示，不吞掉正文。
        self.assertEqual(to_html("**没收尾"), "**没收尾")
        self.assertEqual(visible_text("**没收尾"), "**没收尾")
        self.assertEqual(to_html("~~一半"), "~~一半")
        # 用户写的 HTML 先转义再套标签。
        self.assertEqual(to_html("<b>x</b> & y"), "&lt;b&gt;x&lt;/b&gt; &amp; y")
        self.assertEqual(to_html("**<b>**"), "<b>&lt;b&gt;</b>")

    def test_visible_text_is_what_the_reader_sees(self):
        self.assertEqual(visible_text("**喵**"), "喵")
        self.assertEqual(visible_text("__下划线__"), "下划线")
        self.assertEqual(visible_text("**粗 *斜* 体**"), "粗 斜 体")
        self.assertEqual(visible_text("没标记"), "没标记")

    def test_effect_tokens_are_washed_out_of_the_rendered_text(self):
        # `[雪豹]` 是效果令牌：正文里写它表示贴一张动图，令牌本身不出现在卡片上。
        self.assertEqual(to_html("今天看到[雪豹]路过"), "今天看到路过")
        self.assertEqual(visible_text("今天看到[雪豹]路过"), "今天看到路过")
        self.assertEqual(effect_tokens("今天看到[雪豹]路过"), ("[雪豹]",))
        self.assertEqual(effect_tokens("没有令牌"), ())
        self.assertEqual(effect_tokens("[雪豹]和[雪豹]"), ("[雪豹]",))
        # 令牌算效果、不算可见字数，也不参进底纹以外的排版。
        self.assertEqual(visible_text("[雪豹]"), "")

    def test_effect_tokens_are_stripped_after_markup_render(self):
        # 顺序很重要：先洗令牌会留下一对没有内容的星号，卡片上直接印出 `****`。
        self.assertEqual(to_html("**[雪豹]**"), "<b></b>")
        self.assertEqual(to_html("[雪豹]"), "")

    def test_every_effect_token_has_a_sticker_asset(self):
        # 令牌在 forum_markup、贴图在 forum_sticker：两边漂移就会在贴图时 KeyError。
        self.assertEqual(set(FORUM_EFFECT_TOKENS), set(FORUM_STICKER_ASSETS))

    def test_bold_markers_are_not_mistaken_for_italic(self):
        # `**粗体**` 的星号是偶数串，不能算成一对斜体，否则粗体按钮会让斜体按钮跟着亮。
        self.assertEqual(marker_positions("**粗体**", "*"), ())
        self.assertEqual(marker_positions("**粗体**", "**"), (0, 4))
        self.assertEqual(marker_positions("*斜体*", "*"), (0, 3))
        self.assertIsNone(span_at_cursor("**粗体**", 3, "*"))
        self.assertEqual(span_at_cursor("**粗体**", 3, "**"), (0, 4))

    def test_toggle_wraps_the_selection_and_untoggles_it(self):
        text, start, end = toggle("飞行雪绒", 0, 4, "**")
        self.assertEqual(text, "**飞行雪绒**")
        self.assertEqual((start, end), (2, 6))
        self.assertEqual(toggle(text, start, end, "**"), ("飞行雪绒", 0, 4))

    def test_toggle_at_the_caret_arms_the_next_keystrokes(self):
        text, start, end = toggle("", 0, 0, "**")
        # 空输入：光标落在标记对中间，接着打的字自动落在标记里。
        self.assertEqual(text, "****")
        self.assertEqual(start, end)
        self.assertEqual(text[:start] + "喵" + text[end:], "**喵**")
        # 再点一下取消，回到空输入。
        self.assertEqual(toggle(text, start, end, "**"), ("", 0, 0))

    def test_toggle_inside_bold_italic_peels_only_one_layer(self):
        # `***粗斜体***` 里点一次斜体只脱掉斜体标记，粗体留着。
        self.assertEqual(toggle("***粗斜体***", 4, 4, "*"), ("**粗斜体**", 3, 3))


if __name__ == "__main__":
    unittest.main()
