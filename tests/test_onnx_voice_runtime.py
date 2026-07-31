import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.gsvmove import onnx_runtime as runtime_module
from lib.script.gsvmove.onnx_runtime import (
    OnnxInferenceRequest,
    OnnxVoiceRuntime,
    _split_auto_language_text,
    normalize_language,
)
from lib.script.gsvmove.package_manager import VoicePackageValidation


_FAKE_INFER = """
class _SoundFile:
    @staticmethod
    def write(path, audio, sample_rate, subtype=None):
        with open(path, 'wb') as stream:
            stream.write(b'RIFF' + b'0' * 64)

class _Soxr:
    calls = []
    @classmethod
    def resample(cls, audio, source_rate, target_rate, quality=None):
        cls.calls.append((source_rate, target_rate, quality))
        return audio

sf = _SoundFile()
soxr = _Soxr()

def select_providers(name):
    return [name]

class AimisiOnnx:
    def __init__(self, package_root, providers):
        self.package_root = package_root
        self.providers = providers
        self.calls = []

    def synthesize(self, text, language, max_steps=500, **parameters):
        self.calls.append((text, language, max_steps, parameters))
        return [0.0, 0.1]
"""


class OnnxVoiceRuntimeTests(unittest.TestCase):
    def test_language_detection_preserves_auto_for_mixed_text(self):
        self.assertEqual(normalize_language(None, "你好 Aemeath"), "auto")
        self.assertEqual(normalize_language("auto", "你好 Aemeath"), "auto")
        self.assertEqual(normalize_language(None, "Hello Aemeath"), "en")
        self.assertEqual(normalize_language("en-US", "你好"), "en")

    def test_auto_language_split_preserves_all_mixed_text(self):
        text = "你好, Aemeath! 今天 is sunny."
        segments = _split_auto_language_text(text)

        self.assertEqual(
            segments,
            (
                ("你好, ", "zh"),
                ("Aemeath! ", "en"),
                ("今天 ", "zh"),
                ("is sunny.", "en"),
            ),
        )
        self.assertEqual("".join(value for value, _language in segments), text)

    def test_request_clamps_legacy_parameters(self):
        request = OnnxInferenceRequest.from_payload({
            "text": "hello",
            "speed_factor": 8,
            "temperature": -4,
            "top_k": 5000,
            "top_p": 0,
            "seed": -20,
            "media_type": "OGG",
            "streaming_mode": 8,
            "max_steps": 5000,
        })
        self.assertEqual(request.language, "en")
        self.assertEqual(request.speed_factor, 2.0)
        self.assertEqual(request.temperature, 0.01)
        self.assertEqual(request.top_k, 1025)
        self.assertEqual(request.top_p, 0.01)
        self.assertEqual(request.seed, -1)
        self.assertEqual(request.media_type, "ogg")
        self.assertEqual(request.streaming_mode, 3)
        self.assertEqual(request.max_steps, 1200)

    def test_request_falls_back_to_wav_for_unsupported_media_type(self):
        request = OnnxInferenceRequest.from_payload({"text": "hello", "media_type": "mp3"})

        self.assertEqual(request.media_type, "wav")

    def test_request_accepts_complete_gsv_v2_payload(self):
        request = OnnxInferenceRequest.from_payload({
            "text": "hello",
            "text_lang": "en",
            "prompt_text": "reference words",
            "prompt_lang": "en",
            "top_k": 31,
            "top_p": 0.9,
            "temperature": 0.8,
            "text_split_method": "cut3",
            "batch_size": 4,
            "batch_threshold": 0.6,
            "split_bucket": False,
            "speed_factor": 1.2,
            "fragment_interval": 0.2,
            "seed": 42,
            "media_type": "raw",
            "streaming_mode": 2,
            "parallel_infer": False,
            "repetition_penalty": 1.2,
            "sample_steps": 16,
            "super_sampling": True,
            "overlap_length": 3,
            "min_chunk_length": 24,
            "max_steps": 640,
        })

        expected = {
            "language": "en",
            "prompt_text": "reference words",
            "prompt_language": "en",
            "top_k": 31,
            "top_p": 0.9,
            "temperature": 0.8,
            "text_split_method": "cut3",
            "batch_size": 4,
            "batch_threshold": 0.6,
            "split_bucket": False,
            "speed_factor": 1.2,
            "fragment_interval": 0.2,
            "seed": 42,
            "media_type": "raw",
            "streaming_mode": 2,
            "parallel_infer": False,
            "repetition_penalty": 1.2,
            "sample_steps": 16,
            "super_sampling": True,
            "overlap_length": 3,
            "min_chunk_length": 24,
            "max_steps": 640,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(request, name), value)

    def test_runtime_loads_package_entry_and_writes_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "infer.py").write_text(_FAKE_INFER, encoding="utf-8")
            validation = VoicePackageValidation(
                True,
                "ok",
                {"sample_rate": 32000, "name": "aimisiV2"},
            )
            with patch.object(runtime_module, "validate_voice_package", return_value=validation):
                runtime = OnnxVoiceRuntime(root)
                try:
                    output = runtime.synthesize_to_file(
                        {"text": "hello", "speed_factor": 2.0},
                        root / "output.wav",
                    )
                    self.assertGreater(output.stat().st_size, 44)
                    call = runtime._engine.calls[0]
                    self.assertEqual(call[:3], ("hello", "en", 500))
                    self.assertEqual(call[3]["speed_factor"], 2.0)
                    self.assertEqual(call[3]["temperature"], 1.0)
                    self.assertEqual(call[3]["top_k"], 15)
                    self.assertEqual(runtime._module.soxr.calls, [])
                finally:
                    runtime.close()

    def test_runtime_routes_auto_mixed_text_to_both_frontends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "infer.py").write_text(_FAKE_INFER, encoding="utf-8")
            validation = VoicePackageValidation(
                True,
                "ok",
                {"sample_rate": 32000, "name": "aimisiV2"},
            )
            with patch.object(runtime_module, "validate_voice_package", return_value=validation):
                runtime = OnnxVoiceRuntime(root)
                try:
                    runtime.synthesize_to_file(
                        {
                            "text": "你好, Aemeath!",
                            "text_lang": "auto",
                            "fragment_interval": 0,
                        },
                        root / "mixed.wav",
                    )
                    self.assertEqual(
                        [call[:2] for call in runtime._engine.calls],
                        [("你好, ", "zh"), ("Aemeath!", "en")],
                    )
                finally:
                    runtime.close()


if __name__ == "__main__":
    unittest.main()
