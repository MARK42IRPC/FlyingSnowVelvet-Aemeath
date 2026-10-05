"""Local music decode fallback (MciMusicPlayer / _decoder) contract tests."""

from __future__ import annotations

import subprocess
import sys
import textwrap
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
