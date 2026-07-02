import unittest

from lib.script.gemes.MAIN.lahai_tetris.model import (
    build_fill_columns_result,
    collapse_empty_rows,
    clear_board_cells,
    collect_cross_cells_around_kind,
    convert_board_cells,
    count_kind_cells,
    Piece,
    apply_board_gravity,
    can_place,
    clear_rows,
    create_empty_board,
    find_full_rows,
    hard_drop_target,
    lowest_fill_columns,
    place_piece,
    rows_with_more_than_four_colors,
    settled_stack_height,
)


class LahaiTetrisModelTests(unittest.TestCase):
    class _FixedRng:
        def __init__(self, values):
            self._values = list(values)

        def random(self):
            if self._values:
                return self._values.pop(0)
            return 1.0

    def test_can_place_allows_piece_above_visible_board(self):
        board = create_empty_board()
        piece = Piece("B", x=4, y=-1)

        self.assertTrue(can_place(board, piece))

    def test_can_place_rejects_out_of_bounds_piece(self):
        board = create_empty_board()
        piece = Piece("A", x=-1, y=1)

        self.assertFalse(can_place(board, piece))

    def test_can_place_rejects_collision_with_settled_cells(self):
        board = create_empty_board()
        board[5][4] = "X"
        piece = Piece("B", x=4, y=5)

        self.assertFalse(can_place(board, piece))

    def test_hard_drop_target_stops_on_existing_stack(self):
        board = create_empty_board()
        board[19][4] = "X"
        board[19][5] = "X"
        piece = Piece("B", x=4, y=1)

        target, distance = hard_drop_target(board, piece)

        self.assertEqual(distance, 16)
        self.assertEqual((target.x, target.y, target.rotation), (4, 17, 0))

    def test_place_piece_returns_new_board_with_piece_cells(self):
        board = create_empty_board()
        piece = Piece("B", x=4, y=5)

        placed = place_piece(board, piece)

        self.assertIsNone(board[5][4])
        self.assertEqual(placed[5][4], "B")
        self.assertEqual(placed[5][5], "B")
        self.assertEqual(placed[6][4], "B")
        self.assertEqual(placed[6][5], "B")

    def test_find_full_rows_returns_all_completed_lines(self):
        board = create_empty_board()
        board[18] = ["A"] * 10
        board[19] = ["B"] * 10
        board[17][0] = "C"

        self.assertEqual(find_full_rows(board), [18, 19])

    def test_clear_rows_removes_requested_lines_and_inserts_empty_rows_on_top(self):
        board = create_empty_board()
        board[17][0] = "A"
        board[18] = ["B"] * 10
        board[19] = ["C"] * 10

        cleared_board, normalized_rows = clear_rows(board, [19, 18, 19, -1, 99])

        self.assertEqual(normalized_rows, [18, 19])
        self.assertEqual(cleared_board[0], [None] * 10)
        self.assertEqual(cleared_board[1], [None] * 10)
        self.assertEqual(cleared_board[19][0], "A")
        self.assertTrue(all(cell is None for cell in cleared_board[19][1:]))

    def test_apply_board_gravity_compacts_each_column_independently(self):
        board = create_empty_board()
        board[10][0] = "A"
        board[15][0] = "B"
        board[5][3] = "C"
        board[18][3] = "D"

        compacted = apply_board_gravity(board)

        self.assertEqual(compacted[18][0], "A")
        self.assertEqual(compacted[19][0], "B")
        self.assertEqual(compacted[18][3], "C")
        self.assertEqual(compacted[19][3], "D")
        self.assertIsNone(compacted[10][0])
        self.assertIsNone(compacted[5][3])

    def test_settled_stack_height_counts_from_first_occupied_row(self):
        board = create_empty_board()
        self.assertEqual(settled_stack_height(board), 0)

        board[14][2] = "A"
        self.assertEqual(settled_stack_height(board), 6)

    def test_lowest_fill_columns_ignores_empty_columns_and_prefers_lower_fill_ratio(self):
        board = create_empty_board()
        board[19][0] = "A"
        board[18][0] = "A"
        board[19][1] = "A"
        board[17][2] = "A"
        board[18][2] = "A"
        board[19][2] = "A"

        self.assertEqual(lowest_fill_columns(board, count=3), [2, 0, 1])

    def test_lowest_fill_columns_uses_fill_ratio_within_current_span(self):
        board = create_empty_board()
        board[16][0] = "A"
        board[19][0] = "A"
        board[16][1] = "A"
        board[18][1] = "A"
        board[19][1] = "A"
        board[17][2] = "A"
        board[18][2] = "A"
        board[19][2] = "A"

        self.assertEqual(lowest_fill_columns(board, count=3), [0, 1, 2])

    def test_build_fill_columns_result_fills_from_current_top_to_bottom(self):
        board = create_empty_board()
        board[17][0] = "A"
        board[19][1] = "B"

        filled_board, added_cells = build_fill_columns_result(board, [0, 2], "SCISSOR")

        self.assertIn((0, 18), added_cells)
        self.assertIn((0, 19), added_cells)
        self.assertIn((2, 0), added_cells)
        self.assertEqual(filled_board[18][0], "SCISSOR")
        self.assertEqual(filled_board[19][0], "SCISSOR")
        self.assertIsNone(filled_board[18][1])
        self.assertEqual(filled_board[19][1], "B")

    def test_rows_with_more_than_four_colors(self):
        board = create_empty_board()
        board[18][:5] = ["A", "B", "C", "D", "E"]
        board[19][:4] = ["A", "B", "C", "D"]

        self.assertEqual(rows_with_more_than_four_colors(board), [18])

    def test_rows_with_more_than_four_colors_counts_special_blocks_as_extra_color(self):
        board = create_empty_board()
        board[18][:5] = ["A", "B", "C", "D", "SUN"]

        self.assertEqual(rows_with_more_than_four_colors(board), [18])

    def test_rows_with_more_than_four_colors_counts_sun_and_scissor_as_distinct_colors(self):
        board = create_empty_board()
        board[18][:5] = ["A", "B", "C", "SUN", "SCISSOR"]

        self.assertEqual(rows_with_more_than_four_colors(board), [18])

    def test_convert_board_cells_and_count_kind_cells(self):
        board = create_empty_board()
        board[18][0] = "C"
        board[19][0] = "A"
        board[19][1] = "B"
        rng = self._FixedRng([0.01, 0.03, 0.99])

        converted, changed = convert_board_cells(board, rng=rng, chance=0.10, target_kind="SUN")

        self.assertEqual(changed, 2)
        self.assertEqual(converted[19][0], "SUN")
        self.assertEqual(converted[19][1], "B")
        self.assertEqual(converted[18][0], "SUN")
        self.assertEqual(count_kind_cells(converted, "SUN", [18, 19]), 2)

    def test_collect_cross_cells_around_kind_returns_unique_occupied_cross_area(self):
        board = create_empty_board()
        board[10][4] = "SUN"
        board[9][4] = "A"
        board[11][4] = "B"
        board[10][3] = "C"
        board[10][5] = "D"
        board[11][5] = "SUN"
        board[12][5] = "E"
        board[11][6] = "F"

        cells = collect_cross_cells_around_kind(board, "SUN")

        self.assertEqual(
            cells,
            [(4, 9), (3, 10), (4, 10), (5, 10), (4, 11), (5, 11), (6, 11), (5, 12)],
        )

    def test_clear_board_cells_only_clears_requested_occupied_cells(self):
        board = create_empty_board()
        board[18][0] = "A"
        board[18][1] = "SUN"
        board[19][1] = "B"

        cleared_board, cleared_cells = clear_board_cells(board, [(0, 18), (1, 18), (1, 18), (9, 0)])

        self.assertEqual(cleared_cells, [(0, 18), (1, 18)])
        self.assertIsNone(cleared_board[18][0])
        self.assertIsNone(cleared_board[18][1])
        self.assertEqual(cleared_board[19][1], "B")

    def test_collapse_empty_rows_pulls_upper_rows_down(self):
        board = create_empty_board()
        board[15][0] = "A"
        board[17][1] = "B"
        board[19][2] = "C"

        collapsed_board, empty_rows = collapse_empty_rows(board)

        self.assertEqual(empty_rows, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18])
        self.assertEqual(collapsed_board[17][0], "A")
        self.assertEqual(collapsed_board[18][1], "B")
        self.assertEqual(collapsed_board[19][2], "C")


if __name__ == "__main__":
    unittest.main()
