"""Drive the self-written CUDA runtime with an ONNX-Runtime-shaped session.

``native/cuda_voice_runtime`` executes ONNX graphs with nothing but the NVIDIA
display driver: no CUDA toolkit, no ONNX Runtime, no cuBLAS.  This module binds
its ``fsv_graph_*`` C ABI through :mod:`ctypes` and presents it as a session
object that answers ``run``/``get_inputs``/``get_outputs`` exactly like the
``onnxruntime.InferenceSession`` the voice archive already calls.

The archive keeps owning everything that is not a graph forward pass: text
normalisation, jieba/pypinyin, the sampling loop and the WAV writer all stay in
the package's own Python.  Only ``run``, ``get_inputs`` and ``get_outputs`` are
implemented here, which is all that Python consumes.

Feeds must be NumPy arrays whose dtype matches the graph input: the C ABI takes
the element type from the caller instead of the graph, so a mismatched array is
reinterpreted rather than rejected.  Every feed produced by the pinned voice
archive is already an explicitly typed ``np.asarray``/``np.zeros`` result.
"""

from __future__ import annotations

import ctypes
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lib.core.voice_runtime_contract import resolve_cuda_voice_runtime_path


class NativeGraphError(RuntimeError):
    pass


class NativeRuntimeUnavailable(NativeGraphError):
    pass


_ONNX_DTYPE_BY_NUMPY_NAME = {
    "float32": 1,
    "uint8": 2,
    "int8": 3,
    "uint16": 4,
    "int16": 5,
    "int32": 6,
    "int64": 7,
    "bool": 9,
    "float16": 10,
    "float64": 11,
    "uint32": 12,
    "uint64": 13,
}
_NUMPY_DTYPE_BY_ONNX = {
    code: np.dtype(name) for name, code in _ONNX_DTYPE_BY_NUMPY_NAME.items()
}


class _GraphTensor(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("data", ctypes.c_void_p),
        ("dtype", ctypes.c_int32),
        ("dims", ctypes.POINTER(ctypes.c_int64)),
        ("ndim", ctypes.c_int32),
    ]


class _GraphOutput(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("data", ctypes.c_void_p),
        ("dtype", ctypes.c_int32),
        ("dims", ctypes.POINTER(ctypes.c_int64)),
        ("ndim", ctypes.c_int32),
    ]


class _CudaDeviceInfo(ctypes.Structure):
    """Mirror of ``fsv_cuda_device_info``."""

    _fields_ = [
        ("index", ctypes.c_int),
        ("major", ctypes.c_int),
        ("minor", ctypes.c_int),
        ("global_memory_bytes", ctypes.c_size_t),
        ("name", ctypes.c_char * 256),
    ]


@dataclass(frozen=True)
class NativeDeviceInfo:
    """One NVIDIA card as the runtime sees it.

    The "NVIDIA acceleration" switch is only useful if the card behind it can be
    named, so ``name``, ``capability`` and ``memory_gib`` are what the settings
    and log surfaces report.
    """

    index: int
    name: str
    major: int
    minor: int
    memory_bytes: int

    @property
    def capability(self) -> str:
        return f"{self.major}.{self.minor}"

    @property
    def memory_gib(self) -> float:
        return self.memory_bytes / 1073741824.0


@dataclass(frozen=True)
class NativeTensorInfo:
    """The subset of ``onnxruntime.NodeArg`` the voice archive reads.

    ``shape`` and ``type`` stay ``None``: the C ABI exposes names only, and the
    archive uses these objects purely to recover graph input names and order.
    """

    name: str
    shape: tuple[int, ...] | None = None
    type: str | None = None


def _encode_path(path) -> bytes:
    """Encode a path the way the runtime's ``std::ifstream`` will decode it.

    Windows narrow file APIs interpret byte paths with the active code page, so
    UTF-8 would corrupt a non-ASCII path on a GBK machine and vice versa.
    """

    text = os.fspath(path)
    if os.name == "nt":
        try:
            return text.encode("mbcs")
        except (LookupError, UnicodeEncodeError):
            pass
    return text.encode("utf-8")


_LIBRARIES: dict[str, ctypes.CDLL] = {}
_LIBRARIES_LOCK = threading.Lock()


