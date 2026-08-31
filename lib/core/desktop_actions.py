"""Backend-neutral actions shared by desktop command controls."""
from __future__ import annotations

from collections.abc import Callable

from config.user_scale_config import get_user_scale_config
from lib.core.event.center import Event, EventType, get_event_center


def _publish_information(text: str, *, maximum: int = 60) -> None:
    get_event_center().publish(Event(EventType.INFORMATION, {
        "text": str(text),
        "min": 0,
        "max": max(1, int(maximum)),
    }))


def adjust_desktop_scale(delta: float) -> float:
    """Persist a desktop scale change and publish the shared user feedback."""
    value = get_user_scale_config().adjust_scale(float(delta))
    _publish_information(f"缩放: {value:.1f}（重启生效）")
    return value


def request_tray_menu() -> None:
    """Ask the selected desktop backend to display its tray menu."""
    get_event_center().publish(Event(EventType.UI_TRAY_MENU_REQUEST, {
        "source": "desktop_action",
    }))


def dispatch_desktop_action(
    action: str,
    *,
    clickthrough_enabled: bool = False,
    chat_listening: bool = False,
    launch_wuwa: Callable[[], object] | None = None,
) -> None:
    """Dispatch one command-panel action without importing a UI toolkit."""
    action = str(action or "").strip().lower()
    center = get_event_center()
    if action == "scale_up":
        adjust_desktop_scale(0.1)
    elif action == "scale_down":
        adjust_desktop_scale(-0.1)
    elif action == "close":
        center.publish(Event(EventType.APP_QUIT, {
            "source": "desktop_action",
            "action": action,
        }))
    elif action == "clickthrough":
        center.publish(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {
            "enabled": not bool(clickthrough_enabled),
            "source": "desktop_action",
        }))
    elif action == "chat_mode":
        center.publish(Event(
            EventType.MIC_STT_STOP if chat_listening else EventType.MIC_STT_START,
            {
                "source": "chat_mode_button",
                "auto_mode": False,
                "auto_submit": True,
                "emit_partial": True,
            },
        ))
    elif action == "interaction_mode":
        center.publish(Event(EventType.INTERACTION_MODE_SET, {
            "toggle": True,
            "source": "desktop_action",
        }))
    elif action == "more_functions":
        request_tray_menu()
    elif action == "launch_wuwa":
        if launch_wuwa is None:
            _publish_information("启动鸣潮服务尚未就绪", maximum=90)
        else:
            launch_wuwa()
    else:
        raise ValueError(f"unknown desktop action: {action or '<empty>'}")


__all__ = [
    "adjust_desktop_scale",
    "dispatch_desktop_action",
    "request_tray_menu",
]
