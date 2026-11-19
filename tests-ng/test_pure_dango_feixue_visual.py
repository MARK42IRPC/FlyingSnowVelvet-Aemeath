"""Frost HUD and immutable resource preparation without a GUI toolkit."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from lib.core.render.visuals.commands import RectCommand, TextCommand
from lib.core.render.visuals.image_loader import load_image_resource
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P, roster as R
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'lib/script/gemes/packages/official/dango_showdown'


class FrostVisualTests(unittest.TestCase):
    def card(self, **state):
        hp = R.find_entry('feixue').max_hp
        view = P.CharacterView('left', 'feixue', '绯雪', hp=hp, max_hp=hp, **state)
        return P._character_card(P.Layout().left_panel, view, picked=True,
                                 accent=P.C.C_SIDE_A, layout=P.Layout())

    def test_inventory_and_cooldown_show_live_model_state(self):
        for energy, elapsed, cooldown in ((0, 0, '3.0秒'), (3, 1.7, '1.3秒'), (4, 3, '就绪')):
            commands = self.card(frost_energy=energy, frost_elapsed=elapsed)
            texts = [item.text for item in commands if isinstance(item, TextCommand)]
            for text in ('霜渐能量', f'{energy}/4', '环斩冷却', cooldown,
                         '3秒环斩30 · 命中减速50%', '落空+1能量 · 碰撞耗1增伤2'):
                self.assertIn(text, texts)
            filled = [item for item in commands if isinstance(item, RectCommand)
                      and item.fill == P.C.C_SUN_CYAN and item.rect.height == 8]
            self.assertEqual(len(filled), energy)
            self.assertNotIn('虚空之手', texts)
            self.assertNotIn('日灵能量', texts)

    def test_skill_summary_reads_the_entry_instead_of_repeating_damage_constants(self):
        entry = R.find_entry('feixue')
        spec = replace(entry.frost_slash, interval=4, damage=23, slow_multiplier=.65,
                       contact_bonus=5, energy_per_contact=2)
        entries = tuple(replace(item, frost_slash=spec) if item.character_id == 'feixue' else item
                        for item in R.ROSTER)
        with patch.object(R, 'ROSTER', entries):
            texts = [item.text for item in self.card(frost_elapsed=1) if isinstance(item, TextCommand)]
        self.assertIn('3.0秒', texts)
        self.assertIn('4秒环斩23 · 命中减速35%', texts)
        self.assertIn('落空+1能量 · 碰撞耗2增伤10', texts)

    def test_slow_status_replaces_tag_without_moving_health_or_skill_rows(self):
        normal = self.card()
        slowed = self.card(slow_seconds=2.4, slow_multiplier=.5)
        normal_text = [item for item in normal if isinstance(item, TextCommand)]
        slowed_text = [item for item in slowed if isinstance(item, TextCommand)]
        before = next(item for item in normal_text if item.text == R.find_entry('feixue').tag)
        after = next(item for item in slowed_text if item.text == '减速50% · 2.4秒')
        self.assertEqual(before.rect, after.rect)
        for label in ('生命', '霜渐能量', '环斩冷却'):
            self.assertEqual(next(item.rect for item in normal_text if item.text == label),
                             next(item.rect for item in slowed_text if item.text == label))

    def test_original_blade_is_packaged_unchanged_and_only_alpha_margins_are_trimmed(self):
        filename = PACKAGE / 'assets/effects/feixue_blade.png'
        self.assertEqual(filename.read_bytes(), (ROOT / 'resc/GIF/绯雪刀.png').read_bytes())
        original = load_image_resource(filename).frames[0]
        loader = DangoAssetLoader(PACKAGE / 'assets', inline=True)
        self.assertFalse(loader.error)
        assets = loader.assets()
        blade = assets['feixue_blade'].frames[0]
        self.assertEqual((blade.width, blade.height), (946, 186))
        self.assertEqual(sum(original.pixels[3::4]), sum(blade.pixels[3::4]))
        pivot = assets['feixue_blade_pivot']
        self.assertTrue(0 < pivot[0] < .1 and .1 < pivot[1] < .4, pivot)
        for key in ('feixue_petal', 'feixue_petal_red'):
            frame = assets[key].frames[0]
            self.assertEqual((frame.width, frame.height), (32, 32))
            self.assertEqual(frame.pixels[3], 0)
            self.assertGreater(max(frame.pixels[3::4]), 200)
        crescent = assets['feixue_slash_trail'].frames[0]
        pixels = list(zip(crescent.pixels[0::4], crescent.pixels[1::4],
                          crescent.pixels[2::4], crescent.pixels[3::4]))
        self.assertGreater(sum(r > 230 and g > 245 and b > 245 and a > 180 for r, g, b, a in pixels), 200)
        self.assertGreater(sum(r < 220 and g > 180 and b > 220 and a > 80 for r, g, b, a in pixels), 200)
        self.assertEqual(pixels[128 * 256 + 128][3], 0, 'the bright crescent must leave the fighter uncovered')


if __name__ == '__main__':
    unittest.main()