def _bind(library: ctypes.CDLL, name: str, argtypes, restype) -> None:
    function = getattr(library, name)
    function.argtypes = argtypes
    function.restype = restype


def _optional_binding(library: ctypes.CDLL, name: str, argtypes, restype):
    """Bind an entry point a runtime built before it does not export.

    The driver and the graph runtime ship as one file, but a DLL left behind by
    an earlier install is a real situation: a feature it cannot support is
    skipped rather than turning the whole runtime into a load failure.
    """

    function = getattr(library, name, None)
    if function is None:
        return None
    function.argtypes = argtypes
    function.restype = restype
    return function


def load_native_library(library_path=None) -> ctypes.CDLL:
    """Load and cache the runtime DLL declared by the shared contract."""

    resolved = resolve_cuda_voice_runtime_path() if library_path is None else Path(library_path)
    if resolved is None:
        raise NativeRuntimeUnavailable(
            "未找到自研 CUDA 推理端 fsv_cuda_voice_runtime.dll，请重新安装或更新程序"
        )
    resolved = Path(resolved)
    if not resolved.is_file():
        raise NativeRuntimeUnavailable(f"自研 CUDA 推理端不存在：{resolved}")
    key = str(resolved)
    with _LIBRARIES_LOCK:
        cached = _LIBRARIES.get(key)
        if cached is not None:
            return cached
        try:
            library = ctypes.CDLL(key)
        except OSError as exc:
            raise NativeRuntimeUnavailable(f"加载自研 CUDA 推理端失败：{exc}") from exc
        _bind(library, "fsv_cuda_device_count", [], ctypes.c_int)
        _bind(
            library,
            "fsv_cuda_get_device_info",
            [ctypes.c_int, ctypes.POINTER(_CudaDeviceInfo)],
            ctypes.c_int,
        )
        _bind(
            library,
            "fsv_cuda_active_device",
            [ctypes.c_char_p, ctypes.c_size_t],
            ctypes.c_int,
        )
        _bind(library, "fsv_graph_create", [ctypes.c_char_p, ctypes.c_char_p], ctypes.c_void_p)
        _bind(library, "fsv_graph_free", [ctypes.c_void_p], None)
        _bind(library, "fsv_graph_input_count", [ctypes.c_void_p], ctypes.c_int)
        _bind(library, "fsv_graph_output_count", [ctypes.c_void_p], ctypes.c_int)
        _bind(library, "fsv_graph_input_name", [ctypes.c_void_p, ctypes.c_int], ctypes.c_char_p)
        _bind(library, "fsv_graph_output_name", [ctypes.c_void_p, ctypes.c_int], ctypes.c_char_p)
        _bind(
            library,
            "fsv_graph_run",
            [
                ctypes.c_void_p,
                ctypes.POINTER(_GraphTensor),
                ctypes.c_size_t,
                ctypes.POINTER(_GraphOutput),
                ctypes.c_size_t,
            ],
            ctypes.c_int,
        )
        _bind(library, "fsv_graph_release_outputs", [ctypes.POINTER(_GraphOutput), ctypes.c_size_t], None)
        _bind(library, "fsv_graph_set_capture", [ctypes.c_void_p, ctypes.c_int], None)
        _bind(
            library,
            "fsv_graph_set_resident",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_int],
            None,
        )
        _bind(
            library,
            "fsv_graph_read_resident",
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(_GraphOutput)],
            ctypes.c_int,
        )
        # Device lending. A DLL left behind by an earlier install does not
        # export these, and the feature is skipped rather than turning the
        # whole runtime into a load failure.
        _optional_binding(
            library,
            "fsv_graph_set_retained",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_int],
            None,
        )
        _optional_binding(
            library,
            "fsv_graph_borrow_device",
            [ctypes.c_void_p, ctypes.c_char_p],
            ctypes.c_void_p,
        )
        _optional_binding(library, "fsv_graph_release_device", [ctypes.c_void_p], None)
        _optional_binding(
            library,
            "fsv_graph_import_device",
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p],
            ctypes.c_int,
        )
        _bind(library, "fsv_graph_last_error", [], ctypes.c_char_p)
        _LIBRARIES[key] = library
        return library


