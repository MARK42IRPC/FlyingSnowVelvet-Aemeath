"""办公模式线性图标的"绘制规格"（后端中立）。

图标此前只在 Qt 侧以 `QSvgRenderer` 渲染。真正与工具包无关的是那份 SVG 文本与
呈现尺寸：把它们放到 `visuals/`，让 Qt 宿主（以及将来的 DX 宿主）共用同一份图形事实源。
Qt 侧的「SVG 文本 → `QIcon` / `QPixmap`」在
`lib/core/render/backends/qt/widgets/office_icons.py`，控件经 `render_bridge` 取用。

规格表是内容哈希式的硬编码：名字 → (path 片段, 逻辑尺寸, 最小尺寸)。任何取值变化都会
改变图形，因此 `tests/test_office_icons.py` 把每个名字的成品 SVG 逐字符钉死。
"""

from __future__ import annotations

from config.scale import scale_px

#: name -> (paths, size, min_abs)；顺序即公开契约。
_ICON_SPECS: dict[str, tuple[str, int, int]] = {
    "new": ('<path d="M12 5v14M5 12h14"/>', 15, 13),
    "delete": (
        '<path d="M4 7h16"/>'
        '<path d="M9 7V4h6v3"/>'
        '<path d="M6 7l1 13h10l1-13"/>'
        '<path d="M10 11v5M14 11v5"/>',
        15,
        13,
    ),
    "browse": (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>'
        '<path d="M3 10h18"/>',
        15,
        13,
    ),
    "cancel": ('<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/>', 15, 13),
    "submit": ('<path d="M5 12h14M13 6l6 6-6 6"/>', 15, 13),
    "reject": ('<path d="M6 6l12 12M18 6L6 18"/>', 14, 12),
    "warning": (
        '<path d="M12 4l9 16H3z"/>'
        '<path d="M12 9.5V14"/>'
        '<path d="M12 17v.2"/>',
        24,
        21,
    ),
    "allow": ('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 14, 12),
    "allow_task": (
        '<path d="M4.5 13l3.8 3.8L14.5 10.5"/>'
        '<path d="M9.5 13l3.8 3.8L19.5 10.5"/>',
        14,
        12,
    ),
}

#: 公开的图标名集合；顺序即契约。
OFFICE_ICON_NAMES = tuple(_ICON_SPECS)


def office_icon_size(name: str) -> int:
    """图标的逻辑尺寸（已过 `scale_px`）。"""
    _, size, min_abs = _ICON_SPECS[name]
    return scale_px(size, min_abs=min_abs)


def office_icon_svg(name: str, color: str) -> str:
    """按名字与描边色生成成品 SVG 文本。"""
    paths, _, _ = _ICON_SPECS[name]
    return _line_icon(paths, color)


def _line_icon(paths: str, color: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none">'
        f'<g stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</g></svg>'
    )


__all__ = [
    "OFFICE_ICON_NAMES",
    "office_icon_size",
    "office_icon_svg",
]
