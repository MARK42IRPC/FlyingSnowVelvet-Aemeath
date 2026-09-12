"""Measure the standard voice task on every backend and check their parity.

The numbers this prints are the ones quoted in
``doc/自研CUDA极简推理端.md``: model load time is reported separately because it
is a one-off cost, and only ``synthesize_to_file`` is timed.

    py -3 native\\cuda_voice_runtime\\tools\\dev_voice_benchmark.py --runs 3
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.script.gsvmove.onnx_runtime import OnnxVoiceRuntime  # noqa: E402
from lib.script.gsvmove.native_graph import native_active_device  # noqa: E402


STANDARD_TEXT = "帮我 check 今天 schedule，eight 点叫我。"
DEFAULT_PACKAGE = Path(r"C:\AemeathDeskPet\voice\ONNX_aimisiV2")
DEFAULT_SEED = 20260910


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--text", default=STANDARD_TEXT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--providers", default="cpu,cuda")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    return parser.parse_args()


def run_provider(provider, args, output_dir):
    """Load one backend and time ``--runs`` syntheses of the standard task."""

    payload = {"text": args.text, "text_lang": "auto", "seed": args.seed}
    if args.max_steps is not None:
        payload["max_steps"] = args.max_steps

    started = time.perf_counter()
    runtime = OnnxVoiceRuntime(args.package, provider=provider)
    load_seconds = time.perf_counter() - started
    try:
        durations = []
        audio = None
        target = output_dir / f"bench-{provider}.wav"
        for _ in range(max(1, args.runs)):
            started = time.perf_counter()
            runtime.synthesize_to_file(dict(payload), target)
            durations.append(time.perf_counter() - started)
            if audio is None:
                audio, _rate = sf.read(target, dtype="float32")
        return {
            "provider": provider,
            "load": load_seconds,
            "durations": durations,
            "best": min(durations),
            "median": statistics.median(durations),
            "audio": np.asarray(audio, dtype=np.float32),
            # Which card the runtime picked belongs in the report: a benchmark
            # on the wrong card, or on the host after a refusal, reads as a
            # performance regression otherwise.
            "device": native_active_device()[1] if provider == "cuda" else "",
        }
    finally:
        runtime.close()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir or (PROJECT_ROOT / "build" / "cuda_voice_runtime" / "bench")
    output_dir.mkdir(parents=True, exist_ok=True)
    providers = [item.strip() for item in args.providers.split(",") if item.strip()]

    print(f"标准任务：{args.text}（{len(args.text)} 字）")
    print(f"包：{args.package}")
    print(f"seed：{args.seed}  runs：{max(1, args.runs)}")
    print()

    results = []
    for provider in providers:
        try:
            result = run_provider(provider, args, output_dir)
        except Exception as exc:
            print(f"{provider:<6} 失败：{exc}")
            continue
        results.append(result)
        audio_seconds = result["audio"].size / 32000
        if result["device"]:
            print(f"{provider} 设备：{result['device']}")
        print(
            f"{provider:<6} load {result['load']:5.1f}s  "
            f"best {result['best']:6.2f}s  median {result['median']:6.2f}s  "
            f"runs {[round(value, 2) for value in result['durations']]}  "
            f"audio {audio_seconds:4.2f}s  rtf {result['median'] / audio_seconds:5.2f}"
        )

    if len(results) > 1:
        base = results[0]
        print()
        for other in results[1:]:
            count = min(base["audio"].size, other["audio"].size)
            error = float(np.max(np.abs(base["audio"][:count] - other["audio"][:count])))
            print(
                f"一致性 {base['provider']} vs {other['provider']}："
                f"采样点 {base['audio'].size}/{other['audio'].size}  "
                f"最大绝对误差 {error:.6f}"
            )
        fastest = min(results, key=lambda item: item["median"])
        slowest = max(results, key=lambda item: item["median"])
        if fastest is not slowest:
            print(
                f"最快 {fastest['provider']} {fastest['median']:.2f}s，"
                f"最慢 {slowest['provider']} {slowest['median']:.2f}s，"
                f"倍数 {slowest['median'] / fastest['median']:.2f}x"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
