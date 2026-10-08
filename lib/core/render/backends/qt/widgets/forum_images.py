"""论坛帖子图片与列表行控件（档位 D）：从 `lib/script/ui/forum_board.py` 下沉。

三种图片视图（`ForumImageView` / `ForumImageThumb` / `ForumDetailImage`）与列表两行
（`ForumPostRow` / `ForumReplyRow`）是**纯 Qt 控件**：它们自己就是 `QLabel` / `QFrame`
子类，认得的是 `image_id` 与位图，做的是「等字节、铺位图、按栏宽重排」这类控件工具包
事实，没有后端中立描述的余地——抽象成协议只会得到带 Qt 返回值的协议。

按 `doc/render层边界契约.md` 档位 D 的判定标准：持有 `QWidget.rect()` / `QPixmap`、
且绘制实现不由本模块构造的，就是档位 D。这里不构造任何绘制实现，也不 import 档位 A
（`drawing/`）：位图由调用方（`CommunityService` 取回的字节）经 `set_data()` 交进来。

本模块不 import `lib.script`，因此不是「产品控件面」的一部分；`lib/script/ui/forum_board.py`
按原语义重新导出这几个名字，外部（含测试）的既有导入路径不变。视图样式
（`QLabel#ForumImageThumb` 等）仍写在 `lib/script/ui/forum_style.py`。
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import FORUM_IMAGES_PER_POST, ForumPost, ForumReply, format_timestamp
from lib.core.forum_colors import iter_color_tokens
from lib.core.forum_layout import FORUM_ALIGN_CENTER, FORUM_ALIGN_LEFT, FORUM_ALIGN_RIGHT
from lib.core.forum_markdown import plain_text
from lib.core.render.visuals.forum_visuals import FORUM_IMAGE_FRAME

#: UI 字体取用入口，类型是 `(size: int) -> QFont`；`configure_font_factory()` 可覆盖它。
#: 默认值是档位 B 的字体服务（`runtime/providers.py` 的 `QtFontProvider`），在构造第一个
#: 控件时才去注册表取——`lib/script/ui/render_bridge.py` 的 `ui_font()` 取的是同一个提供者，
#: 所以产品面注不注入都得到同一个 `QFont`；注入只是为了让产品面能显式装配。
_font_factory = None


def _default_font_factory():
    """档位 B 的字体提供者；未注册时退回 Qt 运行时字体注册表（也属档位 B）。"""
    from lib.core.render.registry import get_font_provider

    provider = get_font_provider()
    if provider is not None:
        return provider.ui_font
    from lib.core.render.backends.qt.runtime.font import get_ui_font

    return get_ui_font


def configure_font_factory(factory) -> None:
    """安装 UI 字体取用入口（档位 D 的装配点，`lib/script/ui/forum_board.py` 用它注入）。

    不注入也能工作：默认走档位 B 的字体服务。这个钩子存在只是为了保持「产品面决定自己
    看到的字体」这条原有语义，而不是让档位 D 静态依赖产品面。
    """
    global _font_factory
    _font_factory = factory


#: 列表摘要的长度（`ForumPostRow` 在接口没给摘要时用它兜底）。
EXCERPT_LENGTH = 72
#: 列表行缩略图与发帖页预览图的边长（正方形；图不裁切，按长边贴合进去）。
LIST_THUMB_SIZE = scale_px(54, min_abs=42)
COMPOSE_THUMB_SIZE = scale_px(68, min_abs=54)
#: 内存里最多留几张图的字节：磁盘缓存才是常态（`lib/core/forum_images.py`），
#: 这里只为了让来回切页时预览立刻就在。
THUMB_MEMORY_LIMIT = 128
#: 发帖页富文本正文的高度估算宽度（还没有真实列宽时用它量高）。
BODY_WIDTH_HINT = scale_px(420, min_abs=280)

#: 详情页正文里一张图最多铺成多少像素（宽 × 高，约 16 MiB 的 RGBA）。正文里的图铺满栏宽才显得
#: 完整，但极端宽高比的图按栏宽放大会很吓人：一张 1x2000 的 PNG 不到 100 字节，按 704 的栏宽等比
#: 放大要 702x1404000 的位图（约 4 GB），QLabel 的像素缓冲撑不住。所以超预算的图按同一个宽高比
#: 整体缩回来。
#:
#: 这条线只兜病态长条：临界栏宽 = sqrt(预算 × 宽高比)，所以 16:9 / 3:2 / 4:3 / 1:1 要栏宽 2000px
#: 以上才碰得到，1:2 要 1414px，1:4 要 1000px——都在常见窗口之外（默认窗口 620，撑满 1080p 也就
#: 1900 上下）。真要碰到，图也只是从「铺满栏宽」变成「按预算缩一点」，不会裁、不会变形。
FORUM_IMAGE_MAX_PIXELS = 4_000_000


def _font(size: int, *, bold: bool = False):
    """控件自己的字号：像素档由 `scale_px` 缩放，字体对象由档位 B 取。

    `font_factory` 不在这里写死：`render/` 不得 import 产品面（`forum_style` /
    `render_bridge` 都在 `lib/script/ui`），因此由调用方注入。`lib/script/ui/forum_board.py`
    注入的就是它原来的 `render_bridge.ui_font`，每个控件看到的字体与下沉前逐字段相同。
    """
    factory = _font_factory or _default_font_factory()
    font = factory(size=scale_px(size, min_abs=max(8, size - 2)))
    font.setBold(bold)
    return font


#: 段落对齐令牌到 Qt 对齐标志的映射；没写令牌的段落沿用调用方给的对齐。
_ALIGNMENTS = {
    FORUM_ALIGN_LEFT: Qt.AlignLeft,
    FORUM_ALIGN_CENTER: Qt.AlignHCenter,
    FORUM_ALIGN_RIGHT: Qt.AlignRight,
}


def _block_alignment(block) -> int:
    """这一段该靠哪边：写了 `[center]` / `[right]` 就按令牌走，否则左对齐。"""
    align = str(getattr(block, "align", "") or "").strip().lower()
    return _ALIGNMENTS.get(align, Qt.AlignLeft)


def _like_mark(liked: bool) -> str:
    """实心 / 空心小心形；UI 字体没有 U+2665 时靠系统字体回退。"""
    return "\u2665" if liked else "\u2661"


def _apply_like_state(button: QToolButton, liked: bool, count: int, prefix: str = "") -> None:
    button.setChecked(bool(liked))
    text = f"{_like_mark(liked)} {prefix}{max(0, int(count))}".strip()
    button.setText(text)
    button.setProperty("liked", "yes" if liked else "no")
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)


def _clear_layout(layout) -> None:
    """把一个（嵌套）布局里的控件全部摘掉并排队销毁，更深一层的布局一样处理。

    `QLayout.takeAt()` 只把子布局从这一层摘下来：布局本身还攥着自己的控件不放，得连它
    一起清，否则那些控件会留在页面上（`_clear_detail_body()` 的正文配图行就是这么漏的）。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)
            child.deleteLater()


