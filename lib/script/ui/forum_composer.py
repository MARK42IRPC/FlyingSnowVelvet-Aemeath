"""主论坛页的「发帖屏」：工具栏下的第三屏（标题 / 正文 / 标签 / 发布）。

批次 3 第九轮从 `forum_board.ForumBoardPage` 切出。发帖屏与列表、详情两屏是并列的三块，
本轮把它的装配（`_build_composer` 及其三个控件族助手）、正文编辑区的一整套行内操作
（字号 / 对齐 / 文字色 / 描边色 / 插入文本 / 插图）、以及发布与计数器联动整体搬到这里；
看板本体只保留列表、详情、会话与调度。

切分保持逐行等价：`ForumComposerMixin` 的方法体与搬出前一致（缩进也未变），`ForumBoardPage`
只是多继承本 mixin。发帖屏依赖的看板状态（`_compose_body` / `_compose_images` / `_state` /
`_detail_post` 等）仍由 `ForumBoardPage.__init__` 建立，混入方法与之同源；`_dispatch` 一族与
`show_list` 仍在看板本体，按下标回看板外壳。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor  # noqa: F401 - 既有导出面
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.forum_api import (
    FORUM_CONTENT_MAX,
    FORUM_IMAGES_PER_POST,
    FORUM_TAG_MAX_COUNT,
    FORUM_TITLE_MAX,
    FORUM_TITLE_MIN,
)
from lib.core.forum_colors import apply_color_tokens
from lib.core.forum_layout import (
    FORUM_ALIGN_CENTER,
    FORUM_ALIGN_LEFT,
    FORUM_ALIGN_RIGHT,
    FORUM_SIZE_MAX,
    FORUM_SIZE_MIN,
    parse_layout_tokens,
    set_layout_tokens,
)
from lib.core.render.backends.qt.widgets.forum_images import (
    COMPOSE_THUMB_SIZE,
    ForumImageThumb,
    _color_span_at,
    _font,
)
from lib.script.ui.forum_color_control import ForumColorControl as _ForumColorControl
from lib.script.ui.forum_color_control import format_button_font
from lib.script.ui.forum_markup import (
    FORMAT_BY_KEY,
    FORUM_MARKUP_FORMATS,
    span_at_cursor,
    toggle,
)
from lib.script.ui.workbench_settings_layout import SmoothScrollArea

#: 发帖页字号下拉里的档位（像素）；`FORUM_SIZE_MIN`/`MAX` 之外的值核心层会当「没写」。
FORUM_SIZE_STEPS: tuple[int, ...] = tuple(
    size for size in (12, 14, 16, 18, 20, 24, 28, 32, 40) if FORUM_SIZE_MIN <= size <= FORUM_SIZE_MAX
)

#: 发帖页与详情页共用的行内标记 / 颜色入口（原在 `forum_board` 里定义，随发帖屏一起搬走）。
ForumColorControl = _ForumColorControl


class ForumComposerMixin:
    """发帖屏的装配与正文编辑操作；由 `ForumBoardPage` 混入。"""

    def _build_composer(self) -> QWidget:
        """发帖页：标题 + 正文 + 标签，发布成功就回到列表。"""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame(page)
        bar.setObjectName("ForumToolbar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
            scale_px(16, min_abs=13),
            scale_px(8, min_abs=6),
        )
        bar_layout.setSpacing(scale_px(8, min_abs=6))
        self._compose_back = QPushButton("← 返回列表", bar)
        self._compose_back.setObjectName("ForumGhostButton")
        self._compose_back.setFont(_font(11))
        self._compose_back.clicked.connect(self.show_list)
        bar_layout.addWidget(self._compose_back, 0)
        self._compose_hint = QLabel("新主题", bar)
        self._compose_hint.setObjectName("ForumHint")
        self._compose_hint.setFont(_font(11))
        bar_layout.addWidget(self._compose_hint, 1)
        layout.addWidget(bar, 0)

        scroll = SmoothScrollArea(page)
        scroll.setObjectName("ForumScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget(scroll)
        host.setObjectName("ForumComposerHost")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(
            scale_px(16, min_abs=13),
            scale_px(10, min_abs=8),
            scale_px(16 + 10, min_abs=13 + 8),
            scale_px(10, min_abs=8),
        )
        host_layout.setSpacing(scale_px(8, min_abs=6))

        card = QFrame(host)
        card.setObjectName("ForumThreadComposer")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
        )
        card_layout.setSpacing(scale_px(7, min_abs=5))

        self._thread_title = QLineEdit(card)
        self._thread_title.setObjectName("ForumField")
        self._thread_title.setPlaceholderText(
            f"标题（{FORUM_TITLE_MIN}–{FORUM_TITLE_MAX} 字）"
        )
        self._thread_title.setMaxLength(FORUM_TITLE_MAX)
        self._thread_title.setFont(_font(12))
        card_layout.addWidget(self._thread_title)

        card_layout.addWidget(self._build_compose_tools(card), 0)
        card_layout.addWidget(self._build_compose_layout(card), 0)
        card_layout.addWidget(self._build_compose_colors(card), 0)

        self._thread_body = QPlainTextEdit(card)
        self._thread_body.setObjectName("ForumThreadBody")
        self._thread_body.setPlaceholderText(
            "正文（支持 Markdown：标题、列表、引用、代码块；上面的按钮会在光标处加标记，"
            "也能给选中的一段选文字色与描边色）"
        )
        self._thread_body.setFont(_font(12))
        self._thread_body.setMinimumHeight(scale_px(150, min_abs=110))
        self._thread_body.textChanged.connect(self._on_body_changed)
        # 光标 / 选区一动就重算按钮的复选状态：亮着就表示「光标处的字就是这种格式 / 这个颜色」。
        self._thread_body.cursorPositionChanged.connect(self._sync_format_buttons)
        self._thread_body.selectionChanged.connect(self._sync_format_buttons)
        self._thread_body.cursorPositionChanged.connect(self._sync_layout_controls)
        self._thread_body.selectionChanged.connect(self._sync_layout_controls)
        card_layout.addWidget(self._thread_body, 1)

        self._image_strip = QWidget(card)
        self._image_strip.setObjectName("ForumImageStrip")
        self._image_row = QHBoxLayout(self._image_strip)
        self._image_row.setContentsMargins(0, 0, 0, 0)
        self._image_row.setSpacing(scale_px(6, min_abs=5))
        self._image_strip.setVisible(False)
        card_layout.addWidget(self._image_strip, 0)

        self._thread_counter = QLabel(f"0 / {FORUM_CONTENT_MAX}", card)
        self._thread_counter.setObjectName("ForumThreadCounter")
        self._thread_counter.setFont(_font(10))
        card_layout.addWidget(self._thread_counter, 0, Qt.AlignRight)

        self._thread_tags = QLineEdit(card)
        self._thread_tags.setObjectName("ForumField")
        self._thread_tags.setPlaceholderText(
            f"标签（可选，最多 {FORUM_TAG_MAX_COUNT} 个，用逗号隔开）"
        )
        self._thread_tags.setFont(_font(12))
        card_layout.addWidget(self._thread_tags)

        self._thread_error = QLabel("", card)
        self._thread_error.setObjectName("ForumFieldError")
        self._thread_error.setFont(_font(11))
        self._thread_error.setWordWrap(True)
        self._thread_error.setVisible(False)
        card_layout.addWidget(self._thread_error)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(scale_px(8, min_abs=6))
        self._compose_send = QPushButton("发布", card)
        self._compose_send.setObjectName("ForumSend")
        self._compose_send.setFont(_font(12))
        self._compose_send.clicked.connect(self._on_publish_thread)
        buttons.addWidget(self._compose_send, 0)
        self._compose_cancel = QPushButton("取消", card)
        self._compose_cancel.setObjectName("ForumGhostButton")
        self._compose_cancel.setFont(_font(12))
        self._compose_cancel.clicked.connect(self.show_list)
        buttons.addWidget(self._compose_cancel, 0)
        self._compose_login = QPushButton("去登录", card)
        self._compose_login.setFont(_font(12))
        self._compose_login.setVisible(False)
        self._compose_login.clicked.connect(self.login_requested.emit)
        buttons.addWidget(self._compose_login, 0)
        buttons.addStretch(1)
        card_layout.addLayout(buttons)

        host_layout.addWidget(card, 0)
        host_layout.addStretch(1)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self._sync_thread_counter()
        self._sync_compose_images()
        return page

    def _build_compose_tools(self, card: QWidget) -> QWidget:
        """正文上方的工具栏：行内标记 + 文字色 / 描边色 + 添加图片。

        四个字母按钮与留言墙是同一套（`forum_markup.toggle()`），只是打交道的对象换成了
        `QPlainTextEdit` 的光标；「文字色 / 描边色」两个按钮既是开关也是应用动作
        （勾上就把选中的一段上成当前颜色，再点一下取消），对应的滑条见 `_build_compose_colors()`。
        """
        bar = QWidget(card)
        bar.setObjectName("ForumComposeTools")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(5, min_abs=4))

        self._format_buttons: dict[str, QToolButton] = {}
        for fmt in FORUM_MARKUP_FORMATS:
            button = QToolButton(bar)
            button.setObjectName("ForumFormatButton")
            button.setText(fmt.button)
            button.setCheckable(True)
            button.setToolTip(
                f"{fmt.label}：选中文字后点一下，用 {fmt.marker} 把选区包起来；没有选中时先点亮按钮"
                f"再输入，打的字就自动是{fmt.label}。再点一下取消。"
            )
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(format_button_font(fmt.key))
            button.clicked.connect(lambda _checked=False, key=fmt.key: self._toggle_body_format(key))
            self._format_buttons[fmt.key] = button
            row.addWidget(button, 0)

        self._color_buttons: dict[str, QToolButton] = {}
        for kind, label, tip in (
            ("color", "文字色", "把选中的一段（或接下来打的字）改成选定的文字色；再点一下取消"),
            ("outline", "描边色", "给选中的一段（或接下来打的字）加一圈描边色；再点一下取消"),
        ):
            button = QToolButton(bar)
            button.setObjectName("ForumColorButton")
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(_font(11))
            button.clicked.connect(lambda _checked=False, key=kind: self._toggle_body_color(key))
            self._color_buttons[kind] = button
            row.addWidget(button, 0)

        row.addStretch(1)
        self._image_button = QPushButton("添加图片", bar)
        self._image_button.setObjectName("ForumGhostButton")
        self._image_button.setFont(_font(11))
        self._image_button.clicked.connect(self._on_add_image)
        row.addWidget(self._image_button, 0)
        return bar

    def _build_compose_layout(self, card: QWidget) -> QWidget:
        """排版那一行：字号下拉 + 左 / 中 / 右三个对齐按钮。

        这几个都不是行内标记而是**段落**属性（见 `lib/core/forum_layout.py`）：字号取下拉
        里的档位，对齐三个按钮互斥，作用范围是光标所在的每一段。没有选中时点一下就把整段
        设成这一档；光标已经在那一档里再点一下，字号回到默认、对齐回到左对齐。
        """
        bar = QWidget(card)
        bar.setObjectName("ForumComposeLayout")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(5, min_abs=4))

        label = QLabel("字号", bar)
        label.setObjectName("ForumHint")
        label.setFont(_font(11))
        row.addWidget(label, 0)

        self._size_combo = QComboBox(bar)
        self._size_combo.setObjectName("ForumSizeCombo")
        self._size_combo.setFont(_font(11))
        # 第一档是「默认」：选它就把这一段的 `[size=...]` 摘掉，回到块自己的字号。
        self._size_combo.addItem("默认", 0)
        for size in FORUM_SIZE_STEPS:
            self._size_combo.addItem(str(size), size)
        self._size_combo.setToolTip("把光标所在的段落设成这个字号；选「默认」就回到原来的字号")
        self._size_combo.currentIndexChanged.connect(
            lambda _index: self._apply_body_size()
        )
        row.addWidget(self._size_combo, 0)

        self._align_buttons: dict[str, QToolButton] = {}
        for align, text, tip in (
            (FORUM_ALIGN_LEFT, "靠左", "把光标所在的段落靠左对齐（默认对齐）"),
            (FORUM_ALIGN_CENTER, "居中", "把光标所在的段落居中"),
            (FORUM_ALIGN_RIGHT, "靠右", "把光标所在的段落靠右"),
        ):
            button = QToolButton(bar)
            button.setObjectName("ForumColorButton")
            button.setText(text)
            button.setCheckable(True)
            button.setToolTip(f"{tip}；再点一下回到左对齐")
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(_font(11))
            button.clicked.connect(
                lambda checked=False, value=align: self._apply_body_align(value, checked)
            )
            self._align_buttons[align] = button
            row.addWidget(button, 0)

        row.addStretch(1)
        return bar

    def _build_compose_colors(self, card: QWidget) -> QWidget:
        """颜色滑条那一行：勾了哪一档就铺开哪一档，两个都没勾就整行收起。

        这里的取色控件不带自己的复选框（`show_toggle=False`）：开关就是工具栏上那两个按钮，
        控件只负责「选颜色」，滑条因此常驻。
        """
        host = QWidget(card)
        host.setObjectName("ForumComposeColors")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scale_px(8, min_abs=6))
        self._color_pickers: dict[str, ForumColorControl] = {}
        for kind, label in (("color", "文字颜色"), ("outline", "描边颜色")):
            picker = ForumColorControl(host, label, show_toggle=False)
            picker.colorChanged.connect(lambda _color, key=kind: self._reapply_body_color(key))
            self._color_pickers[kind] = picker
            row.addWidget(picker, 0)
        row.addStretch(1)
        host.setVisible(False)
        self._color_host = host
        return host

    def _body_text(self) -> str:
        return self._thread_body.toPlainText()

    def _body_caret(self) -> int:
        """光标位置；选中一段时给选区起点（`QTextCursor.position()` 指的是选区末尾）。"""
        cursor = self._thread_body.textCursor()
        return cursor.selectionStart() if cursor.hasSelection() else cursor.position()

    def _body_selection(self) -> tuple[int, int]:
        cursor = self._thread_body.textCursor()
        return cursor.selectionStart(), cursor.selectionEnd()

    def _set_body_text(self, text: str, start: int, stop: int) -> None:
        """换掉正文并摆好光标 / 选区；`_body_writing` 让 `_on_body_changed()` 知道是自己改的。"""
        self._body_writing = True
        try:
            self._thread_body.setPlainText(text)
        finally:
            self._body_writing = False
        size = len(text)
        cursor = self._thread_body.textCursor()
        cursor.setPosition(max(0, min(int(start), size)))
        if stop > start:
            cursor.setPosition(max(0, min(int(stop), size)), QTextCursor.KeepAnchor)
        self._thread_body.setTextCursor(cursor)
        self._thread_body.setFocus()
        self._sync_format_buttons()

    def _on_body_changed(self) -> None:
        self._sync_thread_counter()
        if not self._body_writing:
            # 正文被人手改了，之前记下的颜色区段坐标就不再可信，别再拿它去改颜色。
            self._color_spans.clear()

    def _toggle_body_format(self, key: str) -> None:
        """复选按钮：在光标 / 选区处加减一对标记，接着打的字自动落在标记里。"""
        fmt = FORMAT_BY_KEY.get(str(key or ""))
        if fmt is None:
            return
        caret, end = self._body_selection()
        text, start, stop = toggle(self._body_text(), caret, end, fmt.marker)
        if len(text) > FORUM_CONTENT_MAX:
            self._compose_error(f"加上标记会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            self._sync_format_buttons()
            return
        self._set_body_text(text, start, stop)

    def _apply_body_size(self) -> None:
        """把光标所在的每一段设成下拉里选的字号；「默认」把令牌摘掉。"""
        size = int(self._size_combo.currentData() or 0)
        self._write_body_layout(size=size)

    def _apply_body_align(self, align: str, checked: bool) -> None:
        """把光标所在的每一段设成这个对齐；同一个按钮再点一下就回到左对齐。

        `checked` 是 `clicked` 信号带回来的**新**状态（Qt 在发出信号前已经翻过按钮），所以
        「点暗了」就是取消。左对齐本身是默认值，点它永远等于回到默认，用它「点亮」没有意义。
        """
        if align == FORUM_ALIGN_LEFT:
            wanted = FORUM_ALIGN_LEFT
        else:
            wanted = align if checked else ""
        self._write_body_layout(align=wanted)

    def _write_body_layout(self, *, size: int | None = None, align: str | None = None) -> None:
        """把排版写进正文，写完按新状态刷新按钮。"""
        body = self._body_text()
        start, end = self._body_selection()
        text, new_start, new_stop = set_layout_tokens(body, start, end, size=size, align=align)
        if text == body:
            self._sync_layout_controls()
            return
        if len(text) > FORUM_CONTENT_MAX:
            self._compose_error(f"加排版令牌会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            self._sync_layout_controls()
            return
        self._set_body_text(text, new_start, new_stop)

    def _sync_layout_controls(self) -> None:
        """字号下拉与对齐按钮跟着光标走：显示光标所在段落当前的排版。"""
        if not hasattr(self, "_size_combo"):
            return
        body = self._body_text()
        caret = self._body_caret()
        current_size, current_align = self._paragraph_layout_at(body, caret)
        index = self._size_combo.findData(current_size)
        if index >= 0 and self._size_combo.currentIndex() != index:
            blocked = self._size_combo.blockSignals(True)
            self._size_combo.setCurrentIndex(index)
            self._size_combo.blockSignals(blocked)
        for align, button in self._align_buttons.items():
            checked = current_align == align and align != FORUM_ALIGN_LEFT
            if align == FORUM_ALIGN_LEFT:
                checked = current_align in ("", FORUM_ALIGN_LEFT)
            if button.isChecked() != checked:
                button.setChecked(checked)

    @staticmethod
    def _paragraph_layout_at(body: str, caret: int) -> tuple[int, str]:
        """光标所在段落当前的 `(字号, 对齐)`；两段之间按下一段算。"""
        offset = max(0, min(int(caret), len(body)))
        start = body.rfind("\n", 0, offset) + 1
        stop = body.find("\n", start)
        if stop < 0:
            stop = len(body)
        _plain, size, align = parse_layout_tokens(body[start:stop])
        return size, align

    def _toggle_body_color(self, kind: str) -> None:
        """「文字色 / 描边色」按钮：勾上就是把选中的一段上成当前颜色，再点一下取消。"""
        picker = self._color_pickers.get(kind)
        if picker is None:
            return
        body = self._body_text()
        start, end = self._body_selection()
        if _color_span_at(body, start, kind) is not None:
            value = ""  # 光标已经在这一段里了：这一下就是取消
        else:
            value = picker.color()
        text, new_start, new_stop = apply_color_tokens(body, start, end, **{kind: value})
        if len(text) > FORUM_CONTENT_MAX:
            self._compose_error(f"加颜色令牌会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            self._sync_format_buttons()
            return
        if value:
            self._color_spans[kind] = (new_start, new_stop)
        else:
            self._color_spans.pop(kind, None)
        self._set_body_text(text, new_start, new_stop)
        self._sync_color_host()

    def _reapply_body_color(self, kind: str) -> None:
        """拖动滑条时，把刚上过色的那一段换成新颜色（换色是重刷，不会越点越长）。"""
        button = self._color_buttons.get(kind)
        picker = self._color_pickers.get(kind)
        span = self._color_spans.get(kind)
        if span is None or picker is None or button is None or not button.isChecked():
            return
        body = self._body_text()
        text, start, stop = apply_color_tokens(body, span[0], span[1], **{kind: picker.color()})
        if text == body:
            return
        self._color_spans[kind] = (start, stop)
        self._set_body_text(text, start, stop)

    def _sync_format_buttons(self) -> None:
        """按钮的复选状态跟着光标：亮着就表示光标处的字已经是这种格式 / 这个颜色。"""
        if not hasattr(self, "_format_buttons"):
            return
        text = self._body_text()
        caret = self._body_caret()
        for key, button in self._format_buttons.items():
            checked = span_at_cursor(text, caret, FORMAT_BY_KEY[key].marker) is not None
            if button.isChecked() != checked:
                button.setChecked(checked)
        for kind, button in self._color_buttons.items():
            checked = _color_span_at(text, caret, kind) is not None
            if button.isChecked() != checked:
                button.setChecked(checked)
        self._sync_layout_controls()

    def _sync_color_host(self) -> None:
        """颜色滑条跟着两个按钮走：勾了哪一档铺开哪一档，两个都没勾就整行收起。"""
        shown = False
        for kind, picker in self._color_pickers.items():
            visible = self._color_buttons[kind].isChecked()
            picker.setVisible(visible)
            shown = shown or visible
        self._color_host.setVisible(shown)

    def _insert_body_text(self, text: str) -> None:
        """在光标处插一段文字（图片 Markdown 走这里），超过了字数上限就只在错误行说明。"""
        body = self._body_text()
        caret, end = self._body_selection()
        merged = body[:caret] + text + body[end:]
        if len(merged) > FORUM_CONTENT_MAX:
            self._compose_error(f"插进来会超过 {FORUM_CONTENT_MAX} 字上限，先删掉一些再试")
            return
        stop = caret + len(text)
        self._set_body_text(merged, stop, stop)

    def _on_add_image(self) -> None:
        """选一张图并上传；本地能拦下的问题（图上够了）就不必再走一趟网络。"""
        if len(self._compose_images) >= FORUM_IMAGES_PER_POST:
            self._compose_error(f"一个帖子最多挂 {FORUM_IMAGES_PER_POST} 张图，先摘掉一张再传")
            return
        path, _selected = QFileDialog.getOpenFileName(
            self, "选一张图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp);;所有文件 (*)"
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self._compose_error(f"这个文件读不出来：{exc}")
            return
        self._pending_image_bytes = data
        error = self._service.upload_image(data, name=Path(path).name)
        if error:
            self._pending_image_bytes = b""
            self._compose_error(error)
            self._sync_composer()
            return
        self._uploading_image = True
        self._compose_error("")
        self._sync_composer()

    def _remove_compose_image(self, image_id: str) -> None:
        """把一张已经传上去的图从这篇帖子里摘掉（图库那边不动）。"""
        ident = str(image_id or "")
        self._compose_images = [image for image in self._compose_images if image.id != ident]
        self._sync_compose_images()

    def _sync_compose_images(self) -> None:
        """铺开已上传图片的预览条；一张都没有就整条收起。"""
        while self._image_row.count():
            item = self._image_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for image in self._compose_images:
            thumb = ForumImageThumb(
                image.id, size=COMPOSE_THUMB_SIZE, removable=True, parent=self._image_strip
            )
            thumb.set_data(self._thumb_data.get(image.id, b""))
            thumb.clicked.connect(self._remove_compose_image)
            self._image_row.addWidget(thumb, 0)
        self._image_row.addStretch(1)
        self._image_strip.setVisible(bool(self._compose_images))
        self._sync_composer()

    def _sync_composer(self) -> None:
        logged_in = self._session.logged_in()
        has_text = bool(self._reply_input.text().strip())
        self._reply_send.setEnabled(logged_in and has_text)
        self._compose_send.setEnabled(logged_in)
        self._compose_send.setToolTip("" if logged_in else "登录后才能发帖")
        self._compose_login.setVisible(not logged_in)
        self._compose_button.setToolTip(
            "发布一篇新帖" if logged_in else "发布一篇新帖（需要登录）"
        )
        count = len(self._compose_images)
        full = count >= FORUM_IMAGES_PER_POST
        self._image_button.setEnabled(logged_in and not self._uploading_image and not full)
        if self._uploading_image:
            self._image_button.setText("正在上传…")
        else:
            suffix = f"（{count}/{FORUM_IMAGES_PER_POST}）" if count else ""
            self._image_button.setText(f"添加图片{suffix}")
        if not logged_in:
            tip = "登录后才能上传图片"
        elif full:
            tip = f"一个帖子最多挂 {FORUM_IMAGES_PER_POST} 张图"
        else:
            tip = "上传一张图片（PNG / JPEG / GIF / WebP），正文里会插入它的 Markdown"
        self._image_button.setToolTip(tip)

    def _on_publish_thread(self) -> None:
        error = self._service.post_thread(
            self._thread_title.text(),
            self._thread_body.toPlainText(),
            tags=self._thread_tags.text(),
            images=[image.id for image in self._compose_images],
        )
        self._compose_error(error)

    def _compose_error(self, text: str) -> None:
        message = str(text or "").strip()
        self._thread_error.setText(message)
        self._thread_error.setVisible(bool(message))

    def _sync_thread_counter(self) -> None:
        length = len(self._thread_body.toPlainText())
        over = length > FORUM_CONTENT_MAX
        self._thread_counter.setText(
            f"{length} / {FORUM_CONTENT_MAX}" if not over else f"超出 {length - FORUM_CONTENT_MAX} 字"
        )
        if self._thread_counter.property("tone") == ("warn" if over else ""):
            return
        self._thread_counter.setProperty("tone", "warn" if over else "")
        style = self._thread_counter.style()
        if style is not None:
            style.unpolish(self._thread_counter)
            style.polish(self._thread_counter)


__all__ = [
    "ForumComposerMixin",
    "FORUM_SIZE_STEPS",
]
