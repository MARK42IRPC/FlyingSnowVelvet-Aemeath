from __future__ import annotations

import unittest
from pathlib import Path

from lib.script.gemes.MAIN.game_packages import GamePackageManifest, InstalledGame
from lib.script.gemes.MAIN.runtime import build_game_hash_commands


class GameRuntimeHashCommandTests(unittest.TestCase):
    def test_build_game_hash_commands_includes_game_name_and_aliases(self) -> None:
        manifest = GamePackageManifest(
            game_id="lahai_tetris",
            name="拉海洛方块",
            version="1.0.1",
            summary="test summary",
            entry_module="lahai_tetris_pkg.entry",
            entry_class="LahaiTetrisGame",
            command_aliases=("拉海洛", "拉海洛方块"),
        )
        record = InstalledGame(manifest=manifest, install_dir=Path("test-install/lahai_tetris"), source="official")

        commands = build_game_hash_commands([record])

        self.assertEqual(
            commands,
            [
                ("拉海洛方块", "lahai_tetris", "[打开/关闭]", "打开拉海洛方块"),
                ("拉海洛", "lahai_tetris", "[打开/关闭]", "打开拉海洛方块（别名）"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
