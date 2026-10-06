"""Qt 绘制宿主：把后端中立的 ``DrawBatch`` 画进调用方自己的 ``QPainter``。

属于控件工具包事实（档位 D）。产品控件本身就是 ``QWidget`` 子类，它的 ``paintEvent``
必须自己起一个 ``QPainter``；本模块只回答"这份批次怎么落到这个 painter 上"，以及把
边界两侧的颜色/矩形做一次类型转换。它不持有窗口、不解释指针、不知道任何产品形状。

档位 D 不得静态引用档位 A（``drawing/``），因此绘制实现（``DrawBackend``）与 UI 字体
都从外面注入——组合入口是 ``lib/script/ui/render_bridge.create_painter_host()``。
这样换后端时替换的是注入物，不是控件。
"""
from __future__ import annotations

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QColor, QPainter

from lib.core.render.visuals.commands import DrawBatch
from lib.core.render.visuals.types import Color, Rect


class QtPainterHost:
    """在调用方的 ``QPainter`` 上执行绘制批次，并把边界类型来回转换。

    ``draw_backend`` 与 ``font_factory`` 由 ``render_bridge`` 注入：本模块是档位 D，
    "怎么画"必须由外部给，不能在这里 import 档位 A。
    """

    def __init__(self, *, draw_backend, font_factory) -> None:
        self._draw_backend = draw_backend
        self._font_factory = font_factory

    # ── 绘制 ───────────────────────────────────────────────────────
    def render(self, batch: DrawBatch, painter: QPainter, viewport: Rect | None = None) -> None:
        """把一份后端中立批次画到 ``painter`` 上。"""
        self._draw_backend.render(batch, painter, viewport)

    # ── 类型转换 ───────────────────────────────────────────────────
    #: ``#rgb`` / ``#rrggbb`` / ``#rrggbbaa`` / ``#aarrggbb`` 文本。
    _HEX_PREFIX = "#"

    @classmethod
    def color(cls, value: object) -> QColor:
        """核心 ``Color`` / 通道元组 / ``#rrggbb`` 文本 / ``QColor`` → ``QColor``。

        返回新对象而不是共享实例：调用方普遍会就地改返回值，共享实例会让一处改动
        渗到别处。``#`` 开头按 CSS/Qt 的 ``setNamedColor`` 语义解析——工作台主题令牌
        给的就是这种文本，不能当成通道元组拆。
        """
        if isinstance(value, QColor):
            return QColor(value)
        if isinstance(value, str):
            text = value.strip()
            if not text.startswith(cls._HEX_PREFIX) or len(text) not in (4, 7, 9):
                raise ValueError(f"不支持的颜色文本: {value!r}")
            return QColor(text)
        if isinstance(value, Color):
            return QColor(value.red, value.green, value.blue, value.alpha)
        components = tuple(value)  # type: ignore[arg-type]
        alpha = components[3] if len(components) > 3 else 255
        return QColor(int(components[0]), int(components[1]), int(components[2]), int(alpha))

    @staticmethod
    def color_name(value: object) -> str:
        """颜色取 ``#rrggbb`` 文本，供 QSS 拼装。"""
        return QtPainterHost.color(value).name()

    @staticmethod
    def rect(value: object) -> Rect:
        """``QRect`` / 通道元组 / 核心 ``Rect`` → 核心 ``Rect``。"""
        if isinstance(value, Rect):
            return value
        if hasattr(value, "width"):
            return Rect(value.x(), value.y(), value.width(), value.height())
        return Rect(value[0], value[1], value[2], value[3])  # type: ignore[index]

    @staticmethod
    def qrect(value: object) -> QRect:
        """核心 ``Rect`` / 通道元组 / ``QRect`` → ``QRect``（``QPainter`` 要的对象）。"""
        if isinstance(value, QRect):
            return QRect(value)
        if isinstance(value, Rect):
            return QRect(int(value.x), int(value.y), int(value.width), int(value.height))
        if hasattr(value, "width"):
            return QRect(value.x(), value.y(), value.width(), value.height())
        return QRect(value[0], value[1], value[2], value[3])  # type: ignore[index]

    # ── 字体 ───────────────────────────────────────────────────────
    def font(self, size: int | None = None):
        """当前后端的 UI 字体对象（Qt 下是 ``QFont``）。"""
        return self._font_factory(size)


__all__ = ["QtPainterHost"]
