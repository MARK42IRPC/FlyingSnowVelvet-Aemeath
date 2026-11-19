"""Real Qt rendering and lifecycle checks for delayed sun spirits."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tests_ng.support.qt import app
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetricsF, QImage, QPainter
from lib.core.render.qt_bridge import create_painter_host
from lib.core.render.visuals.commands import TextCommand
from lib.script.gemes.MAIN.game_packages import GameContext, GamePackageManifest
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P, events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.widget import DangoShowdownWidget

PACKAGE = Path(__file__).resolve().parents[1] / 'lib/script/gemes/packages/official/dango_showdown'


class SunSpiritQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        from lib.core.render.backends.qt.runtime.font import init_font_config
        init_font_config()
        assets = DangoAssetLoader(PACKAGE / 'assets', inline=True).assets()
        cls.sources = {key: P.FrameSource(resource.resource_id, 1, resource.frames[0])
                       for key, resource in assets['sun_spirits'].items()}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dango-sun-qt-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        manifest = GamePackageManifest.from_dict(json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8')))
        self.widget = DangoShowdownWidget(GameContext(manifest, PACKAGE, root/'data', root/'cache'))
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.shutdown)
        self.widget._timer.stop()
        self.widget._apply_assets(blocking=True)
        self.widget._sfx = Mock()
        self.widget._event_center = Mock()
        self.widget._match.phase = P.MatchPhase.FIGHT
        self.payload = dict(side='left',target='right',color='cyan',point=(.7,.3),radius=.162,
                            delay=1,second_delay=.45,second_radius=.27,summon_id='left:1')

    def render(self, snapshot, width=1080, height=720):
        batch, _ = P.paint_snapshot(snapshot, 1080, 720, feedback=self.widget._feedback.batch())
        image = QImage(width,height,QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.scale(width/1080,height/720)
        create_painter_host().render(batch,painter)
        painter.end()
        return image

    @staticmethod
    def pixels(image):
        return image.constBits().asstring(image.byteCount())

    def test_real_effects_at_every_stage_are_clipped_at_all_four_arena_corners(self):
        snapshot = P.ScreenSnapshot(phase=P.MatchPhase.FIGHT)
        arena = P.Layout().arena.rect
        for width,height in ((1080,720),(810,540)):
            quiet = self.render(snapshot,width,height)
            scale=width/1080
            for point in ((0,0),(1,0),(0,1),(1,1)):
                for stage in (0,1,2):
                    self.widget._feedback.clear()
                    self.widget._sun_spirit_fx.clear()
                    self.widget._handle_skill_event(ev.SUN_SPIRIT_SUMMON,dict(self.payload,point=point))
                    if stage:
                        self.widget._handle_skill_event(ev.SUN_SPIRIT_BURST,dict(self.payload,point=point,stage=stage,damage=0,hit=False))
                    self.widget._feedback.advance(.13)
                    spirit=P.SunSpiritVisual('qa','cyan',point,age=.7,stage=stage,stage_age=.13)
                    active=self.render(replace(snapshot,sun_spirits=(spirit,)),width,height)
                    self.assertNotEqual(self.pixels(quiet), self.pixels(active))
                    for rect in ((0,0,width,int(arena.y*scale)),
                                 (0,int(arena.bottom*scale)+1,width,height-int(arena.bottom*scale)-1),
                                 (0,0,int(arena.x*scale),height),
                                 (int(arena.right*scale)+1,0,width-int(arena.right*scale)-1,height)):
                        self.assertEqual(self.pixels(quiet.copy(*rect)),self.pixels(active.copy(*rect)),
                                         (width,point,stage,rect))

    def test_hud_real_font_has_room_for_inventory_clocks_and_rule_lines(self):
        view=P.CharacterView('left','xigelika','西格莉卡',sun_energy=('yellow','cyan'),
                             sun_cooldowns={'yellow':4.9,'cyan':.1})
        commands=P._character_card(P.Layout().left_panel,view,picked=True,accent=P.C.C_SIDE_A,layout=P.Layout())
        for command in commands:
            if not isinstance(command,TextCommand):
                continue
            if command.text not in {'日灵能量','黄日灵','青日灵','2/4','4.9秒','0.1秒',
                                     '同色×2 · 撞墙50%充能','1秒爆 · 黄28 / 青28+14'}:
                continue
            font=QFont(command.font.family)
            font.setPixelSize(round(command.font.pixel_size))
            font.setBold(command.font.bold)
            metrics=QFontMetricsF(font)
            self.assertLessEqual(metrics.horizontalAdvance(command.text),command.rect.width+2,command.text)
            self.assertLessEqual(metrics.height(),command.rect.height+2,command.text)

    def test_only_burst_events_advance_stage_and_single_damage_number_is_emitted(self):
        self.widget._handle_skill_event(ev.SUN_SPIRIT_SUMMON,self.payload)
        self.widget._clock=2.0
        self.assertEqual(self.widget._snapshot().sun_spirits[0].stage,0)
        self.widget._show_damage_number=Mock()
        self.widget._handle_skill_event(ev.SKILL_DAMAGE,dict(skill='sun_spirit_cyan',damage=28,target='right',hp_left=100,hp_right=72))
        self.widget._handle_skill_event(ev.SUN_SPIRIT_BURST,dict(self.payload,stage=1,damage=28,hit=True))
        self.widget._show_damage_number.assert_called_once()
        self.assertEqual(self.widget._snapshot().sun_spirits[0].stage,1)
        self.widget._clock=4.0
        self.assertEqual(self.widget._snapshot().sun_spirits[0].stage,1)
        self.widget._handle_skill_event(ev.SUN_SPIRIT_BURST,dict(self.payload,stage=2,damage=0,hit=False))
        self.widget._show_damage_number.assert_called_once()
        self.assertEqual(self.widget._snapshot().sun_spirits[0].stage,2)
        self.widget._clock+=1
        self.assertFalse(self.widget._snapshot().sun_spirits)

    def test_sun_art_and_colored_sparks_are_existing_shared_instances(self):
        self.widget._handle_skill_event(ev.SUN_SPIRIT_SUMMON,dict(self.payload,color='yellow'))
        images=self.widget._feedback.effects
        self.assertEqual(len(images),1)
        self.assertEqual(type(images[0]).__name__,'SmoothImageShowEffect')
        sparks=self.widget._feedback.particles
        self.assertTrue(sparks)
        self.assertTrue(all(type(p).__name__=='StarStreakParticle' for p in sparks))
        self.assertTrue(all((p.color.red,p.color.green,p.color.blue)==(255,215,92) for p in sparks))
        self.widget._event_center.publish.assert_not_called()

    def test_pause_hidden_and_new_round_do_not_leak_or_advance_the_effect(self):
        self.widget._handle_skill_event(ev.SUN_SPIRIT_SUMMON,self.payload)
        self.assertTrue(self.widget._feedback.effects)
        self.widget._paused=True
        instances=self.widget._feedback.effects+self.widget._feedback.particles
        ages=tuple(getattr(item,'age',getattr(item,'life',0)) for item in instances)
        before=self.widget._snapshot().sun_spirits
        with patch('time.perf_counter',return_value=self.widget._last_tick+.1):
            self.widget._on_tick()
        self.assertEqual(self.widget._snapshot().sun_spirits,before)
        self.assertEqual(ages,tuple(getattr(item,'age',getattr(item,'life',0)) for item in instances))
        self.widget.hide()
        self.assertFalse(self.widget._timer.isActive())
        self.widget._reset_presentation()
        self.assertFalse(self.widget._snapshot().sun_spirits)
        self.assertFalse(self.widget._feedback.effects)
        self.assertFalse(self.widget._feedback.particles)

    def test_ko_removes_unfired_fuse_and_finishes_a_cyan_first_blast_without_echo(self):
        self.widget._handle_skill_event(ev.SUN_SPIRIT_SUMMON,self.payload)
        self.widget._match.phase=P.MatchPhase.RESULT
        self.assertFalse(self.widget._snapshot().sun_spirits)
        self.widget._match.phase=P.MatchPhase.FIGHT
        self.widget._handle_skill_event(ev.SUN_SPIRIT_SUMMON,self.payload)
        self.widget._handle_skill_event(ev.SUN_SPIRIT_BURST,dict(self.payload,stage=1,damage=28,hit=True))
        self.widget._match.phase=P.MatchPhase.RESULT
        self.assertTrue(self.widget._snapshot().sun_spirits[0].cancelled)
        self.widget._clock+=1
        self.assertFalse(self.widget._snapshot().sun_spirits)


if __name__ == '__main__':
    unittest.main()
