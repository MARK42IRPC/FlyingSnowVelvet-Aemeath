"""音响搜索结果框

跟随 SpeakerSearchDialog 底部显示音乐搜索结果列表。

布局：
  - 左上角对齐 SpeakerSearchDialog 的 bottom_left 锚点（+2px 间距）
  - 配色与搜索框一致：2px 黑外框 + 2px 灰白中框 + 棕色背景 + 纯白字体
  - 每页最多 5 条，超出时底部显示翻页指示器
  - 悬停高亮 + 左键点击立即播放

本类不再继承 ``QWidget``：列表数据、翻页、选中行与绘制批次都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``SearchResultListControl``）里，真实窗口、
透明度动画、指针翻译与拖动/点击意图由后端窗口宿主持有。位置由 ``SpeakerSearchDialog``
通过锚点事件驱动。
"""

from __future__ import annotations

from config.config import UI, SPEAKER_SEARCH_UI
from config.tooltip_config import TOOLTIPS
from lib.core.render.visuals import controls
from lib.core.render.visuals.media_panel_visuals import SEARCH_RESULT_PAGE_SIZE
from lib.core.render.visuals.types import Point
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
from lib.core.render.layers import Layer
from lib.script.ui._particle_helper import publish_click_particle_at
from lib.script.ui.page_turn_buttons import make_page_buttons, update_page_buttons_position


# ── 布局常量 ──────────────────────────────────────────────────────────
_GAP_Y     = scale_px(2, min_abs=1)   # 与搜索框的垂直间距（px）
_DIALOG_H  = SPEAKER_SEARCH_UI.get('height', scale_px(36, min_abs=1))  # 搜索框高度
_LAYER     = int(Layer.PET_UI)


