"""Product-side entry points for shared workbench confirmations and notices."""

from lib.core.render.visuals.message_box import (
    DESTRUCTIVE_BUTTON_OBJECT_NAME,
    ICON_CRITICAL,
    ICON_INFORMATION,
    ICON_QUESTION,
    ICON_WARNING,
    MESSAGE_BOX_OBJECT_NAME as CONFIRM_DIALOG_OBJECT_NAME,
    PRIMARY_BUTTON_OBJECT_NAME,
)
from lib.script.ui.render_bridge import ask_confirmation, show_message
from lib.script.ui.render_bridge import message_box_stylesheet as confirm_dialog_stylesheet

__all__ = [
    "CONFIRM_DIALOG_OBJECT_NAME",
    "DESTRUCTIVE_BUTTON_OBJECT_NAME",
    "ICON_CRITICAL",
    "ICON_INFORMATION",
    "ICON_QUESTION",
    "ICON_WARNING",
    "PRIMARY_BUTTON_OBJECT_NAME",
    "ask_confirmation",
    "confirm_dialog_stylesheet",
    "show_message",
]
