"""系统托盘菜单 UI 组件。"""

from PyQt5.QtWidgets import (
    QApplication,
    QMenu,
    QProxyStyle,
    QStyle,
    QStyleOptionMenuItem,
)
from PyQt5.QtGui import QColor, QFontMetrics, QPainter
from PyQt5.QtCore import Qt, QSize, QRect
from PyQt5.QtCore import QPropertyAnimation, QEasingCurve

from config.config import UI_THEME, UI
from config.font_config import get_ui_font, get_digit_font
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.script.ui.tooltip_panel import get_tooltip_panel


_TRAY_MENU_STYLE_FLAG = '_fxr_tray_menu_style'


class _TrayMenuHintStyle(QProxyStyle):
    """参考三项提示框绘制逻辑的托盘菜单样式。"""

    def __init__(self, base_style=None, parent=None):
        super().__init__(base_style or QApplication.style())
        if parent is not None:
            self.setParent(parent)

        self._layer = scale_px(2, min_abs=1)
        self._border = self._layer * 2
        self._row_h = scale_px(24, min_abs=1)
        self._pad_x = scale_px(6, min_abs=1)
        self._sep_cyan_h = scale_px(5, min_abs=1)
        self._sep_black_h = scale_px(1, min_abs=1)
        self._text_extra_w = scale_px(16, min_abs=1)
        self._wm_font_extra = scale_px(4, min_abs=1)
        self._wm_side_pad = scale_px(1, min_abs=1)
        self._wm_shift_x = scale_px(2, min_abs=1)
        self._text_shift_x = scale_px(7, min_abs=1)

    def _watermark_font(self, base_font):
        base_px = base_font.pixelSize() if base_font is not None else -1
        target_px = (
            max(1, int(base_px + self._wm_font_extra))
            if base_px and base_px > 0
            else None
        )
        wm_font = get_digit_font(size=target_px)
        wm_font.setBold(True)
        return wm_font

    @staticmethod
    def _is_target_menu(widget) -> bool:
        return isinstance(widget, QMenu) and bool(widget.property(_TRAY_MENU_STYLE_FLAG))

    def _inner_rect(self, widget, fallback_rect: QRect) -> QRect:
        """返回菜单内容绘制区域，统一约束所有行绘制范围。"""
        if isinstance(widget, QMenu):
            w = max(0, widget.width() - self._border * 2)
            h = max(0, widget.height() - self._border * 2)
            return QRect(self._border, self._border, w, h)
        return fallback_rect.adjusted(self._border, 0, -self._border, 0)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_PanelMenu and self._is_target_menu(widget):
            rect = widget.rect() if isinstance(widget, QMenu) else option.rect
            painter.fillRect(rect, UI_THEME['border'])
            painter.fillRect(
                rect.adjusted(self._layer, self._layer, -self._layer, -self._layer),
                UI_THEME['mid'],
            )
            painter.fillRect(
                rect.adjusted(self._border, self._border, -self._border, -self._border),
                UI_THEME['bg'],
            )
            return
        if element == QStyle.PE_FrameMenu and self._is_target_menu(widget):
            rect = widget.rect() if isinstance(widget, QMenu) else option.rect
            if rect.isValid():
                painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), 1), UI_THEME['border'])
                painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), UI_THEME['border'])
                painter.fillRect(QRect(rect.left(), rect.top(), 1, rect.height()), UI_THEME['border'])
                painter.fillRect(QRect(rect.right(), rect.top(), 1, rect.height()), UI_THEME['border'])
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):
        if element != QStyle.CE_MenuItem or not self._is_target_menu(widget):
            super().drawControl(element, option, painter, widget)
            return

        opt = QStyleOptionMenuItem(option)
        item_rect = QRect(opt.rect)
        inner_rect = self._inner_rect(widget, item_rect)

        if opt.menuItemType == QStyleOptionMenuItem.Separator:
            sep_rect = QRect(
                inner_rect.left(),
                item_rect.top(),
                inner_rect.width(),
                self._sep_cyan_h,
            )
            painter.fillRect(sep_rect, UI_THEME['mid'])
            black_y = item_rect.top() + max(0, (self._sep_cyan_h - self._sep_black_h) // 2)
            painter.fillRect(
                QRect(inner_rect.left(), black_y, inner_rect.width(), self._sep_black_h),
                UI_THEME['border'],
            )
            return

        row_rect = QRect(
            inner_rect.left(),
            item_rect.top(),
            inner_rect.width(),
            item_rect.height(),
        )
        row_rect = row_rect.intersected(inner_rect)
        is_selected = bool(opt.state & QStyle.State_Selected)
        is_checked = bool(
            opt.checkType != QStyleOptionMenuItem.NotCheckable and opt.checked
        )
        if is_selected:
            painter.fillRect(row_rect, UI_THEME['mid'])
        elif is_checked:
            painter.fillRect(row_rect, UI_THEME['deep_pink'])

        text_rect = QRect(
            row_rect.left() + self._pad_x + self._text_shift_x,
            row_rect.top(),
            max(0, row_rect.width() - self._pad_x * 2),
            row_rect.height(),
        )

        if is_selected:
            wm_color = QColor(UI_THEME['deep_cyan'])
        elif is_checked:
            wm_color = QColor(UI_THEME['bg'])
        else:
            wm_color = QColor(UI_THEME['deep_pink'])
        wm_left = 'L' if is_checked else 'O'
        wm_rect = QRect(
            row_rect.left() + self._wm_side_pad + self._wm_shift_x,
            row_rect.top(),
            max(0, row_rect.width() - self._wm_side_pad * 2 - self._wm_shift_x),
            row_rect.height(),
        )
        painter.setPen(wm_color)
        painter.setFont(self._watermark_font(opt.font))
        painter.drawText(wm_rect, Qt.AlignLeft | Qt.AlignVCenter, wm_left)

        text_color = QColor(UI_THEME['text'])
        if not (opt.state & QStyle.State_Enabled):
            text_color.setAlpha(140)
        painter.setPen(text_color)
        painter.setFont(opt.font)

        fm = QFontMetrics(opt.font)
        text = fm.elidedText(opt.text, Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignCenter, text)

    def sizeFromContents(self, contents_type, option, size, widget=None):
        if contents_type == QStyle.CT_MenuItem and self._is_target_menu(widget):
            opt = QStyleOptionMenuItem(option)
            if opt.menuItemType == QStyleOptionMenuItem.Separator:
                return QSize(size.width(), self._sep_cyan_h)

            fm = QFontMetrics(opt.font)
            text_w = fm.horizontalAdvance(opt.text)

            item_w = (
                self._border * 2
                + self._pad_x * 2
                + text_w
                + self._text_extra_w
            )
            return QSize(max(size.width(), item_w), max(size.height(), self._row_h))
        return super().sizeFromContents(contents_type, option, size, widget)

    def pixelMetric(self, metric, option=None, widget=None):
        is_target = (widget is None) or self._is_target_menu(widget)
        if metric == QStyle.PM_MenuPanelWidth and is_target:
            return 0
        if metric == QStyle.PM_MenuHMargin and is_target:
            return 0
        if metric == QStyle.PM_MenuVMargin and is_target:
            return 0
        if metric == QStyle.PM_MenuDesktopFrameWidth and is_target:
            return 0
        return super().pixelMetric(metric, option, widget)


class TrayContextMenu(QMenu):
    """托盘右键菜单 UI 组件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer_manager = get_layer_manager()
        self._layer_manager.register(self, Layer.DIALOG, name='TrayContextMenu')
        self._menu_style = None
        self._fading_out = False
        self._allow_hide_once = False
        self._opacity_anim = QPropertyAnimation(self, b'windowOpacity', self)
        self._opacity_anim.setDuration(UI.get('ui_fade_duration', 180))
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)
        self.setContentsMargins(0, 0, 0, 0)
        self.setToolTipsVisible(False)
        self._description = ''
        self.hovered.connect(self._on_action_hovered)
        self.aboutToHide.connect(self._on_about_to_hide)
        self.apply_style()

    def apply_style(self):
        """应用托盘菜单视觉样式。"""
        font = get_ui_font()
        font.setBold(True)
        self.setFont(font)
        self.setProperty(_TRAY_MENU_STYLE_FLAG, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self._menu_style = _TrayMenuHintStyle(self.style(), self)
        self.setStyle(self._menu_style)
        self.setStyleSheet(
            """
            QToolTip {
                background-color: rgb(255, 182, 193);
                color: rgb(0, 0, 0);
                border: 1px solid rgb(0, 0, 0);
                padding: 2px 4px;
            }
            """
        )

    def popup(self, p, action=None):
        # 每次弹出前重置不透明度，避免复用上次淡出结束态。
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = False
        self._description = ''
        self._hide_project_tooltip()
        self.setWindowOpacity(0.0)
        super().popup(p, action)
        self._layer_manager.enforce_burst()
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(apply_ui_opacity(1.0))
        self._opacity_anim.start()

    def _hide_project_tooltip(self) -> None:
        panel = get_tooltip_panel()
        if panel is not None:
            panel.hide_now()

    def _on_action_hovered(self, action) -> None:
        text = ''
        if action is not None and not action.isSeparator():
            text = str(action.toolTip() or action.statusTip() or '').strip()
        self._description = text
        self._hide_project_tooltip()

    def _on_about_to_hide(self) -> None:
        self._description = ''
        self._hide_project_tooltip()

    def hide(self):
        # 统一将菜单隐藏转为淡出动画（包括点空白处关闭、点菜单项后关闭）。
        if self._allow_hide_once or self._fading_out or not self.isVisible():
            super().hide()
            return
        self._publish_fade_particle()
        self._fading_out = True
        self._opacity_anim.stop()
        current_opacity = self.windowOpacity()
        self._opacity_anim.setStartValue(max(0.0, min(1.0, float(current_opacity))))
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.start()

    def _publish_fade_particle(self):
        rect = self.geometry()
        if not rect.isValid():
            return
        get_event_center().publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()),
        }))

    def _on_opacity_anim_finished(self):
        if not self._fading_out:
            return
        self._fading_out = False
        self._allow_hide_once = True
        try:
            super().hide()
        finally:
            self._allow_hide_once = False
            self.setWindowOpacity(apply_ui_opacity(1.0))

    def paintEvent(self, event):
        super().paintEvent(event)
        if not bool(self.property(_TRAY_MENU_STYLE_FLAG)):
            return
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.fillRect(QRect(self.width() - 1, 0, 1, self.height()), UI_THEME['border'])
        painter.end()
