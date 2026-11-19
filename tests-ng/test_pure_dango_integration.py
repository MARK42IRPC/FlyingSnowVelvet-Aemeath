"""Exercise the real Dango release ZIP, package service and extension seams."""

from __future__ import annotations

import io
import os
import random
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

from lib.script.gemes.MAIN import game_packages
from lib.script.gemes.MAIN.game_sfx import GameSfx
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import events as ev
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.assets import DangoAssetLoader
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.engine import DangoMatch
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.sfx import CUE_MAP, VOLUMES, DangoSfx
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats
from scripts.release_common import build_generated_payloads


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "lib/script/gemes/packages/official/dango_showdown"
PAYLOAD = Path("gamepack/official/dango_showdown.zip")


class DangoPackageRoundTripTests(unittest.TestCase):
    def test_release_zip_installs_exports_and_preserves_saved_data(self):
        from lib.script.effects.manager import get_effect_script_manager

        generated = build_generated_payloads(ROOT)
        self.assertIn(PAYLOAD, generated)
        with zipfile.ZipFile(io.BytesIO(generated[PAYLOAD])) as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("code/dango_showdown_pkg/entry.py", names)
            self.assertIn("assets/effects/amis_starburst.gif", names)
            for color in ("yellow", "cyan"):
                name = f"assets/effects/sun_spirit_{color}.png"
                self.assertIn(name, names)
                self.assertEqual(archive.read(name), (PACKAGE / name).read_bytes())
            self.assertFalse(any(name.startswith("extensions/") for name in names))
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

        with tempfile.TemporaryDirectory(prefix="dango_integration_") as tmp:
            home = Path(tmp)
            official_zips = home / "official"
            official_zips.mkdir()
            (official_zips / "dango_showdown.zip").write_bytes(generated[PAYLOAD])
            with (
                patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": str(home)}),
                patch.object(game_packages, "ensure_shared_config_ready"),
                patch.object(game_packages, "_official_gamepack_root", return_value=official_zips),
                patch.object(game_packages, "_official_source_root", return_value=home / "no_sources"),
            ):
                service = game_packages.GamePackageService()
                try:
                    installed = service.get_installed_game("dango_showdown")
                    self.assertIsNotNone(installed)
                    self.assertEqual(installed.source, "official")
                    self.assertEqual(installed.manifest.command_aliases, ("团子大决战",))
                    self.assertEqual(installed.manifest.particle_extensions, ())
                    self.assertEqual(installed.manifest.effect_extensions, ())
                    self.assertEqual(service._package_signature(installed.install_dir), service._package_signature(PACKAGE))
                    loader = DangoAssetLoader(installed.assets_root, inline=True)
                    self.assertEqual(loader.error, "")
                    spirits = loader.assets()["sun_spirits"]
                    self.assertEqual(set(spirits), {"yellow", "cyan"})
                    for color, spirit in spirits.items():
                        self.assertIsNotNone(spirit, color)
                        frame = spirit.frames[0]
                        self.assertGreater(frame.width, 0)
                        self.assertGreater(frame.height, 0)
                        self.assertTrue(any(frame.pixels[3::4]), color)
                        self.assertIn(0, frame.pixels[3::4], color)
                    manager = get_effect_script_manager()
                    self.assertFalse(manager.has_effect("dango_showdown.laser_beam"))
                    self.assertTrue(manager.has_effect("smooth_image_show"))
                    script = manager.get_script("smooth_image_show")
                    resource = installed.assets_root / "effects/void_hand.png"
                    point = (220, 65)
                    effect = script.create_effects("point", point, {
                        "resource_path": str(resource), "intro_start_pos": point,
                        "display_pos": point, "outro_end_pos": point,
                        "display_duration": 0.3, "scale": 0.25,
                    })[0]
                    self.assertTrue(effect.alive)
                    self.assertAlmostEqual(effect.x, 220)
                    self.assertLessEqual(1024 * effect.scale, 400)

                    save_path = installed.data_root / "state.json"
                    stats = DangoStats(save_path)
                    starting_chips = stats.snapshot()["chips"]
                    stats.settle(bet_side="left", winner="left", double_ko=False, amount=5)
                    saved = save_path.read_bytes()
                    exported = service.export_game_zip("dango_showdown", home / "export.zip")
                    _, signature = service._inspect_zip_package(exported)
                    self.assertEqual(signature, service._package_signature(PACKAGE))
                    service.uninstall_game("dango_showdown")
                    self.assertIsNone(service.get_installed_game("dango_showdown"))
                    self.assertFalse(manager.has_effect("dango_showdown.laser_beam"))
                    self.assertTrue(manager.has_effect("smooth_image_show"))
                    self.assertEqual(save_path.read_bytes(), saved)
                    restored = service.install_from_zip(exported)
                    self.assertEqual(restored.game_id, "dango_showdown")
                    self.assertFalse(manager.has_effect("dango_showdown.laser_beam"))
                    self.assertTrue(manager.has_effect("smooth_image_show"))
                    self.assertEqual(DangoStats(save_path).snapshot()["chips"], starting_chips + 5)
                    self.assertEqual(save_path.read_bytes(), saved)
                finally:
                    for game_id in list(service._registered_extensions):
                        service._unregister_game_extensions(game_id)


