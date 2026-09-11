import ctypes
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from lib.core import voice_runtime_contract as contract
from lib.script.gsvmove import native_graph as native_module
from lib.script.gsvmove.native_graph import (
    NativeGraphError,
    NativeGraphSession,
    NativeRuntimeUnavailable,
    _encode_path,
    load_native_library,
)


class _FakeLibrary:
    """Stand-in for the runtime DLL that records every C ABI call."""

    def __init__(self, inputs, outputs, results, resident=(), resident_results=None):
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.results = dict(results)
        self.resident = set(resident)
        self.resident_results = dict(resident_results or {})
        self.resident_declared = None
        self.resident_reads = []
        self.feeds = []
        self.capture_enabled = None
        self.free_count = 0
        self.release_count = 0
        self.created_with = None
        self._keepalive = []

    def fsv_graph_create(self, model_path, external_weights):
        self.created_with = (model_path, external_weights)
        return 1

    def fsv_graph_input_count(self, handle):
        return len(self.inputs)

    def fsv_graph_input_name(self, handle, index):
        return self.inputs[index].encode("utf-8")

    def fsv_graph_output_count(self, handle):
        return len(self.outputs)

    def fsv_graph_output_name(self, handle, index):
        return self.outputs[index].encode("utf-8")

    def fsv_graph_set_capture(self, handle, enabled):
        self.capture_enabled = enabled

    def fsv_graph_set_resident(self, handle, names, count):
        self.resident_declared = [
            (names[index * 2].decode("utf-8"), names[index * 2 + 1].decode("utf-8"))
            for index in range(int(count))
        ]

    def fsv_graph_read_resident(self, handle, name, entries):
        key = name.decode("utf-8")
        self.resident_reads.append(key)
        self._store(entries[0], name, self.resident_results.get(key, self.results[key]))
        return 0

    def _store(self, entry, name, value):
        array = np.ascontiguousarray(value)
        dims = (ctypes.c_int64 * array.ndim)(*array.shape)
        self._keepalive.extend((array, dims))
        entry.name = name if isinstance(name, bytes) else name.encode("utf-8")
        entry.data = ctypes.c_void_p(array.ctypes.data)
        entry.dtype = native_module._ONNX_DTYPE_BY_NUMPY_NAME[array.dtype.name]
        entry.dims = dims
        entry.ndim = array.ndim

    def fsv_graph_free(self, handle):
        self.free_count += 1

    def fsv_graph_last_error(self):
        return b"fake failure"

    def fsv_graph_release_outputs(self, outputs, count):
        self.release_count += int(count)

    def fsv_graph_run(self, handle, tensors, input_count, outputs, output_count):
        feeds = {}
        for index in range(input_count):
            entry = tensors[index]
            shape = tuple(int(entry.dims[offset]) for offset in range(int(entry.ndim)))
            dtype = native_module._NUMPY_DTYPE_BY_ONNX[int(entry.dtype)]
            count = 1
            for dimension in shape:
                count *= dimension
            raw = ctypes.string_at(entry.data, count * dtype.itemsize)
            feeds[entry.name.decode("utf-8")] = (
                np.frombuffer(raw, dtype=dtype, count=count).reshape(shape).copy()
            )
        self.feeds.append(feeds)
        for index, name in enumerate(self.outputs):
            if name in self.resident:
                # A retained output comes back named, shaped and typed, but
                # with no payload: the bytes stayed on the card.
                entry = outputs[index]
                entry.name = name.encode("utf-8")
                self._store(entry, name, self.resident_results.get(name, self.results[name]))
                entry.data = None
                continue
            self._store(outputs[index], name, self.results[name])
        return 0


