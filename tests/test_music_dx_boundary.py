from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


class MusicDxBoundaryTests(unittest.TestCase):
    def test_data_cleanup_does_not_initialize_playback_manager(self):
        from lib.script.music.service import MusicService

        service = MusicService()
        with patch.object(service, "initialize", side_effect=AssertionError("must stay lazy")):
            with patch(
                "lib.script.cloudmusic.user_data.clear_music_user_data",
                return_value={"history_items": 0},
            ) as clear_user_data:
                self.assertEqual(
                    service.clear_all_history_and_login_data(),
                    {"history_items": 0},
                )

        clear_user_data.assert_called_once_with(runtime_manager=None)

    def test_music_data_path_imports_without_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
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

            from lib.script.music.service import MusicService

            service = MusicService()
            service.initialize = lambda: (_ for _ in ()).throw(
                AssertionError("data cleanup must not initialize playback")
            )
            result = service.clear_all_history_and_login_data()
            assert "history_items" in result
            assert "lib.script.cloudmusic.manager" not in sys.modules
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cloudmusic_manager_does_not_import_qt_player(self):
        path = Path(__file__).resolve().parents[1] / "lib" / "script" / "cloudmusic" / "manager.py"
        source = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("_qt_player", source)
        self.assertNotIn("QtMusicPlayer", source)

    def test_cloudmusic_manager_uses_mci_when_qt_is_blocked(self):
        repo_root = Path(__file__).resolve().parents[1]
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

            import lib.script.cloudmusic.manager as manager_module

            class Hub:
                def submit_io(self, *_args, **_kwargs):
                    return None

            manager_module.get_compute_hub = lambda: Hub()
            runtime = manager_module.CloudMusicManager()
            try:
                assert runtime._use_native_player is False
                assert runtime._music_player is None
            finally:
                runtime.cleanup()
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dx_composition_does_not_import_pyqt_for_music(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise AssertionError(f"DX imported Qt: {name}")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import
            from lib.script.app.qt_backend_bootstrap import _configure_dx_backend
            from lib.core.dx_bridge.desktop_backend import cleanup_dx_desktop_backend
            from lib.script.music.service import MusicService

            _configure_dx_backend()
            try:
                service = MusicService()
                assert service._player_factory is None
                assert not [name for name in sys.modules if name.startswith("PyQt5")]
            finally:
                cleanup_dx_desktop_backend()
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
