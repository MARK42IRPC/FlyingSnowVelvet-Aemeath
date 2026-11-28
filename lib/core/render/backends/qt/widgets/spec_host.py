"""Qt 窗口描述宿主：把后端中立的 `WindowSpec` 装配成真实控件树。

这是「窗口级描述 + 后端渲染」结构里的后端那一半（档位 D），与 `control_host.py`
（单个叶控件）、`message_box_host.py`（模态消息框）同类。它只做 Qt 事实：窗口标志、
控件的创建与父子关系、布局与间距、按钮点击到语义 id 的回调、图标位图、颜色解析、
无窗眉时的拖动与居中落位、关闭语义。

窗口**里有什么、怎么排、点下去意味着哪个动作**全部由 `visuals/window_spec.py` 的
描述决定；产品窗口只负责收集状态、产出描述、处理语义回调。换后端时替换的是这一层，
不是窗口语义。

本模块不 import `lib.script`，也不静态引用档位 A（`drawing/`）：屏幕归属与像素执行
由组合入口经 `render_bridge` 注入，或经由 `runtime` 档的等价落点取得。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PyQt5 import sip
from PyQt5.QtCore import QEasingCurve, QEventLoop, QPropertyAnimation, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStyle,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.render.backends.qt.widgets.smooth_scroll import SmoothScrollArea
from lib.core.render.backends.qt.widgets.floating_window import FloatingDragFilter, FloatingWindowThemeWatcher
from lib.core.render.backends.qt.widgets.office_icons import render_office_icon
from lib.core.render.backends.qt.widgets.window_buttons import create_window_button
from lib.core.render.layers import WindowLayer, get_layer_manager
from lib.core.services.ui_presentation import ui_fade_duration_ms
from lib.core.render.visuals.announcement_chrome import announcement_document_stylesheet, announcement_stylesheet
from lib.core.render.visuals.layout import PlacementSpec
from lib.core.render.visuals.window_spec import (
    ALIGN_BOTTOM,
    ALIGN_HCENTER,
    ALIGN_LEFT,
    ALIGN_RIGHT,
    ALIGN_TOP,
    ALIGN_VCENTER,
    COLOR_CANVAS,
    COLOR_DANGER,
    COLOR_TEXT,
    COLOR_WARNING,
    LAYER_DIALOG,
    WINDOW_TOOL,
    AccentBarSpec,
    ButtonSpec,
    IconSpec,
    LAYOUT_ROW,
    LabelSpec,
    LayoutSpec,
    ProgressBarSpec,
    RichTextSpec,
    SpacerSpec,
    StretchSpec,
    TextAreaSpec,
    WindowSpec,
)
from lib.core.render.visuals.workbench_tokens import get_workbench_tokens


_H_ALIGN = {
    ALIGN_LEFT: Qt.AlignLeft,
    ALIGN_HCENTER: Qt.AlignHCenter,
    ALIGN_RIGHT: Qt.AlignRight,
}

_V_ALIGN = {
    ALIGN_TOP: Qt.AlignTop,
    ALIGN_VCENTER: Qt.AlignVCenter,
    ALIGN_BOTTOM: Qt.AlignBottom,
}

#: 标准窗眉字形名 → Qt 标准图标（与工作台窗口按钮同一份映射）。
WINDOW_ICONS = {
    "close": QStyle.SP_TitleBarCloseButton,
    "minimize": QStyle.SP_TitleBarMinButton,
    "maximize": QStyle.SP_TitleBarMaxButton,
}

#: 样式表里给颜色语义留的占位写法：`{token:text}` 一类的替换由后端负责。
_COLOR_TOKEN_PREFIX = "{color:"


def _qt_alignment(spec) -> int:
    """把描述里的对齐名折成 Qt 对齐标志；未声明时返回 0（由布局决定）。"""

    flags = 0
    for name in (getattr(spec, "align", ""), getattr(spec, "valign", "")):
        if name in _H_ALIGN:
            flags |= _H_ALIGN[name]
        elif name in _V_ALIGN:
            flags |= _V_ALIGN[name]
    return flags


def resolve_token_color(name: str, mode: str | None = None) -> str:
    """把颜色语义名解析成当前主题下的十六进制串。"""

    key = {
        COLOR_TEXT: "text",
        COLOR_CANVAS: "canvas",
        COLOR_WARNING: "warning",
        COLOR_DANGER: "danger",
    }.get(name, name)
    tokens = get_workbench_tokens(mode)
    return str(tokens.get(key, tokens.get("text", "#000000")))


def apply_color_tokens(stylesheet: str, mode: str | None = None) -> str:
    """把样式表里的 `{color:语义名}` 替换成当前主题下的十六进制串。

    无法解析的名字原样保留，样式表里因此可以写 `rgb(0,0,0,0)` 一类与语义无关的值。
    """

    text = str(stylesheet or "")
    if _COLOR_TOKEN_PREFIX not in text:
        return text
    tokens = get_workbench_tokens(mode)

    def _resolve(name: str) -> str:
        key = {
            COLOR_TEXT: "text",
            COLOR_CANVAS: "canvas",
            COLOR_WARNING: "warning",
            COLOR_DANGER: "danger",
        }.get(name, name)
        return str(tokens.get(key, ""))

    result = []
    index = 0
    while True:
        start = text.find(_COLOR_TOKEN_PREFIX, index)
        if start < 0:
            result.append(text[index:])
            break
        end = text.find("}", start)
        if end < 0:
            result.append(text[index:])
            break
        name = text[start + len(_COLOR_TOKEN_PREFIX):end].strip()
        color = _resolve(name)
        result.append(text[index:start])
        result.append(color if color else text[start:end + 1])
        index = end + 1
    return "".join(result)


class QtSpecWindow:
    """一个按 `WindowSpec` 装配出来的真实窗口及其控件树句柄。

    调用方拿到它以后：

    - ``widget`` 是承载窗口（`QDialog` / `QWidget`），可 `show()` / `open()` / `move()`；
    - ``find(node_id)`` 取描述里某个 `id` 对应的真实控件（换内容、断言用）；
    - ``apply_theme(spec)`` 重新按（可能已换主题的）描述刷样式与图标色；
    - ``center_on(reference_rect=None)`` 居中到父窗或光标所在屏幕。

    ``on_semantic`` 回填按钮/关闭动作的语义 id；``on_destroyed`` 在底层窗口销毁时回调，
    对应此前产品窗口直接连 ``destroyed`` 的做法，而产品窗口本身已不再是 `QObject`。
    """

    def __init__(
        self,
        widget: QWidget,
        spec: WindowSpec,
        *,
        font_factory: Callable,
        on_semantic: Callable[[str], None] | None = None,
        on_destroyed: Callable[[], None] | None = None,
        presentation_host,
        anchor_parent: QWidget | None = None,
    ) -> None:
        self.widget = widget
        self.spec = spec
        self._font_factory = font_factory
        self._on_semantic = on_semantic
        self._presentation = presentation_host
        self._anchor_parent = anchor_parent
        self._nodes: dict[str, QWidget] = {}
        self._buttons: dict[str, ButtonSpec] = {}
        self._root_layout = None
        self._resolved = False
        self._cleaned = False
        self._decision = ""
        self._theme = FloatingWindowThemeWatcher(widget, self.apply_theme) if spec.shared_popup else None
        self._drag_filter = FloatingDragFilter(widget)
        self._rich_html: dict[str, str] = {}
        self._layer_registered = False
        self._fade = spec.fade
        self._requested_visible = False
        self._closing_animation = False
        self._opacity_animation: QPropertyAnimation | None = None
        self._modal_done_result: int | None = None
        self._modal_exit_complete = False
        self._modal_loop: QEventLoop | None = None
        self._border_width = 0
        self._setup_fade()
        self._build(spec)
        self._install_drag_handles(spec)
        if on_destroyed is not None:
            widget.destroyed.connect(lambda *_args: on_destroyed())

    # ── 查询 ─────────────────────────────────────────────────────────

    def find(self, node_id: str):
        return self._nodes.get(node_id)

    def button_spec(self, semantic: str) -> ButtonSpec | None:
        return self._buttons.get(semantic)

    @property
    def root_layout(self):
        return self._root_layout

    @property
    def resolved(self) -> bool:
        """本窗口是否已经给出过决定（决定一旦给出就不重复发出）。"""

        return self._resolved

    # ── 装配 ─────────────────────────────────────────────────────────

    def _build(self, spec: WindowSpec) -> None:
        widget = self.widget
        if spec.object_name:
            widget.setObjectName(spec.object_name)
        if spec.title:
            widget.setWindowTitle(spec.title)
        if spec.translucent:
            widget.setAttribute(Qt.WA_TranslucentBackground)
        if spec.styled_background:
            widget.setAttribute(Qt.WA_StyledBackground, True)
        if spec.delete_on_close:
            widget.setAttribute(Qt.WA_DeleteOnClose, True)
        if spec.min_width:
            widget.setMinimumWidth(int(spec.min_width))
        if spec.max_width:
            widget.setMaximumWidth(int(spec.max_width))
        width, height = spec.fixed_size
        if width and height:
            widget.setFixedSize(width, height)
        elif width:
            widget.setFixedWidth(width)
        elif height:
            widget.setFixedHeight(height)
        elif all(spec.initial_size):
            widget.resize(*spec.initial_size)
        self._setup_border_frame(spec)

        layout = self._build_layout(spec.content, widget)
        widget.setLayout(layout)
        self._root_layout = layout
        if isinstance(widget, QDialog) and spec.modal:
            widget.finished.connect(self._on_dialog_finished)
            default_button = self.find(spec.default_button_id)
            for button in widget.findChildren(QPushButton):
                button.setAutoDefault(False)
            if isinstance(default_button, QPushButton):
                default_button.setDefault(True)
                default_button.setFocus(Qt.OtherFocusReason)
        self.apply_theme()

    def _setup_border_frame(self, spec: WindowSpec) -> None:
        """描边外壳：外描边 + 内容底色两层矩形（公告 / 更新浮窗同款）。"""

        if not spec.border_frame:
            return
        self._border_width = max(1, int(spec.border_width) or 1)

    def paint_border_frame(self, event) -> bool:
        """描边外壳的绘制；未启用时返回 False，由承载窗口走默认绘制。

        颜色在**每次重绘时**取，与公告 / 更新浮窗一样跟着工作台主题走。
        两层式（公告 / 帮助）是「外描边 + 内容底色」；三层式（更新浮窗）在两者之间
        多一条 `border` 内衬线，由 `spec.border_mid` 声明。
        """

        if not self._border_width:
            return False
        painter = QPainter(self.widget)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.widget.rect(), QColor(resolve_token_color("border_strong")))
        mid_token = str(getattr(self.spec, "border_mid", "") or "")
        if mid_token:
            mid = max(1, self._border_width // 2)
            painter.fillRect(
                self.widget.rect().adjusted(mid, mid, -mid, -mid),
                QColor(resolve_token_color(mid_token)),
            )
        inset = self._border_width
        fill_token = str(getattr(self.spec, "border_fill", "") or "") or "canvas"
        painter.fillRect(
            self.widget.rect().adjusted(inset, inset, -inset, -inset),
            QColor(resolve_token_color(fill_token)),
        )
        painter.end()
        return True

    def _build_layout(self, spec: LayoutSpec, parent: QWidget):
        layout = QHBoxLayout(parent) if spec.direction == LAYOUT_ROW else QVBoxLayout(parent)
        layout.setContentsMargins(*(int(v) for v in spec.margin))
        layout.setSpacing(int(spec.spacing))
        for child in spec.children:
            self._add_child(layout, child, parent)
        if spec.top_margin:
            margins = layout.contentsMargins()
            layout.setContentsMargins(
                margins.left(),
                int(spec.top_margin),
                margins.right(),
                margins.bottom(),
            )
        return layout

    def _add_child(self, layout, child, parent: QWidget) -> None:
        stretch = max(0, int(getattr(child, "stretch", 0)))
        if isinstance(child, LayoutSpec):
            if getattr(child, "collapse_when_empty", False):
                layout.addLayout(self._build_nested_layout(child, parent))
                return
            container = self._build_container(child, parent)
            alignment = _qt_alignment(child)
            if alignment:
                layout.addWidget(container, stretch, alignment)
            else:
                layout.addWidget(container, stretch)
            return
        if isinstance(child, StretchSpec):
            layout.addStretch(max(0, int(round(child.weight))))
            return
        if isinstance(child, SpacerSpec):
            layout.addSpacing(int(child.size))
            return
        widget, alignment = self._build_widget(child, parent)
        if widget is None:
            return
        stretch = max(stretch, int(getattr(child, "stretch", 0)))
        # 带伸缩权重的控件不能用 addWidget 的 alignment 形参：Qt 会因此不拉伸它。
        # 对齐已由控件自身的 setAlignment 承担（`_build_label` 里设置），这里只传权重。
        if alignment and not stretch:
            layout.addWidget(widget, stretch, alignment)
        else:
            layout.addWidget(widget, stretch)

    def _build_nested_layout(self, spec: LayoutSpec, parent: QWidget):
        """把一层 ``LayoutSpec`` 直接建成子布局（不套容器控件）。

        用于 ``collapse_when_empty`` 的按钮行：Qt 认为「子项全为空」的布局本身也是空项，
        会自动收起整行连同父布局间距；若包成容控件，即便子控件全部隐藏，
        ``QWidgetItem`` 依旧非空，父布局仍会保留一行间距（原 `QHBoxLayout` 不会）。
        """

        layout = (
            QHBoxLayout() if spec.direction == LAYOUT_ROW else QVBoxLayout()
        )
        layout.setContentsMargins(*(int(v) for v in spec.margin))
        layout.setSpacing(int(spec.spacing))
        for child in spec.children:
            self._add_child(layout, child, parent)
        if spec.top_margin:
            margins = layout.contentsMargins()
            layout.setContentsMargins(
                margins.left(),
                int(spec.top_margin),
                margins.right(),
                margins.bottom(),
            )
        return layout

    def _build_container(self, spec: LayoutSpec, parent: QWidget) -> QWidget:
        """把一个 ``LayoutSpec`` 装成容器；``scroll`` 时外套一层平滑滚动视口。"""

        if spec.scroll:
            container = SmoothScrollArea(parent)
            container.setObjectName(spec.object_name or spec.scroll_object_name)
            container.setWidgetResizable(True)
            container.setFrameShape(QFrame.NoFrame)
            container.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            holder = QWidget(container)
            holder.setObjectName(spec.scroll_host_object_name)
            margins = spec.scroll_margin if any(spec.scroll_margin) else spec.margin
            holder_layout = self._build_layout(replace(spec, margin=margins), holder)
            holder_layout.addStretch(1)
            container.setWidget(holder)
        else:
            container = QFrame(parent) if spec.frame else QWidget(parent)
            container.setObjectName(spec.object_name)
            self._build_layout(spec, container)
        if spec.fixed_width:
            container.setFixedWidth(int(spec.fixed_width))
        if spec.fixed_height:
            container.setFixedHeight(int(spec.fixed_height))
        self._register(spec.id, container)
        return container

    def _build_widget(self, child, parent: QWidget):
        if isinstance(child, LabelSpec):
            # 标签自己 setAlignment（文本在控件内的对齐）；不把同一份对齐再交给布局，
            # 否则 Qt 会让标签按 sizeHint 收窄、不再铺满整行。
            return self._build_label(child, parent), 0
        if isinstance(child, IconSpec):
            return self._build_icon_label(child, parent), _qt_alignment(child)
        if isinstance(child, AccentBarSpec):
            return self._build_accent_bar(child, parent), 0
        if isinstance(child, TextAreaSpec):
            return self._build_text_area(child, parent), 0
        if isinstance(child, RichTextSpec):
            return self._build_rich_text(child, parent), 0
        if isinstance(child, ProgressBarSpec):
            return self._build_progress_bar(child, parent), 0
        if isinstance(child, ButtonSpec):
            return self._build_button(child, parent), _qt_alignment(child)
        return None, 0

    def _build_label(self, spec: LabelSpec, parent: QWidget) -> QLabel:
        label = QLabel(str(spec.text), parent)
        if spec.object_name:
            label.setObjectName(spec.object_name)
        if spec.font_size or spec.bold:
            font = self._font_factory(int(spec.font_size) if spec.font_size else None)
            if spec.bold:
                font.setBold(True)
            label.setFont(font)
        if spec.word_wrap:
            label.setWordWrap(True)
        if spec.plain_text:
            label.setTextFormat(Qt.PlainText)
        if spec.selectable:
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        alignment = _qt_alignment(spec)
        if alignment:
            label.setAlignment(alignment)
        self._register(spec.id, label)
        return label

    def _build_icon_label(self, spec: IconSpec, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        if spec.object_name:
            label.setObjectName(spec.object_name)
        alignment = _qt_alignment(spec)
        label.setAlignment(alignment or (Qt.AlignTop | Qt.AlignHCenter))
        self._register(spec.id, label)
        return label

    def _build_accent_bar(self, spec: AccentBarSpec, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        if spec.object_name:
            bar.setObjectName(spec.object_name)
        if spec.height:
            bar.setFixedHeight(int(spec.height))
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for object_name in ("OfficeAccentCyan", "OfficeAccentPink"):
            segment = QFrame(bar)
            segment.setObjectName(object_name)
            row.addWidget(segment, 1)
        self._register(spec.id, bar)
        return bar

    def _build_text_area(self, spec: TextAreaSpec, parent: QWidget) -> QPlainTextEdit:
        view = QPlainTextEdit(parent)
        if spec.object_name:
            view.setObjectName(spec.object_name)
        view.setReadOnly(bool(spec.readonly))
        view.setPlainText(str(spec.text))
        if spec.max_height:
            view.setMaximumHeight(int(spec.max_height))
        font = self._font_factory(int(spec.font_size) if spec.font_size else None)
        if spec.monospace:
            font.setFamily("Consolas")
        view.setFont(font)
        self._register(spec.id, view)
        return view

    def _build_rich_text(self, spec: RichTextSpec, parent: QWidget) -> QTextBrowser:
        view = QTextBrowser(parent)
        if spec.object_name:
            view.setObjectName(spec.object_name)
        view.setReadOnly(True)
        view.setOpenExternalLinks(bool(spec.open_external_links))
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        if spec.font_size:
            view.setFont(self._font_factory(int(spec.font_size)))
        if spec.document_margin:
            view.document().setDocumentMargin(int(spec.document_margin))
        if spec.document_stylesheet:
            view.document().setDefaultStyleSheet(str(spec.document_stylesheet))
        view.setHtml(str(spec.html))
        self._register(spec.id, view)
        self._rich_html[spec.id] = spec.html
        return view

    def set_html(self, node_id: str, html: str) -> None:
        """Replace a rich text node, retaining its source for theme changes."""
        view = self.find(node_id)
        if not isinstance(view, QTextBrowser):
            raise ValueError(f"Not a rich text node: {node_id!r}")
        self._rich_html[node_id] = html
        view.setHtml(html)
        view.verticalScrollBar().setValue(0)

    def _build_progress_bar(self, spec: ProgressBarSpec, parent: QWidget) -> QProgressBar:
        bar = QProgressBar(parent)
        if spec.object_name:
            bar.setObjectName(spec.object_name)
        if getattr(spec, "tone", ""):
            bar.setProperty("windowProgressRole", spec.tone)
        bar.setTextVisible(bool(spec.text_visible))
        bar.setRange(int(spec.minimum), int(spec.maximum))
        bar.setValue(int(spec.value))
        if getattr(spec, "min_height", 0):
            bar.setMinimumHeight(int(spec.min_height))
        if spec.fmt:
            bar.setFormat(str(spec.fmt))
        self._register(spec.id, bar)
        return bar

    def _build_button(self, spec: ButtonSpec, parent: QWidget):
        if spec.window_icon:
            button = create_window_button(
                parent,
                WINDOW_ICONS.get(spec.window_icon, QStyle.SP_TitleBarNormalButton),
                str(spec.tooltip),
                lambda: self.resolve_with(spec.semantic),
                danger=spec.role == "danger",
            )
        else:
            button = (
                QToolButton(parent)
                if spec.tool_button
                else QPushButton(str(spec.text), parent)
            )
            if spec.tool_button:
                button.setText(str(spec.text))
            if spec.tooltip:
                button.setToolTip(str(spec.tooltip))
            if spec.accessible_name:
                button.setAccessibleName(str(spec.accessible_name))
            if spec.bold or spec.font_size or getattr(spec, "default_font", False):
                # 未指定字号时传 None 取后端默认字体；传 0 会得到退化字号的空字体。
                font = self._font_factory(int(spec.font_size) if spec.font_size else None)
                if spec.bold:
                    font.setBold(True)
                button.setFont(font)
            if spec.semantic:
                button.clicked.connect(
                    lambda _=False, key=spec.semantic: self.resolve_with(key)
                )
        if spec.fixed_size:
            width, height = (int(value) for value in spec.fixed_size)
            if width and height:
                button.setFixedSize(width, height)
        if getattr(spec, "min_height", 0):
            button.setMinimumHeight(int(spec.min_height))
        if spec.object_name:
            button.setObjectName(spec.object_name)
        button.setProperty("windowActionRole", spec.role)
        self._register(spec.id, button)
        if spec.semantic:
            self._buttons[spec.semantic] = spec
        return button

    def _register(self, node_id: str, widget: QWidget) -> None:
        if node_id:
            self._nodes[node_id] = widget

    def _install_drag_handles(self, spec: WindowSpec) -> None:
        """把 `drag_handle_ids` 指向的控件接成拖动把手（无原生标题栏时用）。"""

        handles = [
            self._nodes[node_id]
            for node_id in spec.drag_handle_ids
            if node_id in self._nodes
        ]
        if handles:
            self._drag_filter.attach(*handles)

    def _emit(self, semantic: str) -> None:
        if self._on_semantic is not None:
            self._on_semantic(semantic)

    # ── 语义动作与落位 ───────────────────────────────────────────────

    def resolve_with(self, semantic: str) -> None:
        """Translate a button into a delegated action, a hide, or a final decision."""
        if self._resolved or self._cleaned:
            return
        if semantic in self.spec.non_closing_actions:
            self._emit(semantic)
            return
        if semantic in self.spec.hide_semantics:
            self.hide_dialog()
            self._emit(semantic)
            return
        self._resolved = True
        self._decision = semantic
        self._emit(semantic)
        if self._cleaned or sip.isdeleted(self.widget):
            return
        if isinstance(self.widget, QDialog):
            result = QDialog.Rejected if semantic == self.spec.close_semantic else QDialog.Accepted
            if not self.request_modal_done(result):
                self.widget.done(result)
        elif self._keeps_window():
            self.hide_dialog()
        else:
            self.widget.close()

    def request_modal_done(self, result: int) -> bool:
        """Intercept QDialog accept/reject/done until the shared fade completes."""
        if (
            not isinstance(self.widget, QDialog)
            or not self.spec.modal
            or not self._fade
            or self._modal_exit_complete
            or self._cleaned
        ):
            return False
        if self._modal_done_result is not None:
            return True
        self._modal_done_result = int(result)
        if not self._resolved:
            # Escape/Alt+F4/QDialog.reject() must resolve to the safe close semantic.
            self._resolved = True
            self._decision = self.spec.close_semantic
            if self._decision:
                self._emit(self._decision)
        self._requested_visible = False
        self._closing_animation = True
        self._animate_to(0.0)
        return True

    def dismiss_without_decision(self) -> None:
        if not self._resolved and not self._cleaned:
            self._resolved = True
            if isinstance(self.widget, QDialog):
                self.widget.reject()
            elif self._keeps_window():
                self.hide_dialog()
            else:
                self.widget.close()

    def note_widget_closed(self) -> None:
        self._requested_visible = False
        self.note_widget_hidden()
        if not self._resolved and not self._cleaned:
            self._resolved = True
            self._decision = self.spec.close_semantic
            if self._decision:
                self._emit(self._decision)

    def _on_dialog_finished(self, _result: int) -> None:
        # An external call to accept/reject/done still participates in shared
        # fade-out. Our own fade completion marks `_resolved` before done() to
        # avoid starting a second transition.
        if self.spec.modal and self._fade and not self._modal_exit_complete and not self._cleaned:
            self._modal_done_result = int(_result)
            self._resolved = True
            self._requested_visible = False
            self._closing_animation = True
            self._animate_to(0.0)
            return
        self._modal_exit_complete = True
        self.note_widget_closed()

    def _keeps_window(self) -> bool:
        return not isinstance(self.widget, QDialog) and self._fade and not self.spec.delete_on_close

    def close_requested(self) -> bool:
        """Route native close through the same action as the displayed ×."""
        if (self._keeps_window() or self.spec.modal and self._fade) and self.widget.isVisible():
            semantic = self.spec.close_semantic
            if semantic:
                self.resolve_with(semantic)
            else:
                self.hide_dialog()
            return True
        self.note_widget_closed()
        return False

    # ── Visibility, animation and teardown ────────────────────────────

    def _setup_fade(self) -> None:
        if self._fade:
            animation = QPropertyAnimation(self.widget, b"windowOpacity", self.widget)
            animation.setEasingCurve(QEasingCurve.InOutQuad)
            animation.finished.connect(self._on_animation_finished)
            self._opacity_animation = animation

    def _register_layer(self, name: str | None = None) -> None:
        if self._layer_registered or not self.spec.layer:
            return
        layer = WindowLayer.DIALOG if self.spec.layer == LAYER_DIALOG else WindowLayer.PANEL
        get_layer_manager().register(
            self.widget, layer, z=int(self.spec.modal),
            name=name or self.spec.object_name or "SpecWindow",
        )
        self._layer_registered = True

    def _unregister_layer(self) -> None:
        if self._layer_registered:
            self._layer_registered = False
            get_layer_manager().unregister(self.widget)

    def note_widget_shown(self) -> None:
        self._register_layer()
        if self._theme is not None and self.spec.theme_mode is None:
            self.apply_theme()
            self._theme.subscribe()
        if self._fade and not self._requested_visible and not self._closing_animation:
            # Direct widget.show()/open()/exec_() bypasses show_window(); still
            # apply shared theme, placement, layer, and fade-in.
            self._requested_visible = True
            if self.spec.center_on_parent or self.spec.center_on_cursor_screen:
                self.center_on()
            self.widget.setWindowOpacity(0.0)
            self._animate_to(apply_ui_opacity(1.0))
        elif not self._fade:
            self.widget.setWindowOpacity(apply_ui_opacity(1.0))

    def note_widget_hidden(self) -> None:
        self._unregister_layer()
        if self._theme is not None:
            self._theme.unsubscribe()

    def show_window(self, *, activate: bool = True, layer_name: str | None = None) -> None:
        """Show or restore one interaction, reversing an in-flight fade when needed."""
        if self._cleaned or sip.isdeleted(self.widget):
            return
        was_requested = self._requested_visible
        self._resolved = False
        self._decision = ""
        self._register_layer(layer_name)
        self._requested_visible = True
        self._closing_animation = False
        if not self.widget.isVisible():
            if self._fade and not was_requested:
                self.widget.setWindowOpacity(0.0)
            self.widget.setAttribute(Qt.WA_ShowWithoutActivating, not activate)
            self.widget.show()
        if self._fade and not was_requested:
            self._animate_to(apply_ui_opacity(1.0))
        elif not self._fade:
            self.widget.setWindowOpacity(apply_ui_opacity(1.0))
        self.widget.raise_()
        if activate:
            self.widget.activateWindow()
        get_layer_manager().enforce_burst()

    def hide_dialog(self, *, immediate: bool = False) -> None:
        """Hide the window for reuse. Background transitions can skip the fade."""
        if self._cleaned or sip.isdeleted(self.widget):
            return
        if not immediate and (self._closing_animation or not self.widget.isVisible()):
            return
        self._requested_visible = False
        if self._fade and not immediate:
            self._closing_animation = True
            self._animate_to(0.0)
        else:
            self._closing_animation = False
            if self._opacity_animation is not None:
                self._opacity_animation.stop()
            self.widget.hide()
            if self.spec.modal and isinstance(self.widget, QDialog) and self._modal_loop is not None:
                self._modal_loop.quit()

    def is_requested_visible(self) -> bool:
        return self._requested_visible

    def exec_modal(self, *, layer_name: str | None = None) -> str:
        """Run a modal interaction. An undecided exit returns the close semantic."""
        if not isinstance(self.widget, QDialog) or not self.spec.modal:
            raise ValueError("exec_modal 需要模态 WindowSpec")
        if self._cleaned or sip.isdeleted(self.widget):
            return self.spec.close_semantic
        self._resolved = False
        self._decision = ""
        self._modal_done_result = None
        self._modal_exit_complete = False
        self._register_layer(layer_name)
        self._requested_visible = True
        if self.spec.center_on_parent or self.spec.center_on_cursor_screen:
            self.center_on()
        if self._fade:
            self.widget.setWindowOpacity(0.0)
        self.widget.show()
        get_layer_manager().enforce_burst()
        if self._fade:
            self._animate_to(apply_ui_opacity(1.0))
        try:
            while self._modal_done_result is None and not self._cleaned:
                self.widget.exec_()
                # QDialog.reject()/accept() returns immediately after starting the
                # shared fade-out; keep the modal caller blocked until it finishes.
                if self._closing_animation:
                    loop = QEventLoop()
                    self._modal_loop = loop
                    if self._opacity_animation is not None:
                        self._opacity_animation.finished.connect(loop.quit)
                    loop.exec_()
                    self._modal_loop = None
                if self._modal_exit_complete or self._cleaned:
                    break
            return self._decision or self.spec.close_semantic
        finally:
            self._requested_visible = False
            self.note_widget_hidden()
            self._modal_done_result = None
            self._modal_exit_complete = False

    def cleanup(self) -> None:
        """Stop animation, release the layer, and delete the underlying widget."""
        if self._cleaned:
            return
        self._cleaned = True
        self._requested_visible = False
        self._closing_animation = False
        self.note_widget_hidden()
        if sip.isdeleted(self.widget):
            return
        if self._opacity_animation is not None:
            self._opacity_animation.stop()
        self._drag_filter.detach()
        self.widget.hide()
        self.widget.deleteLater()

    def _animate_to(self, target: float) -> None:
        animation = self._opacity_animation
        animation.stop()
        animation.setDuration(ui_fade_duration_ms())
        animation.setStartValue(float(self.widget.windowOpacity()))
        animation.setEndValue(target)
        animation.start()

    def _on_animation_finished(self) -> None:
        # QPropertyAnimation emits `finished` for fade-in too; wait until the
        # modal exit latch is set, otherwise QDialog would close immediately on
        # the first completed fade-in.
        if self._closing_animation and not self._requested_visible:
            self._closing_animation = False
            self.widget.hide()
            if self._modal_done_result is not None and isinstance(self.widget, QDialog):
                result = self._modal_done_result
                self._modal_done_result = None
                self._modal_exit_complete = True
                self.note_widget_closed()
                self.widget.done(result)

    def center_on(self, reference_rect=None) -> None:
        """把窗口居中到 `reference_rect`，缺省则居中到光标所在屏幕。

        居中算术与浮窗共用 `visuals/layout.py` 的一份实现（「中心对中心再夹取」）。
        """

        widget = self.widget
        if not any(self.spec.initial_size) and not all(self.spec.fixed_size):
            widget.adjustSize()
        size = (widget.width(), widget.height())
        if reference_rect is not None:
            screen = self._presentation.screen_rect_for_widget(
                widget, point=_rect_center(reference_rect)
            )
        else:
            parent = widget.parentWidget()
            if parent is None and self._anchor_parent is not None and not sip.isdeleted(self._anchor_parent):
                parent = self._anchor_parent
            if parent is not None and parent.isVisible():
                reference_rect = self._presentation.widget_global_rect(parent)
                screen = self._presentation.screen_rect_for_widget(
                    parent, point=_rect_center(reference_rect)
                )
            else:
                reference_rect = None
                cursor_point = None
                if self.spec.center_on_cursor_screen:
                    from PyQt5.QtGui import QCursor

                    cursor_point = QCursor.pos()
                screen = self._presentation.screen_rect_for_widget(
                    widget, point=cursor_point
                )
        if screen is None:
            return
        if reference_rect is None:
            placement = PlacementSpec().resolve_centered(size, screen)
        else:
            placement = PlacementSpec(
                target_anchor_id="center", self_anchor_id="center"
            ).resolve_placement(size, reference_rect, screen)
        widget.move(placement.x, placement.y)

    # ── 主题 ─────────────────────────────────────────────────────────

    def _refresh_icons(self) -> None:
        """按当前主题把图标铺到位（颜色语义 → 具体色）。"""

        for node in _walk(self.spec.content):
            if not isinstance(node, IconSpec):
                continue
            label = self._nodes.get(node.id)
            if label is None:
                continue
            color = resolve_token_color(node.color)
            size = int(node.size) or scale_px(24, min_abs=21)
            label.setPixmap(render_office_icon(node.name, color).pixmap(size, size))
        for spec in self._buttons.values():
            if not spec.icon:
                continue
            button = self._nodes.get(spec.id) if spec.id else None
            if button is None:
                continue
            color = resolve_token_color(spec.icon_color)
            button.setIcon(render_office_icon(spec.icon, color))

    def apply_theme(self, spec: WindowSpec | None = None) -> None:
        """重新按描述刷样式表与图标色（主题切换后调用）。"""

        if spec is not None:
            self.spec = spec
        if self.spec.shared_popup:
            stylesheet = announcement_stylesheet(self.spec.theme_mode)
        else:
            stylesheet = apply_color_tokens(self.spec.stylesheet)
        if stylesheet != self.widget.styleSheet():
            self.widget.setStyleSheet(stylesheet)
        if self.spec.shared_popup:
            for node_id, html in self._rich_html.items():
                view = self.find(node_id)
                doc = view.document()
                document_css = announcement_document_stylesheet(self.spec.theme_mode)
                if doc.defaultStyleSheet() != document_css:
                    scrollbar = view.verticalScrollBar()
                    position = scrollbar.value()
                    doc.setDefaultStyleSheet(document_css)
                    view.setHtml(html)
                    scrollbar.setValue(position)
        self.widget.update()
        self._refresh_icons()


def _rect_center(rect) -> object:
    """取任意矩形对象的中心点（核心 `Point`）；无法取值时返回 `None`。"""

    from lib.core.render.visuals.types import coerce_point, coerce_rect

    core = coerce_rect(rect)
    if core is None:
        return coerce_point(rect)
    return core.center


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        children = getattr(current, "children", None)
        if children:
            stack.extend(children)


class _SpecWidgetMixin:
    """给宿主自己造出来的承载窗口用：把 Qt 事件转成描述层的语义。

    Qt 的窗口事件（显示、关闭）只有落到真实 `QWidget` 上才会来；描述层不持有这些
    事实，因此由本 mixin 把两个事件转发给 `QtSpecWindow`。
    """

    _spec_window: QtSpecWindow | None = None
    _placed: bool = False

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 事件名
        super().showEvent(event)
        window = self._spec_window
        if window is None:
            return
        window.note_widget_shown()
        if not self._placed:
            self._placed = True
            if window.spec.center_on_parent or window.spec.center_on_cursor_screen:
                window.center_on()
        get_layer_manager().enforce_burst()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 事件名
        if self._spec_window is not None:
            self._spec_window.note_widget_hidden()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 事件名
        if self._spec_window is not None and self._spec_window.close_requested():
            event.ignore()
            return
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 事件名
        window = self._spec_window
        if window is not None and window.paint_border_frame(event):
            return
        super().paintEvent(event)


class QtSpecDialog(_SpecWidgetMixin, QDialog):
    """承载 `WindowSpec` 的无窗眉对话框。"""

    def done(self, result: int) -> None:
        window = self._spec_window
        if window is not None and window.request_modal_done(result):
            return
        super().done(result)


class QtSpecWidget(_SpecWidgetMixin, QWidget):
    """承载 `WindowSpec` 的普通窗口。"""


def build_spec_window(
    spec: WindowSpec,
    *,
    font_factory: Callable,
    presentation_host,
    on_semantic: Callable[[str], None] | None = None,
    on_destroyed: Callable[[], None] | None = None,
    dialog: bool = True,
    parent: QWidget | None = None,
    widget: QtSpecWidget | QtSpecDialog | None = None,
) -> QtSpecWindow:
    """装配描述，也可装入持有复杂正文控件的 Qt 宿主子类。"""

    tool_window = spec.kind == WINDOW_TOOL
    # Keep non-modal shared popups unparented at the QWidget level so ancestor
    # stylesheets (such as WorkbenchPageHost/SettingsPage) can never cascade into
    # the popup, while preserving `parent` as the centering reference.
    # For modal dialogs, parent is kept only when needed for WindowModal modality,
    # and the shared popup stylesheet explicitly resets font-size/min-height.
    anchor_parent = parent if isinstance(parent, QWidget) else None
    if widget is None:
        # Modal shared popups also stay unparented at QWidget level (ApplicationModal)
        # so ancestor QSS selectors like `QFrame#WorkbenchPageHost QWidget#SettingsPage QPushButton`
        # can never out-specify the popup's own stylesheet, while `anchor_parent` still
        # centers the popup over the caller window.
        qt_parent = None if spec.shared_popup else parent
        widget = QtSpecWidget(qt_parent) if tool_window or not dialog else QtSpecDialog(qt_parent)
    elif spec.shared_popup and widget.parentWidget() is not None:
        anchor_parent = anchor_parent or widget.parentWidget()
        widget.setParent(None)
    _configure_widget(widget, spec, tool_window=tool_window)
    window = QtSpecWindow(
        widget,
        spec,
        font_factory=font_factory,
        presentation_host=presentation_host,
        on_semantic=on_semantic,
        on_destroyed=on_destroyed,
        anchor_parent=anchor_parent,
    )
    widget._spec_window = window
    return window


def _configure_widget(widget: QWidget, spec: WindowSpec, *, tool_window: bool) -> None:
    """Set window flags, modality and icon for the supplied host."""

    flags = Qt.Tool if tool_window else Qt.Dialog if isinstance(widget, QDialog) else Qt.Window
    if spec.frameless:
        flags |= Qt.FramelessWindowHint
    if spec.stay_on_top:
        flags |= Qt.WindowStaysOnTopHint
    widget.setWindowFlags(flags)
    if spec.modal and isinstance(widget, QDialog):
        widget.setWindowModality(
            Qt.WindowModal if widget.parentWidget() is not None else Qt.ApplicationModal
        )
    if spec.window_icon_path:
        from pathlib import Path

        from PyQt5.QtGui import QIcon

        if Path(spec.window_icon_path).is_file():
            widget.setWindowIcon(QIcon(str(spec.window_icon_path)))


__all__ = [
    "QtSpecDialog",
    "QtSpecWidget",
    "QtSpecWindow",
    "WINDOW_ICONS",
    "apply_color_tokens",
    "build_spec_window",
    "resolve_token_color",
]
