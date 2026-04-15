"""QQ群二维码展示窗口。"""

from __future__ import annotations

from pathlib import Path

from config.scale import scale_px
from lib.script.ui.qr_dialog_base import BaseQrDialog


class QQGroupDialog(BaseQrDialog):
    """显示本地 QQ 群二维码图片的独立浮窗。"""

    def __init__(self, image_path: str | Path) -> None:
        self._image_path = Path(image_path)
        super().__init__(
            title="进入QQ群获取更新",
            status="使用手机QQ扫码进群",
            action_text="关闭窗口",
            placeholder_text="QQqrc.png 未找到",
            qr_background=False,
            status_font_size=scale_px(11, min_abs=9),
        )
        self._load_pixmap()

    def _load_pixmap(self) -> None:
        self._set_qr_pixmap_from_path(self._image_path, clear_when_missing=True)

    def show_dialog(self) -> None:
        self._load_pixmap()
        self._show_dialog()
