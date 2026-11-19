"""Tunable numbers and tokens for Dango Showdown.

Pure Python on purpose: nothing here may depend on a GUI toolkit so the
physics and combat tests can import it in a PyQt-blocked interpreter.
"""

from __future__ import annotations

from lib.core.render.visuals.types import Color

MAX_HP = 100
DAMAGE_PER_IMPACT = 1
WALL_JITTER_DEG = 3.0
MIN_IMPACT_SPEED = 0.05
SKILL_TURN_DEG = 55.0
BASE_RADIUS = 0.085
SEPARATION_MARGIN = 0.012
FIXED_STEP_SECONDS = 1.0 / 60.0
MAX_STEPS_PER_FRAME = 5
LAUNCH_SPEED_RANGE = (0.85, 1.0)
# Fired away from the enemy after the starburst resets it to the bottom centre.
STARBURST_LAUNCH_SPEED = 0.92

LAUNCH_ANGLE_LIMIT_DEG = 38.0
SPRITE_DIAMETER_RATIO = 1.62
SPRITE_TARGET_PX = 256
COUNTDOWN_SECONDS = 1.8
LASER_EFFECT_SECONDS = 1.0
LASER_FADE_SECONDS = 0.12
LASER_TRAVEL_SECONDS = 0.10
STARBURST_EFFECT_SECONDS = 1.0
STARBURST_GIF_SECONDS = 2.0
STARBURST_IMPACT_SECONDS = 0.91
STARBURST_FADE_SECONDS = 0.18
STARBURST_SHAKE = 1.6
RESULT_MIN_SECONDS = 0.6
# The round stops in place; the result panel waits out this beat.
RESULT_REVEAL_SECONDS = 3.0
VOID_HAND_EFFECT_SECONDS = 2.0
VOID_HAND_FEATHER_PX = 50.0
# Displayed size of the void-hand art (canvas px, relative to the arena side).
VOID_HAND_ARENA_RATIO = 0.52
# Starburst finale: enemy is parked at the bottom centre, so its resting
# hurt pose can be painted below the live ball without leaving the arena.
RESULT_POSE_RATIO = 0.185
ARENA_WATERMARK_ALPHA = 0.26
ARENA_WATERMARK_SIZE_RATIO = 0.86

IDLE = "idle"
ATTACK = "attack"
HURT = "hurt"

ATTACK_SECONDS = 0.5
# A skill cast lights the caster's card for this long.
CARD_FLASH_SECONDS = 0.45
# The settlement card stays up this long, then the next round starts.
AUTO_ADVANCE_SECONDS = 3.0
HURT_SECONDS = 0.3
IDLE_STRETCH_FREQ = 1.7
IDLE_STRETCH_AMPLITUDE = 0.045
ATTACK_STRETCH_FREQ = 9.0
ATTACK_STRETCH_AMPLITUDE = 0.20
HURT_STRETCH_FREQ = 10.0
# The hurt pose settles into a broad, flattened body instead of a round one.
HURT_FLATTEN = 0.72
HURT_FLASH_SECONDS = 0.3
HURT_FLASH_PERIOD = 0.05
HURT_FLASH_ALPHA = 0.62
HURT_TINT = Color(255, 72, 92)

# Floating damage numbers: every hit deals the same damage, so every number is
# the same bold size; the variable only keeps older call sites compiling.
DAMAGE_NUMBER_MIN_SIZE = 26
DAMAGE_NUMBER_SIZE_STEP = 3
DAMAGE_NUMBER_MAX_SIZE = 58
LASER_TEXTURE_SIZE = (1024, 96)


def damage_number_size(amount: int) -> int:
    """Font pixels for a floating damage number; more damage reads bigger."""
    points = max(0, int(amount))
    size = DAMAGE_NUMBER_MIN_SIZE + DAMAGE_NUMBER_SIZE_STEP * max(0, points - 1)
    return min(DAMAGE_NUMBER_MAX_SIZE, size)

BET_AMOUNTS = (1, 5, 10)
BET_MULTIPLIER = 1
BET_SIDES = ("left", "right")

