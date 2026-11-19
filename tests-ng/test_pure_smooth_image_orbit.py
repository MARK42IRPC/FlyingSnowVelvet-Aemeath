"""Shared image orbit contracts, with no game-private renderer."""
from __future__ import annotations
import math
from types import SimpleNamespace
import unittest
from lib.core.services.feedback_scene import FeedbackScene
from lib.core.render.visuals.commands import SpriteCommand, TransformPush
from lib.core.render.visuals.resources import ImageResource, RasterFrame
from lib.core.render.visuals.visuals import build_effect_batch
from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffect, SmoothImageShowEffectScript


class SmoothImageOrbitTests(unittest.TestCase):
    def effect(self, **options):
        return SmoothImageShowEffect('blade',(100,100),0,(100,100),1,(100,100),0,1,**options)

    def test_full_turn_keeps_handle_pivot_fixed(self):
        effect=self.effect(angular_velocity=720,orbit_offset=(30,0))
        for age in (0,.125,.25,.375,.5):
            effect._apply_state(age)
            angle=math.radians(effect._render_rotation)
            self.assertAlmostEqual(effect.x-30*math.cos(angle),100)
            self.assertAlmostEqual(effect.y-30*math.sin(angle),100)
        self.assertAlmostEqual(effect._render_rotation,360)
        self.assertAlmostEqual(effect.x,130)
        self.assertAlmostEqual(effect.y,100)

    def test_fractional_clockwise_position_and_rotation_match(self):
        effect=self.effect(rotation=20,angular_velocity=-720,orbit_offset=(30,0),orbit_start_angle=20)
        effect._tick_prev_age=.10
        effect.age=.15
        effect.apply_frame_interpolation(.5)
        angle=math.radians(-70)
        self.assertAlmostEqual(effect._render_age,.125)
        self.assertAlmostEqual(effect._render_rotation,-70)
        self.assertAlmostEqual(effect._render_x,100+30*math.cos(angle))
        self.assertAlmostEqual(effect._render_y,100+30*math.sin(angle))
        self.assertEqual(effect.age,.15)

    def test_short_sweep_freezes_pose_while_afterimage_keeps_fading(self):
        effect=SmoothImageShowEffect('blade',(100,100),0,(100,100),.06,(100,100),.5,1,
            angular_velocity=6000,angular_duration=.06,orbit_offset=(30,0))
        effect._apply_state(.03)
        self.assertAlmostEqual(effect._render_rotation,180)
        effect._apply_state(.06)
        self.assertAlmostEqual(effect._render_rotation,360)
        for age in (.18,.4):
            effect._apply_state(age)
            self.assertAlmostEqual(effect._render_rotation,360)
            self.assertAlmostEqual(effect.x,130)
            self.assertAlmostEqual(effect.y,100)
            self.assertGreater(effect.opacity,0)
            self.assertLess(effect.opacity,1)
        effect._tick_prev_age=.04
        effect.age=.08
        effect.apply_frame_interpolation(.25)
        self.assertAlmostEqual(effect._render_rotation,300)

    def test_default_motion_and_mutable_static_rotation_are_compatible(self):
        effect=self.effect()
        effect.rotation=37
        effect._apply_state(.4)
        effect._tick_prev_age=.4
        effect.age=.45
        effect.apply_frame_interpolation(.3)
        self.assertEqual((effect._render_x,effect._render_y),(100,100))
        self.assertEqual(effect._render_rotation,37)

    def test_factory_translates_anchor_not_offset_vector(self):
        effect,=SmoothImageShowEffectScript().create_effects('point',(-800,100),{
            'resource_path':'blade','intro_start_pos':(-800,100),'display_pos':(-800,100),
            'outro_end_pos':(-800,100),'display_duration':1,'orbit_offset':(30,0),
            'angular_velocity':720,'rotation':10,'angular_duration':.06,
        },{'offset_x':-1920,'offset_y':-200})
        self.assertEqual((effect.x,effect.y),(1150,300))
        self.assertEqual(effect.orbit_offset,(30,0))
        self.assertEqual(effect.rotation,10)
        self.assertEqual(effect.angular_duration,.06)

    def test_legacy_static_rotation_can_be_set_before_first_frame(self):
        effect=self.effect()
        effect.rotation=37
        effect._visual_resource=ImageResource('blade',(RasterFrame(2,2,bytes([255]*16),100),))
        transform=next(c for c in build_effect_batch([effect]).commands if isinstance(c,TransformPush))
        self.assertAlmostEqual(transform.matrix[0],math.cos(math.radians(37)))

    def test_invalid_optional_motion_remains_finite(self):
        effect=self.effect(rotation='bad',angular_velocity=float('nan'),
                           orbit_offset=(float('inf'),'bad'),orbit_start_angle=None)
        effect._apply_state(.5)
        self.assertEqual((effect.x,effect.y,getattr(effect,'_render_rotation',effect.rotation)),(100,100,0))

    def test_shared_scene_composer_uses_interpolated_orbit(self):
        manager=SimpleNamespace(get_script=lambda key:SmoothImageShowEffectScript())
        scene=FeedbackScene(effect_manager=manager)
        resource=ImageResource('blade',(RasterFrame(2,2,bytes([255]*16),100),))
        effect,=scene.spawn_effect('smooth_image_show','point',(100,100),{
            'intro_start_pos':(100,100),'display_pos':(100,100),'outro_end_pos':(100,100),
            'display_duration':1,'orbit_offset':(30,0),'angular_velocity':720,
        },resource=resource)
        effect._tick_prev_age=.1
        effect.age=.15
        effect.apply_frame_interpolation(.5)
        commands=scene.batch().commands
        transform=next(c for c in commands if isinstance(c,TransformPush))
        self.assertAlmostEqual(transform.matrix[4],100)
        self.assertAlmostEqual(transform.matrix[5],130)
        self.assertTrue(any(isinstance(c,SpriteCommand) for c in commands))
        scene.clear()
        self.assertEqual(scene.effects,())


if __name__=='__main__':
    unittest.main()
