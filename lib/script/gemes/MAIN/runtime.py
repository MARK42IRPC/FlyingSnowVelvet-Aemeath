"""Game package runtime and manager entry."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from config.config import UI
from lib.core.qt_bridge.font import get_ui_font
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.types import Rect
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.logger import get_logger
from lib.core.qt_bridge.screen import get_screen_geometry_for_point
from lib.core.unified_draw import Layer, RenderCore, RenderRequest, get_layer_manager
from lib.core.voice.ams_open_lahai_tetris import AmsOpenLahaiTetrisSound
from lib.script.gemes.MAIN.game_packages import (
    GamePackageError,
    GamePackageManifest,
    cleanup_game_package_service,
    get_game_package_service,
)
from lib.script.gemes.MAIN.manager_window import GameManagerWindow
from lib.script.music.service import get_music_service

_logger = get_logger(__name__)


def log(msg: str) -> None:
    _logger.debug("[GameRuntime] %s", msg)


def build_game_hash_commands(records) -> list[tuple[str, str, str, str]]:
    entries: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for record in records:
        display_name = str(record.manifest.name).strip()
        if not display_name:
            continue
        for raw_name in (display_name, *record.manifest.command_aliases):
            command_name = str(raw_name).strip()
            if not command_name or command_name in seen:
                continue
            seen.add(command_name)
            description = f"打开{display_name}" if command_name == display_name else f"打开{display_name}（别名）"
            entries.append((command_name, record.game_id, "[打开/关闭]", description))
    return entries


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


class GameRuntimePanel(QWidget):
    """Resizable game runtime host window."""

    _DEFAULT_WIDTH = 1000
    _DEFAULT_HEIGHT = 800
    _MINIMUM_WIDTH = 600
    _MINIMUM_HEIGHT = 480
    _ASPECT_WIDTH = 10
    _ASPECT_HEIGHT = 8

    _PADDING = scale_px(8, min_abs=1)
    _LAYER = scale_px(4, min_abs=1)
    _BORDER = _LAYER * 2
    _RESIZE_MARGIN = scale_px(12, min_abs=1)
    _C_BORDER = QColor(25, 16, 58)
    _C_MID = QColor(145, 122, 232)
    _C_BG = QColor(59, 43, 118)

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
        self._render_core.register_item(RenderRequest("game_runtime_panel_shell", self._paint_panel_layer, Layer.PANEL))

        self._font = get_ui_font()
        self._font.setBold(True)
        self._game_widget: QWidget | None = None
        self._manifest: GamePackageManifest | None = None
        self._active_game_name = ""
        self._close_callback = None
        self._drag_origin: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_edges: set[str] = set()
        self._resize_start_geometry: QRect | None = None
        self._fading_out = False
        self._allow_hide_once = False
        self._fullscreen_active = False
        self._normal_geometry = QRect()
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(UI.get("ui_fade_duration", 180))
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)
        self.setWindowOpacity(0.0)
        self.setMinimumSize(self._MINIMUM_WIDTH, self._MINIMUM_HEIGHT)
        self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
        self.hide()
        self._refresh_size()

    def configure_game(self, manifest: GamePackageManifest, widget: QWidget, close_callback) -> None:
        self._manifest = manifest
        self._active_game_name = manifest.name
        self._close_callback = close_callback
        if self._game_widget is not None:
            try:
                if hasattr(self._game_widget, "deactivate"):
                    self._game_widget.deactivate()
            except Exception:
                pass
            self._game_widget.setParent(None)
            self._game_widget.deleteLater()
        self._game_widget = widget
        self._game_widget.setParent(self)
        self._game_widget.show()
        if hasattr(widget, "set_close_callback"):
            widget.set_close_callback(close_callback)
        if hasattr(widget, "set_fullscreen_callback"):
            widget.set_fullscreen_callback(self.toggle_fullscreen)
        self.setMinimumSize(
            max(320, int(manifest.minimum_width)),
            max(240, int(manifest.minimum_height)),
        )
        self.resize(
            max(self.minimumWidth(), int(manifest.default_width)),
            max(self.minimumHeight(), int(manifest.default_height)),
        )
        self._refresh_size()
        self.update()

    def get_game_middle_third_rect_global(self) -> QRect:
        if not self.isVisible() or self._game_widget is None:
            return QRect()
        local_rect = self._game_widget.geometry()
        third_w = max(1, local_rect.width() // 3)
        middle_x = local_rect.x() + third_w
        middle_rect = QRect(middle_x, local_rect.y(), third_w, local_rect.height())
        return QRect(self.mapToGlobal(middle_rect.topLeft()), middle_rect.size())

    def move_to_screen_center(self) -> None:
        if self._fullscreen_active:
            return
        screen = get_screen_geometry_for_point(fallback_widget=self)
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + (screen.height() - self.height()) // 2
        self.move(x, y)

    def activate(self) -> None:
        if self._game_widget is None:
            return
        if hasattr(self._game_widget, "reset_game"):
            try:
                self._game_widget.reset_game(start_running=False)
            except TypeError:
                self._game_widget.reset_game()
        elif hasattr(self._game_widget, "on_runtime_activated"):
            self._game_widget.on_runtime_activated()
        self._game_widget.show()
        get_layer_manager().bring_to_front(self)
        self.activateWindow()
        self._game_widget.setFocus(Qt.ActiveWindowFocusReason)
        get_layer_manager().enforce_burst()

    def deactivate(self) -> None:
        if self._game_widget is not None and hasattr(self._game_widget, "deactivate"):
            try:
                self._game_widget.deactivate()
            except Exception:
                pass
        self.exit_fullscreen()

    def fade_in(self) -> None:
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = False
        self.setWindowOpacity(0.0)
        self.show()
        get_layer_manager().bring_to_front(self)
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
        if self._game_widget is not None:
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
        if self._game_widget is not None:
            QTimer.singleShot(0, lambda: self._game_widget.setFocus(Qt.ActiveWindowFocusReason))
        get_layer_manager().bring_to_front(self)

    def _current_aspect_width(self) -> int:
        return max(1, int(self._manifest.aspect_width if self._manifest is not None else self._ASPECT_WIDTH))

    def _current_aspect_height(self) -> int:
        return max(1, int(self._manifest.aspect_height if self._manifest is not None else self._ASPECT_HEIGHT))

    def _current_minimum_width(self) -> int:
        return max(320, int(self._manifest.minimum_width if self._manifest is not None else self._MINIMUM_WIDTH))

    def _refresh_size(self) -> None:
        if self._game_widget is None:
            return
        inset = 0 if self._fullscreen_active else self._BORDER
        self._game_widget.setGeometry(
            centered_aspect_rect(
                self.rect(),
                self._current_aspect_width(),
                self._current_aspect_height(),
                inset,
            )
        )

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

    def mouseMoveEvent(self, event) -> None:
        if self._resize_origin is not None and self._resize_start_geometry is not None and self._resize_edges:
            delta = event.globalPos() - self._resize_origin
            geom = aspect_resize_geometry(
                self._resize_start_geometry,
                self._resize_edges,
                delta,
                self._current_minimum_width(),
                self._current_aspect_width(),
                self._current_aspect_height(),
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

    def resizeEvent(self, event) -> None:
        self._refresh_size()
        super().resizeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

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
    """Controller for game manager and active game runtime."""

    def __init__(self) -> None:
        self._event_center = get_event_center()
        self._package_service = get_game_package_service()
        self._registered_game_hash_commands: set[str] = set()
        self._panel = GameRuntimePanel()
        self._manager = GameManagerWindow(self)
        self._open_lahai_sound = AmsOpenLahaiTetrisSound()
        self._active_game_id = ""
        self._active_manifest: GamePackageManifest | None = None
        self._active_entry: Any | None = None
        self._bgm_track_ref = ""
        self._bgm_display = ""
        self._bgm_started_by_game = False
        self._bgm_keyword = ""
        self._bgm_artist = ""
        self._open_generation = 0

        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        get_hash_cmd_registry().register("游戏", "[打开/关闭/列表]", "打开游戏列表管理器")
        self.refresh_available_games()
        log("已初始化")

    def refresh_available_games(self) -> None:
        self._package_service.refresh()
        self._sync_game_hash_commands()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        enabled = event.data.get("enabled", False)
        self._panel.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        self._manager.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)

    def _iter_game_commands(self):
        entries = build_game_hash_commands(self._package_service.list_installed_games())
        for command_name, game_id, _usage, _description in sorted(entries, key=lambda item: len(item[0]), reverse=True):
            yield command_name, game_id

    def _sync_game_hash_commands(self) -> None:
        if not hasattr(self, "_registered_game_hash_commands"):
            self._registered_game_hash_commands = set()
        registry = get_hash_cmd_registry()
        for command_name in tuple(self._registered_game_hash_commands):
            registry.unregister(command_name)

        current: set[str] = set()
        for command_name, _game_id, usage, description in build_game_hash_commands(self._package_service.list_installed_games()):
            registry.register(command_name, usage, description)
            current.add(command_name)
        self._registered_game_hash_commands = current

    def _match_game_command(self, text: str) -> tuple[str, str] | None:
        for key, game_id in self._iter_game_commands():
            if text == key:
                return game_id, ""
            if text.startswith(f"{key} "):
                return game_id, text[len(key):].strip()
        return None

    def _on_hash_command(self, event: Event) -> None:
        text = str(event.data.get("text", "")).strip()
        if not text:
            return

        matched = self._match_game_command(text)
        if matched is not None:
            game_id, rest = matched
            action = rest or "打开"
            if action in ("关闭", "close", "退出"):
                self.close_game(game_id)
            else:
                self.open_game(game_id)
            return

        if not text.startswith("游戏"):
            return

        parts = text.split(maxsplit=1)
        action = parts[1].strip() if len(parts) > 1 else "打开"

        if action in ("打开", "open", "启动", "管理", "manager"):
            self.open_manager()
            return
        if action in ("关闭", "close", "退出"):
            self.close_manager()
            return
        if action in ("列表", "list", "ls"):
            self.report_games()
            return

        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "用法: #游戏 打开 / 关闭 / 列表",
            "min": 0,
            "max": 120,
        }))

    def open_manager(self) -> None:
        self.refresh_available_games()
        self._manager.refresh_games()
        self._manager.fade_in()

    def close_manager(self) -> None:
        try:
            self._manager.hide()
        except Exception:
            pass

    def get_manager_window(self) -> GameManagerWindow:
        return self._manager

    def open_game(self, game_id: str) -> None:
        self.refresh_available_games()
        installed, _context, entry = self._package_service.load_game_entry(game_id)
        if not hasattr(entry, "create_widget"):
            raise GamePackageError(f"{installed.manifest.game_id} 入口未实现 create_widget(parent)")

        widget = entry.create_widget(self._panel)
        self._active_entry = entry
        self._active_game_id = installed.game_id
        self._active_manifest = installed.manifest
        self._bgm_keyword = installed.manifest.bgm_keyword
        self._bgm_artist = installed.manifest.bgm_artist

        self._open_generation += 1
        self._panel.configure_game(installed.manifest, widget, self.close_active_game)
        self._panel.move_to_screen_center()
        self._panel.fade_in()
        self._panel.activate()

        if installed.game_id == "lahai_tetris":
            self._open_lahai_sound.play()
        self._play_game_bgm(self._open_generation)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": f"{installed.manifest.name} 已打开",
            "min": 0,
            "max": 100,
        }))

    def close_game(self, game_id: str) -> None:
        if str(game_id).strip() != self._active_game_id:
            return
        self.close_active_game()

    def close_active_game(self) -> None:
        if not self._active_game_id:
            return
        self._open_generation += 1
        closing_name = self._active_manifest.name if self._active_manifest is not None else "游戏"
        self._panel.deactivate()
        self._panel.fade_out()
        self._active_entry = None
        self._active_game_id = ""
        self._active_manifest = None
        self._bgm_keyword = ""
        self._bgm_artist = ""
        if self._bgm_started_by_game:
            self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {"playing": False}))
            self._bgm_started_by_game = False
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": f"{closing_name} 已关闭",
            "min": 0,
            "max": 80,
        }))

    def get_lahai_game_middle_third_rect_global(self) -> Rect:
        rect = self._panel.get_game_middle_third_rect_global()
        return Rect(rect.x(), rect.y(), rect.width(), rect.height())

    def report_games(self) -> None:
        games = self._package_service.list_installed_games()
        if not games:
            text = "当前没有已安装游戏包"
        else:
            text = "已安装游戏: " + " / ".join(record.manifest.name for record in games)
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": 0,
            "max": 200,
        }))

    def _play_game_bgm(self, open_generation: int) -> None:
        if not self._bgm_keyword:
            return
        if self._bgm_track_ref and self._bgm_display:
            self._publish_game_bgm_if_current(open_generation, self._bgm_track_ref, self._bgm_display)
            return
        future = get_compute_hub().submit_latest(
            "game_runtime_bgm_search",
            self._resolve_game_bgm,
            open_generation,
            executor="io",
        )
        if future is None:
            log("游戏 BGM 搜索任务已在进行中")

    def _resolve_game_bgm(self, open_generation: int) -> None:
        try:
            tracks = get_music_service().search(self._bgm_keyword, mode="song", limit=20)
        except Exception as exc:
            log(f"搜索游戏 BGM 失败: {exc}")
            return
        if not tracks:
            log("搜索游戏 BGM 无结果")
            return

        keyword_lower = self._bgm_keyword.lower()
        artist_hint = self._bgm_artist

        def _priority(track) -> tuple[int, int]:
            title = str(getattr(track, "title", "") or "").strip().lower()
            artist = str(getattr(track, "artist", "") or "").strip()
            exact_title = title == keyword_lower
            artist_match = artist_hint and artist_hint in artist
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
            log("游戏 BGM 搜索结果缺少 track_id")
            return
        display = get_music_service().format_track_display(track, include_provider=False)
        self._bgm_track_ref = track_ref
        self._bgm_display = display
        self._publish_game_bgm_if_current(open_generation, track_ref, display)

    def _publish_game_bgm_if_current(self, open_generation: int, track_ref: str, display: str) -> None:
        if open_generation != self._open_generation or not self._panel.isVisible():
            log("游戏 BGM 结果已过期，跳过自动播放")
            return
        if not self._bgm_started_by_game and not get_music_service().can_takeover_for_bgm():
            log("当前已有音乐播放，跳过游戏 BGM 自动播放")
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
        try:
            self._manager.close()
        except Exception:
            pass
        cleanup_game_package_service()
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
