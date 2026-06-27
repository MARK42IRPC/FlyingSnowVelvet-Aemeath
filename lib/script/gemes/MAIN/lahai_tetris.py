"""拉海洛方块。

独立小游戏模块，负责：
- 俄罗斯方块核心逻辑
- 主题化绘制（圆角彩虹字母砖块）
- 本地键盘输入与帧循环
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from PyQt5.QtCore import QEasingCurve, QPoint, Qt, QRectF, QTimer, QVariantAnimation
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt5.QtWidgets import QWidget

from config.font_config import get_digit_font, get_ui_font
from config.game_user_stats import get_game_user_stats
from config.scale import scale_px
from lib.core.particle_utils import spawn_particle_at_point, spawn_particle_in_rect
from lib.core.voice.ams_lahai_break_ams_record import AmsLahaiBreakAmsRecordSound
from lib.core.voice.ams_lahai_combo_over_five import AmsLahaiComboOverFiveSound
from lib.core.voice.ams_lahai_game_over import AmsLahaiGameOverSound
from lib.core.voice.ams_lahai_level_up import AmsLahaiLevelUpSound
from lib.core.voice.ams_lahai_score_10000 import AmsLahaiScore10000Sound
from lib.core.voice.ams_lahai_score_1000 import AmsLahaiScore1000Sound
from lib.core.voice.ams_lahai_score_5000 import AmsLahaiScore5000Sound
from .game_sfx import GameSfx

_BOARD_W = 10
_BOARD_H = 20
_PREVIEW_GRID = 4
_AMS_RECORD_SCORE = 915800

_SHAPES: dict[str, list[tuple[int, int]]] = {
    "A": [(-1, 0), (0, 0), (1, 0), (2, 0)],       # I
    "B": [(0, 0), (1, 0), (0, 1), (1, 1)],        # O
    "C": [(-1, 0), (0, 0), (1, 0), (0, 1)],       # T
    "D": [(0, 0), (1, 0), (-1, 1), (0, 1)],       # S
    "E": [(-1, 0), (0, 0), (0, 1), (1, 1)],       # Z
    "F": [(-1, 0), (-1, 1), (0, 0), (1, 0)],      # J
    "G": [(-1, 0), (0, 0), (1, 0), (1, 1)],       # L
}

_THEME: dict[str, tuple[QColor, QColor, QColor]] = {
    "A": (QColor(255, 120, 126), QColor(112, 33, 58), QColor(255, 221, 224)),
    "B": (QColor(255, 174, 90), QColor(126, 68, 18), QColor(255, 233, 186)),
    "C": (QColor(255, 221, 96), QColor(120, 100, 20), QColor(255, 243, 181)),
    "D": (QColor(153, 229, 118), QColor(49, 104, 45), QColor(216, 255, 206)),
    "E": (QColor(100, 216, 196), QColor(21, 100, 91), QColor(204, 253, 245)),
    "F": (QColor(108, 164, 255), QColor(28, 63, 126), QColor(211, 227, 255)),
    "G": (QColor(191, 127, 255), QColor(79, 42, 126), QColor(239, 220, 255)),
}


@dataclass
class Piece:
    kind: str
    rotation: int = 0
    x: int = _BOARD_W // 2
    y: int = 1

    def cells(self) -> list[tuple[int, int]]:
        points = list(_SHAPES[self.kind])
        for _ in range(self.rotation % 4):
            points = [(-py, px) for px, py in points]
        return [(self.x + px, self.y + py) for px, py in points]


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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(False)
        self.setMinimumSize(scale_px(720, min_abs=1), scale_px(520, min_abs=1))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._board: list[list[str | None]] = []
        self._current: Piece | None = None
        self._next_piece: Piece | None = None
        self._rng = random.Random()
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
        self._pause_rect = QRectF()
        self._exit_rect = QRectF()
        self._anim_from_cells: list[tuple[float, float]] = []
        self._anim_to_cells: list[tuple[float, float]] = []
        self._anim_progress = 1.0
        self._settled_fall_anim: dict[tuple[int, int], tuple[str, float, float, float]] = {}
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self._pending_shake_force = 0.0
        self._paused = False
        self._awaiting_start = True
        self._close_callback = None
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
        self._best_score = get_game_user_stats().get_best_score()
        self._refresh_fonts()
        self._update_layout_metrics()

        self._piece_anim = QVariantAnimation(self)
        self._piece_anim.setDuration(200)
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

        self._pending_lock_timer = QTimer(self)
        self._pending_lock_timer.setSingleShot(True)
        self._pending_lock_timer.timeout.connect(self._finish_hard_drop_lock)

        self._sfx = GameSfx()
        self._score_1000_sound = AmsLahaiScore1000Sound()
        self._score_5000_sound = AmsLahaiScore5000Sound()
        self._score_10000_sound = AmsLahaiScore10000Sound()
        self._combo_over_five_sound = AmsLahaiComboOverFiveSound()
        self._level_up_sound = AmsLahaiLevelUpSound()
        self._game_over_sound = AmsLahaiGameOverSound()
        self._break_ams_record_sound = AmsLahaiBreakAmsRecordSound()

        self.reset_game(start_running=False)

    def reset_game(self, start_running: bool = True) -> None:
        self._board = [[None for _ in range(_BOARD_W)] for _ in range(_BOARD_H)]
        self._score = 0
        self._lines = 0
        self._level = 1
        self._combo = 0
        self._soft_drop = False
        self._game_over = False
        self._pending_lock_timer.stop()
        self._piece_anim.stop()
        self._settled_anim.stop()
        self._board_shake_anim.stop()
        self._settled_fall_anim = {}
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self._pending_shake_force = 0.0
        self._paused = False
        self._awaiting_start = not start_running
        self._score_1000_triggered = False
        self._score_5000_triggered = False
        self._score_10000_triggered = False
        self._combo_over_five_triggered = False
        self._record_broken_triggered = False
        self._game_over_voice_triggered = False
        self._next_piece = self._make_piece()
        self._spawn_piece()
        if start_running:
            self._timer.start(self._fall_interval_ms())
        else:
            self._timer.stop()
        self.update()

    def deactivate(self) -> None:
        self._timer.stop()
        self._pending_lock_timer.stop()
        self._piece_anim.stop()
        self._settled_anim.stop()
        self._board_shake_anim.stop()
        self._settled_fall_anim = {}
        self._board_shake_x = 0.0
        self._board_shake_y = 0.0
        self._pending_shake_force = 0.0
        self._paused = False
        self._awaiting_start = True
        self._game_over_voice_triggered = False
        self.update()

    def set_close_callback(self, callback) -> None:
        self._close_callback = callback

    def start_game(self) -> None:
        if not self._awaiting_start or self._game_over:
            return
        self._awaiting_start = False
        self._paused = False
        self._timer.start(self._fall_interval_ms())
        self.update()

    def _make_piece(self) -> Piece:
        return Piece(self._rng.choice(list(_SHAPES.keys())))

    def _spawn_piece(self) -> None:
        new_piece = self._next_piece or self._make_piece()
        new_piece.x = _BOARD_W // 2
        new_piece.y = 1
        new_piece.rotation = 0
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
        if self._score > self._best_score and get_game_user_stats().update_best_score(self._score):
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
        for x, y in piece.cells():
            if x < 0 or x >= _BOARD_W or y >= _BOARD_H:
                return False
            if y >= 0 and self._board[y][x] is not None:
                return False
        return True

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
        self._piece_anim.setDuration(200 if duration_ms is None else max(1, int(duration_ms)))
        self._piece_anim.setEasingCurve(QEasingCurve.InOutCubic if easing_curve is None else easing_curve)
        self._piece_anim.start()
        self.update()

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
            self._piece_anim.state() != QVariantAnimation.Running
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
        probe = Piece(self._current.kind, self._current.rotation, self._current.x + dx, self._current.y + dy)
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
            return True
        return False

    def _rotate_piece(self) -> None:
        if self._current is None or self._game_over:
            return
        kicks = (0, -1, 1, -2, 2)
        for kick in kicks:
            probe = Piece(self._current.kind, self._current.rotation + 1, self._current.x + kick, self._current.y)
            if self._can_place(probe):
                self._set_current_piece(probe, animated=True, previous_piece=self._current)
                self._sfx.play_rotate()
                self._spawn_piece_trail(probe)
                return

    def _hard_drop(self) -> None:
        if self._current is None or self._game_over:
            return
        if self._pending_lock_timer.isActive():
            return
        distance = 0
        probe = self._current
        while True:
            next_probe = Piece(probe.kind, probe.rotation, probe.x, probe.y + 1)
            if not self._can_place(next_probe):
                break
            probe = next_probe
            distance += 1
        if distance:
            duration_ms = max(70, min(180, distance * 16))
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
            self._pending_lock_timer.start(duration_ms + 8)
        else:
            self._start_board_shake(force=3.4)
            self._sfx.play_lock()
            self._lock_piece()
        self._add_score(distance * 2)

    def _tick(self) -> None:
        if self._game_over or self._current is None or self._pending_lock_timer.isActive():
            return
        if not self._move_piece(0, 1, play_sound=True, fall_sound=True):
            self._lock_piece()

    def _finish_hard_drop_lock(self) -> None:
        if self._current is None or self._game_over:
            return
        self._lock_piece()

    def _lock_piece(self) -> None:
        if self._current is None:
            return
        self._pending_lock_timer.stop()
        self._piece_anim.stop()
        for x, y in self._current.cells():
            if 0 <= y < _BOARD_H and 0 <= x < _BOARD_W:
                self._board[y][x] = self._current.kind
        if self._pending_shake_force > 0.0:
            self._start_board_shake(self._pending_shake_force)
            self._sfx.play_drop_impact()
            self._pending_shake_force = 0.0
        self._clear_lines()
        self._spawn_piece()
        self._timer.start(self._fall_interval_ms())
        self.update()

    def _clear_lines(self) -> None:
        original_board = [list(row) for row in self._board]
        cleared_rows = [idx for idx, row in enumerate(self._board) if all(cell is not None for cell in row)]
        kept_rows = [row for row in self._board if any(cell is None for cell in row)]
        cleared = len(cleared_rows)
        if not cleared:
            self._combo = 0
            self._combo_over_five_triggered = False
            return
        self._emit_line_clear_particles(cleared_rows)
        for _ in range(cleared):
            kept_rows.insert(0, [None for _ in range(_BOARD_W)])
        self._board = kept_rows
        self._prepare_settled_fall_animation(original_board, cleared_rows)
        self._lines += cleared
        self._combo += cleared
        self._add_score({1: 100, 2: 260, 3: 420, 4: 700}.get(cleared, cleared * 200))
        if self._combo > 1:
            self._add_score(self._combo * 30)
        if self._combo > 5 and not self._combo_over_five_triggered:
            self._combo_over_five_triggered = True
            self._combo_over_five_sound.play()
        previous_level = self._level
        self._level = 1 + self._lines // 8
        if self._level > previous_level:
            self._level_up_sound.play()
        self._sfx.play_clear()

    def _play_game_over_voice(self) -> None:
        if self._game_over_voice_triggered:
            return
        self._game_over_voice_triggered = True
        self._game_over_sound.play()

    def resizeEvent(self, event) -> None:
        self._update_layout_metrics()
        super().resizeEvent(event)

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

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if self._awaiting_start:
            self.start_game()
            event.accept()
            return
        if self._game_over and key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.reset_game()
            event.accept()
            return
        if self._paused and key not in (Qt.Key_P, Qt.Key_R, Qt.Key_Escape):
            event.accept()
            return
        if self._pending_lock_timer.isActive() and key not in (Qt.Key_R, Qt.Key_Down):
            event.accept()
            return
        if key == Qt.Key_Left:
            self._move_piece(-1, 0)
        elif key == Qt.Key_Right:
            self._move_piece(1, 0)
        elif key == Qt.Key_Down:
            self._soft_drop = True
            self._timer.start(max(30, self._fall_interval_ms() // 8))
            self._move_piece(0, 1)
        elif key in (Qt.Key_Up, Qt.Key_X, Qt.Key_W):
            self._rotate_piece()
        elif key == Qt.Key_Space:
            self._hard_drop()
        elif key == Qt.Key_P:
            self._toggle_pause()
        elif key == Qt.Key_Escape:
            self._close_runtime()
        elif key == Qt.Key_R:
            self.reset_game()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

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
            if self._pause_rect.contains(event.pos()):
                self._toggle_pause()
                event.accept()
                return
            if self._exit_rect.contains(event.pos()):
                self._close_runtime()
                event.accept()
                return
        super().mousePressEvent(event)

    def _draw_round_panel(self, painter: QPainter, rect: QRectF, fill: QColor) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, self._PANEL_RADIUS, self._PANEL_RADIUS)
        painter.fillPath(path, fill)
        painter.setPen(QPen(self._C_BORDER_DEEP, max(1, scale_px(3, min_abs=1))))
        painter.drawPath(path)
        painter.setPen(QPen(self._C_PANEL_EDGE, max(1, scale_px(2, min_abs=1))))
        painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), self._PANEL_RADIUS - 2, self._PANEL_RADIUS - 2)
        painter.setPen(QPen(self._C_PANEL_LINE, max(1, scale_px(1, min_abs=1))))
        painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), self._PANEL_RADIUS - 4, self._PANEL_RADIUS - 4)

    def _draw_block(self, painter: QPainter, x: float, y: float, kind: str, size: float | None = None, active: bool = False) -> None:
        block = float(self._block_size if size is None else size)
        outer = QRectF(x, y, block, block)
        base, inner, letter = _THEME[kind]

        if active:
            painter.setPen(Qt.NoPen)
            glow_rect = outer.adjusted(-block * 0.42, -block * 0.42, block * 0.42, block * 0.42)
            gradient = QRadialGradient(glow_rect.center(), glow_rect.width() * 0.48)
            glow_core = QColor(base)
            glow_mid = QColor(base)
            glow_edge = QColor(base)
            glow_core.setAlpha(138)
            glow_mid.setAlpha(72)
            glow_edge.setAlpha(0)
            gradient.setColorAt(0.0, glow_core)
            gradient.setColorAt(0.58, glow_mid)
            gradient.setColorAt(1.0, glow_edge)
            painter.setBrush(gradient)
            painter.drawEllipse(glow_rect)

        # 外层亮边
        path = QPainterPath()
        radius = max(4.0, block * 0.22)
        path.addRoundedRect(outer, radius, radius)
        painter.fillPath(path, base)
        painter.setPen(QPen(letter.lighter(115), max(1, int(block * 0.07))))
        painter.drawPath(path)

        # 内层深色
        inner_rect = outer.adjusted(block * 0.12, block * 0.12, -block * 0.12, -block * 0.12)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner_rect, radius * 0.72, radius * 0.72)
        painter.fillPath(inner_path, inner)

        # 字母辉光
        glow_rect = inner_rect.adjusted(block * 0.02, block * 0.01, 0, 0)
        glow_color = QColor(letter)
        glow_color.setAlpha(105)
        for offset in ((0.0, 0.0), (0.8, 0.0), (-0.8, 0.0), (0.0, 0.8), (0.0, -0.8)):
            painter.setPen(glow_color)
            painter.setFont(self._block_font)
            painter.drawText(
                glow_rect.adjusted(offset[0], offset[1], offset[0], offset[1]),
                Qt.AlignCenter,
                kind,
            )

        painter.setPen(letter)
        painter.setFont(self._block_font)
        painter.drawText(inner_rect, Qt.AlignCenter, kind)

    def _draw_preview(self, painter: QPainter, rect: QRectF) -> None:
        self._draw_round_panel(painter, rect, self._C_PANEL_ALT)
        header_h = float(max(scale_px(26, min_abs=1), self._block_size * 0.9))
        title_rect = QRectF(
            rect.x() + self._PADDING,
            rect.y() + self._PADDING * 0.45,
            rect.width() - self._PADDING * 2,
            header_h,
        )
        painter.setPen(self._C_NEON)
        painter.setFont(self._digit_font)
        painter.drawText(title_rect, Qt.AlignHCenter | Qt.AlignVCenter, "NEXT")
        if self._next_piece is None:
            return

        cells = _SHAPES[self._next_piece.kind]
        min_x = min(px for px, _ in cells)
        max_x = max(px for px, _ in cells)
        min_y = min(py for _, py in cells)
        max_y = max(py for _, py in cells)
        preview_rect = rect.adjusted(
            self._PADDING,
            header_h + self._PADDING * 0.65,
            -self._PADDING,
            -self._PADDING,
        )
        block = min(
            preview_rect.width() / _PREVIEW_GRID,
            preview_rect.height() / _PREVIEW_GRID,
        )
        origin_x = preview_rect.x() + (preview_rect.width() - (max_x - min_x + 1) * block) / 2
        origin_y = preview_rect.y() + (preview_rect.height() - (max_y - min_y + 1) * block) / 2
        for px, py in cells:
            self._draw_block(
                painter,
                origin_x + (px - min_x) * block,
                origin_y + (py - min_y) * block,
                self._next_piece.kind,
                size=block,
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
        particle_id = "lahai_glow_burst"
        base, _, _ = _THEME[piece.kind]
        direction = (0.0, -1.0) if soft else self._particle_direction_for_piece()
        for x, y in piece.cells():
            if y < 0:
                continue
            rect = self._cell_rect(inner, float(x), float(y))
            cx, cy = self._to_global_point(rect.center().x(), rect.center().y())
            spawn_particle_at_point(cx, cy, particle_id, {
                "rgb": (base.red(), base.green(), base.blue()),
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
            spawn_particle_at_point(cx, cy, "lahai_glow_burst", {
                "rgb": (base.red(), base.green(), base.blue()),
                "direction": (0.0, -1.0),
            })

    def _emit_line_clear_particles(self, cleared_rows: list[int]) -> None:
        inner = self._board_inner_screen_rect()
        for row in cleared_rows:
            for col, cell in enumerate(self._board[row]):
                if cell is None:
                    continue
                base, _, _ = _THEME[cell]
                cell_rect = self._cell_rect(inner, float(col), float(row))
                gx1, gy1 = self._to_global_point(cell_rect.left(), cell_rect.top())
                gx2, gy2 = self._to_global_point(cell_rect.right(), cell_rect.bottom())
                spawn_particle_in_rect(gx1, gy1, gx2, gy2, "lahai_line_flash", {
                    "rgb": (base.red(), base.green(), base.blue()),
                })

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
        if self._game_over:
            return
        self._paused = not self._paused
        if self._paused:
            self._timer.stop()
            self._pending_lock_timer.stop()
        else:
            self._timer.start(self._fall_interval_ms())
        self.update()

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
            self._settled_anim.start()

    def _on_settled_anim_value_changed(self, value) -> None:
        self.update()

    def _on_settled_anim_finished(self) -> None:
        self._settled_fall_anim = {}
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self._C_BG)

        bg_step = scale_px(40, min_abs=1)
        painter.setPen(QPen(self._C_BG_GRID, max(1, scale_px(1, min_abs=1))))
        for gx in range(0, self.width() + bg_step, bg_step):
            painter.drawLine(gx, 0, gx, self.height())
        for gy in range(0, self.height() + bg_step, bg_step):
            painter.drawLine(0, gy, self.width(), gy)

        # 背景底部氛围光
        glow_height = float(scale_px(100, min_abs=1))
        glow_top = max(0.0, float(self.height()) - glow_height)
        bg_glow = QLinearGradient(0.0, float(self.height()), 0.0, glow_top)
        glow_base = QColor(self._C_BG_GLOW)
        glow_clear = QColor(self._C_BG_GLOW)
        glow_clear.setAlpha(0)
        bg_glow.setColorAt(0.0, glow_base)
        bg_glow.setColorAt(1.0, glow_clear)
        painter.setPen(Qt.NoPen)
        painter.fillRect(
            QRectF(0.0, glow_top, float(self.width()), float(self.height()) - glow_top),
            bg_glow,
        )

        title_rect = QRectF(float(self._PADDING), float(self._PADDING), float(self.width() - self._PADDING * 2), float(self._HEADER_H))
        painter.setPen(self._C_TEXT)
        painter.setFont(self._title_font)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, "拉海洛方块")
        painter.setPen(self._C_NEON)
        painter.setFont(self._digit_font)
        painter.drawText(title_rect, Qt.AlignRight | Qt.AlignVCenter, "LAHAI ROI BLOCKS")

        board_rect = self._board_screen_rect()
        inner = self._board_inner_screen_rect()
        self._draw_round_panel(painter, board_rect, self._C_BOARD)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner, self._PANEL_RADIUS * 0.7, self._PANEL_RADIUS * 0.7)
        painter.fillPath(inner_path, self._C_BOARD_INNER)

        # 棋盘内霓虹边
        painter.setPen(QPen(QColor(212, 221, 255, 155), max(1, scale_px(1, min_abs=1))))
        painter.drawRoundedRect(inner, self._PANEL_RADIUS * 0.7, self._PANEL_RADIUS * 0.7)

        painter.save()
        painter.setClipRect(inner)
        painter.setPen(QPen(self._C_GRID, 1))
        inner_x = int(round(inner.x()))
        inner_y = int(round(inner.y()))
        inner_w = int(round(inner.width()))
        inner_h = int(round(inner.height()))
        for col in range(_BOARD_W + 1):
            gx = inner_x + col * self._block_size
            painter.drawLine(gx, inner_y, gx, inner_y + inner_h)
        for row in range(_BOARD_H + 1):
            gy = inner_y + row * self._block_size
            painter.drawLine(inner_x, gy, inner_x + inner_w, gy)

        for row in range(_BOARD_H):
            sy = inner_y + row * self._block_size + self._block_size * 0.5
            painter.fillRect(QRectF(inner.x(), sy, inner.width(), 1), self._C_SCANLINE)
        painter.restore()

        for y, row in enumerate(self._board):
            for x, cell in enumerate(row):
                if cell is None:
                    continue
                if self._settled_anim.state() == QVariantAnimation.Running and (x, y) in self._settled_fall_anim:
                    continue
                self._draw_block(painter, inner.x() + x * self._block_size, inner.y() + y * self._block_size, cell)

        if self._settled_anim.state() == QVariantAnimation.Running:
            fall_progress = float(self._settled_anim.currentValue() or 0.0)
            for final_pos, (cell, fx, from_y, to_y) in self._settled_fall_anim.items():
                render_y = from_y + (to_y - from_y) * fall_progress
                self._draw_block(
                    painter,
                    inner.x() + fx * self._block_size,
                    inner.y() + render_y * self._block_size,
                    cell,
                )

        if self._current is not None:
            for x, y in self._current_render_cells():
                if y < 0:
                    continue
                self._draw_block(
                    painter,
                    inner.x() + x * self._block_size,
                    inner.y() + y * self._block_size,
                    self._current.kind,
                    active=True,
                )

        preview_rect = self._preview_rect
        self._draw_preview(painter, preview_rect)

        stat_values = {
            "分数": str(self._score).zfill(6),
            "消行": str(self._lines).zfill(6),
            "等级": str(self._level).zfill(6),
            "连击": str(self._combo).zfill(6),
        }
        for label, rect in self._stat_cards:
            self._draw_round_panel(painter, rect, self._C_PANEL)
            inset_x = max(10, int(self._block_size * 0.36))
            inset_y = max(4, int(self._block_size * 0.14))
            text_rect = rect.adjusted(inset_x, inset_y, -inset_x, -inset_y)
            painter.setPen(self._C_TEXT)
            painter.setFont(self._stat_label_font)
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, f"{label}：")
            painter.setFont(self._stat_digit_font)
            painter.setPen(self._C_NEON)
            painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, stat_values[label])

        for text, rect in (("暂停" if not self._paused else "继续", self._pause_rect), ("退出", self._exit_rect)):
            self._draw_round_panel(painter, rect, self._C_PANEL)
            painter.setPen(self._C_TEXT)
            painter.setFont(self._label_font)
            painter.drawText(rect, Qt.AlignCenter, text)

        help_rect = self._help_rect
        self._draw_round_panel(painter, help_rect, self._C_PANEL)
        painter.setFont(self._label_font)
        painter.setPen(self._C_NEON)
        painter.drawText(help_rect.adjusted(self._PADDING, self._PADDING, -self._PADDING, -self._PADDING), Qt.AlignLeft | Qt.AlignTop, "CONTROL")
        painter.setFont(self._ui_font)
        painter.setPen(self._C_TEXT_SUB)
        help_lines = [
            "← → 移动",
            "↑ / X 旋转",
            "↓ 加速下落",
            "空格 直接落底",
            "P 暂停 / 继续",
            "R 重新开局",
        ]
        for i, line in enumerate(help_lines):
            painter.drawText(
                QRectF(
                    help_rect.x() + self._PADDING,
                    help_rect.y() + self._PADDING * 2 + self._ROW_H() + i * self._ROW_H(),
                    help_rect.width() - self._PADDING * 2,
                    self._ROW_H(),
                ),
                Qt.AlignLeft | Qt.AlignVCenter,
                line,
            )

        best_score_rect = QRectF(
            help_rect.x() + self._PADDING,
            help_rect.bottom() - self._ROW_H() * 1.6,
            help_rect.width() - self._PADDING * 2,
            self._ROW_H(),
        )
        painter.setFont(self._label_font)
        painter.setPen(self._C_TEXT)
        painter.drawText(
            best_score_rect,
            Qt.AlignHCenter | Qt.AlignVCenter,
            f"最高分：{str(self._best_score).zfill(6)}",
        )

        if self._game_over:
            painter.fillRect(inner, self._C_GAMEOVER)
            painter.setPen(QColor(245, 242, 255))
            painter.setFont(self._digit_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.28, inner.width(), self._ROW_H() * 2),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "GAME OVER",
            )
            painter.setFont(self._title_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.44, inner.width(), self._ROW_H() * 3),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "按 空格 或 回车 重开",
            )
        elif self._paused:
            painter.fillRect(inner, QColor(18, 14, 40, 142))
            painter.setPen(QColor(245, 242, 255))
            painter.setFont(self._digit_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.30, inner.width(), self._ROW_H() * 2),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "PAUSED",
            )
            painter.setFont(self._title_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.46, inner.width(), self._ROW_H() * 2),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "按 P 继续",
            )
        elif self._awaiting_start:
            painter.fillRect(inner, QColor(18, 14, 40, 152))
            painter.setPen(QColor(245, 242, 255))
            painter.setFont(self._title_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.22, inner.width(), self._ROW_H() * 2),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "按任意键开始",
            )
            painter.setFont(self._digit_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.40, inner.width(), self._ROW_H() * 2),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "PRESS ANY KEY",
            )
            painter.setFont(self._title_font)
            painter.drawText(
                QRectF(inner.x(), inner.y() + inner.height() * 0.56, inner.width(), self._ROW_H() * 3),
                Qt.AlignHCenter | Qt.AlignVCenter,
                "TO START",
            )

        painter.end()

    def _ROW_H(self) -> float:
        return float(scale_px(24, min_abs=1))
