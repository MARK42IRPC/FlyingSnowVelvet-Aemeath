"""Application-facing helpers for the optional CUDA voice runtime."""

from __future__ import annotations

import sys
import tempfile
import threading
import wave
from pathlib import Path

from lib.core.cuda_runtime_installer import CudaRuntimeInstaller

from .hybrid_worker import VoiceWorkerRuntime
from .package_manager import get_voice_package_status


def _valid_probe_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as stream:
            frames = stream.getnframes()
            sample_rate = stream.getframerate()
            channels = stream.getnchannels()
            sample_width = stream.getsampwidth()
            data = stream.readframes(min(frames, sample_rate))
    except (OSError, EOFError, wave.Error):
        return False
    return (
        frames > 0
        and sample_rate == 32000
        and channels == 1
        and sample_width == 2
        and any(data)
    )


def probe_cuda_voice_package(
    runtime_python: Path,
    package_root: Path,
    cancel_event: threading.Event,
) -> tuple[bool, str]:
    """Create all current CUDA Sessions and synthesize Chinese and English."""

    package = Path(package_root)
    if not (package / "manifest.json").is_file() or not (package / "infer.py").is_file():
        return False, "语音包不完整，无法执行 CUDA 真实模型探测"
    runtime = None
    try:
        with tempfile.TemporaryDirectory(prefix="aemeath-cuda-probe-") as tempdir:
            output_root = Path(tempdir)
            runtime = VoiceWorkerRuntime(
                package,
                output_root,
                provider="cuda",
                python_path=Path(runtime_python),
                isolate_user_site=True,
                cancel_event=cancel_event,
            )
            for index, (language, text) in enumerate(
                (("zh", "你好。"), ("en", "Hello."))
            ):
                if cancel_event.is_set():
                    return False, "安装已取消"
                destination = output_root / f"probe-{index}.wav"
                runtime.synthesize_to_file(
                    {
                        "text": text,
                        "text_lang": language,
                        "max_steps": 64,
                        "seed": 1,
                    },
                    destination,
                )
                if not _valid_probe_wav(destination):
                    return False, f"CUDA {language} 探测没有生成有效 PCM WAV"
    except Exception as exc:
        return False, str(exc).strip() or repr(exc)
    finally:
        if runtime is not None:
            runtime.close()
    return True, ""


def create_cuda_runtime_installer(
    *,
    progress_callback=None,
    info_callback=None,
    python_executable: Path | str | None = None,
) -> CudaRuntimeInstaller:
    """Create a Bundle installer and attach the installed voice-package probe."""

    status = get_voice_package_status()
    voice_probe = None
    if not status.install_required and status.package_root is not None:
        package_root = Path(status.package_root)

        def voice_probe(runtime_python: Path, cancel_event: threading.Event):
            return probe_cuda_voice_package(
                runtime_python,
                package_root,
                cancel_event,
            )

    return CudaRuntimeInstaller(
        python_executable or sys.executable,
        progress_callback=progress_callback,
        info_callback=info_callback,
        voice_probe=voice_probe,
    )


__all__ = [
    "create_cuda_runtime_installer",
    "probe_cuda_voice_package",
]
