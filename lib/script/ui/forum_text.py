"""论坛卡片正文富文本控件（产品垫片）。

`MarkupText`、四个描边常量与 `bold_outline_width()` 的实现已经下沉到档位 D：
`lib/core/render/backends/qt/widgets/forum_text.py`。这里是按原名再导出的产品垫片，
本身不含 Qt 实现、不 `import PyQt5`——产品页面照旧 `from lib.script.ui.forum_text import ...`
取用，导入面与从前完全一致。

名字由 `render_bridge.forum_text_primitives()` 解析（垫片不得静态 import 档位 D），
文档字符串里的渲染语义说明随实现一起搬到了宿主模块。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    BOLD_OUTLINE_RATIO,
    BOLD_OUTLINE_MIN_PX,
    BOLD_OUTLINE_MAX_PX,
    MESSAGE_OUTLINE_BOLD_GAIN,
    bold_outline_width,
    MarkupText,
) = render_bridge.forum_text_primitives()

__all__ = [
    "BOLD_OUTLINE_MAX_PX",
    "BOLD_OUTLINE_MIN_PX",
    "BOLD_OUTLINE_RATIO",
    "MESSAGE_OUTLINE_BOLD_GAIN",
    "MarkupText",
    "bold_outline_width",
]
