"""星空划过粒子：默认参数、召唤数量、方向漂移与淡出曲线。"""

from __future__ import annotations

import random
import unittest

from lib.core.graphics.commands import DrawBatch, EllipseCommand
from lib.core.graphics.types import Color
from lib.core.graphics.visuals import build_particle_batch
from lib.script.plugin_registry import discover_particles, particle_registry
from lib.script.practical.star_streak_particle import (
    CORE_RATIO,
    DEFAULT_BLOOM_RANGE,
    DEFAULT_COLOR,
    DEFAULT_COUNT_RANGE,
    DEFAULT_DIRECTION,
    DEFAULT_DURATION_TICKS,
    DEFAULT_FADE_TICKS,
    STAR_STREAK_PARTICLE_ID,
    StarStreakParticle,
    StarStreakParticleScript,
)


def _script(**options) -> StarStreakParticleScript:
    script = StarStreakParticleScript()
    script.set_request_options(dict(options))
    return script


def _spawn(script: StarStreakParticleScript, area_type: str = "point", area_data=(0.0, 0.0)):
    return script.create_particles(area_type, area_data)


def _effective_alpha(grain: StarStreakParticle) -> float:
    """渲染层实际用的透明度：命令里最后一条就是核心圆。"""
    commands = build_particle_batch([grain]).commands
    return commands[-1].alpha if commands else 0.0


class StarStreakParticleScriptTests(unittest.TestCase):
    def test_particle_is_registered_for_discovery(self):
        discover_particles()

        self.assertEqual(StarStreakParticleScript.PARTICLE_ID, STAR_STREAK_PARTICLE_ID)
        self.assertIn(STAR_STREAK_PARTICLE_ID, particle_registry.get_all_classes())

    def test_default_config_matches_the_documented_parameters(self):
        config = _script().request_config()

        self.assertEqual(config["count_range"], DEFAULT_COUNT_RANGE)
        self.assertEqual(config["color"], DEFAULT_COLOR)
        self.assertEqual(config["bloom_range"], DEFAULT_BLOOM_RANGE)
        self.assertEqual(config["duration_ticks"], DEFAULT_DURATION_TICKS)
        self.assertEqual(config["fade_ticks"], DEFAULT_FADE_TICKS)
        self.assertEqual(config["direction"], DEFAULT_DIRECTION)
        self.assertEqual(config["color"], Color(255, 255, 255))

    def test_request_options_override_every_documented_parameter(self):
        config = _script(
            count_range=(2, 3),
            color=(255, 128, 0),
            bloom_range=(6, 9),
            duration_ticks=40,
            fade_ticks=4,
            direction=(-1.0, 0.0),
        ).request_config()

        self.assertEqual(config["count_range"], (2.0, 3.0))
        self.assertEqual(config["color"], Color(255, 128, 0))
        self.assertEqual(config["bloom_range"], (6.0, 9.0))
        self.assertEqual(config["duration_ticks"], 40)
        self.assertEqual(config["fade_ticks"], 4)
        self.assertEqual(config["direction"], (-1.0, 0.0))

    def test_color_accepts_hex_and_color_objects_and_falls_back_to_white(self):
        self.assertEqual(_script(color="#8cd2ff").request_config()["color"], Color(140, 210, 255))
        self.assertEqual(_script(color=Color(1, 2, 3)).request_config()["color"], Color(1, 2, 3))
        self.assertEqual(_script(color="不知道").request_config()["color"], DEFAULT_COLOR)

    def test_reversed_ranges_and_zero_direction_stay_usable(self):
        config = _script(count_range=(3, 1), bloom_range=(9, 6), direction=(0.0, 0.0)).request_config()

        self.assertEqual(config["count_range"], (1.0, 3.0))
        self.assertEqual(config["bloom_range"], (6.0, 9.0))
        self.assertEqual(config["direction"], DEFAULT_DIRECTION)

    def test_direction_is_normalized(self):
        config = _script(direction=(0.0, 5.0)).request_config()

        self.assertAlmostEqual(config["direction"][0], 0.0)
        self.assertAlmostEqual(config["direction"][1], 1.0)

    def test_default_count_range_can_summon_zero_or_one_grain(self):
        script = _script()
        counts = {len(_spawn(script)) for _ in range(400)}

        self.assertEqual(counts, {0, 1})

    def test_count_range_bounds_the_batch(self):
        script = _script(count_range=(2, 3))
        counts = {len(_spawn(script)) for _ in range(200)}

        self.assertTrue(counts)
        self.assertLessEqual(max(counts), 3)
        self.assertGreaterEqual(min(counts), 2)

    def test_grains_spawn_around_the_requested_point(self):
        script = _script(count_range=(1, 1))
        points = [_spawn(script, "point", (100.0, 50.0))[0] for _ in range(50)]

        self.assertTrue(all(abs(point.x - 100.0) <= 2.0 for point in points))
        self.assertTrue(all(abs(point.y - 50.0) <= 2.0 for point in points))

    def test_grains_spawn_inside_rect_and_circle_areas(self):
        script = _script(count_range=(1, 1))
        rect_points = [_spawn(script, "rect", (0.0, 0.0, 20.0, 10.0))[0] for _ in range(40)]
        circle_points = [_spawn(script, "circle", (0.0, 0.0, 10.0))[0] for _ in range(40)]

        self.assertTrue(all(-2.0 <= point.x <= 22.0 for point in rect_points))
        self.assertTrue(all(-2.0 <= point.y <= 12.0 for point in rect_points))
        self.assertTrue(all((point.x ** 2 + point.y ** 2) ** 0.5 <= 12.5 for point in circle_points))
        self.assertTrue(any((point.x ** 2 + point.y ** 2) ** 0.5 > 4.0 for point in circle_points))


