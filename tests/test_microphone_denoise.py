import array
import unittest

from lib.script.microphone_stt.service import (
    MicrophoneSttOptions,
    MicrophoneSttService,
    _is_spurious_auto_text,
    denoise_pcm16,
)


class MicrophoneDenoiseTests(unittest.TestCase):
    @staticmethod
    def _rms(chunk: bytes) -> float:
        samples = array.array("h")
        samples.frombytes(chunk)
        return (
            sum(int(sample) * int(sample) for sample in samples) / len(samples)
        ) ** 0.5

    def test_common_auto_mode_noise_words_are_suppressed(self):
        self.assertTrue(_is_spurious_auto_text("huh"))
        self.assertTrue(_is_spurious_auto_text(" huh! "))
        self.assertTrue(_is_spurious_auto_text("huh huh"))
        self.assertTrue(_is_spurious_auto_text("嗯、啊"))
        self.assertFalse(_is_spurious_auto_text("hello"))

    def test_quiet_samples_are_attenuated_and_speech_samples_are_retained(self):
        samples = array.array("h", [40, -120, 180, 900, -1400, 40])

        filtered, noise_floor = denoise_pcm16(
            samples.tobytes(),
            strength=1.0,
            gate_threshold=180,
        )

        result = array.array("h")
        result.frombytes(filtered)
        self.assertLess(abs(result[0]), abs(samples[0]))
        self.assertLess(abs(result[1]), abs(samples[1]))
        self.assertEqual(result[3], samples[3])
        self.assertEqual(result[4], samples[4])
        self.assertGreaterEqual(noise_floor, 0.0)

    def test_disabled_strength_keeps_pcm_bytes_unchanged(self):
        samples = array.array("h", [1, -20, 300, -900])

        filtered, _ = denoise_pcm16(
            samples.tobytes(),
            strength=0.0,
            gate_threshold=180,
        )

        self.assertEqual(filtered, samples.tobytes())

    def test_noise_floor_adapts_only_for_quiet_chunks(self):
        samples = array.array("h", [80] * 8)

        _, initial_floor = denoise_pcm16(
            samples.tobytes(),
            strength=0.5,
            gate_threshold=180,
        )
        _, updated_floor = denoise_pcm16(
            samples.tobytes(),
            strength=0.5,
            gate_threshold=180,
            noise_floor=initial_floor,
        )

        self.assertGreaterEqual(updated_floor, 0.0)
        self.assertLessEqual(updated_floor, initial_floor)

    def test_noise_floor_learns_steady_louder_room_noise(self):
        samples = array.array("h", [500] * 160)

        _, floor = denoise_pcm16(
            samples.tobytes(),
            strength=0.65,
            gate_threshold=180,
        )

        self.assertGreater(floor, 180.0)

    def test_speech_contamination_does_not_raise_noise_floor(self):
        quiet = array.array("h", [80] * 160)
        speech = array.array("h", [2400] * 160)
        mixed = (quiet + speech).tobytes()

        _, floor = denoise_pcm16(mixed, strength=0.65, gate_threshold=180)

        self.assertLess(floor, 500.0)

    def test_soft_knee_keeps_samples_above_gate_unchanged(self):
        samples = array.array("h", [170, 220, 900, -1400])

        filtered, _ = denoise_pcm16(
            samples.tobytes(),
            strength=0.65,
            gate_threshold=180,
        )

        result = array.array("h")
        result.frombytes(filtered)
        self.assertLess(abs(result[0]), abs(samples[0]))
        self.assertLess(abs(result[1]), abs(samples[1]))
        self.assertEqual(result[2:], samples[2:])

    def test_stable_loud_noise_is_attenuated_by_change_rate_gate(self):
        samples = array.array("h", [500] * 320)

        filtered, floor = denoise_pcm16(
            samples.tobytes(),
            strength=0.65,
            gate_threshold=180,
        )

        self.assertGreater(floor, 180.0)
        self.assertLess(self._rms(filtered), 420.0)

    def test_loudness_rise_preserves_dynamic_speech(self):
        samples = array.array("h")
        for level in (120, 900, 1400, 700, 1200, 800):
            samples.extend([level] * 160)

        filtered, _ = denoise_pcm16(
            samples.tobytes(),
            strength=0.65,
            gate_threshold=180,
        )
        result = array.array("h")
        result.frombytes(filtered)

        self.assertEqual(result[160], 900)
        self.assertEqual(result[320], 1400)

    def test_loudness_state_protects_onset_across_chunks(self):
        state: dict[str, float] = {}
        noise = array.array("h", [500] * 320).tobytes()
        _, floor = denoise_pcm16(
            noise,
            strength=0.65,
            gate_threshold=180,
            state=state,
        )

        speech = array.array("h", [900] * 320)
        filtered, _ = denoise_pcm16(
            speech.tobytes(),
            strength=0.65,
            gate_threshold=180,
            noise_floor=floor,
            state=state,
        )

        result = array.array("h")
        result.frombytes(filtered)
        self.assertEqual(result[0], 900)

    def test_auto_mode_does_not_rearm_on_stable_background_level(self):
        service = object.__new__(MicrophoneSttService)
        service._auto_voice_candidate_chunks = 0
        options = MicrophoneSttOptions(auto_mode=True, speech_rms_threshold=550)
        background = array.array("h", [600] * 320).tobytes()

        recognizer, segments, last_voice_time = service._handle_auto_mode_chunk(
            lambda: object(),
            None,
            [],
            background,
            0.0,
            None,
            options,
            loudness_change_rate=0.0,
            loudness_has_history=True,
        )

        self.assertIsNone(recognizer)
        self.assertEqual(segments, [])
        self.assertEqual(last_voice_time, 0.0)
        self.assertEqual(service._auto_voice_candidate_chunks, 0)


if __name__ == "__main__":
    unittest.main()
