import unittest

from lib.script.ui.animation_player import (
    _build_exit_shadow_metrics,
    _normalize_exit_shadow_direction,
)


class ExitAnimationShadowConfigTests(unittest.TestCase):
    def test_normalize_direction_accepts_cn_alias(self):
        self.assertEqual(_normalize_exit_shadow_direction("向左下"), "down_left")
        self.assertEqual(_normalize_exit_shadow_direction("不偏移"), "center")

    def test_build_metrics_expands_canvas_for_diagonal_shadow(self):
        metrics = _build_exit_shadow_metrics(300, 300, 112, 14, "down_right")

        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["direction"], "down_right")
        self.assertGreater(metrics["canvas_w"], 300)
        self.assertGreater(metrics["canvas_h"], 300)
        self.assertGreater(metrics["offset_x"], 0)
        self.assertGreater(metrics["offset_y"], 0)

    def test_zero_strength_disables_shadow(self):
        self.assertIsNone(_build_exit_shadow_metrics(300, 300, 0, 14, "down"))


if __name__ == "__main__":
    unittest.main()