class SpeakerSearchResultBox:
    """
    音响搜索结果展示框。

    - 显示音乐抽象层返回的歌曲列表（最多 15 条，一页 5 条）
    - 悬停高亮，左键单击立即播放并关闭 UI
    - 跟随 SpeakerSearchDialog 移动（通过 UI_ANCHOR_RESPONSE 事件）
    """

    def __init__(self) -> None:
        # ── 字体与描述层 ────────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._text_metrics = QtTextMetrics(self._font, self._digit_font)
        self._control = controls.SearchResultListControl(
            self._text_metrics,
            page_size=SEARCH_RESULT_PAGE_SIZE,
            paint_layer=_LAYER,
            opacity_scale=controls.ui_opacity_scale,
        )
        self._description = TOOLTIPS['speaker_search_result_box']

        # ── 宿主（真实窗口）─────────────────────────────────────────
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

        self._anchor_point: Point | None = None

        # ── 翻页按钮 ──────────────────────────────────────────────────
        self._prev_btn, self._next_btn = make_page_buttons(
            lambda: self.turn_page(-1),
            lambda: self.turn_page(1),
        )

        # ── 事件订阅 ─────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE,
                                     self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

        self._refresh_size()

    # ==================================================================
    # 状态视图（测试与内部逻辑读取的稳定入口）
    # ==================================================================
    @property
    def _visible(self) -> bool:
        return self._control.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._control.visible = bool(value)

    @property
    def _searching(self) -> bool:
        return self._control.searching

    @_searching.setter
    def _searching(self, value: bool) -> None:
        self._control.searching = bool(value)

    @property
    def _items(self):
        return self._control.items

    @_items.setter
    def _items(self, value) -> None:
        self._control.items = list(value)

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
    # 公开接口（由 SpeakerSearchDialog 调用）
    # ==================================================================

    def clear_results(self) -> None:
        """清空结果列表。"""
        self._control.clear()
        self._refresh_view(reposition=False)

    def set_results(self, items: list) -> None:
        """载入结果列表。items: [(track_ref, display_text), ...]"""
        self._control.set_items(items)
        self._refresh_view()

    def set_searching(self, state: bool) -> None:
        """设置“搜索中”状态并刷新 loading 提示。"""
        self._control.searching = bool(state)
        self._refresh_view()

    def navigate(self, direction: int) -> None:
        """上下导航选中行（direction: -1 上 / +1 下）。"""
        if self._control.navigate(direction):
            self.update()

    def turn_page(self, direction: int) -> None:
        """翻页。direction: -1 上一页 / +1 下一页，支持循环翻页。"""
        if self._control.turn_page(direction):
            self._refresh_view()

    def fade_in(self, dialog) -> None:
        """
        淡入显示（由 SpeakerSearchDialog 调用）。

        Args:
            dialog: SpeakerSearchDialog 实例，用于初始定位。
        """
        if self._control.visible:
            return
        self._control.visible = True
        # 根据对话框当前位置初始化锚点
        if dialog:
            self._anchor_point = Point(
                float(dialog.x()),
                float(dialog.y() + dialog.height()),
            )
        self._refresh_size()
        self._host.show()
        if self._anchor_point:
            self._update_position()
        self._animate(1.0)

    def fade_out(self) -> None:
        """淡出隐藏（随 SpeakerSearchDialog 消失时调用）。"""
        if not self._control.visible:
            return
        self._control.visible = False
        rect = self._host.geometry_rect()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type':   'rect',
            'area_data':   (int(rect.x), int(rect.y),
                            int(rect.x) + int(rect.width), int(rect.y) + int(rect.height)),
        }))
        self._animate(0.0, fade_out=True)
        self._prev_btn.hide_btn()
        self._next_btn.hide_btn()

    # ==================================================================
    # 私有：分页 / 尺寸 / 位置
    # ==================================================================

    def _page_items(self) -> list:
        return self._control.page_items()

    def _has_pages(self) -> bool:
        return self._control.has_pages()

    def _refresh_size(self) -> None:
        """根据当前内容自适应窗口宽高。"""
        width, height = self._control.refresh_size()
        self._host.apply_size(width, height)

    def _refresh_view(self, *, reposition: bool = True) -> None:
        self._refresh_size()
        if reposition and self._control.visible:
            if self._anchor_point:
                self._update_position()
            else:
                update_page_buttons_position(self, self._prev_btn, self._next_btn, self._has_pages())
        self.update()

    def _update_position(self) -> None:
        """将自身左上角对齐到搜索框底部 + _GAP_Y 偏移。"""
        if not self._anchor_point:
            return
        # 左上锚点对齐搜索框的 bottom_left 锚点 + _GAP_Y。
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

    def _animate(self, target: float, *, fade_out: bool = False) -> None:
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=UI['ui_fade_duration'],
            fade_out=fade_out,
        )

    def _on_fade_out_done(self) -> None:
        if not self._control.visible:
            self._host.hide()

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_anchor_response(self, event: Event) -> None:
        """监听 SpeakerSearchDialog 广播的位置更新，同步跟随。"""
        if not self._control.visible:
            return
        ui_id     = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        if ui_id == 'all' and window_id == 'speaker_search_dialog' and anchor_id == 'all':
            anchor_point = event.data.get('anchor_point')
            try:
                dialog_x = float(anchor_point.x)
                dialog_y = float(anchor_point.y)
            except (AttributeError, TypeError, ValueError):
                return
            # 结果框跟随 dialog 底部
            new_pt = Point(dialog_x, dialog_y + _DIALOG_H)
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._host.set_clickthrough(bool(event.data.get('enabled', False)))

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def _on_pointer_move(self, event) -> None:
        """悬停时更新高亮行。"""
        if self._control.searching or not self._control.items:
            return
        row = self._control.row_at_y(event.local.y)
        if row != -1 and row != self._control.selected:
            self._control.selected = row
            self.update()

    def _on_pointer(self, event):
        """
        左键：立即播放并关闭 UI。
        右键：加入播放队列，UI 保持打开。
        """
        # 点击粒子在任意位置都发射（与迁移前一致：先发粒子再判定是否命中行）。
        particle_id = controls.BUTTON_PARTICLES.get(event.button)
        if particle_id:
            publish_click_particle_at(
                particle_id, int(event.screen.x), int(event.screen.y)
            )

        if self._control.searching or not self._control.items:
            return controls.PointerClick()
        row = self._control.row_at_y(event.local.y)
        items = self._page_items()
        if row < 0 or row >= len(items):
            return controls.PointerClick()

        track_ref, display = items[row]

        if event.button == controls.BUTTON_LEFT:
            # 置顶播放：通过事件系统触发（解耦 UI 与播放逻辑）
            self._event_center.publish(Event(EventType.MUSIC_PLAY_TOP, {
                'song_id': track_ref,
                'track_ref': track_ref,
                'display': display,
            }))
        elif event.button == controls.BUTTON_RIGHT:
            # 加入队列末尾：UI 保持打开，通过事件系统触发
            self._event_center.publish(Event(EventType.MUSIC_ENQUEUE, {
                'song_id': track_ref,
                'track_ref': track_ref,
                'display': display,
            }))

        return controls.PointerClick()

    # ==================================================================
    # 绘制与生命周期
    # ==================================================================

    def _build_visual(self):
        return self._control.build_visual()

    def _paint_batch(self):
        return self._control.build_visual().batch

    def update(self) -> None:
        self._host.update()

    def hide(self) -> None:
        self._host.stop_animation()
        self._control.visible = False
        self._host.hide()

    def close(self) -> None:
        """取消订阅并释放后端窗口。"""
        for event_type, handler in (
            (EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle),
        ):
            try:
                self._event_center.unsubscribe(event_type, handler)
            except Exception:
                pass
        self._host.cleanup()
