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
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from config.scale import scale_px
from lib.core.graphics.commands import DrawBatch
from lib.core.graphics.media_panel_visuals import (
    PROGRESS_PANEL_HEIGHT,
    build_slider_visual,
    slider_ratio_at,
    slider_track_rect,
)
from lib.core.graphics.panel_visuals import UI_THEME, slider_handle_commands
from lib.core.graphics.types import Rect
from lib.core.layer import Layer
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.script.ui.forum_style import forum_picker_track_color

#: 滑条高度直接取共享滑条（音乐进度条、音响音量条）的高度。三层外框和竖把手的比例都
#: 跟着这个高度走，取色滑条因此和播放进度条是同一套控件语言，而不是一条被压扁的细条。
SLIDER_HEIGHT = PROGRESS_PANEL_HEIGHT
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

    def track_rect(self) -> Rect:
        """轨道矩形，直接取共享滑条那份几何。

        返回后端的 ``Rect`` 而不是 ``QRect``：命中测试要把它交给 ``slider_ratio_at``，
        两者用同一份几何，绘制轨道和点击落点不会各算各的。
        """
        return slider_track_rect(width=self.width(), height=self.height())

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
        # 顺序要紧：先让共享滑条铺一遍外壳（外框与青 / 粉两层圈都由它保证和其它滑条一致），
        # 再把渐变盖进轨道，最后把竖把手重画到渐变之上。原来的顺序让渐变和压暗层埋掉了
        # 把手，滑条看上去像没有把手。
        backend = QtDrawBackend()
        backend.render(visual.batch, painter)
        self._paint_gradient(painter, visual.track_rect)
        self._paint_handle(painter, backend, visual.track_rect)
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
        # 一圈 1px 暗描边把渐变收进面板里，和外壳的黑 / 青两层圈是同一套做法。
        self._stroke_inside(painter, rect)

    def _paint_handle(self, painter: QPainter, backend: QtDrawBackend, track) -> None:
        """把共享滑条的竖把手重画在渐变之上，任何色相上都看得见当前位置。"""
        center = float(track.x) + self._ratio * float(track.width)
        commands, rect = slider_handle_commands(
            center,
            track,
            UI_THEME["deep_pink"],
            layer=int(Layer.PANEL),
        )
        backend.render(DrawBatch(tuple(commands)), painter)
        self._stroke_inside(
            painter,
            QRect(int(rect.x), int(rect.y), int(rect.width), int(rect.height)),
        )

    @staticmethod
    def _stroke_inside(painter: QPainter, rect: QRect) -> None:
        """沿矩形内侧描 1px 暗边；描在内侧所以不会溢到外框上。"""
        width = int(rect.width()) - 1
        height = int(rect.height()) - 1
        if width <= 0 or height <= 0:
            return
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(int(rect.x()), int(rect.y()), width, height)

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
