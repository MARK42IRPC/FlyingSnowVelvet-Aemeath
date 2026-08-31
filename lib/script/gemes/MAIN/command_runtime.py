"""Qt-free game command coordinator used by non-Qt desktop backends."""
from __future__ import annotations

from collections.abc import Callable

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.hash_cmd_registry import get_hash_cmd_registry

from .runtime import build_game_hash_commands


class GameCommandRuntime:
    def __init__(
        self,
        package_service,
        request_launcher: Callable[[str, str], bool],
        *,
        package_cleanup: Callable[[], None] | None = None,
    ) -> None:
        self._event_center = get_event_center()
        self._package_service = package_service
        self._request_launcher = request_launcher
        self._package_cleanup = package_cleanup
        self._registered_commands: set[str] = set()
        self._closed = False
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)
        get_hash_cmd_registry().register("游戏", "[打开/关闭/列表]", "打开游戏列表管理器")
        self.refresh()

    def refresh(self) -> None:
        self._package_service.refresh()
        registry = get_hash_cmd_registry()
        for name in self._registered_commands:
            registry.unregister(name)
        self._registered_commands.clear()
        for name, _game_id, usage, description in build_game_hash_commands(
            self._package_service.list_installed_games()
        ):
            registry.register(name, usage, description)
            self._registered_commands.add(name)

    def _match_game(self, text: str) -> tuple[str, str] | None:
        commands = build_game_hash_commands(self._package_service.list_installed_games())
        for name, game_id, _usage, _description in sorted(
            commands,
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if text == name:
                return game_id, ""
            if text.startswith(f"{name} "):
                return game_id, text[len(name):].strip()
        return None

    def _on_hash_command(self, event: Event) -> None:
        text = str((event.data or {}).get("text") or "").strip()
        if not text:
            return
        matched = self._match_game(text)
        if matched is not None:
            game_id, argument = matched
            action = "close" if argument in {"关闭", "close", "退出"} else "open"
            self._launch(action, game_id)
            return
        if not text.startswith("游戏"):
            return
        parts = text.split(maxsplit=1)
        argument = parts[1].strip() if len(parts) > 1 else "打开"
        if argument in {"打开", "open", "启动", "管理", "manager"}:
            self._launch("open", "")
        elif argument in {"关闭", "close", "退出"}:
            self._launch("close", "")
        elif argument in {"列表", "list", "ls"}:
            self._report_games()
        else:
            self._publish("用法: #游戏 打开 / 关闭 / 列表", maximum=120)

    def _launch(self, action: str, game_id: str) -> None:
        try:
            launched = bool(self._request_launcher(action, game_id))
        except Exception:
            launched = False
        if not launched:
            self._publish("游戏界面启动失败，请从控制面板重试", maximum=120)

    def _report_games(self) -> None:
        games = self._package_service.list_installed_games()
        text = (
            "当前没有已安装游戏包"
            if not games
            else "已安装游戏: " + " / ".join(record.manifest.name for record in games)
        )
        self._publish(text, maximum=200)

    def _publish(self, text: str, *, maximum: int) -> None:
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": 0,
            "max": maximum,
        }))

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        registry = get_hash_cmd_registry()
        registry.unregister("游戏")
        for name in self._registered_commands:
            registry.unregister(name)
        self._registered_commands.clear()
        if self._package_cleanup is not None:
            self._package_cleanup()


__all__ = ["GameCommandRuntime"]
