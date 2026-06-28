import unittest

from lib.script.gemes.MAIN.lahai_tetris import LahaiPieceRandomizer


class _FixedRng:
    def __init__(self, values):
        self._values = list(values)

    def random(self):
        if self._values:
            return self._values.pop(0)
        return 0.0

    def choice(self, values):
        return list(values)[0]


class LahaiPieceRandomizerTests(unittest.TestCase):
    def test_selected_piece_loses_weight_and_others_gain_weight(self):
        randomizer = LahaiPieceRandomizer(("A", "B", "C"), _FixedRng([0.0]))

        self.assertEqual(randomizer.next_kind(), "A")

        self.assertEqual(randomizer.generated_count, 1)
        self.assertAlmostEqual(randomizer.weights_snapshot()["A"], 0.8)
        self.assertAlmostEqual(randomizer.weights_snapshot()["B"], 1.05)
        self.assertAlmostEqual(randomizer.weights_snapshot()["C"], 1.05)

    def test_weight_never_drops_below_zero(self):
        randomizer = LahaiPieceRandomizer(("A", "B"), _FixedRng([0.0] * 8))

        for _ in range(8):
            randomizer.next_kind()

        self.assertGreaterEqual(randomizer.weights_snapshot()["A"], 0.0)

    def test_weights_reset_every_fourteen_generated_pieces(self):
        randomizer = LahaiPieceRandomizer(("A", "B"), _FixedRng([0.0] * 15), reset_interval=14)

        for _ in range(14):
            randomizer.next_kind()
        self.assertEqual(randomizer.generated_count, 14)
        self.assertNotEqual(randomizer.weights_snapshot(), {"A": 1.0, "B": 1.0})

        randomizer.next_kind()
        self.assertEqual(randomizer.generated_count, 1)
        self.assertEqual(randomizer.weights_snapshot(), {"A": 0.8, "B": 1.05})


if __name__ == "__main__":
    unittest.main()
