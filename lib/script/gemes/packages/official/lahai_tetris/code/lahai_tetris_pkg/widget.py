"""拉海洛方块。

独立小游戏模块，负责：
- 俄罗斯方块核心逻辑
- 主题化绘制（圆角彩虹字母砖块）
- 本地键盘输入与帧循环
"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

from PyQt5.QtCore import QEasingCurve, QPoint, Qt, QRectF, QTimer, QVariantAnimation
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget

from lib.core.qt_bridge.font import get_digit_font, get_ui_font
from config.scale import scale_px
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.effect_utils import spawn_flash_text_effect, spawn_smooth_image_effect
from lib.core.particle_utils import spawn_particle_at_point, spawn_particle_in_rect
from lib.core.unified_draw import Layer
from lib.core.qt_bridge.render_core import QtRenderCore, QtRenderRequest
from lib.script.voice.ams_lahai_break_ams_record import AmsLahaiBreakAmsRecordSound
from lib.script.voice.ams_lahai_combo_over_five import AmsLahaiComboOverFiveSound
from lib.script.voice.ams_lahai_game_over import AmsLahaiGameOverSound
from lib.script.voice.ams_lahai_idle_chat import AmsLahaiIdleChatSound
from lib.script.voice.ams_lahai_level_up import AmsLahaiLevelUpSound
from lib.script.voice.ams_lahai_score_10000 import AmsLahaiScore10000Sound
from lib.script.voice.ams_lahai_score_1000 import AmsLahaiScore1000Sound
from lib.script.voice.ams_lahai_score_5000 import AmsLahaiScore5000Sound
from lib.script.voice.lahai_skill_release import LahaiSkillReleaseFailedSound, LahaiSkillReleaseSound
from lib.script.gemes.MAIN.game_packages import GameContext
from lib.script.gemes.MAIN.game_sfx import GameSfx
from .constants import (
    AMS_RECORD_SCORE as _AMS_RECORD_SCORE,
    AUTHORIZATION_SKILL_SLOT as _AUTHORIZATION_SKILL_SLOT,
    BOARD_H as _BOARD_H,
    BOARD_W as _BOARD_W,
    FILL_SKILL_SLOT as _FILL_SKILL_SLOT,
    GRAVITY_SKILL_SLOT as _GRAVITY_SKILL_SLOT,
    PARTNER_SKILL_SLOT as _PARTNER_SKILL_SLOT,
    PARTNER_CONVERT_CHANCE as _PARTNER_CONVERT_CHANCE,
    SHAPES as _SHAPES,
    SPECIAL_FILL_KIND as _SPECIAL_FILL_KIND,
    SPLENDOR_SKILL_SLOT as _SPLENDOR_SKILL_SLOT,
    STARLIGHT_SKILL_SLOT as _STARLIGHT_SKILL_SLOT,
    SUN_KIND as _SUN_KIND,
    THEME as _THEME,
    WARNING_LINE_FLASH_STACK_HEIGHT as _WARNING_LINE_FLASH_STACK_HEIGHT,
)
from .model import (
    build_fill_columns_result,
    collapse_empty_rows,
    convert_board_cells,
    count_kind_cells,
    Piece,
    apply_board_gravity,
    can_place,
    clear_rows,
    clear_board_cells,
    create_empty_board,
    find_full_rows,
    hard_drop_target,
    lowest_fill_columns,
    place_piece,
    reset_piece,
    rotate_piece,
    rows_with_more_than_four_colors,
    settled_stack_height,
    translate_piece,
)
from .randomizer import LahaiPieceRandomizer
from .render import (
    draw_avatar_skill_slot,
    draw_block,
    draw_preview,
    draw_round_panel,
    draw_skill_avatar,
    draw_skill_slots,
    draw_warning_line,
    paint_widget,
)
from .skills import AuthorizationSkillSlot, EmptySkillSlot, FillSkillSlot, GravitySkillSlot, LahaiSkillSlot, PartnerSkillSlot, SplendorSkillSlot, StarlightSkillSlot
from .stats import LahaiTetrisStats


_GROUND_LOCK_DELAY_MS = 500
_GROUND_LOCK_DELAY_STEP_MS = 100
_GROUND_LOCK_DELAY_RESET_MOVES = 5
_HARD_DROP_SKILL_REWARD_SECS = 1.0
_SKILL_EFFECT_INTRO_SECS = 0.3
_SKILL_EFFECT_DISPLAY_SECS = 0.6
_SKILL_EFFECT_OUTRO_SECS = 0.3
_STARLIGHT_FLASH_FADE_IN_SECS = 0.3
_STARLIGHT_FLASH_FADE_IN_HZ = 10.0
_STARLIGHT_FLASH_HOLD_SECS = 1.0
_STARLIGHT_FLASH_FADE_OUT_SECS = 0.3
_STARLIGHT_FLASH_FADE_OUT_HZ = 5.0
_SKILL_FLASH_FONT_SIZE = 40
_SKILL_FLASH_GLOW = 12.0
_PARTNER_BURST_STEP_MS = 100
_SKILL_PAUSE_MS = 2000
_SKILL_CHAIN_COOLDOWN_PENALTY_SECS = 10.0
_STAT_GAIN_COLORS = {
    "分数": (146, 255, 170),
    "消行": (146, 255, 170),
    "等级": (146, 255, 170),
    "连击": (146, 255, 170),
}
_STAT_LOSS_COLOR = (255, 150, 150)
_SKILL_FLASH_TEXTS = {
    _STARLIGHT_SKILL_SLOT: ("随机消除三行", (255, 196, 220)),
    _GRAVITY_SKILL_SLOT: ("重力压实所有方块", (216, 162, 255)),
    _AUTHORIZATION_SKILL_SLOT: ("短时间大幅提升红条概率", (226, 206, 255)),
    _FILL_SKILL_SLOT: ("填充填充率最低的三列", (138, 58, 78)),
    _SPLENDOR_SKILL_SLOT: ("消除颜色大于4的行", (110, 238, 204)),
    _PARTNER_SKILL_SLOT: ("引爆并生成日灵方块", (255, 214, 92)),
}


class LahaiTetrisWidget(QWidget):
    """拉海洛方块游戏部件。"""

    _PADDING = scale_px(14, min_abs=1)
    _GAP = scale_px(12, min_abs=1)
    _HEADER_H = scale_px(42, min_abs=1)
    _PANEL_RADIUS = float(scale_px(12, min_abs=1))

    _C_BG = QColor(24, 16, 52)
    _C_BG_GLOW = QColor(193, 118, 255, 74)
    _C_BG_GRID = QColor(196, 168, 255, 58)
    _C_PANEL = QColor(76, 98, 188, 108)
    _C_PANEL_ALT = QColor(66, 136, 214, 118)
    _C_BORDER = QColor(211, 197, 255, 230)
    _C_BORDER_DEEP = QColor(56, 44, 92, 220)
    _C_GRID = QColor(160, 189, 255, 58)
    _C_TEXT = QColor(243, 239, 255)
    _C_TEXT_SUB = QColor(199, 192, 235)
    _C_BOARD = QColor(54, 44, 110)
    _C_BOARD_INNER = QColor(22, 28, 72)
    _C_GAMEOVER = QColor(18, 14, 40, 188)
    _C_SCANLINE = QColor(255, 255, 255, 12)
    _C_NEON = QColor(117, 233, 255)
    _C_PANEL_LINE = QColor(186, 231, 255, 168)
    _C_PANEL_EDGE = QColor(34, 52, 112, 210)

    def __init__(self, context: GameContext, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._render_core = QtRenderCore()
        self._render_core.register_item(QtRenderRequest(
            'lahai_tetris_content',
            self._paint_game_layer,
            Layer.PANEL,
        ))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setMinimumSize(1, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._warning_pulse_timer = QTimer(self)
        self._warning_pulse_timer.setInterval(50)
        self._warning_pulse_timer.timeout.connect(self._on_warning_pulse)

        self._board: list[list[str | None]] = []
        self._current: Piece | None = None
        self._next_piece: Piece | None = None
        self._rng = random.Random()
        self._piece_randomizer = LahaiPieceRandomizer(tuple(_SHAPES.keys()), self._rng)
        self._score = 0
        self._lines = 0
        self._level = 1
        self._combo = 0
        self._soft_drop = False
        self._game_over = False
        self._block_size = scale_px(24, min_abs=1)
        self._left_rect = QRectF()
        self._center_rect = QRectF()
        self._right_rect = QRectF()
        self._board_outer_rect = QRectF()
        self._board_inner_rect = QRectF()
        self._preview_rect = QRectF()
        self._help_rect = QRectF()
        self._stat_cards: list[tuple[str, QRectF]] = []
        self._skill_slots: list[tuple[int, QRectF]] = []
        self._hovered_skill_slot_index: int | None = None
        self._pause_rect = QRectF()
        self._exit_rect = QRectF()
        self._tips: list[str] = self._load_tips()
        self._tips_used: set[str] = set()
        self._current_tip = "暂无tips"
        self._skills: dict[int, LahaiSkillSlot] = {
            _STARLIGHT_SKILL_SLOT: StarlightSkillSlot(),
            _GRAVITY_SKILL_SLOT: GravitySkillSlot(),
            _AUTHORIZATION_SKILL_SLOT: AuthorizationSkillSlot(),
            _FILL_SKILL_SLOT: FillSkillSlot(),
            _SPLENDOR_SKILL_SLOT: SplendorSkillSlot(),
            _PARTNER_SKILL_SLOT: PartnerSkillSlot(),
        }
        self._skill_avatar_cache: dict[str, QPixmap] = {}
        self._anim_from_cells: list[tuple[float, float]] = []
        self._anim_to_cells: list[tuple[float, float]] = []
        self._anim_progress = 1.0
        self._settled_fall_anim: dict[tuple[int, int], tuple[str, float, float, float]] = {}
        self._fill_anim_cells: list[tuple[int, int]] = []
        self._fill_anim_progress = 0.0
        self._pending_fill_clear_rows: list[int] | None = None
        self._block_pixmap_cache: dict[tuple[str, int], QPixmap] = {}
        self._settled_board_cache: QPixmap | None = None
        self._static_scene_cache: QPixmap | None = None
        self._static_scene_cache_dirty = True
        self._settled_board_cache_dirty = True
        self._pending_settled_clear_rows: list[int] | None = None
        self._board_resolution_locked = False
        self._skill_pause_active = False
        self._queued_skill_slot_index: int | None = None
        self._special_fill_particle_accum = 0.0
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self._pending_shake_force = 0.0
        self._ground_lock_resets_used = 0
        self._paused = False
        self._awaiting_start = True
        self._close_callback = None
        self._fullscreen_callback = None
        self._score_1000_triggered = False
        self._score_5000_triggered = False
        self._score_10000_triggered = False
        self._combo_over_five_triggered = False
        self._record_broken_triggered = False
        self._game_over_voice_triggered = False

        self._ui_font = get_ui_font(scale_px(13, min_abs=1))
        self._title_font = get_ui_font(scale_px(18, min_abs=1))
        self._label_font = get_ui_font(scale_px(12, min_abs=1))
        self._digit_font = get_digit_font(scale_px(18, min_abs=1))
        self._stat_label_font = get_ui_font(scale_px(16, min_abs=1))
        self._stat_digit_font = get_digit_font(scale_px(28, min_abs=1))
        self._block_font = get_digit_font(scale_px(12, min_abs=1))
        self._stats = LahaiTetrisStats(self._context.data_root / "state.json")
        self._best_score = self._stats.get_best_score()
        self._refresh_fonts()
        self._update_layout_metrics()

        self._piece_anim = QVariantAnimation(self)
        self._piece_anim.setDuration(self._piece_animation_duration_ms())
        self._piece_anim.setStartValue(0.0)
        self._piece_anim.setEndValue(1.0)
        self._piece_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._piece_anim.valueChanged.connect(self._on_piece_anim_value_changed)
        self._piece_anim.finished.connect(self._on_piece_anim_finished)

        self._board_shake_anim = QVariantAnimation(self)
        self._board_shake_anim.setDuration(180)
        self._board_shake_anim.setStartValue(0.0)
        self._board_shake_anim.setEndValue(1.0)
        self._board_shake_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._board_shake_anim.valueChanged.connect(self._on_board_shake_value_changed)
        self._board_shake_anim.finished.connect(self._on_board_shake_finished)

        self._settled_anim = QVariantAnimation(self)
        self._settled_anim.setDuration(220)
        self._settled_anim.setStartValue(0.0)
        self._settled_anim.setEndValue(1.0)
        self._settled_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._settled_anim.valueChanged.connect(self._on_settled_anim_value_changed)
        self._settled_anim.finished.connect(self._on_settled_anim_finished)

        self._fill_anim = QVariantAnimation(self)
        self._fill_anim.setDuration(200)
        self._fill_anim.setStartValue(0.0)
        self._fill_anim.setEndValue(1.0)
        self._fill_anim.setEasingCurve(QEasingCurve.Linear)
        self._fill_anim.valueChanged.connect(self._on_fill_anim_value_changed)
        self._fill_anim.finished.connect(self._on_fill_anim_finished)

        self._pending_lock_timer = QTimer(self)
        self._pending_lock_timer.setSingleShot(True)
        self._pending_lock_timer.timeout.connect(self._finish_hard_drop_lock)
        self._pending_lock_mode: str | None = None
        self._pending_lock_remaining_ms = 0
        self._skill_pause_timer = QTimer(self)
        self._skill_pause_timer.setSingleShot(True)
        self._skill_pause_timer.timeout.connect(self._finish_skill_pause)
        self._idle_chat_timer = QTimer(self)
        self._idle_chat_timer.setInterval(15000)
        self._idle_chat_timer.timeout.connect(self._maybe_play_idle_chat)
        self._tip_rotate_timer = QTimer(self)
        self._tip_rotate_timer.setInterval(10000)
        self._tip_rotate_timer.timeout.connect(self._rotate_tip_text)
        self._partner_burst_timer = QTimer(self)
        self._partner_burst_timer.setSingleShot(True)
        self._partner_burst_timer.timeout.connect(self._advance_partner_burst_sequence)

        self._sfx = GameSfx()
        self._skill_release_sounds: dict[str, LahaiSkillReleaseSound] = {}
        self._skill_release_failure_sounds: dict[str, LahaiSkillReleaseFailedSound] = {}
        self._score_1000_sound = AmsLahaiScore1000Sound()
        self._score_5000_sound = AmsLahaiScore5000Sound()
        self._score_10000_sound = AmsLahaiScore10000Sound()
        self._combo_over_five_sound = AmsLahaiComboOverFiveSound()
        self._level_up_sound = AmsLahaiLevelUpSound()
        self._game_over_sound = AmsLahaiGameOverSound()
        self._break_ams_record_sound = AmsLahaiBreakAmsRecordSound()
        self._idle_chat_sound = AmsLahaiIdleChatSound()
        self._pending_floating_text_particles: dict[tuple, dict[str, object]] = {}
        self._floating_text_flush_scheduled = False
        self._skill_sequence_freeze_active = False
        self._pending_partner_burst_origins: list[tuple[int, int]] = []
        self._pending_partner_convert_after_burst = False
        self._event_center = get_event_center()

        self.reset_game(start_running=False)

    def reset_game(self, start_running: bool = True) -> None:
        self._board = create_empty_board()
        self._mark_settled_board_cache_dirty()
        self._score = 0
        self._lines = 0
        self._level = 1
        self._combo = 0
        self._soft_drop = False
        self._game_over = False
        self._pending_lock_timer.stop()
        self._pending_lock_mode = None
        self._pending_lock_remaining_ms = 0
        self._piece_anim.stop()
        self._settled_anim.stop()
        self._fill_anim.stop()
        self._board_shake_anim.stop()
        self._settled_fall_anim = {}
        self._fill_anim_cells = []
        self._fill_anim_progress = 0.0
        self._pending_fill_clear_rows = None
        self._pending_settled_clear_rows = None
        self._board_resolution_locked = False
        self._skill_pause_timer.stop()
        self._skill_pause_active = False
        self._queued_skill_slot_index = None
        self._hovered_skill_slot_index = None
        self._special_fill_particle_accum = 0.0
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self._pending_shake_force = 0.0
        self._ground_lock_resets_used = 0
        self._paused = False
        self._awaiting_start = not start_running
        self._score_1000_triggered = False
        self._score_5000_triggered = False
        self._score_10000_triggered = False
        self._combo_over_five_triggered = False
        self._record_broken_triggered = False
        self._game_over_voice_triggered = False
        self._pending_floating_text_particles.clear()
        self._floating_text_flush_scheduled = False
        self._tips_used.clear()
        self._current_tip = self._pick_next_tip()
        for skill in self._skills.values():
            skill.reset()
        self._piece_randomizer.reset()
        self._next_piece = self._make_piece()
        self._spawn_piece()
        if start_running:
            self._timer.start(self._fall_interval_ms())
            self._idle_chat_timer.start()
        else:
            self._timer.stop()
            self._idle_chat_timer.stop()
        self._tip_rotate_timer.start()
        self._refresh_warning_timer()
        self._partner_burst_timer.stop()
        self._skill_sequence_freeze_active = False
        self._pending_partner_burst_origins.clear()
        self._pending_partner_convert_after_burst = False
        self.update()

    def deactivate(self) -> None:
        self._timer.stop()
        self._warning_pulse_timer.stop()
        self._idle_chat_timer.stop()
        self._tip_rotate_timer.stop()
        self._pending_lock_timer.stop()
        self._pending_lock_mode = None
        self._pending_lock_remaining_ms = 0
        self._skill_pause_timer.stop()
        self._skill_pause_active = False
        self._queued_skill_slot_index = None
        self._hovered_skill_slot_index = None
        self._special_fill_particle_accum = 0.0
        self._piece_anim.stop()
        self._settled_anim.stop()
        self._fill_anim.stop()
        self._board_shake_anim.stop()
        self._settled_fall_anim = {}
        self._fill_anim_cells = []
        self._fill_anim_progress = 0.0
        self._pending_fill_clear_rows = None
        self._pending_settled_clear_rows = None
        self._board_resolution_locked = False
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self._pending_shake_force = 0.0
        self._ground_lock_resets_used = 0
        self._paused = False
        self._awaiting_start = True
        self._game_over_voice_triggered = False
        self._pending_floating_text_particles.clear()
        self._floating_text_flush_scheduled = False
        self._partner_burst_timer.stop()
        self._skill_sequence_freeze_active = False
        self._pending_partner_burst_origins.clear()
        self._pending_partner_convert_after_burst = False
        self._tip_rotate_timer.stop()
        self.update()

    def set_close_callback(self, callback) -> None:
        self._close_callback = callback

    def set_fullscreen_callback(self, callback) -> None:
        self._fullscreen_callback = callback

    def start_game(self) -> None:
        if not self._awaiting_start or self._game_over:
            return
        self._awaiting_start = False
        self._paused = False
        self._refresh_warning_timer()
        self._timer.start(self._fall_interval_ms())
        self._idle_chat_timer.start()
        self.update()

    def _make_piece(self) -> Piece:
        return Piece(self._piece_randomizer.next_kind())

    def _replace_next_piece(self, kind: str) -> None:
        self._next_piece = Piece(str(kind))
        self._spawn_preview_replace_particles(str(kind))
        self.update()

    def _spawn_board_data_particles(self, cells: list[tuple[int, int]], rgb: tuple[int, int, int]) -> None:
        inner = self._board_inner_screen_rect()
        for x, y in cells:
            rect = self._cell_rect(inner, float(x), float(y))
            gx, gy = self._to_global_point(rect.center().x(), rect.center().y())
            spawn_particle_at_point(gx, gy, "flicker_data", {
                "rgb": rgb,
            })

    def _apply_fill_skill(self) -> bool:
        candidate_columns = lowest_fill_columns(self._board, count=3)
        columns = [
            col for col in candidate_columns
            if any(self._board[row][col] is not None for row in range(_BOARD_H))
        ]
        next_board, added_cells = build_fill_columns_result(self._board, columns, _SPECIAL_FILL_KIND)
        if not added_cells:
            return 0
        self._board = next_board
        self._mark_settled_board_cache_dirty()
        self._fill_anim.stop()
        self._fill_anim_cells = sorted(added_cells, key=lambda cell: (cell[0], -cell[1]))
        self._fill_anim_progress = 0.0
        self._pending_fill_clear_rows = find_full_rows(self._board)
        self._board_resolution_locked = True
        self._spawn_board_data_particles(added_cells, (196, 54, 120))
        self._fill_anim.start()
        return len(columns)

    def _apply_splendor_skill(self) -> bool:
        cleared_rows = rows_with_more_than_four_colors(self._board)
        if not cleared_rows:
            return False
        return self._clear_board_rows(cleared_rows)

    def _apply_partner_skill(self) -> bool:
        burst_origins = [
            (x, y)
            for y, row in enumerate(self._board)
            for x, cell in enumerate(row)
            if cell == _SUN_KIND
        ]
        if burst_origins:
            self._skill_sequence_freeze_active = True
            self._pending_partner_burst_origins = list(burst_origins)
            self._pending_partner_convert_after_burst = True
            self._partner_burst_timer.start(0)
            return True
        return self._generate_partner_sun_cells()

    def _generate_partner_sun_cells(self) -> bool:
        next_board, changed = convert_board_cells(
            self._board,
            rng=self._rng,
            chance=_PARTNER_CONVERT_CHANCE,
            target_kind=_SUN_KIND,
        )
        if changed > 0:
            self._board = next_board
            self._mark_settled_board_cache_dirty()
            sun_cells = [(x, y) for y, row in enumerate(self._board) for x, cell in enumerate(row) if cell == _SUN_KIND]
            self._spawn_board_data_particles(sun_cells, (255, 212, 84))
            self.update()
            return True
        return False

    def _advance_partner_burst_sequence(self) -> None:
        current_sun_origins = [
            (x, y)
            for y, row in enumerate(self._board)
            for x, cell in enumerate(row)
            if cell == _SUN_KIND
        ]
        self._pending_partner_burst_origins = list(current_sun_origins)
        if not self._pending_partner_burst_origins:
            pending_convert = self._pending_partner_convert_after_burst
            self._pending_partner_convert_after_burst = False
            self._skill_sequence_freeze_active = False
            self._resume_runtime_after_skill_sequence()
            if pending_convert:
                self._generate_partner_sun_cells()
            return

        sun_x, sun_y = self._pending_partner_burst_origins.pop(0)
        burst_cells = [
            (sun_x, sun_y),
            (sun_x, sun_y - 1),
            (sun_x, sun_y + 1),
            (sun_x - 1, sun_y),
            (sun_x + 1, sun_y),
        ]
        original_board = [list(row) for row in self._board]
        burst_triggered_sun = original_board[sun_y][sun_x] == _SUN_KIND
        next_board, cleared_cells = clear_board_cells(self._board, burst_cells)
        collapsed_board, _collapsed_rows = collapse_empty_rows(next_board)
        self._board = collapsed_board
        self._pending_partner_burst_origins = [
            (x, y)
            for y, row in enumerate(self._board)
            for x, cell in enumerate(row)
            if cell == _SUN_KIND
        ]
        self._mark_settled_board_cache_dirty()
        non_sun_cleared = sum(
            1 for x, y in cleared_cells
            if original_board[y][x] not in (None, _SUN_KIND)
        )
        self._add_score(non_sun_cleared * 10 + 100)
        self._spawn_partner_burst_particles(cleared_cells)
        self._spawn_partner_burst_center_particles(sun_x, sun_y)
        if burst_triggered_sun:
            skill = self._skills.get(_PARTNER_SKILL_SLOT)
            if skill is not None and skill.reduce_cooldown(_HARD_DROP_SKILL_REWARD_SECS):
                self._spawn_skill_cooldown_reward_text(_PARTNER_SKILL_SLOT, "1s")
        self._sfx.play_partner_burst()
        self._start_board_shake(force=7.5)
        self.update()
        self._partner_burst_timer.start(_PARTNER_BURST_STEP_MS)

    def _spawn_partner_burst_particles(self, cells: list[tuple[int, int]]) -> None:
        inner = self._board_inner_screen_rect()
        gold_rgb = (242, 214, 116)
        for x, y in cells:
            rect = self._cell_rect(inner, float(x), float(y))
            gx, gy = self._to_global_point(rect.center().x(), rect.center().y())
            spawn_particle_at_point(gx, gy, self._game_particle_id("preview_rise"), {
                "rgb": gold_rgb,
            })
            spawn_particle_at_point(gx, gy, "burst_line", {
                "rgb": gold_rgb,
            })

    def _spawn_partner_burst_center_particles(self, x: int, y: int) -> None:
        inner = self._board_inner_screen_rect()
        rect = self._cell_rect(inner, float(x), float(y))
        gx, gy = self._to_global_point(rect.center().x(), rect.center().y())
        spawn_particle_at_point(gx, gy, self._game_particle_id("glow_burst"), {
            "rgb": (248, 223, 132),
            "direction": (0.0, -1.0),
        })

    def _spawn_piece(self) -> None:
        new_piece = reset_piece(self._next_piece or self._make_piece())
        self._next_piece = self._make_piece()
        self._set_current_piece(new_piece, animated=False)
        if not self._can_place(new_piece):
            self._game_over = True
            self._timer.stop()
            self._play_game_over_voice()

    def _fall_interval_ms(self) -> int:
        return max(90, 640 - (self._level - 1) * 48)

    def _add_score(self, base_score: int) -> None:
        delta = max(0, int(base_score)) * max(1, int(self._level))
        if delta <= 0:
            return
        previous_score = self._score
        self._score += delta
        self._spawn_stat_delta_text("分数", delta, increased=True)
        if self._score > self._best_score and self._stats.update_best_score(self._score):
            self._best_score = self._score
        if not self._score_1000_triggered and previous_score < 1000 <= self._score:
            self._score_1000_triggered = True
            self._score_1000_sound.play()
        if not self._score_5000_triggered and previous_score < 5000 <= self._score:
            self._score_5000_triggered = True
            self._score_5000_sound.play()
        if not self._score_10000_triggered and previous_score < 10000 <= self._score:
            self._score_10000_triggered = True
            self._score_10000_sound.play()
        if not self._record_broken_triggered and previous_score < _AMS_RECORD_SCORE <= self._score:
            self._record_broken_triggered = True
            self._break_ams_record_sound.play()

    def _can_place(self, piece: Piece) -> bool:
        return can_place(self._board, piece)

    def _set_current_piece(
        self,
        piece: Piece | None,
        animated: bool,
        previous_piece: Piece | None = None,
        duration_ms: int | None = None,
        easing_curve=None,
    ) -> None:
        previous = previous_piece if previous_piece is not None else self._current
        self._current = piece
        if piece is None:
            self._piece_anim.stop()
            self._anim_from_cells = []
            self._anim_to_cells = []
            self._anim_progress = 1.0
            self.update()
            return
        if animated and previous is not None and previous.kind == piece.kind:
            current_cells = self._current_render_cells_for_piece(previous)
            self._start_piece_animation(current_cells, piece.cells(), duration_ms=duration_ms, easing_curve=easing_curve)
        else:
            self._piece_anim.stop()
            cells = [(float(x), float(y)) for x, y in piece.cells()]
            self._anim_from_cells = list(cells)
            self._anim_to_cells = list(cells)
            self._anim_progress = 1.0
            self.update()

    def _start_piece_animation(
        self,
        from_cells: list[tuple[int | float, int | float]],
        to_cells: list[tuple[int, int]],
        duration_ms: int | None = None,
        easing_curve=None,
    ) -> None:
        self._piece_anim.stop()
        self._anim_from_cells = [(float(x), float(y)) for x, y in from_cells]
        self._anim_to_cells = [(float(x), float(y)) for x, y in to_cells]
        self._anim_progress = 0.0
        self._piece_anim.setDuration(self._piece_animation_duration_ms() if duration_ms is None else max(1, int(duration_ms)))
        self._piece_anim.setEasingCurve(QEasingCurve.InOutCubic if easing_curve is None else easing_curve)
        self._piece_anim.start()
        self.update()

    def _piece_animation_duration_ms(self) -> int:
        """当前方块缓动时长：0.2 - 0.015 * 等级 秒，最小 0.05 秒。"""
        level = max(1, int(self._level or 1))
        duration_secs = max(0.05, 0.2 - 0.015 * level)
        return max(1, int(round(duration_secs * 1000)))

    def _on_piece_anim_value_changed(self, value) -> None:
        self._anim_progress = float(value)
        self.update()

    def _on_piece_anim_finished(self) -> None:
        self._anim_progress = 1.0
        if self._current is not None:
            cells = [(float(x), float(y)) for x, y in self._current.cells()]
            self._anim_from_cells = list(cells)
            self._anim_to_cells = list(cells)
        self.update()

    def _current_render_cells(self) -> list[tuple[float, float]]:
        if self._current is None:
            return []
        return self._current_render_cells_for_piece(self._current)

    def _current_render_cells_for_piece(self, piece: Piece) -> list[tuple[float, float]]:
        target_cells = [(float(x), float(y)) for x, y in piece.cells()]
        if (
            self._piece_anim.state() == QVariantAnimation.Stopped
            or len(self._anim_from_cells) != len(self._anim_to_cells)
            or len(self._anim_to_cells) != len(target_cells)
        ):
            return target_cells
        eased = self._anim_progress
        return [
            (
                src_x + (dst_x - src_x) * eased,
                src_y + (dst_y - src_y) * eased,
            )
            for (src_x, src_y), (dst_x, dst_y) in zip(self._anim_from_cells, self._anim_to_cells)
        ]

    def _move_piece(
        self,
        dx: int,
        dy: int,
        animated: bool = True,
        *,
        play_sound: bool = True,
        fall_sound: bool = False,
    ) -> bool:
        if self._current is None or self._game_over:
            return False
        probe = translate_piece(self._current, dx, dy)
        if self._can_place(probe):
            self._set_current_piece(probe, animated=animated, previous_piece=self._current)
            if dx != 0 and dy == 0:
                if play_sound:
                    self._sfx.play_move()
                self._spawn_piece_trail(probe)
            elif dy > 0 and dx == 0:
                if play_sound:
                    if fall_sound:
                        self._sfx.play_fall()
                    else:
                        self._sfx.play_move()
                self._spawn_piece_trail(probe, soft=True)
            self._refresh_ground_lock_delay(reset_if_grounded=(dx != 0 or dy == 0))
            return True
        return False

    def _rotate_piece(self) -> None:
        if self._current is None or self._game_over:
            return
        kicks = (0, -1, 1, -2, 2)
        for kick in kicks:
            probe = rotate_piece(self._current, dx=kick)
            if self._can_place(probe):
                self._set_current_piece(probe, animated=True, previous_piece=self._current)
                self._sfx.play_rotate()
                self._spawn_piece_trail(probe)
                self._refresh_ground_lock_delay(reset_if_grounded=True)
                return

    def _hard_drop(self) -> None:
        if self._current is None or self._game_over:
            return
        if self._pending_lock_timer.isActive():
            return
        probe, distance = hard_drop_target(self._board, self._current)
        if distance:
            duration_ms = self._piece_animation_duration_ms()
            self._set_current_piece(
                probe,
                animated=True,
                previous_piece=self._current,
                duration_ms=duration_ms,
                easing_curve=QEasingCurve.Linear,
            )
            self._spawn_drop_burst(probe)
            self._pending_shake_force = max(3.0, min(11.0, distance * 0.75))
            self._sfx.play_drop_start()
            self._start_pending_lock_timer(duration_ms + 8, "hard_drop_anim")
        else:
            self._start_board_shake(force=3.4)
            self._refresh_ground_lock_delay(reset_if_grounded=True)
        self._add_score(distance * 2)
        if distance > 0:
            self._reward_hard_drop_skill_cooldowns()

    def _tick(self) -> None:
        if self._game_over or self._current is None or self._board_resolution_locked or self._skill_pause_active:
            return
        self._tick_special_fill_particles()
        if self._pending_lock_mode == "hard_drop_anim":
            return
        if not self._move_piece(0, 1, play_sound=True, fall_sound=True):
            self._refresh_ground_lock_delay(reset_if_grounded=False)

    def _finish_hard_drop_lock(self) -> None:
        if self._current is None or self._game_over:
            return
        mode = self._pending_lock_mode
        self._pending_lock_remaining_ms = 0
        if mode == "hard_drop_anim":
            self._pending_lock_mode = None
            self._lock_piece()
            return
        if mode == "ground_delay":
            self._pending_lock_mode = None
            if self._current_piece_grounded():
                self._lock_piece()
            return
        self._pending_lock_mode = None

    def _lock_piece(self) -> None:
        if self._current is None:
            return
        self._clear_pending_lock()
        self._ground_lock_resets_used = 0
        self._piece_anim.stop()
        self._board = place_piece(self._board, self._current)
        self._mark_settled_board_cache_dirty()
        if self._pending_shake_force > 0.0:
            self._start_board_shake(self._pending_shake_force)
            self._sfx.play_drop_impact()
            self._pending_shake_force = 0.0
        self._clear_lines()
        self._spawn_piece()
        self._timer.start(self._fall_interval_ms())
        self.update()

    def _clear_lines(self) -> None:
        cleared_rows = find_full_rows(self._board)
        self._clear_board_rows(cleared_rows, reset_combo_on_empty=True)

    def _clear_board_rows(self, cleared_rows: list[int], *, reset_combo_on_empty: bool = False) -> bool:
        next_board, normalized_rows = clear_rows(self._board, cleared_rows)
        cleared = len(normalized_rows)
        if not cleared:
            if not reset_combo_on_empty:
                return False
            previous_combo = self._combo
            self._combo = 0
            self._combo_over_five_triggered = False
            if previous_combo > 0:
                self._spawn_stat_delta_text("连击", previous_combo, increased=False)
            return False
        original_board = [list(row) for row in self._board]
        sun_cells_cleared = count_kind_cells(self._board, _SUN_KIND, normalized_rows)
        self._emit_line_clear_particles(normalized_rows)
        self._board = next_board
        self._mark_settled_board_cache_dirty()
        self._prepare_settled_fall_animation(original_board, normalized_rows)
        self._lines += cleared
        self._spawn_stat_delta_text("消行", cleared, increased=True)
        self._reward_starlight_skill_cooldown(cleared)
        self._combo += cleared
        self._spawn_stat_delta_text("连击", cleared, increased=True)
        self._add_score({1: 100, 2: 260, 3: 420, 4: 700}.get(cleared, cleared * 200))
        if sun_cells_cleared > 0:
            self._add_score(sun_cells_cleared * 100)
        if self._combo > 1:
            self._add_score(self._combo * 30)
        if self._combo > 5 and not self._combo_over_five_triggered:
            self._combo_over_five_triggered = True
            self._combo_over_five_sound.play()
        previous_level = self._level
        self._level = 1 + self._lines // 14
        if self._level > previous_level:
            self._spawn_stat_delta_text("等级", self._level - previous_level, increased=True)
            self._level_up_sound.play()
        self._sfx.play_clear()
        return True

    def _play_game_over_voice(self) -> None:
        if self._game_over_voice_triggered:
            return
        self._game_over_voice_triggered = True
        self._sfx.play_game_over()
        self._game_over_sound.play()

    def _toggle_bgm_pause(self) -> None:
        try:
            self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {}))
        except Exception:
            pass

    def _maybe_play_idle_chat(self) -> None:
        if self._skill_pause_active:
            return
        if self._rng.random() <= 0.6:
            self._idle_chat_sound.play()

    def resizeEvent(self, event) -> None:
        self._static_scene_cache_dirty = True
        self._update_layout_metrics()
        super().resizeEvent(event)

    def _on_warning_pulse(self) -> None:
        if self._settled_stack_height() > _WARNING_LINE_FLASH_STACK_HEIGHT:
            self.update()

    def _refresh_warning_timer(self) -> None:
        should_pulse = self._settled_stack_height() > _WARNING_LINE_FLASH_STACK_HEIGHT
        if should_pulse and not self._warning_pulse_timer.isActive():
            self._warning_pulse_timer.start()
        elif not should_pulse and self._warning_pulse_timer.isActive():
            self._warning_pulse_timer.stop()

    def _queue_clear_board_rows_after_settled_animation(self, cleared_rows: list[int]) -> None:
        normalized_rows = sorted({int(row) for row in cleared_rows if 0 <= int(row) < _BOARD_H})
        self._pending_settled_clear_rows = normalized_rows or None
        self._board_resolution_locked = bool(self._pending_settled_clear_rows)

    def _load_tips(self) -> list[str]:
        tips_path = self._context.asset_path("text", "tips.txt")
        if not tips_path.exists():
            return []
        try:
            return [line.strip() for line in tips_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            return []

    def _pick_next_tip(self) -> str:
        if not self._tips:
            return "暂无tips"
        unused = [tip for tip in self._tips if tip not in self._tips_used]
        pool = unused if unused else self._tips
        tip = random.choice(pool)
        self._tips_used.add(tip)
        if len(self._tips_used) >= len(self._tips):
            self._tips_used.clear()
            self._tips_used.add(tip)
        return tip

    def _rotate_tip_text(self) -> None:
        self._current_tip = self._pick_next_tip()
        self.update()

    def _queue_skill_trigger(self, slot_index: int) -> bool:
        if self._skill_pause_active or self._queued_skill_slot_index is not None:
            return False
        self._queued_skill_slot_index = int(slot_index)
        self._sfx.play_skill_cast()
        self._play_skill_trigger_voice(slot_index)
        self._play_skill_trigger_effect(slot_index)
        self._start_skill_pause()
        return True

    def _start_skill_pause(self) -> None:
        self._skill_pause_timer.stop()
        self._skill_pause_active = True
        self._timer.stop()
        self._warning_pulse_timer.stop()
        self._idle_chat_timer.stop()
        self._pause_pending_lock_timer()
        self._apply_skill_pause_freeze()
        self._skill_pause_timer.start(_SKILL_PAUSE_MS)
        self.update()

    def _apply_skill_pause_freeze(self) -> None:
        self._piece_anim.pause()
        self._settled_anim.pause()
        self._fill_anim.pause()
        self._board_shake_anim.pause()
        for skill in self._skills.values():
            skill.pause_cooldown()

    def _cancel_skill_pause(self) -> None:
        if not self._skill_pause_active:
            return
        self._skill_pause_timer.stop()
        self._skill_pause_active = False
        self._queued_skill_slot_index = None
        for skill in self._skills.values():
            skill.resume_cooldown()
        self._resume_runtime_after_skill_sequence()
        self.update()

    def _finish_skill_pause(self) -> None:
        if not self._skill_pause_active:
            return
        queued_slot_index = self._queued_skill_slot_index
        self._queued_skill_slot_index = None
        self._skill_pause_timer.stop()
        self._skill_pause_active = False
        skill = self._skills.get(queued_slot_index) if queued_slot_index is not None else None
        triggered = False
        if skill is not None:
            for slot in self._skills.values():
                slot.resume_cooldown()
            if skill.apply(self):
                self._sfx.play_skill_release()
                resolved_cooldown = max(0.0, float(skill.resolved_cooldown_secs()))
                skill.cooldown_until = time.monotonic() + resolved_cooldown
                skill._paused_remaining = 0.0
                skill.advance_cooldown_curve(resolved_cooldown)
                self._apply_skill_chain_cooldown_penalty(skill.slot_index)
                triggered = True
            for slot_index, slot in self._skills.items():
                if slot_index == getattr(skill, "slot_index", None):
                    continue
                if slot._paused_remaining > 0.0:
                    slot.resume_cooldown()
        else:
            for slot in self._skills.values():
                slot.resume_cooldown()
        if not self._skill_sequence_freeze_active:
            self._resume_runtime_after_skill_sequence()
        if triggered:
            self.update()
        else:
            self.update()

    def _resume_runtime_after_skill_sequence(self) -> None:
        if self._piece_anim.state() == QVariantAnimation.Paused:
            self._piece_anim.resume()
        if self._settled_anim.state() == QVariantAnimation.Paused:
            self._settled_anim.resume()
        if self._fill_anim.state() == QVariantAnimation.Paused:
            self._fill_anim.resume()
        if self._board_shake_anim.state() == QVariantAnimation.Paused:
            self._board_shake_anim.resume()
        self._refresh_warning_timer()
        self._idle_chat_timer.start()
        if not self._paused and not self._game_over and not self._awaiting_start:
            self._timer.start(self._fall_interval_ms())
            self._resume_pending_lock_timer()

    def _apply_skill_chain_cooldown_penalty(self, source_slot_index: int) -> None:
        for slot_index, skill in self._skills.items():
            if slot_index == source_slot_index or skill.cooldown_secs <= 0.0:
                continue
            if skill.increase_cooldown(_SKILL_CHAIN_COOLDOWN_PENALTY_SECS):
                self._spawn_skill_cooldown_reward_text(
                    slot_index,
                    "10s",
                    rgb=(255, 210, 210),
                )

    def _mark_settled_board_cache_dirty(self) -> None:
        self._settled_board_cache_dirty = True

    def _clear_block_pixmap_cache(self) -> None:
        self._block_pixmap_cache.clear()
        self._mark_settled_board_cache_dirty()

    def _ensure_settled_board_cache(self) -> QPixmap | None:
        if self._settled_board_cache_dirty or self._settled_board_cache is None:
            self._rebuild_settled_board_cache()
        return self._settled_board_cache

    def _rebuild_settled_board_cache(self) -> None:
        width = max(1, int(round(self._board_inner_rect.width())))
        height = max(1, int(round(self._board_inner_rect.height())))
        board_cache = QPixmap(width, height)
        board_cache.fill(Qt.transparent)
        painter = QPainter(board_cache)
        animated_cells: set[tuple[int, int]] = set()
        if self._settled_anim.state() != QVariantAnimation.Stopped:
            animated_cells = set(self._settled_fall_anim)
        if self._fill_anim.state() != QVariantAnimation.Stopped:
            animated_cells.update(self._fill_anim_cells)
        for y, row in enumerate(self._board):
            for x, cell in enumerate(row):
                if cell is None or (x, y) in animated_cells:
                    continue
                self._draw_block(
                    painter,
                    x * self._block_size,
                    y * self._block_size,
                    cell,
                )
        painter.end()
        self._settled_board_cache = board_cache
        self._settled_board_cache_dirty = False

    def _refresh_fonts(self) -> None:
        self._ui_font = get_ui_font(max(11, int(self._block_size * 0.50)))
        self._ui_font.setBold(True)
        self._title_font = get_ui_font(max(15, int(self._block_size * 0.78)))
        self._title_font.setBold(True)
        self._label_font = get_ui_font(max(11, int(self._block_size * 0.52)))
        self._label_font.setBold(True)
        self._digit_font = get_digit_font(max(14, int(self._block_size * 0.80)))
        self._digit_font.setBold(True)
        self._stat_label_font = get_ui_font(max(16, int(self._block_size * 0.82)))
        self._stat_label_font.setBold(True)
        self._stat_digit_font = get_digit_font(max(22, int(self._block_size * 1.28)))
        self._stat_digit_font.setBold(True)
        self._block_font = get_digit_font(max(10, int(self._block_size * 0.52)))
        self._block_font.setBold(False)

    def _update_layout_metrics(self) -> None:
        previous_block_size = getattr(self, "_block_size", 0)
        total_w = max(1.0, float(self.width()))
        total_h = max(1.0, float(self.height()))
        third_w = total_w / 3.0
        top_y = float(self._PADDING)
        content_y = top_y + self._HEADER_H + self._PADDING
        content_h = total_h - content_y - self._PADDING

        self._left_rect = QRectF(0.0, content_y, third_w, content_h)
        self._center_rect = QRectF(third_w, content_y, third_w, content_h)
        self._right_rect = QRectF(third_w * 2.0, content_y, total_w - third_w * 2.0, content_h)
        section_top = content_y + max(float(scale_px(18, min_abs=1)), float(self._PADDING))

        available_w = self._center_rect.width() - self._PADDING
        available_h = self._center_rect.bottom() - section_top - self._PADDING
        self._block_size = max(12, int(min(available_w / _BOARD_W, available_h / _BOARD_H)))
        if self._block_size != previous_block_size:
            self._clear_block_pixmap_cache()
        self._refresh_fonts()

        board_inner_w = float(_BOARD_W * self._block_size)
        board_inner_h = float(_BOARD_H * self._block_size)
        frame_pad = float(max(4, self._block_size * 0.25))
        board_outer_w = board_inner_w + frame_pad * 2
        board_outer_h = board_inner_h + frame_pad * 2
        board_outer_x = self._center_rect.x() + (self._center_rect.width() - board_outer_w) / 2
        board_outer_y = section_top
        self._board_outer_rect = QRectF(board_outer_x, board_outer_y, board_outer_w, board_outer_h)
        self._board_inner_rect = QRectF(board_outer_x + frame_pad, board_outer_y + frame_pad, board_inner_w, board_inner_h)

        card_gap = float(max(6, self._block_size * 0.28))
        card_h = min(
            float(max(scale_px(64, min_abs=1), self._block_size * 2.7)),
            (self._left_rect.height() - card_gap * 5) / 4.0,
        )
        card_w = self._left_rect.width() - self._PADDING * 2 - scale_px(10, min_abs=1)
        card_x = self._left_rect.x() + self._PADDING + scale_px(4, min_abs=1)
        labels = ["分数", "消行", "等级", "连击"]
        self._stat_cards = []
        y = section_top
        for label in labels:
            self._stat_cards.append((label, QRectF(card_x, y, card_w, card_h)))
            y += card_h + card_gap

        right_x = self._right_rect.x() + self._PADDING
        right_w = self._right_rect.width() - self._PADDING * 2
        self._preview_rect = QRectF(
            right_x,
            section_top,
            right_w,
            max(scale_px(116, min_abs=1), self._block_size * 4.8),
        )
        help_y = self._preview_rect.bottom() + card_gap
        self._help_rect = QRectF(right_x, help_y, right_w, self._right_rect.bottom() - help_y - card_gap)
        mini_gap = float(max(6, self._block_size * 0.24))
        mini_h = float(max(scale_px(42, min_abs=1), self._block_size * 1.7))
        mini_w = (card_w - mini_gap) / 2.0
        mini_y = self._left_rect.bottom() - mini_h - max(float(scale_px(16, min_abs=1)), card_gap)
        self._pause_rect = QRectF(card_x, mini_y, mini_w, mini_h)
        self._exit_rect = QRectF(card_x + mini_w + mini_gap, mini_y, mini_w, mini_h)

        slot_gap = float(max(4, self._block_size * 0.18))
        slot_top_margin = max(float(scale_px(4, min_abs=1)), card_gap * 0.2)
        slot_side = (card_w - slot_gap * 2.0) / 3.0
        slot_y = y + slot_top_margin
        self._skill_slots = []
        for row in range(2):
            for col in range(3):
                index = row * 3 + col + 1
                self._skill_slots.append((
                    index,
                    QRectF(card_x + col * (slot_side + slot_gap), slot_y + row * (slot_side + slot_gap), slot_side, slot_side),
                ))

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_F11:
            if callable(self._fullscreen_callback):
                self._fullscreen_callback()
            event.accept()
            return
        if self._awaiting_start:
            self.start_game()
            event.accept()
            return
        if self._game_over and key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.reset_game()
            event.accept()
            return
        if self._skill_pause_active and key != Qt.Key_R:
            event.accept()
            return
        if self._board_resolution_locked and key not in (Qt.Key_P, Qt.Key_R, Qt.Key_Escape):
            event.accept()
            return
        if self._paused and key not in (Qt.Key_P, Qt.Key_R, Qt.Key_Escape):
            event.accept()
            return
        if self._pending_lock_mode == "hard_drop_anim" and key not in (Qt.Key_R, Qt.Key_Down):
            event.accept()
            return
        if key in (Qt.Key_Left, Qt.Key_A):
            self._move_piece(-1, 0)
        elif key in (Qt.Key_Right, Qt.Key_D):
            self._move_piece(1, 0)
        elif key in (Qt.Key_Down, Qt.Key_S):
            self._soft_drop = True
            self._timer.start(max(30, self._fall_interval_ms() // 8))
            self._move_piece(0, 1)
        elif key in (Qt.Key_Up, Qt.Key_X, Qt.Key_W):
            self._rotate_piece()
        elif key == Qt.Key_Space:
            self._hard_drop()
        elif key in (Qt.Key_P, Qt.Key_Escape):
            self._toggle_pause()
        elif key == Qt.Key_R:
            self.reset_game()
        elif key == Qt.Key_B:
            self._toggle_bgm_pause()
        elif Qt.Key_1 <= key <= Qt.Key_6:
            self._trigger_skill_slot(key - Qt.Key_1 + 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _trigger_skill_slot(self, slot_index: int) -> None:
        skill = self._skills.get(slot_index)
        if skill is not None:
            skill.trigger(self)

    def _skill_slot_index_at(self, pos) -> int | None:
        for index, rect in self._skill_slots:
            if rect.contains(pos):
                return index
        return None

    def _apply_board_gravity(self) -> bool:
        original_board = [list(row) for row in self._board]
        new_board = apply_board_gravity(self._board)
        if new_board == self._board:
            return False
        self._board = new_board
        self._mark_settled_board_cache_dirty()
        self._prepare_gravity_fall_animation(original_board, new_board)
        return True

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Down and self._soft_drop:
            self._soft_drop = False
            self._timer.start(self._fall_interval_ms())
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._awaiting_start:
                self.start_game()
                event.accept()
                return
            hovered_skill = self._skill_slot_index_at(event.pos())
            if hovered_skill is not None:
                self._trigger_skill_slot(hovered_skill)
                event.accept()
                return
            if self._pause_rect.contains(event.pos()):
                self._toggle_pause()
                event.accept()
                return
            if self._exit_rect.contains(event.pos()):
                self._close_runtime()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        hovered_skill = self._skill_slot_index_at(event.pos())
        if hovered_skill != self._hovered_skill_slot_index:
            self._hovered_skill_slot_index = hovered_skill
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hovered_skill_slot_index is not None:
            self._hovered_skill_slot_index = None
            self.update()
        super().leaveEvent(event)

    def _draw_round_panel(self, painter: QPainter, rect: QRectF, fill: QColor) -> None:
        draw_round_panel(self, painter, rect, fill)

    def _draw_block(self, painter: QPainter, x: float, y: float, kind: str, size: float | None = None, active: bool = False) -> None:
        draw_block(self, painter, x, y, kind, size=size, active=active)

    def _draw_preview(self, painter: QPainter, rect: QRectF) -> None:
        draw_preview(self, painter, rect)

    def _draw_warning_line(self, painter: QPainter, inner: QRectF) -> None:
        draw_warning_line(self, painter, inner)

    def _settled_stack_height(self) -> int:
        return settled_stack_height(self._board)

    def _draw_skill_slots(self, painter: QPainter) -> None:
        draw_skill_slots(self, painter)

    def _draw_avatar_skill_slot(
        self,
        painter: QPainter,
        rect: QRectF,
        index: int,
        avatar: QPixmap,
        remaining: float,
    ) -> None:
        draw_avatar_skill_slot(self, painter, rect, index, avatar, remaining)

    def _draw_skill_avatar(self, painter: QPainter, rect: QRectF, avatar: QPixmap, *, disabled: bool = False) -> None:
        draw_skill_avatar(self, painter, rect, avatar, disabled=disabled)

    def _load_skill_avatar(self, filename: str) -> QPixmap:
        key = str(filename or "")
        cached = self._skill_avatar_cache.get(key)
        if cached is not None:
            return cached
        path = self._context.asset_path("avatars", key)
        pixmap = QPixmap(str(path))
        self._skill_avatar_cache[key] = pixmap
        return pixmap

    def _skill_avatar_path(self, filename: str) -> Path:
        return self._context.asset_path("avatars", filename)

    def _skill_showcase_path(self, filename: str) -> Path:
        source = Path(str(filename or ""))
        showcase_name = f"{source.stem}展示.webp"
        return self._context.asset_path("avatars", showcase_name)

    def _game_particle_id(self, local_id: str) -> str:
        return self._context.qualify_particle_id(local_id)

    @staticmethod
    def _skill_voice_name(filename: str) -> str:
        return Path(str(filename or "")).stem

    def _play_skill_trigger_voice(self, slot_index: int) -> None:
        skill = self._skills.get(int(slot_index))
        if skill is None or not getattr(skill, "avatar_filename", None):
            return

        voice_name = self._skill_voice_name(skill.avatar_filename)
        if not voice_name:
            return

        sound = self._skill_release_sounds.get(voice_name)
        if sound is None:
            sound = LahaiSkillReleaseSound(voice_name)
            self._skill_release_sounds[voice_name] = sound
        sound.play()

    def _play_skill_trigger_failure_voice(self, slot_index: int) -> None:
        skill = self._skills.get(int(slot_index))
        if skill is None or not getattr(skill, "avatar_filename", None):
            return

        voice_name = self._skill_voice_name(skill.avatar_filename)
        if not voice_name:
            return

        sound = self._skill_release_failure_sounds.get(voice_name)
        if sound is None:
            sound = LahaiSkillReleaseFailedSound(voice_name)
            self._skill_release_failure_sounds[voice_name] = sound
        sound.play()

    def _play_skill_trigger_effect(self, slot_index: int) -> None:
        skill = self._skills.get(int(slot_index))
        if skill is None or not getattr(skill, "avatar_filename", None):
            return

        display_local = QPoint(int(round(self.width() * 0.50)), int(round(self.height() * 0.46)))
        left_entry_local = QPoint(int(round(self.width() * -0.08)), display_local.y())
        right_exit_local = QPoint(int(round(self.width() * 1.08)), display_local.y())

        display_global = self.mapToGlobal(display_local)
        left_entry_global = self.mapToGlobal(left_entry_local)
        right_exit_global = self.mapToGlobal(right_exit_local)

        showcase_path = self._skill_showcase_path(skill.avatar_filename)
        avatar_path = self._skill_avatar_path(skill.avatar_filename)
        effect_path = showcase_path if showcase_path.exists() else avatar_path
        if effect_path.exists():
            spawn_smooth_image_effect(
                intro_start_pos=(left_entry_global.x(), left_entry_global.y()),
                intro_duration=_SKILL_EFFECT_INTRO_SECS,
                display_pos=(display_global.x(), display_global.y()),
                display_duration=_SKILL_EFFECT_DISPLAY_SECS,
                outro_end_pos=(right_exit_global.x(), right_exit_global.y()),
                outro_duration=_SKILL_EFFECT_OUTRO_SECS,
                resource_path=str(effect_path),
                scale=0.5,
                z=10,
                effect_options={
                    "edge_feather": True,
                    "feather_ratio": 0.16,
                },
            )

        flash_text = _SKILL_FLASH_TEXTS.get(int(slot_index))
        if flash_text is not None:
            text, glow_rgb = flash_text
            flash_center_local = QPoint(display_local.x(), int(round(display_local.y() + self.height() * 0.24)))
            flash_center_global = self.mapToGlobal(flash_center_local)
            spawn_flash_text_effect(
                center_pos=(flash_center_global.x(), flash_center_global.y()),
                text=text,
                fade_in_duration=_STARLIGHT_FLASH_FADE_IN_SECS,
                fade_in_frequency=_STARLIGHT_FLASH_FADE_IN_HZ,
                hold_duration=_STARLIGHT_FLASH_HOLD_SECS,
                fade_out_duration=_STARLIGHT_FLASH_FADE_OUT_SECS,
                fade_out_frequency=_STARLIGHT_FLASH_FADE_OUT_HZ,
                font_type="ui",
                font_size=scale_px(_SKILL_FLASH_FONT_SIZE, min_abs=1),
                color=(255, 255, 255),
                bold=True,
                font_weight=75,
                glow=_SKILL_FLASH_GLOW,
                glow_color=glow_rgb,
                z=14,
            )

    def _board_screen_rect(self) -> QRectF:
        return self._board_outer_rect.translated(self._board_shake_x, self._board_shake_y)

    def _board_inner_screen_rect(self) -> QRectF:
        return self._board_inner_rect.translated(self._board_shake_x, self._board_shake_y)

    def _cell_rect(self, inner: QRectF, cell_x: float, cell_y: float) -> QRectF:
        return QRectF(
            inner.x() + cell_x * self._block_size,
            inner.y() + cell_y * self._block_size,
            self._block_size,
            self._block_size,
        )

    def _to_global_point(self, x: float, y: float) -> tuple[int, int]:
        point = self.mapToGlobal(QPoint(int(round(x)), int(round(y))))
        return point.x(), point.y()

    def _spawn_piece_trail(self, piece: Piece, soft: bool = False) -> None:
        if piece is None:
            return
        inner = self._board_inner_screen_rect()
        particle_id = self._game_particle_id("glow_burst")
        base, _, _ = _THEME[piece.kind]
        direction = (0.0, -1.0) if soft else self._particle_direction_for_piece()
        for x, y in piece.cells():
            if y < 0:
                continue
            rect = self._cell_rect(inner, float(x), float(y))
            cx, cy = self._to_global_point(rect.center().x(), rect.center().y())
            spawn_particle_at_point(cx, cy, particle_id, {
                "rgb": base,
                "direction": direction,
            })
            if soft:
                break

    def _spawn_drop_burst(self, piece: Piece) -> None:
        inner = self._board_inner_screen_rect()
        base, _, _ = _THEME[piece.kind]
        for x, y in piece.cells():
            if y < 0:
                continue
            rect = self._cell_rect(inner, float(x), float(y))
            cx, cy = self._to_global_point(rect.center().x(), rect.bottom())
            spawn_particle_at_point(cx, cy, self._game_particle_id("glow_burst"), {
                "rgb": base,
                "direction": (0.0, -1.0),
            })

    def _spawn_preview_replace_particles(self, kind: str) -> None:
        if self._next_piece is None:
            return
        cells = _SHAPES[kind]
        min_x = min(px for px, _ in cells)
        max_x = max(px for px, _ in cells)
        min_y = min(py for _, py in cells)
        max_y = max(py for _, py in cells)
        header_h = float(max(scale_px(26, min_abs=1), self._block_size * 0.9))
        preview_rect = self._preview_rect.adjusted(
            self._PADDING,
            header_h + self._PADDING * 0.65,
            -self._PADDING,
            -self._PADDING,
        )
        block = min(
            preview_rect.width() / 4.0,
            preview_rect.height() / 4.0,
        )
        origin_x = preview_rect.x() + (preview_rect.width() - (max_x - min_x + 1) * block) / 2
        origin_y = preview_rect.y() + (preview_rect.height() - (max_y - min_y + 1) * block) / 2
        for px, py in cells:
            cx = origin_x + (px - min_x + 0.5) * block
            cy = origin_y + (py - min_y + 0.5) * block
            gx, gy = self._to_global_point(cx, cy)
            spawn_particle_at_point(gx, gy, self._game_particle_id("preview_rise"), {
                "rgb": (255, 134, 88),
            })

    def _tick_special_fill_particles(self) -> None:
        if self._fill_anim.state() != QVariantAnimation.Stopped:
            return
        self._special_fill_particle_accum += 1.0 / 20.0
        if self._special_fill_particle_accum < 0.5:
            return
        self._special_fill_particle_accum = 0.0
        inner = self._board_inner_screen_rect()
        for y, row in enumerate(self._board):
            for x, cell in enumerate(row):
                if cell not in (_SPECIAL_FILL_KIND, _SUN_KIND) or self._rng.random() > 0.2:
                    continue
                rect = self._cell_rect(inner, float(x), float(y))
                gx, gy = self._to_global_point(rect.center().x(), rect.center().y())
                rgb = (196, 54, 120) if cell == _SPECIAL_FILL_KIND else (255, 212, 84)
                spawn_particle_at_point(gx, gy, self._game_particle_id("preview_rise"), {
                    "rgb": rgb,
                })

    def _emit_line_clear_particles(self, cleared_rows: list[int]) -> None:
        inner = self._board_inner_screen_rect()
        for row in cleared_rows:
            segments: list[dict[str, object]] = []
            for col, cell in enumerate(self._board[row]):
                if cell is None:
                    continue
                if cell == _SPECIAL_FILL_KIND:
                    base = (196, 54, 120)
                elif cell == _SUN_KIND:
                    base = (255, 212, 84)
                else:
                    base, _, _ = _THEME[cell]
                cell_rect = self._cell_rect(inner, float(col), float(row))
                gx1, gy1 = self._to_global_point(cell_rect.left(), cell_rect.top())
                gx2, gy2 = self._to_global_point(cell_rect.right(), cell_rect.bottom())
                segments.append({
                    "rect": (gx1, gy1, gx2, gy2),
                    "rgb": base,
                })
            if segments:
                first_rect = segments[0]["rect"]
                if isinstance(first_rect, tuple) and len(first_rect) == 4:
                    spawn_particle_in_rect(
                        int(first_rect[0]),
                        int(first_rect[1]),
                        int(first_rect[2]),
                        int(first_rect[3]),
                        self._game_particle_id("line_flash"),
                        {"segments": segments},
                    )

    def _particle_direction_for_piece(self) -> tuple[float, float]:
        if len(self._anim_from_cells) != len(self._anim_to_cells):
            return (0.0, -1.0)
        dx = 0.0
        dy = 0.0
        for (src_x, src_y), (dst_x, dst_y) in zip(self._anim_from_cells, self._anim_to_cells):
            dx += dst_x - src_x
            dy += dst_y - src_y
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return (0.0, -1.0)
        return (-dx, -dy)

    def _toggle_pause(self) -> None:
        if self._game_over or self._skill_pause_active:
            return
        self._paused = not self._paused
        if self._paused:
            self._timer.stop()
            self._pause_pending_lock_timer()
            for skill in self._skills.values():
                skill.pause_cooldown()
        else:
            self._timer.start(self._fall_interval_ms())
            self._resume_pending_lock_timer()
            for skill in self._skills.values():
                skill.resume_cooldown()
        self.update()

    def _clear_pending_lock(self) -> None:
        self._pending_lock_timer.stop()
        self._pending_lock_mode = None
        self._pending_lock_remaining_ms = 0

    def _start_pending_lock_timer(self, delay_ms: int, mode: str) -> None:
        self._pending_lock_mode = str(mode)
        self._pending_lock_remaining_ms = max(1, int(delay_ms))
        self._pending_lock_timer.start(self._pending_lock_remaining_ms)

    def _pause_pending_lock_timer(self) -> None:
        if not self._pending_lock_timer.isActive():
            return
        self._pending_lock_remaining_ms = max(1, int(self._pending_lock_timer.remainingTime()))
        self._pending_lock_timer.stop()

    def _resume_pending_lock_timer(self) -> None:
        if self._pending_lock_mode is None or self._pending_lock_remaining_ms <= 0:
            return
        self._pending_lock_timer.start(self._pending_lock_remaining_ms)

    def _current_piece_grounded(self) -> bool:
        if self._current is None:
            return False
        return not self._can_place(translate_piece(self._current, 0, 1))

    def _refresh_ground_lock_delay(self, *, reset_if_grounded: bool) -> None:
        if self._current is None or self._game_over:
            return
        if self._pending_lock_mode == "hard_drop_anim":
            return
        grounded = self._current_piece_grounded()
        if not grounded:
            if self._pending_lock_mode == "ground_delay":
                self._clear_pending_lock()
            self._ground_lock_resets_used = 0
            return
        if self._pending_lock_mode != "ground_delay":
            self._ground_lock_resets_used = 0
            self._start_pending_lock_timer(_GROUND_LOCK_DELAY_MS, "ground_delay")
            return
        if not reset_if_grounded:
            return
        self._ground_lock_resets_used += 1
        if self._ground_lock_resets_used > _GROUND_LOCK_DELAY_RESET_MOVES:
            self._clear_pending_lock()
            self._lock_piece()
            return
        next_delay_ms = max(0, _GROUND_LOCK_DELAY_MS - self._ground_lock_resets_used * _GROUND_LOCK_DELAY_STEP_MS)
        if next_delay_ms <= 0:
            self._clear_pending_lock()
            self._lock_piece()
            return
        self._start_pending_lock_timer(next_delay_ms, "ground_delay")

    def _reward_hard_drop_skill_cooldowns(self) -> None:
        changed = False
        for slot_index, skill in self._skills.items():
            if skill.cooldown_secs <= 0.0:
                continue
            if skill.reduce_cooldown(_HARD_DROP_SKILL_REWARD_SECS):
                changed = True
                self._spawn_skill_cooldown_reward_text(slot_index, "1s")
        if changed:
            self.update()

    def _reward_starlight_skill_cooldown(self, cleared_lines: int) -> None:
        reduction_secs = max(0, int(cleared_lines))
        if reduction_secs <= 0:
            return
        skill = self._skills.get(_STARLIGHT_SKILL_SLOT)
        if skill is None:
            return
        if skill.reduce_cooldown(float(reduction_secs)):
            self._spawn_skill_cooldown_reward_text(_STARLIGHT_SKILL_SLOT, f"{reduction_secs}s")
            self.update()

    def _spawn_skill_cooldown_reward_text(
        self,
        slot_index: int,
        text: str,
        *,
        rgb: tuple[int, int, int] = (200, 250, 200),
    ) -> None:
        target_rect = None
        for index, rect in self._skill_slots:
            if index == slot_index:
                target_rect = rect
                break
        if target_rect is None:
            return
        start_x = target_rect.center().x()
        start_y = target_rect.y() + target_rect.height() * 0.18
        target_x = start_x
        target_y = start_y - target_rect.height() * 0.12
        gx1, gy1 = self._to_global_point(start_x, start_y)
        gx2, gy2 = self._to_global_point(target_x, target_y)
        reward_color = QColor(*rgb)
        hue, saturation, value, _alpha = reward_color.getHsv()
        if hue >= 0:
            reward_color.setHsv(hue, min(255, int(round(saturation * 1.1))), value)
        self._queue_lahai_floating_text_particle(gx1, gy1, {
            "rgb": (reward_color.red(), reward_color.green(), reward_color.blue()),
            "text": str(text),
            "font_type": "digit",
            "size": 28,
            "font_bold": False,
            "bloom": 5.0,
            "drift_amplitude": max(2.0, target_rect.width() * 0.045),
            "drift_speed": 7.5,
            "target_x": gx2,
            "target_y": gy2,
        })

    def _spawn_stat_delta_text(self, label: str, value: int, *, increased: bool) -> None:
        amount = max(0, int(value))
        if amount <= 0:
            return
        target_rect = self._stat_card_rect(label)
        if target_rect is None:
            return

        start_x = target_rect.right() - target_rect.width() * 0.26
        start_y = target_rect.center().y() - target_rect.height() * 0.02
        target_x = start_x
        target_y = start_y - target_rect.height() * 0.30
        gx1, gy1 = self._to_global_point(start_x, start_y)
        gx2, gy2 = self._to_global_point(target_x, target_y)

        rgb = _STAT_GAIN_COLORS.get(label, (255, 255, 255)) if increased else _STAT_LOSS_COLOR
        bloom = max(4.0, target_rect.height() * 0.065)
        font_size = max(18, int(round(target_rect.height() * 0.52)))
        self._queue_lahai_floating_text_particle(gx1, gy1, {
            "rgb": rgb,
            "text": str(amount),
            "font_type": "lahai",
            "size": font_size,
            "font_bold": False,
            "bloom": bloom,
            "drift_amplitude": max(1.5, target_rect.width() * 0.018),
            "drift_speed": 6.8,
            "target_x": gx2,
            "target_y": gy2,
        })

    @staticmethod
    def _parse_aggregatable_floating_text(text: str) -> tuple[str, int, str] | None:
        match = re.fullmatch(r"([^\d-]*)(\d+)([^\d]*)", str(text or ""))
        if match is None:
            return None
        prefix, number_text, suffix = match.groups()
        return prefix, int(number_text), suffix

    def _queue_lahai_floating_text_particle(self, x: int, y: int, options: dict) -> None:
        payload = dict(options or {})
        parsed = self._parse_aggregatable_floating_text(str(payload.get("text", "")))
        aggregate_tag = None
        if parsed is not None:
            prefix, amount, suffix = parsed
            aggregate_tag = (prefix, suffix)
            payload["_aggregate_amount"] = amount
            payload["_aggregate_prefix"] = prefix
            payload["_aggregate_suffix"] = suffix

        key = (
            int(x),
            int(y),
            int(round(float(payload.get("target_x", x)))),
            int(round(float(payload.get("target_y", y)))),
            tuple(int(v) for v in payload.get("rgb", (255, 255, 255))),
            str(payload.get("font_type", "digit")),
            int(payload.get("size", 18)),
            bool(payload.get("font_bold", payload.get("bold", False))),
            round(float(payload.get("bloom", 0.0)), 3),
            round(float(payload.get("drift_amplitude", 0.0)), 3),
            round(float(payload.get("drift_speed", 0.0)), 3),
            aggregate_tag,
        )

        existing = self._pending_floating_text_particles.get(key)
        if existing is None:
            self._pending_floating_text_particles[key] = {
                "x": int(x),
                "y": int(y),
                "options": payload,
            }
        elif aggregate_tag is not None:
            existing_options = existing["options"]
            total = int(existing_options.get("_aggregate_amount", 0)) + int(payload["_aggregate_amount"])
            existing_options["_aggregate_amount"] = total
            existing_options["text"] = f"{existing_options.get('_aggregate_prefix', '')}{total}{existing_options.get('_aggregate_suffix', '')}"
        else:
            self._pending_floating_text_particles[key] = {
                "x": int(x),
                "y": int(y),
                "options": payload,
            }

        if not self._floating_text_flush_scheduled:
            self._floating_text_flush_scheduled = True
            QTimer.singleShot(0, self._flush_lahai_floating_text_particles)

    def _flush_lahai_floating_text_particles(self) -> None:
        self._floating_text_flush_scheduled = False
        if not self._pending_floating_text_particles:
            return

        pending = list(self._pending_floating_text_particles.values())
        self._pending_floating_text_particles.clear()
        for item in pending:
            options = dict(item["options"])
            options.pop("_aggregate_amount", None)
            options.pop("_aggregate_prefix", None)
            options.pop("_aggregate_suffix", None)
            spawn_particle_at_point(int(item["x"]), int(item["y"]), "floating_text", options)

    def _stat_card_rect(self, label: str) -> QRectF | None:
        for card_label, rect in self._stat_cards:
            if card_label == label:
                return QRectF(rect)
        return None

    def _close_runtime(self) -> None:
        if callable(self._close_callback):
            self._close_callback()
            return
        window = self.window()
        if window is not None:
            window.hide()

    def _prepare_settled_fall_animation(self, original_board: list[list[str | None]], cleared_rows: list[int]) -> None:
        self._settled_anim.stop()
        self._settled_fall_anim = {}
        if not cleared_rows:
            return
        cleared_set = set(cleared_rows)
        for y, row in enumerate(original_board):
            if y in cleared_set:
                continue
            shift = sum(1 for cleared_y in cleared_rows if cleared_y > y)
            if shift <= 0:
                continue
            new_y = y + shift
            if new_y >= _BOARD_H:
                continue
            for x, cell in enumerate(row):
                if cell is None:
                    continue
                self._settled_fall_anim[(x, new_y)] = (cell, float(x), float(y), float(new_y))
        if self._settled_fall_anim:
            self._mark_settled_board_cache_dirty()
            self._settled_anim.start()

    def _prepare_gravity_fall_animation(
        self,
        original_board: list[list[str | None]],
        new_board: list[list[str | None]],
    ) -> None:
        self._settled_anim.stop()
        self._settled_fall_anim = {}
        for col in range(_BOARD_W):
            original_cells = [(row, original_board[row][col]) for row in range(_BOARD_H) if original_board[row][col] is not None]
            new_cells = [(row, new_board[row][col]) for row in range(_BOARD_H) if new_board[row][col] is not None]
            for (from_y, cell), (to_y, _) in zip(original_cells, new_cells):
                if from_y == to_y:
                    continue
                self._settled_fall_anim[(col, to_y)] = (cell, float(col), float(from_y), float(to_y))
        if self._settled_fall_anim:
            self._mark_settled_board_cache_dirty()
            self._settled_anim.start()

    def _on_settled_anim_value_changed(self, value) -> None:
        self.update()

    def _on_settled_anim_finished(self) -> None:
        self._settled_fall_anim = {}
        self._mark_settled_board_cache_dirty()
        pending_rows = self._pending_settled_clear_rows
        self._pending_settled_clear_rows = None
        self._board_resolution_locked = False
        if pending_rows:
            self._clear_board_rows(pending_rows)
        self.update()

    def _on_fill_anim_value_changed(self, value) -> None:
        self._fill_anim_progress = float(value)
        self.update()

    def _on_fill_anim_finished(self) -> None:
        self._fill_anim_progress = 1.0
        pending_rows = self._pending_fill_clear_rows
        self._pending_fill_clear_rows = None
        self._fill_anim_cells = []
        self._mark_settled_board_cache_dirty()
        self._board_resolution_locked = False
        if pending_rows:
            self._clear_board_rows(pending_rows)
        self.update()

    def _start_board_shake(self, force: float) -> None:
        self._board_shake_force = float(force)
        self._board_shake_anim.stop()
        self._board_shake_anim.start()

    def _on_board_shake_value_changed(self, value) -> None:
        progress = float(value)
        strength = (1.0 - progress) * getattr(self, "_board_shake_force", 0.0)
        phase = progress * 5.0 * 3.141592653589793
        self._board_shake_x = strength * 0.75 * (-1.0 if int(progress * 10) % 2 else 1.0)
        self._board_shake_y = abs(strength * 0.55 * (1.0 - progress)) * (1.0 if phase >= 0 else -1.0)
        self.update()

    def _on_board_shake_finished(self) -> None:
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self.update()

    def paintEvent(self, event) -> None:
        self._refresh_warning_timer()
        painter = QPainter(self)
        self._render_core.render(painter, self.rect())
        painter.end()

    def _paint_game_layer(self, painter: QPainter, _target_rect) -> None:
        paint_widget(self, painter=painter)

    def _ROW_H(self) -> float:
        return float(scale_px(24, min_abs=1))
