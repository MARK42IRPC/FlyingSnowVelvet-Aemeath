import unittest

from config.config_animation import ANIMATION


class AnimationDefaultsTests(unittest.TestCase):
    def test_default_frame_rate_balances_smoothness_and_idle_cost(self):
        self.assertEqual(ANIMATION["frame_fps"], 120)


if __name__ == "__main__":
    unittest.main()
