"""AI 设置面板的整段 QSS（后端中立的描述层）。

`ai_settings_panel.py` 的 `_apply_style()` 原本在自己的方法体里拼一段 236 行的 f-string：
控件 id 选择器、三态配色与像素档全部混在 `QWidget` 子类里，主题键（`qt_color_name`）
与字体族（`ui_font().family()`）都是 Qt 侧取值。批次 3 第三轮把这份**描述**搬到
`visuals/`：它只依赖后端中立的 `UI_THEME` / 工作台 token 与 `scale_px`，与
`workbench_chrome.py` 的浮窗 QSS 同一落点、同一写法。

本轮同时把「支持作者 / 贡献者」两页的内联 QSS 片段从 `lib/script/ui/ai_settings_about.py`
挪到这里——它们是同一张样式表的两个内联块，分开两处会让「整段 QSS 的事实源」出现两个
所有者。`ai_settings_about` 改为从本模块导入并按原名再导出，它的对外面不变。

返回的字符串与搬出前逐字符相同（面板 `styleSheet()` 对照 9450 字符）。

字体族取 `config.font_config.get_ui_font_family()`——它正是面板与工作台主题在同一进程里
返回的族名；滚动条尺寸与下拉框补白由像素档直接算出，不再经 `QFont` 对象。
"""

from __future__ import annotations

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.render.visuals.palette import COLORS, UI_THEME
from lib.core.render.visuals.types import Color
from lib.core.render.visuals.workbench_tokens import get_workbench_tokens, resolve_workbench_mode

#: 「设置面板」像素档：与 `ai_settings_panel._CONFIG_FONT_SIZE` 同源同值。
CONFIG_FONT_SIZE = scale_px(17, min_abs=12)
DROPDOWN_ITEM_FONT_SIZE = max(scale_px(8, min_abs=8), CONFIG_FONT_SIZE - scale_px(2, min_abs=1))
COMBO_DROP_WIDTH = scale_px(32, min_abs=28)


def _hex(color: Color) -> str:
    """把后端中立颜色写成 QSS 用的 `#rrggbb`（`qt_color_name()` 的同一格式）。"""
    return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"

def sponsor_author_fragment(c: dict[str, str]) -> str:
    """「支持作者」页的内联 QSS 片段（以换行开头，供整段样式表内联拼接）。"""
    return f"""
            QWidget#sponsorAuthorCard {{
                background: transparent;
                border: none;
            }}
            QWidget#sponsorAuthorImageFrame {{
                background: {c['surface_raised']};
                border: 1px solid {c['border']};
                border-radius: {scale_px(4, min_abs=3)}px;
            }}
            QLabel#sponsorAuthorImage {{
                background: transparent;
                color: {c['text']};
                padding: {scale_px(6, min_abs=4)}px;
            }}
            QPushButton#sponsorAuthorButton {{
                background: {c['cyan']};
                color: {c['canvas']};
                border: 1px solid {c['cyan']};
                border-radius: {scale_px(4, min_abs=3)}px;
                min-height: {scale_px(34, min_abs=30)}px;
                padding: 0px {scale_px(12, min_abs=10)}px;
                font-weight: 700;
            }}
            QPushButton#sponsorAuthorButton:hover {{
                background: {c['pink_hover']};
                color: {c['canvas']};
                border-color: {c['pink_hover']};
            }}
            QPushButton#sponsorAuthorButton:pressed {{
                background: {c['pink']};
                color: {c['canvas']};
                border-color: {c['pink']};
            }}"""


def contribution_list_fragment(c: dict[str, str]) -> str:
    """「贡献者」页的内联 QSS 片段（以换行开头、以最后一条规则的 `}}` 结尾）。"""
    return f"""
            QPushButton#ContributionCardButton {{
                background: {c['surface_raised']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: {scale_px(4, min_abs=3)}px;
                padding: 0px;
                min-height: {scale_px(66, min_abs=58)}px;
            }}
            QPushButton#ContributionCardButton:hover {{
                background: {c['surface_hover']};
                border-color: {c['cyan']};
            }}
            QPushButton#ContributionCardButton:pressed {{
                background: {c['surface']};
                border-color: {c['pink']};
            }}
            QWidget#ContributionCardAccent {{
                background: {c['cyan']};
                border: none;
                border-radius: {scale_px(1, min_abs=1)}px;
            }}
            QLabel#ContributionCardName {{
                background: transparent;
                color: {c['text']};
            }}
            QLabel#ContributionCardRole {{
                background: transparent;
                color: {c['text_muted']};
            }}"""


