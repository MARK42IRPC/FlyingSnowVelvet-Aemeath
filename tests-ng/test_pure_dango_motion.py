"""Subpixel selection breathing keeps one sprite resource and fixed geometry."""
from __future__ import annotations

import math
import unittest

from lib.core.render.visuals.commands import SpriteCommand, TransformPush
from lib.core.render.visuals.resources import RasterFrame
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.animator import selection_breath


class DangoSelectionMotionTests(unittest.TestCase):
    def test_breathing_is_continuous_bounded_and_replayable(self):
        previous = None
        for index in range(240):
            clock = index / 60.0
            sx, sy, lift = selection_breath(clock)
            self.assertAlmostEqual(sx * sy, 1.0)
            self.assertLessEqual(abs(sx - 1.0), .01401)
            self.assertLessEqual(abs(lift), 1.50001)
            self.assertEqual((sx, sy, lift), selection_breath(clock))
            if previous is not None:
                self.assertLess(abs(lift - previous[2]), .05)
                self.assertLess(abs(sx - previous[0]), .001)
            previous = (sx, sy, lift)
        self.assertNotEqual(selection_breath(1.0), selection_breath(1.0, .7))

    def test_portrait_uses_fixed_sprite_and_fractional_affine_transform(self):
        source = P.FrameSource("motion-test", 1, RasterFrame(8, 8, bytes((255, 255, 255, 255)) * 64, 100))
        geometry, transforms = set(), set()
        for index in range(240):
            commands = []
            P._paint_fighter_plate(commands, P.HitMap(), P.Rect(20, 86, 312, 260),
                "left", "1P", P.R.ROSTER[0], P.C.C_SIDE_A, True, {"amis": source}, clock=index / 60.0)
            sprite_index = next(i for i, c in enumerate(commands) if isinstance(c, SpriteCommand))
            sprite = commands[sprite_index]
            transform = commands[sprite_index - 1]
            self.assertIsInstance(transform, TransformPush)
            self.assertEqual(sprite.position.x, 0.0)
            self.assertEqual(sprite.position.y, 0.0)
            self.assertIs(sprite.frame, source.frame)
            geometry.add((sprite.position, sprite.target_size, sprite.resource_id, sprite.frame_index))
            transforms.add(transform.matrix)
            self.assertTrue(all(math.isfinite(v) for v in transform.matrix))
        self.assertEqual(len(geometry), 1)
        self.assertEqual(len(transforms), 240)
