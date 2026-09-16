"""Shared visual language for the 雪绒论坛 window (workbench tokens only)."""

from __future__ import annotations

from PyQt5.QtGui import QColor

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.forum import FORUM_ACCENTS
from lib.script.ui.forum_markup import visible_text
from lib.script.workbench.theme import get_workbench_colors, window_button_stylesheet

#: 论坛卡片的四档描边配色；档位与核心的 `FORUM_ACCENTS` 同源，不再各写一份。
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


#: 卡片圆角；卡片底纹要按同一个圆角裁剪，所以由这里统一给出。
FORUM_CARD_RADIUS = scale_px(6, min_abs=4)

#: 竖排滚动条的宽度。窗口最小宽度要把它与 `forum_window.SCROLL_GAP` 一起算进去，
#: 所以由这里给出，样式表本体的 `QScrollBar:vertical` 也取这一个值。
FORUM_SCROLLBAR_WIDTH = scale_px(10, min_abs=8)

#: 卡片正文的自适应字号倍率：字越少字越大，但只在这个区间里取。
FORUM_CARD_TEXT_SCALE_RANGE = (1.0, 2.0)
#: 正文字数少于/等于这里的值就取到倍率上限。
FORUM_CARD_TEXT_SHORT_LENGTH = 6
#: 正文字数多于/等于这里的值就回到 1 倍。
FORUM_CARD_TEXT_LONG_LENGTH = 24

#: 卡片底纹的色调比例：底纹颜色 = 主题中性色与卡片 accent 描边色按这个比例混合。
#: 底纹因此带上卡片自己的色调（粉/青/蓝/雪各不相同），不再是统一的一层灰白。
FORUM_TEXTURE_TINT_RATIO = 0.4


def forum_card_text_scale(text: str) -> float:
    """按正文字数给出字号倍率：6 字及以内 2 倍，24 字及以上 1 倍，中间线性过渡。

    卡片是拿来看短句的：字少的卡片放大字号才不至于空一大片，但上限两倍，
    免得一个字撑满整张卡。字数按 `forum_markup.visible_text()` 的可见文字算，
    `**喵**` 这种带标记的短句仍然是 1 个字。
    """
    low, high = FORUM_CARD_TEXT_SCALE_RANGE
    length = len(visible_text(text).strip())
    if length <= FORUM_CARD_TEXT_SHORT_LENGTH:
        return high
    if length >= FORUM_CARD_TEXT_LONG_LENGTH:
        return low
    span = FORUM_CARD_TEXT_LONG_LENGTH - FORUM_CARD_TEXT_SHORT_LENGTH
    return low + (high - low) * (FORUM_CARD_TEXT_LONG_LENGTH - length) / span


def forum_card_text_size(text: str, base: int | None = None) -> int:
    """正文实际字号 = 基准字号（默认 17）× 自适应倍率。"""
    base_size = scale_px(17, min_abs=12) if base is None else int(base)
    return max(1, int(round(base_size * forum_card_text_scale(text))))


def forum_accent_color(accent: str, mode: str | None = None) -> str:
    """返回某档 accent 的描边色；未知档位退回主题粉。"""
    colors = _LIGHT_ACCENT_COLORS if _is_light(mode) else _DARK_ACCENT_COLORS
    return colors.get(str(accent or "").strip().lower(), get_workbench_colors(mode).pink)


def forum_card_text_color(mode: str | None = None) -> str:
    """卡片正文颜色。

    正文颜色写在字符格式里（加粗片段的同色描边要用同一个颜色、样式表管不到富文本片段），
    所以由这里单一给出：卡片构造时取一次，`CONFIG_UPDATED` 换主题时再取一次。
    """
    return get_workbench_colors(mode).text


def _is_light(mode: str | None) -> bool:
    from lib.core.graphics.workbench_tokens import resolve_workbench_mode

    return resolve_workbench_mode(mode) == "light"


def forum_texture_color(mode: str | None = None, accent: str | None = None) -> str:
    """卡片底纹的颜色：主题中性色（深色白 / 浅色黑）掺上卡片 accent。

    深色主题从白出发、浅色主题从黑出发，再按 `FORUM_TEXTURE_TINT_RATIO` 混入 accent，
    底纹于是带上卡片自己的色调，和描边是同一套配色；透明度另有 `CARD_TEXTURE_ALPHA_RANGE`
    约束，所以底纹仍然比描边和正文弱。不带 accent 时退回纯中性色。
    """
    neutral = QColor("#000000" if _is_light(mode) else "#ffffff")
    if not str(accent or "").strip():
        return neutral.name()
    tint = QColor(forum_accent_color(accent, mode))
    if not tint.isValid():
        return neutral.name()
    ratio = max(0.0, min(1.0, FORUM_TEXTURE_TINT_RATIO))
    return QColor(
        round(neutral.red() * (1.0 - ratio) + tint.red() * ratio),
        round(neutral.green() * (1.0 - ratio) + tint.green() * ratio),
        round(neutral.blue() * (1.0 - ratio) + tint.blue() * ratio),
    ).name()


def forum_picker_track_color(mode: str | None = None) -> str:
    """无渐变滑条（明度条之外的兜底）的轨道底色，取主题中框色。"""
    return get_workbench_colors(mode).border_strong


def forum_stylesheet(mode: str | None = None) -> str:
    c = get_workbench_colors(mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    card_radius = FORUM_CARD_RADIUS
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
        QLabel#ForumHeaderNotice {{ color: {c.text_dim}; }}
        QLabel#ForumStatus[tone="warn"] {{ color: {c.pink}; }}
        QLabel#ForumCardMeta {{ color: {c.text_dim}; }}
        QLabel#ForumCardName {{ color: {c.text}; font-weight: 700; }}
        QLabel#ForumCardDeviceTag {{ color: {c.text_dim}; }}
        QTextEdit#ForumCardText {{
            background: transparent;
            border: none;
            color: {c.text};
            selection-background-color: {c.pink};
            selection-color: {c.canvas};
        }}
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
        QToolButton#ForumFormatButton {{
            border-radius: {scale_px(3, min_abs=2)}px;
            border: {border}px solid {c.border};
            background: {c.surface_raised};
            color: {c.text_muted};
            min-width: {scale_px(24, min_abs=21)}px;
            max-width: {scale_px(24, min_abs=21)}px;
            min-height: {scale_px(22, min_abs=19)}px;
            max-height: {scale_px(22, min_abs=19)}px;
            padding: 0px;
        }}
        QToolButton#ForumFormatButton:hover {{
            border-color: {c.cyan};
            color: {c.text};
        }}
        QToolButton#ForumFormatButton:checked {{
            border-color: {c.cyan};
            background: {c.surface_hover};
            color: {c.text};
        }}
        QWidget#ForumColorControl {{ background: transparent; }}
        QFrame#ForumColorPreview {{ border-radius: {scale_px(2, min_abs=2)}px; }}
        QWidget#ForumWindow QScrollBar:vertical {{
            background: {c.canvas};
            width: {FORUM_SCROLLBAR_WIDTH}px;
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
    "FORUM_CARD_RADIUS",
    "FORUM_SCROLLBAR_WIDTH",
    "FORUM_CARD_TEXT_SCALE_RANGE",
    "FORUM_TEXTURE_TINT_RATIO",
    "forum_card_text_scale",
    "forum_card_text_size",
    "forum_card_text_color",
    "forum_texture_color",
    "forum_accent_color",
    "forum_picker_track_color",
    "forum_stylesheet",
]
