"""留言墙的颜色选择控件：色相滑条 + 明度滑条。

留言卡片的正文可以自选颜色，选法只有两个滑条——色相（0–359°）与明度（0–100%）。
刻意不做取色盘：留言墙是拿来看短句的，色相 + 明度两条就能覆盖绝大部分需求，而且滑条
天然带「现在是哪个颜色」的实时预览，比拖一个二维方块更容易用对。

控件本身是纯 Qt 绘制，但颜色换算（HSL → RGB、预设色档）放在 `forum_style` 之外的
纯函数里，DX 侧要复用时不需要改这里。滑条外观复用主宠物同款的三层滑条
（`build_slider_visual`），数值不做颗粒吸附。
"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter
from PyQt5.QtWidgets import QWidget

from config.scale import scale_px
from lib.core.graphics.media_panel_visuals import build_slider_visual, slider_ratio_at
from lib.core.layer import Layer
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.script.ui.forum_style import forum_picker_track_color
#: 滑条高度与圆角：比留言卡片正文更细，作为取色的辅助控件不抢视线。
SLIDER_HEIGHT = scale_px(14, min_abs=12)
#: 明度下限不是 0：全黑在深色主题上等于隐形，留一点可见度。
MIN_LIGHTNESS = 0.18
MAX_LIGHTNESS = 1.0


def hsl_color(hue: float, lightness: float, *, saturation: float = 0.72) -> QColor:
    """把色相（0–360）与明度（0–1）换算成 QColor；饱和度固定，避免选出一堆灰。"""
    color = QColor()
    color.setHslF(
        max(0.0, min(1.0, float(hue) / 360.0)),
        max(0.0, min(1.0, float(saturation))),
        max(MIN_LIGHTNESS, min(MAX_LIGHTNESS, float(lightness))),
    )
    return color


def color_to_hsl(color: str | QColor) -> tuple[float, float]:
    """把颜色拆回（色相 0–360, 明度 0–1），供滑条初始化。"""
    value = QColor(color) if not isinstance(color, QColor) else QColor(color)
    if not value.isValid():
        return 0.0, 1.0
    hue, _saturation, lightness, _alpha = value.getHslF()
    return max(0.0, float(hue)) * 360.0, max(MIN_LIGHTNESS, min(MAX_LIGHTNESS, float(lightness)))


class ForumColorSlider(QWidget):
    """单条横向滑条：拖动发布 ``valueChanged``，比例范围 0.0–1.0。

    `gradient_stops` 给出轨道上的颜色渐变；为 None 时用主题中性色填充。
    """

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        *,
        tooltip: str,
        gradient_stops=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ForumColorSlider")
        self.setFixedHeight(SLIDER_HEIGHT)
        self.setMinimumWidth(scale_px(96, min_abs=80))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._ratio = 0.0
        self._dragging = False
        self._gradient_stops = gradient_stops

    # ── 状态 ─────────────────────────────────────────────────────────

    @property
    def ratio(self) -> float:
        return self._ratio

    def set_ratio(self, ratio: float, *, emit: bool = False) -> None:
        clamped = max(0.0, min(1.0, float(ratio)))
        if abs(clamped - self._ratio) < 1e-9:
            return
        self._ratio = clamped
        self.update()
        if emit:
            self.valueChanged.emit(self._ratio)

    def ratio_from_x(self, x: float) -> float:
        return slider_ratio_at(self.track_rect(), x)

    def set_gradient_stops(self, stops) -> None:
        """换轨道渐变（明度条会跟着色相重铺）；传 None 回到主题中性色。"""
        self._gradient_stops = stops
        self.update()

    def track_rect(self) -> QRect:
        return QRect(*_track_geometry(self.width(), self.height()))

    # ── 交互 ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._dragging = True
        try:
            self.grabMouse()
        except RuntimeError:
            pass
        self.set_ratio(self.ratio_from_x(event.x()), emit=True)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.set_ratio(self.ratio_from_x(event.x()), emit=True)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._dragging = False
        try:
            if self.mouseGrabber() is self:
                self.releaseMouse()
        except RuntimeError:
            pass
        self.set_ratio(self.ratio_from_x(event.x()), emit=True)
        super().mouseReleaseEvent(event)

    # ── 绘制 ─────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        visual = build_slider_visual(
            ratio=self._ratio,
            width=self.width(),
            height=self.height(),
            ticks=0,
            layer=int(Layer.PANEL),
        )
        QtDrawBackend().render(visual.batch, painter)
        self._paint_gradient(painter, visual.track_rect)
        painter.end()

    def _paint_gradient(self, painter: QPainter, track) -> None:
        """在轨道上铺一层颜色渐变，再把已选部分之外压暗，滑条自己就是取色预览。"""
        rect = QRect(int(track.x), int(track.y), int(track.width), int(track.height))
        if rect.width() <= 0 or rect.height() <= 0:
            return
        stops = self._gradient_stops
        if stops:
            gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
            for position, color in stops:
                gradient.setColorAt(max(0.0, min(1.0, float(position))), QColor(color))
        else:
            base = QColor(forum_picker_track_color())
            gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
            gradient.setColorAt(0.0, base.darker(140))
            gradient.setColorAt(1.0, base.lighter(120))
        painter.fillRect(rect, gradient)
        # 未选中的部分罩一层半透明黑，选中区间因此自带高亮。
        filled = int(round(self._ratio * rect.width()))
        remaining = rect.width() - filled
        if remaining > 0:
            painter.fillRect(
                QRect(rect.x() + filled, rect.y(), remaining, rect.height()),
                QColor(0, 0, 0, 96),
            )


def _track_geometry(width: int, height: int) -> tuple[int, int, int, int]:
    """滑条内部轨道矩形（去掉三层外框）。"""
    track = slider_track_rect(width=width, height=height)
    return track.x, track.y, track.width, track.height


def hue_gradient_stops():
    """色相滑条的渐变停靠点：6 段 60° 覆盖整圈。"""
    return tuple(
        (index / 6.0, hsl_color(index * 60.0, 0.62).name())
        for index in range(7)
    )


__all__ = [
    "ForumColorSlider",
    "MAX_LIGHTNESS",
    "MIN_LIGHTNESS",
    "SLIDER_HEIGHT",
    "color_to_hsl",
    "hsl_color",
    "hue_gradient_stops",
]
