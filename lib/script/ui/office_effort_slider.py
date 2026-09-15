"""五档推理强度滑条控件（办公页）。

档位名紧挨在滑条左侧（与「推理强度」标签同一行），不再悬浮在滑条上方；
按住滑块本体时向外发 `handle_pressed` / `handle_released`，由办公页据此在每个逻辑 tick
从滑块位置召唤星空粒子（见 `lib/script/practical/star_streak_particle.py`）。
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, QRectF, QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)

from config.scale import scale_px
from lib.script.office.contracts import DEFAULT_REASONING_EFFORT, REASONING_EFFORTS
from lib.script.ui.office_style import office_effort_colors
from lib.script.workbench.theme import get_workbench_colors


# 档位文案与 `REASONING_EFFORTS` 一一对应，顺序即强度顺序：极速 → 沉思。
EFFORT_LABELS = ("极速", "轻量", "一般", "思考", "沉思")
EFFORT_LEVELS = tuple(zip(REASONING_EFFORTS, EFFORT_LABELS))
EFFORT_TICK_COUNT = len(EFFORT_LEVELS)


class _LevelSlider(QSlider):
    """不消费滚轮、按档位色自绘五档刻度的水平滑条。

    QSS 覆盖过 groove/handle 之后原生刻度不再绘制，所以五档刻度在这里补：
    已达档位用该档的档位色，未达档位用分隔线色，滑条本身即一段淡粉到青的进度。
    """

    handle_pressed = pyqtSignal()
    handle_released = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setTickPosition(QSlider.NoTicks)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.handle_pressed.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.handle_released.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        colors = office_effort_colors()
        if len(colors) != EFFORT_TICK_COUNT:
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().pixelMetric(QStyle.PM_SliderLength, option, self)
        span = max(1, self.width() - handle)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        size = scale_px(3, min_abs=3)
        center_y = self.rect().center().y() + scale_px(8, min_abs=7)
        for index in range(self.minimum(), self.maximum() + 1):
            offset = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), index, span)
            x = offset + handle / 2.0
            reached = index <= self.value()
            color = QColor(colors[index - self.minimum()]) if reached else QColor(get_workbench_colors().border_strong)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x - size / 2.0, center_y, size, size), size / 2.0, size / 2.0)
        painter.end()


class OfficeEffortSlider(QWidget):
    """推理强度五档滑条：滑条左侧显示当前档位文字，颜色随强度由淡粉渐变到青。"""

    effort_changed = pyqtSignal(str)
    #: 按住 / 松开滑块：办公页据此决定是否持续召唤星空粒子。
    handle_pressed = pyqtSignal()
    handle_released = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OfficeEffortField")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(6, min_abs=4))

        # 档位名和滑条同一行：名字贴左边，滑条跟在后面，不再浮在滑条上方。
        # 五个档位名都是两个字、宽度一致，换档时滑条不会左右挪。
        self._level_label = QLabel("", self)
        self._level_label.setObjectName("OfficeEffortLevel")
        self._level_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._level_label, 0, Qt.AlignVCenter)

        self._slider = _LevelSlider(self)
        self._slider.setObjectName("OfficeEffortSlider")
        self._slider.setRange(0, EFFORT_TICK_COUNT - 1)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setTickInterval(1)
        self._slider.setFixedWidth(scale_px(200, min_abs=180))
        self._slider.setFocusPolicy(Qt.StrongFocus)
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.handle_pressed.connect(self.handle_pressed)
        self._slider.handle_released.connect(self.handle_released)
        layout.addWidget(self._slider, 0, Qt.AlignVCenter)
        layout.addStretch(1)

        self.set_effort(DEFAULT_REASONING_EFFORT)

    @property
    def levels(self) -> tuple[tuple[str, str], ...]:
        return EFFORT_LEVELS

    def effort(self) -> str:
        return EFFORT_LEVELS[self._slider.value()][0]

    def level_name(self) -> str:
        return EFFORT_LEVELS[self._slider.value()][1]

    def handle_center(self) -> QPoint:
        """当前滑块把手的屏幕中心：星空粒子从这里召唤。"""
        slider = self._slider
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        handle = slider.style().pixelMetric(QStyle.PM_SliderLength, option, slider)
        span = max(1, slider.width() - handle)
        offset = QStyle.sliderPositionFromValue(
            slider.minimum(), slider.maximum(), slider.value(), span
        )
        groove = slider.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderGroove, slider
        )
        center_y = groove.center().y() if groove.height() > 0 else slider.height() // 2
        return slider.mapToGlobal(QPoint(int(round(offset + handle / 2.0)), center_y))

    def set_effort(self, effort: str) -> None:
        """按后端档位值同步滑条，不发出 `effort_changed`。"""
        text = str(effort or "").strip().lower()
        values = [value for value, _label in EFFORT_LEVELS]
        index = values.index(text) if text in values else values.index(DEFAULT_REASONING_EFFORT)
        blocker = QSignalBlocker(self._slider)
        self._slider.setValue(index)
        del blocker
        self._apply_level(index)

    def _on_value_changed(self, index: int) -> None:
        self._apply_level(index)
        self.effort_changed.emit(self.effort())

    def _apply_level(self, index: int) -> None:
        label = EFFORT_LEVELS[index][1]
        self._level_label.setText(label)
        self._level_label.setToolTip("推理强度：%s（第 %d/%d 档）" % (label, index + 1, len(EFFORT_LEVELS)))
        self._slider.setToolTip(self._level_label.toolTip())
        for widget in (self, self._level_label, self._slider):
            widget.setProperty("level", str(index))
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.update()


def effort_level_color(effort: str, mode: str | None = None) -> str:
    """返回某个档位对应的档位色（供测试与预览使用）。"""
    values = [value for value, _label in EFFORT_LEVELS]
    index = values.index(effort) if effort in values else values.index(DEFAULT_REASONING_EFFORT)
    return office_effort_colors(mode)[index]
