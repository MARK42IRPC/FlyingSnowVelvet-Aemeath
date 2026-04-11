"""AI 设置面板 tabs 组装辅助。"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt, pyqtSignal, QRect
from PyQt5.QtWidgets import QTabBar, QWidget, QVBoxLayout, QPushButton, QButtonGroup
from PyQt5.QtGui import QPainter, QColor, QBrush

from config.scale import scale_px
from config.config import UI_THEME


class TabBarWidget(QWidget):
    """自定义标签栏控件，绘制与面板一致的多层边框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer = scale_px(2, min_abs=1)
        self._border = self._layer * 2

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        layer = self._layer
        border = self._border
        width = rect.width()
        height = rect.height()

        # 1. 粉色背景：左边、上边、下边内缩 border，右边延伸到边缘
        painter.fillRect(
            QRect(border, border, width - border, height - 2 * border),
            UI_THEME["bg"]
        )

        # 2. 浅青色边框：完全包围，厚度 layer
        # 左边：x=0, y=layer, 宽度=layer, 高度=height-2*layer
        painter.fillRect(QRect(0, layer, layer, height - 2 * layer), UI_THEME["mid"])
        # 上边：x=border, y=0, 宽度=width-border, 高度=layer
        painter.fillRect(QRect(border, 0, width - border, layer), UI_THEME["mid"])
        # 下边：x=border, y=height-layer, 宽度=width-border, 高度=layer
        painter.fillRect(QRect(border, height - layer, width - border, layer), UI_THEME["mid"])
        # 右边：x=width-layer, y=layer, 宽度=layer, 高度=height-2*layer
        painter.fillRect(QRect(width - layer, layer, layer, height - 2 * layer), UI_THEME["mid"])

        # 3. 黑色边框：左、上、下三边，厚度 layer
        # 左边：x=0, y=0, 宽度=layer, 高度=height
        painter.fillRect(QRect(0, 0, layer, height), UI_THEME["border"])
        # 上边：x=border, y=0, 宽度=width-border, 高度=layer
        painter.fillRect(QRect(border, 0, width - border, layer), UI_THEME["border"])
        # 下边：x=border, y=height-layer, 宽度=width-border, 高度=layer
        painter.fillRect(QRect(border, height - layer, width - border, layer), UI_THEME["border"])


def attach_ai_settings_tabs(panel, general_categories: list[dict]) -> None:
    # 获取主题颜色
    border_color = UI_THEME["border"]
    bg_color = UI_THEME["bg"]
    mid_color = UI_THEME["mid"]
    highlight_color = UI_THEME["deep_cyan"]
    text_color = UI_THEME["text"]
    # 高亮向左扩展距离
    highlight_expand_left = scale_px(6, min_abs=4)

    panel._tab_pages = [panel._ai_panel]
    for category in general_categories:
        page = panel._build_config_category_panel(category)
        page.hide()
        panel._tab_pages.append(page)

    # 创建独立的垂直标签栏窗口（放在面板外部）
    panel._tab_floating = TabBarWidget(
        panel.parent() if panel.parent() else None,
    )
    panel._tab_floating.setWindowFlags(
        Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
    )
    panel._tab_floating.setAttribute(Qt.WA_ShowWithoutActivating)

    # 设置标签栏容器宽度（背景和边框由 paintEvent 绘制）
    panel._tab_floating.setFixedWidth(scale_px(108, min_abs=88))

    # 创建垂直布局
    tab_layout = QVBoxLayout(panel._tab_floating)
    tab_layout.setContentsMargins(0, scale_px(8), 0, scale_px(8))
    tab_layout.setSpacing(scale_px(2))

    # 创建按钮组
    panel._tab_button_group = QButtonGroup(panel._tab_floating)
    panel._tab_button_group.setExclusive(True)

    # 创建标签按钮
    tab_names = ["AI设置"] + [str(category["tab"]) for category in general_categories]
    panel._tab_buttons = []

    for i, name in enumerate(tab_names):
        btn = QPushButton(name, panel._tab_floating)
        btn.setCheckable(True)
        btn.setFixedHeight(scale_px(32, min_abs=28))

        # 设置按钮样式 - 垂直标签栏
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {text_color.name()};
                border: none;
                font-weight: bold;
                text-align: center;
                padding: 0;
                margin: 0px;
                min-height: {scale_px(32, min_abs=28)}px;
            }}
            QPushButton:checked {{
                background-color: {mid_color.name()};
                margin-left: -{highlight_expand_left}px;
                padding-left: {highlight_expand_left}px;
            }}
            QPushButton:hover {{
                background-color: {highlight_color.name()};
            }}
        """)

        btn.clicked.connect(lambda checked, idx=i: panel._on_top_tab_changed(idx))
        panel._tab_button_group.addButton(btn, i)
        tab_layout.addWidget(btn)
        panel._tab_buttons.append(btn)

    # 默认选中第一个标签
    if panel._tab_buttons:
        panel._tab_buttons[0].setChecked(True)

    tab_layout.addStretch(1)

    layout_ai_settings_tab_bar(panel)
    layout_ai_settings_tab_panels(panel)


def layout_ai_settings_tab_bar(panel) -> None:
    if not hasattr(panel, "_tab_floating") or panel._tab_floating is None:
        return

    # 计算标签栏位置：右上角对齐面板的左上角
    panel_global_pos = panel.mapToGlobal(QPoint(0, 0))

    # 标签栏宽度固定
    tab_width = panel._tab_floating.width()

    # 标签栏的右上角 (x + width) 应该对齐面板的左上角 x
    # 所以标签栏的 x = 面板x - 标签栏宽度
    # 向左偏移2像素更靠近面板
    tab_x = panel_global_pos.x() - tab_width - scale_px(2, min_abs=1)
    tab_y = panel_global_pos.y()

    # 设置标签栏位置和高度
    tab_height = panel.height()
    panel._tab_floating.setGeometry(tab_x, tab_y, tab_width, tab_height)


def show_ai_settings_tab_bar(panel) -> None:
    if panel._tab_floating is None:
        return
    layout_ai_settings_tab_bar(panel)
    panel._tab_floating.show()
    panel._tab_floating.raise_()


def hide_ai_settings_tab_bar(panel) -> None:
    if panel._tab_floating is None:
        return
    panel._tab_floating.hide()


def layout_ai_settings_tab_panels(panel) -> None:
    if not hasattr(panel, "_ai_panel") or panel._ai_panel is None:
        return

    geometry = panel._ai_panel.geometry()
    for page in panel._tab_pages[1:]:
        if page is None:
            continue
        page.setGeometry(geometry)
    if panel._tab_floating is not None and panel._tab_floating.isVisible():
        panel._tab_floating.raise_()


def set_active_ai_settings_tab(panel, index: int) -> None:
    if not panel._tab_pages:
        return

    target_index = max(0, min(index, len(panel._tab_pages) - 1))
    for page_index, page in enumerate(panel._tab_pages):
        if page is not None:
            page.setVisible(page_index == target_index)

    layout_ai_settings_tab_panels(panel)
    if 0 <= target_index < len(panel._tab_pages):
        panel._tab_pages[target_index].raise_()
    if panel._tab_floating is not None and panel._tab_floating.isVisible():
        panel._tab_floating.raise_()