def ai_settings_panel_stylesheet(mode: str | None = None) -> str:
    """生成设置面板的整段 QSS。

    `mode` 参与工作台主题判定（与浮窗 QSS 同一条路径）；本段 QSS 目前用一套调色板，
    判定结果保留为将来按主题切换的入口，行为与搬出前一致。
    """
    resolve_workbench_mode(mode)
    main_colors = {name: _hex(value) for name, value in UI_THEME.items()}
    # `qt_color_name()` 的查表顺序是「先 COLORS 后 UI_THEME」，这里必须同序。
    main_colors.update({name: _hex(value) for name, value in COLORS.items()})
    workbench_colors = get_workbench_tokens(mode)
    font_family = str(get_ui_font_family() or "").replace("'", "\\'")
    config_font_size = CONFIG_FONT_SIZE
    dropdown_item_font_size = DROPDOWN_ITEM_FONT_SIZE
    menu_font_size = max(scale_px(12, min_abs=10), CONFIG_FONT_SIZE)
    combo_drop_w = COMBO_DROP_WIDTH
    combo_right_pad = combo_drop_w + scale_px(8, min_abs=6)
    scroll_w = scale_px(14, min_abs=12)
    scroll_handle_min_h = scale_px(28, min_abs=20)
    sponsor_author_fragment_text = sponsor_author_fragment(workbench_colors)
    contribution_list_fragment_text = contribution_list_fragment(workbench_colors)
    return f"""
            QWidget {{
                background: transparent;
                color: {main_colors['text']};
            }}
            QLabel#ConfigSectionLabel {{
                background: {main_colors['deep_cyan']};
                color: {main_colors['text']};
                border: 2px solid {main_colors['border']};
                padding: {scale_px(5, min_abs=4)}px {scale_px(10, min_abs=8)}px;
                min-height: {scale_px(28, min_abs=24)}px;
                font-weight: 700;
            }}
            QLabel#ConfigFormLabel {{
                color: {main_colors['text']};
                padding: 0px {scale_px(4, min_abs=3)}px;
                font-weight: 700;
            }}{sponsor_author_fragment_text}{contribution_list_fragment_text}
            QScrollArea {{
                border: 0px;
                background: transparent;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QMenu {{
                background: {main_colors['bg']};
                color: {main_colors['text']};
                border: 2px solid {main_colors['border']};
                border-radius: 0px;
                padding: {scale_px(3, min_abs=2)}px 0px;
                font-family: '{font_family}';
                font-size: {menu_font_size}px;
                font-weight: 700;
            }}
            QMenu::item {{
                background: {main_colors['bg']};
                color: {main_colors['text']};
                padding: {scale_px(4, min_abs=3)}px {scale_px(18, min_abs=12)}px;
                margin: 0px;
                border: 0px;
            }}
            QMenu::item:selected {{
                background: {main_colors['mid']};
                color: {main_colors['text']};
            }}
            QMenu::item:pressed {{
                background: {main_colors['deep_cyan']};
                color: {main_colors['text']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {main_colors['border']};
                margin: {scale_px(4, min_abs=3)}px {scale_px(8, min_abs=6)}px;
            }}
            QScrollBar:vertical {{
                background: {main_colors['bg']};
                width: {scroll_w}px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {main_colors['mid']};
                border: 1px solid {main_colors['border']};
                min-height: {scroll_handle_min_h}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {main_colors['deep_cyan']};
            }}
            QScrollBar::handle:vertical:pressed {{
                background: {main_colors['text']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
                border: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QLineEdit {{
                background: rgba(255, 255, 255, 128);
                color: {main_colors['text']};
                border: 1px solid {main_colors['border']};
                border-radius: 0px;
                padding: {scale_px(4, min_abs=3)}px {scale_px(8, min_abs=6)}px;
                min-height: {scale_px(28, min_abs=24)}px;
                font-size: {config_font_size}px;
                font-weight: 700;
            }}
            QComboBox {{
                background: rgba(255, 255, 255, 128);
                color: {main_colors['text']};
                border: 1px solid {main_colors['border']};
                border-radius: 0px;
                padding: {scale_px(4, min_abs=3)}px {scale_px(8, min_abs=6)}px;
                padding-right: {combo_right_pad}px;
                min-height: {scale_px(28, min_abs=24)}px;
                font-size: {config_font_size}px;
                font-weight: 700;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: {combo_drop_w}px;
                border-left: 1px solid {main_colors['border']};
                border-top: 0px;
                border-right: 0px;
                border-bottom: 0px;
                border-radius: 0px;
                background: {main_colors['mid']};
            }}
            QComboBox::drop-down:hover {{
                background: {main_colors['deep_cyan']};
            }}
            QComboBox::drop-down:pressed {{
                background: {main_colors['bg']};
            }}
            QComboBox::down-arrow {{
                image: url(resc/ui/combo_down_arrow.svg);
                width: {scale_px(12, min_abs=10)}px;
                height: {scale_px(8, min_abs=6)}px;
            }}
            QComboBox QAbstractItemView {{
                background: {main_colors['bg']};
                color: {main_colors['text']};
                font-size: {dropdown_item_font_size}px;
                font-weight: 700;
                selection-background-color: {main_colors['mid']};
                selection-color: {main_colors['text']};
                border: 1px solid {main_colors['border']};
                border-radius: 0px;
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item {{
                background: {main_colors['bg']};
                color: {main_colors['text']};
                font-size: {dropdown_item_font_size}px;
                font-weight: 700;
                border: 0px;
                border-radius: 0px;
                padding: 4px 8px;
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background: {main_colors['mid']};
                color: {main_colors['text']};
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {main_colors['mid']};
                color: {main_colors['text']};
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item:focus {{
                border: 0px;
                outline: 0px;
            }}
            QPushButton {{
                background: {main_colors['bg']};
                color: {main_colors['text']};
                border: 2px solid {main_colors['border']};
                border-radius: 0px;
                padding: {scale_px(5, min_abs=4)}px {scale_px(12, min_abs=9)}px;
                min-height: {scale_px(32, min_abs=28)}px;
                font-size: {config_font_size}px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {main_colors['mid']};
            }}
            QPushButton:pressed {{
                background: {main_colors['deep_cyan']};
            }}
            QCheckBox {{
                spacing: {scale_px(6, min_abs=4)}px;
                color: {main_colors['text']};
                background: transparent;
                padding: 1px 0px;
            }}
            QCheckBox::indicator {{
                width: {scale_px(18, min_abs=15)}px;
                height: {scale_px(18, min_abs=15)}px;
                border: 1px solid {main_colors['border']};
                border-radius: 0px;
                background: {main_colors['bg']};
            }}
            QCheckBox::indicator:hover {{
                background: {main_colors['mid']};
            }}
            QCheckBox::indicator:checked {{
                border: 1px solid {main_colors['border']};
                border-radius: 0px;
                background: {main_colors['mid']};
            }}
            QCheckBox::indicator:checked:hover {{
                background: {main_colors['deep_cyan']};
            }}
            QSlider::groove:horizontal {{
                border: 1px solid {main_colors['border']};
                background: rgba(255, 255, 255, 128);
                height: {scale_px(8, min_abs=6)}px;
                border-radius: 0px;
            }}
            QSlider::sub-page:horizontal {{
                background: {main_colors['mid']};
                border: 0px;
            }}
            QSlider::add-page:horizontal {{
                background: rgba(255, 255, 255, 128);
                border: 0px;
            }}
            QSlider::handle:horizontal {{
                background: {main_colors['bg']};
                border: 2px solid {main_colors['border']};
                width: {scale_px(13, min_abs=11)}px;
                margin: -5px 0px;
                border-radius: 0px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {main_colors['deep_cyan']};
            }}
            QSlider::handle:horizontal:pressed {{
                background: {main_colors['text']};
            }}
            """

__all__ = [
    "COMBO_DROP_WIDTH",
    "CONFIG_FONT_SIZE",
    "DROPDOWN_ITEM_FONT_SIZE",
    "ai_settings_panel_stylesheet",
    "contribution_list_fragment",
    "sponsor_author_fragment",
]
