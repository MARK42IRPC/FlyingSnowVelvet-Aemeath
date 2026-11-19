"""Real shared weapon effects, clipping, HUD and lifecycle for Feixue."""
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetricsF, QImage, QPainter
from lib.core.render.qt_bridge import create_painter_host
from lib.core.render.visuals.commands import TextCommand
from lib.script.gemes.MAIN.game_packages import GameContext, GamePackageManifest
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P, events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.widget import DangoShowdownWidget

PACKAGE = Path(__file__).resolve().parents[1] / 'lib/script/gemes/packages/official/dango_showdown'


class FrostSlashQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config
        init_font_config()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dango-frost-qt-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8')))
        self.widget = DangoShowdownWidget(GameContext(manifest, PACKAGE, root/'data', root/'cache'))
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.shutdown)
        self.widget._timer.stop()
        self.assertTrue(self.widget._apply_assets(blocking=True), self.widget._asset_loader.error)
        self.widget._sfx = Mock()
        self.widget._event_center = Mock()
        match = self.widget._match
        temporary = next(entry.character_id for entry in P.R.ROSTER
                         if entry.character_id not in (match.right.character_id, 'amis'))
        self.assertTrue(match.choose_dango('left', temporary))
        self.assertTrue(match.choose_dango('right', 'amis'))
        self.assertTrue(match.choose_dango('left', 'feixue'))
        self.assertTrue(match.choose_bet_amount(1))
        self.assertTrue(match.start_match())
        match.countdown = 0
        match.balls = tuple(replace(ball, x=x, y=.5) for ball, x in zip(match.balls, (.4, .6)))
        self.payload = dict(side='left', target='right', point=(.4, .5), radius=.286,
                            hit=True, damage=30, slow_seconds=3, slow_multiplier=.5, energy=0,
                            cooldown=3, hp_left=match.left.hp, hp_right=150)

    @staticmethod
    def pixels(image):
        return image.constBits().asstring(image.byteCount())

    def render(self, width=1080, height=720):
        batch, _ = P.paint_snapshot(P.ScreenSnapshot(phase=P.MatchPhase.FIGHT), 1080, 720,
                                     feedback=self.widget._feedback.batch())
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.scale(width/1080, height/720)
        create_painter_host().render(batch, painter)
        painter.end()
        return image

    def test_instant_blade_keeps_handle_pivot_then_leaves_stationary_fading_crescent(self):
        self.widget._handle_skill_event(ev.FROST_SLASH, self.payload)
        cast = self.widget._frost_blades[0]
        image = cast['image']
        self.assertEqual(type(image).__module__, 'lib.script.effects.smooth_image_show_effect')
        self.assertEqual((image.angular_velocity, image.angular_duration), (6000, .06))
        resource = image._visual_resource
        self.assertIn('feixue_blade', resource.resource_id)
        self.assertAlmostEqual(resource.size[0] * (1 - self.widget._blade_pivot[0]) * image.scale,
                               .286 * self.widget._arena_rect().width)
        self.assertEqual(len(self.widget._feedback.effects), 11, 'blade, crescent, short flash and eight petals')
        self.widget._sfx.play.assert_called_with('sun_echo')
        colors = {(p.color.red, p.color.green, p.color.blue) for p in self.widget._feedback.particles}
        self.assertTrue({(235, 253, 255), (105, 220, 255)} <= colors)
        flakes = [particle for particle in self.widget._feedback.particles if getattr(particle, 'is_snowflake', False)]
        self.assertEqual(len(flakes), 14)
        self.assertTrue(all(type(particle).__module__ == 'lib.script.practical.frost_snowflake_particle'
                            for particle in flakes))
        previous = image._render_rotation
        self.widget._match.balls = (replace(self.widget._match.balls[0], x=.45, y=.55), self.widget._match.balls[1])
        for elapsed in (.03, .03, .03):
            self.widget._advance_visuals(elapsed)
            angle = math.radians(image._render_rotation)
            dx = (self.widget._blade_pivot[0] - .5) * resource.size[0] * image.scale
            dy = (self.widget._blade_pivot[1] - .5) * resource.size[1] * image.scale
            grip = (image._render_x + dx*math.cos(angle) - dy*math.sin(angle),
                    image._render_y + dx*math.sin(angle) + dy*math.cos(angle))
            center = self.widget._canvas_point((.45, .55))
            self.assertAlmostEqual(grip[0], center[0], places=6)
            self.assertAlmostEqual(grip[1], center[1], places=6)
            self.assertGreaterEqual(image._render_rotation, previous)
            previous = image._render_rotation
        self.assertGreater(previous, image.rotation)
        self.widget._advance_visuals(.07)
        self.assertNotIn(image, self.widget._feedback.effects)
        trail = next(item for item in self.widget._feedback.effects if 'slash_trail' in item._visual_resource.resource_id)
        self.assertAlmostEqual(trail._render_rotation, trail.rotation + 360)
        alpha = trail._render_opacity
        self.widget._advance_visuals(.2)
        self.assertAlmostEqual(trail._render_rotation, trail.rotation + 360)
        self.assertLess(trail._render_opacity, alpha)
        self.widget._advance_visuals(.3)
        self.assertFalse(self.widget._frost_blades)

    def test_right_caster_reverses_sweep_without_touching_match_rng(self):
        previous = self.widget._match.rng.getstate()
        self.widget._handle_skill_event(ev.FROST_SLASH, dict(self.payload, side='right', target='left', point=(.6, .5)))
        self.assertEqual(self.widget._match.rng.getstate(), previous)
        blade = self.widget._frost_blades[0]['image']
        self.assertEqual((blade.angular_velocity, blade.angular_duration), (-6000, .06))
        trail = next(item for item in self.widget._feedback.effects if 'slash_trail' in item._visual_resource.resource_id)
        self.assertTrue(trail._visual_resource.resource_id.endswith(':reverse'))
        self.widget._advance_visuals(.18)
        self.assertAlmostEqual(trail._render_rotation, trail.rotation - 360)

    def test_hit_numbers_are_emitted_once_and_body_numbers_use_actual_hp_loss(self):
        self.widget._show_damage_number = Mock()
        self.widget._handle_skill_event(ev.SKILL_DAMAGE, dict(self.payload, skill='frost_slash'))
        self.widget._handle_skill_event(ev.FROST_SLASH, self.payload)
        self.widget._show_damage_number.assert_called_once()
        self.widget._float_number_at = Mock()
        self.widget._show_collision_damage(dict(point=(.5, .5), damage_left=1, damage_right=3))
        self.assertEqual([call.args[0]['damage'] for call in self.widget._float_number_at.call_args_list], [1, 3])

    def test_miss_has_complete_blade_and_petals_and_four_corner_clip(self):
        arena = P.Layout().arena.rect
        for width, height in ((1080, 720), (810, 540)):
            self.widget._feedback.clear()
            quiet = self.render(width, height)
            scale = width/1080
            for point in ((0, 0), (1, 0), (0, 1), (1, 1)):
                self.widget._feedback.clear()
                self.widget._frost_blades.clear()
                self.widget._handle_skill_event(ev.FROST_SLASH, dict(self.payload, point=point, hit=False, damage=0))
                self.assertEqual(len(self.widget._feedback.effects), 11)
                self.widget._feedback.advance(.13)
                active = self.render(width, height)
                self.assertNotEqual(self.pixels(quiet), self.pixels(active))
                for rect in ((0, 0, width, int(arena.y*scale)),
                             (0, int(arena.bottom*scale)+1, width, height-int(arena.bottom*scale)-1),
                             (0, 0, int(arena.x*scale), height),
                             (int(arena.right*scale)+1, 0, width-int(arena.right*scale)-1, height)):
                    self.assertEqual(self.pixels(quiet.copy(*rect)), self.pixels(active.copy(*rect)), (width, point, rect))

    def test_pause_freezes_pose_and_reset_clears_only_local_feedback(self):
        self.widget._handle_skill_event(ev.FROST_SLASH, self.payload)
        self.widget._advance_visuals(.1)
        image = self.widget._frost_blades[0]['image']
        previous = (image.age, image._render_rotation, image._render_x, image._render_y)
        flake = next(particle for particle in self.widget._feedback.particles if getattr(particle, 'is_snowflake', False))
        frozen_flake = (flake.x, flake.y, flake.rotation, flake.life)
        self.widget._paused = True
        with patch('time.perf_counter', return_value=self.widget._last_tick + .1):
            self.widget._on_tick()
        self.assertEqual(previous, (image.age, image._render_rotation, image._render_x, image._render_y))
        self.assertEqual(frozen_flake, (flake.x, flake.y, flake.rotation, flake.life))
        self.widget._reset_presentation()
        self.assertFalse(self.widget._feedback.effects)
        self.assertFalse(self.widget._feedback.particles)
        self.assertFalse(self.widget._frost_blades)

    def test_live_hud_and_slow_status_fit_the_real_font_at_minimum_size(self):
        match = self.widget._match
        match.left.frost_energy = 3
        match.left.frost_elapsed = 1.7
        match.right.slow_seconds = 2.4
        match.right.slow_multiplier = .5
        snapshot = self.widget._snapshot()
        self.assertEqual((snapshot.characters[0].frost_energy, snapshot.characters[0].frost_elapsed), (3, 1.7))
        self.assertEqual(snapshot.characters[1].slow_seconds, 2.4)
        batch, _ = P.paint_snapshot(snapshot, 1080, 720)
        checked = 0
        for command in batch.commands:
            if not isinstance(command, TextCommand) or command.text not in {
                '霜渐能量', '3/4', '环斩冷却', '1.3秒', '减速50% · 2.4秒',
                '3秒环斩30 · 命中减速50%', '落空+1能量 · 碰撞耗1增伤2'}:
                continue
            font = QFont(command.font.family)
            font.setPixelSize(round(command.font.pixel_size))
            font.setBold(command.font.bold)
            metrics = QFontMetricsF(font)
            self.assertLessEqual(metrics.horizontalAdvance(command.text), command.rect.width+1, command.text)
            self.assertLessEqual(metrics.height(), command.rect.height+2, command.text)
            checked += 1
        self.assertEqual(checked, 7)


if __name__ == '__main__':
    unittest.main()