class NativeGraphSessionTests(unittest.TestCase):
    def _session(self, library, *, capture=False, external=None, weights_exist=False):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "graph.onnx"
            model.write_bytes(b"graph")
            weights = None
            if external is not None:
                weights = Path(tmp) / "graph_fp16.bin"
                if weights_exist:
                    weights.write_bytes(b"weights")
            with patch.object(native_module, "load_native_library", return_value=library):
                session = NativeGraphSession(model, weights, capture=capture)
            return session

    def test_session_exposes_graph_names_in_declaration_order(self):
        library = _FakeLibrary(["x", "y"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        self.assertEqual([item.name for item in session.get_inputs()], ["x", "y"])
        self.assertEqual([item.name for item in session.get_outputs()], ["out"])
        self.assertEqual(session.input_names, ("x", "y"))
        self.assertEqual(session.output_names, ("out",))
        self.assertEqual(session.get_providers(), ["FSVCudaExecutionProvider"])

    def test_run_marshals_inputs_and_copies_outputs(self):
        library = _FakeLibrary(
            ["ids", "mask"],
            ["logits", "count"],
            {
                "logits": np.arange(6, dtype=np.float32).reshape(2, 3),
                "count": np.asarray([7], dtype=np.int64),
            },
        )
        session = self._session(library)
        ids = np.asarray([[1, 2, 3]], dtype=np.int64)
        mask = np.ones((1, 3), dtype=np.float32)
        outputs = session.run(None, {"ids": ids, "mask": mask})

        np.testing.assert_array_equal(library.feeds[0]["ids"], ids)
        np.testing.assert_array_equal(library.feeds[0]["mask"], mask)
        self.assertEqual([item.shape for item in outputs], [(2, 3), (1,)])
        self.assertEqual(outputs[1].dtype, np.int64)
        np.testing.assert_array_equal(outputs[0], library.results["logits"])
        self.assertEqual(library.release_count, 2)

    def test_run_accepts_a_non_contiguous_feed(self):
        library = _FakeLibrary(["ids"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        session.run(None, {"ids": np.arange(12, dtype=np.int64).reshape(3, 4)[:, ::2]})
        self.assertEqual(library.feeds[0]["ids"].shape, (3, 2))
        self.assertTrue(library.feeds[0]["ids"].flags["C_CONTIGUOUS"])

    def test_run_returns_only_requested_outputs(self):
        library = _FakeLibrary(
            ["x"],
            ["a", "b"],
            {"a": np.zeros(2, dtype=np.float32), "b": np.ones(3, dtype=np.float32)},
        )
        session = self._session(library)
        outputs = session.run(["b"], {"x": np.zeros(1, dtype=np.float32)})
        self.assertEqual(len(outputs), 1)
        np.testing.assert_array_equal(outputs[0], library.results["b"])

    def test_run_rejects_unknown_and_missing_inputs(self):
        library = _FakeLibrary(["x", "y"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        with self.assertRaisesRegex(NativeGraphError, "无效的输入张量名"):
            session.run(None, {"x": np.zeros(1), "y": np.zeros(1), "z": np.zeros(1)})
        with self.assertRaisesRegex(NativeGraphError, "缺少输入张量"):
            session.run(None, {"x": np.zeros(1)})
        self.assertEqual(library.feeds, [])

    def test_run_rejects_unsupported_input_dtype(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        with self.assertRaisesRegex(NativeGraphError, "数据类型不受支持"):
            session.run(None, {"x": np.asarray(["text"], dtype=object)})

    def test_run_rejects_unknown_output_name(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        with self.assertRaisesRegex(NativeGraphError, "无效的输出张量名"):
            session.run(["missing"], {"x": np.zeros(1)})

    def test_run_reports_runtime_failure_and_frees_outputs(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        library.fsv_graph_run = lambda *args: 1
        session = self._session(library)
        with self.assertRaisesRegex(NativeGraphError, "fake failure"):
            session.run(None, {"x": np.zeros(1)})
        self.assertEqual(library.release_count, 0)

    def test_close_is_idempotent_and_run_fails_after_close(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        session.close()
        session.close()
        self.assertEqual(library.free_count, 1)
        with self.assertRaisesRegex(NativeGraphError, "已关闭"):
            session.run(None, {"x": np.zeros(1)})

    def test_capture_flag_follows_the_constructor_and_environment(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        self._session(library, capture=True)
        self.assertEqual(library.capture_enabled, 1)

        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        self._session(library)
        self.assertIsNone(library.capture_enabled)
        with patch.dict(os.environ, {"FSV_NATIVE_CAPTURE": "1"}):
            library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
            self._session(library)
        self.assertEqual(library.capture_enabled, 1)

    def test_session_rejects_a_missing_model(self):
        with self.assertRaisesRegex(NativeGraphError, "模型文件不存在"):
            NativeGraphSession(Path("does-not-exist.onnx"))

    def test_external_weights_are_ignored_when_the_file_is_absent(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        session = self._session(library, external="declared", weights_exist=False)
        self.assertIsNone(library.created_with[1])
        self.assertIsNone(session._external_weights)

    def test_external_weights_are_forwarded_when_present(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        session = self._session(library, external="declared", weights_exist=True)
        self.assertIsNotNone(library.created_with[1])
        self.assertEqual(session._external_weights.name, "graph_fp16.bin")


class NativeGraphResidentTests(unittest.TestCase):
    """The KV cache hand-off: an output the host never reads."""

    def _session(self, library):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "decoder.onnx"
            model.write_bytes(b"graph")
            with patch.object(native_module, "load_native_library", return_value=library):
                return NativeGraphSession(model, None)

    def _decoder(self, **kwargs):
        return _FakeLibrary(
            ["token", "past_k"],
            ["logits", "present_k"],
            {
                "logits": np.asarray([[1.0, 2.0]], dtype=np.float32),
                "present_k": np.zeros((1, 4), dtype=np.float32),
            },
            **kwargs,
        )

    def test_output_input_pairs_are_declared_to_the_runtime(self):
        library = self._decoder(resident=("present_k",))
        session = self._session(library)
        self.assertEqual(session.resident_outputs, frozenset({"present_k"}))
        self.assertEqual(library.resident_declared, [("present_k", "past_k")])

    def test_a_graph_without_the_cache_cycle_declares_nothing(self):
        library = _FakeLibrary(["x"], ["out"], {"out": np.zeros(1)})
        session = self._session(library)
        self.assertEqual(session.resident_outputs, frozenset())
        self.assertIsNone(library.resident_declared)

    def test_pairing_needs_both_halves_of_the_name(self):
        self.assertEqual(
            native_module._resident_pairs(
                ["token", "past_k"], ["present_k", "present_v", "logits"]
            ),
            {"present_k": "past_k"},
        )
        self.assertEqual(native_module._resident_pairs(["x"], ["present_k"]), {})
        self.assertEqual(native_module._resident_pairs(["present_k"], ["present_k"]), {})

    def test_run_returns_a_handle_instead_of_bytes(self):
        library = self._decoder(
            resident=("present_k",),
            resident_results={"present_k": np.arange(4, dtype=np.float32).reshape(1, 4)},
        )
        session = self._session(library)
        outputs = session.run(None, {"token": np.asarray([[3]], dtype=np.int64)})
        self.assertIsInstance(outputs[1], native_module.NativeDeviceTensor)
        self.assertEqual(outputs[1].name, "present_k")
        self.assertEqual(outputs[1].shape, (1, 4))
        np.testing.assert_array_equal(outputs[0], library.results["logits"])
        self.assertEqual(library.resident_reads, [])

    def test_the_handle_materialises_on_demand(self):
        library = self._decoder(
            resident=("present_k",),
            resident_results={"present_k": np.arange(4, dtype=np.float32).reshape(1, 4)},
        )
        session = self._session(library)
        outputs = session.run(None, {"token": np.asarray([[3]], dtype=np.int64)})
        np.testing.assert_array_equal(
            np.asarray(outputs[1]), np.arange(4, dtype=np.float32).reshape(1, 4)
        )
        self.assertEqual(library.resident_reads, ["present_k"])

    def test_a_cache_input_is_omitted_but_an_array_is_still_sent(self):
        library = self._decoder(
            resident=("present_k",),
            resident_results={"present_k": np.arange(4, dtype=np.float32).reshape(1, 4)},
        )
        session = self._session(library)
        token = np.asarray([[3]], dtype=np.int64)
        retained = session.run(None, {"token": token})[1]
        session.run(None, {"token": token, "past_k": retained})
        self.assertNotIn("past_k", library.feeds[-1])
        session.run(None, {"token": token, "past_k": np.zeros((1, 4), dtype=np.float32)})
        self.assertIn("past_k", library.feeds[-1])

    def test_a_cache_input_may_be_left_out_entirely(self):
        library = self._decoder(resident=("present_k",))
        session = self._session(library)
        session.run(None, {"token": np.asarray([[3]], dtype=np.int64)})
        self.assertEqual(list(library.feeds[-1]), ["token"])

    def test_the_seeded_cache_still_requires_a_plain_input(self):
        library = self._decoder(resident=("present_k",))
        session = self._session(library)
        with self.assertRaisesRegex(NativeGraphError, "缺少输入张量"):
            session.run(None, {})


class NativeRuntimePathTests(unittest.TestCase):
    def test_dtype_tables_cover_every_declared_type(self):
        self.assertEqual(
            {dtype.name for dtype in native_module._NUMPY_DTYPE_BY_ONNX.values()},
            set(native_module._ONNX_DTYPE_BY_NUMPY_NAME),
        )
        self.assertEqual(native_module._NUMPY_DTYPE_BY_ONNX[7], np.int64)
        self.assertEqual(native_module._ONNX_DTYPE_BY_NUMPY_NAME["int64"], 7)

    def test_candidates_let_the_environment_override_take_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            (root / "build" / "cuda_voice_runtime" / "Release").mkdir(parents=True)
            override_file = Path(tmp) / "custom.dll"
            override_file.write_bytes(b"dll")
            with patch.dict(os.environ, {contract.CUDA_VOICE_RUNTIME_ENV_VAR: str(override_file)}):
                candidates = contract.get_cuda_voice_runtime_candidates(root)
                self.assertEqual(candidates[0], override_file)
                self.assertEqual(contract.resolve_cuda_voice_runtime_path(root), override_file)

    def test_environment_directory_uses_the_default_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            override_dir = Path(tmp) / "runtime"
            override_dir.mkdir()
            with patch.dict(os.environ, {contract.CUDA_VOICE_RUNTIME_ENV_VAR: str(override_dir)}):
                candidates = contract.get_cuda_voice_runtime_candidates(Path(tmp) / "app")
                self.assertEqual(
                    candidates[0],
                    override_dir / contract.CUDA_VOICE_RUNTIME_DLL_NAME,
                )

    def test_development_build_output_is_found_without_any_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            build = root / "build" / "cuda_voice_runtime" / "Release"
            build.mkdir(parents=True)
            (build / contract.CUDA_VOICE_RUNTIME_DLL_NAME).write_bytes(b"dll")
            with patch.dict(os.environ, {contract.CUDA_VOICE_RUNTIME_ENV_VAR: ""}):
                self.assertEqual(
                    contract.resolve_cuda_voice_runtime_path(root),
                    build / contract.CUDA_VOICE_RUNTIME_DLL_NAME,
                )

    def test_bundled_runtime_wins_over_the_development_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            bundled = root.parent / "runtime" / contract.CUDA_VOICE_RUNTIME_DIR_NAME
            bundled.mkdir(parents=True)
            (bundled / contract.CUDA_VOICE_RUNTIME_DLL_NAME).write_bytes(b"dll")
            build = root / "build" / "cuda_voice_runtime" / "Release"
            build.mkdir(parents=True)
            (build / contract.CUDA_VOICE_RUNTIME_DLL_NAME).write_bytes(b"dll")
            with patch.dict(os.environ, {contract.CUDA_VOICE_RUNTIME_ENV_VAR: ""}):
                self.assertEqual(
                    contract.resolve_cuda_voice_runtime_path(root),
                    bundled / contract.CUDA_VOICE_RUNTIME_DLL_NAME,
                )

    def test_resolve_returns_none_when_nothing_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {contract.CUDA_VOICE_RUNTIME_ENV_VAR: ""},
        ), patch.object(
            contract,
            "get_shared_cuda_voice_runtime_path",
            return_value=Path(tmp) / "shared" / "missing.dll",
        ):
            self.assertIsNone(contract.resolve_cuda_voice_runtime_path(Path(tmp) / "app"))

    def test_load_native_library_reports_a_missing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            native_module,
            "resolve_cuda_voice_runtime_path",
            return_value=None,
        ):
            with self.assertRaisesRegex(NativeRuntimeUnavailable, "fsv_cuda_voice_runtime.dll"):
                load_native_library()
            with self.assertRaisesRegex(NativeRuntimeUnavailable, "不存在"):
                load_native_library(Path(tmp) / "missing.dll")

    def test_encode_path_round_trips_through_the_native_code_page(self):
        codec = "mbcs" if os.name == "nt" else "utf-8"
        for raw in ("C:/a/b.dll", "C:/voice/ONNX_aimisiV2/vits_v2pro.onnx"):
            path = Path(raw)
            self.assertEqual(_encode_path(path).decode(codec), os.fspath(path))


if __name__ == "__main__":
    unittest.main()
