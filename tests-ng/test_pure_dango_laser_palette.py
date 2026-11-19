"""Blue/pink laser resource preparation preserves the original beam mask."""
from __future__ import annotations

import unittest
from pathlib import Path

from lib.core.render.visuals.image_loader import load_image_resource
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader, laser_palette

PACKAGE=Path(__file__).resolve().parents[1]/'lib/script/gemes/packages/official/dango_showdown'


class LaserPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=PACKAGE/'assets/effects/laser_beam.png'
        cls.original_bytes=cls.path.read_bytes()
        cls.original=load_image_resource(cls.path)
        cls.beam=DangoAssetLoader(PACKAGE/'assets',inline=True).assets()['laser']

    def test_decode_keeps_original_png_and_alpha_byte_for_byte(self):
        self.assertEqual(self.path.read_bytes(),self.original_bytes)
        self.assertNotEqual(self.beam.resource_id,self.original.resource_id)
        self.assertEqual(len(self.beam.frames),len(self.original.frames))
        for original,beam in zip(self.original.frames,self.beam.frames):
            self.assertEqual((beam.width,beam.height,beam.duration_ms),
                             (original.width,original.height,original.duration_ms))
            self.assertEqual(beam.pixels[3::4],original.pixels[3::4])
            self.assertNotEqual(beam.pixels,original.pixels)

    def test_beam_has_smooth_blue_and_pink_shoulders_and_a_hot_white_core(self):
        frame=self.beam.frames[0]
        def pixel(x,y):
            index=(y*frame.width+x)*4
            return tuple(frame.pixels[index:index+4])
        y=round(frame.height*.35)
        blue=pixel(round(frame.width*.18),y)
        pink=pixel(round(frame.width*.90),y)
        self.assertGreater(blue[2]-blue[0],80)
        self.assertGreater(pink[0]-pink[1],80)
        self.assertGreater(pink[2]-pink[1],40)
        core=pixel(round(frame.width*.18),frame.height//2)
        self.assertTrue(all(channel>=225 for channel in core[:3]),core)
        self.assertGreater(core[3],200)
        # Adjacent columns blend smoothly rather than becoming two flat strips.
        row=[pixel(x,y) for x in range(frame.width)]
        self.assertGreater(len(set(p[:3] for p in row)),100)
        self.assertLessEqual(max(abs(a-b) for p,q in zip(row,row[1:])
                                 for a,b in zip(p[:3],q[:3])),2)

    def test_stream_palette_uses_the_same_blue_to_pink_order(self):
        from lib.script.practical.star_streak_particle import StarStreakParticleScript
        colors=[laser_palette((index+.5)/14) for index in range(14)]
        self.assertGreater(colors[0][2]-colors[0][0],100)
        self.assertGreater(colors[-1][0]-colors[-1][1],100)
        self.assertEqual(len(set(colors)),14)
        script=StarStreakParticleScript()
        for color in (colors[0],colors[-1]):
            script.set_request_options({'count_range':(1,1),'color':color})
            particle=script.create_particles('point',(0,0))[0]
            self.assertEqual((particle.color.red,particle.color.green,particle.color.blue),color)


if __name__=='__main__':
    unittest.main()
