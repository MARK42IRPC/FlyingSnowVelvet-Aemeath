"""Standalone bug tracker window."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QCursor, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config.font_config import get_digit_font, get_ui_font
from config.scale import scale_px
from lib.core.layer import Layer
from lib.core.qt_bridge.workbench_page import QtWorkbenchToolPage
from lib.core.unified_draw import get_layer_manager
from lib.script.app.startup_probe import load_saved_watermark_payload
from lib.script.bug_tracker.storage import BugInstanceInfo, BugRecord, BugTrackerLogStore
from lib.script.workbench.theme import get_workbench_colors


def _set_theme_colors() -> None:
    colors = get_workbench_colors()
    globals().update({
        "_BG": QColor(colors.canvas),
        "_HEADER_BG": QColor(colors.surface),
        "_PANEL_BG": QColor(colors.surface_raised),
        "_BORDER": QColor(colors.border),
        "_SOFT_BORDER": QColor(colors.border_strong),
        "_MID": QColor(colors.surface_hover),
        "_PINK": QColor(colors.pink),
        "_PINK_SOFT": QColor(colors.pink_hover),
        "_CYAN": QColor(colors.cyan),
        "_WARNING": QColor(colors.warning),
        "_DANGER": QColor(colors.danger),
        "_TEXT_MAIN": QColor(colors.text),
        "_TEXT_SOFT": QColor(colors.text_muted),
        "_TEXT_DIM": QColor(colors.text_dim),
        "_BLACK": QColor(colors.canvas),
    })


_set_theme_colors()


class _BugTrackerWatermarkOverlay(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, event) -> None:
        del event
        host = self.parent()
        if host is None:
            return

        title_text = str(getattr(host, "_watermark_title_text", "") or "").strip()
        meta_text = str(getattr(host, "_watermark_meta_text", "") or "").strip()
        hardware_text = str(getattr(host, "_watermark_hardware_text", "") or "").strip()
        corner_text = str(getattr(host, "_watermark_corner_text", "") or "").strip()
        if not any((title_text, meta_text, hardware_text, corner_text)):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = self.rect()

        title_color = QColor(110, 78, 92)
        title_color.setAlpha(78)
        detail_color = QColor(128, 92, 108)
        detail_color.setAlpha(92)

        if title_text:
            painter.setPen(title_color)
            title_font = get_digit_font(size=max(scale_px(34, min_abs=24), int(rect.height() * 0.072)))
            title_font.setBold(True)
            painter.setFont(title_font)
            title_rect = rect.adjusted(
                scale_px(22, min_abs=16),
                int(rect.height() * 0.48),
                -int(rect.width() * 0.55),
                -scale_px(26, min_abs=18),
            )
            painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignBottom, title_text)

        if hardware_text:
            painter.setPen(detail_color)
            hardware_font = get_digit_font(size=max(scale_px(11, min_abs=9), int(rect.height() * 0.015)))
            hardware_font.setBold(True)
            painter.setFont(hardware_font)
            hardware_rect = rect.adjusted(
                int(rect.width() * 0.58),
                scale_px(92, min_abs=78),
                -scale_px(24, min_abs=16),
                -int(rect.height() * 0.68),
            )
            painter.drawText(hardware_rect, Qt.AlignRight | Qt.AlignTop, hardware_text)

        if corner_text:
            painter.setPen(detail_color)
            corner_font = get_digit_font(size=max(scale_px(15, min_abs=12), int(rect.height() * 0.021)))
            corner_font.setBold(True)
            painter.setFont(corner_font)
            corner_rect = rect.adjusted(
                int(rect.width() * 0.54),
                int(rect.height() * 0.60),
                -scale_px(28, min_abs=20),
                -scale_px(34, min_abs=24),
            )
            painter.drawText(corner_rect, Qt.AlignRight | Qt.AlignBottom, corner_text)

        if meta_text:
            painter.save()
            painter.setPen(detail_color)
            meta_font = get_digit_font(size=max(scale_px(12, min_abs=9), int(rect.height() * 0.018)))
            meta_font.setBold(True)
            painter.setFont(meta_font)
            painter.translate(rect.width() - scale_px(20, min_abs=16), int(rect.height() * 0.80))
            painter.rotate(-90)
            painter.drawText(0, 0, meta_text)
            painter.restore()


class BugTrackerWindow(QtWorkbenchToolPage):
    def __init__(self, embedded: bool = False) -> None:
        super().__init__(embedded=embedded)
        self.setObjectName("BugTrackerWindow")
        self.setWindowTitle("故障跟踪")
        if not self._embedded:
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setMinimumSize(scale_px(1040, min_abs=940), scale_px(660, min_abs=580))
            self.resize(scale_px(1180, min_abs=1020), scale_px(760, min_abs=640))
            get_layer_manager().register(self, Layer.PANEL, name="BugTrackerWindow")

        self._store = BugTrackerLogStore()
        self._records: list[BugRecord] = []
        self._instances: list[BugInstanceInfo] = []
        self._instance_filter = ""
        self._selected_record_key = ""
        self._snapshot_token = None
        self._level_filters = {
            "info": True,
            "warn": True,
            "error": True,
        }
        self._watermark_title_text = "BUG\nTRACKER"
        self._watermark_hardware_text = "UnKnow GPU 0.00 GB\nRAM 0.00 GB"
        self._watermark_meta_text = "CPU 0C  RAM 0.00 GB\n0x0  x1.0"
        self._watermark_corner_text = "UnKnow GPU\nunknown"

        self._title_font = get_ui_font(size=scale_px(22, min_abs=18))
        self._title_font.setBold(True)
        self._ui_font = get_ui_font(size=scale_px(12, min_abs=10))
        self._ui_bold_font = get_ui_font(size=scale_px(12, min_abs=10))
        self._ui_bold_font.setBold(True)
        self._subtitle_font = get_ui_font(size=scale_px(11, min_abs=10))

        self._header = QFrame(self)
        self._header.setObjectName("BugTrackerHeader")
        self._header.setFixedHeight(scale_px(58, min_abs=50))
        self.set_drag_handle(self._header)

        self._title = QLabel("故障跟踪", self._header)
        self._title.setFont(self._title_font)
        self._title.setObjectName("BugTrackerTitle")

        self._subtitle = QLabel("自动载入全部 app 日志，按启动实例分类，实时查看并定位 INFO / WARN / ERROR", self._header)
        self._subtitle.setFont(self._subtitle_font)
        self._subtitle.setObjectName("BugTrackerSubtitle")

        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(0)
        header_text.addWidget(self._title)
        header_text.addWidget(self._subtitle)

        header_actions = QHBoxLayout()
        header_actions.setContentsMargins(0, 0, 0, 0)
        header_actions.setSpacing(scale_px(8, min_abs=6))
        self._header_refresh_button = self._make_button("刷新", self._refresh_now)
        self._header_source_button = self._make_button("打开源码", self._open_selected_source)
        self._header_copy_button = self._make_button("复制详情", self._copy_selected_detail)
        header_actions.addWidget(self._header_refresh_button)
        header_actions.addWidget(self._header_source_button)
        header_actions.addWidget(self._header_copy_button)
        self._header_close_button = self._make_button("关闭", self._request_close)
        header_actions.addWidget(self._header_close_button)

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(scale_px(14, min_abs=10), scale_px(10, min_abs=8), scale_px(14, min_abs=10), scale_px(10, min_abs=8))
        header_layout.setSpacing(scale_px(14, min_abs=10))
        header_layout.addLayout(header_text, 1)
        header_layout.addLayout(header_actions, 0)

        self._card_total = self._make_stat_card("当前分类日志")
        self._card_today = self._make_stat_card("今日日志")
        self._card_modules = self._make_stat_card("模块数")
        self._card_dup = self._make_stat_card("重复组")

        cards = QHBoxLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(scale_px(10, min_abs=8))
        for card in (self._card_total, self._card_today, self._card_modules, self._card_dup):
            cards.addWidget(card, 1)

        self._filter_bar = QFrame(self)
        self._filter_bar.setObjectName("BugTrackerPanel")
        filter_layout = QHBoxLayout(self._filter_bar)
        filter_layout.setContentsMargins(scale_px(8, min_abs=6), scale_px(6, min_abs=4), scale_px(8, min_abs=6), scale_px(6, min_abs=4))
        filter_layout.setSpacing(scale_px(8, min_abs=6))
        filter_label = QLabel("等级筛选", self._filter_bar)
        filter_label.setFont(self._ui_bold_font)
        filter_label.setObjectName("BugTrackerFilterLabel")
        filter_layout.addWidget(filter_label)
        self._filter_info_btn = self._make_filter_button("INFO", "info")
        self._filter_warn_btn = self._make_filter_button("WARN", "warn")
        self._filter_error_btn = self._make_filter_button("ERROR", "error")
        filter_layout.addWidget(self._filter_info_btn)
        filter_layout.addWidget(self._filter_warn_btn)
        filter_layout.addWidget(self._filter_error_btn)
        filter_layout.addStretch(1)
        self._export_zip_btn = self._make_export_button("打包当前项到桌面")
        filter_layout.addWidget(self._export_zip_btn)

        self._instance_list = QListWidget(self)
        self._instance_list.setObjectName("BugTrackerList")
        self._instance_list.setFont(self._ui_font)
        self._instance_list.setSelectionMode(QListWidget.SingleSelection)
        self._instance_list.currentItemChanged.connect(self._on_instance_changed)

        self._error_list = QListWidget(self)
        self._error_list.setObjectName("BugTrackerList")
        self._error_list.setFont(self._ui_font)
        self._error_list.setSelectionMode(QListWidget.SingleSelection)
        self._error_list.currentItemChanged.connect(self._on_error_changed)
        self._error_list.itemDoubleClicked.connect(lambda _: self._open_selected_source())

        self._detail = QTextEdit(self)
        self._detail.setObjectName("BugTrackerDetail")
        self._detail.setReadOnly(True)
        self._detail.setFont(self._ui_font)

        left_panel = self._make_panel("启动实例", self._instance_list)
        center_panel = self._make_panel("日志列表", self._error_list)
        right_panel = self._make_detail_panel()

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        splitter.setChildrenCollapsible(False)
        left_panel.setMinimumWidth(scale_px(150, min_abs=130))
        center_panel.setMinimumWidth(scale_px(220, min_abs=190))
        right_panel.setMinimumWidth(scale_px(260, min_abs=220))
        splitter.setSizes([scale_px(180), scale_px(300), scale_px(340)])
        self._content_splitter = splitter

        self._status = QLabel("就绪", self)
        self._status.setFont(self._ui_font)
        self._status.setObjectName("BugTrackerStatus")

        root = QVBoxLayout(self)
        self._root_layout = root
        root_margin = scale_px(10, min_abs=8) if self._embedded else scale_px(12, min_abs=10)
        root.setContentsMargins(root_margin, root_margin, root_margin, root_margin)
        root.setSpacing(scale_px(7, min_abs=5))
        root.addWidget(self._header)
        root.addLayout(cards)
        root.addWidget(self._filter_bar)
        root.addWidget(splitter, 1)
        root.addWidget(self._status)

        self._watermark_overlay = _BugTrackerWatermarkOverlay(self)
        self._watermark_overlay.setGeometry(self.rect())
        self._watermark_overlay.setVisible(not self._embedded)
        self._watermark_overlay.raise_()

        self.setStyleSheet(
            f"""
            QWidget#BugTrackerWindow {{
                color: rgb({_TEXT_MAIN.red()}, {_TEXT_MAIN.green()}, {_TEXT_MAIN.blue()});
                background: rgb({_BG.red()}, {_BG.green()}, {_BG.blue()});
            }}
            QWidget#BugTrackerWindow QWidget {{
                color: rgb({_TEXT_MAIN.red()}, {_TEXT_MAIN.green()}, {_TEXT_MAIN.blue()});
                background: transparent;
            }}
            QWidget#BugTrackerWindow QLabel {{
                background: transparent;
            }}
            QFrame#BugTrackerHeader, QFrame#BugTrackerPanel, QFrame#BugTrackerCard {{
                background: rgb({_HEADER_BG.red()}, {_HEADER_BG.green()}, {_HEADER_BG.blue()});
                border: 1px solid rgb({_BORDER.red()}, {_BORDER.green()}, {_BORDER.blue()});
                border-radius: {scale_px(4, min_abs=3)}px;
            }}
            QFrame#BugTrackerCard {{
                background: rgb({_PANEL_BG.red()}, {_PANEL_BG.green()}, {_PANEL_BG.blue()});
            }}
            QLabel#BugTrackerTitle, QLabel#BugTrackerStatValue {{
                color: rgb({_TEXT_MAIN.red()}, {_TEXT_MAIN.green()}, {_TEXT_MAIN.blue()});
            }}
            QLabel#BugTrackerSubtitle, QLabel#BugTrackerFilterLabel,
            QLabel#BugTrackerStatTitle, QLabel#BugTrackerStatus {{
                color: rgb({_TEXT_SOFT.red()}, {_TEXT_SOFT.green()}, {_TEXT_SOFT.blue()});
            }}
            QLabel#BugTrackerPanelTitle {{
                color: rgb({_TEXT_MAIN.red()}, {_TEXT_MAIN.green()}, {_TEXT_MAIN.blue()});
                font-weight: 700;
            }}
            QLabel#BugTrackerStatus {{
                background: transparent;
                border: 1px solid rgb({_BORDER.red()}, {_BORDER.green()}, {_BORDER.blue()});
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: {scale_px(7, min_abs=5)}px {scale_px(9, min_abs=7)}px;
            }}
            QListWidget#BugTrackerList, QTextEdit#BugTrackerDetail {{
                background: rgb({_PANEL_BG.red()}, {_PANEL_BG.green()}, {_PANEL_BG.blue()});
                color: rgb({_TEXT_MAIN.red()}, {_TEXT_MAIN.green()}, {_TEXT_MAIN.blue()});
                border: 1px solid rgb({_BORDER.red()}, {_BORDER.green()}, {_BORDER.blue()});
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: 4px;
            }}
            QListWidget#BugTrackerList::item {{
                background: rgb({_PANEL_BG.red()}, {_PANEL_BG.green()}, {_PANEL_BG.blue()});
                padding: {scale_px(7, min_abs=5)}px {scale_px(6, min_abs=4)}px;
                border-bottom: 1px solid rgb({_BORDER.red()}, {_BORDER.green()}, {_BORDER.blue()});
            }}
            QListWidget#BugTrackerList::item:selected {{
                background: rgb({_MID.red()}, {_MID.green()}, {_MID.blue()});
                color: rgb({_PINK.red()}, {_PINK.green()}, {_PINK.blue()});
            }}
            QPushButton {{
                background: rgb({_PANEL_BG.red()}, {_PANEL_BG.green()}, {_PANEL_BG.blue()});
                color: rgb({_TEXT_MAIN.red()}, {_TEXT_MAIN.green()}, {_TEXT_MAIN.blue()});
                border: 1px solid rgb({_BORDER.red()}, {_BORDER.green()}, {_BORDER.blue()});
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: 0px {scale_px(11, min_abs=9)}px;
                min-height: {scale_px(32, min_abs=28)}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: rgb({_MID.red()}, {_MID.green()}, {_MID.blue()});
                border-color: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
            }}
            QPushButton:pressed {{
                background: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
                color: rgb({_BLACK.red()}, {_BLACK.green()}, {_BLACK.blue()});
            }}
            QPushButton[filterButton="true"] {{
                min-width: {scale_px(66, min_abs=58)}px;
            }}
            QPushButton[filterButton="true"]:checked {{
                color: rgb({_BLACK.red()}, {_BLACK.green()}, {_BLACK.blue()});
            }}
            QPushButton[filterTone="info"]:checked {{
                background: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
                border-color: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
            }}
            QPushButton[filterTone="warn"]:checked {{
                background: rgb({_WARNING.red()}, {_WARNING.green()}, {_WARNING.blue()});
                border-color: rgb({_WARNING.red()}, {_WARNING.green()}, {_WARNING.blue()});
            }}
            QPushButton[filterTone="error"]:checked {{
                background: rgb({_DANGER.red()}, {_DANGER.green()}, {_DANGER.blue()});
                border-color: rgb({_DANGER.red()}, {_DANGER.green()}, {_DANGER.blue()});
            }}
            QPushButton[primary="true"] {{
                background: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
                border-color: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
                color: rgb({_BLACK.red()}, {_BLACK.green()}, {_BLACK.blue()});
            }}
            QPushButton[primary="true"]:hover {{
                background: rgb({_PINK_SOFT.red()}, {_PINK_SOFT.green()}, {_PINK_SOFT.blue()});
                border-color: rgb({_PINK_SOFT.red()}, {_PINK_SOFT.green()}, {_PINK_SOFT.blue()});
            }}
            QSplitter::handle {{
                background: rgb({_BORDER.red()}, {_BORDER.green()}, {_BORDER.blue()});
                width: {scale_px(4, min_abs=3)}px;
            }}
            QSplitter::handle:hover {{
                background: rgb({_CYAN.red()}, {_CYAN.green()}, {_CYAN.blue()});
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: {scale_px(10, min_abs=8)}px;
                margin: 4px 2px 4px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgb({_SOFT_BORDER.red()}, {_SOFT_BORDER.green()}, {_SOFT_BORDER.blue()});
                min-height: {scale_px(28, min_abs=22)}px;
                border-radius: {scale_px(3, min_abs=2)}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgb({_PINK.red()}, {_PINK.green()}, {_PINK.blue()});
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: {scale_px(10, min_abs=8)}px;
                margin: 2px 4px 2px 4px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgb({_SOFT_BORDER.red()}, {_SOFT_BORDER.green()}, {_SOFT_BORDER.blue()});
                min-width: {scale_px(28, min_abs=22)}px;
                border-radius: {scale_px(3, min_abs=2)}px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: rgb({_PINK.red()}, {_PINK.green()}, {_PINK.blue()});
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            """
        )

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._reload_snapshot)

        self.set_embedded_mode(embedded)
        self._reload_watermark_texts()
        self._reload_snapshot()

    def refresh_workbench_theme(self) -> None:
        """Recolor the already-built page after a workbench theme change."""
        old_values = {
            name: globals()[name].getRgb() for name in (
                "_BG", "_HEADER_BG", "_PANEL_BG", "_BORDER", "_SOFT_BORDER",
                "_MID", "_PINK", "_PINK_SOFT", "_CYAN", "_WARNING", "_DANGER",
                "_TEXT_MAIN", "_TEXT_SOFT", "_TEXT_DIM", "_BLACK",
            )
        }
        _set_theme_colors()
        new_values = {name: globals()[name].getRgb() for name in old_values}
        stylesheet = self.styleSheet()
        replacements = []
        for index, name in enumerate(old_values):
            old_rgb = ", ".join(str(value) for value in old_values[name][:3])
            new_rgb = ", ".join(str(value) for value in new_values[name][:3])
            marker = f"__WORKBENCH_THEME_{index}__"
            replacements.append((old_rgb, marker, new_rgb))
        for old_rgb, marker, _new_rgb in replacements:
            stylesheet = stylesheet.replace(old_rgb, marker)
        for _old_rgb, marker, new_rgb in replacements:
            stylesheet = stylesheet.replace(marker, new_rgb)
        self.setStyleSheet(stylesheet)
        self.update()

    def _sync_embedded_presentation(self) -> None:
        if hasattr(self, "_title"):
            self._title.setVisible(not self._embedded)
            self._subtitle.setVisible(not self._embedded)
            self._header_source_button.setVisible(not self._embedded)
            self._header_copy_button.setVisible(not self._embedded)
            self._header_close_button.setVisible(not self._embedded)
            self._header.setFixedHeight(scale_px(42, min_abs=38) if self._embedded else scale_px(58, min_abs=50))
        if hasattr(self, "_root_layout"):
            margin = scale_px(10, min_abs=8) if self._embedded else scale_px(12, min_abs=10)
            self._root_layout.setContentsMargins(margin, margin, margin, margin)
        if hasattr(self, "_watermark_overlay"):
            self._watermark_overlay.setVisible(not self._embedded)

    def _before_standalone_show(self) -> None:
        get_layer_manager().register(self, Layer.PANEL, name='BugTrackerWindow')

    def _after_standalone_hide(self) -> None:
        self._poll_timer.stop()
        try:
            get_layer_manager().unregister(self)
        except Exception:
            pass

    def refresh_workbench_page(self) -> None:
        self._refresh_now()

    def _make_button(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text, self._header)
        btn.setFont(self._ui_bold_font)
        btn.clicked.connect(slot)
        return btn

    def _make_filter_button(self, text: str, level_key: str) -> QPushButton:
        btn = QPushButton(text, self._filter_bar)
        btn.setFont(self._ui_bold_font)
        btn.setCheckable(True)
        btn.setChecked(bool(self._level_filters.get(level_key, True)))
        btn.setProperty("filterButton", True)
        btn.setProperty("filterTone", level_key)
        btn.clicked.connect(lambda checked, key=level_key: self._on_toggle_level_filter(key, checked))
        return btn

    def _make_stat_card(self, title: str) -> QFrame:
        card = QFrame(self)
        card.setObjectName("BugTrackerCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(scale_px(10, min_abs=8), scale_px(8, min_abs=6), scale_px(10, min_abs=8), scale_px(8, min_abs=6))
        layout.setSpacing(scale_px(4, min_abs=2))
        title_label = QLabel(title, card)
        title_label.setFont(self._ui_font)
        title_label.setObjectName("BugTrackerStatTitle")
        value_label = QLabel("-", card)
        value_label.setFont(self._title_font)
        value_label.setObjectName("BugTrackerStatValue")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        card._value_label = value_label  # type: ignore[attr-defined]
        return card

    def _make_panel(self, title: str, widget: QWidget) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("BugTrackerPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(scale_px(6, min_abs=4), scale_px(6, min_abs=4), scale_px(6, min_abs=4), scale_px(6, min_abs=4))
        layout.setSpacing(scale_px(4, min_abs=2))
        label = QLabel(title, frame)
        label.setFont(self._ui_bold_font)
        label.setObjectName("BugTrackerPanelTitle")
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return frame

    def _make_detail_panel(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("BugTrackerPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(scale_px(6, min_abs=4), scale_px(6, min_abs=4), scale_px(6, min_abs=4), scale_px(6, min_abs=4))
        layout.setSpacing(scale_px(4, min_abs=2))
        label = QLabel("详情 / 定位", frame)
        label.setFont(self._ui_bold_font)
        label.setObjectName("BugTrackerPanelTitle")
        layout.addWidget(label)
        layout.addWidget(self._detail, 1)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(scale_px(8, min_abs=6))
        button_row.addWidget(self._make_detail_button("打开源码", self._open_selected_source))
        button_row.addWidget(self._make_detail_button("复制详情", self._copy_selected_detail))
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return frame

    def _make_detail_button(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setFont(self._ui_bold_font)
        btn.clicked.connect(slot)
        return btn

    def _make_export_button(self, text: str) -> QPushButton:
        btn = QPushButton(text, self._filter_bar)
        btn.setFont(self._ui_bold_font)
        btn.setProperty("primary", True)
        btn.clicked.connect(self._export_current_filtered_logs)
        return btn

    def show_centered(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else self.geometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(max(geo.left(), x), max(geo.top(), y))
        self.fade_in()
        self._reload_watermark_texts()
        self._poll_timer.start()

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        try:
            get_layer_manager().unregister(self)
        except Exception:
            pass
        super().closeEvent(event)

    def _refresh_now(self) -> None:
        self._reload_snapshot(force=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        overlay = getattr(self, "_watermark_overlay", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())
            overlay.raise_()

    def _reload_snapshot(self, force: bool = False) -> None:
        snapshot_token = self._store.snapshot_token()
        if not force and snapshot_token == self._snapshot_token:
            return
        self._snapshot_token = snapshot_token
        current = self._selected_record_key
        self._records = self._store.load_all_records()
        self._instances = self._store.list_instances(self._records)
        self._refresh_instance_list()
        self._refresh_stats()
        self._refresh_error_list()
        self._restore_selection(current)
        self._update_status_summary()

    def _reload_watermark_texts(self) -> None:
        payload = load_saved_watermark_payload()
        self._watermark_title_text = "\n".join(payload.get("bug_tracker_title", ("BUG", "TRACKER")))
        self._watermark_hardware_text = "\n".join(payload.get("hardware", ("UnKnow GPU 0.00 GB", "RAM 0.00 GB")))
        self._watermark_meta_text = "\n".join(payload.get("bug_tracker_meta", ("CPU 0C  RAM 0.00 GB", "0x0  x1.0")))
        self._watermark_corner_text = "\n".join(payload.get("bug_tracker_corner", ("UnKnow GPU", "unknown")))
        overlay = getattr(self, "_watermark_overlay", None)
        if overlay is not None:
            overlay.update()

    def _refresh_instance_list(self) -> None:
        previous = self._instance_filter or "all"
        self._instance_list.blockSignals(True)
        try:
            self._instance_list.clear()
            all_item = QListWidgetItem(f"全部实例  [{len(self._records)}]")
            all_item.setData(Qt.UserRole, "all")
            self._instance_list.addItem(all_item)
            for info in self._instances:
                suffix = f"[{info.error_count}]"
                if info.error_count <= 0:
                    suffix = "[0]"
                item = QListWidgetItem(f"{info.instance_label}  {suffix}")
                item.setData(Qt.UserRole, info.instance_id)
                item.setToolTip(info.log_path)
                self._instance_list.addItem(item)
            self._select_instance_row(previous)
            current_item = self._instance_list.currentItem()
            current_key = str(current_item.data(Qt.UserRole) or "") if current_item is not None else ""
            self._instance_filter = "" if current_key in {"", "all"} else current_key
        finally:
            self._instance_list.blockSignals(False)

    def _select_instance_row(self, target: str) -> None:
        for row in range(self._instance_list.count()):
            item = self._instance_list.item(row)
            if item.data(Qt.UserRole) == target:
                self._instance_list.setCurrentRow(row)
                return
        self._instance_list.setCurrentRow(0)

    def _refresh_stats(self) -> None:
        records = self._filtered_records()
        total = len(records)
        today = 0
        fingerprints = Counter()
        modules = Counter()
        now_date = datetime.now().date()
        for record in records:
            dt = record.iso_datetime
            if dt is not None and dt.date() == now_date:
                today += 1
            fingerprints[record.fingerprint] += 1
            modules[self._module_key(record)] += 1
        duplicate_groups = sum(1 for count in fingerprints.values() if count > 1)
        self._card_total._value_label.setText(str(total))  # type: ignore[attr-defined]
        self._card_today._value_label.setText(str(today))  # type: ignore[attr-defined]
        self._card_modules._value_label.setText(str(len(modules)))  # type: ignore[attr-defined]
        self._card_dup._value_label.setText(str(duplicate_groups))  # type: ignore[attr-defined]

    def _refresh_error_list(self) -> None:
        selected = self._selected_record_key
        records = self._filtered_records()
        self._error_list.blockSignals(True)
        try:
            self._error_list.clear()
            for index, record in enumerate(records):
                when = self._format_when(record)
                level = record.level or self._level_name(record.levelno)
                module = self._module_key(record)
                text = f"{when}  [{level}]  {module}  {record.message}"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, index)
                item.setToolTip(record.source_label)
                item.setForeground(self._color_for_level(record.levelno))
                self._error_list.addItem(item)
            self._restore_error_selection_by_key(selected)
        finally:
            self._error_list.blockSignals(False)
        self._show_current_detail()

    def _restore_selection(self, record_key: str) -> None:
        self._restore_error_selection_by_key(record_key)

    def _restore_error_selection_by_key(self, record_key: str) -> None:
        records = self._filtered_records()
        if not record_key:
            if records and self._error_list.currentRow() < 0:
                self._error_list.setCurrentRow(0)
            return
        for row in range(self._error_list.count()):
            item = self._error_list.item(row)
            idx = int(item.data(Qt.UserRole) or 0)
            if 0 <= idx < len(records) and records[idx].unique_key == record_key:
                self._error_list.setCurrentRow(row)
                return
        if records and self._error_list.currentRow() < 0:
            self._error_list.setCurrentRow(0)

    def _on_instance_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        del previous
        self._instance_filter = str(current.data(Qt.UserRole) or "") if current is not None else ""
        if self._instance_filter == "all":
            self._instance_filter = ""
        self._refresh_stats()
        self._refresh_error_list()
        self._update_status_summary()

    def _on_toggle_level_filter(self, level_key: str, checked: bool) -> None:
        self._level_filters[level_key] = bool(checked)
        self._refresh_stats()
        self._refresh_error_list()
        self._update_status_summary()

    def _on_error_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        del previous
        if current is None:
            self._selected_record_key = ""
            self._show_placeholder()
            return
        idx = int(current.data(Qt.UserRole) or 0)
        records = self._filtered_records()
        if 0 <= idx < len(records):
            record = records[idx]
            self._selected_record_key = record.unique_key
            self._detail.setPlainText(self._render_detail(record))
            self._status.setText(record.source_label)
            return
        self._show_placeholder()

    def _show_current_detail(self) -> None:
        current = self._error_list.currentItem()
        if current is None:
            self._show_placeholder()
            return
        idx = int(current.data(Qt.UserRole) or 0)
        records = self._filtered_records()
        if 0 <= idx < len(records):
            record = records[idx]
            self._selected_record_key = record.unique_key
            self._detail.setPlainText(self._render_detail(record))
            self._status.setText(record.source_label)
            return
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self._detail.setPlainText("请选择一条日志记录。左侧可按启动实例筛选。")
        self._update_status_summary()

    def _update_status_summary(self) -> None:
        records = self._filtered_records()
        instance_count = len({record.instance_id for record in self._records})
        if self._selected_record() is not None:
            return
        if self._instance_filter:
            label = next((record.instance_label for record in self._records if record.instance_id == self._instance_filter), self._instance_filter)
            self._status.setText(f"{label} / {len(records)} 条日志 / 共 {instance_count} 个实例")
            return
        self._status.setText(f"已载入 {len(records)} 条日志 / 共 {instance_count} 个启动实例")

    def _filtered_records(self) -> list[BugRecord]:
        records = self._records
        if self._instance_filter:
            records = [record for record in records if record.instance_id == self._instance_filter]
        return [record for record in records if self._record_matches_level_filters(record)]

    def _record_matches_level_filters(self, record: BugRecord) -> bool:
        if record.levelno >= 40:
            return bool(self._level_filters.get("error", True))
        if record.levelno >= 30:
            return bool(self._level_filters.get("warn", True))
        return bool(self._level_filters.get("info", True))

    def _module_key(self, record: BugRecord) -> str:
        return record.module or Path(record.pathname).stem or record.logger or "unknown"

    def _format_when(self, record: BugRecord) -> str:
        dt = record.iso_datetime
        if dt is None:
            return record.timestamp[:19] if record.timestamp else "--"
        return dt.strftime("%H:%M:%S")

    def _level_name(self, levelno: int) -> str:
        if levelno >= 50:
            return "CRITICAL"
        if levelno >= 40:
            return "ERROR"
        if levelno >= 30:
            return "WARN"
        if levelno >= 20:
            return "INFO"
        return "DEBUG"

    def _color_for_level(self, levelno: int) -> QColor:
        if levelno >= 40:
            return QColor(_DANGER)
        if levelno >= 30:
            return QColor(_WARNING)
        return QColor(_CYAN)

    def _render_detail(self, record: BugRecord) -> str:
        parts = [
            f"实例: {record.instance_label or '-'}",
            f"日志文件: {record.log_path or '-'}",
            f"时间: {record.timestamp or '-'}",
            f"级别: {record.level or '-'} ({record.levelno})",
            f"日志器: {record.logger or '-'}",
            f"模块: {self._module_key(record)}",
            f"位置: {record.pathname or '-'}:{record.lineno or 0}",
            f"函数: {record.func_name or '-'}",
            f"进程: {record.process or 0}",
            f"线程: {record.thread_name or '-'}",
            "",
            f"消息: {record.message or '-'}",
        ]
        if record.exception:
            parts.extend(["", "异常堆栈:", record.exception])
        if record.stack_info:
            parts.extend(["", "附加堆栈:", record.stack_info])
        return "\n".join(parts)

    def _selected_record(self) -> BugRecord | None:
        current = self._error_list.currentItem()
        if current is None:
            return None
        idx = int(current.data(Qt.UserRole) or 0)
        records = self._filtered_records()
        if 0 <= idx < len(records):
            return records[idx]
        return None

    def _copy_selected_detail(self) -> None:
        record = self._selected_record()
        if record is None:
            self._status.setText("没有可复制的日志记录")
            return
        QApplication.clipboard().setText(self._render_detail(record))
        self._status.setText("已复制详情")

    def _export_current_filtered_logs(self) -> None:
        records = self._filtered_records()
        desktop_dir = self._resolve_desktop_dir()
        if desktop_dir is None:
            self._status.setText("未找到桌面目录")
            return

        zip_path = desktop_dir / "logs.zip"
        grouped: dict[str, list[BugRecord]] = {}
        order: list[str] = []
        for record in records:
            key = record.instance_id or record.instance_label or "live"
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(record)

        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for instance_id in order:
                    items = grouped.get(instance_id, [])
                    if not items:
                        continue
                    txt_name = f"{self._sanitize_filename(self._instance_file_name(items[0]))}.txt"
                    zf.writestr(txt_name, self._build_export_text(instance_id, items).encode("utf-8"))
            self._status.setText(f"已打包到 {zip_path}")
        except Exception as exc:
            self._status.setText(f"打包失败: {exc}")

    def _open_selected_source(self) -> None:
        record = self._selected_record()
        if record is None:
            self._status.setText("没有选中日志记录")
            return
        path = record.pathname
        if not path:
            self._status.setText("这条历史日志没有结构化源码路径")
            return
        if not os.path.exists(path):
            self._status.setText(f"源码不存在: {path}")
            return

        line = max(1, int(record.lineno or 1))
        editors = (
            shutil.which("code"),
            shutil.which("cursor"),
            shutil.which("code-insiders"),
            shutil.which("subl"),
        )
        for editor in editors:
            if not editor:
                continue
            try:
                subprocess.Popen([editor, "--goto", f"{path}:{line}"])
                self._status.setText(f"已定位: {Path(path).name}:{line}")
                return
            except Exception:
                continue

        try:
            os.startfile(path)  # type: ignore[attr-defined]
            self._status.setText(f"已打开: {Path(path).name}")
        except Exception as exc:
            self._status.setText(f"打开失败: {exc}")

    @staticmethod
    def _resolve_desktop_dir() -> Path | None:
        candidates = [
            Path.home() / "Desktop",
            Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except Exception:
                continue
        return None

    @staticmethod
    def _sanitize_filename(text: str) -> str:
        raw = str(text or "").strip() or "logs"
        cleaned = []
        for ch in raw:
            if ch.isalnum() or ch in ("-", "_", ".", " ", "(", ")"):
                cleaned.append(ch)
            else:
                cleaned.append("_")
        name = "".join(cleaned).strip().strip(".")
        return name or "logs"

    @staticmethod
    def _instance_file_name(record: BugRecord) -> str:
        if record.instance_id:
            path = Path(record.instance_id)
            return path.stem if path.suffix.lower() == ".log" else record.instance_id
        if record.instance_label:
            return record.instance_label
        return "live"

    def _build_export_text(self, instance_id: str, records: list[BugRecord]) -> str:
        instance_label = records[0].instance_label if records else instance_id
        lines = [
            f"实例: {instance_label}",
            f"数量: {len(records)}",
            "",
        ]
        for index, record in enumerate(records, start=1):
            lines.extend([
                f"[{index}] {record.timestamp or '-'} {record.level or self._level_name(record.levelno)} {record.message or '-'}",
                f"来源: {record.source_label or '-'}",
            ])
            if record.exception:
                lines.extend(["异常:", record.exception])
            if record.stack_info:
                lines.extend(["堆栈:", record.stack_info])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
