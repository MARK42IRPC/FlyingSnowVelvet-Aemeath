"""AI 设置面板 tabs 组装辅助（档位 D 宿主）。"""

from __future__ import annotations

from PyQt5.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
)
from PyQt5.QtWidgets import (
    QButtonGroup,
    QGraphicsOpacityEffect,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtGui import QPainter

from config.scale import scale_px
from lib.core.render.visuals.panel_visuals import build_tab_bar_visual
from lib.core.render.visuals.types import Size
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.layers import get_layer_manager, WindowLayer

#: 分区入场动画的基础时长与相邻分区的错峰步长（毫秒）。
_PAGE_SECTION_DURATION_MS = 180
_PAGE_SECTION_STAGGER_MS = 28


class TabBarWidget(QWidget):
    """自定义标签栏控件，绘制与面板一致的多层边框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer = scale_px(2, min_abs=1)
        self._border = self._layer * 2
        self._draw_backend = tier_d_seams.create_draw_backend()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        visual = build_tab_bar_visual(
            Size(rect.width(), rect.height()),
            inset=self._layer,
        )
        self._draw_backend.render(visual.batch, painter)


def attach_ai_settings_tabs(panel, general_categories) -> None:
    # 获取主题颜色
    mid_color = tier_d_seams.color("mid")
    highlight_color = tier_d_seams.color("deep_cyan")
    text_color = tier_d_seams.color("text")
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
    get_layer_manager().register(
        panel._tab_floating,
        WindowLayer.PANEL,
        z=1,
        name='AISettingsTabBar',
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
    tab_names = ["AI设置"] + [category.tab_title for category in general_categories]
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
    get_layer_manager().enforce_burst()


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
        get_layer_manager().enforce_burst()


def _page_fade_duration() -> int:
    """切页淡入时长：跟面板整体的淡入淡出同一个档，但收得更短一点。

    整页切换比「面板出现」更频繁，用同一个时长会显得拖沓；这里取 60% 并夹在
    90~180 ms 之间，既看得出过渡又不挡手。
    """
    try:
        from config.config import UI

        base = int(UI.get("ui_fade_duration", 180))
    except Exception:
        base = 180
    return max(90, min(180, int(base * 0.6)))


def _page_opacity_effect(page: QWidget) -> QGraphicsOpacityEffect:
    """取（必要时创建）某一页的透明度效果。

    每页各挂一个：切页时只动画新页那一份，不会连带把整个面板（以及它的浮动标签栏）
    一起淡掉。effect 挂在页面上而不是面板上，面板自己的淡入淡出互不干扰。
    """
    effect = page.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        return effect
    effect = QGraphicsOpacityEffect(page)
    page.setGraphicsEffect(effect)
    return effect


def _animate_page_in(panel, page: QWidget) -> None:
    """把新页从半透明淡到完全不透明；没有动画环境时直接落到终值。"""
    effect = _page_opacity_effect(page)
    effect.setOpacity(0.0)
    anim = getattr(panel, "_tab_page_anim", None)
    if anim is None:
        anim = QPropertyAnimation(panel)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        panel._tab_page_anim = anim
    anim.stop()
    # 目标对象与属性必须成对设置，且要在 start() 前完成；只换 targetObject 而不
    # 重新 setPropertyName 时，动画会找不到可写的属性、静默停在起始值。
    anim.setTargetObject(effect)
    anim.setPropertyName(b"opacity")
    anim.setDuration(_page_fade_duration())
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.start()


def _animate_sections_in(page: QWidget) -> None:
    """页面里的分区依次淡入（轻微错峰），让整页出现时有层次而不是「啪」地出现。

    只动画每个 `SettingsSection` 自己的透明度效果：它们互不重叠，错峰 28 ms 在
    200 ms 内全部落位，肉眼看到的是「内容顺次浮现」而不是延迟。
    """
    sections = [
        child
        for child in page.findChildren(QWidget)
        if child.objectName() == "SettingsSection"
    ]
    if not sections:
        return
    group = QParallelAnimationGroup(page)
    for order, section in enumerate(sections[:8]):
        effect = _page_opacity_effect(section)
        effect.setOpacity(0.0)
        anim = QPropertyAnimation(effect, b"opacity")
        # 错峰靠延长时长实现：第 order 个分区的动画时长 = 基础时长 + order × 28 ms，
        # 起始值仍是 0，所以它看起来「晚一点开始淡入」。QPropertyAnimation 没有 delay。
        anim.setDuration(_PAGE_SECTION_DURATION_MS + order * _PAGE_SECTION_STAGGER_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        group.addAnimation(anim)
    # 用 `panel` 持有 group，避免它作为局部变量被回收导致动画停在半途。
    page._section_enter_anim = group
    group.start()


def set_active_ai_settings_tab(panel, index: int, *, animate: bool = True) -> None:
    if not panel._tab_pages:
        return

    target_index = max(0, min(index, len(panel._tab_pages) - 1))
    # 初始页（第一个标签）在装配时就已经可见，所以把「上一次的页」默认成它：
    # 否则用户第一次切页时 `previous_index` 是 None，动画会被当成首次显示跳过，
    # 表现就是「第一次切换没有过渡、之后才有」。
    previous_index = getattr(panel, "_active_tab_index", 0)
    panel._active_tab_index = target_index
    for page_index, page in enumerate(panel._tab_pages):
        if page is not None:
            page.setVisible(page_index == target_index)

    layout_ai_settings_tab_panels(panel)
    if 0 <= target_index < len(panel._tab_pages):
        target_page = panel._tab_pages[target_index]
        target_page.raise_()
        # 只有真的换了页才动画；面板尚未显示时不动画（它自己会整体淡入）。
        if animate and previous_index != target_index:
            _animate_page_in(panel, target_page)
            _animate_sections_in(target_page)
    if panel._tab_floating is not None and panel._tab_floating.isVisible():
        get_layer_manager().enforce_burst()
