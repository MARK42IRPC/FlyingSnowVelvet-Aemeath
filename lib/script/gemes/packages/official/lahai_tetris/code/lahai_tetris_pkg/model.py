"""Pure game-state helpers for Lahai Tetris."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import BOARD_H, BOARD_W, SHAPES


@dataclass
class Piece:
    kind: str
    rotation: int = 0
    x: int = BOARD_W // 2
    y: int = 1

    def cells(self) -> list[tuple[int, int]]:
        points = list(SHAPES[self.kind])
        for _ in range(self.rotation % 4):
            points = [(-py, px) for px, py in points]
        return [(self.x + px, self.y + py) for px, py in points]


def create_empty_board() -> list[list[str | None]]:
    return [[None for _ in range(BOARD_W)] for _ in range(BOARD_H)]


def reset_piece(piece: Piece, *, x: int = BOARD_W // 2, y: int = 1, rotation: int = 0) -> Piece:
    return Piece(piece.kind, rotation, x, y)


def can_place(board: list[list[str | None]], piece: Piece) -> bool:
    for x, y in piece.cells():
        if x < 0 or x >= BOARD_W or y >= BOARD_H:
            return False
        if y >= 0 and board[y][x] is not None:
            return False
    return True


def translate_piece(piece: Piece, dx: int, dy: int) -> Piece:
    return Piece(piece.kind, piece.rotation, piece.x + dx, piece.y + dy)


def rotate_piece(piece: Piece, *, rotation_delta: int = 1, dx: int = 0, dy: int = 0) -> Piece:
    return Piece(piece.kind, piece.rotation + rotation_delta, piece.x + dx, piece.y + dy)


def hard_drop_target(board: list[list[str | None]], piece: Piece) -> tuple[Piece, int]:
    distance = 0
    probe = piece
    while True:
        next_probe = translate_piece(probe, 0, 1)
        if not can_place(board, next_probe):
            return probe, distance
        probe = next_probe
        distance += 1


def place_piece(board: list[list[str | None]], piece: Piece) -> list[list[str | None]]:
    new_board = [list(row) for row in board]
    for x, y in piece.cells():
        if 0 <= y < BOARD_H and 0 <= x < BOARD_W:
            new_board[y][x] = piece.kind
    return new_board


def find_full_rows(board: list[list[str | None]]) -> list[int]:
    return [idx for idx, row in enumerate(board) if all(cell is not None for cell in row)]


def clear_rows(board: list[list[str | None]], cleared_rows: list[int]) -> tuple[list[list[str | None]], list[int]]:
    normalized_rows = sorted({row for row in cleared_rows if 0 <= row < BOARD_H})
    if not normalized_rows:
        return [list(row) for row in board], []
    cleared_set = set(normalized_rows)
    kept_rows = [list(row) for idx, row in enumerate(board) if idx not in cleared_set]
    for _ in normalized_rows:
        kept_rows.insert(0, [None for _ in range(BOARD_W)])
    return kept_rows, normalized_rows


def collapse_empty_rows(board: list[list[str | None]]) -> tuple[list[list[str | None]], list[int]]:
    empty_rows = [idx for idx, row in enumerate(board) if all(cell is None for cell in row)]
    if not empty_rows:
        return [list(row) for row in board], []
    empty_set = set(empty_rows)
    kept_rows = [list(row) for idx, row in enumerate(board) if idx not in empty_set]
    for _ in empty_rows:
        kept_rows.insert(0, [None for _ in range(BOARD_W)])
    return kept_rows, empty_rows


def apply_board_gravity(board: list[list[str | None]]) -> list[list[str | None]]:
    new_board = create_empty_board()
    for col in range(BOARD_W):
        write_row = BOARD_H - 1
        for row in range(BOARD_H - 1, -1, -1):
            cell = board[row][col]
            if cell is None:
                continue
            new_board[write_row][col] = cell
            write_row -= 1
    return new_board


def settled_stack_height(board: list[list[str | None]]) -> int:
    for row_index, row in enumerate(board):
        if any(cell is not None for cell in row):
            return BOARD_H - row_index
    return 0


def lowest_fill_columns(board: list[list[str | None]], *, count: int = 3) -> list[int]:
    column_metrics: list[tuple[float, int, int]] = []
    for col in range(BOARD_W):
        occupied_rows = [row for row in range(BOARD_H) if board[row][col] is not None]
        if not occupied_rows:
            continue
        top_row = min(occupied_rows)
        span_height = BOARD_H - top_row
        occupied_count = len(occupied_rows)
        fill_ratio = occupied_count / max(1, span_height)
        column_metrics.append((fill_ratio, top_row, col))
    column_metrics.sort(key=lambda item: (item[0], item[1], item[2]))
    return [col for _, _, col in column_metrics[: max(0, min(count, BOARD_W))]]


def build_fill_columns_result(
    board: list[list[str | None]],
    columns: list[int],
    fill_kind: str,
) -> tuple[list[list[str | None]], list[tuple[int, int]]]:
    new_board = [list(row) for row in board]
    added_cells: list[tuple[int, int]] = []
    for col in columns:
        if col < 0 or col >= BOARD_W:
            continue
        occupied_rows = [row for row in range(BOARD_H) if new_board[row][col] is not None]
        target_top = min(occupied_rows) if occupied_rows else 0
        for row in range(target_top, BOARD_H):
            if new_board[row][col] is None:
                new_board[row][col] = fill_kind
                added_cells.append((col, row))
    return new_board, added_cells


def rows_with_more_than_four_colors(board: list[list[str | None]]) -> list[int]:
    rows: list[int] = []
    for idx, row in enumerate(board):
        colors = {cell for cell in row if cell is not None}
        if len(colors) > 4:
            rows.append(idx)
    return rows


def convert_board_cells(
    board: list[list[str | None]],
    *,
    rng,
    chance: float,
    target_kind: str,
) -> tuple[list[list[str | None]], int]:
    new_board = [list(row) for row in board]
    changed = 0
    probability = max(0.0, min(1.0, float(chance)))
    for y in range(BOARD_H):
        for x in range(BOARD_W):
            cell = new_board[y][x]
            if cell is None or cell == target_kind:
                continue
            if rng.random() <= probability:
                new_board[y][x] = target_kind
                changed += 1
    return new_board, changed


def collect_cross_cells_around_kind(
    board: list[list[str | None]],
    target_kind: str,
) -> list[tuple[int, int]]:
    hits: set[tuple[int, int]] = set()
    for y in range(BOARD_H):
        for x in range(BOARD_W):
            if board[y][x] != target_kind:
                continue
            for dx, dy in ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0)):
                nx = x + dx
                ny = y + dy
                if not (0 <= nx < BOARD_W and 0 <= ny < BOARD_H):
                    continue
                if board[ny][nx] is None:
                    continue
                hits.add((nx, ny))
    return sorted(hits, key=lambda cell: (cell[1], cell[0]))


def clear_board_cells(
    board: list[list[str | None]],
    cells: list[tuple[int, int]],
) -> tuple[list[list[str | None]], list[tuple[int, int]]]:
    new_board = [list(row) for row in board]
    cleared: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for x, y in cells:
        key = (int(x), int(y))
        if key in seen:
            continue
        seen.add(key)
        cx, cy = key
        if not (0 <= cx < BOARD_W and 0 <= cy < BOARD_H):
            continue
        if new_board[cy][cx] is None:
            continue
        new_board[cy][cx] = None
        cleared.append((cx, cy))
    return new_board, cleared


def count_kind_cells(board: list[list[str | None]], kind: str, rows: list[int]) -> int:
    normalized_rows = {row for row in rows if 0 <= row < BOARD_H}
    total = 0
    for row in normalized_rows:
        total += sum(1 for cell in board[row] if cell == kind)
    return total
