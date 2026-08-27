import math
import queue
import threading
import unittest
from unittest.mock import Mock

import numpy as np

from lib.script.microphone_stt.service import (
    MicrophoneSttOptions,
    MicrophoneSttService,
    _is_spurious_auto_text,
    _split_pcm16_frames,
    denoise_pcm16,
)
from lib.core.event.center import Event, EventType


class MicrophoneDenoiseTests(unittest.TestCase):
    @staticmethod
    def _tone(level: float, frequency: float, seconds: float = 0.4, sample_rate: int = 16000) -> bytes:
        count = int(seconds * sample_rate)
        time_axis = np.arange(count, dtype=np.float32) / sample_rate
        samples = np.rint(level * np.sin(2.0 * math.pi * frequency * time_axis))
        return np.clip(samples, -32768, 32767).astype("<i2").tobytes()

    @staticmethod
    def _rms(chunk: bytes) -> float:
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
        return float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0

    def test_common_auto_mode_noise_words_are_suppressed(self):
        self.assertTrue(_is_spurious_auto_text("huh"))
        self.assertTrue(_is_spurious_auto_text("嗯、啊"))
        self.assertFalse(_is_spurious_auto_text("hello"))

    def test_vad_frame_split_uses_complete_pcm16_frames(self):
        payload = bytes(range(20))
        frames = _split_pcm16_frames(payload, 4)
        self.assertEqual(frames, [payload[:8], payload[8:16]])

    def test_disabled_denoise_keeps_pcm_bytes_unchanged(self):
        speech = self._tone(2200, 440)
        filtered, noise_floor = denoise_pcm16(speech, strength=0.0)
        self.assertEqual(filtered, speech)
        self.assertGreater(noise_floor, 0.0)

    def test_spectral_soft_mask_reduces_stationary_noise_without_zeroing_speech(self):
        noise = self._tone(700, 220)
        speech = self._tone(2600, 700)
        noise_samples = np.frombuffer(noise, dtype="<i2").astype(np.int32)
        speech_samples = np.frombuffer(speech, dtype="<i2").astype(np.int32)
        mixed = np.clip(noise_samples + speech_samples, -32768, 32767).astype("<i2").tobytes()

        filtered, noise_floor = denoise_pcm16(
            mixed,
            strength=0.8,
            noise_reference=noise,
        )

        self.assertGreater(noise_floor, 0.0)
        self.assertLess(self._rms(filtered), self._rms(mixed))
        self.assertGreater(self._rms(filtered), self._rms(noise) * 1.2)

    def test_quietest_frames_are_used_when_noise_reference_is_missing(self):
        quiet = self._tone(300, 180)
        speech = self._tone(2400, 650)
        quiet_samples = np.frombuffer(quiet, dtype="<i2").astype(np.int32)
        speech_samples = np.frombuffer(speech, dtype="<i2").astype(np.int32)
        mixed = np.clip(quiet_samples + speech_samples, -32768, 32767).astype("<i2").tobytes()

        filtered, noise_floor = denoise_pcm16(mixed, strength=0.6)

        self.assertGreater(noise_floor, 0.0)
        self.assertGreater(self._rms(filtered), 0.0)

    def test_default_capture_is_vad_sized_and_options_keep_compatibility(self):
        options = MicrophoneSttOptions()
        self.assertEqual(options.block_size, 320)
        self.assertEqual(options.vad_mode, 2)
        self.assertEqual(options.pre_roll_ms, 300)

    def test_auto_only_stop_does_not_interrupt_manual_listening(self):
        service = object.__new__(MicrophoneSttService)
        service._lock = threading.RLock()
        service._current_options = MicrophoneSttOptions(auto_mode=False)
        service.stop_listening = Mock()

        event = Event(EventType.MIC_STT_STOP, {"auto_only": True})
        service._on_stop_request(event)

        service.stop_listening.assert_not_called()
        self.assertTrue(event.handled)

    def test_auto_only_stop_interrupts_auto_listening_or_startup(self):
        service = object.__new__(MicrophoneSttService)
        service._lock = threading.RLock()
        service._current_options = MicrophoneSttOptions(auto_mode=True)
        service.stop_listening = Mock()

        event = Event(EventType.MIC_STT_STOP, {"auto_only": True})
        service._on_stop_request(event)

        service.stop_listening.assert_called_once_with()
        self.assertTrue(event.handled)

    def test_offline_recognition_collects_final_text_only(self):
        class FakeRecognizer:
            def __init__(self):
                self.chunks = 0

            def AcceptWaveform(self, chunk):
                self.chunks += 1
                return False

            def FinalResult(self):
                return '{"text": "hello world"}'

        service = object.__new__(MicrophoneSttService)
        options = MicrophoneSttOptions(denoise_enabled=False)
        text = service._recognize_utterance(
            FakeRecognizer,
            self._tone(1800, 440, seconds=0.1),
            b"",
            options,
        )
        self.assertEqual(text, "hello world")

    def test_offline_recognition_keeps_short_tail_frame(self):
        recognizer = None

        class FakeRecognizer:
            def __init__(self):
                nonlocal recognizer
                recognizer = self
                self.chunk_sizes = []

            def AcceptWaveform(self, chunk):
                self.chunk_sizes.append(len(chunk))
                return False

            def FinalResult(self):
                return '{"text": "tail kept"}'

        service = object.__new__(MicrophoneSttService)
        options = MicrophoneSttOptions(denoise_enabled=False)
        text = service._recognize_utterance(
            FakeRecognizer,
            b"\x00\x00" * 321,
            b"",
            options,
        )

        self.assertEqual(text, "tail kept")
        self.assertIsNotNone(recognizer)
        self.assertEqual(recognizer.chunk_sizes, [640, 2])

    def test_empty_denoised_result_retries_original_pcm(self):
        created = []

        class FakeRecognizer:
            def __init__(self):
                self.index = len(created)
                created.append(self)

            def AcceptWaveform(self, chunk):
                return False

            def FinalResult(self):
                text = "" if self.index == 0 else "raw recovered"
                return '{"text": "%s"}' % text

        service = object.__new__(MicrophoneSttService)
        text = service._recognize_utterance(
            FakeRecognizer,
            self._tone(1800, 440, seconds=0.1),
            self._tone(200, 120, seconds=0.1),
            MicrophoneSttOptions(denoise_enabled=True),
        )

        self.assertEqual(text, "raw recovered")
        self.assertEqual(len(created), 2)

    def test_manual_worker_collects_audio_frames_until_stop(self):
        class FakeVadModule:
            class Vad:
                def __init__(self, mode):
                    self.mode = mode

                def is_speech(self, frame, sample_rate):
                    return True

        finalized = []
        audio_queue = queue.Queue()
        audio_queue.put(b"\x01\x00" * 320)
        audio_queue.put(None)

        service = object.__new__(MicrophoneSttService)
        service._auto_speech_active = False
        service._publish_state = lambda *args, **kwargs: None
        service._finalize_utterance = lambda _factory, frames, noise, _options, **kwargs: finalized.append(
            (list(frames), list(noise), kwargs)
        )

        service._worker_loop(
            lambda: None,
            MicrophoneSttOptions(auto_mode=False, denoise_enabled=False),
            audio_queue,
            threading.Event(),
            FakeVadModule,
        )

        self.assertEqual(len(finalized), 1)
        self.assertEqual(finalized[0][0], [b"\x01\x00" * 320])
        self.assertEqual(finalized[0][1], [])

    def test_manual_worker_finalizes_after_sentence_silence(self):
        class FakeVadModule:
            class Vad:
                def __init__(self, mode):
                    self.mode = mode

                def is_speech(self, frame, sample_rate):
                    return frame[:2] != b"\x00\x00"

        finalized = []
        audio_queue = queue.Queue()
        speech_frame = b"\x10\x00" * 320
        silence_frame = b"\x00\x00" * 320
        audio_queue.put(speech_frame)
        audio_queue.put(speech_frame)
        for _ in range(30):
            audio_queue.put(silence_frame)
        audio_queue.put(None)

        service = object.__new__(MicrophoneSttService)
        service._auto_speech_active = False
        service._publish_state = lambda *args, **kwargs: None
        service._finalize_utterance = lambda _factory, frames, noise, _options, **kwargs: finalized.append(
            (list(frames), list(noise), kwargs)
        )

        service._worker_loop(
            lambda: None,
            MicrophoneSttOptions(
                auto_mode=False,
                denoise_enabled=False,
            ),
            audio_queue,
            threading.Event(),
            FakeVadModule,
        )

        self.assertEqual(len(finalized), 1)
        self.assertEqual(finalized[0][0], [speech_frame, speech_frame] + [silence_frame] * 30)
        self.assertEqual(finalized[0][1], [silence_frame] * 30)
        self.assertEqual(finalized[0][2]["reason"], "silence")


if __name__ == "__main__":
    unittest.main()
