"""Shared visual language for the 雪绒论坛 window (workbench tokens only)."""

from __future__ import annotations

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.script.workbench.theme import get_workbench_colors, window_button_stylesheet

#: 论坛卡片的四档描边配色，与 `/api/feed` 的 accent 字段一一对应。
FORUM_ACCENTS = ("pink", "cyan", "blue", "snow")
FORUM_ACCENT_LABELS = {
    "pink": "粉",
    "cyan": "青",
    "blue": "蓝",
    "snow": "雪",
}

#: 工作台令牌里没有独立蓝/雪两色，这里按明暗主题补一组同基调配色。
_DARK_ACCENT_COLORS = {
    "pink": "#ff95bc",
    "cyan": "#8cd2ff",
    "blue": "#9db4ff",
    "snow": "#dfe6f2",
}
_LIGHT_ACCENT_COLORS = {
    "pink": "#e0699a",
    "cyan": "#4a9fd8",
    "blue": "#5b74c9",
    "snow": "#8e9bb0",
}


def forum_accent_color(accent: str, mode: str | None = None) -> str:
    """返回某档 accent 的描边色；未知档位退回主题粉。"""
    colors = _LIGHT_ACCENT_COLORS if _is_light(mode) else _DARK_ACCENT_COLORS
    return colors.get(str(accent or "").strip().lower(), get_workbench_colors(mode).pink)


def _is_light(mode: str | None) -> bool:
    from lib.core.graphics.workbench_tokens import resolve_workbench_mode

    return resolve_workbench_mode(mode) == "light"


def forum_stylesheet(mode: str | None = None) -> str:
    c = get_workbench_colors(mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    card_radius = scale_px(6, min_abs=4)
    control_height = scale_px(32, min_abs=28)
    accent_rules = "\n        ".join(
        f'QFrame#ForumCard[accent="{accent}"] {{ border: {border}px solid '
        f"{forum_accent_color(accent, mode)}; }}"
        for accent in FORUM_ACCENTS
    )
    swatch_rules = "\n        ".join(
        f'QToolButton#ForumAccentSwatch[accent="{accent}"] {{ background: '
        f"{forum_accent_color(accent, mode)}; }}"
        for accent in FORUM_ACCENTS
    )
    return f"""
        QWidget#ForumWindow {{
            background: {c.canvas};
            border: {border}px solid {c.border_strong};
            color: {c.text};
            font-family: '{font_family}';
        }}
        QWidget#ForumWindow QWidget {{ font-family: '{font_family}'; }}
        QWidget#ForumWindow QLabel {{ background: transparent; color: {c.text}; }}
        QFrame#ForumHeader {{
            background: {c.surface};
            border: none;
            border-bottom: {border}px solid {c.border};
        }}
        QLabel#ForumTitle {{
            color: {c.text};
            font-weight: 700;
        }}
        QLabel#ForumSubtitle, QLabel#ForumStatus, QLabel#ForumHint {{
            color: {c.text_muted};
        }}
        QLabel#ForumCardMeta {{ color: {c.text_dim}; }}
        QLabel#ForumCardName {{ color: {c.text}; font-weight: 700; }}
        QLabel#ForumCardText {{ color: {c.text}; }}
        QScrollArea#ForumScroll {{
            background: transparent;
            border: none;
        }}
        QScrollArea#ForumScroll > QWidget#qt_scrollarea_viewport {{
            background: transparent;
        }}
        QWidget#ForumColumnHost, QWidget#ForumColumn {{ background: transparent; }}
        QFrame#ForumCard {{
            background: {c.surface_raised};
            border-radius: {card_radius}px;
        }}
        {accent_rules}
        QFrame#ForumComposer {{
            background: {c.surface};
            border: none;
            border-top: {border}px solid {c.border};
        }}
        QWidget#ForumWindow QLineEdit {{
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            padding: {scale_px(6, min_abs=5)}px {scale_px(9, min_abs=7)}px;
            selection-background-color: {c.pink};
        }}
        QWidget#ForumWindow QLineEdit:focus {{ border-color: {c.cyan}; }}
        QWidget#ForumWindow QPushButton {{
            min-height: {control_height}px;
            padding: 0px {scale_px(14, min_abs=11)}px;
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            font-weight: 600;
        }}
        QWidget#ForumWindow QPushButton:hover {{
            background: {c.surface_hover};
            border-color: {c.cyan};
        }}
        QWidget#ForumWindow QPushButton:disabled {{
            color: {c.text_dim};
            border-color: {c.border};
            background: {c.surface};
        }}
        QPushButton#ForumSend {{
            background: {c.pink};
            color: {c.canvas};
            border-color: {c.pink};
        }}
        QPushButton#ForumSend:hover:enabled {{
            background: {c.pink_hover};
            border-color: {c.pink_hover};
        }}
        QPushButton#ForumSend:disabled {{
            background: {c.surface_raised};
            color: {c.text_dim};
            border-color: {c.border};
        }}
        QToolButton#ForumAccentSwatch {{
            border-radius: {scale_px(3, min_abs=2)}px;
            border: {border}px solid {c.border};
            color: rgba(0, 0, 0, 0.68);
            font-weight: 700;
            min-width: {scale_px(22, min_abs=19)}px;
            min-height: {scale_px(22, min_abs=19)}px;
            max-width: {scale_px(22, min_abs=19)}px;
            max-height: {scale_px(22, min_abs=19)}px;
            padding: 0px;
        }}
        QToolButton#ForumAccentSwatch:checked {{
            border: {scale_px(2, min_abs=2)}px solid {c.text};
        }}
        {swatch_rules}
        QWidget#ForumWindow QScrollBar:vertical {{
            background: {c.canvas};
            width: {scale_px(10, min_abs=8)}px;
            border: none;
        }}
        QWidget#ForumWindow QScrollBar::handle:vertical {{
            background: {c.border_strong};
            min-height: {scale_px(26, min_abs=22)}px;
            border-radius: {scale_px(3, min_abs=2)}px;
        }}
        QWidget#ForumWindow QScrollBar::handle:vertical:hover {{ background: {c.pink}; }}
        QWidget#ForumWindow QScrollBar::add-line:vertical,
        QWidget#ForumWindow QScrollBar::sub-line:vertical {{
            height: 0px;
            border: none;
        }}
        QWidget#ForumWindow QScrollBar::add-page:vertical,
        QWidget#ForumWindow QScrollBar::sub-page:vertical {{ background: transparent; }}
    """ + window_button_stylesheet(mode)


__all__ = [
    "FORUM_ACCENTS",
    "FORUM_ACCENT_LABELS",
    "forum_accent_color",
    "forum_stylesheet",
]
