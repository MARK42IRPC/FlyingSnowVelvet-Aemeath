"""命令提示框 - 在输入框正下方显示操作提示和 # 命令实时补全列表

布局：
  - 左上锚点对齐 CommandDialog 的 bottom_left 锚点（+2px 间距）
  - 绘制风格与输入框一致：2px 黑色外框 + 2px 青色中框 + 粉色内背景
  - 文字左对齐，自适应宽度，最大 360px

显示逻辑：
  - 无输入 / 非 # 输入 → 默认提示（3 条静态说明行）
  - # 输入时 → 过滤 # 命令列表，支持 Tab 补全 / ↑↓ 导航 / ←→ 翻页
  - 每页最多 5 条，超出时底部显示页码指示器

本类不再继承 ``QWidget``：模式、过滤结果、翻页、选中行、尺寸与绘制批次都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``CommandHintControl``）里，真实窗口、
透明度动画与指针翻译由后端窗口宿主持有。落位仍由 ``CommandDialog`` 通过锚点事件驱动。
"""

from __future__ import annotations

from config.config import UI
from config.tooltip_config import TOOLTIPS
from lib.core.render.visuals import controls
from lib.core.render.visuals.application_visuals import (
    COMMAND_HINT_DEFAULT_ITEMS,
    COMMAND_HINT_PAGE_SIZE,
    command_hint_side_font_size,
)
from lib.core.render.visuals.types import Point, coerce_point
from lib.script.ui.render_bridge import (
    create_control_host,
    digit_font as get_digit_font,
    place_at_point,
    screen_rect_for_point,
    text_metrics as QtTextMetrics,
    ui_font as get_ui_font,
)
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.render.layers import Layer
from lib.script.ui._particle_helper import publish_click_particle_at
from lib.script.ui.page_turn_buttons import make_page_buttons, update_page_buttons_position


_GAP_Y       = scale_px(2, min_abs=1)   # 与 CommandDialog 的垂直间距（px）

# ── 无输入时显示的默认提示行 ──────────────────────────────────────────
_DEFAULT_HINTS: list[str] = list(COMMAND_HINT_DEFAULT_ITEMS)

_LAYER = int(Layer.PET_UI)


