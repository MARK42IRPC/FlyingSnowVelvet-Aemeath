"""Backend-neutral game runtime facade."""

from __future__ import annotations

from importlib import import_module


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
            description = (
                f"打开{display_name}"
                if command_name == display_name
                else f"打开{display_name}（别名）"
            )
            entries.append((command_name, record.game_id, "[打开/关闭]", description))
    return entries


def get_game_runtime():
    module = import_module("lib.script.ui.game_runtime")
    return module.get_game_runtime()


def cleanup_game_runtime() -> None:
    module = import_module("lib.script.ui.game_runtime")
    module.cleanup_game_runtime()