def _color_span_at(text, caret: int, kind: str) -> tuple[int, int] | None:
    """光标落在哪一段同类颜色令牌里，给出 `(开始令牌起点, 这一段结束的位置)`。

    颜色令牌是「从出现处生效、到同类下一个令牌为止」（见 `lib/core/forum_colors.py`），所以
    按顺序扫一遍同类令牌就够：光标在开始令牌之后、下一个同类令牌之前，就是被这一段包着。
    """
    body = str(text or "")
    opening: int | None = None
    for start, _end, token_kind, value in iter_color_tokens(body):
        if token_kind != kind:
            continue
        if opening is not None and opening <= caret < start:
            return opening, start
        opening = start if value else None
    if opening is not None and caret >= opening:
        return opening, len(body)
    return None


class ForumImageView(QLabel):
    """帖子里的图片控件的共同部分：认一个 `image_id`、等字节、把位图画上去。

    字节由页面去服务层取（`CommunityService.load_thumbnail()`），`_refresh_thumbs()` 按控件树
    现铺；两种观感共用这一套接口，铺图的那段代码就不用分家：`ForumImageThumb` 是正方形的小
    缩略图（列表行、发帖页），`ForumDetailImage` 是正文里就地铺开的那一张整幅图。
    """

    def __init__(self, image_id, parent=None) -> None:
        super().__init__(parent)
        self.image_id = str(image_id or "")
        self._data = b""

    def set_data(self, data) -> bool:
        """把字节画上去；解码不出来就保持占位框（少一张预览不该打断整页）。"""
        raw = bytes(data or b"")
        if not raw:
            return False
        if raw == self._data:
            return True
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw):
            return False
        self._data = raw
        self._paint(pixmap)
        return True

    def has_data(self) -> bool:
        return bool(self._data)

    def _paint(self, pixmap: QPixmap) -> None:
        """把解出来的位图铺上去；尺寸由子类决定。"""
        raise NotImplementedError


