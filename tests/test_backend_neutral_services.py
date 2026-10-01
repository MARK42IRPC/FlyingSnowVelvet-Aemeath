"""Boundary: product physics numbers are resolved in exactly one place.

Business arithmetic must live outside the backends, in ``lib/core/services``.
The world-object simulation was the largest remaining duplication: the Qt
widgets in ``lib/script/ui/world_objects`` and the DirectX simulation in
``lib/core/render/backends/dx/world_object_backend`` each read ``PHYSICS`` / ``MORTOR`` /
``SNOWBALL`` / ``SNOW_LEOPARD`` / ``BEHAVIOR`` on their own, so one copy could
silently drift from the other.

These tests pin the contract from the outside:

* the resolver reflects live config, so it is a source of truth and not a frozen
  copy taken at import time;
* the DirectX backend no longer reads the physics config itself;
* the Qt world-object pages no longer read the physics config themselves;
* the resolver stays importable with PyQt blocked.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.core.services.world_object_physics import (
    CLOCK_UP_FORCE_MULTIPLIER,
    FADE_TICK_MS,
    WorldObjectPhysics,
    resolve_world_object_physics,
)
from lib.core.services.ui_presentation import (
    DEFAULT_OVERLAY_HIDE_LINGER_MS,
    DEFAULT_UI_FADE_DURATION_MS,
    overlay_hide_linger_ms,
    ui_fade_duration_ms,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

PHYSICS_CONFIG_NAMES = frozenset({
    "PHYSICS",
    "MORTOR",
    "SNOWBALL",
    "SNOW_LEOPARD",
    "BEHAVIOR",
})

QT_WORLD_OBJECTS = REPO_ROOT / "lib" / "script" / "ui" / "world_objects"
DX_WORLD_OBJECT = (
    REPO_ROOT / "lib" / "core" / "render" / "backends" / "dx" / "world_object_backend.py"
)


def _ui() -> dict:
    from config.config import UI

    return UI


def _particles() -> dict:
    from config.config import PARTICLES

    return PARTICLES


def _physics_config_imports(path: Path) -> set[str]:
    """Names imported from a ``config`` module that describe product motion."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None or not node.module.startswith("config"):
            continue
        for alias in node.names:
            if alias.name in PHYSICS_CONFIG_NAMES:
                found.add(alias.name)
    return found


class WorldObjectPhysicsResolverTests(unittest.TestCase):
    def test_resolver_exposes_the_documented_defaults_shape(self):
        physics = resolve_world_object_physics()

        self.assertIsInstance(physics, WorldObjectPhysics)
        self.assertGreater(physics.max_throw_vx, 0.0)
        self.assertGreater(physics.max_throw_vy, 0.0)
        self.assertGreaterEqual(physics.drag_threshold, 0)
        self.assertGreaterEqual(physics.max_bounces, 0)
        self.assertGreater(physics.fade_step, 0.0)
        self.assertGreater(physics.fade_interval_ms, 0)
        self.assertGreaterEqual(physics.motor_jump_max_charges, 1)
        self.assertGreaterEqual(physics.double_click_ticks, 1)

    def test_ground_fraction_is_not_scaled_as_pixels(self):
        """``ground_y_pct`` is a screen fraction and must stay a fraction."""
        physics = resolve_world_object_physics()

        self.assertGreater(physics.ground_y_pct, 0.0)
        self.assertLessEqual(physics.ground_y_pct, 1.0)

    def test_derived_values_match_their_inputs(self):
        physics = resolve_world_object_physics()

        self.assertEqual(
            physics.fade_tick_stride,
            max(1, int(round(physics.fade_interval_ms / FADE_TICK_MS))),
        )

        self.assertEqual(
            physics.clock_up_force_vy,
            physics.snow_leopard_jump_vy * CLOCK_UP_FORCE_MULTIPLIER,
        )

        self.assertEqual(
            physics.snow_leopard_flip_interval_seconds,
            (
                physics.flip_interval_min_ms / 1000.0,
                physics.flip_interval_max_ms / 1000.0,
            ),
        )

    def test_clamp_throw_applies_both_limits(self):
        physics = resolve_world_object_physics()

        self.assertEqual(
            physics.clamp_throw(1000000.0, -1000000.0),
            (physics.max_throw_vx, -physics.max_throw_vy),
        )

        self.assertEqual(physics.clamp_throw(1.0, 2.0), (1.0, 2.0))

    def test_resolver_reads_live_config_rather_than_a_frozen_copy(self):
        """A user override must reach both backends on the next resolution."""
        import config.config as config_module

        original = config_module.PHYSICS.get("max_throw_vx")
        config_module.PHYSICS["max_throw_vx"] = 4321.0
        try:
            self.assertEqual(resolve_world_object_physics().max_throw_vx, 4321.0)
        finally:
            if original is None:
                config_module.PHYSICS.pop("max_throw_vx", None)
            else:
                config_module.PHYSICS["max_throw_vx"] = original

    def test_unusable_override_falls_back_instead_of_raising(self):
        """A malformed override must not depend on which backend loads first."""
        import config.config as config_module

        original = config_module.MORTOR.get("jump_max_charges")
        config_module.MORTOR["jump_max_charges"] = "not-a-number"
        try:
            physics = resolve_world_object_physics()
            self.assertGreaterEqual(physics.motor_jump_max_charges, 1)
        finally:
            if original is None:
                config_module.MORTOR.pop("jump_max_charges", None)
            else:
                config_module.MORTOR["jump_max_charges"] = original