def _last_error(library: ctypes.CDLL) -> str:
    raw = library.fsv_graph_last_error()
    if not raw:
        return "未知错误"
    return raw.decode("utf-8", "replace")


def native_device_count() -> int:
    """Return the device count the self-written runtime sees."""

    return int(load_native_library().fsv_cuda_device_count())


def native_device_info(index: int) -> NativeDeviceInfo | None:
    """Return one card's name, capability and memory, or ``None`` on failure.

    Unlike :func:`native_device_count` this does not require a usable card: the
    runtime binds the driver only, so every card can be listed even when none of
    them is big enough to be selected.
    """

    raw = _CudaDeviceInfo()
    library = load_native_library()
    # A struct instance is passed by reference for a POINTER() argument, which
    # also keeps the call usable with the plain-object double the tests use.
    if int(library.fsv_cuda_get_device_info(int(index), raw)) != 0:
        return None
    return NativeDeviceInfo(
        index=int(raw.index),
        name=raw.name.decode("utf-8", "replace"),
        major=int(raw.major),
        minor=int(raw.minor),
        memory_bytes=int(raw.global_memory_bytes),
    )


def native_active_device() -> tuple[int, str]:
    """Return the index and description of the card the runtime selected.

    ``(-1, "")`` means there is no usable card and :func:`native_last_error`
    explains why. The description reads like
    ``NVIDIA GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)``.
    """

    buffer = ctypes.create_string_buffer(320)
    index = int(load_native_library().fsv_cuda_active_device(buffer, len(buffer)))
    if index < 0:
        return -1, ""
    return index, buffer.value.decode("utf-8", "replace")


def native_last_error() -> str:
    """Return the runtime's last driver-level error message."""

    return _last_error(load_native_library())


def _decode_name(raw: bytes | None) -> str:
    return raw.decode("utf-8", "replace") if raw else ""


_RESIDENT_OUTPUT_PREFIX = "present_"
_RESIDENT_INPUT_PREFIX = "past_"
# Besides its cache the decoder hands the token it just produced, and the
# embedding that goes with it, straight back in on the next step. Neither name
# says anything about the other, so the pair is listed here: the archive is the
# only place that defines the convention.
_DECODER_STEP_PAIRS = (("y", "iy"), ("y_emb", "iy_emb"))


def _resident_pairs(input_names, output_names) -> dict[str, str]:
    """Find outputs that are only ever handed back to the next run.

    An autoregressive decoder names its cache ``past_*`` on the way in and
    ``present_*`` on the way out, and nothing on the host ever reads those
    tensors: the decode loop only feeds them forward. Pairing the names lets the
    runtime keep the buffer on the card and skip both the read-back and the
    re-upload, which for this archive is a third of all transfer volume.

    The rule is inert for every graph without that cycle, because it needs both
    halves of the name to be present.

    The step pair below rides on the same discovery: a graph that names its own
    previous step is the graph that names a cache, so the cache is what says
    the archive is a decoder before anything else is assumed about it.
    """

    available = set(input_names)
    pairs: dict[str, str] = {}
    for name in output_names:
        if not name.startswith(_RESIDENT_OUTPUT_PREFIX):
            continue
        source = _RESIDENT_INPUT_PREFIX + name[len(_RESIDENT_OUTPUT_PREFIX) :]
        if source in available:
            pairs[name] = source
    if not pairs:
        return pairs
    outputs = set(output_names)
    for output, source in _DECODER_STEP_PAIRS:
        if output in outputs and source in available:
            pairs[output] = source
    return pairs


def _retained_outputs(pairs: dict[str, str], output_names) -> tuple[str, ...]:
    """Outputs nobody on the host reads and no input name pairs with.

    The first half of an autoregressive decoder is the case this exists for: it
    produces the cache the second half consumes, so the value only has to reach
    the next graph. Leaving it on the card saves the read-back and the upload
    that would otherwise carry that cache to the host and straight back to the
    same card, which for a long text is gigabytes of traffic nobody looks at.
    """

    return tuple(
        name
        for name in output_names
        if name.startswith(_RESIDENT_OUTPUT_PREFIX) and name not in pairs
    )


