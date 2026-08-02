import array
import unittest

from lib.script.microphone_stt.service import _is_spurious_auto_text, denoise_pcm16


class MicrophoneDenoiseTests(unittest.TestCase):
    def test_common_auto_mode_noise_words_are_suppressed(self):
        self.assertTrue(_is_spurious_auto_text("huh"))
        self.assertTrue(_is_spurious_auto_text(" huh! "))
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


if __name__ == "__main__":
    unittest.main()