class StarStreakParticleTests(unittest.TestCase):
    def _grain(self, **options) -> StarStreakParticle:
        config = _script(count_range=(1, 1), **options).request_config()
        return StarStreakParticle(10.0, 20.0, config)

    def test_bloom_radius_follows_the_range_and_core_is_a_fraction_of_it(self):
        config = _script(count_range=(1, 1), bloom_range=(2, 4)).request_config()
        grains = [StarStreakParticle(0.0, 0.0, config) for _ in range(80)]

        self.assertTrue(all(2.0 <= grain.bloom <= 4.0 for grain in grains))
        self.assertTrue(all(grain.is_circle for grain in grains))
        for grain in grains:
            with self.subTest(bloom=grain.bloom):
                self.assertAlmostEqual(grain.size, max(1.2, grain.bloom * CORE_RATIO))

    def test_default_direction_drifts_left(self):
        # 布朗抖动是随机的，不固定种子时这条断言会飘。
        random.seed(1234)
        grain = self._grain()
        start_x, start_y = grain.x, grain.y

        for _ in range(20):
            grain.update()

        self.assertLess(grain.x - start_x, -20.0)
        # 主体位移在水平轴上：方向若写成斜向，纵向位移会追平横向位移。
        self.assertLess(abs(grain.y - start_y), abs(grain.x - start_x))

    def test_direction_option_flips_the_drift(self):
        grain = self._grain(direction=(1.0, 0.0))
        start_x = grain.x

        for _ in range(20):
            grain.update()

        self.assertGreater(grain.x - start_x, 20.0)

    def test_grains_keep_drifting_with_brownian_jitter(self):
        config = _script(count_range=(1, 1)).request_config()
        random.seed(1234)
        steps = []
        for _ in range(40):
            grain = StarStreakParticle(0.0, 0.0, config)
            grain.update()
            steps.append((grain.x, grain.y))

        # 布朗抖动让每颗的位移都不一样，但都朝同一个方向。
        self.assertGreater(len({round(x, 3) for x, _y in steps}), 20)
        self.assertTrue(all(x < 0.0 for x, _y in steps))
        self.assertTrue(any(abs(y) > 0.01 for _x, y in steps))

    def test_fade_curve_holds_then_drops_over_the_fade_ticks(self):
        grain = self._grain(duration_ticks=20, fade_ticks=10)
        alphas = []
        for _ in range(20):
            grain.update()
            alphas.append(_effective_alpha(grain))

        self.assertEqual(len(alphas), 20)
        # 前 10 tick 常亮，后 10 tick 线性淡出到 0.1，第 20 tick 结束时死亡。
        self.assertEqual(alphas[:10], [1.0] * 10)
        self.assertEqual(
            [round(alpha, 3) for alpha in alphas[10:]],
            [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0],
        )
        self.assertFalse(grain.alive)

    def test_zero_fade_keeps_the_grain_opaque(self):
        grain = self._grain(duration_ticks=4, fade_ticks=0)

        for _ in range(3):
            grain.update()
            self.assertEqual(_effective_alpha(grain), 1.0)

        grain.update()
        self.assertFalse(grain.alive)

    def test_life_decays_by_one_tick_at_a_time(self):
        grain = self._grain(duration_ticks=20)

        grain.update()

        # 常亮段 life 钉在 1.0：渲染层的淡出阈值管不到这一段，只有淡出窗口才下降。
        self.assertAlmostEqual(grain.life, 1.0, places=6)
        self.assertEqual(grain.max_life, 1.0)

    def test_renderer_reports_the_intended_alpha_curve(self):
        grain = self._grain(duration_ticks=4, fade_ticks=2)
        alphas = []
        for _ in range(3):
            grain.update()
            alphas.append(_effective_alpha(grain))

        # 渲染层按 life 算 alpha，看到的正是「常亮两 tick、再两 tick 线性淡出」。
        self.assertEqual(alphas, [1.0, 1.0, 0.5])
        batch: DrawBatch = build_particle_batch([grain])
        self.assertTrue(all(isinstance(command, EllipseCommand) for command in batch.commands))
        grain.update()
        self.assertFalse(grain.alive)
        self.assertEqual(build_particle_batch([grain]).commands, ())


if __name__ == "__main__":
    unittest.main()