class ForumImageThumb(ForumImageView):
    """一张图片的小预览：列表行与发帖页共用。

    给得出字节就直接画（发帖页刚上传的图本地就有），否则先摆一个占位空框，等页面把
    `CommunityService.load_thumbnail()` 取回来的字节交给 `set_data()`。描边与底色在样式表的
    `QLabel#ForumImageThumb` 里，尺寸由调用方给（列表行与发帖页两档）。

    `removable=True` 时点一下发 `clicked`（发帖页用它把这张图从帖子里摘掉）；列表行里不可移除，
    点击照常由父级（整行）接走，于是在列表里点缩略图与点这一行是同一件事。
    """

    clicked = pyqtSignal(str)

    def __init__(self, image_id, *, size: int, removable: bool = False, parent=None) -> None:
        super().__init__(image_id, parent)
        self._removable = bool(removable)
        self._size = max(8, int(size))
        self.setObjectName("ForumImageThumb")
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(_font(10))
        self.setText("图" if self.image_id else "?")
        self.setToolTip("点一下把这张图从帖子里摘掉" if self._removable else "帖子里的图片")
        if self._removable:
            self.setCursor(Qt.PointingHandCursor)

    def _paint(self, pixmap: QPixmap) -> None:
        self.setText("")
        self.setPixmap(
            pixmap.scaled(self._size, self._size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def mouseReleaseEvent(self, event) -> None:
        if self._removable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.image_id)
            return
        super().mouseReleaseEvent(event)


class ForumDetailImage(ForumImageView):
    """详情页正文里的一张图：就地替掉那句「【图片】」占位符。

    铺满正文栏：比栏宽的缩到栏宽，比栏窄的跟着放大，只等比缩放——不拉伸、不裁切、也不旋转，
    所以只要栏里放得下，图就是「尽可能大」的那一档。高度由原图宽高比推出来并钉死
    （`setFixedHeight()`），宽度交给布局跟着正文栏走；栏宽一变就重算一次（父控件上挂了 resize
    事件过滤器）。

    描边是样式表给的（`QLabel#ForumDetailImage`），所以算尺寸时要把那圈边框刨掉：
    QLabel 不缩放超出内容区的位图，算漏一步就会把图裁掉一圈。
    """

    def __init__(self, image_id, *, width_hint: int = BODY_WIDTH_HINT, parent=None) -> None:
        super().__init__(image_id, parent)
        self._pixmap: QPixmap | None = None
        self._natural = QSize()
        self._width_hint = max(1, int(width_hint))
        self._watched: QWidget | None = None
        self.setObjectName("ForumDetailImage")
        self.setAlignment(Qt.AlignCenter)
        self.setFont(_font(10))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setText("图片加载中…" if self.image_id else "图片")
        self.setToolTip("帖子里的图片")
        self.setMinimumSize(scale_px(140, min_abs=100), scale_px(84, min_abs=62))

    # ── 尺寸 ─────────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        size = self._desired_size()
        return size if not size.isEmpty() else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        """铺开之后不再要求「至少这么宽」：控件的最小宽度会把整个窗口钉住，收不回去。

        图的尺寸靠 `sizeHint()` 撑起来，所以这里让路不影响观感，只让窄窗口还能继续变窄。
        """
        if self._pixmap is None:
            return super().minimumSizeHint()
        return QSize(0, 0)

    def _frame(self) -> int:
        """边框占掉的宽度（左右各一条）。"""
        return 2 * int(FORUM_IMAGE_FRAME)

    def _column_width(self) -> int:
        """正文栏的可用宽度：父控件宽度减掉它自己那圈外边距。"""
        parent = self.parentWidget()
        width = parent.width() if parent is not None else 0
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            margins = layout.contentsMargins()
            width -= margins.left() + margins.right()
        return width if width > 0 else self._width_hint

    def _desired_size(self) -> QSize:
        """当前栏宽下图该占的位置（含描边）：铺满正文栏，只等比缩放。

        宽度**就是**栏宽——正文里的图是正文的一部分，比栏窄的图缩在左上角、右半边全是空白，
        看着就像排版坏了。高度按原图宽高比推出来，所以「尽可能大」与「不拉伸、不旋转」
        同时成立。

        极端宽高比的图会被 `FORUM_IMAGE_MAX_PIXELS` 收一道：放大的倍数与收窄的倍数都是同一个
        比例，所以收回来之后仍然只有等比缩放，宽高比不变。
        """
        if self._natural.isEmpty():
            return QSize()
        natural_width = max(1, self._natural.width())
        natural_height = max(1, self._natural.height())
        width = max(1, self._column_width() - self._frame())
        height = self._height_for_width(width, natural_width, natural_height)
        area = width * height
        if area > FORUM_IMAGE_MAX_PIXELS:
            # 收窄之后**只用新宽度重算高度**（而不是两个方向各缩一次）：两个方向各缩一次会各带
            # 一次取整，比例就对不上原位图了，位图按原比例铺进这个框会留出一条空白。
            shrink = (FORUM_IMAGE_MAX_PIXELS / float(area)) ** 0.5
            width = max(1, int(width * shrink))
            height = self._height_for_width(width, natural_width, natural_height)
        return QSize(width + self._frame(), height + self._frame())

    @staticmethod
    def _height_for_width(width: int, natural_width: int, natural_height: int) -> int:
        """按原图宽高比算出给定宽度对应的高度（只这一处做这个除法）。"""
        return max(1, int(round(width * natural_height / natural_width)))

    # ── 铺图与自适应 ─────────────────────────────────────────────────

    def _paint(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._natural = pixmap.size()
        self.setMinimumSize(0, 0)
        self.updateGeometry()
        self._fit_to_column()

    def _fit_to_column(self) -> None:
        """按当前栏宽重排一次：高度按宽高比钉死，位图跟着控件实际尺寸铺。

        高度走 `setFixedHeight()`、宽度留给布局，刻意**不**用 `setFixedSize()`：固定**宽度**会把
        控件的最小宽度一起钉死，图一铺开整个窗口就再也收不回去（QLayout 拿它当自己的最小宽度，
        实测 760 宽的窗口缩到 460 会被顶回来）。高度不钉死也有坑：布局在某一帧里没排开时会把
        控件压到最小高度，位图比内容区高，QLabel 不缩放、直接裁掉一条（离屏实测 704x353 的控件
        被压成 704x84，位图却是 702x351）——高度钉住，这一帧就不会出现。
        """
        if self._pixmap is None or self._natural.isEmpty():
            return
        size = self._desired_size()
        self.setFixedHeight(size.height())
        # 宽度是布局说了算，但当场先摆到位：不然第一帧会拿占位框的宽度去铺图，画出一张小图再
        # 跳到整栏宽（离屏实测的一帧闪烁）。布局随后按正文栏重排，宽了窄了都归它管。
        if self.width() != size.width():
            self.resize(size.width(), size.height())
        self.setText("")
        self._rescale_pixmap()

    def _rescale_pixmap(self) -> None:
        """按控件现在的尺寸重铺位图（只等比缩放）。

        量的主要是控件自己的 `width()` 而不是栏宽：宽度是布局给的，窄窗口下两者可能差一个滚动条。
        但还要跟 `_desired_size()` 取一次较小值：那是「栏宽 / 预算」算出来的上限，布局万一给得
        更宽（将来换了 sizePolicy、或哪一层加了 stretch），位图也不会跟着涨过上限——不裁切靠
        这个盒子，不超预算也靠它。宽高比固定，所以也不会有拉伸。尺寸没变就不重铺，
        `resizeEvent` 里可以放心多调。
        """
        if self._pixmap is None or self._natural.isEmpty():
            return
        limit = self._desired_size()
        box = QSize(
            max(1, min(self.width(), limit.width()) - self._frame()),
            max(1, min(self.height(), limit.height()) - self._frame()),
        )
        scaled = self._pixmap.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        current = self.pixmap()
        if current is None or current.isNull() or current.size() != scaled.size():
            self.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        """宽度是布局给的：布局一改尺寸就按新尺寸重铺位图。"""
        super().resizeEvent(event)
        self._rescale_pixmap()

    # ── 跟着正文栏走 ─────────────────────────────────────────────────

    def _watch_parent(self) -> None:
        parent = self.parentWidget()
        if parent is self._watched:
            return
        if self._watched is not None:
            self._watched.removeEventFilter(self)
        self._watched = parent
        if parent is not None:
            parent.installEventFilter(self)

    def event(self, event) -> bool:
        if event.type() in (QEvent.ParentChange, QEvent.Show):
            self._watch_parent()
            self._fit_to_column()
        return super().event(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._watched and event.type() == QEvent.Resize:
            self._fit_to_column()
        return super().eventFilter(watched, event)


class ForumPostRow(QFrame):
    """列表里的一行帖子；整行可点，点赞按钮单独吃掉自己的点击。"""

    activated = pyqtSignal(int)
    like_clicked = pyqtSignal(int, bool)

    def __init__(self, post: ForumPost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.post = post
        self.setObjectName("ForumPostRow")
        self.setCursor(Qt.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(
            scale_px(12, min_abs=10),
            scale_px(9, min_abs=7),
            scale_px(12, min_abs=10),
            scale_px(9, min_abs=7),
        )
        root.setSpacing(scale_px(10, min_abs=8))

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(scale_px(3, min_abs=2))

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(scale_px(6, min_abs=5))
        if post.is_pinned:
            title_row.addWidget(self._badge("置顶", "pinned"), 0)
        if post.is_locked:
            title_row.addWidget(self._badge("已锁定", "locked"), 0)
        self._title = QLabel(post.title or "（无标题）", self)
        self._title.setObjectName("ForumPostTitle")
        self._title.setFont(_font(14, bold=True))
        self._title.setWordWrap(True)
        title_row.addWidget(self._title, 1)
        text_box.addLayout(title_row)

        body = post.excerpt.strip() or plain_text(post.content, limit=EXCERPT_LENGTH)
        # 拉到了图片列表就铺缩略图，只在「接口只给了张数、没给图」时才退回文字提示。
        if post.image_count and not post.images and "【图片" not in body:
            body = f"{body}　【图片 ×{post.image_count}】"
        self._excerpt = QLabel(body or "（没有正文）", self)
        self._excerpt.setObjectName("ForumPostExcerpt")
        self._excerpt.setFont(_font(11))
        self._excerpt.setWordWrap(True)
        text_box.addWidget(self._excerpt)

        self._thumbs: list[ForumImageThumb] = []
        if post.images:
            strip = QHBoxLayout()
            strip.setContentsMargins(0, scale_px(2, min_abs=1), 0, 0)
            strip.setSpacing(scale_px(5, min_abs=4))
            for image in post.images[:FORUM_IMAGES_PER_POST]:
                thumb = ForumImageThumb(image.id, size=LIST_THUMB_SIZE, parent=self)
                self._thumbs.append(thumb)
                strip.addWidget(thumb, 0)
            strip.addStretch(1)
            text_box.addLayout(strip)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(scale_px(8, min_abs=6))
        author = post.author.label if post.author is not None else "未知用户"
        stamp = format_timestamp(post.last_reply_at or post.created_at)
        self._meta = QLabel(" · ".join(part for part in (author, stamp) if part), self)
        self._meta.setObjectName("ForumPostMeta")
        self._meta.setFont(_font(10))
        meta_row.addWidget(self._meta, 0)
        for tag in post.tags:
            chip = QLabel(f"#{tag}", self)
            chip.setObjectName("ForumPostTag")
            chip.setFont(_font(10))
            meta_row.addWidget(chip, 0)
        meta_row.addStretch(1)
        counts = QLabel(
            f"回复 {post.reply_count} · 点赞 {post.like_count} · 浏览 {post.view_count}",
            self,
        )
        counts.setObjectName("ForumPostMeta")
        counts.setFont(_font(10))
        meta_row.addWidget(counts, 0)
        text_box.addLayout(meta_row)
        root.addLayout(text_box, 1)

        self._like = QToolButton(self)
        self._like.setObjectName("ForumLikeButton")
        self._like.setCursor(Qt.PointingHandCursor)
        self._like.setFont(_font(11))
        self._like.setToolTip("点赞这篇帖子（需要登录）")
        self._like.clicked.connect(self._on_like)
        root.addWidget(self._like, 0)
        self.set_like(post.liked_by_me, post.like_count)

    def _badge(self, text: str, state: str) -> QLabel:
        badge = QLabel(text, self)
        badge.setObjectName("ForumBadge")
        badge.setProperty("state", state)
        badge.setFont(_font(10, bold=True))
        return badge

    def set_like(self, liked: bool, count: int) -> None:
        _apply_like_state(self._like, liked, count)

    def set_like_busy(self, busy: bool) -> None:
        self._like.setEnabled(not busy)

    def _on_like(self) -> None:
        self.like_clicked.emit(self.post.id, not self.post.liked_by_me)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._hits_like(event.pos()):
            self.activated.emit(self.post.id)
        super().mouseReleaseEvent(event)

    def _hits_like(self, point) -> bool:
        """点赞按钮自己处理点击，行不能再跟着进详情。"""
        if not self._like.isVisible():
            return False
        return self._like.geometry().contains(point)


class ForumReplyRow(QFrame):
    """一条回复：楼层号、作者、时间、正文，外带点赞与「回复这一层」。"""

    like_clicked = pyqtSignal(int, bool)
    reply_clicked = pyqtSignal(int)

    def __init__(
        self,
        reply: ForumReply,
        floor: int,
        *,
        parent_floor: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.reply = reply
        self.floor = int(floor)
        self.setObjectName("ForumReplyRow")

        root = QVBoxLayout(self)
        root.setContentsMargins(
            scale_px(12, min_abs=10),
            scale_px(8, min_abs=6),
            scale_px(12, min_abs=10),
            scale_px(8, min_abs=6),
        )
        root.setSpacing(scale_px(4, min_abs=3))

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(scale_px(7, min_abs=5))
        floor_label = QLabel(f"{self.floor} 楼", self)
        floor_label.setObjectName("ForumFloor")
        floor_label.setFont(_font(10, bold=True))
        head.addWidget(floor_label, 0)

        author = reply.author.label if reply.author is not None else "未知用户"
        prefix = f"回复 {parent_floor} 楼 · " if parent_floor else ""
        meta = QLabel(f"{prefix}{author} · {format_timestamp(reply.created_at)}", self)
        meta.setObjectName("ForumReplyMeta")
        meta.setFont(_font(10))
        head.addWidget(meta, 0)
        head.addStretch(1)

        self._reply_button = QToolButton(self)
        self._reply_button.setObjectName("ForumLinkButton")
        self._reply_button.setText("回复")
        self._reply_button.setCursor(Qt.PointingHandCursor)
        self._reply_button.setFont(_font(10))
        self._reply_button.setToolTip(f"回复 {floor} 楼")
        self._reply_button.clicked.connect(lambda: self.reply_clicked.emit(self.reply.id))
        head.addWidget(self._reply_button, 0)

        self._like = QToolButton(self)
        self._like.setObjectName("ForumLikeButton")
        self._like.setCursor(Qt.PointingHandCursor)
        self._like.setFont(_font(10))
        self._like.clicked.connect(
            lambda: self.like_clicked.emit(self.reply.id, not self.reply.liked_by_me)
        )
        head.addWidget(self._like, 0)
        root.addLayout(head)

        body = plain_text(reply.content, limit=2000) or "（空回复）"
        if reply.image_count:
            body = f"{body}　【图片 ×{reply.image_count}】"
        text = QLabel(body, self)
        text.setObjectName("ForumReplyText")
        text.setFont(_font(12))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(text)

        self.set_like(reply.liked_by_me, reply.like_count)

    def set_like(self, liked: bool, count: int) -> None:
        _apply_like_state(self._like, liked, count)

    def set_like_busy(self, busy: bool) -> None:
        self._like.setEnabled(not busy)


__all__ = [
    "ForumDetailImage",
    "ForumImageThumb",
    "ForumImageView",
    "ForumPostRow",
    "ForumReplyRow",
]
