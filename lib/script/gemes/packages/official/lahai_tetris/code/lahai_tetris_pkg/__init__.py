"""Lahai Tetris package exports."""

from typing import TYPE_CHECKING

from .randomizer import LahaiPieceRandomizer

if TYPE_CHECKING:
    from .widget import LahaiTetrisWidget

__all__ = [
    "LahaiPieceRandomizer",
    "LahaiTetrisWidget",
]


def __getattr__(name: str):
    if name == "LahaiTetrisWidget":
        from .widget import LahaiTetrisWidget

        return LahaiTetrisWidget
    raise AttributeError(name)
