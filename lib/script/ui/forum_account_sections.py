"""主论坛账号页区块（产品垫片）。

主论坛账号页区块的实现已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/forum_account_sections.py`。这里按原名再导出（经 `render_bridge.tier_d_reexports()` 解析，
垫片本身不得静态 import 档位 D），既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    Qt,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    scale_px,
    FORUM_DISPLAY_NAME_MAX,
    FORUM_PASSWORD_MIN,
    FORUM_USERNAME_MAX,
    FORUM_USERNAME_MIN,
    session_path,
    SmoothScrollArea,
    configure_font_factory,
    get_ui_font,
    ForumAccountSectionsMixin,
) = render_bridge.tier_d_reexports("forum_account_sections", (
    "Qt",
    "QFrame",
    "QHBoxLayout",
    "QLabel",
    "QLineEdit",
    "QPushButton",
    "QStackedWidget",
    "QToolButton",
    "QVBoxLayout",
    "QWidget",
    "scale_px",
    "FORUM_DISPLAY_NAME_MAX",
    "FORUM_PASSWORD_MIN",
    "FORUM_USERNAME_MAX",
    "FORUM_USERNAME_MIN",
    "session_path",
    "SmoothScrollArea",
    "configure_font_factory",
    "get_ui_font",
    "ForumAccountSectionsMixin",
))

render_bridge.configure_forum_account_sections_font(render_bridge.ui_font)

__all__ = [
    "Qt",
    "QFrame",
    "QHBoxLayout",
    "QLabel",
    "QLineEdit",
    "QPushButton",
    "QStackedWidget",
    "QToolButton",
    "QVBoxLayout",
    "QWidget",
    "scale_px",
    "FORUM_DISPLAY_NAME_MAX",
    "FORUM_PASSWORD_MIN",
    "FORUM_USERNAME_MAX",
    "FORUM_USERNAME_MIN",
    "session_path",
    "SmoothScrollArea",
    "configure_font_factory",
    "get_ui_font",
    "ForumAccountSectionsMixin",
]
