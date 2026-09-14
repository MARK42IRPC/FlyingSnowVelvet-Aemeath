"""五档推理强度滑条控件（办公页）。

档位名紧挨在滑条左侧（与「推理强度」标签同一行），不再悬浮在滑条上方；
已达到的填色段里铺一层星点当星空：星点位置由固定种子生成、按整条轨道的比例摆放，
所以换档位时星图不动，只是填色变长、多露出几颗。
"""

from __future__ import annotations

import random

from PyQt5.QtCore import QPointF, QRectF, QSignalBlocker, Qt, pyqtSignal
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

#: 星点数量与随机种子：固定种子保证每次重绘、每个档位的星图都一样。
EFFORT_STAR_COUNT = 22
EFFORT_STAR_SEED = 0x5A17


def effort_stars() -> tuple[tuple[float, float, float, int], ...]:
    """星点表 `(沿轨道比例, 相对中线偏移, 半径, 透明度)`；比例按整条轨道算，星点不随档位移动。"""
    rng = random.Random(EFFORT_STAR_SEED)
    return tuple(
        (
            rng.random(),
            rng.uniform(-1.0, 1.0),
            rng.uniform(0.7, 1.5),
            rng.randint(150, 245),
        )
        for _ in range(EFFORT_STAR_COUNT)
    )


EFFORT_STARS = effort_stars()


def star_dots(
    left: float, width: float, edge: float, middle: float, spread: float
) -> tuple[tuple[float, float, float, int], ...]:
    """把星点表换算成轨道坐标 `(x, y, 半径, 透明度)`。

    横坐标只由整条轨道（`left`/`width`）与固定种子决定，和当前档位无关；`edge` 是填色段
    右界，落在它右边的星点属于未填色区，不画。
    """
    unit = float(scale_px(1, min_abs=1))
    dots = []
    for ratio, drift, radius, alpha in EFFORT_STARS:
        x = left + ratio * width
        if x >= edge:
            continue
        dots.append((x, middle + drift * spread, max(1.0, unit * radius), alpha))
    return tuple(dots)


class _LevelSlider(QSlider):
    """不消费滚轮、按档位色自绘刻度与星空的水平滑条。

    QSS 覆盖过 groove/handle 之后原生刻度不再绘制，所以五档刻度在这里补：
    已达档位用该档的档位色，未达档位用分隔线色，滑条本身即一段淡粉到青的进度。
    已达段的填色里再叠一层白色星点，在档位色上做出星空质感。
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        self._paint_stars(painter, option, handle, span)
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

    def _paint_stars(self, painter: QPainter, option: QStyleOptionSlider, handle: int, span: int) -> None:
        """把星点画进已达档位的填色段：星点按整条轨道比例定位，档位越高露出越多。"""
        groove = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self
        )
        if groove.width() <= 0 or groove.height() <= 0:
            return
        offset = QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), self.value(), span
        )
        # 填色段（QSS 的 sub-page）从槽左端铺到滑块中心，星点越过这条界就不画。
        edge = groove.left() + handle / 2.0 + offset
        center = groove.center()
        # 星点连同光晕都裁在槽内，免得糊到轨道外面像脏点。
        painter.setClipRect(groove)
        for x, y, dot, alpha in star_dots(
            groove.left(),
            groove.width(),
            edge,
            center.y(),
            max(1.0, groove.height() / 2.0 - 2.0),
        ):
            # 外圈淡光晕 + 内核亮点，让星点在档位色上也能看出来。
            painter.setBrush(QColor(255, 255, 255, alpha // 4))
            painter.drawEllipse(QPointF(x, y), dot * 1.7, dot * 1.7)
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(QPointF(x, y), dot, dot)
        painter.setClipping(False)


class OfficeEffortSlider(QWidget):
    """推理强度五档滑条：滑条左侧显示当前档位文字，颜色随强度由淡粉渐变到青。"""

    effort_changed = pyqtSignal(str)

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
