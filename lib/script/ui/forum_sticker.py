"""卡片底部效果贴图（产品垫片）。

卡片底部效果贴图的实现已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/forum_sticker.py`。这里按原名再导出（经 `render_bridge.tier_d_reexports()` 解析，
垫片本身不得静态 import 档位 D），既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    lru_cache,
    QRect,
    QSize,
    Qt,
    QBitmap,
    QImage,
    QPainter,
    QRegion,
    QSizePolicy,
    QWidget,
    scale_px,
    EventType,
    get_event_center,
    decode_image_frames,
    effect_tokens,
    get_logger,
    configure_raster_converter,
    logger,
    FORUM_STICKER_ASSETS,
    STICKER_HEIGHT,
    sticker_paths,
    ink_bounds,
    sticker_frames,
    release_subscription,
    ForumSticker,
) = render_bridge.tier_d_reexports("forum_sticker", (
    "lru_cache",
    "QRect",
    "QSize",
    "Qt",
    "QBitmap",
    "QImage",
    "QPainter",
    "QRegion",
    "QSizePolicy",
    "QWidget",
    "scale_px",
    "EventType",
    "get_event_center",
    "decode_image_frames",
    "effect_tokens",
    "get_logger",
    "configure_raster_converter",
    "logger",
    "FORUM_STICKER_ASSETS",
    "STICKER_HEIGHT",
    "sticker_paths",
    "ink_bounds",
    "sticker_frames",
    "release_subscription",
    "ForumSticker",
))

render_bridge.configure_forum_sticker_raster_converter(render_bridge.qimage_from_raster_frame)

__all__ = [
    "lru_cache",
    "QRect",
    "QSize",
    "Qt",
    "QBitmap",
    "QImage",
    "QPainter",
    "QRegion",
    "QSizePolicy",
    "QWidget",
    "scale_px",
    "EventType",
    "get_event_center",
    "decode_image_frames",
    "effect_tokens",
    "get_logger",
    "configure_raster_converter",
    "logger",
    "FORUM_STICKER_ASSETS",
    "STICKER_HEIGHT",
    "sticker_paths",
    "ink_bounds",
    "sticker_frames",
    "release_subscription",
    "ForumSticker",
]
