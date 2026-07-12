from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.gemes.MAIN import game_sfx
from lib.script.cloudmusic import _constants as cloud_constants


class GameSfxStorageTests(unittest.TestCase):
    def test_drop_impact_generator_has_heavy_duration(self):
        instance = game_sfx.GameSfx.__new__(game_sfx.GameSfx)
        frames = instance._build_drop_impact()
        self.assertEqual(len(frames), int(game_sfx.GameSfx._RATE * 0.160) * 2)

    def test_music_cache_migration_leaves_non_music_game_sfx_in_place(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            legacy_user_cache = root / "legacy-user-cache"
            legacy_music_cache = root / "legacy-music-cache"
            target_cache = root / "cache" / "music"
            user_data = root / "user" / "secrets" / "music"
            (legacy_user_cache / "netease").mkdir(parents=True)
            (legacy_user_cache / "netease" / "song.mp3").write_bytes(b"music")
            (legacy_user_cache / "game_sfx").mkdir(parents=True)
            (legacy_user_cache / "game_sfx" / "impact.wav").write_bytes(b"heavy")

            with patch.object(cloud_constants, "ensure_canonical_user_storage_layout"), patch.object(
                cloud_constants, "_LEGACY_USER_CACHE_DIR", legacy_user_cache
            ), patch.object(cloud_constants, "_LEGACY_CACHE_DIR", legacy_music_cache), patch.object(
                cloud_constants, "_CACHE_DIR", target_cache
            ), patch.object(cloud_constants, "_USER_DATA_DIR", user_data), patch.object(
                cloud_constants, "_LOGIN_CACHE_FILE", user_data / "cloud.json"
            ), patch.object(cloud_constants, "_QQ_LOGIN_CACHE_FILE", user_data / "qq.json"), patch.object(
                cloud_constants, "_KUGOU_LOGIN_CACHE_FILE", user_data / "kugou.json"
            ), patch.object(cloud_constants, "_LEGACY_LOGIN_CACHE_FILE", root / "legacy-login.json"), patch.object(
                cloud_constants, "_LEGACY_USER_DATA_DIR", root / "legacy-user"
            ):
                cloud_constants.ensure_user_storage_layout()

            self.assertTrue((target_cache / "netease" / "song.mp3").exists())
            self.assertTrue((legacy_user_cache / "game_sfx" / "impact.wav").exists())
            self.assertFalse((target_cache / "game_sfx").exists())


if __name__ == "__main__":
    unittest.main()
