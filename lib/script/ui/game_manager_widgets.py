"""游戏管理窗口的卡片控件：一张已安装游戏的卡片。

批次 3 后续轮次从 `game_manager_window` 切出。游戏管理窗口（`GameManagerWindow`）本体是
一个工作台工具页，里面夹着自包含的卡片控件 `_GameCardWidget`（标题 / 官方徽标 / 版本 /
摘要 / 扩展计数）。它只读一条 `InstalledGame` 记录，不看窗口状态，因此先整块搬出来。

切分保持逐行等价：`_GameCardWidget` 的类体与搬出前一致（缩进也未变）。`game_manager_window`
按原名重新导出该名字，窗口本体的调用点零改动。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from config.scale import scale_px
from lib.script.gemes.MAIN.game_packages import InstalledGame
from lib.script.ui.render_bridge import ui_font as get_ui_font

class _GameCardWidget(QFrame):
    def __init__(self, record: InstalledGame, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GameCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("selected", False)

        title = QLabel(record.manifest.name, self)
        title_font = get_ui_font(size=scale_px(14, min_abs=12))
        title_font.setBold(True)
        title.setFont(title_font)
        title.setObjectName("GameCardTitle")

        badge = QLabel("官方示例" if record.manifest.official else "开发者包", self)
        badge_font = get_ui_font(size=scale_px(10, min_abs=9))
        badge_font.setBold(True)
        badge.setFont(badge_font)
        badge.setAlignment(Qt.AlignCenter)
        badge.setObjectName("GameCardBadge")
        badge.setProperty("official", record.manifest.official)
        self._badge = badge

        meta = QLabel(f"v{record.manifest.version}   {record.manifest.game_id}", self)
        meta.setFont(get_ui_font(size=scale_px(12, min_abs=10)))
        meta.setObjectName("GameCardMeta")

        summary = QLabel(record.manifest.summary or "暂无简介", self)
        summary.setFont(get_ui_font(size=scale_px(12, min_abs=10)))
        summary.setWordWrap(True)
        summary.setObjectName("GameCardSummary")

        ext = QLabel(
            f"粒子 {len(record.manifest.particle_extensions)}  ·  特效 {len(record.manifest.effect_extensions)}",
            self,
        )
        ext.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        ext.setObjectName("GameCardExt")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(scale_px(8, min_abs=6))
        top.addWidget(title, 1)
        top.addWidget(badge, 0, Qt.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(scale_px(12, min_abs=10), scale_px(10, min_abs=8), scale_px(12, min_abs=10), scale_px(10, min_abs=8))
        layout.setSpacing(scale_px(5, min_abs=4))
        layout.addLayout(top)
        layout.addWidget(meta)
        layout.addWidget(summary)
        layout.addWidget(ext)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.style().unpolish(self._badge)
        self.style().polish(self._badge)


__all__ = [
    "_GameCardWidget",
]
