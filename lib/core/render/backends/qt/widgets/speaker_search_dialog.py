"""音响右键搜索窗口（档位 D 宿主）。

本模块原住在 `lib/script/ui/speaker_search_dialog.py`，按《render 层边界契约》第 3 节下沉到
档位 D：`SpeakerSearchDialog`（输入框 160px + 「搜索歌曲」按钮 80px，下方挂搜索结果框）与
它的全局单例。

宿主不得 import `lib.script`：本模块用到的产品事实一律经 `tier_d_seams` 取——

- 音乐服务 / 播放状态：`hook("speaker_search_dialog:music_service")`；
- 搜索结果框、控制按钮组、播放队列面板与点击粒子：`hook("speaker_search_dialog:...")`；
- 单例注册与关闭：`hook("speaker_search_dialog:register_instance")` /
  `hook("speaker_search_dialog:unregister_instance")`；
- 说明文本键 `speaker_search_dialog` 的 tooltip 直接读 `config.tooltip_config`（不是产品包）。

绘制配方与屏幕几何分别走 `speaker_menu_style`（后端中立门面）与 `tier_d_seams`；产品侧
`lib/script/ui/speaker_search_dialog.py` 只做再导出，既有导入面不变。
"""

from __future__ import annotations

from concurrent.futures import Future

from PyQt5.QtCore import QEvent, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QCursor, QPainter
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QLineEdit, QWidget

from config.config import CLOUD_MUSIC, SPEAKER_SEARCH_UI, UI
from config.scale import scale_px, scale_style_px
from config.tooltip_config import TOOLTIPS
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.layers import WindowLayer, get_layer_manager
from lib.core.render.visuals.types import Point, Rect
from lib.core.world_objects import WorldObjectInstance

_SEARCH_MODE_ORDER = ('song', 'artist', 'album', 'playlist')
_SEARCH_MODE_LABELS = {
    'song': '单曲优先',
    'artist': '歌手优先',
    'album': '专辑优先',
    'playlist': '歌单优先',
}
_MAX_SEARCH_RESULTS = int(CLOUD_MUSIC.get('search_result_limit', 128))


def _hook(name):
    return tier_d_seams.hook(name)


def _music_service():
    hook = _hook("speaker_search_dialog:music_service")
    if hook is None:
        raise RuntimeError("音响搜索窗口需要经 render_bridge 注入音乐服务")
    return hook()


def _first_artist(song) -> str:
    hook = _hook("speaker_search_dialog:first_artist")
    if hook is None:
        raise RuntimeError("音响搜索窗口需要经 render_bridge 注入曲目文本助手")
    return hook(song)


def _format_duration(value) -> str:
    hook = _hook("speaker_search_dialog:format_duration")
    if hook is None:
        raise RuntimeError("音响搜索窗口需要经 render_bridge 注入曲目文本助手")
    return hook(value)


def _menu_style():
    """音响菜单族共享样式门面（后端中立；由产品垫片经 seam 钩子注入）。"""
    hook = _hook("speaker_search_dialog:menu_style")
    if hook is None:
        raise RuntimeError("音响搜索窗口需要经 render_bridge 注入音响菜单样式门面")
    return hook()


def paint_speaker_menu_panel(painter, rect):
    return _menu_style().paint_speaker_menu_panel(painter, rect)


def paint_speaker_action_button(painter, rect, **kwargs):
    return _menu_style().paint_speaker_action_button(painter, rect, **kwargs)


def _hex(value) -> str:
    """核心 `Color` / 通道元组 / `#rrggbb` 文本 → QSS 要的 `#rrggbb` 文本。"""
    return tier_d_seams.create_painter_host().color_name(value)


def _publish_click_particle(widget, event) -> None:
    hook = _hook("click_particle")
    if hook is not None:
        hook(widget, event)


def _result_box_class():
    hook = _hook("speaker_search_dialog:result_box_class")
    return hook() if hook is not None else None


def _control_buttons_class():
    hook = _hook("speaker_search_dialog:control_buttons_class")
    return hook() if hook is not None else None


def _playlist_panel_getter():
    hook = _hook("speaker_search_dialog:playlist_panel_getter")
    return hook() if hook is not None else None