class NativeDeviceTensor:
    """An output that stayed on the card instead of coming back as bytes.

    The runtime reports such an output with a null payload, and this object is
    what the decode loop passes on. It converts to a real array on demand, so a
    caller that does look at the value still gets the same bytes for the price
    of one read-back.
    """

    __slots__ = ("_session", "_name", "_shape", "_dtype")

    def __init__(self, session, name, shape, dtype) -> None:
        self._session = session
        self._name = name
        self._shape = shape
        self._dtype = dtype

    @property
    def name(self) -> str:
        return self._name

    @property
    def owner(self) -> "NativeGraphSession":
        """The session whose device buffer this handle names."""

        return self._session

    def borrow(self):
        """Lend this output's device buffer to another graph.

        Returns ``None`` when the value has no live device copy, which sends
        the caller back to :meth:`numpy`.
        """

        return self._session.borrow_device(self._name)

    @property
    def shape(self) -> tuple[int, ...]:
        return self._shape

    @property
    def dtype(self):
        return self._dtype

    def numpy(self) -> np.ndarray:
        """Read the retained device buffer back to the host."""

        return self._session.read_resident(self._name)

    def __getitem__(self, key):
        """Index the value as the array it stands for; see :meth:`numpy`."""

        return self.numpy()[key]

    def __array__(self, dtype=None, copy=None) -> np.ndarray:
        array = self.numpy()
        if dtype is not None:
            array = array.astype(dtype, copy=False)
        return array

    def __repr__(self) -> str:
        return f"<NativeDeviceTensor {self._name} {self._shape} {self._dtype.name}>"