STATE_KEYS = ("current_streak", "best_streak", "total_rounds", "total_bet_wins", "total_bet_losses", "total_draws", "chips")

# ── Hard-sci-fi geometric palette ────────────────────────────────────────────
# Master palette: "Midnight Navy / Azure Signal / Frost". Five hues only —
# navy (structure), azure (signal), ice cyan (highlight), signal red (danger,
# HP, damage) and amber (reward). Roster tints and the two skill beams are
# character identity and live in roster.py / the presenter.
#
#   navy    #050C16  #080F1B  #0D192A  #13253E  #1A334C
#   azure   #1F4E73  #34669E  #4A82BF  #6694E8  #78C0FF
#   cyan    #68B2EE  #5EC0E0  #48BEFF  #88E2D2  #B4ECFF
#   ice     #5E6674  #8AA2B8  #C6D4E4  #E2ECF7  #F4F8FC
#   red     #B4323C  #E63C46  #FF404E  #FF4656  #FF7380
#   amber   #FFC452
C_BG_TOP = Color(5, 12, 22)
C_BG_BOTTOM = Color(8, 15, 27)
C_BG_GEO = Color(31, 78, 115, 46)
C_GRID = Color(52, 102, 158, 22)
C_GRID_MAJOR = Color(72, 136, 191, 46)
C_PANEL = Color(13, 25, 42, 236)
C_PANEL_LIFTED = Color(19, 37, 62, 244)
C_PANEL_DEEP = Color(8, 15, 27, 248)
C_PANEL_EDGE = Color(52, 102, 158)
C_PANEL_EDGE_BRIGHT = Color(104, 148, 232)
C_PANEL_INNER = Color(72, 136, 191, 86)
C_ARENA = Color(5, 12, 22, 255)
C_ARENA_EDGE = Color(72, 190, 255)
C_TEXT = Color(226, 236, 247)
C_TEXT_DIM = Color(138, 162, 184)
C_TEXT_FAINT = Color(78, 102, 130)
C_ACCENT = Color(120, 192, 255)
C_ACCENT_DEEP = Color(52, 102, 158)
C_GOLD = Color(255, 196, 82)
C_RED = Color(255, 70, 86)
C_GREEN = Color(136, 226, 210)
C_SIDE_A = Color(104, 178, 238)
C_SIDE_B = Color(144, 226, 255)
# Every HP decrease uses one bright signal red, in bars and numbers alike.
C_HP_BACK = Color(19, 37, 62, 255)
C_HP_FILL = Color(255, 64, 78, 255)
C_HP_FILL_LOW = Color(255, 64, 78, 255)
C_DAMAGE = Color(255, 64, 78, 255)
# Character identity / skill beams (not chrome).
C_CHARGE_BLUE = Color(72, 190, 255, 255)
C_CHARGE_PINK = Color(255, 120, 200, 255)
C_CHARGE_SLOT = Color(8, 15, 27, 255)
C_SELECT_HEADER = Color(8, 15, 27, 246)
C_SELECT_DIM = Color(5, 12, 22, 228)
C_SELECT_CARD = Color(19, 37, 62, 246)
C_SELECT_JOIN = Color(255, 70, 86)
C_BUTTON = Color(19, 37, 62)
C_BUTTON_ACTIVE = Color(52, 102, 158)
C_BUTTON_EDGE = Color(104, 148, 232)
C_BADGE_A = Color(31, 78, 115)
C_BADGE_B = Color(52, 102, 158)
# Skill-cast glow around the caster's card.
C_CARD_FLASH = Color(255, 226, 138)

# Sun spirits keep their supplied yellow/cyan identity in both HUD and arena.
C_SUN_YELLOW = Color(255, 215, 92)
C_SUN_CYAN = Color(82, 232, 229)
SUN_BURST_EFFECT_SECONDS = 0.58
SUN_SPIRIT_SIZE_RATIO = 0.22


def damage_color() -> Color:
    return C_DAMAGE


def damage_rgb() -> tuple[int, int, int]:
    color = damage_color()
    return color.red, color.green, color.blue