def _register_instance(instance) -> None:
    hook = _hook("speaker_search_dialog:register_instance")
    if hook is not None:
        hook(instance)


def _unregister_instance() -> None:
    hook = _hook("speaker_search_dialog:unregister_instance")
    if hook is not None:
        hook()


def _resolve_placement(self_size, target_rect, screen, **kwargs):
    """窗口落位的共享解算（档位 1）；宿主自己解析，不经产品转发层。"""
    from lib.core.render.visuals.layout import resolve_placement as resolve

    return resolve(self_size, target_rect, screen, **kwargs)


def get_ui_font(size: int | None = None):
    return tier_d_seams.ui_font(size)


def screen_rect_for_point(point=None, fallback_widget=None):
    return tier_d_seams.screen_rect_for_point(point=point, fallback_widget=fallback_widget)


def widget_global_rect(widget):
    return tier_d_seams.widget_global_rect(widget)


# ── 从配置文件读取尺寸参数 ───────────────────────────────────────────
_INPUT_W = SPEAKER_SEARCH_UI.get('input_width', scale_px(160, min_abs=1))
_BTN_W   = SPEAKER_SEARCH_UI.get('button_width', scale_px(80, min_abs=1))
_TOTAL_W = _INPUT_W + _BTN_W
_HEIGHT  = SPEAKER_SEARCH_UI.get('height', scale_px(36, min_abs=1))
_BORDER  = SPEAKER_SEARCH_UI.get('border', scale_px(4, min_abs=1))
_GAP     = SPEAKER_SEARCH_UI.get('gap', scale_px(6, min_abs=1))
_AUTO_HIDE_MOUSE_DISTANCE = UI.get('auto_hide_mouse_distance', scale_px(300, min_abs=1))


# ── 跨线程信号载体 ────────────────────────────────────────────────────
class _SearchSignals(QObject):
    results_ready = pyqtSignal(int, list)   # [(track_ref, display_text), ...]
    error         = pyqtSignal(int, str)