class NativeGraphSession:
    """One parsed ONNX graph executed by the self-written CUDA runtime."""

    def __init__(
        self,
        model_path,
        external_weights=None,
        *,
        library_path=None,
        capture: bool = False,
    ) -> None:
        self._model_path = Path(model_path)
        if not self._model_path.is_file():
            raise NativeGraphError(f"模型文件不存在：{self._model_path}")
        weights = Path(external_weights) if external_weights else None
        if weights is not None and not weights.is_file():
            weights = None
        self._external_weights = weights
        library = load_native_library(library_path)
        handle = library.fsv_graph_create(
            _encode_path(self._model_path),
            _encode_path(weights) if weights is not None else None,
        )
        if not handle:
            raise NativeGraphError(
                f"无法加载 ONNX 图 {self._model_path.name}：{_last_error(library)}"
            )
        self._library = library
        self._handle = handle
        self._lock = threading.RLock()
        if capture or os.environ.get("FSV_NATIVE_CAPTURE"):
            library.fsv_graph_set_capture(handle, 1)
        self._input_names = tuple(
            _decode_name(library.fsv_graph_input_name(handle, index))
            for index in range(int(library.fsv_graph_input_count(handle)))
        )
        self._output_names = tuple(
            _decode_name(library.fsv_graph_output_name(handle, index))
            for index in range(int(library.fsv_graph_output_count(handle)))
        )
        self._resident_pairs = _resident_pairs(self._input_names, self._output_names)
        self._resident_inputs = frozenset(self._resident_pairs.values())
        self._resident_outputs = frozenset(self._resident_pairs)
        # Bound by load_native_library, and left unset by a runtime too old to
        # export them; the retention below is what the feature hangs on.
        self._set_retained = getattr(library, "fsv_graph_set_retained", None)
        self._borrow_device = getattr(library, "fsv_graph_borrow_device", None)
        self._release_device = getattr(library, "fsv_graph_release_device", None)
        self._import_device = getattr(library, "fsv_graph_import_device", None)
        self._retained_outputs = (
            _retained_outputs(self._resident_pairs, self._output_names)
            if self._set_retained is not None
            else ()
        )
        self._device_outputs = self._resident_outputs | frozenset(self._retained_outputs)
        if self._retained_outputs:
            encoded = [name.encode("utf-8") for name in self._retained_outputs]
            retained_names = (ctypes.c_char_p * len(encoded))(*encoded)
            self._set_retained(handle, retained_names, len(encoded))
        if self._resident_pairs:
            flat: list[bytes] = []
            for output, source in sorted(self._resident_pairs.items()):
                flat.extend((output.encode("utf-8"), source.encode("utf-8")))
            names = (ctypes.c_char_p * len(flat))(*flat)
            library.fsv_graph_set_resident(handle, names, len(self._resident_pairs))

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def resident_outputs(self) -> frozenset[str]:
        """Output names that stay on the card between runs."""

        return self._resident_outputs

    @property
    def retained_outputs(self) -> tuple[str, ...]:
        """Output names kept on the card for another graph to read."""

        return self._retained_outputs

    def borrow_device(self, name: str):
        """Lend one retained output's device buffer to another graph.

        Returns the runtime's borrow handle, or ``None`` when that value has no
        live device copy (an operator left it on the host) or the runtime is
        too old to lend one: the caller then goes through the bytes like any
        other array.
        """

        with self._lock:
            handle = self._handle
            if handle is None or self._borrow_device is None:
                return None
            return self._borrow_device(handle, str(name).encode("utf-8")) or None

    def release_device(self, borrowed) -> None:
        """Give a borrowed device buffer back; see :meth:`borrow_device`."""

        if borrowed and self._release_device is not None:
            self._release_device(borrowed)

    @property
    def input_names(self) -> tuple[str, ...]:
        return self._input_names

    @property
    def output_names(self) -> tuple[str, ...]:
        return self._output_names

    def get_inputs(self) -> list[NativeTensorInfo]:
        return [NativeTensorInfo(name) for name in self._input_names]

    def get_outputs(self) -> list[NativeTensorInfo]:
        return [NativeTensorInfo(name) for name in self._output_names]

    def get_providers(self) -> list[str]:
        return ["FSVCudaExecutionProvider"]

    def run(self, output_names=None, input_feed=None, run_options=None):
        """Run the graph; mirrors ``onnxruntime.InferenceSession.run``."""

        del run_options
        with self._lock:
            handle = self._handle
            if handle is None:
                raise NativeGraphError("自研 CUDA 推理会话已关闭")
            feed = dict(input_feed or {})
            unknown = [name for name in feed if name not in self._input_names]
            if unknown:
                raise NativeGraphError("无效的输入张量名：" + ", ".join(sorted(map(str, unknown))))
            missing = [
                name
                for name in self._input_names
                if name not in feed and name not in self._resident_inputs
            ]
            if missing:
                raise NativeGraphError("缺少输入张量：" + ", ".join(missing))

            # A retained cache tensor is named but not sent: the runtime reads
            # its own device buffer for that input, so leaving it out of the
            # feed list is what makes it skip the upload. A tensor another
            # graph retained travels the same way once its buffer has been
            # bound here, which is what keeps a cache produced by the first
            # stage from a round trip through host memory.
            supplied: list[str] = []
            loans: list[tuple[str, object]] = []
            for name in self._input_names:
                if name not in feed:
                    continue
                value = feed[name]
                if isinstance(value, NativeDeviceTensor):
                    if value.owner is self:
                        if name in self._resident_inputs:
                            continue
                    elif (
                        name in self._resident_inputs
                        and self._resident_pairs.get(value.name) == name
                    ):
                        borrowed = value.borrow()
                        if borrowed is not None:
                            # The buffer is bound to the name this graph reads
                            # it as, which is not the name the other graph
                            # produced it under.
                            loans.append((name, borrowed))
                            continue
                supplied.append(name)
            for input_name, borrowed in loans:
                encoded_name = input_name.encode("utf-8")
                if int(self._import_device(handle, encoded_name, borrowed)) != 0:
                    raise NativeGraphError(
                        f"绑定借用的设备缓冲失败（{self._model_path.name}）："
                        f"{_last_error(self._library)}"
                    )
            keepalive: list[object] = []
            tensors = (_GraphTensor * len(supplied))()
            for index, name in enumerate(supplied):
                buffer = np.ascontiguousarray(feed[name])
                code = _ONNX_DTYPE_BY_NUMPY_NAME.get(buffer.dtype.name)
                if code is None:
                    raise NativeGraphError(
                        f"输入张量 {name} 的数据类型不受支持：{buffer.dtype}"
                    )
                shape = tuple(int(value) for value in buffer.shape)
                dims = (ctypes.c_int64 * len(shape))(*shape)
                encoded = name.encode("utf-8")
                keepalive.extend((buffer, dims, encoded))
                entry = tensors[index]
                entry.name = encoded
                entry.data = ctypes.c_void_p(buffer.ctypes.data)
                entry.dtype = code
                entry.dims = dims
                entry.ndim = len(shape)

            outputs = (_GraphOutput * len(self._output_names))()
            try:
                status = self._library.fsv_graph_run(
                    handle,
                    tensors,
                    len(supplied),
                    outputs,
                    len(self._output_names),
                )
            finally:
                # The run has bound what it borrowed into its own value table,
                # so the loan can go back before the graph is reported as
                # finished.
                for _input_name, borrowed in loans:
                    self.release_device(borrowed)
            if status != 0:
                raise NativeGraphError(
                    f"自研 CUDA 推理图执行失败（{self._model_path.name}）："
                    f"{_last_error(self._library)}"
                )
            try:
                results = self._collect(outputs)
            finally:
                self._library.fsv_graph_release_outputs(outputs, len(self._output_names))
            del keepalive

            if output_names is None:
                return [results[name] for name in self._output_names]
            if isinstance(output_names, (str, bytes)):
                output_names = [output_names]
            requested = [str(name) for name in output_names]
            unavailable = [name for name in requested if name not in results]
            if unavailable:
                raise NativeGraphError("无效的输出张量名：" + ", ".join(unavailable))
            return [results[name] for name in requested]

    def _collect(self, outputs) -> dict[str, np.ndarray]:
        """Copy the runtime's freshly allocated outputs into NumPy arrays."""

        return {
            name: self._collect_one(outputs[index], name)
            for index, name in enumerate(self._output_names)
        }

    def _collect_one(self, entry, name, *, resident_handles: bool = True):
        shape = tuple(int(entry.dims[offset]) for offset in range(int(entry.ndim)))
        dtype = _NUMPY_DTYPE_BY_ONNX.get(int(entry.dtype))
        if dtype is None:
            raise NativeGraphError(f"输出张量 {name} 的数据类型不受支持：{int(entry.dtype)}")
        if resident_handles and name in self._device_outputs:
            return NativeDeviceTensor(self, name, shape, dtype)
        count = 1
        for dimension in shape:
            count *= dimension
        if count == 0 or not entry.data:
            return np.empty(shape, dtype=dtype)
        raw = (ctypes.c_char * (count * dtype.itemsize)).from_address(int(entry.data))
        return np.frombuffer(raw, dtype=dtype, count=count).reshape(shape).copy()

    def close(self) -> None:
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is not None:
            self._library.fsv_graph_free(handle)

    def read_resident(self, name: str) -> np.ndarray:
        """Read one retained output back to the host.

        Only ``NativeDeviceTensor.__array__`` needs this: the decode loop never
        asks, it just passes the handle on.
        """

        with self._lock:
            handle = self._handle
            if handle is None:
                raise NativeGraphError("自研 CUDA 推理会话已关闭")
            entries = (_GraphOutput * 1)()
            status = self._library.fsv_graph_read_resident(
                handle, str(name).encode("utf-8"), entries
            )
            if status != 0:
                raise NativeGraphError(
                    f"读取驻留张量 {name} 失败：{_last_error(self._library)}"
                )
            try:
                # The caller asked for the bytes, so this read is the one place
                # a retained output must not come back as another handle.
                return self._collect_one(entries[0], str(name), resident_handles=False)
            finally:
                self._library.fsv_graph_release_outputs(entries, 1)

    def __enter__(self) -> "NativeGraphSession":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def open_native_session(model_path, external_weights=None, *, library_path=None, capture=False):
    """Convenience wrapper used by the runtime adapter."""

    return NativeGraphSession(
        model_path,
        external_weights,
        library_path=library_path,
        capture=capture,
    )


__all__ = [
    "NativeDeviceInfo",
    "NativeDeviceTensor",
    "NativeGraphError",
    "NativeGraphSession",
    "NativeRuntimeUnavailable",
    "NativeTensorInfo",
    "load_native_library",
    "native_active_device",
    "native_device_count",
    "native_device_info",
    "native_last_error",
    "open_native_session",
]
