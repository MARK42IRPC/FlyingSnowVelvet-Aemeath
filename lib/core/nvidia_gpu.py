"""Detect an NVIDIA GPU through the installed display driver.

The self-written voice runtime needs nothing but ``nvcuda.dll`` from the NVIDIA
driver: no CUDA toolkit, no cuBLAS/cuDNN, no ONNX Runtime, no Python runtime
environment.  This probe calls the same driver entry point the runtime does, so
the settings switch and the runtime agree on what counts as an accelerated
machine.

The module is standard-library only: the dependency installer imports it before
third-party packages exist.
"""

from __future__ import annotations

import ctypes

__all__ = ["nvidia_driver_device_count", "has_nvidia_gpu"]


def nvidia_driver_device_count() -> int | None:
    """Return the device count the driver reports, or ``None`` without a driver."""

    try:
        driver = ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return None
    try:
        driver.cuInit.argtypes = [ctypes.c_uint]
        driver.cuInit.restype = ctypes.c_int
        driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        driver.cuDeviceGetCount.restype = ctypes.c_int
        if int(driver.cuInit(0)) != 0:
            return None
        count = ctypes.c_int(0)
        if int(driver.cuDeviceGetCount(ctypes.byref(count))) != 0:
            return None
    except (AttributeError, OSError, TypeError):
        return None
    return max(0, int(count.value))


def has_nvidia_gpu() -> bool:
    """Return whether the machine has a usable NVIDIA GPU."""

    return (nvidia_driver_device_count() or 0) > 0
