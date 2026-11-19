"""Actual desktop scripts reused in independently owned, toolkit-free scenes."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from lib.core.services.feedback_scene import (
    FeedbackScene, create_effect_instances, create_particle_instances,
    interpolate_effects, interpolate_particles, prepare_effects, prepare_particles,
    update_instances,
)
from lib.core.render.visuals.resources import ImageResource, RasterFrame
from lib.core.render.visuals.visuals import build_effect_batch, build_particle_batch
from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffect, SmoothImageShowEffectScript
from lib.script.practical.floating_text_particle import FloatingTextParticle, FloatingTextParticleScript
from lib.script.practical.burst_line_particle import BurstLineParticle, BurstLineParticleScript


class FeedbackSceneTests(unittest.TestCase):
    def setUp(self):
        self.floating = FloatingTextParticleScript()
        self.burst = BurstLineParticleScript()
        self.particles = SimpleNamespace(get_script=lambda key: {
            'floating_text': self.floating, 'burst_line': self.burst,
        }.get(key))
        self.effects = SimpleNamespace(get_script=lambda key: SmoothImageShowEffectScript()
                                       if key == 'smooth_image_show' else None)
        self.scene = FeedbackScene(particle_manager=self.particles, effect_manager=self.effects)
        self.resource = ImageResource('test-existing-image', (RasterFrame(2, 2, bytes([255] * 16), 100),))

    def test_real_existing_particle_classes_and_shared_composer_are_used(self):
        created = self.scene.spawn_particle('burst_line', 'point', (200, 200), {'count_range': (6, 6)})
        self.assertEqual(len(created), 6)
        self.assertTrue(all(isinstance(item, BurstLineParticle) for item in created))
        self.assertEqual(self.scene.batch(), build_particle_batch(list(self.scene.particles)))
        initial = [(item.x, item.y) for item in created]
        self.scene.advance(.075)
        self.assertNotEqual(initial, [(item.x, item.y) for item in created])
        self.assertTrue(self.scene.batch().commands)
        self.scene.advance(1)
        self.assertEqual(self.scene.particles, ())

    def test_real_image_script_and_composer_and_identity_removal(self):
        options = {'intro_start_pos': (40, 50), 'display_pos': (40, 50),
                   'outro_end_pos': (40, 50), 'intro_duration': 0,
                   'display_duration': .2, 'outro_duration': .2}
        first, = self.scene.spawn_effect('smooth_image_show', 'point', (40, 50), options, resource=self.resource)
        second, = self.scene.spawn_effect('smooth_image_show', 'point', (40, 50), options, resource=self.resource)
        self.assertIsInstance(first, SmoothImageShowEffect)
        self.assertEqual(self.scene.batch(), build_effect_batch([first, second]))
        self.scene.remove(first)
        self.assertEqual(self.scene.effects, (second,))
        self.scene.advance(.5)
        self.assertFalse(self.scene.effects)

    def test_shared_image_effect_animates_variable_duration_frames_at_double_speed(self):
        resource = ImageResource('existing-gif', tuple(
            RasterFrame(1, 1, bytes((red, 0, 0, 255)), duration)
            for red, duration in ((50, 100), (100, 200), (200, 300))
        ))
        options = {'intro_start_pos': (40, 50), 'display_pos': (40, 50),
                   'outro_end_pos': (40, 50), 'intro_duration': 0,
                   'display_duration': 2, 'outro_duration': .1,
                   'animate': True, 'playback_rate': 2, 'animation_loop': False}
        effect, = self.scene.spawn_effect('smooth_image_show', 'point', (40, 50), options, resource=resource)
        for seconds, expected in ((0, 0), (.05, 1), (.15, 2), (2, 2)):
            effect.animation_seconds = seconds
            command, = self.scene.batch().commands
            self.assertEqual(command.frame_index, expected)
            self.assertEqual(command.frame, resource.frames[expected])
        effect.animate = False
        self.assertEqual(self.scene.batch().commands[0].frame_index, 0)

    def test_negative_desktop_origin_translates_both_spawn_and_text_destination(self):
        options = {'text': '-20', 'target_x': -800, 'target_y': 100}
        item, = create_particle_instances(self.particles, 'floating_text', 'point', (-800, 150), options,
                                          offset_x=-1920, offset_y=0)
        self.assertIsInstance(item, FloatingTextParticle)
        self.assertEqual((item.x, item.y), (1120, 150))
        self.assertEqual((item._target_x, item._target_y), (1120, 100))
        self.assertEqual(options, {'text': '-20', 'target_x': -800, 'target_y': 100})
        self.assertEqual(self.floating._request_options, {})

    def test_scene_ownership_does_not_clear_other_scenes_and_clocks_freeze_without_advance(self):
        other = FeedbackScene(particle_manager=self.particles)
        self.scene.spawn_particle('floating_text', 'point', (40, 50), {'text': '-1'})
        other.spawn_particle('floating_text', 'point', (40, 50), {'text': '-2'})
        self.scene.advance(.125)
        frozen = self.scene.batch()
        self.assertEqual(frozen, self.scene.batch())
        self.assertAlmostEqual(other.particles[0].life, other.particles[0].max_life)
        self.scene.clear()
        self.assertFalse(self.scene.batch().commands)
        self.assertEqual(len(other.particles), 1)
        self.assertEqual(other.particles[0].text, '-2')

    def test_shared_helpers_keep_particle_logic_as_interpolation_origin(self):
        particle, = create_particle_instances(self.particles, 'floating_text', 'point', (0, 0),
                                              {'text': '-1', 'target_x': 100, 'target_y': 100})
        particle._render_x = -500
        prepare_particles([particle])
        self.assertEqual(particle._tick_prev_x, 0)
        alive = update_instances([particle])
        interpolate_particles(alive, .5)
        self.assertGreaterEqual(particle._render_x, 0)
        self.assertEqual(update_instances([SimpleNamespace(update=lambda: 1/0)]), [])

    def test_shared_effect_helpers_resolve_global_options_and_interpolation(self):
        options = {'resource_path': 'test.png', 'intro_start_pos': (-800, 50),
                   'display_pos': (-800, 100), 'outro_end_pos': (-800, 150),
                   'intro_duration': .1, 'display_duration': .1, 'outro_duration': .1}
        item, = create_effect_instances(self.effects, 'smooth_image_show', 'point', (-800, 100), options,
                                        request_context={'offset_x': -1920, 'offset_y': 0})
        self.assertEqual(item.display_pos, (1120, 100))
        prepare_effects([item])
        update_instances([item])
        interpolate_effects([item], .5)
        self.assertEqual(item._render_x, 1120)
        self.assertGreater(item._render_y, 50)
        self.assertLess(item._render_y, 100)

if __name__ == '__main__':
    unittest.main()
