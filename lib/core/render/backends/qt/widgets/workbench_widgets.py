"""工作台自绘件（档位 D 宿主）：淡入淡出遮罩与明暗主题开关。

`lib/script/ui/workbench_widgets.py` 里的这两个自绘小件只依赖尺寸助手与工作台主题色，
不看窗口状态，因此从产品侧下沉到 toolkit 宿主，成为 `lib/script/ui` 可以共享的基类。

**为什么取色改读 `workbench_tokens`：** 档位 D 位于 `lib/core/render/` 下，而
`test_render_layer_never_imports_product_modules` 禁止 render 层 import `lib.script`，
所以这里不能再调 `lib.script.workbench.theme.get_workbench_colors()`。改读
`lib.core.render.visuals.workbench_tokens` 的 token——`lib/script/workbench/theme.py` 的
`DARK_COLORS` / `LIGHT_COLORS` 正是用同一份 token 构造的（`WorkbenchColors(**TOKENS)`），
hex 值与模式解析逐值相同，`tests/test_workbench_overlay_host.py` 钉住这条等价。

产品侧 `lib/script/ui/workbench_widgets.py` 保留原名再导出，`workbench_window.py` 的
调用点零改动。

本模块 `import PyQt5`；它位于 `lib/core/render/backends/qt/` 内，是该目录既有许可的一部分，
不入 `frozen_ui_qt_importers`（该清单只统计 `lib/script/ui/`）。
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QCheckBox, QWidget

from config.scale import scale_px


def _workbench_palette(mode: str | None = None) -> dict[str, str]:
    """当前（或指定模式）工作台主题的 hex 调色板，与产品侧 `WorkbenchColors` 同源。"""

    from lib.core.render.visuals.workbench_tokens import get_workbench_tokens

    return get_workbench_tokens(mode)


class _WorkbenchFadeOverlay(QWidget):
    """Paint-only fade cover that does not alter child widget composition."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._opacity = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_opacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, _event) -> None:
        if self._opacity <= 0.0:
            return
        painter = QPainter(self)
        color = QColor(_workbench_palette()["canvas"])
        color.setAlphaF(self._opacity)
        painter.fillRect(self.rect(), color)


class _WorkbenchThemeToggle(QCheckBox):
    """紧凑的粉青色明暗主题开关。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkbenchThemeToggle")
        self.setText("")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(scale_px(52, min_abs=46), scale_px(28, min_abs=25))
        self.setAccessibleName("工作台明暗主题")

    def hitButton(self, pos: QPoint) -> bool:
        """让自绘轨道的整个区域都能响应点击。"""
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        colors = _workbench_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        track = self.rect().adjusted(
            scale_px(1, min_abs=1),
            scale_px(4, min_abs=3),
            -scale_px(1, min_abs=1),
            -scale_px(4, min_abs=3),
        )
        radius = track.height() / 2.0
        active = self.isChecked()
        painter.setPen(QColor(colors["cyan"] if active else colors["border_strong"]))
        painter.setBrush(QColor(colors["pink"] if active else colors["surface_raised"]))
        painter.drawRoundedRect(track, radius, radius)

        knob_diameter = max(scale_px(16, min_abs=14), track.height() - scale_px(4, min_abs=3))
        knob_x = (
            track.right() - knob_diameter - scale_px(2, min_abs=1)
            if active
            else track.left() + scale_px(2, min_abs=1)
        )
        knob_y = track.center().y() - knob_diameter / 2.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colors["cyan"] if active else colors["pink"]))
        painter.drawEllipse(int(knob_x), int(knob_y), knob_diameter, knob_diameter)

        # The switch is self-painted; a Qt focus frame would look like an
        # extra border and flash during rapid toggles.


__all__ = [
    "_WorkbenchFadeOverlay",
    "_WorkbenchThemeToggle",
]
