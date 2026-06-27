"""小游戏运行时主入口。"""

from .runtime import get_game_runtime, cleanup_game_runtime

__all__ = [
    "get_game_runtime",
    "cleanup_game_runtime",
]
