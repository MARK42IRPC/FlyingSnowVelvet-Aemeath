import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt5.QtGui import QColor

from lib.core.graphics.types import Color, FontSpec, coerce_color
from lib.core.qt_bridge.particle_system import ParticleOverlay, _to_qcolor, _to_qfont
from lib.script.practical.collision_particle import CollisionParticleScript
from lib.script.practical.snow_drift_particle import SnowDriftParticleScript


class ParticleColorContractTests(unittest.TestCase):
    def test_color_is_clamped_and_coerces_color_like_values(self):
        self.assertEqual(Color(-1, 128, 999, 300), Color(0, 128, 255, 255))
        self.assertEqual(coerce_color((12, 34, 56, 78)), Color(12, 34, 56, 78))
        self.assertEqual(
            Color(100, 80, 40).with_alpha(90),
            Color(100, 80, 40, 90),
        )

    def test_qt_backend_converts_core_color(self):
        converted = _to_qcolor(Color(12, 34, 56, 78))

        self.assertEqual(
            (converted.red(), converted.green(), converted.blue(), converted.alpha()),
            (12, 34, 56, 78),
        )

    def test_qt_backend_resolves_font_spec_and_measures_text_once(self):
        spec = FontSpec("Microsoft YaHei", 18, bold=True)
        font = _to_qfont(spec)
        self.assertEqual(font.family(), "Microsoft YaHei")
        self.assertEqual(font.pixelSize(), 18)
        self.assertTrue(font.bold())

        overlay = ParticleOverlay.__new__(ParticleOverlay)
        overlay._font_cache = {}
        particle = SimpleNamespace(is_text=True, font=spec, text="test")
        metrics = SimpleNamespace(
            horizontalAdvance=lambda _text: 42,
            height=lambda: 20,
            ascent=lambda: 15,
            descent=lambda: 5,
        )
        with patch(
            "lib.core.qt_bridge.particle_system.QFontMetrics",
            return_value=metrics,
        ):
            overlay._prepare_particle_backend_state(particle)

        self.assertEqual(particle._text_w, 42)
        self.assertEqual(particle._text_h, 20)
        self.assertEqual(particle._baseline_offset, 5)
        self.assertEqual(len(overlay._font_cache), 1)

    def test_core_lightness_adjustment_tracks_qcolor(self):
        samples = (
            Color(255, 134, 88),
            Color(117, 233, 255),
            Color(58, 92, 176),
        )
        for color in samples:
            qt_color = QColor(color.red, color.green, color.blue)
            for factor in (92, 110, 120, 132, 145, 150):
                expected = qt_color.lighter(factor)
                actual = color.lighter(factor)
                differences = (
                    abs(expected.red() - actual.red),
                    abs(expected.green() - actual.green),
                    abs(expected.blue() - actual.blue),
                )
                self.assertLessEqual(max(differences), 1)
            for factor in (110, 140, 200):
                expected = qt_color.darker(factor)
                actual = color.darker(factor)
                differences = (
                    abs(expected.red() - actual.red),
                    abs(expected.green() - actual.green),
                    abs(expected.blue() - actual.blue),
                )
                self.assertLessEqual(max(differences), 1)

    def test_particle_data_modules_do_not_import_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        roots = (
            repo_root / "lib/script/practical",
            repo_root / "lib/script/gemes/packages/official/lahai_tetris/extensions/particles",
        )
        violations = []
        for source_root in roots:
            for module_path in source_root.glob("*.py"):
                tree = ast.parse(module_path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if (node.module or "").startswith("PyQt5"):
                            violations.append(str(module_path.relative_to(repo_root)))
                    elif isinstance(node, ast.Import):
                        if any(alias.name.startswith("PyQt5") for alias in node.names):
                            violations.append(str(module_path.relative_to(repo_root)))

        self.assertEqual(violations, [])

    def test_particle_screen_bounds_use_core_rect(self):
        from lib.core.graphics.types import Rect

        with patch(
            "lib.script.practical.collision_particle.get_virtual_screen_rect",
            return_value=Rect(-1920, 0, 3840, 1080),
        ):
            particle = CollisionParticleScript().create_particles("point", (20, 30))[0]
        self.assertEqual((particle._screen_w, particle._screen_h), (3840.0, 1080.0))

        with patch(
            "lib.script.practical.snow_drift_particle.get_virtual_screen_rect",
            return_value=Rect(-1920, 0, 3840, 1080),
        ):
            snow = SnowDriftParticleScript().create_particles("point", (20, 30))[0]
        self.assertEqual(snow._ground_y, 1074.0)


if __name__ == "__main__":
    unittest.main()
