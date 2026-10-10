"""Game package runtime and manager entry."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.game_obstacles import (
    configure_game_obstacle_provider,
    reset_game_obstacle_provider,
)
from lib.core.render.visuals.types import Rect
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.logger import get_logger
from lib.script.voice.ams_open_lahai_tetris import AmsOpenLahaiTetrisSound
from lib.script.gemes.MAIN.game_packages import (
    GamePackageError,
    GamePackageManifest,
    cleanup_game_package_service,
    get_game_package_service,
)
from lib.script.gemes.MAIN.runtime import build_game_hash_commands
from lib.script.music.service import get_music_service
from lib.script.ui.game_manager_window import GameManagerWindow

from lib.script.ui.game_runtime_panel import (  # noqa: F401 - 既有导入面
    GameRuntimePanel,
    aspect_resize_geometry,
    centered_aspect_rect,
)
_logger = get_logger(__name__)


def log(msg: str) -> None:
    _logger.debug("[GameRuntime] %s", msg)

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
        self._obstacle_provider = self.get_lahai_game_middle_third_rect_global

        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        get_hash_cmd_registry().register("游戏", "[打开/关闭/列表]", "打开游戏列表管理器")
        self.refresh_available_games()
        configure_game_obstacle_provider(self._obstacle_provider)
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
        reset_game_obstacle_provider(self._obstacle_provider)
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
