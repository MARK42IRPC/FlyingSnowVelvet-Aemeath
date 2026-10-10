"""工作台自绘件：淡入淡出遮罩与明暗主题开关。

批次 3 后续轮次从 `workbench_window` 切出。工作台窗口本体（`WorkbenchWindow`）是一个大
控件，里面夹着两个**自包含**的自绘小件：淡入淡出遮罩 `_WorkbenchFadeOverlay`（只铺一层
半透明画布色，不改变子控件构成）与紧凑的粉青色明暗开关 `_WorkbenchThemeToggle`。它们的
绘制只依赖主题色与尺寸助手，不看窗口状态，因此先整块搬出来。

切分保持逐行等价：两个类的类体与搬出前一致（缩进也未变）。`workbench_window` 按原名
重新导出这两个名字，窗口本体的调用点零改动。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QCheckBox, QWidget

from config.scale import scale_px
from lib.script.workbench.theme import get_workbench_colors

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
        color = QColor(get_workbench_colors().canvas)
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
        colors = get_workbench_colors()
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
        painter.setPen(QColor(colors.cyan if active else colors.border_strong))
        painter.setBrush(QColor(colors.pink if active else colors.surface_raised))
        painter.drawRoundedRect(track, radius, radius)

        knob_diameter = max(scale_px(16, min_abs=14), track.height() - scale_px(4, min_abs=3))
        knob_x = (
            track.right() - knob_diameter - scale_px(2, min_abs=1)
            if active
            else track.left() + scale_px(2, min_abs=1)
        )
        knob_y = track.center().y() - knob_diameter / 2.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colors.cyan if active else colors.pink))
        painter.drawEllipse(int(knob_x), int(knob_y), knob_diameter, knob_diameter)

        # The switch is self-painted; a Qt focus frame would look like an
        # extra border and flash during rapid toggles.


__all__ = [
    "_WorkbenchFadeOverlay",
    "_WorkbenchThemeToggle",
]
