"""Confirmation and notice descriptions built with the announcement shell."""

from __future__ import annotations

from lib.core.render.visuals.announcement_chrome import (
    announcement_body,
    announcement_shell_spec,
    announcement_stylesheet,
    get_popup_chrome,
    text_paragraph,
)
from lib.core.render.visuals.window_spec import ButtonSpec, WindowSpec

MESSAGE_BOX_OBJECT_NAME = "WorkbenchConfirmDialog"
DESTRUCTIVE_BUTTON_OBJECT_NAME = "WorkbenchConfirmDestructive"
PRIMARY_BUTTON_OBJECT_NAME = "WorkbenchConfirmPrimary"

ICON_INFORMATION = "information"
ICON_WARNING = "warning"
ICON_QUESTION = "question"
ICON_CRITICAL = "critical"


def message_box_window_spec(
    *,
    title: str,
    text: str,
    informative_text: str = "",
    primary_text: str,
    cancel_text: str | None = None,
    destructive: bool = False,
    icon: str = ICON_INFORMATION,
    modal: bool = True,
) -> WindowSpec:
    """Use one shell for confirmations and one-button notices."""
    primary_name = DESTRUCTIVE_BUTTON_OBJECT_NAME if destructive else PRIMARY_BUTTON_OBJECT_NAME
    primary_role = "danger" if destructive else "primary"
    primary = ButtonSpec(primary_text, "accept", id="primary", object_name=primary_name, role=primary_role)
    actions = (primary,)
    dismiss_action = "accept"
    default_button_id = "primary"
    if cancel_text is not None:
        actions = (ButtonSpec(cancel_text, "cancel", id="cancel"), primary)
        dismiss_action = "cancel"
        default_button_id = "cancel"

    chrome = get_popup_chrome()
    tag = "h2" if informative_text else "p"
    html = text_paragraph(text, tag)
    if informative_text:
        html += text_paragraph(informative_text)
    return announcement_shell_spec(
        object_name=MESSAGE_BOX_OBJECT_NAME,
        title=title,
        source_text={ICON_WARNING: "请留意", ICON_CRITICAL: "操作失败"}.get(icon, "")
        if cancel_text is None else "",
        body=announcement_body(html),
        actions=actions,
        width=chrome.notice_width,
        height=chrome.notice_height,
        close_action=dismiss_action,
        close_tooltip="取消" if cancel_text is not None else "关闭",
        modal=modal,
        delete_on_close=not modal,
        default_button_id=default_button_id,
    )


def message_box_stylesheet(mode: str | None = None) -> str:
    """Shared announcement stylesheet used by every confirmation/notice."""
    return announcement_stylesheet(mode)