class CommandHintBox:
    """
    命令提示框（右键 UI 组件）。

    - 无输入 / 非 # 输入时：显示三条通用操作提示（静态）
    - # 输入时：实时过滤并展示匹配的 # 命令列表
      · Tab     → 自动补全当前选中命令
      · ↑ ↓    → 切换选中行
      · ← →    → 翻页（游标位于行首 / 行尾时才触发）
    - 跟随 CommandDialog 的 bottom_left 锚点
    - 淡入淡出 + right_fade 粒子消散特效（与其余右键 UI 一致）
    """

    def __init__(self) -> None:
        # ── 字体（粗体）────────────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._side_label_font = get_digit_font(
            size=command_hint_side_font_size(self._font.pixelSize())
        )
        self._text_metrics = QtTextMetrics(
            self._font,
            self._digit_font,
            side_font=self._side_label_font,
        )

        self._control = controls.CommandHintControl(
            self._text_metrics,
            default_items=tuple(_DEFAULT_HINTS),
            page_size=COMMAND_HINT_PAGE_SIZE,
            paint_layer=_LAYER,
            opacity_scale=controls.ui_opacity_scale,
        )
        self._description = TOOLTIPS['command_hint_box']
        self._anchor_point: Point | None = None

        # ── 宿主（真实窗口）────────────────────────────────────────────
        self._host = create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_pointer_move=self._on_pointer_move,
            on_fade_out_finished=self._on_fade_out_done,
            layer=Layer.PET_UI,
            fade_duration_ms=UI['ui_fade_duration'],
            fade_out_duration_ms=UI['ui_fade_duration'],
        )
        self._host._description = self._description

        # ── 翻页按钮 ──────────────────────────────────────────────────
        self._prev_btn, self._next_btn = make_page_buttons(
            lambda: self.turn_page(-1),
            lambda: self.turn_page(1),
        )

        # ── 事件订阅 ──────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME,                    self._on_frame)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE,       self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,   self._on_clickthrough_toggle)

        # 初始内容与尺寸
        self._set_default_mode()
        self._refresh_size()

    # ==================================================================
    # 状态视图（测试与内部逻辑读取的稳定入口）
    # ==================================================================
    @property
    def _mode(self) -> str:
        return self._control.mode

    @_mode.setter
    def _mode(self, value: str) -> None:
        self._control.mode = value

    @property
    def _all_items(self):
        return self._control.all_items

    @_all_items.setter
    def _all_items(self, value) -> None:
        self._control.all_items = list(value)

    @property
    def _selected(self) -> int:
        return self._control.selected

    @_selected.setter
    def _selected(self, value: int) -> None:
        self._control.selected = int(value)

    @property
    def _page(self) -> int:
        return self._control.page

    @_page.setter
    def _page(self, value: int) -> None:
        self._control.page = int(value)

    @property
    def _visible(self) -> bool:
        return self._control.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._control.visible = bool(value)

    @property
    def _anchor_available(self) -> bool:
        return self._control.anchor_available

    @_anchor_available.setter
    def _anchor_available(self, value: bool) -> None:
        self._control.anchor_available = bool(value)

    @property
    def _visual(self):
        return self._control.visual

    def width(self) -> int:
        return self._host.width()

    def height(self) -> int:
        return self._host.height()

    def x(self) -> int:
        return self._host.x()

    def y(self) -> int:
        return self._host.y()

    def isVisible(self) -> bool:
        return bool(self._host.isVisible())

    # ==================================================================
    # 公开接口（由 CommandDialog 调用）
    # ==================================================================

    def update_input(self, text: str) -> None:
        """输入框文本变化时更新提示内容（由 _entry.textChanged 驱动）。"""
        if text.startswith('#'):
            self._set_hash_mode(text[1:])
        else:
            self._set_default_mode()
        self._refresh_size()
        if self._visible and self._anchor_available and self._anchor_point:
            self._update_position()
        self.update()

    def get_completion(self) -> str:
        """
        返回当前选中命令的补全字符串（含 # 前缀和尾部空格）。
        仅 hash 模式且有有效选中项时非空。
        """
        return self._control.completion()

    def navigate(self, direction: int) -> None:
        """上下导航：direction = -1（上）/ +1（下）。"""
        if self._control.navigate(direction):
            self._refresh_size()
            self.update()

    def turn_page(self, direction: int) -> None:
        """翻页：direction = -1（上一页）/ +1（下一页），支持循环翻页。"""
        if self._control.turn_page(direction):
            self._refresh_size()
            if self._visible and self._anchor_available and self._anchor_point:
                self._update_position()
            self.update()

    def fade_in(self) -> None:
        """淡入显示（随 CommandDialog 出现时调用）。"""
        if self._control.visible:
            return
        self._control.visible         = True
        self._control.anchor_available = True
        self._set_default_mode()
        self._refresh_size()
        self._host.show()
        # 申请 CommandDialog 的 bottom_left 锚点
        self._event_center.publish(Event(EventType.UI_CREATE, {
            'window_id': 'command_dialog',
            'anchor_id': 'bottom_left',
            'ui_id':     'command_hint_box',
        }))
        self._animate(1.0)

    def fade_out(self) -> None:
        """淡出隐藏，同时发射 right_fade 粒子（随 CommandDialog 消失时调用）。"""
        if not self._control.visible:
            return
        self._control.visible         = False
        self._control.anchor_available = False
        # right_fade 消散特效（与 CloseButton 等一致）
        rect = self._host.geometry_rect()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type':   'rect',
            'area_data':   (int(rect.x), int(rect.y), int(rect.x) + int(rect.width), int(rect.y) + int(rect.height)),
        }))
        self._animate(0.0, fade_out=True)
        self._prev_btn.hide_btn()
        self._next_btn.hide_btn()

    # ==================================================================
    # 私有：模式切换
    # ==================================================================

    def _set_default_mode(self) -> None:
        self._control.set_default_mode()

    def _set_hash_mode(self, query: str) -> None:
        self._control.set_hash_mode(get_hash_cmd_registry().filter(query))

    def _page_items(self) -> list:
        return self._control.page_items()

    def _has_pages(self) -> bool:
        return self._control.has_pages()

    # ==================================================================
    # 私有：格式化与尺寸
    # ==================================================================

    def _refresh_size(self) -> None:
        """Rebuild the shared visual and apply its resolved window size."""
        visual = self._control.build_visual()
        self._host.apply_size(
            int(visual.size.width),
            int(visual.size.height),
        )

    def _update_position(self) -> None:
        """将自身左上角对齐到 CommandDialog bottom_left + _GAP_Y 偏移。"""
        if not self._anchor_point:
            return
        # 左上锚点对齐 CommandDialog 的 bottom_left 锚点 + _GAP_Y。
        placement = place_at_point(
            (self.width(), self.height()),
            self._anchor_point,
            screen_rect_for_point(point=self._anchor_point, fallback_widget=self._host),
            target_anchor_id='bottom_left',
            self_anchor_id='top_left',
            offset_y=_GAP_Y,
        )
        self._host.move_to(placement.x, placement.y)
        update_page_buttons_position(self, self._prev_btn, self._next_btn, self._has_pages())

    # ==================================================================
    # 私有：动画
    # ==================================================================

    def _animate(self, target: float, *, fade_out: bool = False) -> None:
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=UI['ui_fade_duration'],
            fade_out=fade_out,
        )

    def _on_fade_out_done(self) -> None:
        # 仅在确实处于隐藏状态时才收窗，防止 fade_in 后被错误隐藏
        if not self._control.visible:
            self._host.hide()

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self._host.set_clickthrough(bool(event.data.get('enabled', False)))

    def _on_frame(self, event: Event) -> None:
        if self._visible and self._anchor_available and self._anchor_point:
            self._update_position()

    def _on_anchor_response(self, event: Event) -> None:
        if not self._anchor_available:
            return
        ui_id     = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        if ui_id == 'command_hint_box':
            # CommandDialog 对 bottom_left 请求的直接响应
            new_pt = coerce_point(event.data.get('anchor_point'))
            if new_pt is None:
                return
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

        elif ui_id == 'all' and window_id == 'command_dialog' and anchor_id == 'all':
            # CommandDialog 移动时的全局广播（anchor_point = 其左上角坐标）
            cmd_pos = coerce_point(event.data.get('anchor_point'))
            if cmd_pos is None:
                return
            cmd_h   = UI['cmd_window_height']
            new_pt  = Point(cmd_pos.x, cmd_pos.y + cmd_h)
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def _on_pointer_move(self, event) -> None:
        """鼠标悬停时实时更新高亮行，便于直观点击。"""
        row = self._control.row_at_y(event.local.y)
        if self._control.mode == 'default':
            if row != self._control.selected:
                self._control.selected = row
                self._refresh_size()
                self.update()
        elif self._control.mode == 'hash':
            items = self._page_items()
            if 0 <= row < len(items) and row != self._control.selected:
                self._control.selected = row
                self._refresh_size()
                self.update()

    def _on_pointer(self, event):
        """
        左键点击条目：
        - 默认模式：填充命令前缀
        - # 模式：执行命令（不关闭 UI）
        点击翻页指示器左/右半区翻页。
        """
        particle_id = controls.BUTTON_PARTICLES.get(event.button)
        if particle_id:
            publish_click_particle_at(
                particle_id, int(event.screen.x), int(event.screen.y)
            )

        if self._control.mode == 'default':
            if event.button == controls.BUTTON_LEFT:
                row = self._control.row_at_y(event.local.y)
                if row == 0:
                    self._event_center.publish(Event(EventType.UI_HINT_PICK, {'text': '/'}))
                elif row == 1:
                    self._event_center.publish(Event(EventType.UI_HINT_PICK, {'text': '#'}))
                elif row == 2:
                    self._event_center.publish(Event(EventType.UI_HINT_PICK, {'text': '你好啊,爱弥斯'}))
            return controls.PointerClick()

        if self._control.mode != 'hash':
            return controls.PointerClick()

        items = self._page_items()
        row_index = self._control.row_at_y(event.local.y)
        if row_index < 0 or row_index >= len(items):
            if self._control.page_indicator_contains(event.local.y):
                self.turn_page(-1 if event.local.x < self.width() // 2 else 1)
            return controls.PointerClick()

        if event.button == controls.BUTTON_LEFT:
            # 执行命令：通过事件系统触发（解耦 UI 与命令逻辑）
            name = items[row_index][0]
            self._event_center.publish(Event(EventType.INPUT_HASH, {
                'text': name,
                'raw':  f'#{name}',
            }))

        return controls.PointerClick()

    # ==================================================================
    # 绘制与生命周期
    # ==================================================================

    def _paint_batch(self):
        return self._control.ensure_visual().batch

    def update(self) -> None:
        self._host.update()

    def hide(self) -> None:
        self._host.stop_animation()
        self._control.visible = False
        self._host.hide()

    def close(self) -> None:
        """取消订阅并释放后端窗口。"""
        for event_type, callback in (
            (EventType.FRAME, self._on_frame),
            (EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle),
        ):
            try:
                self._event_center.unsubscribe(event_type, callback)
            except Exception:
                pass
        self._host.cleanup()


__all__ = ["CommandHintBox"]
