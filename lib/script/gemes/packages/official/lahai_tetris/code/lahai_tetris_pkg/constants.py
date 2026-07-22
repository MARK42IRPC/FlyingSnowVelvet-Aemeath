"""Shared constants for Lahai Tetris."""

from __future__ import annotations

from PyQt5.QtGui import QColor

BOARD_W = 10
BOARD_H = 20
PREVIEW_GRID = 4
AMS_RECORD_SCORE = 915800
WARNING_LINE_ROW = 6
WARNING_LINE_DEFAULT_HZ = 0.2
WARNING_LINE_FLASH_HZ = 1.0
WARNING_LINE_FLASH_STACK_HEIGHT = 10
STARLIGHT_SKILL_SLOT = 1
STARLIGHT_COOLDOWN_SECS = 22.5
STARLIGHT_CLEAR_ROWS = 3
GRAVITY_SKILL_SLOT = 2
GRAVITY_COOLDOWN_SECS = 60.0
AUTHORIZATION_SKILL_SLOT = 3
AUTHORIZATION_COOLDOWN_SECS = 60.0
RED_BAR_KIND = "A"
RED_BAR_WEIGHT = 5.0
FILL_SKILL_SLOT = 4
FILL_SKILL_COOLDOWN_SECS = 30.0
SPECIAL_FILL_KIND = "SCISSOR"
SPLENDOR_SKILL_SLOT = 5
SPLENDOR_SKILL_COOLDOWN_SECS = 40.0
PARTNER_SKILL_SLOT = 6
PARTNER_SKILL_COOLDOWN_SECS = 20.0
PARTNER_CONVERT_CHANCE = 0.20
SUN_KIND = "SUN"

SHAPES: dict[str, list[tuple[int, int]]] = {
    "A": [(-1, 0), (0, 0), (1, 0), (2, 0)],
    "B": [(0, 0), (1, 0), (0, 1), (1, 1)],
    "C": [(-1, 0), (0, 0), (1, 0), (0, 1)],
    "D": [(0, 0), (1, 0), (-1, 1), (0, 1)],
    "E": [(-1, 0), (0, 0), (0, 1), (1, 1)],
    "F": [(-1, 0), (-1, 1), (0, 0), (1, 0)],
    "G": [(-1, 0), (0, 0), (1, 0), (1, 1)],
}

THEME: dict[str, tuple[QColor, QColor, QColor]] = {
    "A": (QColor(255, 120, 126), QColor(112, 33, 58), QColor(255, 221, 224)),
    "B": (QColor(255, 174, 90), QColor(126, 68, 18), QColor(255, 233, 186)),
    "C": (QColor(255, 221, 96), QColor(120, 100, 20), QColor(255, 243, 181)),
    "D": (QColor(153, 229, 118), QColor(49, 104, 45), QColor(216, 255, 206)),
    "E": (QColor(100, 216, 196), QColor(21, 100, 91), QColor(204, 253, 245)),
    "F": (QColor(108, 164, 255), QColor(28, 63, 126), QColor(211, 227, 255)),
    "G": (QColor(191, 127, 255), QColor(79, 42, 126), QColor(239, 220, 255)),
}
