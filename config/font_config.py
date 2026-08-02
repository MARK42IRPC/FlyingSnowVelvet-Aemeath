"""字体配置与数字混排工具。

缩放逻辑统一使用 `config.scale` 的 draw_scale（与其余绘制逻辑一致）：
`font_px = scale_px(design_px)`。

- 默认字体：资源目录内鸿蒙字体
- 数字字体：资源目录内拉海洛字体
"""
from __future__ import annotations

import re as _re
from pathlib import Path as _Path

from config.scale import scale_px

# 设计稿像素字号（统一走 scale_px）
_DEFAULT_FONT_PX = {
    'ui_size': 12,
    'cmd_size': 12,
}

# 当前使用字号（初始化后按 draw_scale 刷新）
FONT = dict(_DEFAULT_FONT_PX)

# 资源字体路径
_FONT_DIR = _Path(__file__).parent.parent / 'resc' / 'FRONTS'
_HARMONY_PATH = str(_FONT_DIR / 'HarmonyOS_Sans_SC_Bold.ttf')
_LAHAI_ROI_PATH = str(_FONT_DIR / 'WuWa Lahai-Roi Regular.ttf')

# 缓存已注册字体族名
_harmony_family: str | None = None
_lahai_roi_family: str | None = None

_DIGIT_RE = _re.compile(r'\d+')
_DIGIT_SPLIT_RE = _re.compile(r'(\d+)')


def _set_scaled_font_defaults() -> None:
    """按全局 draw_scale 写入 FONT 像素字号。"""
    for key, design_px in _DEFAULT_FONT_PX.items():
        FONT[key] = max(9, scale_px(design_px, min_abs=1))


def init_font_config() -> None:
    """公开接口：初始化字体字号配置。"""
    _set_scaled_font_defaults()
    try:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.setFont(get_ui_font())
    except Exception:
        # 字体初始化不能阻断无 Qt 环境下的配置/测试导入。
        pass


def _build_font(family: str, pixel_size: int):
    """构建像素字号字体。"""
    from PyQt5.QtGui import QFont

    font = QFont(family)
    font.setPixelSize(max(1, int(pixel_size)))
    return font


def _register_font_family(font_path: str, fallback_family: str) -> str | None:
    """注册应用内字体并返回字体族名；QApplication 未就绪时返回 None。"""
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QFontDatabase

    if QApplication.instance() is None:
        return None

    path = _Path(font_path)
    if not path.exists():
        return fallback_family

    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id == -1:
        return fallback_family

    families = QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else fallback_family


def _ensure_harmony_os() -> str:
    """返回鸿蒙字体族名（按需注册）。"""
    global _harmony_family
    if _harmony_family is None:
        family = _register_font_family(_HARMONY_PATH, 'Microsoft YaHei')
        if family is not None:
            _harmony_family = family
    return _harmony_family or 'Microsoft YaHei'


def _ensure_lahai_roi() -> str:
    """返回拉海洛字体族名（按需注册）。"""
    global _lahai_roi_family
    if _lahai_roi_family is None:
        family = _register_font_family(_LAHAI_ROI_PATH, _ensure_harmony_os())
        if family is not None:
            _lahai_roi_family = family
    return _lahai_roi_family or _ensure_harmony_os()


def get_ui_font(size: int | None = None):
    """返回 UI 默认字体实例（鸿蒙，像素字号）。"""
    font_size = FONT['ui_size'] if size is None else int(size)
    return _build_font(_ensure_harmony_os(), font_size)


def get_ui_font_family() -> str:
    """返回已注册的鸿蒙 UI 字体族名。"""
    return _ensure_harmony_os()


def apply_ui_font_tree(widget) -> None:
    """统一控件树字体族，同时保留每个控件已有字号、粗细和样式。"""
    from PyQt5.QtWidgets import QWidget

    family = get_ui_font_family()
    for target in (widget, *widget.findChildren(QWidget)):
        font = target.font()
        font.setFamily(family)
        target.setFont(font)


def get_cmd_font(size: int | None = None):
    """返回命令输入字体实例（默认同 UI 字体，像素字号）。"""
    font_size = FONT['cmd_size'] if size is None else int(size)
    return _build_font(_ensure_harmony_os(), font_size)


