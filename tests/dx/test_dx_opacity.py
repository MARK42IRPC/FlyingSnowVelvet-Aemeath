from __future__ import annotations

import unittest

from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.opacity import DxOpacityAnimator
from lib.core.graphics.commands import DrawBatch, RectCommand, scale_batch_alpha
from lib.core.graphics.types import Color, Rect


class DxOpacityTests(unittest.TestCase):
    def test_batch_alpha_scales_only_drawable_commands(self):
        batch = DrawBatch((
            RectCommand(Rect(0, 0, 10, 10), fill=Color(1, 2, 3), alpha=0.8),
        ))

        scaled = scale_batch_alpha(batch, 0.25)

        self.assertAlmostEqual(scaled.commands[0].alpha, 0.2)
        self.assertAlmostEqual(batch.commands[0].alpha, 0.8)

    def test_animator_uses_in_out_curve_and_finishes_hide(self):
        now = [0.0]
        repaints = []
        finished = []
        context = DxLoopContext(clock=lambda: now[0])
        animator = DxOpacityAnimator(
            context,
            lambda: repaints.append(animator.value),
            duration_ms=200,
        )

        animator.fade_in()
        now[0] = 0.1
        context.run_once()
        self.assertAlmostEqual(animator.value, 0.5, places=2)
        now[0] = 0.2
        context.run_once()
        self.assertAlmostEqual(animator.value, 1.0)

        animator.fade_out(lambda: finished.append(True))
        now[0] = 0.4
        context.run_once()
        self.assertEqual(finished, [True])
        self.assertEqual(animator.value, 0.0)
        self.assertGreaterEqual(len(repaints), 4)


if __name__ == "__main__":
    unittest.main()
