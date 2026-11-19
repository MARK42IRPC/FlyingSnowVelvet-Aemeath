"""Desktop snowflake script and shared geometry/interpolation contracts."""
import math
import random
from types import SimpleNamespace
import unittest

from lib.core.render.visuals.commands import LineCommand
from lib.core.render.visuals.visuals import build_particle_batch
from lib.core.services.feedback_scene import FeedbackScene, interpolate_particles, prepare_particles
from lib.script.practical.frost_snowflake_particle import FrostSnowflakeParticleScript


class FrostSnowflakeTests(unittest.TestCase):
    def test_seed_replays_without_consuming_global_or_game_randomness(self):
        script = FrostSnowflakeParticleScript()
        script.set_request_options({'seed': 71, 'count': 12, 'rgb': (125, 220, 255)})
        previous = random.getstate()
        first = script.create_particles('circle', (-10, -20, 5))
        second = script.create_particles('circle', (-10, -20, 5))
        self.assertEqual(random.getstate(), previous)
        self.assertEqual([vars(p) for p in first], [vars(p) for p in second])
        self.assertEqual(len(first), 12)
        for particle in first:
            self.assertLessEqual(math.hypot(particle.x + 10, particle.y + 20), 5)
            self.assertEqual((particle.color.red, particle.color.green, particle.color.blue), (125, 220, 255))
            self.assertTrue(.55 <= particle.life <= .9)
            self.assertTrue(8 <= particle.size <= 14)

    def test_six_arms_and_short_branches_are_one_backend_neutral_line_batch(self):
        script = FrostSnowflakeParticleScript()
        script.set_request_options({'seed': 5, 'count': 1})
        flake = script.create_particles('point', (10, 20))[0]
        flake.size, flake.rotation = 12, 0
        batch = build_particle_batch([flake])
        self.assertEqual(len(batch.commands), 18)
        self.assertTrue(all(isinstance(command, LineCommand) for command in batch.commands))
        arms = [command for command in batch.commands if (command.start.x, command.start.y) == (10, 20)]
        self.assertEqual(len(arms), 6)
        for arm in arms:
            self.assertAlmostEqual(math.hypot(arm.end.x - 10, arm.end.y - 20), 6)
        self.assertTrue(all(abs(point.x - 10) <= 6 and abs(point.y - 20) <= 6
                            for command in batch.commands for point in (command.start, command.end)))

    def test_shared_scene_interpolates_rotation_without_wrapping_and_resets_options(self):
        script = FrostSnowflakeParticleScript()
        manager = SimpleNamespace(get_script=lambda identifier: script if identifier == 'frost_snowflake' else None)
        scene = FeedbackScene(particle_manager=manager)
        flake = scene.spawn_particle('frost_snowflake', 'point', (10, 20),
                                      {'count': 1, 'seed': 4, 'life': .3, 'speed': 0})[0]
        self.assertEqual(script._request_options, {})
        flake.rotation, flake.angular_velocity = 350, 400
        scene.advance(.075)
        self.assertAlmostEqual(flake.rotation, 370)
        self.assertAlmostEqual(flake._render_rotation, 360)
        self.assertGreater(flake.y, 19, 'a flake should drift gently rather than jump across the scene')
        scene.advance(.3)
        self.assertFalse(scene.particles)
        legacy = SimpleNamespace(x=1, y=2)
        prepare_particles([legacy])
        legacy.x = 3
        interpolate_particles([legacy], .5)
        self.assertEqual(legacy._render_x, 2)
        self.assertFalse(hasattr(legacy, '_render_rotation'), 'old particles gain no new rotation behaviour')

    def test_expiry_and_fade_preserve_the_shared_lifetime_contract(self):
        script = FrostSnowflakeParticleScript()
        script.set_request_options({'count': 1, 'seed': 2, 'life': .5, 'speed': 0})
        flake = script.create_particles('rect', (0, 0, 1, 1))[0]
        start = build_particle_batch([flake]).commands[0].alpha
        for _ in range(8):
            flake.update()
        self.assertTrue(flake.alive)
        self.assertLess(build_particle_batch([flake]).commands[0].alpha, start)
        for _ in range(2):
            flake.update()
        self.assertFalse(flake.alive)
        self.assertFalse(build_particle_batch([flake]).commands)


if __name__ == '__main__':
    unittest.main()
