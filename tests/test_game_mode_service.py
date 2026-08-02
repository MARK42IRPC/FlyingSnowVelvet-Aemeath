from __future__ import annotations

import unittest
from unittest.mock import patch

import lib.script.app.game_mode_service as game_mode_module
from lib.script.app.game_mode_service import (
    GameModeService,
    get_game_mode_auto_companion_interval_override,
)
from lib.script.chat.handler_auto_companion import _get_effective_auto_companion_interval_ms


class _FakeTimingManager:
    def __init__(self, frame_fps: int = 120, gif_fps: int = 16) -> None:
        self.frame_fps = frame_fps
        self.configured_frame_fps = frame_fps
        self.gif_fps = gif_fps

    def get_frame_fps(self) -> int:
        return self.frame_fps

    def get_configured_frame_fps(self) -> int:
        return self.configured_frame_fps

    def get_gif_fps(self) -> int:
        return self.gif_fps

    def set_frame_fps(self, fps: int) -> None:
        self.frame_fps = int(fps)
        self.configured_frame_fps = int(fps)

    def set_gif_fps(self, fps: int) -> None:
        self.gif_fps = int(fps)


class _FakePet:
    def __init__(self, timing_manager=None) -> None:
        self._timing_manager = timing_manager or _FakeTimingManager()


class _FakeOverlay:
    def __init__(self) -> None:
        self.paused_states: list[bool] = []

    def set_paused(self, paused: bool) -> None:
        self.paused_states.append(bool(paused))


class _FakePhysicsWorld:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def pause(self) -> None:
        self.actions.append("pause")

    def resume(self) -> None:
        self.actions.append("resume")


class _FakeLayerManager:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def pause(self) -> None:
        self.actions.append("pause")

    def resume(self) -> None:
        self.actions.append("resume")


class GameModeServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        game_mode_module.cleanup_game_mode_service()

    def test_enable_and_disable_game_mode_updates_runtime_switches(self) -> None:
        physics_world = _FakePhysicsWorld()
        layer_manager = _FakeLayerManager()
        particles = _FakeOverlay()
        effects = _FakeOverlay()
        pet = _FakePet()

        with patch.object(game_mode_module, "get_physics_world", return_value=physics_world), patch.object(
            game_mode_module,
            "get_layer_manager",
            return_value=layer_manager,
        ):
            service = GameModeService()
            service.configure_runtime(pet, particles, effects)

            changed = service.set_enabled(True, source="tray_menu", notify=False)
            self.assertTrue(changed)
            self.assertTrue(service.is_enabled())
            self.assertEqual(pet._timing_manager.frame_fps, 30)
            self.assertEqual(pet._timing_manager.gif_fps, 16)
            self.assertEqual(physics_world.actions, ["pause"])
            self.assertEqual(layer_manager.actions, ["pause"])
            self.assertEqual(particles.paused_states, [True])
            self.assertEqual(effects.paused_states, [True])
            self.assertEqual(get_game_mode_auto_companion_interval_override(), (300000, 300000))

            changed = service.set_enabled(False, source="tray_menu", notify=False)
            self.assertTrue(changed)
            self.assertFalse(service.is_enabled())
            self.assertEqual(pet._timing_manager.frame_fps, 120)
            self.assertEqual(pet._timing_manager.gif_fps, 16)
            self.assertEqual(physics_world.actions, ["pause", "resume"])
            self.assertEqual(layer_manager.actions, ["pause", "resume"])
            self.assertEqual(particles.paused_states, [True, False])
            self.assertEqual(effects.paused_states, [True, False])
            self.assertIsNone(get_game_mode_auto_companion_interval_override())

            service.cleanup()

    def test_auto_companion_interval_uses_game_mode_override(self) -> None:
        self.assertGreaterEqual(_get_effective_auto_companion_interval_ms()[0], 120000)
        service = GameModeService()
        service.set_enabled(True, source="auto", notify=False)
        self.assertEqual(_get_effective_auto_companion_interval_ms(), (300000, 300000))
        service.cleanup()

    def test_auto_companion_interval_accepts_configured_minute_limits(self) -> None:
        from config.ollama_config import AUTO_COMPANION

        with patch.dict(AUTO_COMPANION, {"interval_ms": (60000, 60000)}):
            self.assertEqual(_get_effective_auto_companion_interval_ms(), (60000, 60000))
        with patch.dict(AUTO_COMPANION, {"interval_ms": (1200000, 1200000)}):
            self.assertEqual(_get_effective_auto_companion_interval_ms(), (1200000, 1200000))

    def test_restore_uses_configured_fps_when_runtime_is_temporarily_limited(self) -> None:
        timing = _FakeTimingManager()
        timing.frame_fps = 30
        pet = _FakePet(timing)

        with patch.object(game_mode_module, "get_physics_world", return_value=_FakePhysicsWorld()), patch.object(
            game_mode_module,
            "get_layer_manager",
            return_value=_FakeLayerManager(),
        ):
            service = GameModeService()
            service.configure_runtime(pet)
            service.set_enabled(True, notify=False)
            service.set_enabled(False, notify=False)

        self.assertEqual(timing.frame_fps, 120)
        service.cleanup()


if __name__ == "__main__":
    unittest.main()
