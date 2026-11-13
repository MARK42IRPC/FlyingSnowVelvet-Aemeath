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
    native_active_device,
    native_device_info,
)


class _FakeLibrary:
    """Stand-in for the runtime DLL that records every C ABI call."""

    def __init__(self, inputs, outputs, results, resident=(), resident_results=None):
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.results = dict(results)
        self.resident = set(resident)
        self.retained = set()
        self.lendable = True
        self.resident_results = dict(resident_results or {})
        self.resident_declared = None
        self.retained_declared = None
        self.borrows = []
        self.imports = []
        self.released = []
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

    def fsv_graph_set_retained(self, handle, names, count):
        self.retained_declared = [
            names[index].decode("utf-8") for index in range(int(count))
        ]
        self.retained.update(self.retained_declared)

    def fsv_graph_borrow_device(self, handle, name):
        key = name.decode("utf-8")
        self.borrows.append(key)
        # Only a value that was declared retained and is still on the card has
        # anything to lend; anything else sends the caller back to the bytes.
        if key not in self.retained or not self.lendable:
            return None
        return id(self) + len(self.borrows)

    def fsv_graph_import_device(self, handle, name, borrowed):
        self.imports.append((name.decode("utf-8"), borrowed))
        return 0

    def fsv_graph_release_device(self, borrowed):
        self.released.append(borrowed)

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
            if name in self.resident or name in self.retained:
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


class NativeGraphDeviceLoanTests(unittest.TestCase):
    """The cache handed from one graph to the next one."""

    def _session(self, library, name="graph.onnx"):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / name
            model.write_bytes(b"graph")
            with patch.object(native_module, "load_native_library", return_value=library):
                return NativeGraphSession(model, None)

    def _producer(self):
        """The half that produces a cache and never reads it back."""

        return _FakeLibrary(
            ["x"],
            ["y", "present_k"],
            {
                "y": np.zeros((1, 2), dtype=np.float32),
                "present_k": np.zeros((1, 4), dtype=np.float32),
            },
        )

    def _consumer(self, suffix="k"):
        """The half that keeps the cache it is handed."""

        past = f"past_{suffix}"
        present = f"present_{suffix}"
        return _FakeLibrary(
            ["token", past],
            ["logits", present],
            {
                "logits": np.asarray([[1.0, 2.0]], dtype=np.float32),
                present: np.zeros((1, 4), dtype=np.float32),
            },
        )

    def test_a_cache_with_no_paired_input_is_declared_retained(self):
        library = self._producer()
        session = self._session(library, "first_stage.onnx")
        self.assertEqual(session.retained_outputs, ("present_k",))
        self.assertEqual(library.retained_declared, ["present_k"])
        self.assertEqual(session.resident_outputs, frozenset())
        self.assertIsNone(library.resident_declared)

    def test_run_returns_the_retained_cache_as_a_handle(self):
        library = self._producer()
        session = self._session(library, "first_stage.onnx")
        outputs = session.run(None, {"x": np.zeros((1, 1), dtype=np.float32)})
        self.assertIsInstance(outputs[1], native_module.NativeDeviceTensor)
        self.assertEqual(library.resident_reads, [])

    def test_a_borrowed_buffer_is_imported_and_given_back(self):
        producer = self._producer()
        consumer = self._consumer()
        first = self._session(producer, "first_stage.onnx")
        stage = self._session(consumer, "stage.onnx")
        cache = first.run(None, {"x": np.zeros((1, 1), dtype=np.float32)})[1]
        token = np.asarray([[3]], dtype=np.int64)
        stage.run(None, {"token": token, "past_k": cache})
        # The buffer is bound to the name the consumer reads it as, which is
        # what makes the run pick it up instead of asking for bytes.
        self.assertEqual([name for name, _ in consumer.imports], ["past_k"])
        self.assertEqual(consumer.released, [consumer.imports[0][1]])
        # The bytes never crossed to the host: no upload, no read-back.
        self.assertNotIn("past_k", consumer.feeds[-1])
        self.assertEqual(producer.resident_reads, [])

    def test_a_cache_without_a_device_copy_is_sent_as_bytes(self):
        producer = self._producer()
        producer.lendable = False
        consumer = self._consumer()
        first = self._session(producer, "first_stage.onnx")
        stage = self._session(consumer, "stage.onnx")
        cache = first.run(None, {"x": np.zeros((1, 1), dtype=np.float32)})[1]
        stage.run(None, {"token": np.asarray([[3]], dtype=np.int64), "past_k": cache})
        self.assertEqual(consumer.imports, [])
        self.assertEqual(consumer.released, [])
        self.assertIn("past_k", consumer.feeds[-1])
        self.assertEqual(producer.resident_reads, ["present_k"])

    def test_a_handle_only_travels_to_the_input_it_pairs_with(self):
        producer = self._producer()
        consumer = self._consumer(suffix="v")
        first = self._session(producer, "first_stage.onnx")
        stage = self._session(consumer, "stage.onnx")
        cache = first.run(None, {"x": np.zeros((1, 1), dtype=np.float32)})[1]
        stage.run(None, {"token": np.asarray([[3]], dtype=np.int64), "past_v": cache})
        self.assertEqual(consumer.imports, [])
        self.assertEqual(producer.resident_reads, ["present_k"])


