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


def get_anchor_point(widget, anchor_id: str):
    """Compatibility wrapper; new QWidget code imports from qt_bridge."""
    from lib.core.qt_bridge.widget_anchors import get_anchor_point as qt_get_anchor_point

    return qt_get_anchor_point(widget, anchor_id)


def get_aligned_position(
    widget,
    target_window,
    target_anchor_id: str,
    self_anchor_id: str = 'top_left',
    offset_x: int = 0,
    offset_y: int = 0,
):
    """Compatibility wrapper; new QWidget code imports from qt_bridge."""
    from lib.core.qt_bridge.widget_anchors import get_aligned_position as qt_get_aligned_position

    return qt_get_aligned_position(
        widget,
        target_window,
        target_anchor_id,
        self_anchor_id,
        offset_x,
        offset_y,
    )


def align_to_anchor(
    widget,
    target_window,
    target_anchor_id: str,
    self_anchor_id: str = 'top_left',
    offset_x: int = 0,
    offset_y: int = 0,
) -> None:
    """Compatibility wrapper; new QWidget code imports from qt_bridge."""
    from lib.core.qt_bridge.widget_anchors import align_to_anchor as qt_align_to_anchor

    qt_align_to_anchor(
        widget,
        target_window,
        target_anchor_id,
        self_anchor_id,
        offset_x,
        offset_y,
    )


def align_to_point(
    widget,
    point,
    self_anchor_id: str = 'top_left',
    offset_x: int = 0,
    offset_y: int = 0,
) -> None:
    """Compatibility wrapper; new QWidget code imports from qt_bridge."""
    from lib.core.qt_bridge.widget_anchors import align_to_point as qt_align_to_point

    qt_align_to_point(widget, point, self_anchor_id, offset_x, offset_y)


def publish_widget_anchor_response(event_center, widget, *, window_id: str, anchor_id: str, ui_id: str) -> None:
    """Compatibility wrapper; new QWidget code imports from qt_bridge."""
    from lib.core.qt_bridge.widget_anchors import publish_widget_anchor_response as qt_publish

    qt_publish(
        event_center,
        widget,
        window_id=window_id,
        anchor_id=anchor_id,
        ui_id=ui_id,
    )


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
