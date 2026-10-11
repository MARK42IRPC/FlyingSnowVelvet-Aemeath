"""主论坛账号子页面（产品垫片）。

主论坛账号子页面的实现已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/forum_account.py`。这里按原名再导出（经 `render_bridge.tier_d_reexports()` 解析，
垫片本身不得静态 import 档位 D），既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    Qt,
    pyqtSignal,
    QLineEdit,
    QWidget,
    ForumUser,
    format_date,
    format_expiry,
    validate_password,
    validate_username,
    CommunityService,
    ForumSessionStore,
    is_logged_in,
    get_logger,
    ForumAccountSectionsMixin,
    ForumPostRow,
    logger,
    ForumAccountPage,
) = render_bridge.tier_d_reexports("forum_account", (
    "Qt",
    "pyqtSignal",
    "QLineEdit",
    "QWidget",
    "ForumUser",
    "format_date",
    "format_expiry",
    "validate_password",
    "validate_username",
    "CommunityService",
    "ForumSessionStore",
    "is_logged_in",
    "get_logger",
    "ForumAccountSectionsMixin",
    "ForumPostRow",
    "logger",
    "ForumAccountPage",
))

__all__ = [
    "Qt",
    "pyqtSignal",
    "QLineEdit",
    "QWidget",
    "ForumUser",
    "format_date",
    "format_expiry",
    "validate_password",
    "validate_username",
    "CommunityService",
    "ForumSessionStore",
    "is_logged_in",
    "get_logger",
    "ForumAccountSectionsMixin",
    "ForumPostRow",
    "logger",
    "ForumAccountPage",
]
