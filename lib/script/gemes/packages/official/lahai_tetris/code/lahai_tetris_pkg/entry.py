"""Entry point for the official Lahai Tetris package."""

from __future__ import annotations

from lib.script.gemes.MAIN.game_packages import GameContext

from .widget import LahaiTetrisWidget


class LahaiTetrisGame:
    def __init__(self, context: GameContext) -> None:
        self._context = context

    def create_widget(self, parent=None):
        return LahaiTetrisWidget(self._context, parent)
