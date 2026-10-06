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

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.render.backends.qt.widgets.smooth_scroll import SmoothScrollArea
from lib.core.render.backends.qt.widgets.floating_window import FloatingDragFilter
from lib.core.render.backends.qt.widgets.office_icons import render_office_icon
from lib.core.render.backends.qt.widgets.window_buttons import create_window_button
from lib.core.render.layers import WindowLayer, get_layer_manager
from lib.core.services.ui_presentation import ui_fade_duration_ms
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
    ) -> None:
        self.widget = widget
        self.spec = spec
        self._font_factory = font_factory
        self._on_semantic = on_semantic
        self._presentation = presentation_host
        self._nodes: dict[str, QWidget] = {}
        self._buttons: dict[str, ButtonSpec] = {}
        self._root_layout = None
        self._resolved = False
        self._closed_by_widget = False
        self._drag_filter = FloatingDragFilter(widget)
        self._scroll_by_id: dict[str, QWidget] = {}
        self._layer_registered = False
        self._fade = bool(getattr(spec, "fade", False))
        self._requested_visible = False
        self._closing_animation = False
        self._opacity_animation: QPropertyAnimation | None = None
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
        if spec.fixed_size:
            width, height = (int(value) for value in spec.fixed_size)
            if width and height:
                widget.setFixedSize(width, height)
            elif width:
                widget.setFixedWidth(width)
            elif height:
                widget.setFixedHeight(height)
        self._setup_border_frame(spec)

        layout = self._build_layout(spec.content, widget)
        widget.setLayout(layout)
        self._root_layout = layout
        self.apply_theme()

    def _setup_border_frame(self, spec: WindowSpec) -> None:
        """描边外壳：外描边 + 内容底色两层矩形（公告 / 更新浮窗同款）。"""

        if not spec.border_frame:
            return
        self._border_width = max(1, int(spec.border_width) or 1)

    def paint_border_frame(self, event) -> bool:
        """描边外壳的绘制；未启用时返回 False，由承载窗口走默认绘制。

        颜色在**每次重绘时**取，与公告 / 更新浮窗一样跟着工作台主题走。
        """

        if not self._border_width:
            return False
        painter = QPainter(self.widget)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.widget.rect(), QColor(resolve_token_color("border_strong")))
        inset = self._border_width
        painter.fillRect(
            self.widget.rect().adjusted(inset, inset, -inset, -inset),
            QColor(resolve_token_color("canvas")),
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
        if alignment:
            layout.addWidget(widget, stretch, alignment)
        else:
            layout.addWidget(widget, stretch)

    def _build_container(self, spec: LayoutSpec, parent: QWidget) -> QWidget:
        """把一个 ``LayoutSpec`` 装成容器；``scroll`` 时外套一层平滑滚动视口。"""

        container = QFrame(parent) if spec.frame else QWidget(parent)
        if spec.object_name:
            container.setObjectName(spec.object_name)
        if spec.fixed_width:
            container.setFixedWidth(int(spec.fixed_width))
        if spec.fixed_height:
            container.setFixedHeight(int(spec.fixed_height))
        if spec.scroll:
            scroll = SmoothScrollArea(parent)
            if spec.scroll_object_name:
                scroll.setObjectName(spec.scroll_object_name)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            holder = QWidget(scroll)
            if spec.scroll_host_object_name:
                holder.setObjectName(spec.scroll_host_object_name)
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(*(int(v) for v in spec.scroll_margin))
            holder_layout.setSpacing(0)
            for child in spec.children:
                self._add_child(holder_layout, child, holder)
            holder_layout.addStretch(1)
            scroll.setWidget(holder)
            self._scroll_by_id[spec.id] = scroll
            self._register(spec.id, scroll)
            return scroll
        self._build_layout(spec, container)
        self._register(spec.id, container)
        return container

    def _build_widget(self, child, parent: QWidget):
        if isinstance(child, LabelSpec):
            return self._build_label(child, parent), _qt_alignment(child)
        if isinstance(child, IconSpec):
            return self._build_icon_label(child, parent), _qt_alignment(child)
        if isinstance(child, AccentBarSpec):
            return self._build_accent_bar(child, parent), 0
        if isinstance(child, TextAreaSpec):
            return self._build_text_area(child, parent), 0
        if isinstance(child, ButtonSpec):
            return self._build_button(child, parent), _qt_alignment(child)
        return None, 0

    def _build_label(self, spec: LabelSpec, parent: QWidget) -> QLabel:
        label = QLabel(str(spec.text), parent)
        if spec.object_name:
            label.setObjectName(spec.object_name)
        if spec.font_size:
            font = self._font_factory(int(spec.font_size))
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
        font = self._font_factory(int(spec.font_size) if spec.font_size else 0)
        if spec.monospace:
            font.setFamily("Consolas")
        view.setFont(font)
        self._register(spec.id, view)
        return view

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
            if spec.bold or spec.font_size:
                font = self._font_factory(int(spec.font_size) if spec.font_size else 0)
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
        if spec.object_name:
            button.setObjectName(spec.object_name)
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
        """给出决定并关闭：发一次语义回调，再按对话框语义 `accept()`。"""

        widget = self.widget
        if self._resolved:
            return
        if str(semantic) in self._hide_semantics:
            self._emit(semantic)
            self.hide_dialog()
            return
        self._resolved = True
        self._emit(semantic)
        accept = getattr(widget, "accept", None)
        if callable(accept):
            accept()
        else:
            widget.close()

    def dismiss_without_decision(self) -> None:
        """不发语义回调地收起（调用方已经放弃这次交互）。"""

        if self._resolved:
            return
        self._resolved = True
        reject = getattr(self.widget, "reject", None)
        if callable(reject):
            reject()
        else:
            self.widget.close()

    def note_widget_closed(self) -> None:
        """窗口被原生关闭路径收起时，按 `close_semantic` 补发一次决定。

        与产品窗口此前在 `closeEvent` 里补发拒绝等价：关闭按钮、Esc 与窗口管理器
        关窗都会走同一条语义。
        """

        if self._resolved:
            return
        self._resolved = True
        if self.spec.close_semantic:
            self._emit(self.spec.close_semantic)
        self._unregister_layer()
        self._requested_visible = False

    def closed_by_widget_should_hide(self) -> bool:
        """工具类浮窗被原生关闭路径收起时只隐藏，不结束窗口生命周期。"""

        return self._fade and not self.spec.delete_on_close and bool(self._layer_name)

    # ── 显示、淡入淡出与收尾（浮窗语义）──────────────────────────────

    def _setup_fade(self) -> None:
        """给需要淡入淡出的窗口配一条 ``windowOpacity`` 动画。"""

        self._hide_semantics = tuple(getattr(self.spec, "hide_semantics", ()) or ())
        self._layer_name = str(getattr(self.spec, "layer", "") or "")
        if not self._fade:
            return
        animation = QPropertyAnimation(self.widget, b"windowOpacity", self.widget)
        animation.setDuration(int(ui_fade_duration_ms()))
        animation.setEasingCurve(QEasingCurve.InOutQuad)
        animation.finished.connect(self._on_animation_finished)
        self._opacity_animation = animation

    def _register_layer(self) -> None:
        """按描述把窗口注册进 `LayerManager`（只注册一次）。"""

        if self._layer_registered or not self._layer_name:
            return
        self._layer_registered = True
        if self._layer_name == LAYER_DIALOG:
            layer = WindowLayer.DIALOG
        else:
            layer = WindowLayer.PANEL
        get_layer_manager().register(
            self.widget, layer, name=self.spec.object_name or "SpecWindow"
        )

    def _unregister_layer(self) -> None:
        if not self._layer_registered:
            return
        self._layer_registered = False
        try:
            get_layer_manager().unregister(self.widget)
        except Exception:
            pass

    def show_window(self) -> None:
        """显示窗口：居中落位、注册层级、前置并淡入。"""

        self._register_layer()
        was_visible = self._requested_visible
        self._requested_visible = True
        self._closing_animation = False
        if not was_visible:
            if self._fade:
                self.widget.setWindowOpacity(0.0)
            self.widget.show()
        get_layer_manager().bring_to_front(self.widget)
        self.widget.raise_()
        self.widget.activateWindow()
        if self._fade:
            self._animate_to(apply_ui_opacity(1.0))

    def hide_dialog(self) -> None:
        """收起窗口：淡出后 ``hide()``；不结束窗口生命周期。"""

        if not self._requested_visible:
            return
        self._requested_visible = False
        if self._fade:
            self._closing_animation = True
            self._animate_to(0.0)
        else:
            self.widget.hide()

    def is_requested_visible(self) -> bool:
        """窗口是否处于「请求可见」状态（淡出过程中即为 False）。"""

        return self._requested_visible

    def cleanup(self) -> None:
        """停掉动画、注销层级并销毁底层窗口（幂等）。"""

        if self._opacity_animation is not None:
            self._opacity_animation.stop()
        self._requested_visible = False
        self._closing_animation = False
        self._drag_filter.detach()
        self.widget.hide()
        self._unregister_layer()
        self.widget.deleteLater()

    def _animate_to(self, target: float) -> None:
        animation = self._opacity_animation
        if animation is None:
            return
        animation.stop()
        animation.setStartValue(float(self.widget.windowOpacity()))
        animation.setEndValue(float(target))
        animation.start()

    def _on_animation_finished(self) -> None:
        if self._closing_animation and not self._requested_visible:
            self._closing_animation = False
            self.widget.hide()

    def center_on(self, reference_rect=None) -> None:
        """把窗口居中到 `reference_rect`，缺省则居中到光标所在屏幕。

        居中算术与浮窗共用 `visuals/layout.py` 的一份实现（「中心对中心再夹取」）。
        """

        widget = self.widget
        widget.adjustSize()
        size = (widget.width(), widget.height())
        if reference_rect is not None:
            screen = self._presentation.screen_rect_for_widget(
                widget, point=_rect_center(reference_rect)
            )
        else:
            parent = widget.parentWidget()
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
        if self.spec.stylesheet:
            self.widget.setStyleSheet(
                apply_color_tokens(self.spec.stylesheet)
            )
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
        if window is None or self._placed:
            return
        self._placed = True
        if window.spec.center_on_parent or window.spec.center_on_cursor_screen:
            window.center_on()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 事件名
        window = self._spec_window
        if window is not None:
            if window.closed_by_widget_should_hide():
                event.ignore()
                window.hide_dialog()
                return
            window.note_widget_closed()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 事件名
        window = self._spec_window
        if window is not None and window.paint_border_frame(event):
            return
        super().paintEvent(event)


class QtSpecDialog(_SpecWidgetMixin, QDialog):
    """承载 `WindowSpec` 的无窗眉对话框。"""


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
) -> QtSpecWindow:
    """按描述构造承载窗口并装配控件树。"""

    widget = _make_widget(spec, dialog=dialog, parent=parent)
    window = QtSpecWindow(
        widget,
        spec,
        font_factory=font_factory,
        presentation_host=presentation_host,
        on_semantic=on_semantic,
        on_destroyed=on_destroyed,
    )
    widget._spec_window = window
    return window


