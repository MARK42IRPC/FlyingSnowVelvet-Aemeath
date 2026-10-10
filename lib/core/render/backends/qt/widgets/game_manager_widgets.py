"""游戏管理窗口的卡片控件（档位 D 宿主）：一张已安装游戏的卡片。

`lib/script/ui/game_manager_widgets.py` 的 `_GameCardWidget` 是纯 Qt 控件：`QFrame` 子类，
只做「把一条记录铺进 QLabel/QVBoxLayout、选中的样式重打磨」这类控件工具包事实。
因此从产品侧下沉到 toolkit 宿主。

**唯一必要改动是业务记录入口**：档位 D 位于 `lib/core/render/` 下，而
`test_render_layer_never_imports_product_modules` 禁止 render 层 import `lib.script`，
所以这里不能再 `from lib.script.gemes.MAIN.game_packages import InstalledGame`。构造签名改为
`(model, ...)`——宿主只读 `manifest.official` / `manifest.name` / `manifest.version` /
`manifest.game_id` / `manifest.summary` 与两个扩展元组，不 import 记录类；原来的类型标注落在
产品侧垫片（它把 `_GameCardWidget` 包一层，保留注入的 `font_factory` 与原有签名）。
类的其余部分逐行搬入，控件树、字体档位与选中样式逻辑未改一个字。

字体取用入口与 `forum_images` / `bug_tracker_widgets` 同形：默认走档位 B 的字体提供者，
产品面用 `configure_font_factory()` 注入同一个提供者。

产品侧 `lib/script/ui/game_manager_widgets.py` 不再是 Qt 实现、不再 `import PyQt5`，
冻结清单里不再有它；`game_manager_window.py` 的导入面与调用点零改动。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from config.scale import scale_px

#: 字体取用入口，类型是 `(size: int | None) -> QFont`；`configure_font_factory()` 可覆盖它。
#: 默认值是档位 B 的字体服务（`runtime/providers.py` 的 `QtFontProvider`），构造第一个控件时
#: 才去注册表取；`lib/script/ui/render_bridge.py` 的 `ui_font()` 取的是同一个提供者。
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
    """安装卡片字体取用入口（档位 D 的装配点，`lib/script/ui/game_manager_widgets.py` 用它注入）。"""
    global _font_factory
    _font_factory = factory


def _ui_font(size: int | None = None):
    factory = _font_factory or _default_font_factory()
    return factory(size)


class _GameCardWidget(QFrame):
    def __init__(self, model, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        record = model

        self.setObjectName("GameCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("selected", False)

        title = QLabel(record.manifest.name, self)
        title_font = _ui_font(size=scale_px(14, min_abs=12))
        title_font.setBold(True)
        title.setFont(title_font)
        title.setObjectName("GameCardTitle")

        badge = QLabel("官方示例" if record.manifest.official else "开发者包", self)
        badge_font = _ui_font(size=scale_px(10, min_abs=9))
        badge_font.setBold(True)
        badge.setFont(badge_font)
        badge.setAlignment(Qt.AlignCenter)
        badge.setObjectName("GameCardBadge")
        badge.setProperty("official", record.manifest.official)
        self._badge = badge

        meta = QLabel(f"v{record.manifest.version}   {record.manifest.game_id}", self)
        meta.setFont(_ui_font(size=scale_px(12, min_abs=10)))
        meta.setObjectName("GameCardMeta")

        summary = QLabel(record.manifest.summary or "暂无简介", self)
        summary.setFont(_ui_font(size=scale_px(12, min_abs=10)))
        summary.setWordWrap(True)
        summary.setObjectName("GameCardSummary")

        ext = QLabel(
            f"粒子 {len(record.manifest.particle_extensions)}  ·  特效 {len(record.manifest.effect_extensions)}",
            self,
        )
        ext.setFont(_ui_font(size=scale_px(11, min_abs=9)))
        ext.setObjectName("GameCardExt")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(scale_px(8, min_abs=6))
        top.addWidget(title, 1)
        top.addWidget(badge, 0, Qt.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(scale_px(12, min_abs=10), scale_px(10, min_abs=8), scale_px(12, min_abs=10), scale_px(10, min_abs=8))
        layout.setSpacing(scale_px(5, min_abs=4))
        layout.addLayout(top)
        layout.addWidget(meta)
        layout.addWidget(summary)
        layout.addWidget(ext)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.style().unpolish(self._badge)
        self.style().polish(self._badge)


__all__ = [
    "_GameCardWidget",
]
