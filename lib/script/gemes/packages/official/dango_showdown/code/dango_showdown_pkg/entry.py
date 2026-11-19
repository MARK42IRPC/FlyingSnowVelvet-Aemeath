"""Entry point for the official Dango Showdown package."""

from __future__ import annotations

from lib.script.gemes.MAIN.game_packages import GameContext

from .widget import DangoShowdownWidget


class DangoShowdownGame:
    def __init__(self, context: GameContext) -> None:
        self._context = context

    def create_widget(self, parent=None):
        return DangoShowdownWidget(self._context, parent)
