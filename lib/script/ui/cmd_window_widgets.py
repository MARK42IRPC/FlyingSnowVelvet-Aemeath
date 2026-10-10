"""CMD 窗口的共享助手与自绘小件：ANSI 清洗、边缘命中与无边框标题按钮。

批次 3 后续轮次从 `cmd_window` 切出。CMD 窗口本体（`CmdWindow`）是一个大控件，里面夹着
一组**不依赖窗口状态**的助手与小件：ANSI 转义清洗、无边框拖拽的边缘光标判定、两个投递
后台输出的自定义 `QEvent`，以及标题栏按钮 `_TitleButton` / `_CloseButton`（`QPainter`
自绘叉号）。

切分保持逐行等价：搬出的函数与类与搬出前一致（缩进也未变）。`cmd_window` 按原名重新导出
`_hex` / `_strip_ansi` / `_hit_edge` / `_EDGE_CURSORS` / `_StreamLineEvent` /
`_StreamDoneEvent` / `_TitleButton` / `_CloseButton`，`CmdWindow` 本体的调用点零改动。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

import re

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QPushButton

from config.scale import scale_px
from lib.script.ui.render_bridge import qt_color, ui_font as get_ui_font

# 工具函数
# ---------------------------------------------------------------------------

def _hex(color: QColor) -> str:
    return color.name()


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b.')


def _strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列（颜色、光标移动等）。"""
    return _ANSI_RE.sub('', text)


# 边缘拖拽缩放参数
_EDGE = scale_px(6)

# 边缘 → 光标映射
_EDGE_CURSORS = {
    'l':  Qt.SizeHorCursor,
    'r':  Qt.SizeHorCursor,
    'b':  Qt.SizeVerCursor,
    'bl': Qt.SizeBDiagCursor,
    'br': Qt.SizeFDiagCursor,
}


def _hit_edge(pos: QPoint, w: int, h: int) -> str | None:
    """返回鼠标命中的边缘方向（不含顶部，由标题栏拖拽处理）。"""
    x, y = pos.x(), pos.y()
    e = _EDGE
    left   = x < e
    right  = x > w - e
    bottom = y > h - e
    if bottom and left:  return 'bl'
    if bottom and right: return 'br'
    if left:             return 'l'
    if right:            return 'r'
    if bottom:           return 'b'
    return None


# ---------------------------------------------------------------------------
# 自定义 QEvent 子类（线程→主线程安全传递）
# ---------------------------------------------------------------------------


class _StreamLineEvent(QEvent):
    """后台线程每读取一行输出就投递此事件。"""
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, line: str):
        super().__init__(_StreamLineEvent._TYPE)
        self.line = line


class _StreamDoneEvent(QEvent):
    """命令执行完毕（或失败）时投递此事件。"""
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, success: bool, msg: str = ''):
        super().__init__(_StreamDoneEvent._TYPE)
        self.success = success
        self.msg = msg


# ---------------------------------------------------------------------------
# 标题栏无边框按钮
# ---------------------------------------------------------------------------

class _TitleButton(QPushButton):
    """标题栏小按钮：无边框，hover 变色，黑色边框，支持自定义字体。"""

    def __init__(self, text: str, hover_bg: QColor, parent=None, custom_font=None):
        super().__init__(text, parent)
        self._hover_bg   = hover_bg
        self._normal_bg  = qt_color('pink')
        self._hovered    = False
        font = custom_font if custom_font is not None else get_ui_font(size=scale_px(9))
        self.setFont(font)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(scale_px(28), scale_px(18))
        self.setFocusPolicy(Qt.NoFocus)
        self._refresh_style(False)

    def _refresh_style(self, hovered: bool):
        bg     = _hex(self._hover_bg) if hovered else _hex(self._normal_bg)
        border = qt_color('black').name()          # ← 黑色边框
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background: {bg};"
            f"  color: {qt_color('black').name()};"
            f"  border: {scale_px(1, min_abs=1)}px solid {border};"
            f"  padding: 0px;"
            f"}}"
        )

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_style(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_style(False)
        super().leaveEvent(event)


# ---------------------------------------------------------------------------
# 绘制型关闭按钮（QPainter 对角线 × 符号，比字符更粗醒目）
# ---------------------------------------------------------------------------

class _CloseButton(_TitleButton):
    """关闭按钮：覆盖 paintEvent，用 QPainter 粗线绘制 × 号。"""

    def __init__(self, hover_bg: QColor, parent=None):
        super().__init__('', hover_bg, parent)     # 文本为空，完全靠绘制
        self.setFixedSize(scale_px(22), scale_px(18))

    def paintEvent(self, event):
        super().paintEvent(event)                  # 先画背景 + 黑色边框
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen_w = max(2, scale_px(2, min_abs=2))
        pen = QPen(qt_color('black'), pen_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        m = max(5, scale_px(5, min_abs=5))
        r = self.rect().adjusted(m, m, -m, -m)
        painter.drawLine(r.topLeft(), r.bottomRight())
        painter.drawLine(r.topRight(), r.bottomLeft())
        painter.end()


__all__ = [
    "_CloseButton",
    "_EDGE_CURSORS",
    "_StreamDoneEvent",
    "_StreamLineEvent",
    "_TitleButton",
    "_hex",
    "_hit_edge",
    "_strip_ansi",
]
