"""五档推理强度滑条控件（办公页）。"""

from __future__ import annotations

from PyQt5.QtCore import QRectF, QSignalBlocker, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QLabel, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget

from config.scale import scale_px
from lib.script.office.contracts import DEFAULT_REASONING_EFFORT, REASONING_EFFORTS
from lib.script.ui.office_style import office_effort_colors
from lib.script.workbench.theme import get_workbench_colors


# 档位文案与 `REASONING_EFFORTS` 一一对应，顺序即强度顺序：极速 → 沉思。
EFFORT_LABELS = ("极速", "轻量", "一般", "思考", "沉思")
EFFORT_LEVELS = tuple(zip(REASONING_EFFORTS, EFFORT_LABELS))
EFFORT_TICK_COUNT = len(EFFORT_LEVELS)


class _LevelSlider(QSlider):
    """不消费滚轮、按档位色自绘刻度的水平滑条。

    QSS 覆盖过 groove/handle 之后原生刻度不再绘制，所以五档刻度在这里补：
    已达档位用该档的档位色，未达档位用分隔线色，滑条本身即一段淡粉到青的进度。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setTickPosition(QSlider.NoTicks)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        colors = office_effort_colors()
        if len(colors) != EFFORT_TICK_COUNT:
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().pixelMetric(QStyle.PM_SliderLength, option, self)
        span = max(1, self.width() - handle)
        size = scale_px(3, min_abs=3)
        center_y = self.rect().center().y() + scale_px(8, min_abs=7)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        for index in range(self.minimum(), self.maximum() + 1):
            offset = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), index, span)
            x = offset + handle / 2.0
            reached = index <= self.value()
            color = QColor(colors[index - self.minimum()]) if reached else QColor(get_workbench_colors().border_strong)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x - size / 2.0, center_y, size, size), size / 2.0, size / 2.0)
        painter.end()


class OfficeEffortSlider(QWidget):
    """推理强度五档滑条：滑条上方显示当前档位文字，颜色随强度由淡粉渐变到青。"""

    effort_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OfficeEffortField")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(3, min_abs=2))

        self._level_label = QLabel("", self)
        self._level_label.setObjectName("OfficeEffortLevel")
        self._level_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._level_label)

        self._slider = _LevelSlider(self)
        self._slider.setObjectName("OfficeEffortSlider")
        self._slider.setRange(0, EFFORT_TICK_COUNT - 1)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setTickInterval(1)
        self._slider.setFixedWidth(scale_px(200, min_abs=180))
        self._slider.setFocusPolicy(Qt.StrongFocus)
        self._slider.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._slider, 0, Qt.AlignHCenter)

        self.set_effort(DEFAULT_REASONING_EFFORT)

    @property
    def levels(self) -> tuple[tuple[str, str], ...]:
        return EFFORT_LEVELS

    def effort(self) -> str:
        return EFFORT_LEVELS[self._slider.value()][0]

    def level_name(self) -> str:
        return EFFORT_LEVELS[self._slider.value()][1]

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
