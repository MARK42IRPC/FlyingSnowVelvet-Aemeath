"""供 `lib/script/ui` 控件使用的后端无关渲染适配。

产品控件本身是 QWidget，绘制时要拿到两样东西：一个绘制实现，和 QWidget 画完之后
能交给它去画的图像对象。这两件事都是 Qt 事实，但它们出现在控件代码里就会让控件直接
依赖 `lib/core/render/backends/qt/drawing/`，那正是 `doc/render层边界契约.md` 档位 A
要挡住的引用——一旦控件能自己 new 出 Qt 绘制实现，"换后端不动业务层"就无从谈起。

所以控件只问本模块要能力，本模块是唯一允许触碰到具体实现的 UI 侧落点：

- 已配置后端（正常运行时）→ 取后端注册进 registry 的绘制实现；
- 未配置后端（控件单元测试、隔离 helper）→ 直接构造 Qt 实现。

第二种情况看着像"偷偷用 Qt"，其实是刻意的：Qt 是产品唯一受支持的后端，控件测试要
比对真实像素就必须有真实绘制实现。回退到空实现会让控件画不出东西（历史上正是如此），
那既掩盖回归也让像素断言失去意义。本模块的存在把这一事实收敛到一处，而不是让它
散落在二十多个控件文件里。

同一份理由适用于这里的颜色与坐标转换：色板事实源在 `visuals/palette.py`，但 QWidget
要的是 `QColor`，锚点计算要的是 `QPoint`。把这两件 Qt 事实也收进本模块，控件就不必
为了一个 `UI_THEME['mid']` 或一次 `coerce_qpoint` 去直接 import 绘制档。

平台能力（档位 B：屏幕几何、字体、文本度量）走同一层但方向相反：本模块不构造实现，
而是把 registry 里后端注入的服务转出来。控件拿到的屏幕几何是核心 `Rect`，字体与
度量则是后端自己的对象——控件本身是工具包控件，这一步收敛的是"从哪里取"。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from lib.core.render.visuals.anchors import get_anchor_point as get_rect_anchor_point
from lib.core.render.visuals.types import Rect, coerce_point

from lib.core.render.registry import (
    get_draw_backend_factory,
    get_font_provider,
    get_presentation_host,
    get_text_metrics_factory,
)

if TYPE_CHECKING:
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QColor


def pointer_button_name(event) -> str:
    """把 Qt 鼠标事件的按钮翻译成后端中立名（`left` / `right` / `middle` / `none`）。

    产品层（如点击粒子）只需要"哪个键"，不需要 Qt；本函数是档位 A 的翻译落点。
    """
    from PyQt5.QtCore import Qt

    from lib.core.render.visuals.controls import (
        BUTTON_LEFT,
        BUTTON_MIDDLE,
        BUTTON_NONE,
        BUTTON_RIGHT,
    )

    return {
        Qt.LeftButton: BUTTON_LEFT,
        Qt.RightButton: BUTTON_RIGHT,
        Qt.MiddleButton: BUTTON_MIDDLE,
    }.get(event.button(), BUTTON_NONE)


def set_widget_clickthrough(widget, enabled: bool) -> None:
    """把“鼠标穿透”开关落到一个控件上（档位 A 的翻译落点）。

    控件控制器（如游戏运行时）只需要“这一层收不收鼠标”这个语义，不需要知道 Qt 用
    ``WA_TransparentForMouseEvents`` 表达它。把这件 Qt 事实收进本模块，与
    ``pointer_button_name`` 同级——两者都只是把产品语义翻译成工具包事实。
    """
    from PyQt5.QtCore import Qt

    widget.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))


def create_draw_backend():
    """返回当前后端的绘制实现；没有已配置后端时返回 Qt 实现。"""
    factory = get_draw_backend_factory()
    if factory is not None:
        return factory()
    from lib.core.render.backends.qt.drawing.draw_backend import QtDrawBackend

    return QtDrawBackend()


def create_control_host(**kwargs):
    """创建渲染单个控件的后端窗口宿主（Qt 下是 ``QtControlHost``）。

    控件描述（``lib/core/render/visuals/controls.py``）不含窗口事实，真实窗口由后端
    提供；本函数是控件侧唯一允许触碰"控件窗口宿主"实现的落点，与
    ``create_draw_backend`` 同属档位 A 的解析/转发层。
    """
    from lib.core.render.backends.qt.widgets.control_host import QtControlHost

    kwargs.setdefault("draw_backend", create_draw_backend())
    kwargs.setdefault("presentation_host", presentation_host())
    return QtControlHost(**kwargs)


def create_painter_host():
    """创建"在调用方自己的 QPainter 上执行绘制批次"的后端宿主（档位 D）。

    与 ``create_control_host`` 同属解析/转发层，但形状相反：控件窗口宿主负责"有个窗口"，
    本宿主只负责"把批次画到 painter 上"。产品控件是 ``QWidget`` 子类，``paintEvent``
    必须自己起 ``QPainter``，而它的包壳配方（面板三层、按钮四态、几何图标）是后端中立
    事实，因此这里把绘制实现与 UI 字体注入进去，让控件不必 import 档位 A。

    返回 ``QtPainterHost``：``render(batch, painter)`` 执行批次，``color`` / ``rect``
    做边界类型转换，``font()`` 取后端 UI 字体。
    """
    from lib.core.render.backends.qt.widgets.control_painter_host import QtPainterHost

    return QtPainterHost(draw_backend=create_draw_backend(), font_factory=ui_font)


def painter_color(value):
    """核心 ``Color`` / 通道元组 / ``QColor`` → ``QColor``。

    控件在 ``paintEvent`` 里设置画笔、画刷时用它把共享色板的值换成 Qt 值，从而不必
    自己 import 绘制档的 ``to_qcolor``。
    """
    return create_painter_host().color(value)


def apply_settings_page_fonts(page) -> None:
    """把工作台设置页的字号档铺到一页控件树上（产品面调用面不变）。"""
    from lib.script.ui.workbench_settings_layout import apply_settings_page_fonts as apply

    apply(page)


def apply_office_widget_fonts(
    root,
    *,
    settings_font_size: int | None = None,
    settings_hint_font_size: int | None = None,
) -> None:
    """办公面独有的控件与标签铺字号（Qt 实现见 ``widgets/office_widgets.py``）。

    与 ``create_control_host`` / ``create_painter_host`` 同属解析/转发层：办公面样式门面
    ``office_style`` 只交一棵控件树与字号档，真实遍历由注入的 Qt 实现完成，门面因此
    不必 import ``PyQt5``。
    """
    from lib.core.render.backends.qt.widgets.office_widgets import (
        apply_office_widget_fonts as apply,
    )

    apply(
        root,
        font_factory=ui_font,
        settings_font_size=settings_font_size,
        settings_hint_font_size=settings_hint_font_size,
    )


def create_office_accent_bar(parent):
    """创建办公面顶部的双色 accent bar（Qt 实现见 ``widgets/office_widgets.py``）。"""
    from lib.core.render.backends.qt.widgets.office_widgets import (
        create_office_accent_bar as create,
    )

    return create(parent)


def render_office_icon(name: str, color: str):
    """渲染一枚办公线性图标（Qt 返回 `QIcon`）。

    图形事实（SVG 文本与尺寸）在后端中立的 `visuals/office_icons.py`，本函数只是
    控件取的唯一落点。将来其它后端按同一份规格渲染，控件调用面不变。
    """
    from lib.core.render.backends.qt.widgets.office_icons import render_office_icon as render

    return render(name, color)


def render_office_icon_pixmap(name: str, color: str, pixel_size: int):
    """渲染办公图标并取指定像素尺寸的位图（Qt 返回 `QPixmap`）。"""
    from lib.core.render.backends.qt.widgets.office_icons import (
        render_office_icon_pixmap as render,
    )

    return render(name, color, pixel_size)


def create_message_box_host(parent=None, *, object_name: str = ""):
    """创建模态消息框宿主（Qt 下是 ``QtMessageBoxHost``）。

    与 ``create_control_host`` 同属档位 A 的解析/转发层：确认/提示框只声明"问什么、
    有哪些按钮、用哪种配色"，真实 ``QMessageBox`` 与模态循环由后端提供。
    """
    from lib.core.render.backends.qt.widgets.message_box_host import QtMessageBoxHost

    return QtMessageBoxHost(parent, object_name=object_name)


def create_spec_window(
    spec,
    *,
    on_semantic=None,
    on_destroyed=None,
    dialog: bool = True,
    parent=None,
):
    """按后端中立的窗口描述（``visuals/window_spec.py``）装配一个真实窗口。

    这是"窗口级描述 + 后端渲染"的控件侧唯一落点，与 ``create_control_host`` /
    ``create_message_box_host`` 同类：产品窗口只交一棵 ``WindowSpec`` 与语义回调，
    真实控件树由后端宿主（Qt 下是 ``widgets/spec_host.py``）搭建。字体经本模块注入，
    档位 D 因此不静态引用档位 A。

    ``on_destroyed`` 对应产品窗口此前直接连 ``destroyed`` 的做法：迁移后产品窗口不再
    是 ``QObject``，生命周期回调经本入口回填。
    """
    from lib.core.render.backends.qt.widgets.spec_host import build_spec_window

    return build_spec_window(
        spec,
        font_factory=ui_font,
        presentation_host=presentation_host(),
        on_semantic=on_semantic,
        on_destroyed=on_destroyed,
        dialog=dialog,
        parent=parent,
    )


def create_floating_window_base(parent=None):
    """返回浮窗外壳的 Qt 基类 ``QtWorkbenchFloatingWindow``（档位 D）。

    `lib/script/ui` 的浮窗族（公告 / 更新 / 二维码 / 语音包 / 论坛）都以它为基类。
    直接 import `lib/core/render/backends/qt/widgets/` 会命中档位 D 的引用规则，
    因此这里和 ``create_control_host`` 一样，把"产品要继承的工具包基类"收敛成
    一个转发落点：换后端时只有本函数需要改，产品浮窗的调用面不变。
    """
    from lib.core.render.backends.qt.widgets.floating_window import QtWorkbenchFloatingWindow

    return QtWorkbenchFloatingWindow


def create_window_button(parent, standard_icon, tooltip, callback, *, danger: bool = False):
    """创建一枚窗眉控制按钮（最小化 / 最大化 / 关闭），档位 D 的唯一控件侧落点。"""
    from lib.core.render.backends.qt.widgets.window_buttons import create_window_button as create

    return create(parent, standard_icon, tooltip, callback, danger=danger)


def create_ui_dispatcher(parent=None):
    """创建 UI 线程调度宿主（档位 D 的 ``UiDispatcher``）。

    产品控制器（公告 / 论坛 / 帮助 / 办公模式页）需要把回调投递回 Qt 事件循环，
    此前各自声明一个 ``pyqtSignal(object)`` 并连 ``Qt.QueuedConnection``，因此被迫
    继承 ``QObject`` 且 import ``PyQt5``。本函数是它们取用该能力的唯一落点。
    """
    from lib.core.render.backends.qt.widgets.ui_dispatch import UiDispatcher

    return UiDispatcher(parent)


def workbench_overlay_classes():
    """返回工作台自绘件二件套：淡入淡出遮罩与明暗主题开关（档位 D）。

    与 ``floating_window_classes`` 同一用途：``lib/script/ui`` 的垫片要按名字再导出这两
    个类，而它自己不得静态 import 档位 D。两者只依赖尺寸助手与工作台主题色，不看窗口状态。
    """
    from lib.core.render.backends.qt.widgets.workbench_widgets import (
        _WorkbenchFadeOverlay,
        _WorkbenchThemeToggle,
    )

    return _WorkbenchFadeOverlay, _WorkbenchThemeToggle


def floating_window_classes():
    """返回浮窗实现的三件套：拖拽过滤器、主题观察者与基类（档位 D）。

    与 ``create_floating_window_base`` 同一用途，只是形态是三元组：``lib/script/ui``
    的门面需要按名字再导出这几个类，而它自己不得静态 import 档位 D。
    """
    from lib.core.render.backends.qt.widgets.floating_window import (
        FloatingDragFilter,
        FloatingWindowThemeWatcher,
        QtWorkbenchFloatingWindow,
    )

    return FloatingDragFilter, FloatingWindowThemeWatcher, QtWorkbenchFloatingWindow


def window_button_icons():
    """窗眉标准图标的语义名 → Qt 标准字形（`QStyle.SP_*`）映射。"""
    from lib.core.render.backends.qt.widgets.spec_host import WINDOW_ICONS

    return dict(WINDOW_ICONS)


def create_component_layer():
    """创建控件自用的绘制回调层（排序与注册是后端无关的，只有回调是 Qt）。"""
    from lib.core.render.backends.qt.drawing.render_core import QtComponentLayer

    return QtComponentLayer()


def create_component_layer_request(item_id, paint, layer: int = 0, z: int = 0, visible: bool = True):
    """构造一条绘制回调层注册请求。"""
    from lib.core.render.backends.qt.drawing.render_core import QtComponentLayerRequest

    return QtComponentLayerRequest(item_id, paint, layer=layer, z=z, visible=visible)


def qimage_from_raster_frame(frame):
    """把核心 RGBA 帧转换成 QWidget 能直接绘制的 Qt 图像对象。"""
    from lib.core.render.backends.qt.drawing.gif_loader import qimage_from_raster_frame as convert

    return convert(frame)


def qt_color(token: str) -> QColor:
    """取主题色的 Qt 表示：先查 `COLORS`，再查 `UI_THEME`。"""
    from lib.core.render.backends.qt.drawing.colors import to_qcolor
    from lib.core.render.visuals.palette import COLORS, UI_THEME

    table = COLORS if token in COLORS else UI_THEME
    return to_qcolor(table[token])


def qt_color_name(token: str) -> str:
    """取主题色的 `#rrggbb` 文本，用于 QSS 拼装。"""
    return qt_color(token).name()


def ensure_qcolor(value: object) -> QColor:
    """把 QColor / 核心 Color / RGBA 元组统一成 QColor。"""
    from lib.core.render.backends.qt.drawing.colors import to_qcolor

    return to_qcolor(value)


def qpoint_from_point(value: object) -> QPoint | None:
    """把锚点或坐标载荷转换成 QPoint；无效输入返回 None。"""
    from lib.core.render.backends.qt.drawing.window import coerce_qpoint

    return coerce_qpoint(value)


# ── 平台能力（档位 B）的后端中立取用 ────────────────────────────────────────
#
# 控件不必 `import lib.core.render.backends.qt.runtime.*` 就能拿到屏幕几何、字体与
# 文本度量。后端未注入时（隔离 helper、无后端的单元测试）回退到 Qt 实现与核心算法，
# 而不是抛错：控件仍然要能构造、能布局，只是拿不到平台特有能力。


def presentation_host():
    """当前后端的呈现几何实现；未注入时用 Qt 实现。"""
    host = get_presentation_host()
    if host is None:
        from lib.core.render.backends.qt.drawing.presentation import QtPresentationHost

        host = QtPresentationHost()
    return host


def screen_rect_for_point(point=None, fallback_widget=None):
    """控件所在屏幕的核心 `Rect`。

    屏幕归属是平台事实（Qt 看 `windowHandle()`，Win32 看 `MonitorFromPoint`），
    选择与夹取算法在 `lib/core/render/visuals/screen.py`。两个后端都返回核心
    `Rect`，控件不再需要 `QRect` 参与布局算术。
    """
    rect = presentation_host().screen_rect_for_widget(fallback_widget, point=point)
    if rect is not None:
        return rect
    from lib.core.render.visuals.screen import virtual_screen_rect

    return virtual_screen_rect(())


def clamp_rect_position(
    x: int,
    y: int,
    width: int,
    height: int,
    point=None,
    fallback_widget=None,
):
    """把窗口左上角夹取到屏幕内，返回 `(x, y, screen)`。

    第三个值是核心 `Rect`（此前是 Qt 的 `QRect`）；控件普遍写 `x, y, _ =`，
    形状保持不变，只是类型换成后端中立几何。取不到屏幕信息时原样返回，
    静默挪窗口比不挪更难排查。
    """
    host = presentation_host()
    screen = screen_rect_for_point(point=point, fallback_widget=fallback_widget)
    clamped = host.clamp_position(
        x, y, width, height, widget=fallback_widget, point=point
    )
    if clamped is None:
        return int(x), int(y), screen
    return clamped[0], clamped[1], screen


def resolve_placement(self_size, target_rect, screen, *, target_anchor_id="top_left",
                      self_anchor_id="bottom_left", offset_x=0.0, offset_y=0.0):
    """控件窗口落位的唯一共享入口（《render 层边界契约》档位 1）。

    控件此前各自写「取目标锚点、取自身锚点、相减、加偏移、夹取屏幕」，算术其实
    只有一份；这里把它转发给 ``visuals/layout.py`` 的 ``PlacementSpec``，控件只声明
    锚点与偏移。返回值是 ``AnchorPlacement``（窗口左上角 + 所在屏幕）。
    """
    from lib.core.render.visuals.layout import resolve_placement as resolve

    return resolve(
        self_size,
        target_rect,
        screen,
        target_anchor_id=target_anchor_id,
        self_anchor_id=self_anchor_id,
        offset_x=offset_x,
        offset_y=offset_y,
    )


def place_at_point(self_size, anchor_point, screen, *, target_anchor_id="top_left",
                    self_anchor_id="bottom_left", offset_x=0.0, offset_y=0.0):
    """目标只有一个全局锚点（而不是矩形）时的窗口落位。"""
    from lib.core.render.visuals.layout import PlacementSpec

    return PlacementSpec(
        target_anchor_id=target_anchor_id,
        self_anchor_id=self_anchor_id,
        offset_x=offset_x,
        offset_y=offset_y,
    ).resolve_from_point(self_size, anchor_point, screen)


def centered_placement(self_size, screen):
    """在 ``screen`` 里居中的窗口落位（公告 / 更新 / 帮助 / 语音包浮窗共用）。"""
    from lib.core.render.visuals.layout import PlacementSpec

    return PlacementSpec().resolve_centered(self_size, screen)


def resolve_anchor_graph(graph, root_rect, *, sizes=None, scale: float = 1.0):
    """一次解算一批窗口的落位（《render 层边界契约》档位 2）。

    右键按钮族不再逐控件收发锚点事件，而是把命令框矩形交给 ``visuals/anchor_graph.py``
    的声明式锚点图，一次拿到整族的屏幕矩形。本函数是控件侧唯一取用入口，与
    ``resolve_placement`` 同属档位 1/2 的中立转发面。
    """
    return graph.resolve(root_rect, sizes=sizes, scale=scale)


def command_action_graph():
    """右键按钮族的共享锚点图（逻辑尺寸，1080p 基准）。"""
    from lib.core.render.visuals.anchor_graph import COMMAND_ACTION_GRAPH

    return COMMAND_ACTION_GRAPH


def command_action_node(ui_id: str):
    """按 Qt 控件 ``_ui_id`` 取该控件在按钮族里的链路声明。"""
    from lib.core.render.visuals.anchor_graph import command_action_node as lookup

    return lookup(ui_id)


def family_placement(widget, node_id: str):
    """右键按钮族里某节点控件的目标屏幕矩形（档位 2 的统一落位入口）。

    节点控件不再自己取上游几何、算锚点：它向所在的 ``RightClickUiLayer`` 要一次整族
    解算的结果。返回核心 ``Rect``，宿主缺席时返回 ``None``。
    """
    host = getattr(widget, "_layer_host", None)
    getter = getattr(host, "family_rects", None)
    if not callable(getter):
        return None
    rects = getter()
    return None if rects is None else rects.get(node_id)


def core_point(value):
    """把 Qt 的 `QPoint` / `(x, y)` / 核心 `Point` 统一成核心 `Point`；无效输入返回 `None`。

    控件层做几何算术前用它把载荷归一化（锚点事件、光标位置都可能是任一形态），
    从而不必为了取 `.x()` 而 import Qt。
    """
    return coerce_point(value)


def local_anchor_point(anchor_id: str, width, height):
    """控件本地几何里某个锚点的位置（核心 `Point`）。

    与 Qt 侧 `get_anchor_point(widget, anchor_id)` 同为「窗口矩形取锚点」的一份事实，
    只是入参从 QWidget 换成已知宽高，控件因此不需要为一次锚点算术持有 Qt 对象。
    """
    return get_rect_anchor_point(Rect(0.0, 0.0, float(width), float(height)), str(anchor_id))


def widget_global_rect(widget):
    """控件在屏幕坐标系中的核心 `Rect`。"""
    return presentation_host().widget_global_rect(widget)


def widget_global_point(widget, point):
    """控件本地坐标点换算成屏幕坐标点（核心 `Point`）。"""
    return presentation_host().widget_global_point(widget, point)


def move_widget_to_global(widget, x: int, y: int) -> None:
    """按屏幕坐标移动控件；宿主分层时由后端换算成宿主本地坐标。"""
    presentation_host().move_widget_to_global(widget, int(x), int(y))


def pointer_position():
    """当前指针的屏幕位置（核心 `Point`）。"""
    from lib.core.render.backends.qt.drawing.window import pointer_core_position

    return pointer_core_position()


def pointer_cursor():
    """当前指针的原始 Qt 光标对象。

    少数控件（说明书面板）需要把同一个光标对象既当命中测试的输入，又当
    `QWidget` 的坐标参数；`pointer_position()` 返回核心 `Point` 会丢掉这层身份。
    挡位 A 的规则不变：Qt 事实仍只在本模块这一处落点被取出。
    """
    from lib.core.render.backends.qt.drawing.window import pointer_cursor as cursor

    return cursor()


def screen_rect_for_cursor(cursor, fallback_widget=None):
    """光标对象所在屏幕的核心 `Rect`（不是 `QRect`）。"""
    return screen_rect_for_point(point=cursor, fallback_widget=fallback_widget)


def ui_font(size: int | None = None):
    """当前后端的 UI 字体对象（Qt 下是 `QFont`）。"""
    provider = get_font_provider()
    if provider is not None:
        return provider.ui_font(size)
    from lib.core.render.backends.qt.runtime.font import get_ui_font

    return get_ui_font(size)


def digit_font(size: int | None = None):
    """数字与拉丁字形字体对象。"""
    provider = get_font_provider()
    if provider is not None:
        return provider.digit_font(size)
    from lib.core.render.backends.qt.runtime.font import get_digit_font

    return get_digit_font(size)


def cmd_font(size: int | None = None):
    """命令框字体对象。"""
    provider = get_font_provider()
    if provider is not None:
        return provider.cmd_font(size)
    from lib.core.render.backends.qt.runtime.font import get_cmd_font

    return get_cmd_font(size)


def ui_font_family() -> str:
    """已注册的 UI 字体族名。"""
    provider = get_font_provider()
    if provider is not None:
        return provider.ui_font_family()
    from lib.core.render.backends.qt.runtime.font import get_ui_font_family

    return get_ui_font_family()


def apply_ui_font_tree(widget) -> None:
    """把已注册的 UI 字体族刷到整棵控件树上。"""
    provider = get_font_provider()
    if provider is not None:
        provider.apply_ui_font_tree(widget)
        return
    from lib.core.render.backends.qt.runtime.font import apply_ui_font_tree as apply

    apply(widget)


def text_metrics(default_font, digit_font=None, *, side_font=None):
    """按后端自己的字体对象构造文本度量。

    presenter 决定换行、省略与基线，后端只提供推进量与该文本实际使用的基线高度。
    """
    factory = get_text_metrics_factory()
    if factory is not None:
        return factory(default_font, digit_font, side_font=side_font)
    from lib.core.render.backends.qt.drawing.text_metrics import QtTextMetrics

    return QtTextMetrics(default_font, digit_font, side_font=side_font)
