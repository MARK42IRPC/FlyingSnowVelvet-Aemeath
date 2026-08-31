from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.script.ui.game_manager_window import GameManagerWindow


class GameManagerNativeDialogTests(unittest.TestCase):
    def test_install_uses_platform_default_file_dialog(self) -> None:
        fake_window = SimpleNamespace()
        with patch(
            "lib.script.ui.game_manager_window.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ) as get_open_file_name:
            GameManagerWindow._install_zip(fake_window)

        self.assertNotIn("options", get_open_file_name.call_args.kwargs)

    def test_export_uses_platform_default_file_dialog(self) -> None:
        record = SimpleNamespace(
            game_id="sample",
            manifest=SimpleNamespace(game_id="sample", version="1.0.0"),
        )
        fake_window = SimpleNamespace(_selected_game=lambda: record)
        with patch(
            "lib.script.ui.game_manager_window.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ) as get_save_file_name:
            GameManagerWindow._export_selected_game(fake_window)

        self.assertNotIn("options", get_save_file_name.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