class NativeGraphStepFeedTests(unittest.TestCase):
    """The decoder handing its own previous step straight back in."""

    def _session(self, library):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "stage.onnx"
            model.write_bytes(b"graph")
            with patch.object(native_module, "load_native_library", return_value=library):
                return NativeGraphSession(model, None)

    def _stage(self):
        return _FakeLibrary(
            ["iy", "iy_emb", "past_k"],
            ["y", "y_emb", "present_k"],
            {
                "y": np.asarray([[3]], dtype=np.int64),
                "y_emb": np.zeros((1, 2, 4), dtype=np.float32),
                "present_k": np.zeros((1, 4), dtype=np.float32),
            },
            resident={"y", "y_emb", "present_k"},
        )

    def _first_step(self, session):
        return session.run(
            None,
            {
                "iy": np.asarray([[3]], dtype=np.int64),
                "iy_emb": np.zeros((1, 2, 4), dtype=np.float32),
                "past_k": np.zeros((1, 4), dtype=np.float32),
            },
        )

    def test_the_step_is_declared_next_to_the_cache(self):
        library = self._stage()
        session = self._session(library)
        self.assertEqual(
            session.resident_outputs, frozenset({"y", "y_emb", "present_k"})
        )
        self.assertEqual(
            sorted(library.resident_declared),
            [("present_k", "past_k"), ("y", "iy"), ("y_emb", "iy_emb")],
        )
        self.assertEqual(library.retained_declared, None)

    def test_the_step_pair_needs_the_cache_to_look_like_a_decoder(self):
        self.assertEqual(native_module._resident_pairs(["iy"], ["y"]), {})
        self.assertEqual(
            native_module._resident_pairs(
                ["token", "past_k", "iy"], ["logits", "present_k", "y"]
            ),
            {"present_k": "past_k", "y": "iy"},
        )

    def test_handing_the_step_back_keeps_it_on_the_card(self):
        library = self._stage()
        session = self._session(library)
        outputs = self._first_step(session)
        self.assertIsInstance(outputs[0], native_module.NativeDeviceTensor)
        session.run(None, {"iy": outputs[0], "iy_emb": outputs[1], "past_k": outputs[2]})
        self.assertEqual(list(library.feeds[-1]), [])
        self.assertEqual(library.resident_reads, [])

    def test_a_step_that_is_read_still_materialises(self):
        library = self._stage()
        session = self._session(library)
        outputs = self._first_step(session)
        self.assertEqual(outputs[0][:, 0].tolist(), [3])
        self.assertEqual(library.resident_reads, ["y"])


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


class _FakeDeviceLibrary:
    """Stand-in for the device-query half of the C ABI.

    ``devices`` is a list of dicts (name, major, minor, memory) and ``active``
    is what ``fsv_cuda_active_device`` should answer, so a test can describe a
    machine the runtime refused as easily as one it accepted.
    """

    def __init__(self, devices=(), active=-1, active_name=""):
        self.devices = list(devices)
        self.active = active
        self.active_name = active_name
        self.queried = []

    def fsv_cuda_device_count(self):
        return len(self.devices)

    def fsv_cuda_get_device_info(self, index, out):
        self.queried.append(index)
        if index < 0 or index >= len(self.devices):
            return 2
        entry = self.devices[index]
        out.index = index
        out.major = entry["major"]
        out.minor = entry["minor"]
        out.global_memory_bytes = entry["memory"]
        out.name = entry["name"].encode("utf-8")
        return 0

    def fsv_cuda_active_device(self, buffer, size):
        if self.active < 0:
            return -1
        buffer.value = self.active_name.encode("utf-8")
        return self.active


class NativeDeviceQueryTests(unittest.TestCase):
    def _library(self, *args, **kwargs):
        library = _FakeDeviceLibrary(*args, **kwargs)
        return library, patch.object(native_module, "load_native_library", return_value=library)

    def test_device_info_reports_name_capability_and_memory(self):
        library, patcher = self._library(
            devices=[{"name": "NVIDIA GeForce RTX 3050 Laptop GPU", "major": 8, "minor": 6,
                      "memory": 4294443008}]
        )
        with patcher:
            info = native_device_info(0)
        self.assertEqual(library.queried, [0])
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "NVIDIA GeForce RTX 3050 Laptop GPU")
        self.assertEqual(info.capability, "8.6")
        self.assertAlmostEqual(info.memory_gib, 4.0, places=1)

    def test_device_info_returns_none_for_an_unknown_index(self):
        library, patcher = self._library(
            devices=[{"name": "NVIDIA GeForce GTX 1050", "major": 6, "minor": 1,
                      "memory": 2147483648}]
        )
        with patcher:
            self.assertIsNone(native_device_info(3))
        self.assertEqual(library.queried, [3])

    def test_active_device_reports_the_selected_card(self):
        _, patcher = self._library(
            devices=[{"name": "NVIDIA GeForce RTX 3050 Laptop GPU", "major": 8, "minor": 6,
                      "memory": 4294443008}],
            active=0,
            active_name="NVIDIA GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)",
        )
        with patcher:
            index, description = native_active_device()
        self.assertEqual(index, 0)
        self.assertEqual(description, "NVIDIA GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)")

    def test_active_device_maps_a_refusal_to_minus_one(self):
        _, patcher = self._library(devices=[], active=-1)
        with patcher:
            self.assertEqual(native_active_device(), (-1, ""))


if __name__ == "__main__":
    unittest.main()
