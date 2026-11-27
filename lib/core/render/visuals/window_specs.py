"""Window descriptions for every product pop-up, all built on one shared shell.

This module carries no styling of its own: the chrome, palette and QSS come
from ``announcement_chrome``. Each factory only decides *content* (labels,
fields, actions) and *semantics* (what a click means). Sizes come from the
product caller, keeping this description layer free of ``config`` imports.
"""

from __future__ import annotations

from lib.core.render.visuals.announcement_chrome import (
    announcement_approval_body,
    announcement_body,
    announcement_plain_body,
    announcement_shell_spec,
    announcement_update_body,
)
from lib.core.render.visuals.window_spec import (
    ROLE_PRIMARY,
    ButtonSpec,
    WindowSpec,
)

APPROVAL_REJECT = "reject"
APPROVAL_ALLOW = "allow"
APPROVAL_ALLOW_TASK = "allow_task"
HELP_CLOSE = "close"
UPDATE_CLOSE = "update_close"
UPDATE_PRIMARY = "update_primary"
UPDATE_SECONDARY = "update_secondary"
UPDATE_MINIMIZE = "update_minimize"
ANNOUNCEMENT_CLOSE = "announcement_close"
ANNOUNCEMENT_UPDATE = "announcement_update"
ANNOUNCEMENT_SUPPRESS_TODAY = "announcement_suppress_today"
ANNOUNCEMENT_SUPPRESS_FOREVER = "announcement_suppress_forever"
ANNOUNCEMENT_RETRY = "announcement_retry"


def office_approval_window_spec(
    *, title, reason, command_text="", width=0, height=0, **_legacy
) -> WindowSpec:
    """Office permission prompt: same window as every other product pop-up."""

    return announcement_shell_spec(
        object_name="OfficeApprovalDialog",
        title="办公权限许可",
        title_text=str(title or "执行受限操作"),
        source_text="办公模式 · 权限许可",
        body=announcement_approval_body(str(reason), str(command_text)),
        actions=(
            ButtonSpec("拒绝", APPROVAL_REJECT, id="reject"),
            ButtonSpec("允许", APPROVAL_ALLOW, id="allow", role=ROLE_PRIMARY),
            ButtonSpec("始终允许", APPROVAL_ALLOW_TASK, id="allow_task"),
        ),
        width=int(width),
        height=int(height),
        close_action=APPROVAL_REJECT,
        close_tooltip="关闭（视为拒绝）",
        modal=True,
        delete_on_close=True,
        default_button_id="reject",
    )


def help_window_spec(
    *, title, text, width=0, height=0,
    empty_text="这一项暂时还没有补充说明。", **_legacy,
) -> WindowSpec:
    """Help pop-up: plain-text body, shared chrome."""

    body_text = str(text or "").strip() or str(empty_text)
    return announcement_shell_spec(
        object_name="DesktopPetHelpDialog",
        title=str(title or "帮助"),
        source_text="HELP  /  FSV",
        body=announcement_plain_body(body_text),
        width=int(width),
        height=int(height),
        close_action=HELP_CLOSE,
        close_tooltip="关闭帮助",
        hide_on_actions=(HELP_CLOSE,),
    )


def announcement_window_spec(*, title, html, width=0, height=0, **_legacy) -> WindowSpec:
    """The announcement pop-up: where the shared visual language comes from."""

    return announcement_shell_spec(
        object_name="DesktopPetAnnouncementDialog",
        title="桌宠公告",
        title_text=str(title or "桌宠公告"),
        source_text="SYSTEM BROADCAST  /  FSV",
        channel_text="REMOTE CHANNEL  /  01",
        body=announcement_body(html),
        actions=(
            ButtonSpec("关闭", ANNOUNCEMENT_CLOSE, id="error_close"),
            ButtonSpec("重新加载", ANNOUNCEMENT_RETRY, id="retry"),
            ButtonSpec("今日不再显示", ANNOUNCEMENT_SUPPRESS_TODAY, id="today", role=ROLE_PRIMARY),
            ButtonSpec("永远不再显示", ANNOUNCEMENT_SUPPRESS_FOREVER, id="forever"),
            ButtonSpec("立即更新", ANNOUNCEMENT_UPDATE, id="update"),
        ),
        width=int(width),
        height=int(height),
        close_action=ANNOUNCEMENT_CLOSE,
        close_tooltip="关闭公告",
        minimize_action=ANNOUNCEMENT_CLOSE,
        hide_on_actions=(
            ANNOUNCEMENT_CLOSE, ANNOUNCEMENT_SUPPRESS_TODAY,
            ANNOUNCEMENT_SUPPRESS_FOREVER, ANNOUNCEMENT_UPDATE,
        ),
        non_closing_actions=(ANNOUNCEMENT_RETRY,),
    )


def update_window_spec(
    *, status="", detail="", width=0, height=0, **_legacy
) -> WindowSpec:
    """Update pop-up: same chrome, stacked status/detail/progress body."""

    return announcement_shell_spec(
        object_name="DesktopPetUpdateDialog",
        title="桌宠更新",
        source_text="SYSTEM UPDATE  /  FSV",
        body=announcement_update_body(str(status), str(detail)),
        actions=(
            ButtonSpec("", UPDATE_SECONDARY, id="secondary"),
            ButtonSpec("", UPDATE_PRIMARY, id="primary", role=ROLE_PRIMARY),
        ),
        width=int(width),
        height=int(height),
        close_action=UPDATE_CLOSE,
        close_tooltip="关闭更新",
        minimize_action=UPDATE_MINIMIZE,
        non_closing_actions=(UPDATE_CLOSE, UPDATE_MINIMIZE, UPDATE_PRIMARY, UPDATE_SECONDARY),
    )


__all__ = [
    "ANNOUNCEMENT_CLOSE",
    "ANNOUNCEMENT_RETRY",
    "ANNOUNCEMENT_SUPPRESS_FOREVER",
    "ANNOUNCEMENT_SUPPRESS_TODAY",
    "ANNOUNCEMENT_UPDATE",
    "APPROVAL_ALLOW",
    "APPROVAL_ALLOW_TASK",
    "APPROVAL_REJECT",
    "HELP_CLOSE",
    "UPDATE_CLOSE",
    "UPDATE_MINIMIZE",
    "UPDATE_PRIMARY",
    "UPDATE_SECONDARY",
    "announcement_window_spec",
    "help_window_spec",
    "office_approval_window_spec",
    "update_window_spec",
]
