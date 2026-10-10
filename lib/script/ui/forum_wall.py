"""雪绒论坛窗口的「留言墙 / 页内发帖框」：卡片墙、页签与底栏输入。

批次 3 后续轮次从 `forum_window.ForumWindow` 切出。论坛窗口把三个子页面（留言墙 / 主论坛 /
账号页）与底部发帖框拼在一个工作台外壳里；这一轮把**留言墙本体**与**窗口自己的发帖框**那一半
搬出来——`_build_ui()` 搭外壳与三页堆栈，`_build_header()` / `_build_nav()` 两个顶部区块，
`_build_wall()` 的三列卡片墙，`_build_composer()` 的底栏输入框与格式按钮，以及输入框颜色
预览 `_sync_input_colors()`。服务协调、滚动分页、渲染与生命周期仍留在 `ForumWindow` 本体。

切分保持逐行等价：`ForumWallMixin` 的方法体与搬出前一致（缩进也未变），`ForumWindow` 只是
多继承本 mixin。混入方法按 `self` 解析 `_service` / `_messages` / `columns` 等窗口状态，与
`ForumWindow.__init__` 同源。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum import (
    FORUM_ACCENTS,
    FORUM_MAX_CONTENT,
)
from lib.core.forum_session import is_logged_in
from lib.script.ui.forum_account import ForumAccountPage
from lib.script.ui.forum_board import ForumBoardPage
from lib.script.ui.forum_color_control import ForumColorControl, format_button_font
from lib.script.ui.forum_markup import FORUM_MARKUP_FORMATS
from lib.script.ui.forum_style import FORUM_ACCENT_LABELS, forum_stylesheet
from lib.script.ui.forum_wall_layout import (
    COLUMN_COUNT,
    COLUMN_SPACING,
    FORUM_DEFAULT_PAGE,
    FORUM_HEADER_NOTICE,
    FORUM_NICKNAME_PLACEHOLDER,
    FORUM_PAGES,
    NICKNAME_MAX_LENGTH,
    SCROLL_GAP,
    WALL_MARGIN,
)
from lib.script.ui.render_bridge import ui_font as get_ui_font
from lib.script.ui.workbench_components import create_window_button
from lib.script.ui.workbench_settings_layout import SmoothScrollArea


class ForumWallMixin:
    """留言墙、页签与底栏发帖框的装配；由 `ForumWindow` 混入。"""

    def _build_ui(self) -> None:
        self.setStyleSheet(forum_stylesheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        self._nav = self._build_nav()
        root.addWidget(self._nav)

        # 三个子页面共用一块显示区：留言墙还是原来那面卡片墙，另外两个是主站社区页。
        self._stack = QStackedWidget(self)
        self._stack.setObjectName("ForumPageStack")
        self._wall = self._build_wall()
        self._board = ForumBoardPage(session=self._session_store, api=self._api, parent=self)
        self._account = ForumAccountPage(session=self._session_store, api=self._api, parent=self)
        # 未登录时主论坛的「去登录」直接把用户送到账号页。
        self._board.login_requested.connect(lambda: self.set_page("account"))
        # 账号页「最新帖子」点一行：切到主论坛并直接进这篇帖子的详情。
        self._account.post_requested.connect(self._open_post_in_board)
        self._board.subtitle_changed.connect(self._sync_subtitle)
        self._account.subtitle_changed.connect(self._sync_subtitle)
        for widget in (self._wall, self._board, self._account):
            self._stack.addWidget(widget)
        root.addWidget(self._stack, 1)

        self._composer = self._build_composer()
        root.addWidget(self._composer)

        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(scale_px(18, min_abs=15), scale_px(18, min_abs=15))
        self._size_grip.raise_()
        self._size_grip.setVisible(not self._embedded)
        self.set_page(FORUM_DEFAULT_PAGE, refresh=False)

    def _build_header(self) -> QWidget:
        header = QFrame(self)
        header.setObjectName("ForumHeader")
        # 三行页眉：标题、副标题、社区内容提示；比原来高一行小字。
        header.setFixedHeight(scale_px(82, min_abs=72))
        self.set_drag_handle(header)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(scale_px(1, min_abs=1))
        title = QLabel("雪绒论坛", header)
        title.setObjectName("ForumTitle")
        title_font = get_ui_font(size=scale_px(19, min_abs=16))
        title_font.setBold(True)
        title.setFont(title_font)
        self._subtitle = QLabel("雪绒留言墙 · 正在读取…", header)
        self._subtitle.setObjectName("ForumSubtitle")
        self._subtitle.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        title_box.addWidget(title)
        title_box.addWidget(self._subtitle)

        # 页眉小字：留言是社区内容，不是官方公告，标出来免得被当成官方口径。
        self._notice = QLabel(FORUM_HEADER_NOTICE, header)
        self._notice.setObjectName("ForumHeaderNotice")
        self._notice.setFont(get_ui_font(size=scale_px(9, min_abs=8)))
        title_box.addWidget(self._notice)

        self._refresh_button = QPushButton("刷新", header)
        self._refresh_button.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._refresh_button.clicked.connect(self.refresh)

        close_button = create_window_button(
            header, QStyle.SP_TitleBarCloseButton, "关闭论坛", self.fade_out, danger=True
        )

        layout = QHBoxLayout(header)
        layout.setContentsMargins(
            scale_px(18, min_abs=14),
            scale_px(10, min_abs=8),
            scale_px(12, min_abs=9),
            scale_px(10, min_abs=8),
        )
        layout.setSpacing(scale_px(10, min_abs=8))
        layout.addLayout(title_box, 1)
        layout.addWidget(self._refresh_button, 0)
        layout.addWidget(close_button, 0)
        return header

    def _build_nav(self) -> QWidget:
        """导航条：留言墙 / 主论坛 / 账号页三个页签，右侧是当前账号状态。"""
        bar = QFrame(self)
        bar.setObjectName("ForumToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(scale_px(16, min_abs=13), 0, scale_px(16, min_abs=13), 0)
        layout.setSpacing(scale_px(4, min_abs=3))
        self._tab_buttons: dict[str, QToolButton] = {}
        for key, label in FORUM_PAGES:
            button = QToolButton(bar)
            button.setObjectName("ForumTab")
            button.setText(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(get_ui_font(size=scale_px(13, min_abs=11)))
            button.clicked.connect(lambda _checked=False, target=key: self.set_page(target))
            self._tab_buttons[key] = button
            layout.addWidget(button, 0)
        layout.addStretch(1)
        self._account_badge = QLabel("未登录", bar)
        self._account_badge.setObjectName("ForumHint")
        self._account_badge.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        layout.addWidget(self._account_badge, 0)
        return bar

    # ── 子页面导航 ───────────────────────────────────────────────────

    def page(self) -> str:
        """当前子页面的 key（`wall` / `board` / `account`）。"""
        return self._page

    def _open_post_in_board(self, post_id) -> None:
        """从账号页跳到主论坛的某篇帖子；没登录时按钮本来就点不到。"""
        if not post_id:
            return
        self.set_page("board")
        self._board.open_post(int(post_id))

    def set_page(self, name: str, *, refresh: bool = True) -> None:
        """切换子页面；`refresh=False` 只摆位置（构造期用）。"""
        key = str(name or "").strip()
        if key not in self._tab_buttons:
            key = FORUM_DEFAULT_PAGE
        self._page = key
        for page_key, button in self._tab_buttons.items():
            button.setChecked(page_key == key)
        self._stack.setCurrentWidget(
            {"wall": self._wall, "board": self._board, "account": self._account}[key]
        )
        # 发帖框是留言墙的：主论坛有自己的回复框，账号页不需要输入。
        self._composer.setVisible(key == "wall")
        self._sync_subtitle()
        if refresh:
            self._refresh_page(key)

    def _refresh_page(self, key: str) -> None:
        """切页签只在「这一页还没拉过数据」时请求一次，来回切不重复发请求。"""
        if key == "wall":
            if not self._wall_loaded:
                self.refresh()
            return
        page = self._board if key == "board" else self._account
        if page.needs_initial_load():
            page.refresh()

    def _sync_subtitle(self) -> None:
        if self._page == "wall":
            self._set_subtitle(self._wall_subtitle)
            return
        page = self._board if self._page == "board" else self._account
        self._set_subtitle(page.subtitle())

    def _on_session_changed(self, session) -> None:
        """登录态变化：导航条右侧的小字跟着变，账号页的副标题也重算。"""
        logged_in = is_logged_in(session)
        label = session.user.label if logged_in else ""
        self._account_badge.setText(f"已登录：{label}" if logged_in else "未登录")
        if self._page == "account":
            self._sync_subtitle()

    def _build_wall(self) -> QWidget:
        wall = QWidget(self)
        wall_layout = QVBoxLayout(wall)
        wall_layout.setContentsMargins(
            WALL_MARGIN,
            scale_px(12, min_abs=10),
            WALL_MARGIN,
            scale_px(7, min_abs=6),
        )
        wall_layout.setSpacing(scale_px(10, min_abs=8))

        # 平滑滚动：复用设置页那套把离散滚轮步进转成短动画的容器，
        # 留言墙的滚动手感与工作台一致，不再是一格一格地跳。
        self._scroll = SmoothScrollArea(wall)
        self._scroll.setObjectName("ForumScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget(self._scroll)
        host.setObjectName("ForumColumnHost")
        self._host = host
        host_layout = QHBoxLayout(host)
        # 只在右侧留出滚动条的空隙：视口宽度不含滚动条，卡片会正好顶在条子上。
        host_layout.setContentsMargins(0, 0, SCROLL_GAP, 0)
        host_layout.setSpacing(COLUMN_SPACING)
        self._host_layout = host_layout
        for index in range(COLUMN_COUNT):
            column = QWidget(host)
            column.setObjectName("ForumColumn")
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(COLUMN_SPACING)
            column_layout.addStretch(1)
            host_layout.addWidget(column, 1)
            self._column_layouts.append(column_layout)
            self._column_heights.append(0)
        self._scroll.setWidget(host)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self._scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        self._scroll.viewport().installEventFilter(self)
        wall_layout.addWidget(self._scroll, 1)

        self._status = QLabel("正在连接雪绒论坛…", wall)
        self._status.setObjectName("ForumStatus")
        self._status.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        wall_layout.addWidget(self._status, 0)
        return wall

    def _build_composer(self) -> QWidget:
        composer = QFrame(self)
        composer.setObjectName("ForumComposer")
        layout = QVBoxLayout(composer)
        layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(11, min_abs=9),
            scale_px(16, min_abs=13),
            scale_px(11, min_abs=9),
        )
        layout.setSpacing(scale_px(9, min_abs=7))

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(scale_px(10, min_abs=8))

        accent_row = QHBoxLayout()
        accent_row.setContentsMargins(0, 0, 0, 0)
        accent_row.setSpacing(scale_px(5, min_abs=4))
        accent_label = QLabel("卡片描边", composer)
        accent_label.setObjectName("ForumHint")
        accent_label.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        accent_row.addWidget(accent_label)
        self._accent_buttons: dict[str, QToolButton] = {}
        for accent in FORUM_ACCENTS:
            button = QToolButton(composer)
            button.setObjectName("ForumAccentSwatch")
            button.setProperty("accent", accent)
            button.setCheckable(True)
            button.setChecked(accent == self._selected_accent)
            button.setToolTip(f"{FORUM_ACCENT_LABELS[accent]}色描边")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, target=accent: self._select_accent(target)
            )
            self._accent_buttons[accent] = button
            accent_row.addWidget(button)
        self._select_accent(self._selected_accent)
        top_row.addLayout(accent_row, 0)

        # 正文颜色与描边颜色：各一组「色相 + 明度」滑条，外加一枚跟随主题的重置。
        # 原有的「卡片描边」档位仍在左边，管的是卡片边框；这两组管的是卡里的字。
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(scale_px(8, min_abs=6))
        self._text_color_picker = ForumColorControl(composer, "文字颜色")
        self._outline_color_picker = ForumColorControl(composer, "描边颜色")
        color_row.addWidget(self._text_color_picker, 0)
        color_row.addWidget(self._outline_color_picker, 0)
        color_row.addStretch(1)
        # 颜色控件单独占一行：挤进昵称那一行会把两个滑条压到看不出渐变，
        # 「拖哪个位置是什么颜色」当场就看不出来了。
        layout.addLayout(color_row)

        self._nickname = QLineEdit(composer)
        self._nickname.setObjectName("ForumNickname")
        self._nickname.setPlaceholderText(FORUM_NICKNAME_PLACEHOLDER)
        self._nickname.setMaxLength(NICKNAME_MAX_LENGTH)
        self._nickname.setFont(get_ui_font(size=scale_px(12, min_abs=10)))
        self._nickname.setToolTip(
            f"最多 {NICKNAME_MAX_LENGTH} 字；留空就以「匿名」发送，服务端会给空昵称补上这一档。"
        )
        # 小输入框：宽度刚好框住那句提示（再加样式表的左右内边距），不挤压正文输入框。
        self._nickname.setFixedWidth(max(
            scale_px(184, min_abs=164),
            self._nickname.fontMetrics().horizontalAdvance(FORUM_NICKNAME_PLACEHOLDER)
            + scale_px(26, min_abs=22),
        ))
        top_row.addWidget(self._nickname, 0)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(scale_px(10, min_abs=8))

        format_row = QHBoxLayout()
        format_row.setContentsMargins(0, 0, 0, 0)
        format_row.setSpacing(scale_px(5, min_abs=4))
        self._format_buttons: dict[str, QToolButton] = {}
        for fmt in FORUM_MARKUP_FORMATS:
            button = QToolButton(composer)
            button.setObjectName("ForumFormatButton")
            button.setText(fmt.button)
            button.setCheckable(True)
            button.setToolTip(
                f"{fmt.label}：选中文字后点一下，用 {fmt.marker} 把选区包起来；没有选中时"
                f"先点亮按钮再输入，打的字就自动是{fmt.label}。再点一下取消。"
            )
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(format_button_font(fmt.key))
            button.clicked.connect(
                lambda _checked=False, key=fmt.key: self._toggle_format(key)
            )
            self._format_buttons[fmt.key] = button
            format_row.addWidget(button)
        bottom_row.addLayout(format_row, 0)

        self._input = QLineEdit(composer)
        self._input.setObjectName("ForumInput")
        self._input.setPlaceholderText(f"说点什么…（最多 {FORUM_MAX_CONTENT} 字）")
        self._input.setMaxLength(FORUM_MAX_CONTENT)
        self._input.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._input.setToolTip(
            "支持 **粗体**、*斜体*、__下划线__、~~删除线~~；左边四个小按钮会在光标处自动加标记。"
        )
        self._input.textChanged.connect(self._sync_composer_state)
        # 光标/选区一变就重算按钮的复选状态，按钮始终表示「光标处是不是这种格式」。
        self._input.cursorPositionChanged.connect(self._sync_format_buttons)
        self._input.selectionChanged.connect(self._sync_format_buttons)
        self._input.returnPressed.connect(self._on_send)
        bottom_row.addWidget(self._input, 1)

        self._counter = QLabel(f"0/{FORUM_MAX_CONTENT}", composer)
        self._counter.setObjectName("ForumHint")
        self._counter.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        bottom_row.addWidget(self._counter, 0)

        self._send_button = QPushButton("发送", composer)
        self._send_button.setObjectName("ForumSend")
        self._send_button.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._send_button.clicked.connect(self._on_send)
        bottom_row.addWidget(self._send_button, 0)
        layout.addLayout(bottom_row)

        # 输入框里实时预览选中的字色与描边色：选色控件改一下，输入框立刻变。
        self._text_color_picker.colorChanged.connect(lambda _c: self._sync_input_colors())
        self._outline_color_picker.colorChanged.connect(lambda _c: self._sync_input_colors())
        self._sync_input_colors()
        return composer

    def _sync_input_colors(self) -> None:
        """把选中的文字色 / 描边色刷到输入框上，选色结果当场可见。

        没勾选的颜色不写规则：清空局部样式表之后，输入框回落到样式表里的默认色，
        「没启用就用默认色」在输入框这儿也要成立。
        """
        if not hasattr(self, "_input"):
            return
        rules: list[str] = []
        text_color = self._text_color_picker.token_color()
        outline_color = self._outline_color_picker.token_color()
        if text_color:
            rules.append(f"QLineEdit#ForumInput {{ color: {text_color}; }}")
        if outline_color:
            rules.append(f"QLineEdit#ForumInput:focus {{ border-color: {outline_color}; }}")
        self._input.setStyleSheet("".join(rules))


__all__ = [
    "ForumWallMixin",
]
