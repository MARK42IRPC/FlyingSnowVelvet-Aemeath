"""音响搜索UI - 音响右键时显示的搜索框

配色（区别于主宠物UI）：
  外框：黑色  |  中框：灰色  |  内背景：粉色  |  字体：黑色  |  输入框：白色背景+粉色边框

布局：
  [      输入框 160px      ][  搜索歌曲 80px  ]
  下方：搜索结果列表（SpeakerSearchResultBox），一页 5 条，翻页。

单例：同时只存在一个 UI 实例，多个音响共享；以最近获焦的音响为主。
"""

import re
from concurrent.futures import Future

from PyQt5.QtWidgets import QWidget, QLineEdit, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QObject, QRect, QEvent
from PyQt5.QtGui import QColor, QPainter, QCursor
from PyQt5.QtCore import pyqtSignal

from config.config import UI, SPEAKER_SEARCH_UI, CLOUD_MUSIC
from lib.core.qt_bridge.font import get_ui_font
from config.scale import scale_px, scale_style_px
from config.tooltip_config import TOOLTIPS
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.graphics.types import Point
from lib.core.world_objects import WorldObjectInstance
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.qt_bridge.screen import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.music import get_music_service
from lib.script.ui.speaker_menu_style import (
    _C_BG,
    _C_ACTION_TEXT,
    _C_ENTRY_BG,
    paint_speaker_menu_panel,
    paint_speaker_action_button,
)

_DURATION_TEXT_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s*$")
_SEARCH_MODE_ORDER = ('song', 'artist', 'album', 'playlist')
_SEARCH_MODE_LABELS = {
    'song': '单曲优先',
    'artist': '歌手优先',
    'album': '专辑优先',
    'playlist': '歌单优先',
}
_MAX_SEARCH_RESULTS = int(CLOUD_MUSIC.get('search_result_limit', 128))


def _hex(color: QColor) -> str:
    return color.name()


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
        get_layer_manager().register(self, Layer.PANEL)

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
                background: {_hex(_C_ENTRY_BG)};
                color: black;
                border: 2px solid {_hex(_C_BG)};
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
        from lib.script.ui.speaker_search_result_box import SpeakerSearchResultBox
        self._result_box = SpeakerSearchResultBox()

        # ── 控制按钮组 ───────────────────────────────────────────────
        from lib.script.ui.speaker_control_buttons import SpeakerControlButtons
        self._control_buttons = SpeakerControlButtons(self)

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
        self._result_box.installEventFilter(self)

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
        else:
            if speaker is None:
                return
            self._focused_speaker = speaker
            self._update_anchor()
            self._show()

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
        from lib.script.ui.playlist_panel import get_playlist_panel
        panel = get_playlist_panel()
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

    @staticmethod
    def _format_duration_text(duration_ms) -> str:
        try:
            if isinstance(duration_ms, str):
                m = _DURATION_TEXT_RE.match(duration_ms)
                if m:
                    total_sec = int(m.group(1)) * 60 + int(m.group(2))
                else:
                    total_sec = max(0, int(float(duration_ms)) // 1000)
            elif isinstance(duration_ms, dict):
                raw = (
                    duration_ms.get('duration_ms')
                    or duration_ms.get('duration')
                    or duration_ms.get('dt')
                    or duration_ms.get('ms')
                )
                total_sec = max(0, int(raw) // 1000) if raw is not None else 0
            else:
                total_sec = max(0, int(duration_ms) // 1000)
        except (TypeError, ValueError):
            return "00:00"
        mins, secs = divmod(total_sec, 60)
        return f"{mins:02d}:{secs:02d}"

    @staticmethod
    def _extract_first_artist(song: dict) -> str:
        artists = song.get('ar') or song.get('artists') or []
        if not artists:
            return "未知作者"
        first = artists[0]
        if isinstance(first, dict):
            name = str(first.get('name') or '').strip()
            return name or "未知作者"
        name = str(first).strip()
        return name or "未知作者"

    def _build_song_display(self, song: dict) -> str:
        title = str(song.get('name') or '未知歌曲').strip() or '未知歌曲'
        artist = self._extract_first_artist(song)
        duration = self._format_duration_text(song.get('dt') or song.get('duration'))
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
            tracks = get_music_service().search(
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

        # 先尝试右侧
        x, y, _ = clamp_rect_position(
            right_x,
            new_y,
            _TOTAL_W,
            _HEIGHT,
            point=anchor_point,
            fallback_widget=self,
        )

        # 右侧受阻时翻到左侧
        if x != right_x:
            fx, fy, _ = clamp_rect_position(
                left_x,
                new_y,
                _TOTAL_W,
                _HEIGHT,
                point=anchor_point,
                fallback_widget=self,
            )
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
        return [w for w in widgets if w is not None]

    def _is_mouse_far_from_family(self) -> bool:
        mouse = QCursor.pos()
        limit_sq = _AUTO_HIDE_MOUSE_DISTANCE * _AUTO_HIDE_MOUSE_DISTANCE
        nearest_sq = None
        for widget in self._iter_family_widgets():
            try:
                if not widget.isVisible():
                    continue
                center = widget.geometry().center()
                dx = mouse.x() - center.x()
                dy = mouse.y() - center.y()
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
        p.setPen(_C_ACTION_TEXT)
        p.setFont(font)
        btn_label = '搜索中...' if self._searching else '搜索歌曲'
        p.drawText(
            btn_content,
            Qt.AlignCenter,
            btn_label,
        )
        p.end()

    def mousePressEvent(self, event):
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
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
        super().closeEvent(event)


# ── 全局单例 ──────────────────────────────────────────────────────────
_instance: 'SpeakerSearchDialog | None' = None


def get_speaker_search_dialog() -> 'SpeakerSearchDialog | None':
    """获取全局搜索 UI 单例（未初始化时返回 None）。"""
    return _instance


def init_speaker_search_dialog() -> 'SpeakerSearchDialog':
    """初始化并返回全局搜索 UI 单例，需在 Qt 主线程中调用。"""
    global _instance
    if _instance is None:
        _instance = SpeakerSearchDialog()
    return _instance


def cleanup_speaker_search_dialog():
    """释放全局搜索 UI 资源（程序退出时调用）。"""
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None
