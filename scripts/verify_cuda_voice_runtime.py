#!/usr/bin/env python3
"""Install a local CUDA bundle temporarily and run real voice probes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.core import voice_runtime_contract as contract
from lib.core.cuda_runtime_bundle import sha256_file
from lib.core.cuda_runtime_installer import CudaRuntimeInstaller
from lib.script.gsvmove.cuda_runtime import _valid_probe_wav, probe_cuda_voice_package
from lib.script.gsvmove.hybrid_worker import CudaVoiceWorkerRuntime


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="local runtime ZIP")
    parser.add_argument("--voice-package", type=Path, required=True, help="validated ONNX voice package")
    parser.add_argument("--python", default=sys.executable, help="64-bit Python 3.11 executable")
    parser.add_argument("--expected-sha256", help="override the pinned archive SHA-256")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    return parser.parse_args(argv)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


def verify(args: argparse.Namespace) -> dict:
    archive = args.archive.resolve()
    package = args.voice_package.resolve()
    if not archive.is_file():
        raise RuntimeError(f"bundle archive does not exist: {archive}")
    if not (package / "manifest.json").is_file():
        raise RuntimeError(f"voice package manifest does not exist: {package}")
    actual_hash = sha256_file(archive)
    expected_hash = str(
        args.expected_sha256 or contract.CUDA_RUNTIME_BUNDLE_SHA256 or ""
    ).strip().lower()
    if len(expected_hash) != 64 or actual_hash != expected_hash:
        raise RuntimeError(
            f"archive SHA-256 mismatch: {actual_hash} != {expected_hash or '<unset>'}"
        )

    previous_home = os.environ.get("AEMEATH_DESK_PET_HOME")
    previous_hash = contract.CUDA_RUNTIME_BUNDLE_SHA256
    try:
        with tempfile.TemporaryDirectory(prefix="aemeath-cuda-validation-") as tempdir:
            os.environ["AEMEATH_DESK_PET_HOME"] = tempdir
            contract.CUDA_RUNTIME_BUNDLE_SHA256 = expected_hash
            target = contract.get_cuda_runtime_root()
            installer = CudaRuntimeInstaller(
                args.python,
                target_root=target,
                urls=(archive.as_uri(),),
                voice_probe=lambda runtime_python, cancel_event: probe_cuda_voice_package(
                    runtime_python,
                    package,
                    cancel_event,
                ),
            )
            installer.install()

            output_root = Path(tempdir) / "probe-output"
            runtime = CudaVoiceWorkerRuntime(package, output_root)
            try:
                mixed_output = output_root / "mixed.wav"
                runtime.synthesize_to_file(
                    {
                        "text": "你好，nice to meet you。",
                        "text_lang": "auto",
                        "max_steps": 96,
                        "seed": 1,
                    },
                    mixed_output,
                )
                if not _valid_probe_wav(mixed_output):
                    raise RuntimeError("mixed-language probe did not produce valid PCM WAV")
            finally:
                runtime.close()

            installed_files = [path for path in target.rglob("*") if path.is_file()]
            marker = json.loads((target / "runtime.json").read_text(encoding="utf-8"))
            return {
                "archive": archive.name,
                "archive_bytes": archive.stat().st_size,
                "archive_sha256": actual_hash,
                "installed_files": len(installed_files),
                "installed_bytes": sum(path.stat().st_size for path in installed_files),
                "bundle_id": marker.get("bundle_id"),
                "provider": marker.get("provider"),
                "chinese_probe": "passed",
                "english_probe": "passed",
                "mixed_probe": "passed",
                "mixed_wav_bytes": mixed_output.stat().st_size,
            }
    finally:
        contract.CUDA_RUNTIME_BUNDLE_SHA256 = previous_hash
        if previous_home is None:
            os.environ.pop("AEMEATH_DESK_PET_HOME", None)
        else:
            os.environ["AEMEATH_DESK_PET_HOME"] = previous_home


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    args = parse_args(argv or sys.argv[1:])
    try:
        report = verify(args)
    except Exception as exc:
        print(f"[cuda-bundle-verify] error: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(payload, end="")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
