"""卡片底部的效果贴图：`[雪豹]` 这类令牌对应的 gif。

令牌本身是纯文本、由 `forum_markup` 定义并从正文里洗掉（`FORUM_EFFECT_TOKENS`），令牌到
gif 的对应关系长在这里：新增一个令牌必须同时补 `FORUM_STICKER_ASSETS`，两处成对，测试会核对。

gif 帧走后端无关的 `lib/core/graphics/image_loader.decode_image_frames` 解码，再按
（路径 + 目标尺寸）缓存成 `QImage`（`lru_cache`）——同一张 gif 的所有卡片共用一份，重排整墙
不会反复解码。动画由全局 `EventType.GIF_FRAME` 推进，和雪豹世界物体同一个时钟，不给每张贴图
各起一个 QTimer；贴图在卡片重排时跟着卡片 `deleteLater()` 一起销毁，销毁时退订事件中心，
否则事件中心会一直握着已经销毁的控件。

贴图尺寸固定（高 `STICKER_HEIGHT`，宽按 gif 自身比例），所以卡片量高直接用 `sizeHint()`，
不需要再走一遍富文本排版；文件缺失时是零尺寸的透明控件，卡片留空而不是整墙渲染失败。gif 自带
的透明留白会按全部帧的可见像素并集裁掉，贴图方块就是图案本身，`STICKER_HEIGHT` 说的是图案高度。
"""

from __future__ import annotations

from functools import lru_cache

from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import QBitmap, QImage, QPainter, QRegion
from PyQt5.QtWidgets import QSizePolicy, QWidget

from config.scale import scale_px
from lib.core.event.center import EventType, get_event_center
from lib.core.graphics.image_loader import decode_image_frames
from lib.core.logger import get_logger
from lib.core.qt_bridge.gif_loader import qimage_from_raster_frame
from lib.script.ui.forum_markup import effect_tokens


logger = get_logger(__name__)

#: 令牌 → 贴图路径（相对仓库根）。令牌集合是 `forum_markup.FORUM_EFFECT_TOKENS`，两边必须成对。
FORUM_STICKER_ASSETS = {
    "[雪豹]": "resc/GIF/snow_leopard.gif",
}

#: 贴图高度；宽度按 gif 比例算，卡片再窄也不会把贴图压扁。
STICKER_HEIGHT = scale_px(88, min_abs=72)


def sticker_paths(text) -> tuple[str, ...]:
    """正文里出现过的贴图路径，按令牌首次出现顺序去重。"""
    return tuple(
        FORUM_STICKER_ASSETS[token]
        for token in effect_tokens(text)
        if token in FORUM_STICKER_ASSETS
    )


def ink_bounds(images) -> QRect:
    """所有帧可见像素的并集，用来裁掉 gif 自带的透明留白；全透明时返回整帧。

    用全部帧求并集而不是逐帧裁：逐帧裁会让各帧的对齐基准漂移，动起来画面会抖。
    """
    images = tuple(images)
    if not images:
        return QRect()
    union = QRegion()
    for image in images:
        union = union.united(QRegion(QBitmap(image.createAlphaMask())))
    bounds = union.boundingRect()
    if bounds.isEmpty():
        return QRect(0, 0, images[0].width(), images[0].height())
    return bounds


@lru_cache(maxsize=8)
def sticker_frames(path: str, height: int) -> tuple[QImage, ...]:
    """gif 每一帧裁掉透明留白、按目标高度缩放后的 `QImage`；解码失败时是空元组。"""
    frames = decode_image_frames(path)
    if not frames:
        logger.warning("论坛贴图加载失败：%s", path)
        return ()
    images = tuple(qimage_from_raster_frame(frame) for frame in frames)
    box = ink_bounds(images)
    height = max(1, int(height))
    width = max(1, round(height * box.width() / max(1, box.height())))
    return tuple(
        image.copy(box).scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        for image in images
    )


def release_subscription(center, callback) -> None:
    """退掉一次贴图订阅。

    销毁期（`destroyed`）与解释器退出期都可能拿不到事件中心，退不掉就算了，不能因此抛出异常：
    这条路径本来就是「控件已经没了，顺手把订阅摘掉」。
    """
    try:
        center.unsubscribe(EventType.GIF_FRAME, callback)
    except Exception:
        pass


class ForumSticker(QWidget):
    """一张自己会走的贴图：固定尺寸，帧由全局 `GIF_FRAME` 事件推进。"""

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = str(path)
        self._frames = sticker_frames(self.path, STICKER_HEIGHT)
        self._frame_index = 0
        self.setObjectName("ForumSticker")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(self.sizeHint())
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.GIF_FRAME, self._on_gif_frame)
        # 卡片重排是 setParent(None) + deleteLater()：贴图跟着卡片一起销毁，订阅必须跟着退，
        # 否则事件中心会一直握着一个已销毁的控件，下一次 GIF_FRAME 就报 RuntimeError。
        # 槽里只碰捕获下来的事件中心与回调——`destroyed` 触发时控件本身已经不能访问了。
        center, callback = self._event_center, self._on_gif_frame
        self.destroyed.connect(
            lambda *_args: release_subscription(center, callback)
        )

    # ── 对外 ─────────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        return self._frames[0].size() if self._frames else QSize(0, 0)

    def current_frame(self) -> QImage | None:
        return self._frames[self._frame_index] if self._frames else None

    def frame_index(self) -> int:
        return self._frame_index

    def frame_count(self) -> int:
        return len(self._frames)

    def advance(self) -> None:
        """换到下一帧；只有一帧或没有帧时不动。"""
        if not self._frames:
            return
        self._frame_index = (self._frame_index + 1) % len(self._frames)
        self.update()

    # ── 事件与绘制 ───────────────────────────────────────────────────

    def _on_gif_frame(self, _event=None) -> None:
        self.advance()

    def paintEvent(self, event) -> None:
        frame = self.current_frame()
        if frame is None:
            return
        painter = QPainter(self)
        try:
            painter.drawImage(0, 0, frame)
        finally:
            painter.end()


__all__ = [
    "FORUM_STICKER_ASSETS",
    "STICKER_HEIGHT",
    "ForumSticker",
    "ink_bounds",
    "release_subscription",
    "sticker_frames",
    "sticker_paths",
]
