"""Game package manager window."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.font_config import get_digit_font, get_ui_font
from config.scale import scale_px
from lib.script.gemes.MAIN.game_packages import InstalledGame, get_game_package_service

if TYPE_CHECKING:
    from .runtime import GameRuntime


_BG = QColor(8, 8, 10)
_HEADER_BG = QColor(14, 14, 18)
_PANEL = QColor(18, 18, 24)
_CARD = QColor(18, 18, 24)
_CARD_ACTIVE = QColor(14, 14, 18)
_BORDER = QColor(52, 52, 58)
_SOFT = QColor(72, 72, 80)
_MID = QColor(255, 173, 204)
_PINK = QColor(255, 145, 188)
_PINK_SOFT = QColor(255, 198, 220)
_CYAN = QColor(140, 210, 255)
_CYAN_SOFT = QColor(220, 240, 255)
_TEXT = QColor(248, 248, 252)
_TEXT_SOFT = QColor(216, 218, 226)
_TEXT_DIM = QColor(168, 170, 180)
_BLACK = QColor(0, 0, 0)
_DANGER = QColor(255, 122, 146)


def _rgb(color: QColor) -> str:
    return f"rgb({color.red()}, {color.green()}, {color.blue()})"


def _rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


class _GameCardWidget(QFrame):
    def __init__(self, record: InstalledGame, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GameCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("selected", False)

        title = QLabel(record.manifest.name, self)
        title_font = get_ui_font(size=scale_px(16, min_abs=14))
        title_font.setBold(True)
        title.setFont(title_font)
        title.setObjectName("GameCardTitle")

        badge = QLabel("官方示例" if record.manifest.official else "开发者包", self)
        badge_font = get_ui_font(size=scale_px(11, min_abs=10))
        badge_font.setBold(True)
        badge.setFont(badge_font)
        badge.setAlignment(Qt.AlignCenter)
        badge.setObjectName("GameCardBadge")
        badge.setProperty("official", record.manifest.official)
        self._badge = badge

        meta = QLabel(f"v{record.manifest.version}   {record.manifest.game_id}", self)
        meta.setFont(get_ui_font(size=scale_px(13, min_abs=11)))
        meta.setObjectName("GameCardMeta")

        summary = QLabel(record.manifest.summary or "暂无简介", self)
        summary.setFont(get_ui_font(size=scale_px(13, min_abs=11)))
        summary.setWordWrap(True)
        summary.setObjectName("GameCardSummary")

        ext = QLabel(
            f"粒子 {len(record.manifest.particle_extensions)}  ·  特效 {len(record.manifest.effect_extensions)}",
            self,
        )
        ext.setFont(get_ui_font(size=scale_px(12, min_abs=11)))
        ext.setObjectName("GameCardExt")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(scale_px(8, min_abs=6))
        top.addWidget(title, 1)
        top.addWidget(badge, 0, Qt.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(scale_px(14, min_abs=12), scale_px(13, min_abs=11), scale_px(14, min_abs=12), scale_px(13, min_abs=11))
        layout.setSpacing(scale_px(6, min_abs=5))
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


class GameManagerWindow(QWidget):
    """Manager for installed game packages."""

    def __init__(self, runtime: "GameRuntime") -> None:
        super().__init__()
        self._runtime = runtime
        self._service = get_game_package_service()
        self._games: list[InstalledGame] = []
        self._cards: dict[str, _GameCardWidget] = {}
        self._dragging = False
        self._drag_offset = QPoint()

        self.setWindowTitle("游戏列表管理器")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.resize(scale_px(1040, min_abs=920), scale_px(720, min_abs=640))
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFont(get_ui_font(size=scale_px(13, min_abs=12)))

        self._build_ui()
        self._apply_styles()
        self.refresh_games()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(scale_px(16, min_abs=14), scale_px(16, min_abs=14), scale_px(16, min_abs=14), scale_px(16, min_abs=14))
        root.setSpacing(scale_px(10, min_abs=8))

        header = QFrame(self)
        header.setObjectName("ManagerHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setCursor(Qt.OpenHandCursor)
        header.installEventFilter(self)
        self._header = header
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(scale_px(18, min_abs=14), scale_px(15, min_abs=12), scale_px(18, min_abs=14), scale_px(15, min_abs=12))
        header_layout.setSpacing(scale_px(14, min_abs=12))

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(scale_px(4, min_abs=3))
        self._title = QLabel("游戏列表管理器", header)
        title_font = get_ui_font(size=scale_px(28, min_abs=24))
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setObjectName("ManagerTitle")
        self._title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._subtitle = QLabel("像整理桌面小玩具一样整理你的游戏包：打开、安装、打包、卸载，都在这里。", header)
        self._subtitle.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._subtitle.setWordWrap(True)
        self._subtitle.setObjectName("ManagerSubtitle")
        self._subtitle.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._inbox_hint = QLabel(header)
        self._inbox_hint.setFont(get_ui_font(size=scale_px(12, min_abs=11)))
        self._inbox_hint.setWordWrap(True)
        self._inbox_hint.setObjectName("InboxHint")
        self._inbox_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        title_box.addWidget(self._title)
        title_box.addWidget(self._subtitle)
        title_box.addWidget(self._inbox_hint)
        header_layout.addLayout(title_box, 1)

        glance = QLabel("GAME PACKAGE", header)
        glance_font = get_digit_font(size=scale_px(30, min_abs=26))
        glance_font.setBold(True)
        glance.setFont(glance_font)
        glance.setObjectName("HeaderGlance")
        glance.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._header_glance = glance
        header_layout.addWidget(glance, 0, Qt.AlignRight | Qt.AlignVCenter)

        self._close_btn = self._make_button("×", self.close, "title_close")
        self._close_btn.setObjectName("TitleCloseButton")
        self._close_btn.setFixedSize(scale_px(34, min_abs=32), scale_px(30, min_abs=28))
        close_font = get_ui_font(size=scale_px(18, min_abs=16))
        close_font.setBold(True)
        self._close_btn.setFont(close_font)
        header_layout.addWidget(self._close_btn, 0, Qt.AlignTop)
        root.addWidget(header)

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(scale_px(8, min_abs=6))
        self._stat_total = self._make_stat_card("已安装游戏")
        self._stat_official = self._make_stat_card("官方示例")
        self._stat_custom = self._make_stat_card("开发者包")
        for frame, _value in (self._stat_total, self._stat_official, self._stat_custom):
            stats.addWidget(frame, 1)
        root.addLayout(stats)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(scale_px(10, min_abs=8))
        root.addLayout(content, 1)

        list_panel = QFrame(self)
        list_panel.setObjectName("ManagerPanel")
        list_panel.setAttribute(Qt.WA_StyledBackground, True)
        self._list_panel = list_panel
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(scale_px(14, min_abs=12), scale_px(14, min_abs=12), scale_px(14, min_abs=12), scale_px(14, min_abs=12))
        list_layout.setSpacing(scale_px(8, min_abs=6))
        list_layout.addWidget(self._make_panel_title("已安装游戏", "双击卡片可直接打开。"))
        self._list = QListWidget(list_panel)
        self._list.setObjectName("GameList")
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setSpacing(scale_px(2, min_abs=2))
        self._list.currentItemChanged.connect(lambda *_: self._update_detail())
        self._list.itemDoubleClicked.connect(lambda _item: self._open_selected_game())
        list_layout.addWidget(self._list, 1)
        content.addWidget(list_panel, 5)

        self._fun_watermark = QLabel("HAVING\nFUN", list_panel)
        fun_font = get_digit_font(size=scale_px(48, min_abs=42))
        fun_font.setBold(True)
        self._fun_watermark.setFont(fun_font)
        self._fun_watermark.setObjectName("FunWatermark")
        self._fun_watermark.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        self._fun_watermark.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._fun_watermark.lower()

        detail = QFrame(self)
        detail.setObjectName("DetailPanel")
        detail.setAttribute(Qt.WA_StyledBackground, True)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(scale_px(16, min_abs=13), scale_px(16, min_abs=13), scale_px(16, min_abs=13), scale_px(16, min_abs=13))
        detail_layout.setSpacing(scale_px(10, min_abs=8))
        detail_layout.addWidget(self._make_panel_title("游戏详情", "安装目录、扩展和操作入口都集中在这里。"))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(scale_px(10, min_abs=8))
        self._detail_title = QLabel("暂无已选游戏", detail)
        detail_title_font = get_ui_font(size=scale_px(22, min_abs=18))
        detail_title_font.setBold(True)
        self._detail_title.setFont(detail_title_font)
        self._detail_title.setObjectName("DetailTitle")
        self._detail_badge = QLabel("待选择", detail)
        badge_font = get_ui_font(size=scale_px(12, min_abs=11))
        badge_font.setBold(True)
        self._detail_badge.setFont(badge_font)
        self._detail_badge.setAlignment(Qt.AlignCenter)
        self._detail_badge.setObjectName("DetailBadge")
        top.addWidget(self._detail_title, 1)
        top.addWidget(self._detail_badge, 0, Qt.AlignTop)
        detail_layout.addLayout(top)

        self._detail_summary = QLabel("请选择左侧的游戏卡片。", detail)
        self._detail_summary.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._detail_summary.setWordWrap(True)
        self._detail_summary.setObjectName("DetailSummary")
        detail_layout.addWidget(self._detail_summary)

        self._detail_meta = QLabel(detail)
        self._detail_meta.setFont(get_ui_font(size=scale_px(14, min_abs=12)))
        self._detail_meta.setWordWrap(True)
        self._detail_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_meta.setObjectName("DetailMeta")
        detail_layout.addWidget(self._detail_meta)

        self._detail_paths = QLabel(detail)
        self._detail_paths.setFont(get_ui_font(size=scale_px(13, min_abs=11)))
        self._detail_paths.setWordWrap(True)
        self._detail_paths.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_paths.setObjectName("DetailPaths")
        detail_layout.addWidget(self._detail_paths)

        detail_layout.addWidget(self._make_panel_title("管理操作", "打包会一起带走游戏资源、粒子与特效脚本。"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(scale_px(8, min_abs=6))
        grid.setVerticalSpacing(scale_px(8, min_abs=6))
        detail_layout.addLayout(grid)

        self._open_btn = self._make_button("打开游戏", self._open_selected_game, "cyan")
        self._install_btn = self._make_button("安装 ZIP", self._install_zip, "pink")
        self._scan_btn = self._make_button("扫描收件箱", self._scan_inbox, "soft")
        self._export_btn = self._make_button("打包导出", self._export_selected_game, "soft")
        self._uninstall_btn = self._make_button("卸载游戏", self._uninstall_selected_game, "danger")
        self._refresh_btn = self._make_button("刷新列表", self.refresh_games, "soft")
        for index, button in enumerate((self._open_btn, self._install_btn, self._scan_btn, self._export_btn, self._uninstall_btn, self._refresh_btn)):
            grid.addWidget(button, index // 2, index % 2)

        detail_layout.addStretch(1)
        self._status = QLabel(detail)
        self._status.setFont(get_ui_font(size=scale_px(13, min_abs=11)))
        self._status.setWordWrap(True)
        self._status.setObjectName("StatusText")
        detail_layout.addWidget(self._status)
        content.addWidget(detail, 6)
        self._layout_overlays()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget {{
                color: {_rgb(_TEXT)};
                background: {_rgb(_BG)};
            }}
            QFrame#ManagerHeader, QFrame#ManagerPanel, QFrame#DetailPanel, QFrame#StatCard {{
                background: transparent;
                border: 2px solid {_rgb(_BORDER)};
                border-radius: 0px;
            }}
            QLabel#ManagerTitle {{ color: {_rgb(_PINK)}; letter-spacing: 1px; }}
            QLabel#ManagerSubtitle {{ color: {_rgb(_TEXT_SOFT)}; }}
            QLabel#InboxHint {{ color: {_rgb(_CYAN_SOFT)}; }}
            QLabel#HeaderGlance {{
                color: rgba({_PINK.red()}, {_PINK.green()}, {_PINK.blue()}, 88);
                letter-spacing: 1px;
            }}
            QLabel#FunWatermark {{
                color: rgba({_PINK.red()}, {_PINK.green()}, {_PINK.blue()}, 58);
                background: transparent;
                letter-spacing: 1px;
            }}
            QLabel#PanelTitle {{ color: {_rgb(_PINK_SOFT)}; }}
            QLabel#PanelSubtitle, QLabel#StatTitle {{ color: {_rgb(_TEXT_DIM)}; }}
            QListWidget#GameList {{
                background: transparent;
                border: 1px solid {_rgb(_SOFT)};
                outline: none;
                padding: 4px;
            }}
            QListWidget#GameList::item {{
                background: transparent;
                border: none;
                padding: 0px;
            }}
            QListWidget#GameList::item:selected {{ background: transparent; }}
            QFrame#GameCard {{
                background: transparent;
                border: 1px solid {_rgb(_SOFT)};
                border-radius: 0px;
            }}
            QFrame#GameCard[selected="true"] {{
                background: rgba({_PINK.red()}, {_PINK.green()}, {_PINK.blue()}, 18);
                border: 2px solid {_rgb(_PINK)};
            }}
            QLabel#GameCardTitle, QLabel#DetailTitle, QLabel#StatValue {{ color: {_rgb(_TEXT)}; }}
            QLabel#GameCardMeta {{ color: {_rgb(_CYAN_SOFT)}; }}
            QLabel#GameCardSummary, QLabel#DetailSummary, QLabel#DetailMeta, QLabel#DetailPaths, QLabel#StatusText {{ color: {_rgb(_TEXT_SOFT)}; }}
            QLabel#GameCardExt {{ color: {_rgb(_TEXT_DIM)}; }}
            QLabel#GameCardBadge {{
                padding: 3px 8px;
                border-radius: 0px;
                color: {_rgb(_BLACK)};
                background: {_rgb(_CYAN_SOFT)};
            }}
            QLabel#GameCardBadge[official="true"] {{ background: {_rgb(_PINK_SOFT)}; }}
            QLabel#DetailBadge {{
                min-width: {scale_px(90, min_abs=80)}px;
                padding: 4px 10px;
                border-radius: 0px;
                color: {_rgb(_BLACK)};
                background: {_rgb(_CYAN_SOFT)};
            }}
            QLabel#DetailSummary, QLabel#DetailMeta, QLabel#DetailPaths {{
                background: transparent;
                border-top: 1px solid {_rgb(_SOFT)};
                border-bottom: 1px solid {_rgb(_SOFT)};
                border-left: 0px;
                border-right: 0px;
                border-radius: 0px;
                padding: {scale_px(12, min_abs=10)}px;
            }}
            QPushButton {{
                background: transparent;
                color: {_rgb(_TEXT)};
                border: 1px solid {_rgb(_SOFT)};
                border-radius: 0px;
                padding: {scale_px(10, min_abs=8)}px {scale_px(14, min_abs=12)}px;
                min-height: {scale_px(44, min_abs=40)}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border-color: {_rgb(_PINK)};
                background: {_rgb(_MID)};
                color: {_rgb(_BLACK)};
            }}
            QPushButton:pressed {{ color: {_rgb(_BLACK)}; background: {_rgb(_PINK_SOFT)}; }}
            QPushButton:disabled {{
                color: rgba({_TEXT_DIM.red()}, {_TEXT_DIM.green()}, {_TEXT_DIM.blue()}, 160);
                border-color: rgba({_SOFT.red()}, {_SOFT.green()}, {_SOFT.blue()}, 100);
                background: transparent;
            }}
            QPushButton[accent="pink"]:hover {{ border-color: {_rgb(_PINK)}; background: {_rgb(_MID)}; }}
            QPushButton[accent="pink"]:pressed {{ background: {_rgb(_PINK_SOFT)}; }}
            QPushButton[accent="danger"]:hover {{ border-color: {_rgb(_DANGER)}; background: rgb(255, 168, 188); color: {_rgb(_BLACK)}; }}
            QPushButton#TitleCloseButton {{
                background: transparent;
                border: 1px solid {_rgb(_SOFT)};
                padding: 0px;
                min-height: 0px;
            }}
            QPushButton#TitleCloseButton:hover {{
                background: {_rgb(_MID)};
                border-color: {_rgb(_PINK)};
                color: {_rgb(_BLACK)};
            }}
            QPushButton#TitleCloseButton:pressed {{
                background: {_rgb(_PINK_SOFT)};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: {scale_px(10, min_abs=10)}px;
                margin: 4px 2px 4px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {_rgb(_PINK)};
                min-height: {scale_px(28, min_abs=24)}px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            """
        )

    def _make_panel_title(self, title: str, subtitle: str) -> QWidget:
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(3, min_abs=2))
        title_label = QLabel(title, box)
        title_font = get_ui_font(size=scale_px(14, min_abs=12))
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setObjectName("PanelTitle")
        subtitle_label = QLabel(subtitle, box)
        subtitle_label.setFont(get_ui_font(size=scale_px(12, min_abs=11)))
        subtitle_label.setWordWrap(True)
        subtitle_label.setObjectName("PanelSubtitle")
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        return box

    def _make_stat_card(self, title: str) -> tuple[QFrame, QLabel]:
        frame = QFrame(self)
        frame.setObjectName("StatCard")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(scale_px(14, min_abs=12), scale_px(13, min_abs=11), scale_px(14, min_abs=12), scale_px(13, min_abs=11))
        layout.setSpacing(scale_px(6, min_abs=5))
        title_label = QLabel(title, frame)
        title_label.setFont(get_ui_font(size=scale_px(12, min_abs=11)))
        title_label.setObjectName("StatTitle")
        value_label = QLabel("0", frame)
        value_font = get_ui_font(size=scale_px(24, min_abs=22))
        value_font.setBold(True)
        value_label.setFont(value_font)
        value_label.setObjectName("StatValue")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return frame, value_label

    def _make_button(self, text: str, callback, accent: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setProperty("accent", accent)
        font = get_ui_font(size=scale_px(13, min_abs=12))
        font.setBold(True)
        button.setFont(font)
        button.clicked.connect(callback)
        return button

    def _layout_overlays(self) -> None:
        if not hasattr(self, "_fun_watermark") or not hasattr(self, "_list_panel"):
            return
        self._fun_watermark.adjustSize()
        margin_x = scale_px(18, min_abs=14)
        margin_y = scale_px(18, min_abs=14)
        target_y = self._list_panel.height() - self._fun_watermark.height() - margin_y
        self._fun_watermark.move(margin_x, max(scale_px(52, min_abs=44), target_y))

    @staticmethod
    def _dialog_options() -> QFileDialog.Options:
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        return options

    def eventFilter(self, watched, event) -> bool:
        if watched is self._header:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                self._header.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return True
            if event.type() == QEvent.MouseMove and self._dragging and (event.buttons() & Qt.LeftButton):
                self.move(event.globalPos() - self._drag_offset)
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._dragging = False
                self._header.setCursor(Qt.OpenHandCursor)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_overlays()

    def refresh_games(self) -> None:
        self._runtime.refresh_available_games()
        self._games = self._service.list_installed_games()
        current_id = self._selected_game_id()
        self._inbox_hint.setText(f"收件箱：{self._service.inbox_dir()}")

        self._list.clear()
        self._cards.clear()
        selected_row = -1
        for index, record in enumerate(self._games):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, record.game_id)
            card = _GameCardWidget(record, self._list)
            item.setSizeHint(card.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, card)
            self._cards[record.game_id] = card
            if record.game_id == current_id:
                selected_row = index

        if selected_row >= 0:
            self._list.setCurrentRow(selected_row)
        elif self._games:
            self._list.setCurrentRow(0)

        official = sum(1 for record in self._games if record.manifest.official)
        self._stat_total[1].setText(str(len(self._games)))
        self._stat_official[1].setText(str(official))
        self._stat_custom[1].setText(str(len(self._games) - official))
        self._set_status(
            "当前没有已安装游戏。可以直接安装 ZIP，或把 ZIP 丢进收件箱后点击“扫描收件箱”。"
            if not self._games
            else f"已载入 {len(self._games)} 个游戏包。"
        )
        self._update_detail()

    def _selected_game_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return str(value).strip() if value else None

    def _selected_game(self) -> InstalledGame | None:
        game_id = self._selected_game_id()
        if not game_id:
            return None
        return next((record for record in self._games if record.game_id == game_id), None)

    def _update_detail(self) -> None:
        record = self._selected_game()
        selected_id = record.game_id if record is not None else None
        for game_id, card in self._cards.items():
            card.set_selected(game_id == selected_id)

        has_selection = record is not None
        self._open_btn.setEnabled(has_selection)
        self._export_btn.setEnabled(has_selection)
        self._uninstall_btn.setEnabled(has_selection)

        if record is None:
            self._detail_title.setText("暂无已选游戏")
            self._detail_badge.setText("待选择")
            self._detail_badge.setStyleSheet(f"background: {_rgb(_CYAN_SOFT)}; color: {_rgb(_BLACK)};")
            self._detail_summary.setText("请选择左侧的游戏卡片。")
            self._detail_meta.setText("安装后，游戏 ID、版本、扩展数量和来源会显示在这里。")
            self._detail_paths.setText(f"收件箱目录\n{self._service.inbox_dir()}")
            return

        manifest = record.manifest
        particle_text = ", ".join(ext.local_id for ext in manifest.particle_extensions) or "无"
        effect_text = ", ".join(ext.local_id for ext in manifest.effect_extensions) or "无"
        source_text = "官方示例包" if manifest.official else "开发者包"
        self._detail_title.setText(manifest.name)
        self._detail_badge.setText(source_text)
        badge_color = _rgb(_PINK_SOFT if manifest.official else _CYAN_SOFT)
        self._detail_badge.setStyleSheet(f"background: {badge_color}; color: {_rgb(_BLACK)};")
        self._detail_summary.setText(manifest.summary or "暂无简介")
        self._detail_meta.setText(
            "<b>ID</b>  {game_id}<br>"
            "<b>版本</b>  v{version}<br>"
            "<b>来源</b>  {source}<br>"
            "<b>粒子扩展</b>  {particles}<br>"
            "<b>特效扩展</b>  {effects}".format(
                game_id=html.escape(manifest.game_id),
                version=html.escape(manifest.version),
                source=html.escape(source_text),
                particles=html.escape(particle_text),
                effects=html.escape(effect_text),
            )
        )
        self._detail_paths.setText(
            "安装目录\n{install}\n\n数据目录\n{data}\n\n缓存目录\n{cache}".format(
                install=record.install_dir,
                data=record.data_root,
                cache=record.cache_root,
            )
        )

    def _open_selected_game(self) -> None:
        record = self._selected_game()
        if record is None:
            self._show_info("请先选择要打开的游戏。")
            return
        try:
            self._runtime.open_game(record.game_id)
        except Exception as exc:
            self._show_error(f"打开 {record.manifest.name} 失败：{exc}")
            return
        self._set_status(f"已打开 {record.manifest.name}。")

    def _install_zip(self) -> None:
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择游戏 ZIP",
            str(Path.home()),
            "Game Packages (*.zip)",
            options=self._dialog_options(),
        )
        if not zip_path:
            return
        try:
            installed = self._service.install_from_zip(Path(zip_path), source="dialog")
        except Exception as exc:
            self._show_error(f"安装失败：{exc}")
            return
        self._runtime.refresh_available_games()
        self.refresh_games()
        self._set_status(f"已安装 {installed.manifest.name} {installed.manifest.version}。")
        self._show_info(f"已安装 {installed.manifest.name} {installed.manifest.version}")

    def _scan_inbox(self) -> None:
        messages = self._service.scan_inbox()
        self._runtime.refresh_available_games()
        self.refresh_games()
        self._set_status("收件箱扫描完成。")
        self._show_info("\n".join(messages))

    def _export_selected_game(self) -> None:
        record = self._selected_game()
        if record is None:
            self._show_info("请先选择要打包导出的游戏。")
            return
        default_name = f"{record.manifest.game_id}-{record.manifest.version}.zip"
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出游戏包",
            str(Path.home() / "Desktop" / default_name),
            "Game Packages (*.zip)",
            options=self._dialog_options(),
        )
        if not output_path:
            return
        try:
            exported = self._service.export_game_zip(record.game_id, Path(output_path))
        except Exception as exc:
            self._show_error(f"导出失败：{exc}")
            return
        self._set_status(f"已导出 {record.manifest.name}。")
        self._show_info(f"已导出：{exported}")

    def _uninstall_selected_game(self) -> None:
        record = self._selected_game()
        if record is None:
            self._show_info("请先选择要卸载的游戏。")
            return
        answer = QMessageBox.question(
            self,
            "卸载游戏",
            f"确定卸载 {record.manifest.name} {record.manifest.version} 吗？\n这不会删除 {record.data_root} 内的存档。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self._runtime.close_game(record.game_id)
            self._service.uninstall_game(record.game_id)
        except Exception as exc:
            self._show_error(f"卸载失败：{exc}")
            return
        self._runtime.refresh_available_games()
        self.refresh_games()
        self._set_status(f"已卸载 {record.manifest.name}。")
        self._show_info(f"已卸载 {record.manifest.name}")

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _show_info(self, text: str) -> None:
        QMessageBox.information(self, "游戏管理器", text)

    def _show_error(self, text: str) -> None:
        QMessageBox.critical(self, "游戏管理器", text)