class DangoSoundIntegrationTests(unittest.TestCase):
    def test_every_cue_reuses_host_cached_pcm_and_keeps_its_event_volume(self):
        emitted = []
        with tempfile.TemporaryDirectory(prefix="dango_sfx_") as tmp:
            sound = DangoSfx(Path(tmp), lambda *args: emitted.append(args))
            shared = GameSfx(cache_dir=Path(tmp), emit=lambda *args: None)
            for key, host_cue in CUE_MAP.items():
                with self.subTest(cue=key):
                    sound.play(key)
                    emitted_cue, path, volume = emitted[-1]
                    self.assertEqual(emitted_cue, host_cue)
                    self.assertEqual(volume, VOLUMES[key])
                    self.assertEqual(path, shared._files[host_cue])
                    self.assertEqual(Path(path).name, f"lahai_{host_cue}.wav")
                    with wave.open(str(path), "rb") as wav:
                        self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
                        self.assertGreater(wav.getnframes(), 0)
                        pcm = wav.readframes(wav.getnframes())
                    self.assertTrue(any(pcm))
            self.assertFalse(list(Path(tmp).glob("dango_*.wav")))
            self.assertEqual(len(emitted), len(CUE_MAP))
            sound.play("unknown")
            self.assertEqual(len(emitted), len(CUE_MAP))

    def test_collision_speed_selects_the_existing_light_or_heavy_host_cue(self):
        emitted = []
        with tempfile.TemporaryDirectory(prefix="dango_sfx_") as tmp:
            sound = DangoSfx(Path(tmp), lambda *args: emitted.append(args))
            sound.hit(0.449)
            sound.hit(0.45)
        self.assertEqual([(cue, volume) for cue, _, volume in emitted],
                         [("drop_start", VOLUMES["hit_light"]), ("drop_impact", VOLUMES["hit_heavy"])])


class DangoExtensionLifecycleTests(unittest.TestCase):
    def test_match_end_hook_receives_same_match_and_final_result_once(self):
        calls = []
        events = []

        class Hook:
            def on_match_start(self, match):
                calls.append(("start", match))

            def on_match_end(self, match, result):
                calls.append(("end", match, result))

        class BrokenHook:
            def on_match_end(self, match, result):
                raise RuntimeError("an external hook is broken")

        bus = ev.ExtensionBus(publish=lambda name, payload: events.append((name, payload)))
        bus.add_hook(BrokenHook())
        bus.add_hook(Hook())
        with tempfile.TemporaryDirectory(prefix="dango_hooks_") as tmp:
            match = DangoMatch(DangoStats(Path(tmp) / "state.json"), bus=bus, rng=random.Random(7))
            match.prepare_round()
            self.assertTrue(match.choose_dango("left"))
            self.assertTrue(match.choose_bet_amount(5))
            self.assertTrue(match.start_match())
            match.countdown = 0
            # A lethal skill is an actual engine path, including settlement.
            match._skill_damage(match.left, match.right, match.right.hp)
            match.update(1 / 60)
            self.assertEqual(match.phase, MatchPhase.RESULT)
            self.assertEqual(calls, [("start", match), ("end", match, match.result)])
            for _ in range(10):
                match.update(0.1)
            self.assertEqual(len([call for call in calls if call[0] == "end"]), 1)
            self.assertEqual(len([name for name, _ in events if name == ev.MATCH_END]), 1)
            self.assertEqual(match.stats.snapshot()["total_rounds"], 1)


if __name__ == "__main__":
    unittest.main()
