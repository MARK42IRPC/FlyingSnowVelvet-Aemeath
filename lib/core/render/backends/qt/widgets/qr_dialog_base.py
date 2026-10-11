"""统一二维码窗口基类（档位 D 宿主）。

本模块原住在 `lib/script/ui/qr_dialog_base.py`，按《render 层边界契约》第 3 节下沉到档位 D。
宿主是 Qt 浮窗（继承档位 D 的 `QtWorkbenchFloatingWindow`），产品事实与档位 A 事实经
seam 注入：

- 屏幕几何 `screen_rect_for_point` 与居中落位 `centered_placement` 走
  `tier_d_seams.screen_rect_for_point` / `tier_d_seams.centered_placement`；
- 绘制实现走 `tier_d_seams.create_draw_backend()`。

产品侧 `lib/script/ui/qr_dialog_base.py` 只做再导出，`BaseQrDialog` 子类（网易云 / 元宝 /
QQ 群）与既有导入面不变。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QEvent, QRect, QPropertyAnimation, QEasingCurve, Qt, QTimer
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QPushButton, QStyle

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.layers import Layer, WindowLayer, get_layer_manager
from lib.core.render.visuals.application_visuals import (
    build_qr_panel_visual,
    decode_panel_image,
    qr_panel_size,
    resolve_qr_panel_layout,
)
from lib.core.render.visuals.image_loader import load_image_resource
from lib.core.render.visuals.resources import ImageResource

get_screen_geometry_for_point = tier_d_seams.screen_rect_for_point


def _centered_placement(self_size, screen):
    """居中落位：优先用产品垫片注入的 `render_bridge.centered_placement`，
    未注入时用档位 D 自带的同源实现（同一份 `visuals/layout.PlacementSpec`）。
    """
    hook = tier_d_seams.hook("qr_dialog_centered_placement")
    if hook is not None:
        return hook(self_size, screen)
    return tier_d_seams.centered_placement(self_size, screen)


def _floating_window_base():
    """档位 D 浮窗外壳基类；未注入时给出同名的空壳（隔离构造时使用）。"""
    hook = tier_d_seams.hook("qr_dialog_floating_base")
    if hook is not None:
        return hook()

    from lib.core.render.backends.qt.widgets.floating_window import QtWorkbenchFloatingWindow

    return QtWorkbenchFloatingWindow


class BaseQrDialog(_floating_window_base()):
    """标准二维码浮窗基类。"""

    def __init__(
        self,
        *,
        title: str,
        status: str,
        action_text: str,
        placeholder_text: str,
        qr_background: bool = True,
        status_font_size: int | None = None,
        status_bold: bool = True,
        window_flags: "int | str | None" = None,
    ) -> None:
        super().__init__()
        if window_flags == "login":
            # 登录浮窗不进任务栏、也不抢焦点：与网易云/元宝扫码窗一致（后者原先各写一份）。
            flags = Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            if hasattr(Qt, "WindowDoesNotAcceptFocus"):
                flags |= Qt.WindowDoesNotAcceptFocus
        elif window_flags is not None:
            flags = int(window_flags)
        else:
            flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(*qr_panel_size())
        get_layer_manager().register(self, WindowLayer.DIALOG)

        self._visible = False
        self._title = str(title or "").strip()
        self._status = str(status or "").strip()
        self._action_text = str(action_text or "").strip()
        self._placeholder_text = str(placeholder_text or "").strip()
        self._qr_background = bool(qr_background)
        self._status_bold = bool(status_bold)
        self._status_font_size = status_font_size
        self._qr_resource: ImageResource | None = None
        self._draw_backend = tier_d_seams.create_draw_backend()

        self._minimize_btn = self.create_floating_window_button(
            self,
            QStyle.SP_TitleBarMinButton,
            "最小化",
            self.minimize_floating_window,
        )
        # 无任务栏入口的工具窗口无法从系统恢复，拖动整块浮窗面板即可移动。
        self.attach_floating_drag_handle(self, cursor=False)

        self._action_btn = QPushButton(self._action_text, self)
        self._action_btn.setFocusPolicy(Qt.NoFocus)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.setAttribute(Qt.WA_Hover, True)
        self._action_btn.installEventFilter(self)
        self._action_btn.clicked.connect(self._on_action_clicked)
        # The child remains the native input adapter; product pixels come from
        # build_qr_panel_visual() in the parent paint pass.
        self._action_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 0px; "
            "color: transparent; padding: 0px; min-width: 0px; min-height: 0px; }"
        )

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI["ui_fade_duration"])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        # QR 登录成功后自动收起的单次定时器；基类提供，子类按需 start/stop。
        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self.hide_dialog)
        self._layout_controls()
        self.install_floating_chrome()

    def _content_rects(self) -> tuple[QRect, QRect, QRect, QRect, QRect]:
        layout = resolve_qr_panel_layout((self.width(), self.height()))

        def _qrect(rect) -> QRect:
            return QRect(
                int(round(rect.x)),
                int(round(rect.y)),
                int(round(rect.width)),
                int(round(rect.height)),
            )

        return tuple(_qrect(rect) for rect in (
            layout.inner_rect,
            layout.title_rect,
            layout.qr_rect,
            layout.status_rect,
            layout.action_rect,
        ))

    def _layout_controls(self) -> None:
        inner, title, _, _, btn_rect = self._content_rects()
        self._action_btn.setGeometry(btn_rect)
        size = scale_px(24, min_abs=22)
        margin = scale_px(8, min_abs=6)
        self._minimize_btn.setGeometry(
            inner.x() + inner.width() - margin - size,
            title.y() + max(0, (title.height() - size) // 2),
            size,
            size,
        )

    def _set_dialog_title(self, title: str | None) -> None:
        if title:
            self._title = str(title).strip()

    def _set_dialog_status(self, status: str | None) -> None:
        if status:
            self._status = str(status).strip()

    def _set_qr_pixmap_from_bytes(self, qr_png: bytes | None, *, clear_when_none: bool) -> None:
        if qr_png:
            resource = decode_panel_image(qr_png, resource_prefix="application-qr")
            if resource is not None:
                self._qr_resource = resource
                return
        if clear_when_none:
            self._qr_resource = None

    def _set_qr_pixmap_from_path(self, image_path: str | Path, *, clear_when_missing: bool = True) -> None:
        candidate = Path(image_path)
        if candidate.exists():
            resource = load_image_resource(candidate)
            if resource is not None:
                self._qr_resource = resource
                return
        if clear_when_missing:
            self._qr_resource = None

    def _center_on_screen(self) -> None:
        cursor_pos = QCursor.pos()
        screen = get_screen_geometry_for_point(point=cursor_pos, fallback_widget=self)
        placement = _centered_placement((self.width(), self.height()), screen)
        self.move(placement.x, placement.y)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _disconnect_fade_out_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass

    def _show_dialog(self) -> None:
        self._center_on_screen()
        if not self._visible:
            self._visible = True
            self._disconnect_fade_out_done()
            self.show()
            self._animate(1.0)
        get_layer_manager().enforce_now()
        self.update()

    def hide_dialog(self) -> None:
        self._auto_close_timer.stop()
        if not self._visible:
            return
        self._visible = False
        self._disconnect_fade_out_done()
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)

    def _before_hide_widget(self) -> None:
        """子类可在真正 hide 前注入行为。"""

    def set_clickthrough(self, enabled: bool) -> None:
        """穿透模式切换：QR 登录浮窗共享的同一条宿主行为。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))

    def restore_soon(self) -> None:
        """下一轮事件循环把窗口重新置前：用于"禁止收起"的浮窗被系统隐藏后的自愈。"""
        QTimer.singleShot(0, self._restore_if_needed)

    def _restore_if_needed(self) -> None:
        if not self._visible:
            return
        try:
            self.show()
            get_layer_manager().bring_to_front(self)
        except Exception:
            return

    def _on_fade_out_done(self) -> None:
        self._disconnect_fade_out_done()
        if not self._visible:
            self._before_hide_widget()
            self.hide()

    def _on_action_clicked(self) -> None:
        self.hide_dialog()

    def _action_visual_state(self) -> str:
        if not self._action_btn.isEnabled():
            return "disabled"
        if self._action_btn.isDown():
            return "pressed"
        if self._action_btn.underMouse():
            return "hover"
        return "normal"

    def eventFilter(self, watched, event) -> bool:
        if watched is self._action_btn and event.type() in {
            QEvent.Enter,
            QEvent.Leave,
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.EnabledChange,
        }:
            self.update()
        return super().eventFilter(watched, event)

    def _visual_status_text(self) -> str:
        return self._status

    def _build_panel_visual(self):
        return build_qr_panel_visual(
            self._title,
            self._visual_status_text(),
            self._placeholder_text,
            self._qr_resource,
            size=(self.width(), self.height()),
            layer=int(Layer.DIALOG),
            status_bold=self._status_bold,
            qr_background=self._qr_background,
            status_font_size=self._status_font_size,
            action_text=self._action_text,
            action_state=self._action_visual_state(),
            action_enabled=self._action_btn.isEnabled(),
        )

    def resizeEvent(self, event) -> None:
        self._layout_controls()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        visual = self._build_panel_visual()
        self._draw_backend.render(visual.batch, painter)
        painter.end()


__all__ = ["BaseQrDialog"]
