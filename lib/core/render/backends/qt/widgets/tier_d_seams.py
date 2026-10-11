"""档位 D 宿主共用的注入 seam：字体、绘制后端、主题色、文本度量。

档位 D（`backends/qt/widgets/`）不得静态引用档位 A（`drawing/`），也不得 import
`lib.script` 产品包。凡是需要这两类事实的宿主控件，都在这里拿一份**由产品垫片注入**的
可调用对象；未注入时给出与产品侧同源的缺省实现（字体走档位 B 提供者，颜色走后端中立的
palette + Qt 颜色转换）。

与 `workbench_settings_layout.configure_font_factory()` 同形，只是集中到一处，避免每个
宿主各写一遍 `_font_factory = None` 样板。产品垫片在导入时调用
`render_bridge.install_tier_d_seams(...)` 完成装配。
"""

from __future__ import annotations

_font_factory = None
_digit_font_factory = None
_draw_backend_factory = None
_color_factory = None
_color_name_factory = None
_text_metrics_factory = None


def install(*, ui_font=None, digit_font=None, draw_backend=None, color=None, color_name=None,
            text_metrics=None) -> None:
    """安装一批注入物；传 None 的项保持原值（便于分模块各自注入）。"""
    global _font_factory, _digit_font_factory, _draw_backend_factory, _color_factory, _color_name_factory
    global _text_metrics_factory
    if ui_font is not None:
        _font_factory = ui_font
    if digit_font is not None:
        _digit_font_factory = digit_font
    if draw_backend is not None:
        _draw_backend_factory = draw_backend
    if color is not None:
        _color_factory = color
    if color_name is not None:
        _color_name_factory = color_name
    if text_metrics is not None:
        _text_metrics_factory = text_metrics


def ui_font(size: int | None = None):
    """UI 字体：优先注入物，其次档位 B 字体提供者，最后 Qt 运行时字体表。"""
    if _font_factory is not None:
        return _font_factory(size)
    from lib.core.render.registry import get_font_provider

    provider = get_font_provider()
    if provider is not None:
        return provider.ui_font(size)
    from lib.core.render.backends.qt.runtime.font import get_ui_font

    return get_ui_font(size)


def text_metrics(default_font, digit_font=None, *, side_font=None):
    """文本度量：优先注入物，其次后端注册表，最后档位 A 的 Qt 实现。

    `TextMetrics` 是「字形推进量」这类绘制期事实；档位 D 不得静态引用档位 A，因此
    最终缺省实现按需惰性 import（与 `create_draw_backend` 同形，不是模块级静态引用）。
    """
    if _text_metrics_factory is None:
        raise RuntimeError("档位 D 宿主需要经 render_bridge 注入文本度量工厂")
    return _text_metrics_factory(default_font, digit_font, side_font=side_font)


def digit_font(size: int | None = None):
    """数字 / 拉丁字体：与 `ui_font` 同源的注入路径。"""
    if _digit_font_factory is not None:
        return _digit_font_factory(size)
    from lib.core.render.registry import get_font_provider

    provider = get_font_provider()
    if provider is not None:
        return provider.digit_font(size)
    from lib.core.render.backends.qt.runtime.font import get_digit_font

    return get_digit_font(size)


def create_draw_backend():
    """绘制实现：必须由产品侧注入（档位 D 不得静态引用档位 A）。"""
    if _draw_backend_factory is None:
        raise RuntimeError("档位 D 宿主需要经 render_bridge 注入绘制后端工厂")
    return _draw_backend_factory()


def color(token: str):
    """主题色 / 调色板 token 的 Qt 表示：由产品侧注入 `render_bridge.qt_color`。"""
    if _color_factory is None:
        raise RuntimeError("档位 D 宿主需要经 render_bridge 注入颜色转换入口")
    return _color_factory(token)


def color_name(token: str) -> str:
    """token 的 `#rrggbb` 文本，用于样式表拼装。"""
    if _color_name_factory is not None:
        return _color_name_factory(token)
    return color(token).name()

#: 产品侧钩子注册表：`name -> callable`。宿主（档位 D）不得 import `lib.script`，
#: 因此凡是「要调一个产品包函数 / 取一个产品包单例」的地方，都由产品垫片在导入时
#: 用 `register_hook()` 登记，宿主按名字调用。键名是稳定的契约字符串。
_hooks: dict = {}


