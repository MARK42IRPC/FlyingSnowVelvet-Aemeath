"""Shared UI anchor/alignment and opacity animation helpers."""

import time

from config.config import UI


def _clamp_opacity_value(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    return max(0.0, min(1.0, number))


def _ui_widget_opacity_scale() -> float:
    return _clamp_opacity_value(UI.get('ui_widget_opacity', 1.0))


def apply_ui_opacity(value: float) -> float:
    """Scale a target opacity by the global UI控件透明度设置."""
    base = _clamp_opacity_value(value)
    return _clamp_opacity_value(base * _ui_widget_opacity_scale())


def animate_opacity(anim, opacity_effect, target: float) -> None:
    """Run opacity animation from current opacity to target."""
    target = apply_ui_opacity(target)
    anim.stop()
    anim.setStartValue(opacity_effect.opacity())
    anim.setEndValue(target)
    anim.start()


def refresh_last_activity(owner, visible_attr: str = '_visible', ts_attr: str = '_last_activity_time') -> bool:
    """Update activity timestamp for visible widgets."""
    if not getattr(owner, visible_attr, False):
        return False
    setattr(owner, ts_attr, time.time())
    return True
