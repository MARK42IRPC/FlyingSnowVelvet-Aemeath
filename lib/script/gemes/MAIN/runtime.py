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

from PyQt5.QtCore import Qt, QPoint, QRect
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from config.font_config import get_ui_font
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.logger import get_logger
from lib.core.screen_utils import get_screen_geometry_for_point
from lib.core.topmost_manager import get_topmost_manager
from lib.core.voice.ams_open_lahai_tetris import AmsOpenLahaiTetrisSound
from lib.script.music.service import get_music_service
from .lahai_tetris import LahaiTetrisWidget

_logger = get_logger(__name__)


def log(msg: str) -> None:
    _logger.debug("[GameRuntime] %s", msg)


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
        get_topmost_manager().register(self)

        self._font = get_ui_font()
        self._font.setBold(True)
        self._items: list[GameMeta] = []
        self._active_game_name = "拉海洛方块"
        self._game_widget = LahaiTetrisWidget(self)
        self._game_widget.set_close_callback(self._handle_game_close_request)
        self.setMinimumSize(scale_px(980, min_abs=1), scale_px(620, min_abs=1))
        self.resize(scale_px(1120, min_abs=1), scale_px(700, min_abs=1))
        self._drag_origin: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_edges: set[str] = set()
        self._resize_start_geometry: QRect | None = None
        self.hide()
        self._refresh_size()

    def set_items(self, items: list[GameMeta]) -> None:
        self._items = list(items)
        self._refresh_size()
        self.update()

    def _refresh_size(self) -> None:
        content_w = self.width() - self._BORDER * 2
        content_h = self.height() - self._BORDER * 2
        game_x = self._BORDER
        game_y = self._BORDER
        self._game_widget.resize(content_w, content_h)
        self._game_widget.move(game_x, game_y)

    def _hit_test_edges(self, pos) -> set[str]:
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
        self._game_widget.raise_()
        self.activateWindow()
        self._game_widget.setFocus(Qt.ActiveWindowFocusReason)

    def deactivate(self) -> None:
        self._game_widget.deactivate()

    def _handle_game_close_request(self) -> None:
        self.hide()
        runtime = get_game_runtime()
        runtime.close_panel()

    def mousePressEvent(self, event) -> None:
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

    def mouseMoveEvent(self, event) -> None:
        if self._resize_origin is not None and self._resize_start_geometry is not None and self._resize_edges:
            delta = event.globalPos() - self._resize_origin
            geom = QRect(self._resize_start_geometry)
            min_w = self.minimumWidth()
            min_h = self.minimumHeight()
            if "left" in self._resize_edges:
                new_left = min(geom.right() - min_w, geom.left() + delta.x())
                geom.setLeft(new_left)
            if "right" in self._resize_edges:
                geom.setRight(max(geom.left() + min_w, geom.right() + delta.x()))
            if "top" in self._resize_edges:
                new_top = min(geom.bottom() - min_h, geom.top() + delta.y())
                geom.setTop(new_top)
            if "bottom" in self._resize_edges:
                geom.setBottom(max(geom.top() + min_h, geom.bottom() + delta.y()))
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
        painter.setRenderHint(QPainter.Antialiasing, False)
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
        painter.end()

class GameRuntime:
    """小游戏 runtime 控制器。"""

    _GAME_BGM_KEYWORD = "星际穿跃"
    _GAME_BGM_ARTIST = "鸣潮先约电台"

    def __init__(self) -> None:
        self._event_center = get_event_center()
        self._panel = GameRuntimePanel()
        self._open_lahai_sound = AmsOpenLahaiTetrisSound()
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
        self._panel.set_items(self._games)
        self._panel.move_to_screen_center()
        self._panel.show()
        self._panel.raise_()
        self._panel.activate()
        self._open_lahai_sound.play()
        self._play_game_bgm()
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "拉海洛方块已打开",
            "min": 0,
            "max": 80,
        }))

    def close_panel(self) -> None:
        self._panel.deactivate()
        self._panel.hide()
        self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {}))
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "小游戏 runtime 已关闭",
            "min": 0,
            "max": 80,
        }))

    def report_games(self) -> None:
        game_text = " / ".join(item.name for item in self._games)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": f"内置小游戏: {game_text}",
            "min": 0,
            "max": 180,
        }))

    def _play_game_bgm(self) -> None:
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
        self._event_center.publish(Event(EventType.MUSIC_PLAY_TOP, {
            "song_id": track_ref,
            "track_ref": track_ref,
            "display": display,
        }))

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