def register_hook(name: str, factory) -> None:
    """登记一个产品侧可调用对象（档位 D 的装配点，产品垫片用它注入）。"""
    _hooks[name] = factory


def hook(name: str):
    """取一个已登记的产品侧可调用对象；未登记时返回 None（调用方据此走缺省路径）。"""
    return _hooks.get(name)


#: 动画目录枚举的缺省注入物：读 `config.config_animation` 的源值，不 import 产品包。
#: 产品垫片会经 `install_anim_folders()` 覆盖成 SEanima 的完整枚举（含实际存在的目录）。
def _default_animation_folder_choices() -> list[str]:
    from config.config_animation import ANIMATION as _ANIMATION_CONFIG

    names: list[str] = []
    for key in ("start_animation_folder", "exit_animation_folder"):
        name = str(_ANIMATION_CONFIG.get(key, "")).strip()
        if name and name not in names:
            names.append(name)
    return names


def _default_resolve_animation_folder_path(folder_name: str) -> str:
    import os

    here = os.path.abspath(__file__)
    # lib/core/render/backends/qt/widgets/ -> 上溯 6 级到仓库根。
    root = here
    for _ in range(7):
        root = os.path.dirname(root)
    anim_root = os.path.join(root, "resc", "GIF", "SEanima")
    normalized = str(folder_name or "").strip()
    return os.path.join(anim_root, normalized)


_folder_choices_factory = _default_animation_folder_choices
_resolve_folder_path_factory = _default_resolve_animation_folder_path


def install_anim_folders(*, choices=None, resolve_path=None) -> None:
    """注入动画目录枚举 / 路径解析（product 侧 SEanima 的实现；两者都是纯文件系统事实）。"""
    global _folder_choices_factory, _resolve_folder_path_factory
    if choices is not None:
        _folder_choices_factory = choices
    if resolve_path is not None:
        _resolve_folder_path_factory = resolve_path


def animation_folder_choices() -> list[str]:
    """可用的动画目录名（默认只含 config 里的两个默认目录）。"""
    return list(_folder_choices_factory())


def resolve_animation_folder_path(folder_name: str) -> str:
    """把一个动画目录名解析成硬盘路径（默认按仓库自算，产品侧覆盖为 SEanima 的实现）。"""
    return str(_resolve_folder_path_factory(str(folder_name)))

# ── 世界对象宿主共用的注入物 ───────────────────────────────────────────────
#
# 世界对象（clock / motor / sofa / snowball / snow_pile / snow_leopard / speaker）是
# QWidget，落在档位 D；它们需要的三件事都经这里注入：
#
# - 声音工厂（`world_object_sound(name)`）：产品侧 `lib.script.voice.*` 的 Sound 类；
# - 屏幕几何（`world_object_screen_rect(point)`）：返回核心 `Rect`；
# - 拖拽结束时召唤的粒子（`particle_spawn(x, y, particle_id)`）。
#
# 三者未注入时给出惰性缺省：声音退化为无声对象，屏幕几何按虚拟屏幕，粒子不动。
_particle_spawn_factory = None
_screen_geometry_factory = None


def install_world_object_seams(*, sound=None, screen_geometry=None, particle_spawn=None) -> None:
    """安装世界对象宿主所需的注入物；传 None 的项保持原值。"""
    global _particle_spawn_factory, _screen_geometry_factory
    if sound is not None:
        register_hook("world_object_sound", sound)
    if screen_geometry is not None:
        _screen_geometry_factory = screen_geometry
    if particle_spawn is not None:
        _particle_spawn_factory = particle_spawn