def _make_widget(spec: WindowSpec, *, dialog: bool, parent: QWidget | None) -> QWidget:
    """构造承载窗口并设好窗口标志、模态与窗口图标。"""

    tool_window = spec.kind == WINDOW_TOOL
    widget = QtSpecWidget(parent) if tool_window else QtSpecDialog(parent)
    flags = Qt.Window
    if tool_window:
        flags |= Qt.Tool
    if spec.frameless:
        flags |= Qt.FramelessWindowHint
    if spec.stay_on_top:
        flags |= Qt.WindowStaysOnTopHint
    if dialog and not tool_window:
        flags |= Qt.Dialog
    widget.setWindowFlags(flags)
    if spec.modal and dialog:
        widget.setWindowModality(
            Qt.WindowModal if parent is not None else Qt.ApplicationModal
        )
    if spec.window_icon_path:
        from pathlib import Path

        from PyQt5.QtGui import QIcon

        if Path(spec.window_icon_path).is_file():
            widget.setWindowIcon(QIcon(str(spec.window_icon_path)))
    return widget


__all__ = [
    "QtSpecDialog",
    "QtSpecWidget",
    "QtSpecWindow",
    "WINDOW_ICONS",
    "apply_color_tokens",
    "build_spec_window",
    "resolve_token_color",
]