class SpeakerSearchDialog(QWidget):
    """
    音响右键搜索 UI（全局单例）。

    包含输入框（160px）+ "搜索歌曲"按钮（80px），
    下方挂载 SpeakerSearchResultBox 显示搜索结果。
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(_TOTAL_W, _HEIGHT)
        self.setMouseTracking(True)
        get_layer_manager().register(self, WindowLayer.PANEL)

        # ── 输入框 ──────────────────────────────────────────────────
        self._entry = QLineEdit(self)
        self._entry.setPlaceholderText('输入歌曲名搜索...')
        self._entry.setFont(get_ui_font())
        # 绝对定位：在左侧输入区域内（去掉 4px 边框后的内容区）
        self._entry.setGeometry(
            _BORDER, _BORDER,
            _INPUT_W - _BORDER * 2, _HEIGHT - _BORDER * 2
        )
        self._entry.setStyleSheet(scale_style_px(f"""
            QLineEdit {{
                background: {_hex(_menu_style()._C_ENTRY_BG)};
                color: black;
                border: 2px solid {_hex(_menu_style()._C_BG)};
                padding: 2px 4px;
            }}
            QLineEdit::placeholder {{
                color: rgba(0, 0, 0, 100);
            }}
        """))

        # ── 状态 ────────────────────────────────────────────────────
        self._visible         = False
        self._focused_speaker = None   # 当前锚定的 Speaker 实例
        self._anchor_point: QPoint | None = None
        self._searching       = False
        self._search_mode     = 'song'
        self._search_generation = 0
        self._search_future: Future | None = None
        self._description     = TOOLTIPS['speaker_search_dialog']
        self._search_btn_hovered = False
        self._search_btn_pressed = False

        # ── 透明度 & 动画 ────────────────────────────────────────────
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        # ── 搜索结果框 ───────────────────────────────────────────────
        self._result_box = _result_box_class()()

        # ── 控制按钮组 ───────────────────────────────────────────────
        self._control_buttons = _control_buttons_class()(self)

        # ── 跨线程信号 ───────────────────────────────────────────────
        self._sig = _SearchSignals()
        self._sig.results_ready.connect(self._on_results_ready)
        self._sig.error.connect(self._on_search_error)

        # ── 事件订阅 ─────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)
        self._event_center.subscribe(
            EventType.SPEAKER_SEARCH_TOGGLE_REQUEST,
            self._on_search_toggle_request,
        )

        # ── 输入框信号 ───────────────────────────────────────────────
        self._entry.returnPressed.connect(self._trigger_search)
        self._entry.installEventFilter(self)
        # 结果框已迁为“描述 + 后端渲染”，不再是 QWidget，无法安装事件过滤器；
        # 方向键在输入框有焦点时由 eventFilter 处理（见 eventFilter）。
        _register_instance(self)


    # ==================================================================
    # 公开接口
    # ==================================================================

    @property
    def focused_speaker(self):
        """返回当前锚定的音响实例（未锚定时为 None）。"""
        return self._focused_speaker

    @property
    def search_priority_label(self) -> str:
        return _SEARCH_MODE_LABELS.get(self._search_mode, _SEARCH_MODE_LABELS['song'])

    def cycle_search_priority(self) -> str:
        try:
            idx = _SEARCH_MODE_ORDER.index(self._search_mode)
        except ValueError:
            idx = 0
        self._search_mode = _SEARCH_MODE_ORDER[(idx + 1) % len(_SEARCH_MODE_ORDER)]
        return self.search_priority_label

    def toggle(self, speaker):
        """
        切换显示/隐藏。

        Args:
            speaker: 触发右键的 Speaker 实例；None = 强制关闭。
        """
        if self._visible:
            if speaker is None or speaker == self._focused_speaker:
                # 同一音响再次右键 或 外部强制关闭 → 关闭
                self._hide()
            else:
                # 不同音响右键 → 切换锚定对象，移动 UI
                self._focused_speaker = speaker
                self._update_anchor()
                self._update_position()
                self._control_buttons.set_focused_speaker(speaker)
        else:
            if speaker is None:
                return
            self._focused_speaker = speaker
            self._update_anchor()
            self._show()
            self._control_buttons.set_focused_speaker(speaker)

    def _on_search_toggle_request(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        backend_id = str(data.get("backend_id") or "").strip()
        try:
            instance_id = int(data.get("instance_id", 0))
        except (TypeError, ValueError):
            return
        if backend_id != "qt" or instance_id <= 0:
            return
        self.toggle(WorldObjectInstance(backend_id, instance_id, "speaker"))

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================

    def _show(self):
        # 若播放队列 UI 处于打开状态，先将其关闭
        get_panel = _playlist_panel_getter()
        panel = get_panel() if get_panel is not None else None
        if panel is not None and panel._visible:
            panel.hide_panel()

        self._update_position()
        self.show()
        self._entry.setFocus()
        self._entry.clear()
        self._result_box.clear_results()
        self._visible = True
        self._animate(1.0)
        self._result_box.fade_in(self)
        self._control_buttons.fade_in()

    def _hide(self):
        self._visible = False
        self._entry.clear()
        self._animate(0.0)
        self._result_box.fade_out()
        self._control_buttons.fade_out()
        # right_fade 粒子消散特效
        rect = self.geometry()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type':   'rect',
            'area_data':   (rect.x(), rect.y(),
                            rect.x() + rect.width(), rect.y() + rect.height()),
        }))

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_anim_finished(self):
        if not self._visible:
            self.hide()

    # ==================================================================
    # 搜索
    # ==================================================================

    def _build_song_display(self, song: dict) -> str:
        title = str(song.get('name') or '未知歌曲').strip() or '未知歌曲'
        artist = _first_artist(song.get("ar") or song.get("artists") or [])
        duration = _format_duration(song.get('dt') or song.get('duration'))
        return f"{duration} {title} - {artist}"

    def _trigger_search(self):
        """回车或点击按钮触发搜索。"""
        keyword = self._entry.text().strip()
        if not keyword or self._searching:
            return
        mode = self._search_mode
        self._search_generation += 1
        generation = self._search_generation
        self._searching = True
        self._result_box.set_searching(True)
        self.update()
        from lib.core.compute_hub import get_compute_hub

        future = get_compute_hub().submit_latest(
            "speaker_search_dialog",
            self._search_worker,
            keyword,
            mode,
            generation,
            executor="io",
        )
        if future is not None:
            self._search_future = future

    def _search_worker(self, keyword: str, mode: str, generation: int):
        """后台线程：调用音乐抽象层搜索，结果通过信号投递到主线程。"""
        try:
            tracks = _music_service().search(
                keyword,
                mode=mode,
                limit=_MAX_SEARCH_RESULTS,
                fallback_enabled=False,
            )
            items = []
            for track in tracks:
                title = str(track.title or '未知歌曲').strip() or '未知歌曲'
                artist = str(track.artist or '未知作者').strip() or '未知作者'
                display = str(track.display or '').strip() or f"--:-- {title} - {artist}"
                items.append((track.track_id, display))
            self._sig.results_ready.emit(generation, items)
        except Exception as e:
            self._sig.error.emit(generation, str(e))

    def _on_results_ready(self, generation: int, items: list):
        if generation != self._search_generation:
            return
        self._searching = False
        self._search_future = None
        self.update()
        self._result_box.set_results(items)
        self._result_box.set_searching(False)

    def _on_search_error(self, generation: int, msg: str):
        if generation != self._search_generation:
            return
        self._searching = False
        self._search_future = None
        self.update()
        self._result_box.set_results([])
        self._result_box.set_searching(False)
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'[搜索失败] {msg}', 'min': 1, 'max': 180,
        }))

    # ==================================================================
    # 位置锚点
    # ==================================================================

    def _update_anchor(self):
        """计算当前锚定音响的中心点（用于跨屏定位）。"""
        if self._focused_speaker is None:
            return
        geometry = self._focused_speaker.get_geometry()
        self._anchor_point = QPoint(
            int(round(geometry.x + geometry.width / 2.0)),
            int(round(geometry.y + geometry.height / 2.0)),
        )

    def _update_position(self):
        """始终优先放在音响右侧；右侧被边缘阻挡时翻到左侧。"""
        if self._focused_speaker is None:
            return

        geometry = self._focused_speaker.get_geometry()
        if not self._anchor_point:
            self._update_anchor()
        anchor_point = self._anchor_point or QPoint(
            int(round(geometry.x + geometry.width / 2.0)),
            int(round(geometry.y + geometry.height / 2.0)),
        )

        center_y = int(round(geometry.y + geometry.height / 2.0))
        new_y = center_y - _HEIGHT // 2 - scale_px(30, min_abs=1)  # 上移 30px
        right_x = int(round(geometry.x + geometry.width)) + _GAP
        left_x = int(round(geometry.x)) - _TOTAL_W - _GAP

        # 右侧优先、受阻翻左：解算与夹取都在 visuals/layout.py（档位 1）。
        screen = screen_rect_for_point(point=anchor_point, fallback_widget=self)
        placement = _resolve_placement(
            (_TOTAL_W, _HEIGHT), Rect(right_x, new_y, _TOTAL_W, _HEIGHT), screen,
            target_anchor_id='top_left', self_anchor_id='top_left',
        )
        x, y = placement.x, placement.y

        if x != right_x:
            placement = _resolve_placement(
                (_TOTAL_W, _HEIGHT), Rect(left_x, new_y, _TOTAL_W, _HEIGHT), screen,
                target_anchor_id='top_left', self_anchor_id='top_left',
            )
            fx, fy = placement.x, placement.y
            if fx == left_x or abs(fx - left_x) < abs(x - right_x):
                x, y = fx, fy

        self.move(x, y)

        # 广播自身位置，供结果框跟随
        self._event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id':   'speaker_search_dialog',
            'anchor_id':   'all',
            'anchor_point': Point(x, y),
            'ui_id':       'all',
        }))

    def _on_tick(self, event: Event):
        if not self._visible or self._focused_speaker is None:
            return
        if not self._focused_speaker.is_alive():
            self._hide()
            return
        if self._is_mouse_far_from_family():
            self._hide()
            return

    def _on_frame(self, event: Event):
        if not self._visible or self._focused_speaker is None:
            return
        self._update_anchor()
        self._update_position()

    def _iter_family_widgets(self):
        widgets = [
            self,
            self._result_box,
            getattr(self._result_box, "_prev_btn", None),
            getattr(self._result_box, "_next_btn", None),
        ]
        widgets.extend(getattr(self._control_buttons, "_buttons", []))
        for name in ("_volume_slider", "_band_slider"):
            slider = getattr(self._control_buttons, name, None)
            if slider is not None:
                widgets.append(slider)
        return [w for w in widgets if w is not None]

    def _is_mouse_far_from_family(self) -> bool:
        mouse = QCursor.pos()
        limit_sq = _AUTO_HIDE_MOUSE_DISTANCE * _AUTO_HIDE_MOUSE_DISTANCE
        nearest_sq = None
        for widget in self._iter_family_widgets():
            try:
                if not widget.isVisible():
                    continue
                # `widget_global_rect` 返回核心 `Rect`：center/x/y 是属性，不是方法。
                # 不能再读 `widget.geometry()`：已迁移到描述 + 宿主的控件没有该方法。
                center = widget_global_rect(widget).center
                dx = mouse.x() - int(center.x)
                dy = mouse.y() - int(center.y)
                dist_sq = dx * dx + dy * dy
                if nearest_sq is None or dist_sq < nearest_sq:
                    nearest_sq = dist_sq
            except RuntimeError:
                continue
        if nearest_sq is None:
            return False
        return nearest_sq > limit_sq

    # ==================================================================
    # 键盘导航（驱动结果框）
    # ==================================================================

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            # 左右翻页：输入框或结果框有焦点时均生效
            if obj in (self._entry, self._result_box):
                if key == Qt.Key_Left:
                    self._result_box.turn_page(-1)
                    return True
                elif key == Qt.Key_Right:
                    self._result_box.turn_page(1)
                    return True
            # 上下导航：仅在输入框有焦点时生效
            if obj is self._entry:
                if key == Qt.Key_Up:
                    self._result_box.navigate(-1)
                    return True
                elif key == Qt.Key_Down:
                    self._result_box.navigate(1)
                    return True
        return super().eventFilter(obj, event)

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)

        # ── 输入框区域（左侧 _INPUT_W px）──────────────────────────
        paint_speaker_menu_panel(p, QRect(0, 0, _INPUT_W, _HEIGHT))

        # ── 按钮区域（右侧 _BTN_W px）───────────────────────────────
        bx = _INPUT_W
        btn_content = paint_speaker_action_button(
            p,
            QRect(bx, 0, _BTN_W, _HEIGHT),
            hovered=self._search_btn_hovered,
            pressed=self._search_btn_pressed,
        )

        # 按钮文字
        font = get_ui_font()
        font.setBold(True)
        p.setPen(tier_d_seams.painter_color(_menu_style()._C_ACTION_TEXT))
        p.setFont(font)
        btn_label = '搜索中...' if self._searching else '搜索歌曲'
        p.drawText(
            btn_content,
            Qt.AlignCenter,
            btn_label,
        )
        p.end()

    def mousePressEvent(self, event):
        _publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            # 点击了按钮区域（右侧 80px）
            if event.pos().x() >= _INPUT_W:
                self._search_btn_pressed = True
                try:
                    self.grabMouse()
                except RuntimeError:
                    pass
                self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._search_btn_pressed:
            commit = event.button() == Qt.LeftButton and event.pos().x() >= _INPUT_W and self.rect().contains(event.pos())
            self._search_btn_pressed = False
            try:
                if self.mouseGrabber() is self:
                    self.releaseMouse()
            except RuntimeError:
                pass
            self.update()
            if commit:
                self._trigger_search()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        hovered = event.pos().x() >= _INPUT_W
        if hovered != self._search_btn_hovered:
            self._search_btn_hovered = hovered
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._search_btn_hovered:
            self._search_btn_hovered = False
            self.update()
        super().leaveEvent(event)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                       self._on_clickthrough_toggle)
        self._event_center.unsubscribe(
            EventType.SPEAKER_SEARCH_TOGGLE_REQUEST,
            self._on_search_toggle_request,
        )
        self._control_buttons.cleanup()
        _unregister_instance()
        super().closeEvent(event)


__all__ = ["SpeakerSearchDialog"]
