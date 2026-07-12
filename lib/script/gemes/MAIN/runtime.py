"""小游戏运行时骨架。

当前职责：
- 订阅 INPUT_HASH，提供 #游戏 命令入口
- 管理独立 runtime 面板显示/隐藏
- 维护可扩展的小游戏元信息列表

后续可在此基础上继续扩展：
- 独立游戏实例生命周期
- 游戏模块动态加载
- 存档 / 排行 / 成就
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from config.config import UI
from config.font_config import get_ui_font
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.logger import get_logger
from lib.core.screen_utils import get_screen_geometry_for_point
from lib.core.unified_draw import Layer, RenderCore, RenderRequest, get_layer_manager
from lib.core.voice.ams_open_lahai_tetris import AmsOpenLahaiTetrisSound
from lib.script.music.service import get_music_service
from .lahai_tetris import LahaiTetrisWidget

_logger = get_logger(__name__)


def log(msg: str) -> None:
    _logger.debug("[GameRuntime] %s", msg)


def centered_aspect_rect(
    container: QRect,
    aspect_width: int,
    aspect_height: int,
    inset: int = 0,
) -> QRect:
    inner = container.adjusted(inset, inset, -inset, -inset)
    if inner.width() * aspect_height <= inner.height() * aspect_width:
        width = max(1, inner.width())
        height = max(1, width * aspect_height // aspect_width)
    else:
        height = max(1, inner.height())
        width = max(1, height * aspect_width // aspect_height)
    x = inner.x() + (inner.width() - width) // 2
    y = inner.y() + (inner.height() - height) // 2
    return QRect(x, y, width, height)


def aspect_resize_geometry(
    start: QRect,
    edges: set[str],
    delta: QPoint,
    minimum_width: int,
    aspect_width: int,
    aspect_height: int,
) -> QRect:
    width_delta = 0
    height_delta = 0
    if "left" in edges:
        width_delta = -delta.x()
    elif "right" in edges:
        width_delta = delta.x()
    if "top" in edges:
        height_delta = -delta.y()
    elif "bottom" in edges:
        height_delta = delta.y()
    width_from_height = round(height_delta * aspect_width / aspect_height)
    effective_delta = width_delta if abs(width_delta) >= abs(width_from_height) else width_from_height
    width = max(int(minimum_width), int(start.width() + effective_delta))
    height = max(1, round(width * aspect_height / aspect_width))
    x = start.right() - width + 1 if "left" in edges else start.x()
    y = start.bottom() - height + 1 if "top" in edges else start.y()
    return QRect(x, y, width, height)


@dataclass(frozen=True)
class GameMeta:
    game_id: str
    name: str
    summary: str
    status: str = "预留"


class GameRuntimePanel(QWidget):
    """小游戏 runtime 面板。"""

    _ROW_H = scale_px(24, min_abs=1)
    _HEADER_H = scale_px(30, min_abs=1)
    _PADDING = scale_px(8, min_abs=1)
    _LAYER = scale_px(4, min_abs=1)
    _BORDER = _LAYER * 2
    _RESIZE_MARGIN = scale_px(12, min_abs=1)
    _ASPECT_WIDTH = 10
    _ASPECT_HEIGHT = 8
    _DEFAULT_WIDTH = 1000
    _DEFAULT_HEIGHT = 800
    _MINIMUM_WIDTH = 600
    _MINIMUM_HEIGHT = 480

    _C_BORDER = QColor(25, 16, 58)
    _C_MID = QColor(145, 122, 232)
    _C_BG = QColor(59, 43, 118)
    _C_TEXT = QColor(245, 240, 255)
    _C_ACCENT = QColor(117, 233, 255)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.NoDropShadowWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        get_layer_manager().register(self, Layer.PANEL)
        self._render_core = RenderCore()
        self._render_core.register_item(RenderRequest(
            'game_runtime_panel_shell',
            self._paint_panel_layer,
            Layer.PANEL,
        ))

        self._font = get_ui_font()
        self._font.setBold(True)
        self._items: list[GameMeta] = []
        self._active_game_name = "拉海洛方块"
        self._game_widget = LahaiTetrisWidget(self)
        self._game_widget.set_close_callback(self._handle_game_close_request)
        self._game_widget.set_fullscreen_callback(self.toggle_fullscreen)
        self.setMinimumSize(self._MINIMUM_WIDTH, self._MINIMUM_HEIGHT)
        self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
        self._drag_origin: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_edges: set[str] = set()
        self._resize_start_geometry: QRect | None = None
        self._fading_out = False
        self._allow_hide_once = False
        self._fullscreen_active = False
        self._normal_geometry = QRect()
        self._opacity_anim = QPropertyAnimation(self, b'windowOpacity', self)
        self._opacity_anim.setDuration(UI.get('ui_fade_duration', 180))
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)
        self.setWindowOpacity(0.0)
        self.hide()
        self._refresh_size()

    def set_items(self, items: list[GameMeta]) -> None:
        self._items = list(items)
        self._refresh_size()
        self.update()

    def _refresh_size(self) -> None:
        inset = 0 if self._fullscreen_active else self._BORDER
        self._game_widget.setGeometry(centered_aspect_rect(
            self.rect(),
            self._ASPECT_WIDTH,
            self._ASPECT_HEIGHT,
            inset,
        ))

    def get_game_middle_third_rect_global(self) -> QRect:
        """
        返回小游戏区域横向中间三分之一区域的全局矩形。

        用于外部逻辑避让桌宠漫游目标，避免遮挡拉海洛方块主要游玩区。
        面板不可见时返回空 QRect。
        """
        if not self.isVisible():
            return QRect()
        local_rect = self._game_widget.geometry()
        third_w = max(1, local_rect.width() // 3)
        middle_x = local_rect.x() + third_w
        middle_rect = QRect(
            middle_x,
            local_rect.y(),
            third_w,
            local_rect.height(),
        )
        top_left = self.mapToGlobal(middle_rect.topLeft())
        return QRect(top_left, middle_rect.size())

    def _hit_test_edges(self, pos) -> set[str]:
        if self._fullscreen_active:
            return set()
        edges: set[str] = set()
        if pos.x() <= self._RESIZE_MARGIN:
            edges.add("left")
        elif pos.x() >= self.width() - self._RESIZE_MARGIN:
            edges.add("right")
        if pos.y() <= self._RESIZE_MARGIN:
            edges.add("top")
        elif pos.y() >= self.height() - self._RESIZE_MARGIN:
            edges.add("bottom")
        return edges

    def _update_cursor(self, edges: set[str]) -> None:
        if edges in ({"left", "top"}, {"right", "bottom"}):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in ({"right", "top"}, {"left", "bottom"}):
            self.setCursor(Qt.SizeBDiagCursor)
        elif "left" in edges or "right" in edges:
            self.setCursor(Qt.SizeHorCursor)
        elif "top" in edges or "bottom" in edges:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def move_to_screen_center(self) -> None:
        if self._fullscreen_active:
            return
        screen = get_screen_geometry_for_point(fallback_widget=self)
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + (screen.height() - self.height()) // 2
        self.move(x, y)

    def resizeEvent(self, event) -> None:
        self._refresh_size()
        super().resizeEvent(event)

    def activate(self) -> None:
        self._game_widget.reset_game(start_running=False)
        self._game_widget.show()
        get_layer_manager().bring_to_front(self)
        self.activateWindow()
        self._game_widget.setFocus(Qt.ActiveWindowFocusReason)
        # 重新激活小游戏后，系统可能在后续几十毫秒内继续调整 z-order，
        # 用短时连续重排把 overlay 稳定保持在 runtime 之上。
        get_layer_manager().enforce_burst()

    def toggle_fullscreen(self) -> None:
        if self._fullscreen_active:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self) -> None:
        if self._fullscreen_active:
            return
        self._normal_geometry = QRect(self.geometry())
        self._fullscreen_active = True
        self._drag_origin = None
        self._resize_origin = None
        self._resize_start_geometry = None
        self._resize_edges.clear()
        self.setCursor(Qt.ArrowCursor)
        self.showFullScreen()
        self._refresh_size()
        self.update()
        self._game_widget.setFocus(Qt.ActiveWindowFocusReason)
        get_layer_manager().bring_to_front(self)

    def exit_fullscreen(self) -> None:
        if not self._fullscreen_active:
            return
        restore_geometry = QRect(self._normal_geometry)
        self._fullscreen_active = False
        self.showNormal()
        if restore_geometry.isValid():
            self.setGeometry(restore_geometry)
        else:
            self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
            self.move_to_screen_center()
        self._refresh_size()
        self.update()
        QTimer.singleShot(0, lambda: self._game_widget.setFocus(Qt.ActiveWindowFocusReason))
        get_layer_manager().bring_to_front(self)

    def deactivate(self) -> None:
        self._game_widget.deactivate()
        self.exit_fullscreen()

    def fade_in(self) -> None:
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = False
        self.setWindowOpacity(0.0)
        self.show()
        get_layer_manager().bring_to_front(self)
        # show()/raise_() 后不仅当前帧会改 z-order，激活链路也可能稍后再次调整。
        get_layer_manager().enforce_burst()
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(apply_ui_opacity(1.0))
        self._opacity_anim.start()

    def fade_out(self) -> None:
        if self._fading_out or not self.isVisible():
            return
        self._fading_out = True
        self._opacity_anim.stop()
        current_opacity = self.windowOpacity()
        self._opacity_anim.setStartValue(max(0.0, min(1.0, float(current_opacity))))
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.start()

    def hide(self) -> None:
        if self._allow_hide_once or self._fading_out or not self.isVisible():
            super().hide()
            return
        self.fade_out()

    def _on_opacity_anim_finished(self) -> None:
        if not self._fading_out:
            return
        self._fading_out = False
        self._allow_hide_once = True
        try:
            super().hide()
        finally:
            self._allow_hide_once = False
            self.setWindowOpacity(apply_ui_opacity(1.0))

    def _handle_game_close_request(self) -> None:
        runtime = get_game_runtime()
        runtime.close_panel()

    def mousePressEvent(self, event) -> None:
        if self._fullscreen_active:
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        edges = self._hit_test_edges(event.pos())
        if edges:
            self._resize_edges = edges
            self._resize_origin = event.globalPos()
            self._resize_start_geometry = self.geometry()
        else:
            self._drag_origin = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_origin is not None and self._resize_start_geometry is not None and self._resize_edges:
            delta = event.globalPos() - self._resize_origin
            geom = aspect_resize_geometry(
                self._resize_start_geometry,
                self._resize_edges,
                delta,
                self._MINIMUM_WIDTH,
                self._ASPECT_WIDTH,
                self._ASPECT_HEIGHT,
            )
            self.setGeometry(geom)
            event.accept()
            return
        if self._drag_origin is not None:
            self.move(event.globalPos() - self._drag_origin)
            event.accept()
            return
        self._update_cursor(self._hit_test_edges(event.pos()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_origin = None
        self._resize_start_geometry = None
        self._resize_edges.clear()
        self._update_cursor(self._hit_test_edges(event.pos()))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._drag_origin is None and self._resize_origin is None:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._render_core.render(painter, self.rect())
        painter.end()

    def _paint_panel_layer(self, painter: QPainter, _target_rect) -> None:
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self._fullscreen_active:
            painter.fillRect(self.rect(), Qt.black)
            return
        painter.fillRect(self.rect(), self._C_BORDER)
        painter.fillRect(
            self.rect().adjusted(self._LAYER, self._LAYER, -self._LAYER, -self._LAYER),
            self._C_MID,
        )
        content = self.rect().adjusted(self._BORDER, self._BORDER, -self._BORDER, -self._BORDER)
        painter.fillRect(content, self._C_BG)
        painter.fillRect(
            content.adjusted(scale_px(3, min_abs=1), scale_px(3, min_abs=1), -scale_px(3, min_abs=1), -scale_px(3, min_abs=1)),
            QColor(88, 68, 166),
        )

class GameRuntime:
    """小游戏 runtime 控制器。"""

    _GAME_BGM_KEYWORD = "星际穿跃"
    _GAME_BGM_ARTIST = "鸣潮先约电台"

    def __init__(self) -> None:
        self._event_center = get_event_center()
        self._panel = GameRuntimePanel()
        self._open_lahai_sound = AmsOpenLahaiTetrisSound()
        self._bgm_track_ref = ""
        self._bgm_display = ""
        self._bgm_started_by_game = False
        self._open_generation = 0
        self._games = [
            GameMeta("lahai_tetris", "拉海洛方块", "圆角彩虹字母俄罗斯方块", "可玩"),
            GameMeta("snake", "贪吃蛇", "后续预留"),
            GameMeta("minesweeper", "扫雷", "后续预留"),
        ]
        self._panel.set_items(self._games)

        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        get_hash_cmd_registry().register("游戏", "[打开/关闭/列表]", "打开小游戏 runtime 面板")
        get_hash_cmd_registry().register("拉海洛方块", "[打开/关闭]", "打开首款小游戏")
        log("已初始化")

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._panel.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            event.data.get("enabled", False),
        )

    def _on_hash_command(self, event: Event) -> None:
        text = str(event.data.get("text", "")).strip()
        if text.startswith("拉海洛方块"):
            action = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else "打开"
            if action in ("关闭", "close", "退出"):
                self.close_panel()
            else:
                self.open_panel()
            return
        if not text.startswith("游戏"):
            return

        parts = text.split(maxsplit=1)
        action = parts[1].strip() if len(parts) > 1 else "打开"

        if action in ("打开", "open", "启动"):
            self.open_panel()
            return
        if action in ("关闭", "close", "退出"):
            self.close_panel()
            return
        if action in ("列表", "list", "ls"):
            self.report_games()
            return

        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "用法: #游戏 打开 / 关闭 / 列表",
            "min": 0,
            "max": 100,
        }))

    def open_panel(self) -> None:
        self._open_generation += 1
        self._panel.set_items(self._games)
        self._panel.move_to_screen_center()
        self._panel.fade_in()
        self._panel.activate()
        self._open_lahai_sound.play()
        self._play_game_bgm(self._open_generation)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "拉海洛方块已打开",
            "min": 0,
            "max": 80,
        }))

    def close_panel(self) -> None:
        self._open_generation += 1
        self._panel.deactivate()
        self._panel.fade_out()
        if self._bgm_started_by_game:
            self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": False}))
            self._bgm_started_by_game = False
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "小游戏 runtime 已关闭",
            "min": 0,
            "max": 80,
        }))

    def get_lahai_game_middle_third_rect_global(self) -> QRect:
        """返回拉海洛方块游戏区域横向中间三分之一区域的全局矩形。"""
        return self._panel.get_game_middle_third_rect_global()

    def report_games(self) -> None:
        game_text = " / ".join(item.name for item in self._games)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": f"内置小游戏: {game_text}",
            "min": 0,
            "max": 180,
        }))

    def _play_game_bgm(self, open_generation: int) -> None:
        if self._bgm_track_ref:
            self._publish_game_bgm_if_current(open_generation, self._bgm_track_ref, self._bgm_display)
            return
        future = get_compute_hub().submit_latest(
            "game_runtime_bgm_search",
            self._resolve_game_bgm,
            open_generation,
            executor="io",
        )
        if future is None:
            log("游戏BGM搜索任务已在进行中")

    def _resolve_game_bgm(self, open_generation: int) -> None:
        try:
            tracks = get_music_service().search(self._GAME_BGM_KEYWORD, mode="song", limit=20)
        except Exception as e:
            log(f"搜索游戏BGM失败: {e}")
            return
        if not tracks:
            log("搜索游戏BGM无结果")
            return

        keyword_lower = self._GAME_BGM_KEYWORD.lower()
        artist_hint = self._GAME_BGM_ARTIST

        def _priority(track) -> tuple[int, int]:
            title = str(getattr(track, "title", "") or "").strip().lower()
            artist = str(getattr(track, "artist", "") or "").strip()
            exact_title = title == keyword_lower
            artist_match = artist_hint in artist
            if exact_title and artist_match:
                rank = 0
            elif artist_match:
                rank = 1
            elif exact_title:
                rank = 2
            else:
                rank = 3
            return rank, len(title)

        tracks.sort(key=_priority)
        track = tracks[0]
        track_ref = str(getattr(track, "track_id", "") or "").strip()
        if not track_ref:
            log("游戏BGM搜索结果缺少 track_id")
            return
        display = get_music_service().format_track_display(track, include_provider=False)
        self._bgm_track_ref = track_ref
        self._bgm_display = display
        self._publish_game_bgm_if_current(open_generation, track_ref, display)

    def _publish_game_bgm_if_current(self, open_generation: int, track_ref: str, display: str) -> None:
        if open_generation != self._open_generation or not self._panel.isVisible():
            log("游戏BGM结果已过期，跳过自动播放")
            return
        if not self._bgm_started_by_game and not get_music_service().can_takeover_for_bgm():
            log("当前已有音乐播放，跳过游戏BGM自动播放")
            return
        self._event_center.publish(Event(EventType.MUSIC_PLAY_TOP, {
            "song_id": track_ref,
            "track_ref": track_ref,
            "display": display,
        }))
        self._bgm_started_by_game = True

    def cleanup(self) -> None:
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        try:
            self._panel.close()
        except Exception:
            pass
        log("已清理")


_instance: GameRuntime | None = None


def get_game_runtime() -> GameRuntime:
    global _instance
    if _instance is None:
        _instance = GameRuntime()
    return _instance


def cleanup_game_runtime() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
