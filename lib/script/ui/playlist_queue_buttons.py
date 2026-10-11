"""音响队列面板的自绘小件（产品侧再导出垫片）。

两个自绘按钮（`_QueueRemoveButton` / `_QueuePlayNowButton`）与尺寸常量已按
《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/playlist_queue_buttons.py`。本模块按原名再导出，既有
导入面不变；按钮交互外观混入（`speaker_menu_style.SpeakerActionButtonMixin`）与动作按钮
文字色由本垫片在导入时经 seam 钩子注入（宿主不得 import `lib.script`）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _action_button_mixin():
    from lib.script.ui.speaker_menu_style import SpeakerActionButtonMixin

    return SpeakerActionButtonMixin


def _action_text_color():
    from lib.script.ui.speaker_menu_style import _C_ACTION_TEXT

    return _C_ACTION_TEXT


render_bridge.install_tier_d_hook(
    "playlist_queue_buttons:action_button_mixin", _action_button_mixin
)
render_bridge.install_tier_d_hook(
    "playlist_queue_buttons:action_text_color", _action_text_color
)

(
    _AUTO_HIDE_MOUSE_DISTANCE,
    _BORDER,
    _GAP,
    _LAYER,
    _PAD_X,
    _PAGE_SIZE,
    _QueuePlayNowButton,
    _QueueRemoveButton,
    _REMOVE_BTN_H,
    _REMOVE_BTN_W,
    _ROW_H,
    _WIDTH,
) = render_bridge.tier_d_reexports("playlist_queue_buttons", (
    "_AUTO_HIDE_MOUSE_DISTANCE",
    "_BORDER",
    "_GAP",
    "_LAYER",
    "_PAD_X",
    "_PAGE_SIZE",
    "_QueuePlayNowButton",
    "_QueueRemoveButton",
    "_REMOVE_BTN_H",
    "_REMOVE_BTN_W",
    "_ROW_H",
    "_WIDTH",
))

__all__ = [
    "_AUTO_HIDE_MOUSE_DISTANCE",
    "_BORDER",
    "_GAP",
    "_LAYER",
    "_PAD_X",
    "_PAGE_SIZE",
    "_QueuePlayNowButton",
    "_QueueRemoveButton",
    "_REMOVE_BTN_H",
    "_REMOVE_BTN_W",
    "_ROW_H",
    "_WIDTH",
]