def get_digit_font(size: int | None = None):
    """返回数字字体实例（拉海洛，像素字号）。"""
    font_size = FONT['ui_size'] if size is None else int(size)
    return _build_font(_ensure_lahai_roi(), font_size)


def get_digit_font_family() -> str:
    """返回已注册的数字字体族名。"""
    return _ensure_lahai_roi()


def _split_digit_segments(text: str) -> list[tuple[str, bool]]:
    """按数字/非数字切分文本，返回 [(segment, is_digit), ...]。"""
    segments: list[tuple[str, bool]] = []
    for seg in _DIGIT_SPLIT_RE.split(text):
        if not seg:
            continue
        segments.append((seg, bool(_DIGIT_RE.fullmatch(seg))))
    return segments


def measure_mixed_text(text: str, default_font, digit_font) -> int:
    """计算混排文本宽度（数字按 digit_font，其余按 default_font）。"""
    from PyQt5.QtGui import QFontMetrics

    if not text:
        return 0

    fm_def = QFontMetrics(default_font)
    fm_dig = QFontMetrics(digit_font)
    total = 0
    for seg, is_digit in _split_digit_segments(text):
        total += (fm_dig if is_digit else fm_def).horizontalAdvance(seg)
    return total


def elide_mixed_text(text: str, max_width: int, default_font, digit_font, mode=None) -> str:
    """按混排宽度裁剪文本，默认右侧省略号。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFontMetrics

    if mode is None:
        mode = Qt.ElideRight

    if max_width <= 0:
        return ''
    if measure_mixed_text(text, default_font, digit_font) <= max_width:
        return text

    # 仅手动处理最常用的右省略；其余模式回退 Qt 默认行为
    if mode != Qt.ElideRight:
        return QFontMetrics(default_font).elidedText(text, mode, max_width)

    ellipsis = '...'
    if measure_mixed_text(ellipsis, default_font, digit_font) > max_width:
        return ''

    kept = ''
    for ch in text:
        probe = kept + ch
        if measure_mixed_text(probe + ellipsis, default_font, digit_font) > max_width:
            break
        kept = probe
    return kept + ellipsis


def wrap_mixed_text(text: str, max_width: int, default_font, digit_font) -> list[str]:
    """按混排宽度对文本自动换行（保留原始 \\n 硬换行）。"""
    from PyQt5.QtGui import QFontMetrics

    if max_width <= 0:
        return ['']

    fm_def = QFontMetrics(default_font)
    fm_dig = QFontMetrics(digit_font)

    def _char_width(ch: str) -> int:
        return (fm_dig if ch.isdigit() else fm_def).horizontalAdvance(ch)

    lines: list[str] = []
    for para in text.split('\n'):
        if para == '':
            lines.append('')
            continue

        cur = ''
        cur_w = 0
        for ch in para:
            ch_w = _char_width(ch)
            if cur and cur_w + ch_w > max_width:
                lines.append(cur)
                cur = ch
                cur_w = ch_w
            else:
                cur += ch
                cur_w += ch_w
        lines.append(cur if cur else '')

    return lines or ['']


def draw_mixed_text(painter, rect, text: str, default_font, digit_font, align=None):
    """在 rect 内绘制 text：数字使用 digit_font，其余字符使用 default_font。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFontMetrics

    if align is None:
        align = Qt.AlignLeft | Qt.AlignVCenter

    fm_def = QFontMetrics(default_font)
    fm_dig = QFontMetrics(digit_font)
    segments = _split_digit_segments(text)
    total_w = sum((fm_dig if is_digit else fm_def).horizontalAdvance(seg) for seg, is_digit in segments)

    if align & Qt.AlignHCenter:
        x = rect.x() + (rect.width() - total_w) / 2.0
    elif align & Qt.AlignRight:
        x = rect.x() + rect.width() - total_w
    else:
        x = rect.x()

    ascent = max(fm_def.ascent(), fm_dig.ascent())
    descent = max(fm_def.descent(), fm_dig.descent())
    y = rect.y() + (rect.height() + ascent - descent) / 2.0

    painter.save()
    painter.setClipRect(rect)
    for seg, is_digit in segments:
        fm = fm_dig if is_digit else fm_def
        painter.setFont(digit_font if is_digit else default_font)
        painter.drawText(int(round(x)), int(round(y)), seg)
        x += fm.horizontalAdvance(seg)
    painter.restore()

    painter.setFont(default_font)
