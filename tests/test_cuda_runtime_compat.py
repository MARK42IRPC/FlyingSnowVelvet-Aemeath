"""The CUDA runtime's compatibility promises, and where each one is pinned.

Which PTX target ships, which cards device selection accepts, how a refusal is
explained and how a host asks which card was picked are claims that only hold
while the build files, the C ABI, the ctypes binding and both documents agree.
Each test below ties one claim to its source of truth, so changing a number in
one place fails here instead of shipping a mismatch.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_NATIVE = _ROOT / "native" / "cuda_voice_runtime"
_DOC = _ROOT / "doc" / "自研CUDA极简推理端.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class PtxTargetTests(unittest.TestCase):
    """One DLL has to run on every card the driver can still JIT for."""

    def test_cmake_default_target_covers_pascal(self):
        match = re.search(r'set\(FSV_PTX_ARCH "([^"]+)"\)', _read(_NATIVE / "CMakeLists.txt"))
        self.assertIsNotNone(match, "FSV_PTX_ARCH default disappeared from CMakeLists.txt")
        # compute_75 cannot be JIT-compiled for Pascal, which locked GTX 10xx out.
        self.assertEqual(match.group(1), "compute_61")

    def test_build_script_agrees_with_the_cmake_default(self):
        self.assertIn(
            'set "FSV_PTX_ARCH=compute_61"',
            _read(_NATIVE / "tools" / "build_windows.cmd"),
        )

    def test_runtime_reports_the_target_it_was_built_for(self):
        self.assertIn(
            'FSV_PTX_ARCH_TARGET="${FSV_PTX_ARCH}"',
            _read(_NATIVE / "CMakeLists.txt"),
        )
        # A load failure has to name the target, or "driver too old" is
        # indistinguishable from "PTX built for a card you do not have".
        self.assertIn("FSV_PTX_ARCH_TARGET", _read(_NATIVE / "src" / "host" / "nv_runtime.cpp"))


class DeviceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _read(_NATIVE / "src" / "host" / "nv_runtime.cpp")

    def test_memory_floor_matches_the_documented_figure(self):
        self.assertIn(
            "const std::size_t kMinDeviceMemoryBytes = 3ull << 30;",
            self.runtime,
        )

    def test_selection_weighs_memory_and_honours_an_explicit_card(self):
        self.assertIn("cuDeviceTotalMem(&total, device)", self.runtime)
        self.assertIn("requested_device_index()", self.runtime)
        self.assertIn("AEMEATH_CUDA_VOICE_DEVICE", self.runtime)

    def test_recycle_pool_budget_is_a_share_of_the_card(self):
        self.assertIn("pool_limit_for_memory", self.runtime)
        self.assertIn("pool_pressure_limit_for_memory", self.runtime)
        self.assertIn("total_bytes / 5", self.runtime)

    def test_refusal_lists_the_cards_it_saw(self):
        self.assertIn("list_devices(api_, count)", self.runtime)
        # Enumerating cards must not need a usable one, otherwise the reason a
        # machine has no device cannot be reported.
        self.assertIn("bool NvRuntime::driver_ready", self.runtime)


class DeviceQuerySurfaceTests(unittest.TestCase):
    def test_header_declares_the_active_device_query(self):
        self.assertIn(
            "FSV_CUDA_API int fsv_cuda_active_device(char* name, size_t name_size);",
            _read(_NATIVE / "include" / "fsv_cuda_voice_runtime.h"),
        )

    def test_smoke_test_exercises_the_new_entry_point(self):
        self.assertIn(
            "fsv_cuda_active_device(",
            _read(_NATIVE / "tools" / "fsv_engine_smoke.cpp"),
        )

    def test_ctypes_binding_exposes_the_device_queries(self):
        module = _read(_ROOT / "lib" / "script" / "gsvmove" / "native_graph.py")
        self.assertIn('"fsv_cuda_active_device"', module)
        self.assertIn('"fsv_cuda_get_device_info"', module)
        self.assertIn('"native_active_device"', module)
        self.assertIn('"native_device_info"', module)


class DeviceOperatorSurfaceTests(unittest.TestCase):
    """The operators that used to run on the host and read their operand back.

    Each one is pinned in four places at once -- the C entry point, the kernel,
    the graph runtime's dispatch and the smoke test -- and a path that is only
    wired in three of them silently falls back to the host loop it replaced.
    """

    def setUp(self):
        self.header = _read(_NATIVE / "include" / "fsv_cuda_voice_runtime.h")
        self.kernels = _read(_NATIVE / "src" / "kernels" / "fsv_kernels.cu")
        self.runtime = _read(_NATIVE / "src" / "host" / "graph_runtime.cpp")
        self.smoke = _read(_NATIVE / "tools" / "fsv_engine_smoke.cpp")

    def test_new_entries_are_public(self):
        for name in ("fsv_cuda_argmax_i64", "fsv_cuda_instance_norm_f32",
                     "fsv_cuda_scatter_elements_f32"):
            self.assertIn(f"FSV_CUDA_API int {name}(", self.header)

    def test_kernels_back_every_entry(self):
        for name in ("fsv_argmax_i64", "fsv_instance_norm_f32",
                     "fsv_scatter_elements_f32"):
            self.assertIn(f"void {name}(", self.kernels)

    def test_graph_runtime_prefers_the_device_path(self):
        for name in ("device_argmax", "device_instance_norm", "device_pad",
                     "device_scatter_elements"):
            self.assertIn(f"bool {name}(", self.runtime)

    def test_smoke_test_has_a_case_per_path(self):
        for name in ("argmax_dev", "instance_norm_dev", "pad_constant_dev",
                     "scatter_elements_dev"):
            self.assertIn(f'"{name}"', self.smoke)

    def test_documents_list_the_device_paths(self):
        doc = _read(_DOC)
        for name in ("fsv_cuda_argmax_i64", "fsv_cuda_instance_norm_f32"):
            self.assertIn(name, doc)
        self.assertIn("设备快路径与前提", _read(_NATIVE / "README.md"))


class CompatibilityDocumentationTests(unittest.TestCase):
    def test_documents_name_the_shipped_ptx_target(self):
        self.assertIn("`compute_61`", _read(_DOC))

    def test_documents_explain_how_to_pick_a_card(self):
        self.assertIn("AEMEATH_CUDA_VOICE_DEVICE", _read(_DOC))
        self.assertIn("AEMEATH_CUDA_VOICE_DEVICE", _read(_NATIVE / "README.md"))

    def test_documents_state_the_memory_floor(self):
        self.assertIn("3 GiB", _read(_DOC))
        self.assertIn("3 GiB", _read(_NATIVE / "README.md"))


if __name__ == "__main__":
    unittest.main()
