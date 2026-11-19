"""Sun-spirit inventory, keyed resources and event-stage drawing contracts."""
from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from lib.core.render.visuals.commands import EllipseCommand, SpriteCommand, TextCommand
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader

PACKAGE = Path(__file__).resolve().parents[1] / 'lib/script/gemes/packages/official/dango_showdown'


class SunSpiritPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = DangoAssetLoader(PACKAGE / 'assets', inline=True)
        assert loader.ready, loader.error
        cls.resources = loader.assets()['sun_spirits']
        cls.sources = {key: P.FrameSource(resource.resource_id, 1, resource.frames[0])
                       for key, resource in cls.resources.items()}

    def paint(self, visual):
        commands = []
        P._paint_sun_spirits(commands, P.Rect(0, 0, 500, 500), (visual,))
        return commands

    def test_original_art_is_copied_and_matte_is_removed_without_losing_body(self):
        root = Path(__file__).resolve().parents[1]
        for key, original in (('yellow', '日灵1.png'), ('cyan', '日灵2.png')):
            with self.subTest(color=key):
                self.assertEqual((PACKAGE / f'assets/effects/sun_spirit_{key}.png').read_bytes(),
                                 (root / 'resc/GIF' / original).read_bytes())
                frame = self.resources[key].frames[0]
                self.assertEqual((frame.width, frame.height), (512, 512))
                self.assertEqual(frame.pixels[3], 0)
                alphas = frame.pixels[3::4]
                self.assertGreater(sum(alpha == 255 for alpha in alphas), 30000)
                self.assertGreater(sum(alpha == 0 for alpha in alphas), 30000)
                self.assertTrue(any(0 < alpha < 255 for alpha in alphas))

    def test_four_inventory_slots_preserve_color_order_and_independent_cooldowns(self):
        view = P.CharacterView('left', 'xigelika', '西格莉卡',
                               sun_energy=('cyan', 'yellow', 'cyan', 'yellow'),
                               sun_cooldowns={'yellow':3.6, 'cyan':0})
        commands = []
        P._paint_sun_meter(commands, P.Rect(0, 0, 210, 300), 10, view,
                           P.R.find_entry('xigelika').sun_spirit, 1)
        texts = [command.text for command in commands if isinstance(command, TextCommand)]
        self.assertIn('4/4', texts)
        self.assertIn('3.6秒', texts)
        self.assertIn('就绪', texts)
        dots = [command.fill for command in commands if isinstance(command, EllipseCommand)]
        self.assertEqual(dots, [P.C.C_SUN_CYAN, P.C.C_SUN_YELLOW] * 2)

    def test_fight_card_explains_wall_charge_and_delayed_damage_without_extra_rows(self):
        commands = P._character_card(P.Layout().left_panel,
                                     P.CharacterView('left', 'xigelika', '西格莉卡'),
                                     picked=True, accent=P.C.C_SIDE_A, layout=P.Layout())
        texts = {command.text: command for command in commands if isinstance(command, TextCommand)}
        self.assertIn('同色×2 · 撞墙50%充能', texts)
        self.assertIn('1秒爆 · 黄28 / 青28+14', texts)
        rules = [texts['同色×2 · 撞墙50%充能'], texts['1秒爆 · 黄28 / 青28+14']]
        self.assertLessEqual(rules[0].rect.bottom, rules[1].rect.y)
        self.assertLess(rules[1].rect.bottom, texts['命中 0    受击 0'].rect.y)

    def test_fuse_never_explodes_from_visual_age_and_keeps_fixed_anchor(self):
        visual = P.SunSpiritVisual('left:1', 'yellow', (.7, .3), age=99)
        commands = self.paint(visual)
        sprites = [command for command in commands if isinstance(command, SpriteCommand)]
        self.assertEqual(sprites, [], 'the existing shared image effect owns summoned artwork')
        warning=next(command for command in commands if isinstance(command,EllipseCommand))
        self.assertAlmostEqual(warning.rect.center.x,350)
        self.assertAlmostEqual(warning.rect.center.y,150)
        self.assertFalse(any(getattr(command, 'z', 0) >= 12 for command in commands))

    def test_full_inventory_card_explains_the_next_energy_reduces_cooldowns(self):
        from unittest.mock import patch
        entry = P.R.find_entry('xigelika')
        full = P.CharacterView('left', 'xigelika', '西格莉卡',
                               sun_energy=('yellow', 'cyan', 'yellow', 'cyan'),
                               sun_cooldowns={'yellow': 3.8, 'cyan': .8})
        for reduction in (1, 2.5):
            custom = replace(entry, sun_spirit=replace(entry.sun_spirit,
                                                       overflow_cooldown_reduction=reduction))
            with patch.object(P.R, 'find_entry', return_value=custom):
                commands = P._character_card(P.Layout().left_panel, full, picked=True,
                                              accent=P.C.C_SIDE_A, layout=P.Layout())
            texts = {command.text: command for command in commands if isinstance(command, TextCommand)}
            hint = texts[f'满槽再获能 · 双冷却−{reduction:g}秒']
            damage = texts['1秒爆 · 黄28 / 青28+14']
            self.assertIn('4/4', texts)
            self.assertIn('3.8秒', texts)
            self.assertIn('0.8秒', texts)
            self.assertLessEqual(hint.rect.bottom, damage.rect.y)
            self.assertNotIn('同色×2 · 撞墙50%充能', texts)

    def test_second_cyan_blast_expands_its_range_and_cancelled_echo_has_no_warning(self):
        first = P.SunSpiritVisual('left:1', 'cyan', (.5,.5), age=1.3, stage=1, stage_age=.3)
        second = replace(first, stage=2)
        def blast_radius(commands):
            return max(command.rect.width for command in commands
                       if isinstance(command, EllipseCommand) and command.z == 12)
        self.assertAlmostEqual(blast_radius(self.paint(second)) / blast_radius(self.paint(first)), .27/.162)
        for visual,radius in ((first,.162),(second,.27)):
            for age in (.01,.3,.55):
                feedback=[command for command in self.paint(replace(visual,stage_age=age))
                          if isinstance(command,EllipseCommand) and command.z==12]
                self.assertEqual(len(feedback),1)
                self.assertAlmostEqual(feedback[0].rect.width,radius*500*2)
                self.assertIsNone(feedback[0].fill)
        self.assertFalse(any(getattr(command,'z',0)==13 for command in self.paint(second)))
        cancelled = self.paint(replace(first, cancelled=True))
        self.assertFalse(any(getattr(command, 'z', 0) == 7 for command in cancelled))
        self.assertTrue(any(getattr(command, 'z', 0) == 12 for command in cancelled))

    def test_animation_changes_continuously_and_is_repeatable(self):
        visual = P.SunSpiritVisual('left:1', 'cyan', (.4, .6), age=.4)
        first = self.paint(visual)
        self.assertEqual(first, self.paint(visual))
        self.assertNotEqual(first, self.paint(replace(visual, age=.4166667)))

    def test_visual_defaults_and_hud_read_the_rules_spec(self):
        spec=P.R.SunSpiritSpec()
        visual=P.SunSpiritVisual('qa','yellow',(.5,.5))
        self.assertEqual((visual.radius,visual.second_radius,visual.delay,visual.second_delay),
                         (spec.radius,spec.second_radius,spec.delay,spec.second_delay))
        entry=P.R.find_entry('xigelika')
        custom=replace(entry,sun_spirit=replace(spec,damage=35,second_damage=17,delay=1.2))
        roster=tuple(custom if item.character_id=='xigelika' else item for item in P.R.ROSTER)
        with patch.object(P.R,'ROSTER',roster):
            commands=P._character_card(P.Layout().left_panel,P.CharacterView('left','xigelika','西格莉卡'),
                                        picked=True,accent=P.C.C_SIDE_A,layout=P.Layout())
        texts=[command.text for command in commands if isinstance(command,TextCommand)]
        self.assertIn('1.2秒爆 · 黄35 / 青35+17',texts)


if __name__ == '__main__':
    unittest.main()
