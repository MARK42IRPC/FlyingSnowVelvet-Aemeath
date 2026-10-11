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


def install(*, ui_font=None, digit_font=None, draw_backend=None, color=None, color_name=None) -> None:
    """安装一批注入物；传 None 的项保持原值（便于分模块各自注入）。"""
    global _font_factory, _digit_font_factory, _draw_backend_factory, _color_factory, _color_name_factory
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
# - 屏幕几何（`screen_rect_for_point(point)`）：返回核心 `Rect`；
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


def screen_rect_for_point(point=None):
    """世界对象所在屏幕的核心 `Rect`；缺省按虚拟屏幕。"""
    if _screen_geometry_factory is not None:
        return _screen_geometry_factory(point)
    from lib.core.render.visuals.screen import virtual_screen_rect

    return virtual_screen_rect(())


def spawn_particle(x, y, particle_id) -> None:
    """在屏幕坐标点召唤一枚粒子；产品侧注入 `spawn_particle_at_point`。"""
    if _particle_spawn_factory is not None:
        _particle_spawn_factory(int(x), int(y), str(particle_id))
