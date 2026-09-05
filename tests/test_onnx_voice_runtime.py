import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from lib.script.gsvmove import onnx_runtime as runtime_module
from lib.script.gsvmove.onnx_runtime import (
    OnnxInferenceRequest,
    OnnxVoiceRuntime,
    OnnxVoiceRuntimeError,
    _configure_cuda_provider,
    _run_cuda_session,
    _configure_hybrid_provider,
    _configure_mixed_language_frontend,
    _load_isolated_genie_frontend,
    _split_auto_language_text,
    normalize_language,
)
from lib.script.gsvmove.package_manager import VoicePackageValidation


_FAKE_INFER = """
import numpy as np

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

def normalize_language(value, text):
    return value

class AimisiOnnx:
    def __init__(self, package_root, providers):
        self.package_root = package_root
        self.providers = providers
        self.calls = []

    def _phones(self, text, language):
        return np.asarray([[1]], dtype=np.int64), np.zeros((1, 1024), dtype=np.float32)

    def synthesize(self, text, language, max_steps=500, **parameters):
        self.calls.append((text, language, max_steps, parameters))
        return [0.0, 0.1]
"""


class OnnxVoiceRuntimeTests(unittest.TestCase):
    @staticmethod
    def _make_fake_genie(site: Path, version: str = "2.0.2") -> Path:
        package = site / "genie_tts"
        for relative in (
            "Core/Resources.py",
            "G2P/SymbolsV2.py",
            "G2P/Chinese/ChineseG2P.py",
            "G2P/English/EnglishG2P.py",
        ):
            path = package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        (package / "__init__.py").write_text(
            "raise RuntimeError('GUI init must never execute')\n",
            encoding="utf-8",
        )
        (package / "GetPhonesAndBert.py").write_text(
            "def get_phones_and_bert(text, language='Chinese'):\n"
            "    return text, language\n",
            encoding="utf-8",
        )
        (package / "ModelManager.py").write_text(
            "class _Manager:\n"
            "    def load_roberta_model(self):\n"
            "        return True\n"
            "model_manager = _Manager()\n",
            encoding="utf-8",
        )
        dist = site / f"genie_tts-{version}.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: genie-tts\nVersion: {version}\n",
            encoding="utf-8",
        )
        return package

    @staticmethod
    def _make_fake_common(root: Path) -> Path:
        common = root / "common"
        for relative in (
            "G2P/EnglishG2P/checkpoint20.npz",
            "G2P/ChineseG2P/opencpop-strict.txt",
            "chinese-hubert-base/chinese-hubert-base.onnx",
            "speaker_encoder.onnx",
            "RoBERTa/RoBERTa.onnx",
            "RoBERTa/roberta_tokenizer/tokenizer.json",
        ):
            path = common / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        return common

    def test_genie_loader_skips_side_effectful_init_and_restores_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            site = root / "site-packages"
            self._make_fake_genie(site)
            common = self._make_fake_common(root)
            sentinel = object()
            with patch.dict(sys.modules, {"genie_tts": sentinel}), patch.object(
                runtime_module, "_genie_search_roots", return_value=(site,)
            ):
                frontend, cleanup = _load_isolated_genie_frontend(common)
                self.assertEqual(frontend("hello", "English"), ("hello", "English"))
                self.assertIsNot(sys.modules["genie_tts"], sentinel)
                cleanup()
                self.assertIs(sys.modules.get("genie_tts"), sentinel)

    def test_genie_loader_rejects_wrong_pinned_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            site = root / "site-packages"
            self._make_fake_genie(site, version="2.0.1")
            common = self._make_fake_common(root)
            with patch.object(runtime_module, "_genie_search_roots", return_value=(site,)):
                with self.assertRaisesRegex(
                    OnnxVoiceRuntimeError,
                    r"genie-tts==2\.0\.2.*2\.0\.1",
                ):
                    _load_isolated_genie_frontend(common)

    def test_cuda_iobinding_keeps_device_outputs_and_cpu_stop_flag(self):
        class FakeBinding:
            def __init__(self):
                self.inputs = []
                self.outputs = []

            def bind_ortvalue_input(self, name, value):
                self.inputs.append((name, "ort", value))

            def bind_cpu_input(self, name, value):
                self.inputs.append((name, "cpu", value))

            def bind_output(self, name, device):
                self.outputs.append((name, device))

            def get_outputs(self):
                return self.outputs

        class FakeSession:
            def __init__(self):
                self.binding = FakeBinding()

            def io_binding(self):
                return self.binding

            def get_outputs(self):
                return [
                    type("Output", (), {"name": "y"})(),
                    type("Output", (), {"name": "stop"})(),
                ]

            def run_with_iobinding(self, binding):
                self.ran_binding = binding

        class FakeOrtValue:
            def device_name(self):
                return "cuda"

        session = FakeSession()
        _run_cuda_session(
            session,
            {"state": FakeOrtValue(), "sample_noise": object()},
            cpu_output_indexes=(1,),
        )

        self.assertEqual(session.binding.inputs[0][1], "ort")
        self.assertEqual(session.binding.inputs[1][1], "cpu")
        self.assertEqual(session.binding.outputs, [("y", "cuda"), ("stop", "cpu")])

    def test_hybrid_provider_keeps_iterative_stage_on_cpu(self):
        class FakeOptions:
            pass

        class FakeOrt:
            class GraphOptimizationLevel:
                ORT_ENABLE_ALL = "all"

            class ExecutionMode:
                ORT_SEQUENTIAL = "sequential"

            SessionOptions = FakeOptions

            @staticmethod
            def get_available_providers():
                return ["DmlExecutionProvider", "CPUExecutionProvider"]

        calls = []

        class FakeModule:
            ort = FakeOrt()
            os = type("FakeOs", (), {"cpu_count": staticmethod(lambda: 12)})

            @staticmethod
            def load_optional_external_session(model_path, weights_path, providers):
                calls.append((Path(model_path).name, tuple(providers)))
                return providers

        module = FakeModule()
        providers = _configure_hybrid_provider(module)
        options = module.make_session_options()
        module.load_optional_external_session(Path("t2s_stage_decoder_fp32.onnx"), Path("a.bin"), providers)
        module.load_optional_external_session(Path("vits_v2pro.onnx"), Path("b.bin"), providers)

        self.assertEqual(providers, ["DmlExecutionProvider", "CPUExecutionProvider"])
        self.assertFalse(options.enable_mem_pattern)
        self.assertEqual(options.execution_mode, "sequential")
        self.assertEqual(calls, [
            ("t2s_stage_decoder_fp32.onnx", ("CPUExecutionProvider",)),
            ("vits_v2pro.onnx", ("DmlExecutionProvider", "CPUExecutionProvider")),
        ])

    def test_cuda_provider_uses_cuda_for_iterative_stage(self):
        class FakeOptions:
            pass

        class FakeOrt:
            class GraphOptimizationLevel:
                ORT_ENABLE_ALL = "all"

            class ExecutionMode:
                ORT_SEQUENTIAL = "sequential"

            SessionOptions = FakeOptions

            @staticmethod
            def get_available_providers():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        calls = []

        class FakeModule:
            ort = FakeOrt()
            os = type("FakeOs", (), {"cpu_count": staticmethod(lambda: 12)})

            @staticmethod
            def load_optional_external_session(model_path, weights_path, providers):
                calls.append((Path(model_path).name, tuple(providers)))
                return providers

        module = FakeModule()
        providers = _configure_cuda_provider(module)
        options = module.make_session_options()
        module.load_optional_external_session(Path("t2s_stage_decoder_fp32.onnx"), Path("a.bin"), providers)
        module.load_optional_external_session(Path("vits_v2pro.onnx"), Path("b.bin"), providers)

        self.assertEqual(providers, ["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.assertFalse(options.enable_mem_pattern)
        self.assertEqual(options.execution_mode, "sequential")
        self.assertEqual(calls, [
            ("t2s_stage_decoder_fp32.onnx", ("CUDAExecutionProvider", "CPUExecutionProvider")),
            ("vits_v2pro.onnx", ("CUDAExecutionProvider", "CPUExecutionProvider")),
        ])

    def test_cuda_provider_rejects_session_that_falls_back_to_cpu(self):
        class FakeOptions:
            pass

        class FakeOrt:
            class GraphOptimizationLevel:
                ORT_ENABLE_ALL = "all"

            class ExecutionMode:
                ORT_SEQUENTIAL = "sequential"

            SessionOptions = FakeOptions

            @staticmethod
            def get_available_providers():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        class FakeSession:
            @staticmethod
            def get_providers():
                return ["CPUExecutionProvider"]

        class FakeModule:
            ort = FakeOrt()
            os = type("FakeOs", (), {"cpu_count": staticmethod(lambda: 12)})

            @staticmethod
            def load_optional_external_session(_model_path, _weights_path, _providers):
                return FakeSession()

        module = FakeModule()
        _configure_cuda_provider(module)

        with self.assertRaisesRegex(
            OnnxVoiceRuntimeError,
            "未启用 CUDAExecutionProvider",
        ):
            module.load_optional_external_session(
                Path("vits_v2pro.onnx"),
                Path("weights.bin"),
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
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

    def test_native_mixed_frontend_keeps_drive_letter_and_english_word_in_one_sequence(self):
        calls = []

        class FakeEngine:
            def _phones(self, text, language):
                calls.append((text, language))
                phone_id = 1 if language == "zh" else 2
                return (
                    runtime_module.np.asarray([[phone_id]], dtype=runtime_module.np.int64),
                    runtime_module.np.full((1, 4), phone_id, dtype=runtime_module.np.float32),
                )

        class FakeModule:
            np = runtime_module.np
            AimisiOnnx = FakeEngine

            @staticmethod
            def normalize_language(value, _text):
                return value

        module = FakeModule()
        self.assertTrue(_configure_mixed_language_frontend(module))
        self.assertEqual(module.normalize_language("auto", "打开D盘里的nice文件"), "auto")
        phones, bert = FakeEngine()._phones("打开D盘里的nice文件", "auto")

        self.assertEqual(calls, [
            ("打开", "zh"),
            ("D", "en"),
            ("盘里的", "zh"),
            ("nice", "en"),
            ("文件", "zh"),
        ])
        self.assertEqual(phones.tolist(), [[1, 2, 1, 2, 1]])
        self.assertEqual(bert[:, 0].tolist(), [1.0, 2.0, 1.0, 2.0, 1.0])

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
                        [("你好, Aemeath!", "auto")],
                    )
                finally:
                    runtime.close()


if __name__ == "__main__":
    unittest.main()
