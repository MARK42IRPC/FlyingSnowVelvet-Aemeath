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
        """管理器只依赖中立播放器接口，不得导入 Qt 播放器或它的信号。"""
        source = (
            Path(__file__).resolve().parents[1]
            / "lib" / "script" / "cloudmusic" / "manager.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("qt_bridge", source)
        self.assertNotIn("pyqtSignal", source)
        # Qt 信号名字是 Qt 播放器才有的 API，管理器应该只调方法。
        for signal_name in (
            "play_requested",
            "pause_requested",
            "resume_requested",
            "stop_requested",
            "volume_requested",
            "seek_requested",
            "playback_started",
            "playback_finished",
            "playback_error",
        ):
            self.assertNotIn(signal_name, source)
        # 对播放器只能调方法，不能走 Qt 信号的 emit/connect。
        self.assertNotIn(".emit(", source)
        self.assertNotIn(".connect(", source)
        self.assertNotIn(".disconnect(", source)

    def test_cloudmusic_manager_still_plays_locally_when_qt_is_blocked(self):
        """没有 PyQt 时管理器仍然拿到一个造合同的非 Qt 播放器，而不是“没有播放器”。"""
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
            from lib.script.cloudmusic._player import MciMusicPlayer

            class Hub:
                def submit_io(self, *_args, **_kwargs):
                    return None

            manager_module.get_compute_hub = lambda: Hub()
            runtime = manager_module.CloudMusicManager()
            try:
                assert isinstance(runtime._music_player, MciMusicPlayer)
                # 造合同：管理器只依赖这一组接口，不再依赖 Qt 信号。
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
                    assert callable(getattr(runtime._music_player, name, None)), name
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
            from lib.core.render.backends.dx.desktop_backend import cleanup_dx_desktop_backend
            from lib.script.music.service import MusicService

            _configure_dx_backend()
            try:
                service = MusicService()
                factory = service._player_factory
                assert factory is not None, "DX must inject a non-Qt player"
                player = factory()
                assert callable(getattr(player, "set_callbacks", None))
                assert type(player).__module__.startswith("lib.script.cloudmusic")
                player.cleanup()
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
