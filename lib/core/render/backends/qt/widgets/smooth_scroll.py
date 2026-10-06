"""滚轮平滑滚动容器（档位 D）：把离散滚动步进变成短动画过渡。

设置页、帮助浮窗与论坛列表共用同一份滚动手感。它按 `lib/core/render` 的档位划分
属于「工具包事实」——它操作的是 `QScrollArea` 的滚动条与 `QPropertyAnimation`，
没有任何产品视觉决策——因此落在后端控件宿主里，产品面只按原路径取用。

本模块不 import `lib.script`。
"""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation
from PyQt5.QtWidgets import QScrollArea

from config.scale import scale_px

#: 滚轮一格对应的像素步长（无 `pixelDelta` 的鼠标）。
WHEEL_NOTCH_PX = scale_px(48, min_abs=36)
#: 滚动条的单击步进与翻页步进。
SCROLL_SINGLE_STEP_PX = scale_px(24, min_abs=18)
SCROLL_PAGE_STEP_PX = scale_px(120, min_abs=96)
#: 过渡动画时长区间与增量系数。
_DURATION_MIN_MS = 110
_DURATION_MAX_MS = 280
_DURATION_BASE_MS = 120
_DURATION_PER_PX = 0.45


class SmoothScrollArea(QScrollArea):
    """滚轮平滑滚动容器：把离散滚动步进变成短动画过渡，设置页共用同一份手感。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_target_value = 0
        self._wheel_pending_px = 0.0
        self._wheel_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._wheel_anim.setEasingCurve(QEasingCurve.OutQuart)
        self._wheel_anim.setDuration(160)
        bar = self.verticalScrollBar()
        bar.setSingleStep(SCROLL_SINGLE_STEP_PX)
        bar.setPageStep(SCROLL_PAGE_STEP_PX)
        bar.rangeChanged.connect(self._on_scroll_range_changed)

    def _on_scroll_range_changed(self, minimum: int, maximum: int) -> None:
        self._wheel_target_value = max(minimum, min(maximum, self._wheel_target_value))

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        if bar is None or bar.maximum() <= bar.minimum():
            super().wheelEvent(event)
            return

        if not event.pixelDelta().isNull():
            delta_px = float(event.pixelDelta().y())
        else:
            angle_y = int(event.angleDelta().y())
            if angle_y == 0:
                super().wheelEvent(event)
                return
            delta_px = float(angle_y) / 120.0 * float(WHEEL_NOTCH_PX)

        if abs(delta_px) < 1e-6:
            event.accept()
            return

        self._wheel_pending_px += delta_px
        scroll_delta = int(self._wheel_pending_px)
        if scroll_delta == 0:
            event.accept()
            return
        self._wheel_pending_px -= float(scroll_delta)

        current = int(bar.value())
        base = (
            self._wheel_target_value
            if self._wheel_anim.state() == QPropertyAnimation.Running
            else current
        )
        target = max(bar.minimum(), min(bar.maximum(), int(round(base - scroll_delta))))
        if target == current:
            self._wheel_pending_px = 0.0
            event.accept()
            return

        distance = abs(target - current)
        duration = max(
            _DURATION_MIN_MS,
            min(_DURATION_MAX_MS, int(_DURATION_BASE_MS + distance * _DURATION_PER_PX)),
        )

        self._wheel_target_value = target
        self._wheel_anim.stop()
        self._wheel_anim.setDuration(duration)
        self._wheel_anim.setStartValue(current)
        self._wheel_anim.setEndValue(target)
        self._wheel_anim.start()
        event.accept()


__all__ = [
    "SCROLL_PAGE_STEP_PX",
    "SCROLL_SINGLE_STEP_PX",
    "WHEEL_NOTCH_PX",
    "SmoothScrollArea",
]
