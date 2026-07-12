from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config.game_user_stats as game_stats_module
import config.user_scale_config as scale_module
import config.user_settings as user_settings
from config.music import volume_config as volume_module


class UserStorageMigrationTests(unittest.TestCase):
    def test_scale_migrates_to_sparse_settings_without_rewriting_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings_path = root / "user" / "settings.json"
            shared_legacy = root / "legacy" / "user_scale.json"
            shared_legacy.parent.mkdir(parents=True)
            shared_legacy.write_text('{"user_scale": 1.4}', encoding="utf-8")

            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path), patch.object(
                scale_module, "get_user_settings_path", return_value=settings_path
            ), patch.object(scale_module, "get_shared_config_path", return_value=shared_legacy), patch.object(
                scale_module, "get_project_root", return_value=root / "project"
            ), patch.object(scale_module, "ensure_shared_config_ready"):
                manager = scale_module.UserScaleConfig()
                self.assertEqual(manager.get_scale(), 1.4)
                manager.set_scale(1.0)

            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("ui", payload["overrides"])
            self.assertEqual(json.loads(shared_legacy.read_text(encoding="utf-8"))["user_scale"], 1.4)

    def test_volume_uses_config_default_and_migrates_old_user_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings_path = root / "user" / "settings.json"
            shared_legacy = root / "legacy" / "music" / "volume.json"
            shared_legacy.parent.mkdir(parents=True)
            shared_legacy.write_text('{"volume": 0.14}', encoding="utf-8")

            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path), patch.object(
                volume_module, "get_user_settings_path", return_value=settings_path
            ), patch.object(volume_module, "get_shared_config_path", return_value=shared_legacy), patch.object(
                volume_module, "get_project_root", return_value=root / "project"
            ), patch.object(volume_module, "ensure_shared_config_ready"):
                manager = volume_module.VolumeConfig()
                self.assertEqual(manager.get_volume(), 0.14)

            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["overrides"]["audio"]["volume"], 0.14)
            self.assertEqual(volume_module.get_default_volume(), 0.3)

    def test_game_stats_writes_only_canonical_state_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            canonical = root / "user" / "state" / "games" / "lahai_tetris.json"
            shared_root = root / "shared"
            legacy = shared_root / "resc" / "user" / "games" / "lahai_tetris.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"best_score": 10}', encoding="utf-8")

            with patch.object(game_stats_module, "get_user_state_dir", return_value=canonical), patch.object(
                game_stats_module, "get_shared_root_dir", return_value=shared_root
            ), patch.object(game_stats_module, "get_project_root", return_value=root / "project"), patch.object(
                game_stats_module, "ensure_shared_config_ready"
            ):
                manager = game_stats_module.GameUserStats()
                self.assertEqual(manager.get_best_score(), 10)
                manager.update_best_score(20)

            self.assertEqual(json.loads(canonical.read_text(encoding="utf-8"))["best_score"], 20)
            self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["best_score"], 10)


if __name__ == "__main__":
    unittest.main()