class _SilentSound:
    """缺省声音：任何世界对象在没有注入声音工厂时也能安全 `play()`。"""

    def play(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def world_object_sound(name: str):
    """按名字取一个世界对象声音；产品侧注入工厂，缺省为静音对象。"""
    factory = hook("world_object_sound")
    if factory is None:
        return _SilentSound()
    return factory(str(name))


def world_object_screen_rect(point=None):
    """世界对象所在屏幕的核心 `Rect`；缺省按虚拟屏幕。

    与右键 UI 层的 `screen_rect_for_point()` 区分：后者经呈现宿主解析（含 fallback 控件），
    世界对象只需要「点落在哪块屏」。产品侧 `lib/script/ui/world_objects/*` 注入的实现是
    `render_bridge.screen_rect_for_point`（后端中立屏幕 provider）。
    """
    if _screen_geometry_factory is not None:
        return _screen_geometry_factory(point)
    from lib.core.render.visuals.screen import virtual_screen_rect

    return virtual_screen_rect(())


def spawn_particle(x, y, particle_id) -> None:
    """在屏幕坐标点召唤一枚粒子；产品侧注入 `spawn_particle_at_point`。"""
    if _particle_spawn_factory is not None:
        _particle_spawn_factory(int(x), int(y), str(particle_id))

# ── 画布宿主：在调用方自己的 QPainter 上执行绘制批次 ─────────────────────────
_painter_host = None


def create_painter_host():
    """当前绘制批次宿主；绘制实现与字体缺省时用后端注入物。"""
    global _painter_host
    from lib.core.render.backends.qt.widgets.control_painter_host import QtPainterHost

    if _painter_host is None:
        _painter_host = QtPainterHost(
            draw_backend=create_draw_backend(),
            font_factory=ui_font,
        )
    return _painter_host


def painter_color(value):
    """边界颜色 → `QColor`。"""
    return create_painter_host().color(value)

# ── 呈现几何宿主（档位 B 能力）的取用 ───────────────────────────────────────
#
# 右键 UI 层需要「控件全局矩形 / 按屏幕坐标移动控件 / 所在屏幕矩形」这三件事，它们由后端
# 呈现宿主提供。宿主（档位 D）不得静态引用档位 A，因此经产品侧注入；缺省退回 Qt 实现。
_presentation_host = None


class _FallbackPresentationHost:
    """档位 D 的缺省呈现几何宿主（后端未注入且注册表为空时的兜底）。

    与档位 A 的 `drawing/presentation.py::QtPresentationHost` 同义，但落在档位 D 并复用
    档位 B（`runtime/screen.py`）的 Qt 查询，因此不构成对档位 A 的静态引用。正常运行时产品
    垫片总会经 `install_presentation_host()` 注入真正的宿主，这里只服务隔离构造的控件测试。
    """

    def screen_rect_for_widget(self, widget=None, point=None):
        from lib.core.render.backends.qt.runtime.screen import get_screen_rect_for_point

        return get_screen_rect_for_point(point=point, fallback_widget=widget)

    def widget_global_rect(self, widget):
        from lib.core.render.backends.qt.runtime.screen import widget_global_rect as _rect
        from lib.core.render.visuals.types import Rect

        rect = _rect(widget)
        return Rect(rect.x(), rect.y(), rect.width(), rect.height())

    def widget_global_point(self, widget, point):
        from lib.core.render.visuals.types import Point, coerce_point

        core = coerce_point(point) or Point()
        rect = self.widget_global_rect(widget)
        return Point(rect.x + core.x, rect.y + core.y)

    def move_widget_to_global(self, widget, x, y) -> None:
        from lib.core.render.backends.qt.runtime.screen import move_widget_to_global as _move

        _move(widget, int(x), int(y))


def install_presentation_host(host) -> None:
    """注入呈现几何宿主（产品侧 `render_bridge.presentation_host()`）。"""
    global _presentation_host
    _presentation_host = host


def presentation_host():
    """当前呈现几何宿主；先取注入物，再取后端注册表，最后退回档位 D 缺省实现。"""
    if _presentation_host is not None:
        return _presentation_host()
    from lib.core.render.registry import get_presentation_host

    host = get_presentation_host()
    if host is not None:
        return host
    return _FallbackPresentationHost()


def widget_global_rect(widget):
    """控件在屏幕坐标系中的核心 `Rect`。"""
    return presentation_host().widget_global_rect(widget)


def move_widget_to_global(widget, x: int, y: int) -> None:
    """按屏幕坐标移动控件；宿主分层时由后端换算成宿主本地坐标。"""
    presentation_host().move_widget_to_global(widget, int(x), int(y))


def screen_rect_for_point(point=None, fallback_widget=None):
    """控件所在屏幕的核心 `Rect`。"""
    rect = presentation_host().screen_rect_for_widget(fallback_widget, point=point)
    if rect is not None:
        return rect
    from lib.core.render.visuals.screen import virtual_screen_rect

    return virtual_screen_rect(())


def centered_placement(self_size, screen):
    """在 `screen` 里居中的窗口落位（档位 1 的共享解算）。"""
    from lib.core.render.visuals.layout import PlacementSpec

    return PlacementSpec().resolve_centered(self_size, screen)
