"""论坛卡片正文的富文本控件：把 `forum_markup` 的片段排版出来。

为什么不用 QLabel：UI 字体只注册了 Bold 一个字面（`resc/FRONTS/HarmonyOS_Sans_SC_Bold.ttf`），
`<b>`、`font-weight` 和 QFont 的三种 weight 实测墨迹完全相同，粗体在 QLabel 里看不出区别；
QLabel 也不暴露内部 QTextDocument，没法给某个片段单独加笔画。这里改用只读
QTextEdit：既拿得到 QTextDocument（给加粗片段加一层同色细描边把笔画撑粗），又自带鼠标
选中复制。滚动条关掉、滚轮让给外层卡片墙，外观上仍是一块普通正文。

斜体、下划线、删除线不需要额外处理：Qt 会为没有斜体面的字体合成倾斜，`<u>` / `<s>` 本来
就是绘制期装饰。正文颜色写在字符格式里（粗体描边也要用同一个颜色），所以换主题时调用方要
重新 `set_color()`。
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import (
    QColor,
    QFont,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PyQt5.QtWidgets import QFrame, QSizePolicy, QTextEdit

#: 粗体描边的笔宽按字号取：字号越大笔画越粗，固定像素数在大字号下会显得没有加粗。
BOLD_OUTLINE_RATIO = 0.045
#: 再小的字号也要留一点描边，否则短句放大的卡片上看不出区别。
BOLD_OUTLINE_MIN_PX = 1.0


def bold_outline_width(font_size: int) -> float:
    """粗体描边的笔宽（px）：UI 字体只有 Bold 一个字面，靠同色描边把笔画撑粗。"""
    return max(BOLD_OUTLINE_MIN_PX, round(float(font_size) * BOLD_OUTLINE_RATIO, 2))


class MarkupText(QTextEdit):
    """只读富文本正文：居中、按宽度自适应高度，粗体带同色描边。"""

    def __init__(
        self,
        html: str,
        *,
        font: QFont,
        color: str,
        width_hint: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._bold_outline = bold_outline_width(font.pixelSize())
        #: 原始片段留着：量高要用一份临时文档重排一遍，不能拿现成文档反复改宽度。
        self._html = str(html or "")
        #: 没有布局宽度时（卡片刚建好、还没排版）按这个宽度估高。
        self._width_hint = max(1, int(width_hint))
        self.setObjectName("ForumCardText")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.viewport().setAutoFillBackground(False)
        self.setFont(font)

        document = self.document()
        document.setDocumentMargin(0)
        document.setDefaultFont(font)
        document.setHtml(self._html)
        self._restyle()
        self._sync_height()

    # ── 对外 ─────────────────────────────────────────────────────────

    def set_color(self, color: str) -> None:
        """换主题时重刷正文颜色（颜色写在字符格式里，样式表管不到）。"""
        self._color = QColor(color)
        self._restyle()
        self.update()

    def sizeHint(self) -> QSize:
        width = self._measure_width()
        return QSize(width, self._measured_height(width))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ── 绘制与自适应 ─────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:
        # 正文不滚动，滚轮交给外层卡片墙。
        event.ignore()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        width = self._measure_width()
        # 先让正文按真实列宽换行，再按同一个宽度量高，两边用的是同一套排版参数。
        self.document().setTextWidth(width)
        height = self._measured_height(width)
        if self.height() != height:
            self.setFixedHeight(height)

    def _measure_width(self) -> int:
        """量高用的宽度：排过版就用真实视口宽度，还没有宽度时退回卡片最小宽度估。"""
        width = self.viewport().width()
        return width if width > 0 else self._width_hint

    def _measured_height(self, width: int) -> int:
        """给定宽度下的正文高度。

        刻意用一份临时文档量：`document().setTextWidth()` 会把已经按真实列宽排好的正文改窄，
        排版跟着重算，卡片会裁掉最后一行（离屏渲染实测）。临时文档和正文用的 html、字体、
        宽度完全一样，差别只有 `_restyle()` 给的颜色与描边——两者都不参与排版尺寸。
        """
        measure = QTextDocument()
        measure.setDocumentMargin(0)
        measure.setDefaultFont(self.font())
        measure.setHtml(self._html)
        measure.setTextWidth(max(1, int(width)))
        return max(1, int(math.ceil(measure.size().height())))

    # ── 排版 ─────────────────────────────────────────────────────────

    def _restyle(self) -> None:
        """居中、统一正文颜色，并给加粗片段加同色描边。"""
        document = self.document()
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        block_format = QTextBlockFormat()
        block_format.setAlignment(Qt.AlignHCenter)
        cursor.mergeBlockFormat(block_format)
        # 只带前景色的空格式：`cursor.charFormat()` 会把光标处的字重一起交出来，整篇铺下去
        # 就变成「最后一段是粗体则全文都粗」——实测 `普通**粗**` 整条留言都会变粗。
        base_format = QTextCharFormat()
        base_format.setForeground(self._color)
        cursor.mergeCharFormat(base_format)

        block = document.begin()
        while block.isValid():
            fragment = block.begin()
            while not fragment.atEnd():
                piece = fragment.fragment()
                if piece.isValid() and piece.charFormat().fontWeight() >= QFont.Bold:
                    self._embolden(piece)
                fragment += 1
            block = block.next()

    def _embolden(self, piece) -> None:
        fmt = piece.charFormat()
        fmt.setForeground(self._color)
        fmt.setTextOutline(QPen(self._color, self._bold_outline))
        cursor = QTextCursor(self.document())
        cursor.setPosition(piece.position())
        cursor.setPosition(piece.position() + piece.length(), QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(fmt)


__all__ = ["BOLD_OUTLINE_MIN_PX", "BOLD_OUTLINE_RATIO", "MarkupText", "bold_outline_width"]
