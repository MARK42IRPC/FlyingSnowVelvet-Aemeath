"""Rendered Dango acceptance checks with the bundled font and real Qt painter."""
from __future__ import annotations

import unittest
import hashlib
from dataclasses import replace
from pathlib import Path
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QFont, QFontMetricsF, QImage, QPainter
from PyQt5.QtWidgets import QApplication

from lib.core.render.backends.qt.runtime.font import init_font_config
from lib.core.render.qt_bridge import create_painter_host
from lib.core.render.visuals.commands import DrawBatch, TextCommand
from lib.core.render.visuals.resources import RasterFrame
from lib.core.render.visuals.image_loader import load_image_resource
from lib.core.render.visuals.commands import SpriteCommand, TransformPush
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import presenter as P
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase


class DangoVisualAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        init_font_config()
        cls.host = create_painter_host()
        frame = RasterFrame(8, 8, bytes((210, 80, 240, 255)) * 64, 100)
        cls.source = P.FrameSource("dango-visual-test", 1, frame)
        cls.views = (P.CharacterView("left", "amis", "爱弥斯", x=.3),
                     P.CharacterView("right", "daniya", "达妮娅", x=.7))
        cls.snapshot = P.ScreenSnapshot(phase=MatchPhase.FIGHT, characters=cls.views)

    def batch(self, snapshot=None, **kwargs):
        return P.paint_snapshot(snapshot or self.snapshot, 1080, 720, **kwargs)[0]

    def render(self, batch):
        image = QImage(1080, 720, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        self.host.render(batch, painter)
        painter.end()
        return image

    @staticmethod
    def pixels(image):
        return image.constBits().asstring(image.byteCount())

    @staticmethod
    def text_bounds(command):
        font = QFont(command.font.family)
        font.setPixelSize(round(command.font.pixel_size))
        font.setBold(command.font.bold)
        return QFontMetricsF(font).boundingRect(
            QRectF(command.rect.x, command.rect.y, command.rect.width, command.rect.height),
            int(command.alignment), command.text)

    def test_impact_flash_survives_loaded_sprite_and_hurt_tint(self):
        snapshot = replace(self.snapshot, characters=(replace(self.views[0], flash=.7), self.views[1]))
        args = {"sprites": {"amis": self.source, "daniya": self.source},
                "flashes": {"amis": self.source}}
        quiet = self.render(self.batch(snapshot, **args))
        impact = self.render(self.batch(snapshot, flash=1.0, **args))
        self.assertTrue(self.pixels(quiet) != self.pixels(impact), "impact must change the rendered arena")

    def test_positive_health_keeps_visible_red_strip_until_zero(self):
        empty, alive = [], []
        P._hp_bar(empty, P.Rect(30, 30, 200, 10), 0.0)
        P._hp_bar(alive, P.Rect(30, 30, 200, 10), .01)
        self.assertTrue(self.pixels(self.render(DrawBatch(tuple(empty)))) !=
                        self.pixels(self.render(DrawBatch(tuple(alive)))), "1 HP must differ from 0 HP")

    def test_title_and_subtitle_do_not_overlap_with_real_font(self):
        texts = {c.text: c for c in self.batch().commands if isinstance(c, TextCommand)}
        title = self.text_bounds(texts["团子大决战"])
        subtitle = self.text_bounds(texts["DANGO SHOWDOWN"])
        self.assertLessEqual(title.bottom(), subtitle.top())

    def test_large_record_values_do_not_overlap_labels(self):
        record = {"current_streak": 12345678, "best_streak": 12345678, "chips": -123456789}
        texts = [c for c in self.batch(replace(self.snapshot, record=record)).commands
                 if isinstance(c, TextCommand) and c.rect.y < 75]
        for label, value in (("连续押胜", "12345678"), ("最高", "12345678"), ("筹码", "-123456789")):
            label_command = next(c for c in texts if c.text == label)
            value_command = next(c for c in texts if c.text == value and abs(c.rect.x - label_command.rect.x) < 90)
            self.assertFalse(self.text_bounds(label_command).intersects(self.text_bounds(value_command)), label)

    def test_wall_hands_and_laser_do_not_paint_over_header_or_cards(self):
        quiet = self.render(self.batch())
        snapshot = self.snapshot
        from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffect
        from lib.core.render.visuals.resources import ImageResource
        from lib.core.render.visuals.visuals import build_effect_batch
        effects=[]
        arena=P.Layout().arena.rect
        for point,rotation in (((arena.x,arena.y),45),((arena.right,arena.bottom),275)):
            effect=SmoothImageShowEffect('hand',point,0,point,0,point,2,20)
            effect._visual_resource=ImageResource(self.source.resource_id,(self.source.frame,))
            effect.rotation=rotation
            effect.apply_frame_interpolation(1)
            effects.append(effect)
        active = self.render(self.batch(snapshot, feedback=build_effect_batch(effects)))
        arena = P.Layout().arena.rect
        outside = ((0, 0, 1080, int(arena.y)),
                   (0, int(arena.bottom) + 1, 1080, 720 - int(arena.bottom) - 1),
                   (0, int(arena.y), int(arena.x), int(arena.height)),
                   (int(arena.right) + 1, int(arena.y), 1080 - int(arena.right) - 1, int(arena.height)))
        for rect in outside:
            self.assertTrue(self.pixels(quiet.copy(*rect)) == self.pixels(active.copy(*rect)), rect)


class DangoLaserGradientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests_ng.support.qt import app
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader
        cls.app=app()
        root=Path(__file__).resolve().parents[1]/'lib/script/gemes/packages/official/dango_showdown/assets'
        cls.beam=DangoAssetLoader(root,inline=True).assets()['laser']
        cls.host=create_painter_host()

    def image(self,angle,point=(.5,.5),width=1080,height=720):
        import math
        from lib.core.services.feedback_scene import FeedbackScene
        from lib.core.render.visuals.commands import ClipPush,ClipPop
        from lib.script.effects.manager import get_effect_script_manager
        scene=FeedbackScene(effect_manager=get_effect_script_manager())
        arena=P.Layout().arena.rect
        center=(arena.x+point[0]*arena.width,arena.y+point[1]*arena.height)
        effect=scene.spawn_effect('smooth_image_show','point',center,{
            'intro_start_pos':center,'display_pos':center,'outro_end_pos':center,
            'intro_duration':0,'display_duration':1,'outro_duration':.12,
            'scale':arena.width*1.2/self.beam.size[0],
        },resource=self.beam)[0]
        effect.rotation=math.degrees(angle)
        effect.apply_frame_interpolation(1)
        batch=scene.batch()
        image=QImage(width,height,QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter=QPainter(image)
        painter.scale(width/1080,height/720)
        self.host.render(DrawBatch((ClipPush(arena),*batch.commands,ClipPop()),batch.resource_revisions),painter)
        painter.end()
        return image

    def test_real_shared_image_pixels_have_blue_pink_and_white_at_multiple_angles(self):
        for width,height in ((1080,720),(810,540)):
            for angle in (0,.65,1.9,3.7,5.3):
                image=self.image(angle,width=width,height=height).convertToFormat(QImage.Format_RGBA8888)
                data=image.constBits().asstring(image.byteCount())
                blue=pink=white=0
                for index in range(0,len(data),4):
                    r,g,b,a=data[index:index+4]
                    if a<40:
                        continue
                    blue+=b>r+25 and b>g+5
                    pink+=r>g+30 and b>g+20
                    white+=min(r,g,b)>205
                self.assertGreater(blue,100,(width,angle,'blue'))
                self.assertGreater(pink,100,(width,angle,'pink'))
                self.assertGreater(white,40,(width,angle,'white hot core'))

    def test_gradient_image_stays_clipped_at_every_corner_and_window_scale(self):
        arena=P.Layout().arena.rect
        for width,height in ((1080,720),(810,540)):
            scale=width/1080
            for point,angle in zip(((0,0),(1,0),(0,1),(1,1)),(.3,1.2,2.3,5.4)):
                image=self.image(angle,point,width,height)
                left,top=int(arena.x*scale),int(arena.y*scale)
                right,bottom=int(arena.right*scale)+1,int(arena.bottom*scale)+1
                for rect in ((0,0,width,top),(0,bottom,width,height-bottom),
                             (0,top,left,bottom-top),(right,top,width-right,bottom-top)):
                    outside=image.copy(*rect).convertToFormat(QImage.Format_RGBA8888)
                    data=outside.constBits().asstring(outside.byteCount())
                    self.assertFalse(any(data[3::4]),(width,point,angle,rect))


class DangoPortraitMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        path = Path(__file__).resolve().parents[1] / "lib/script/gemes/packages/official/dango_showdown/assets/dango/amis/idle.png"
        resource = load_image_resource(path, resource_id="motion-portrait")
        cls.source = P.FrameSource(resource.resource_id, 1, resource.frames[0])

    def portrait(self, clock, scale, host):
        commands = []
        P._paint_fighter_plate(commands, P.HitMap(), P.Rect(20, 86, 312, 260), "left", "1P",
            P.R.ROSTER[0], P.C.C_SIDE_A, True, {"amis": self.source}, clock=clock)
        index = next(i for i, c in enumerate(commands) if isinstance(c, SpriteCommand))
        self.assertIsInstance(commands[index - 1], TransformPush)
        image = QImage(360, 360, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.scale(scale, scale)
        host.render(DrawBatch(tuple(commands[index - 1:index + 2])), painter)
        painter.end()
        return image.constBits().asstring(image.byteCount())

    def test_real_portrait_changes_smoothly_at_both_window_scales(self):
        for scale in (1.0, .75):
            host = create_painter_host()
            hashes = [hashlib.sha256(self.portrait(i / 60.0, scale, host)).digest() for i in range(240)]
            run = longest = 1
            for previous, current in zip(hashes, hashes[1:]):
                run = run + 1 if current == previous else 1
                longest = max(longest, run)
            self.assertGreaterEqual(len(set(hashes)), 220, scale)
            self.assertLessEqual(longest, 2, scale)
            self.assertEqual(len(host._draw_backend._render_pixmap_cache), 1)

    def test_frozen_presentation_clock_keeps_the_exact_same_pixels(self):
        host = create_painter_host()
        before = self.portrait(.63, 1.0, host)
        self.assertTrue(before == self.portrait(.63, 1.0, host))

