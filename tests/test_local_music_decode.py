"""Local music decode fallback (MciMusicPlayer / _decoder) contract tests."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 一个极小但合法的 8-bit/8kHz 单声道 WAV，够 soundfile 解码成 PCM16。
_MINIMAL_WAV = (
    b"RIFF"
    + (36 + 8).to_bytes(4, "little")
    + b"WAVEfmt "
    + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little")
    + (1).to_bytes(2, "little")
    + (8000).to_bytes(4, "little")
    + (8000).to_bytes(4, "little")
    + (1).to_bytes(2, "little")
    + (8).to_bytes(2, "little")
    + b"data"
    + (8).to_bytes(4, "little")
    + bytes([128, 160, 192, 224, 224, 192, 160, 128])
)


class LocalAudioDecodeTests(unittest.TestCase):
    def test_passthrough_set_covers_mci_native_formats(self):
        from lib.script.cloudmusic._constants import local_audio_needs_decode

        for name in ("song.mp3", "song.MP3", "voice.wav", "old.wma"):
            self.assertFalse(local_audio_needs_decode(name), name)
        for name in ("song.flac", "song.m4a", "song.aac", "song.ogg", "song.opus", "song.webm"):
            self.assertTrue(local_audio_needs_decode(name), name)

    def test_soundfile_engine_decodes_a_wav_container(self):
        from lib.script.cloudmusic import _decoder

        with _TempDir() as tmp:
            source = tmp / "tone.wav"
            source.write_bytes(_MINIMAL_WAV)
            target = tmp / "cache" / "out.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(_decoder._decode_soundfile(source, target))
            with wave.open(str(target), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), 8000)
                self.assertEqual(handle.getnframes(), 8)

    def test_ensure_decoded_wav_caches_by_stamp(self):
        from lib.script.cloudmusic import _decoder

        with _TempDir() as tmp:
            source = tmp / "tone.flac"
            source.write_bytes(_MINIMAL_WAV)
            with patch.object(_decoder, "decoded_cache_root", return_value=tmp / "cache"):
                first = _decoder.ensure_decoded_wav(source)
                self.assertIsNotNone(first)
                self.assertTrue(first.is_file())
                with wave.open(str(first), "rb") as handle:
                    self.assertEqual(handle.getnchannels(), 1)
                # 第二次命中缓存：把引擎全部屏蔽，仍然返回同一路径。
                with patch.object(_decoder, "_decode_soundfile", side_effect=AssertionError("cache hit")):
                    second = _decoder.ensure_decoded_wav(source)
                self.assertEqual(first, second)

    def test_ensure_decoded_wav_returns_none_when_all_engines_fail(self):
        from lib.script.cloudmusic import _decoder

        with _TempDir() as tmp:
            source = tmp / "broken.opus"
            source.write_bytes(b"not really audio")
            with patch.object(_decoder, "decoded_cache_root", return_value=tmp / "cache"):
                with patch.object(_decoder, "_decode_soundfile", return_value=False):
                    with patch.object(_decoder, "_decode_av", return_value=False):
                        self.assertIsNone(_decoder.ensure_decoded_wav(source))

    def test_player_decodes_before_opening_mci(self):
        from lib.script.cloudmusic import _player

        decoded = Path("C:/cache/decoded/track.wav")
        opened: list[str] = []

        class _FakeMci:
            @staticmethod
            def send(command, *_args, **_kwargs):
                opened.append(command)
                # 只有 open 命令需要返回码，其余按成功处理。
                return 0

        player = _player.MciMusicPlayer()
        with patch.object(_player, "ensure_decoded_wav", return_value=decoded) as decoder:
            with patch.object(_player, "_mci", _FakeMci.send):
                with patch.object(_player, "_mci_query", lambda *_a, **_k: "1000"):
                    player.play("C:/music/song.flac", 0.5, 7)
        decoder.assert_called_once()
        self.assertTrue(
            any("track.wav" in command and command.startswith("open") for command in opened),
            opened,
        )
        player.cleanup()

    def test_decoder_path_imports_without_pyqt(self):
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

            from lib.script.cloudmusic._constants import local_audio_needs_decode
            from lib.script.cloudmusic._decoder import ensure_decoded_wav
            from lib.script.cloudmusic._player import MciMusicPlayer

            assert local_audio_needs_decode("a.flac") is True
            assert local_audio_needs_decode("a.mp3") is False
            assert ensure_decoded_wav("C:/definitely/missing.flac") is None
            player = MciMusicPlayer()
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
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class MciMusicPlayerThreadAffinityTests(unittest.TestCase):
    """MCI 别名与线程强绑定：open 与后续每条命令必须落在同一个线程。

    回归「本地音乐播放队列音频无限重叠」：旧实现里 play() 在调用线程 open，
    随后在新建的 cloudmusic-poll 线程里 status mode 轮询。跨线程的 MCI 调用返回
    错误码 263（"设备未打开"）加空串，旧代码把空串当成「播完」立即推进队列；而同一线程外
    close 也失败，被放弃的音频继续发声。整条队列因此不断叠加播放。

    本用例盯住修复的本质：open 与 status mode 必须由同一个线程发出，且轮询期间
    mode 仍为 playing 时绝不能判定为播放完成。这里用一张按线程登记的别名表模拟 MCI
    的线程亲和：只有 open 别名的那个线程才认得它，别的线程一律得到错误码 263 与空串。
    """

    _ERR_NOT_OPEN = 263

    def _thread_affine_tools(self):
        """构造一套模拟 MCI 线程亲和的 _mci / _mci_query / _mci_query_result。"""
        from lib.script.cloudmusic import _player

        owner: dict[str, int] = {}
        retired: list[str] = []
        state = {"mode": "playing"}
        open_threads: list[int] = []
        mode_threads: list[int] = []

        def fake_mci(command, *_args, **_kwargs):
            thread = threading.get_ident()
            parts = command.split()
            verb = parts[0] if parts else ""
            alias = parts[1] if len(parts) > 1 else ""
            if verb == "open":
                open_threads.append(threading.get_ident())
                owner[parts[-1]] = thread
                return 0
            if verb in ("stop", "close"):
                if owner.get(alias) != thread:
                    return self._ERR_NOT_OPEN
                if verb == "close":
                    owner.pop(alias, None)
                    retired.append(alias)
                return 0
            if verb == "play":
                return 0 if owner.get(alias) == thread else self._ERR_NOT_OPEN
            return 0

        def fake_query(command, *_args, **_kwargs):
            thread = threading.get_ident()
            parts = command.split()
            alias = parts[1] if len(parts) > 1 else ""
            if owner.get(alias) != thread:
                return ""
            return "1000" if command.endswith("length") else "500"

        def fake_query_result(command, *_args, **_kwargs):
            thread = threading.get_ident()
            parts = command.split()
            alias = parts[1] if len(parts) > 1 else ""
            if owner.get(alias) != thread:
                return self._ERR_NOT_OPEN, ""
            if command.endswith("mode"):
                mode_threads.append(threading.get_ident())
                return 0, state["mode"]
            return 0, fake_query(command)

        return _player, fake_mci, fake_query, fake_query_result, open_threads, mode_threads, retired

    def test_every_mci_command_runs_on_the_same_thread(self):
        finished: list[int] = []
        (
            _player,
            fake_mci,
            fake_query,
            fake_query_result,
            open_threads,
            mode_threads,
            retired,
        ) = self._thread_affine_tools()

        player = _player.MciMusicPlayer()
        player.set_callbacks(on_finished=lambda gen: finished.append(gen))
        with patch.object(_player, "_mci", fake_mci), patch.object(
            _player, "_mci_query", fake_query
        ), patch.object(
            _player, "_mci_query_result", fake_query_result, create=True
        ):
            player.play("C:/music/thread-affinity.wav", 0.5, 1)
            time.sleep(0.8)
            player.cleanup()

        self.assertTrue(open_threads, "open 没有被调用")
        self.assertTrue(mode_threads, "播放状态轮询没有跑起来")
        self.assertEqual(
            set(open_threads) | set(mode_threads),
            {open_threads[0]},
            "open 与 status mode 必须由同一个线程发出（MCI 别名与线程强绑定）",
        )
        self.assertEqual(
            finished, [], "mode 仍为 playing 时不得判定为播放完成"
        )

    def test_an_off_thread_query_must_not_look_like_a_finished_track(self):
        """换歌时旧别名必须真的被 close 掉，不能留下还在发声的设备。

        这是「音频无限重叠」的直接断言：连放两首后，第一首的别名必须出现在被 close
        的清单里；只要它没被关掉，两个设备就会同时出声。
        """
        (
            _player,
            fake_mci,
            fake_query,
            fake_query_result,
            _open_threads,
            _mode_threads,
            retired,
        ) = self._thread_affine_tools()

        player = _player.MciMusicPlayer()
        with patch.object(_player, "_mci", fake_mci), patch.object(
            _player, "_mci_query", fake_query
        ), patch.object(
            _player, "_mci_query_result", fake_query_result, create=True
        ):
            player.play("C:/music/first.wav", 0.5, 1)
            time.sleep(0.4)
            player.play("C:/music/second.wav", 0.5, 2)
            time.sleep(0.4)
            player.cleanup()

        self.assertGreaterEqual(
            len(retired), 2,
            f"每次换歌都要关掉上一首的设备，实际关闭: {retired}",
        )


class _TempDir:
    """轻量级临时目录上下文，避免依赖 tempfile 的清理策略差异。"""

    def __enter__(self) -> Path:
        import uuid

        base = REPO_ROOT / "resc" / "temp" / f"decode-test-{uuid.uuid4().hex[:8]}"
        base.mkdir(parents=True, exist_ok=True)
        self._base = base
        return base

    def __exit__(self, *_exc) -> None:
        import shutil

        shutil.rmtree(self._base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
