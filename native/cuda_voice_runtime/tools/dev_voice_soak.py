"""Keep the standard voice task running and watch the card over time.

``dev_voice_benchmark.py`` answers "how fast is one synthesis"; this answers
"does the runtime stay healthy while it holds the card for a long time".  The
package is loaded once and then synthesised ``--runs`` times in the same
process: per-run seconds, the GPU memory ``nvidia-smi`` reports, and the
maximum sample difference against the first run are printed as a trend, so a
leak, a slow drift or a quietly different result shows up as a slope instead
of being hidden by a median.  Run it with ``FSV_CUDA_STATS=1`` to close the
session with the driver-side counters; the ``alloc``/``pool hit`` pair and
``device bytes owned`` are what a leak would move.

    py -3 native\cuda_voice_runtime\tools\dev_voice_soak.py --runs 30
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dev_voice_benchmark as bench  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=bench.DEFAULT_PACKAGE)
    parser.add_argument("--text", default=bench.STANDARD_TEXT)
    parser.add_argument("--seed", type=int, default=bench.DEFAULT_SEED)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--provider", default="cuda")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    return parser.parse_args()


def sample_gpu_memory() -> tuple[int, int] | None:
    """Return ``(used_mib, total_mib)`` from nvidia-smi, or None offline."""
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    first = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    parts = [item.strip() for item in first.split(",")]
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def window_median(values: list[float], size: int = 5) -> float:
    window = values[-size:] if len(values) >= size else values
    return statistics.median(window)


def main() -> int:
    args = parse_args()
    output = args.output or (
        PROJECT_ROOT / "build" / "cuda_voice_runtime" / "bench" / "soak.wav"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"text": args.text, "text_lang": "auto", "seed": args.seed}
    if args.max_steps is not None:
        payload["max_steps"] = args.max_steps

    print(f"standard task: {args.text} ({len(args.text)} chars)")
    print(f"package: {args.package}")
    print(f"provider: {args.provider}  runs: {max(1, args.runs)}")

    started = time.perf_counter()
    runtime = bench.OnnxVoiceRuntime(args.package, provider=args.provider)
    print(f"loaded in {time.perf_counter() - started:.1f}s")
    if args.provider == "cuda":
        index, device = bench.native_active_device()
        print(f"device: {index} {device}")
    print()

    durations: list[float] = []
    memory: list[tuple[int, int] | None] = []
    first_audio: np.ndarray | None = None
    worst_error = 0.0
    try:
        for run in range(1, max(1, args.runs) + 1):
            started = time.perf_counter()
            runtime.synthesize_to_file(dict(payload), output)
            seconds = time.perf_counter() - started
            durations.append(seconds)
            audio, _rate = sf.read(output, dtype="float32")
            audio = np.asarray(audio, dtype=np.float32)
            if first_audio is None:
                first_audio = audio
            else:
                count = min(first_audio.size, audio.size)
                error = float(np.max(np.abs(first_audio[:count] - audio[:count])))
                worst_error = max(worst_error, error)
            sample = sample_gpu_memory()
            memory.append(sample)
            memory_text = f"  gpu {sample[0]:5d}/{sample[1]} MiB" if sample else ""
            samples_text = "  samples match" if audio.size == first_audio.size else (
                f"  samples {audio.size} != {first_audio.size}"
            )
            print(f"run {run:3d}  {seconds:6.2f}s{memory_text}{samples_text}")
    finally:
        runtime.close()

    first = window_median(durations[:5])
    last = window_median(durations)
    drift = (last - first) / first * 100.0 if first else 0.0
    print()
    print(f"total {sum(durations):.1f}s over {len(durations)} runs")
    print(
        f"first5 median {first:.2f}s  last5 median {last:.2f}s  "
        f"drift {drift:+.1f}%"
    )
    print(
        f"best {min(durations):.2f}s  median {statistics.median(durations):.2f}s  "
        f"max {max(durations):.2f}s"
    )
    used = [item[0] for item in memory if item]
    if used:
        print(f"gpu memory {used[0]} MiB -> {used[-1]} MiB ({used[-1] - used[0]:+d} MiB)")
    print(f"audio vs run 1: max abs diff {worst_error:.3e}")
    healthy = abs(drift) < 10.0 and (not used or used[-1] - used[0] < 256)
    print("verdict: " + ("stable" if healthy else "DRIFT — inspect the run log"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