class WorldObjectPhysicsBoundaryTests(unittest.TestCase):
    def test_ui_fade_duration_has_one_default_for_both_bridges(self):
        """The two bridges used to fall back to 180 and 200 respectively."""
        self.assertEqual(ui_fade_duration_ms(), int(_ui()["ui_fade_duration"]))
        self.assertEqual(ui_fade_duration_ms("250"), 250)
        self.assertEqual(ui_fade_duration_ms("bad"), DEFAULT_UI_FADE_DURATION_MS)
        self.assertEqual(ui_fade_duration_ms(-5), 0)

    def test_overlay_linger_has_one_default_for_both_bridges(self):
        self.assertEqual(
            overlay_hide_linger_ms(), int(_particles()["overlay_hide_linger_ms"])
        )

        self.assertEqual(overlay_hide_linger_ms(0), 0)
        self.assertEqual(overlay_hide_linger_ms("250"), 250)
        self.assertEqual(
            overlay_hide_linger_ms("bad"), DEFAULT_OVERLAY_HIDE_LINGER_MS
        )

    def test_qt_overlay_policy_delegates_to_the_service(self):
        """The Qt module keeps the mechanism and re-exports the shared numbers."""
        from lib.core.render.backends.qt.runtime import overlay_policy
        from lib.core.services import ui_presentation

        self.assertEqual(
            overlay_policy.DEFAULT_HIDE_LINGER_MS,
            ui_presentation.DEFAULT_OVERLAY_HIDE_LINGER_MS,
        )
        self.assertEqual(
            overlay_policy.resolve_hide_linger_ms(), overlay_hide_linger_ms()
        )
        self.assertEqual(overlay_policy.resolve_hide_linger_ms(7), 7)

    def test_directx_world_object_backend_does_not_read_physics_config(self):
        found = _physics_config_imports(DX_WORLD_OBJECT)

        self.assertEqual(
            found,
            set(),
            "DX world objects must use lib/core/services, not %s" % sorted(found),
        )

    def test_qt_world_object_pages_do_not_read_physics_config(self):
        offenders = {}
        for path in sorted(QT_WORLD_OBJECTS.glob("*.py")):
            found = _physics_config_imports(path)
            if found:
                offenders[path.name] = sorted(found)
        self.assertEqual(
            offenders,
            {},
            "Qt world objects must read motion values from lib/core/services",
        )

    def test_both_backends_share_the_resolved_values(self):
        """The DX module constants must be the service values, not local reads."""
        from lib.core.render.backends.dx import world_object_backend as dx

        physics = resolve_world_object_physics()

        self.assertEqual(dx._MAX_THROW_VX, physics.max_throw_vx)
        self.assertEqual(dx._MAX_THROW_VY, physics.max_throw_vy)
        self.assertEqual(dx._MAX_BOUNCES, physics.max_bounces)
        self.assertEqual(dx._GROUND_Y_PCT, physics.ground_y_pct)
        self.assertEqual(dx._FADE_STEP, physics.fade_step)
        self.assertEqual(dx._FADE_TICK_STRIDE, physics.fade_tick_stride)
        self.assertEqual(dx._MOTOR_BASE_SPEED, physics.motor_move_speed_px_per_frame)
        self.assertEqual(dx._MOTOR_MAX_SPEED, physics.motor_move_speed_max)
        self.assertEqual(dx._MOTOR_JUMP_VY, physics.motor_jump_vy)
        self.assertEqual(dx._MOTOR_JUMP_MAX_CHARGES, physics.motor_jump_max_charges)
        self.assertEqual(
            dx._SNOW_LEOPARD_FLIP_INTERVAL_SECONDS,
            physics.snow_leopard_flip_interval_seconds,
        )

    def test_resolver_imports_without_pyqt(self):
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.services.world_object_physics import (
                resolve_world_object_physics,
            )

            physics = resolve_world_object_physics()
            assert physics.max_throw_vx > 0.0
            assert physics.ground_y_pct <= 1.0
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_music_player_contract_lives_in_services(self):
        """音乐播放器的契约是后端中立的，两个后端都注入同一份接口。"""
        from lib.core.services.music_playback import MusicPlayerProtocol

        for name in (
            "set_callbacks",
            "play",
            "pause",
            "resume",
            "stop",
            "set_volume",
            "seek",
            "is_busy",
            "position_ms",
            "duration_ms",
            "cleanup",
        ):
            self.assertTrue(
                callable(getattr(MusicPlayerProtocol, name, None)),
                f"MusicPlayerProtocol 缺少 {name}",
            )

    def test_both_backends_inject_a_player_implementing_the_contract(self):
        """Qt 注入 QtMusicPlayer，DX 注入 MciMusicPlayer，都要满足同一契约。"""
        from lib.core.services.music_playback import MusicPlayerProtocol

        required = (
            "set_callbacks",
            "play",
            "pause",
            "resume",
            "stop",
            "set_volume",
            "seek",
            "is_busy",
            "position_ms",
            "duration_ms",
            "cleanup",
        )

        from lib.script.cloudmusic._player import MciMusicPlayer

        mci = MciMusicPlayer()
        for name in required:
            self.assertTrue(
                callable(getattr(mci, name, None)),
                f"MCI 缺少 {name}",
            )

        for name in required:
            self.assertIn(name, dir(MusicPlayerProtocol))

    def test_music_player_contract_imports_without_pyqt(self):
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.services.music_playback import MusicPlayerProtocol
            from lib.script.cloudmusic._player import MciMusicPlayer

            assert callable(getattr(MusicPlayerProtocol, "set_callbacks", None))
            player = MciMusicPlayer()
            seen = []
            player.set_callbacks(on_started=lambda gen: seen.append(gen))
            player.set_callbacks()
            assert seen == []
            player.cleanup()
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cloudmusic_manager_drives_one_player_without_qt_signals(self):
        """"没有注入" 已不再意味着"没有播放器"：管理器总会拿到一个播放器。"""
        manager_source = (
            REPO_ROOT / "lib" / "script" / "cloudmusic" / "manager.py"
        ).read_text(encoding="utf-8-sig")

        self.assertIn("MciMusicPlayer", manager_source)
        self.assertNotIn("_fallback_player", manager_source)
        for signal_name in (
            "play_requested",
            "pause_requested",
            "resume_requested",
            "stop_requested",
            "volume_requested",
            "seek_requested",
            "duration_ms_value",
        ):
            self.assertNotIn(signal_name, manager_source)

    def test_services_package_stays_out_of_script_layer(self):
        """``lib/core`` may not reach back into ``lib/script``."""
        offenders = []
        for path in sorted((REPO_ROOT / "lib" / "core" / "services").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name == "lib.script" or name.startswith("lib.script."):
                        offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
